"""
embed_spec_chunks node — generates embeddings for spec chunks.

Implements: Design Doc §3.5 Embed Spec Chunks
Touches: spec_chunk_embeddings (Silver layer)

Uses OpenAI embeddings API when OPENAI_API_KEY is set,
falls back to deterministic fake embeddings for tests.

Supports token-aware batching for large specs.
"""
import os
import logging
from typing import Optional, List, Tuple

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import SpecChunkEmbedding
from integration_coworker.config import get_embedding_config, get_settings

logger = logging.getLogger(__name__)

# OpenAI embedding API limits
MAX_BATCH_SIZE = 2048  # Max inputs per API call
MAX_TOKENS_PER_REQUEST = 250000  # Stay under 300K limit with safety margin
MAX_INPUT_CHARS = 8000  # Max chars per individual input
CHARS_PER_TOKEN = 4  # Rough estimate: 1 token ≈ 4 characters

# Optional: OpenAI client for real embeddings
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    OpenAI = None


def _get_embedding_client() -> Optional["OpenAI"]:
    """Get OpenAI client if available and configured."""
    if not HAS_OPENAI:
        return None
    
    settings = get_settings()
    if not settings.llm.api_key or settings.llm.use_mock:
        return None
    
    try:
        kwargs = {"api_key": settings.llm.api_key}
        if settings.llm.base_url:
            kwargs["base_url"] = settings.llm.base_url
        return OpenAI(**kwargs)
    except Exception as e:
        logger.warning(f"Failed to create OpenAI client: {e}")
        return None


def _generate_fake_embedding(chunk_idx: int, total_chunks: int, dimensions: int) -> list[float]:
    """Generate deterministic fake embedding vector for tests."""
    embedding = [0.0] * dimensions
    if dimensions > 0:
        embedding[0] = float(chunk_idx) / max(total_chunks, 1)
        if dimensions > 1:
            embedding[1] = 0.5 - (float(chunk_idx) / max(total_chunks * 2, 1))
    return embedding


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


def _batch_embed(client: "OpenAI", texts: List[str], model: str) -> List[Optional[List[float]]]:
    """
    Embed texts in token-aware batches using OpenAI API.
    
    Returns list of embedding vectors in same order as input texts.
    None values indicate failed embeddings.
    """
    # Initialize result array with None
    all_embeddings: List[Optional[List[float]]] = [None] * len(texts)
    
    # Create token-aware batches
    batches = _create_token_aware_batches(texts)
    total_batches = len(batches)
    
    logger.info(f"Embedding {len(texts)} chunks in {total_batches} batches")
    
    for batch_num, batch in enumerate(batches):
        # Extract just the texts for the API call
        batch_texts = [text for _, text in batch]
        batch_indices = [idx for idx, _ in batch]
        
        try:
            response = client.embeddings.create(
                input=batch_texts,
                model=model,
            )
            
            # Sort by index to ensure order is preserved
            sorted_data = sorted(response.data, key=lambda x: x.index)
            
            # Map embeddings back to original indices
            for i, item in enumerate(sorted_data):
                original_idx = batch_indices[i]
                all_embeddings[original_idx] = item.embedding
            
            if (batch_num + 1) % 10 == 0 or batch_num == total_batches - 1:
                embedded_count = sum(1 for e in all_embeddings if e is not None)
                logger.info(f"Embedded {embedded_count}/{len(texts)} chunks (batch {batch_num + 1}/{total_batches})")
                
        except Exception as e:
            logger.error(f"Batch {batch_num + 1}/{total_batches} failed: {e}")
            # Leave None values for failed batch - will use fake embeddings
    
    return all_embeddings


def embed_spec_chunks(state: WorkflowState) -> WorkflowState:
    """
    Generate embeddings for spec chunks.

    Reads: doc_chunks, spec_documents, plan["chunk_index_to_spec_document_uri"]
    Writes: spec_chunk_embeddings
    
    Uses batched API calls for efficiency with large specs.
    """
    if not state.doc_chunks:
        state.completed_steps.append("embed_spec_chunks")
        return state

    config = get_embedding_config()
    model = config.get("model", "text-embedding-3-small")
    dimensions = config.get("dimensions", 1536)

    # Build URI -> spec_document_id mapping
    uri_to_doc_id: dict[str, int] = {}
    for doc in state.spec_documents:
        if doc.id is not None:
            uri_to_doc_id[doc.uri] = doc.id
        elif doc.uri:
            # Use uri as temporary key if id not yet assigned
            uri_to_doc_id[doc.uri] = None

    # Get chunk-to-URI mapping from plan
    chunk_to_uri: dict[int, str] = {}
    if state.plan and "chunk_index_to_spec_document_uri" in state.plan:
        chunk_to_uri = state.plan["chunk_index_to_spec_document_uri"]

    # Check if we should use real embeddings
    client = _get_embedding_client()
    use_real = client is not None

    total_chunks = len(state.doc_chunks)
    
    if use_real:
        logger.info(f"Using real OpenAI embeddings with model {model} for {total_chunks} chunks")
        
        # Use batched embedding for efficiency
        embeddings = _batch_embed(client, state.doc_chunks, model)
        
        for idx, chunk in enumerate(state.doc_chunks):
            chunk_uri = chunk_to_uri.get(idx)
            spec_document_id = uri_to_doc_id.get(chunk_uri) if chunk_uri else None
            if spec_document_id is None and state.spec_documents:
                spec_document_id = state.spec_documents[0].id

            # Use real embedding or fall back to fake if batch failed
            if idx < len(embeddings) and embeddings[idx] is not None:
                embedding_vector = embeddings[idx]
            else:
                embedding_vector = _generate_fake_embedding(idx, total_chunks, dimensions)

            chunk_embedding = SpecChunkEmbedding(
                id=None,
                spec_document_id=spec_document_id,
                chunk_index=idx,
                content=chunk[:500],
                embedding=embedding_vector,
            )
            chunk_embedding._full_content = chunk
            state.spec_chunk_embeddings.append(chunk_embedding)
    else:
        logger.info(f"Using fake embeddings for {total_chunks} chunks (no OpenAI API key or USE_MOCK_LLM=true)")
        
        for idx, chunk in enumerate(state.doc_chunks):
            chunk_uri = chunk_to_uri.get(idx)
            spec_document_id = uri_to_doc_id.get(chunk_uri) if chunk_uri else None
            if spec_document_id is None and state.spec_documents:
                spec_document_id = state.spec_documents[0].id

            embedding_vector = _generate_fake_embedding(idx, total_chunks, dimensions)

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
