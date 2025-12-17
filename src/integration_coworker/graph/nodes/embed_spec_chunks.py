"""
embed_spec_chunks node — generates embeddings for spec chunks.

Implements: Design Doc §3.5 Embed Spec Chunks
Touches: spec_chunk_embeddings (Silver layer)

V2: Requires real OpenAI embeddings. No fake fallback in production.
For tests, use pytest fixtures with mocked embeddings.

Supports token-aware batching for large specs.

V2.1: Uses LangChain OpenAIEmbeddings for automatic LangSmith tracing.
All embedding calls are now traced alongside LLM calls.

V3 Adaptive Streaming Mode:
- Automatically uses streaming for large specs (based on chunk count)
- Lazy loads chunks from DB instead of reading from state.doc_chunks
- Streams embeddings directly to DB as they're computed
- Keeps state.spec_chunk_embeddings empty for memory efficiency

Bug #58 fix: Added exponential backoff retry for rate limit errors.
"""
import os
import time
import logging
from typing import Optional, List, Tuple, Any, Protocol, runtime_checkable

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import SpecChunkEmbedding
from integration_coworker.config import get_embedding_config, get_settings

logger = logging.getLogger(__name__)

# OpenAI embedding API limits
MAX_BATCH_SIZE = 2048  # Max inputs per API call
MAX_TOKENS_PER_REQUEST = 250000  # Stay under 300K limit with safety margin
MAX_INPUT_CHARS = 8000  # Max chars per individual input
CHARS_PER_TOKEN = 4  # Rough estimate: 1 token ≈ 4 characters

# Bug #58 fix: Retry configuration for rate limit errors
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0


def _is_rate_limit_error(error: Exception) -> bool:
    """Check if an exception is a rate limit or retryable error."""
    error_str = str(error).lower()
    retryable_patterns = [
        "rate limit",
        "rate_limit",
        "429",
        "too many requests",
        "quota exceeded",
        "timeout",
        "service unavailable",
        "503",
        "connection error",
    ]
    return any(pattern in error_str for pattern in retryable_patterns)


def _embed_with_retry(
    client: Any,
    texts: List[str],
    max_retries: int = MAX_RETRIES,
) -> List[List[float]]:
    """
    Call embed_documents with exponential backoff retry for rate limit errors.
    
    Bug #58 fix: Adds retry logic for transient API errors.
    
    Args:
        client: LangChain embedding client
        texts: List of texts to embed
        max_retries: Maximum number of retry attempts
        
    Returns:
        List of embedding vectors
        
    Raises:
        Original exception if all retries fail or non-retryable error
    """
    last_error = None
    backoff = INITIAL_BACKOFF_SECONDS
    
    for attempt in range(max_retries + 1):
        try:
            return client.embed_documents(texts)
        except Exception as e:
            last_error = e
            
            # Only retry on rate limit / transient errors
            if not _is_rate_limit_error(e):
                logger.error(f"Non-retryable embedding error: {e}")
                raise
            
            if attempt < max_retries:
                logger.warning(
                    f"Embedding rate limit hit (attempt {attempt + 1}/{max_retries + 1}), "
                    f"retrying in {backoff:.1f}s: {e}"
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            else:
                logger.error(f"All {max_retries + 1} embedding attempts failed: {e}")
                raise last_error


@runtime_checkable
class EmbeddingsProtocol(Protocol):
    """Protocol for LangChain-style embeddings interface."""
    def embed_query(self, text: str) -> List[float]: ...
    def embed_documents(self, texts: List[str]) -> List[List[float]]: ...


# Optional: LangChain OpenAI embeddings for LangSmith tracing
try:
    from langchain_openai import OpenAIEmbeddings
    HAS_LANGCHAIN_EMBEDDINGS = True
except ImportError:
    HAS_LANGCHAIN_EMBEDDINGS = False
    OpenAIEmbeddings = None


def _get_embedding_client() -> Optional[EmbeddingsProtocol]:
    """
    Get LangChain OpenAIEmbeddings client for automatic LangSmith tracing.
    
    Returns an object with embed_query() and embed_documents() methods.
    All embedding calls are automatically traced in LangSmith.
    """
    if not HAS_LANGCHAIN_EMBEDDINGS:
        logger.warning("langchain-openai not installed. Install with: pip install langchain-openai")
        return None
    
    settings = get_settings()
    if not settings.llm.api_key or settings.llm.use_mock:
        return None
    
    try:
        config = get_embedding_config()
        model = config.get("model", "text-embedding-3-small")
        
        kwargs = {
            "api_key": settings.llm.api_key,
            "model": model,
        }
        if settings.llm.base_url:
            kwargs["base_url"] = settings.llm.base_url
            
        return OpenAIEmbeddings(**kwargs)
    except Exception as e:
        logger.warning(f"Failed to create LangChain OpenAIEmbeddings client: {e}")
        return None


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    return len(text) // CHARS_PER_TOKEN + 1


def _create_token_aware_batches(texts: List[str]) -> List[List[Tuple[int, str]]]:
    """
    Create batches that respect both count and token limits.
    
    Returns list of batches, where each batch is a list of (original_index, text) tuples.
    """
    batches = []
    current_batch = []
    current_tokens = 0
    
    for idx, text in enumerate(texts):
        # Truncate text if needed
        truncated = text[:MAX_INPUT_CHARS]
        text_tokens = _estimate_tokens(truncated)
        
        # Check if adding this text would exceed limits
        would_exceed_tokens = (current_tokens + text_tokens) > MAX_TOKENS_PER_REQUEST
        would_exceed_count = len(current_batch) >= MAX_BATCH_SIZE
        
        if current_batch and (would_exceed_tokens or would_exceed_count):
            # Save current batch and start a new one
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
        
        current_batch.append((idx, truncated))
        current_tokens += text_tokens
    
    # Don't forget the last batch
    if current_batch:
        batches.append(current_batch)
    
    return batches


def _batch_embed(client: EmbeddingsProtocol, texts: List[str], model: str) -> List[Optional[List[float]]]:
    """
    Embed texts in token-aware batches using LangChain OpenAIEmbeddings.
    
    Uses embed_documents() for batch embedding with automatic LangSmith tracing.
    Bug #58 fix: Uses _embed_with_retry for rate limit handling.
    
    Returns list of embedding vectors in same order as input texts.
    None values indicate failed embeddings.
    """
    # Initialize result array with None
    all_embeddings: List[Optional[List[float]]] = [None] * len(texts)
    
    # Create token-aware batches
    batches = _create_token_aware_batches(texts)
    total_batches = len(batches)
    
    logger.info(f"Embedding {len(texts)} chunks in {total_batches} batches (with LangSmith tracing)")
    
    for batch_num, batch in enumerate(batches):
        # Extract just the texts for the API call (already truncated in _create_token_aware_batches)
        batch_texts = [text for _, text in batch]
        batch_indices = [idx for idx, _ in batch]
        
        try:
            # Use _embed_with_retry for automatic rate limit handling
            # Bug #58 fix: Uses exponential backoff for transient errors
            batch_embeddings = _embed_with_retry(client, batch_texts)
            
            # Map embeddings back to original indices
            for i, embedding in enumerate(batch_embeddings):
                original_idx = batch_indices[i]
                all_embeddings[original_idx] = embedding
            
            if (batch_num + 1) % 10 == 0 or batch_num == total_batches - 1:
                embedded_count = sum(1 for e in all_embeddings if e is not None)
                logger.info(f"Embedded {embedded_count}/{len(texts)} chunks (batch {batch_num + 1}/{total_batches})")
                
        except Exception as e:
            logger.error(f"Batch {batch_num + 1}/{total_batches} failed: {e}")
            # Leave None values for failed batch
    
    return all_embeddings


def embed_spec_chunks(state: WorkflowState) -> WorkflowState:
    """
    Generate embeddings for spec chunks.

    V2: Requires real embeddings. No fake fallback in production.
    For tests, use pytest fixtures with mocked embeddings.

    V3 Adaptive Streaming:
    - Uses streaming if chunks were streamed by ingest_spec (chunks_streamed=True)
    - Otherwise uses legacy in-memory mode
    - Streaming: Lazy loads chunks from DB, streams embeddings to DB
    - Legacy: Reads from state.doc_chunks, accumulates in state.spec_chunk_embeddings

    Reads: doc_chunks (legacy) OR spec_document_ids (streaming)
    Writes: spec_chunk_embeddings (legacy) OR embedding_count (streaming)
    
    Uses batched API calls for efficiency with large specs.
    """
    # Check if ingest_spec used streaming mode
    streaming_mode = state.persisted_ids.get("chunks_streamed", False)
    
    if streaming_mode:
        return _embed_spec_chunks_streaming(state)
    else:
        return _embed_spec_chunks_legacy(state)


def _embed_spec_chunks_legacy(state: WorkflowState) -> WorkflowState:
    """
    Legacy embedding mode: read from state, accumulate in state.
    
    Original behavior for backward compatibility.
    """
    if not state.doc_chunks:
        state.completed_steps.append("embed_spec_chunks")
        return state

    config = get_embedding_config()
    model = config.get("model", "text-embedding-3-small")

    # Build URI -> spec_document_id mapping
    uri_to_doc_id: dict[str, int] = {}
    for doc in state.spec_documents:
        if doc.id is not None:
            uri_to_doc_id[doc.uri] = doc.id
        elif doc.uri:
            uri_to_doc_id[doc.uri] = None

    # Get chunk-to-URI mapping from plan
    chunk_to_uri: dict[int, str] = {}
    if state.plan and "chunk_index_to_spec_document_uri" in state.plan:
        chunk_to_uri = state.plan["chunk_index_to_spec_document_uri"]

    # Get embedding client - REQUIRED in V2
    client = _get_embedding_client()
    
    if not client:
        # V2.1: Embedding unavailable is a warning, not an error.
        # The workflow can continue in degraded mode (KG matching only, no semantic search).
        warning_msg = (
            "Embedding client unavailable - continuing in degraded mode. "
            "KG matching will work but semantic search will be limited. "
            "To enable embeddings, set OPENAI_API_KEY."
        )
        logger.warning(warning_msg)
        state.warnings.append(warning_msg)
        state.completed_steps.append("embed_spec_chunks")
        return state

    total_chunks = len(state.doc_chunks)
    logger.info(f"Generating embeddings for {total_chunks} chunks using {model} (LangSmith tracing enabled)")
    
    # Use batched embedding for efficiency
    embeddings = _batch_embed(client, state.doc_chunks, model)
    
    # Check for embedding failures
    failed_count = sum(1 for e in embeddings if e is None)
    if failed_count > 0:
        state.warnings.append(
            f"{failed_count}/{total_chunks} chunks failed to embed"
        )
    
    for idx, chunk in enumerate(state.doc_chunks):
        chunk_uri = chunk_to_uri.get(idx)
        spec_document_id = uri_to_doc_id.get(chunk_uri) if chunk_uri else None
        if spec_document_id is None and state.spec_documents:
            spec_document_id = state.spec_documents[0].id

        embedding_vector = embeddings[idx] if idx < len(embeddings) else None
        
        if embedding_vector is None:
            continue

        chunk_embedding = SpecChunkEmbedding(
            id=None,
            spec_document_id=spec_document_id,
            chunk_index=idx,
            content=chunk[:500],
            embedding=embedding_vector,
        )
        chunk_embedding._full_content = chunk
        state.spec_chunk_embeddings.append(chunk_embedding)

    state.completed_steps.append("embed_spec_chunks")
    return state


def _embed_spec_chunks_streaming(state: WorkflowState) -> WorkflowState:
    """
    V3 Streaming embedding mode: lazy load from DB, stream embeddings to DB.
    
    Memory efficient - never loads all chunks or embeddings into memory at once.
    """
    from integration_coworker.persistence.lazy_loader import iter_chunks_for_embedding
    from integration_coworker.persistence.streaming import stream_embedding_batch, EMBEDDING_BATCH_SIZE

    # Check if we have spec documents with IDs (streaming mode requires them)
    spec_doc_ids = [doc.id for doc in state.spec_documents if doc.id is not None]
    if not spec_doc_ids:
        # Fall back to chunk count if available
        if state.chunk_count == 0:
            logger.info("No chunks to embed in streaming mode")
            state.completed_steps.append("embed_spec_chunks")
            return state

    config = get_embedding_config()
    model = config.get("model", "text-embedding-3-small")

    # Get embedding client
    client = _get_embedding_client()
    
    if not client:
        # V2.1: Embedding unavailable is a warning, not an error.
        # The workflow can continue in degraded mode (KG matching only, no semantic search).
        warning_msg = (
            "Embedding client unavailable - continuing in degraded mode. "
            "KG matching will work but semantic search will be limited. "
            "To enable embeddings, set OPENAI_API_KEY."
        )
        logger.warning(warning_msg)
        state.warnings.append(warning_msg)
        state.completed_steps.append("embed_spec_chunks")
        return state

    total_embedded = 0
    failed_count = 0

    # Process each spec document
    for spec_doc_id in spec_doc_ids:
        logger.info(f"Streaming embeddings for spec_document_id={spec_doc_id}")
        
        # Collect chunks for batch embedding
        batch_chunks = []  # List of (chunk_id, chunk_index, content)
        
        for chunk_id, chunk_index, content in iter_chunks_for_embedding(spec_doc_id):
            batch_chunks.append((chunk_id, chunk_index, content))
            
            # Process batch when full
            if len(batch_chunks) >= EMBEDDING_BATCH_SIZE:
                embedded, failed = _process_embedding_batch(
                    client, batch_chunks, model
                )
                total_embedded += embedded
                failed_count += failed
                batch_chunks = []
        
        # Process remaining chunks
        if batch_chunks:
            embedded, failed = _process_embedding_batch(
                client, batch_chunks, model
            )
            total_embedded += embedded
            failed_count += failed

    # Update state with counts (not full embeddings)
    state.embedding_count = total_embedded
    state.spec_chunk_embeddings = []  # Keep empty in streaming mode
    
    if failed_count > 0:
        state.warnings.append(
            f"{failed_count} chunks failed to embed in streaming mode"
        )

    # Mark embeddings as streamed
    state.persisted_ids["embeddings_streamed"] = True
    state.persisted_ids["embedding_count"] = total_embedded

    logger.info(f"Streaming embedding complete: {total_embedded} embeddings written to DB")
    state.completed_steps.append("embed_spec_chunks")
    return state


def _process_embedding_batch(
    client: Any,
    batch_chunks: List[Tuple[int, int, str]],
    model: str,
) -> Tuple[int, int]:
    """
    Process a batch of chunks: compute embeddings and stream to DB.
    
    Bug #58 fix: Uses _embed_with_retry for rate limit handling.
    
    Args:
        client: LangChain embedding client
        batch_chunks: List of (chunk_id, chunk_index, content) tuples
        model: Embedding model name
    
    Returns:
        Tuple of (embedded_count, failed_count)
    """
    from integration_coworker.persistence.streaming import stream_embedding_batch
    
    if not batch_chunks:
        return 0, 0
    
    # Extract texts for embedding
    texts = [content[:MAX_INPUT_CHARS] for _, _, content in batch_chunks]
    chunk_ids = [chunk_id for chunk_id, _, _ in batch_chunks]
    
    try:
        # Use _embed_with_retry for automatic rate limit handling
        # Bug #58 fix: Uses exponential backoff for transient errors
        embeddings = _embed_with_retry(client, texts)
        
        # Prepare updates for DB
        updates = list(zip(chunk_ids, embeddings))
        
        # Stream to DB
        success_count = stream_embedding_batch(updates)
        failed_count = len(batch_chunks) - success_count
        
        return success_count, failed_count
        
    except Exception as e:
        logger.error(f"Batch embedding failed after retries: {e}")
        return 0, len(batch_chunks)
