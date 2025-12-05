"""
ingest_spec node — fetches and chunks all spec_refs (primary + supporting).

Implements: Design Doc §3.2 Ingest Spec
Touches: spec_documents, source_refs (Silver layer)

V3 Streaming Mode:
Automatically enabled for large specs (>500KB or >500 chunks) to prevent
memory issues. Can be forced via STREAMING_PERSISTENCE=true/false.

When streaming:
- Chunks are streamed directly to the database
- state.doc_chunks remains empty (memory efficient)
- Memory usage stays <20MB regardless of spec size

API-002: Multi-Spec Source Reference Handling
- Creates SourceRef objects for each spec
- Links SpecDocument to its SourceRef
- Enables traceability for multi-provider integrations
"""
from pathlib import Path
import hashlib
import logging
from typing import List, Tuple, Iterator

import httpx

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import SpecDocument, SourceRef
from integration_coworker.config import should_use_streaming_for_spec, is_streaming_persistence_disabled

logger = logging.getLogger(__name__)


def _fetch_spec_content(ref: str) -> tuple[str, str]:
    """
    Fetch content from a spec ref (HTTP URL or local file path).
    Returns (content, content_type).
    Raises on failure.
    """
    if ref.startswith("http://") or ref.startswith("https://"):
        response = httpx.get(ref, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        content = response.text
        content_type = response.headers.get("content-type", "application/octet-stream")
        return content, content_type
    else:
        file_path = Path(ref)
        if not file_path.exists():
            raise FileNotFoundError(f"Spec file not found: {ref}")
        content = file_path.read_text(encoding="utf-8")
        suffix = file_path.suffix.lower()
        if suffix in [".yaml", ".yml"]:
            content_type = "application/yaml"
        elif suffix == ".json":
            content_type = "application/json"
        else:
            content_type = "text/plain"
        return content, content_type


def _chunk_content(content: str, chunk_size: int = 1000) -> list[str]:
    """
    Split content into chunks on section boundaries or size limit.
    """
    chunks = []
    lines = content.split("\n")
    current_chunk = []
    current_size = 0

    for line in lines:
        current_chunk.append(line)
        current_size += len(line) + 1  # +1 for newline

        # Chunk on section markers or size limit
        if current_size >= chunk_size or line.strip().startswith("paths:") or line.strip().startswith("components:"):
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_size = 0

    # Add remaining content
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks if chunks else [content]


def ingest_spec(state: WorkflowState) -> WorkflowState:
    """
    Ingest all spec_refs (primary + supporting) into spec_documents and doc_chunks.

    API-002: Creates SourceRef objects for each spec, enabling traceability.

    V3 Adaptive Streaming:
    - Automatically streams large specs (>500KB) to DB for memory efficiency
    - Small specs use fast in-memory mode
    - Override with STREAMING_PERSISTENCE=true/false

    Streaming Mode:
    - Chunks are streamed directly to spec_silver.spec_chunks
    - state.doc_chunks remains empty (memory efficient)
    - state.spec_chunk_ids holds the DB IDs instead
    - state.chunk_count holds the count for downstream nodes

    Legacy Mode:
    - Chunks are accumulated in state.doc_chunks (original behavior)
    - All data persisted in persist_silver_checkpoint

    Reads: spec_refs, plan, provider_code
    Writes: source_refs, spec_documents, doc_chunks (legacy) OR spec_chunk_ids (streaming),
            plan["chunk_index_to_spec_document_uri"]
    """
    if not state.spec_refs:
        state.errors.append("No spec_refs provided")
        state.completed_steps.append("ingest_spec")
        return state

    # API-002: Create SourceRef objects for each spec
    if not state.source_refs:
        state.source_refs = []
    
    for ref in state.spec_refs:
        source_ref = SourceRef.from_ref(ref, provider_code=state.provider_code)
        state.source_refs.append(source_ref)
    
    logger.debug(f"Created {len(state.source_refs)} SourceRef objects")

    # If streaming is explicitly disabled, use legacy mode
    if is_streaming_persistence_disabled():
        logger.debug("Streaming persistence disabled, using legacy mode")
        return _ingest_spec_legacy(state)
    
    # Fetch all specs first to determine total size
    fetched_specs = []
    total_bytes = 0
    
    for ref in state.spec_refs:
        try:
            content, content_type = _fetch_spec_content(ref)
            fetched_specs.append((ref, content, content_type))
            total_bytes += len(content.encode("utf-8"))
        except Exception as e:
            state.errors.append(f"Failed to fetch spec from {ref}: {str(e)}")
    
    if not fetched_specs:
        state.errors.append("No specs could be fetched")
        state.completed_steps.append("ingest_spec")
        return state
    
    # Estimate chunks (rough: 1 chunk per 1000 chars)
    estimated_chunks = total_bytes // 1000
    
    # Decide streaming mode based on spec size
    use_streaming = should_use_streaming_for_spec(total_bytes, estimated_chunks)
    
    if use_streaming:
        logger.info(f"Using streaming mode for {total_bytes:,} bytes ({estimated_chunks} estimated chunks)")
        return _ingest_spec_streaming_with_fetched(state, fetched_specs)
    else:
        logger.debug(f"Using legacy mode for {total_bytes:,} bytes")
        return _ingest_spec_legacy_with_fetched(state, fetched_specs)


def _ingest_spec_legacy(state: WorkflowState) -> WorkflowState:
    """
    Legacy ingest mode: accumulate chunks in memory.
    
    Original behavior - all chunks held in state.doc_chunks until
    persist_silver_checkpoint writes them to DB.
    
    Note: This fetches specs itself. For pre-fetched specs, use
    _ingest_spec_legacy_with_fetched().
    """
    all_chunks: list[str] = []
    chunk_index_to_uri: dict[int, str] = {}

    for ref in state.spec_refs:
        try:
            content, content_type = _fetch_spec_content(ref)
            sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

            # Create SpecDocument for this ref
            spec_doc = SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0",
                uri=ref,
                content_type=content_type,
                sha256=sha256,
                content=content,
            )
            state.spec_documents.append(spec_doc)

            # Chunk this document
            doc_chunks = _chunk_content(content)

            # Track which chunks belong to which spec document
            start_idx = len(all_chunks)
            for i, chunk in enumerate(doc_chunks):
                chunk_index_to_uri[start_idx + i] = ref
            all_chunks.extend(doc_chunks)

        except Exception as e:
            state.errors.append(f"Failed to ingest spec from {ref}: {str(e)}")

    state.doc_chunks = all_chunks

    # Store mapping in plan for downstream nodes (embed_spec_chunks, persist)
    if state.plan is not None:
        state.plan["chunk_index_to_spec_document_uri"] = chunk_index_to_uri

    state.completed_steps.append("ingest_spec")
    return state


def _ingest_spec_legacy_with_fetched(
    state: WorkflowState,
    fetched_specs: List[Tuple[str, str, str]],
) -> WorkflowState:
    """
    Legacy ingest mode with pre-fetched specs.
    
    API-002: Links each SpecDocument to its SourceRef for traceability.
    
    Args:
        fetched_specs: List of (uri, content, content_type) tuples
    """
    all_chunks: list[str] = []
    chunk_index_to_uri: dict[int, str] = {}
    
    # Build URI -> SourceRef mapping (handle backwards compatibility)
    uri_to_source_ref = {}
    for sr in state.source_refs:
        # Handle both SourceRef objects and legacy string refs
        if hasattr(sr, 'uri'):
            uri_to_source_ref[sr.uri] = sr
        elif isinstance(sr, str):
            uri_to_source_ref[sr] = None  # Legacy: no SourceRef object

    for ref, content, content_type in fetched_specs:
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Create SpecDocument for this ref
        spec_doc = SpecDocument(
            id=None,
            source_system_id=None,
            version="1.0",
            uri=ref,
            content_type=content_type,
            sha256=sha256,
            content=content,
        )
        
        # API-002: Link to SourceRef
        source_ref = uri_to_source_ref.get(ref)
        if source_ref:
            spec_doc._source_ref = source_ref
        
        state.spec_documents.append(spec_doc)

        # Chunk this document
        doc_chunks = _chunk_content(content)

        # Track which chunks belong to which spec document
        start_idx = len(all_chunks)
        for i, chunk in enumerate(doc_chunks):
            chunk_index_to_uri[start_idx + i] = ref
        all_chunks.extend(doc_chunks)

    state.doc_chunks = all_chunks

    # Store mapping in plan for downstream nodes
    if state.plan is not None:
        state.plan["chunk_index_to_spec_document_uri"] = chunk_index_to_uri

    state.completed_steps.append("ingest_spec")
    return state


def _ingest_spec_streaming(state: WorkflowState) -> WorkflowState:
    """
    V3 Streaming ingest mode: stream chunks to DB immediately.
    
    Reduces memory from 200MB+ to <20MB by:
    1. Writing chunks to DB as they're created
    2. Clearing state.doc_chunks (keeping it empty)
    3. Storing chunk IDs in state.spec_chunk_ids instead
    
    Note: This fetches specs itself. For pre-fetched specs, use
    _ingest_spec_streaming_with_fetched().
    """
    fetched_specs = []
    for ref in state.spec_refs:
        try:
            content, content_type = _fetch_spec_content(ref)
            fetched_specs.append((ref, content, content_type))
        except Exception as e:
            state.errors.append(f"Failed to fetch spec from {ref}: {str(e)}")
            logger.error(f"Failed to fetch spec from {ref}: {e}")
    
    return _ingest_spec_streaming_with_fetched(state, fetched_specs)


def _ingest_spec_streaming_with_fetched(
    state: WorkflowState,
    fetched_specs: List[Tuple[str, str, str]],
) -> WorkflowState:
    """
    V3 Streaming ingest mode with pre-fetched specs.
    
    Args:
        fetched_specs: List of (uri, content, content_type) tuples
    """
    from integration_coworker.persistence import db
    from integration_coworker.persistence.streaming import stream_chunks_to_silver
    from integration_coworker.persistence.sql_helpers import upsert_ignore, select_by_columns, get_engine_type

    # Initialize schema for streaming writes
    db.init_schema()
    
    chunk_index_to_uri: dict[int, str] = {}
    all_chunk_ids: list[int] = []
    total_chunk_count = 0
    global_chunk_index = 0

    engine = get_engine_type()
    schema = "spec_silver" if engine == "postgres" else None

    for ref, content, content_type in fetched_specs:
        try:
            sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

            # Create SpecDocument for this ref (content stored for parsing, will be cleared later)
            spec_doc = SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0",
                uri=ref,
                content_type=content_type,
                sha256=sha256,
                content=content,  # Needed for detect_and_parse_spec
            )
            state.spec_documents.append(spec_doc)

            # We need to persist the spec_document first to get an ID
            # This is normally done in persist_silver_checkpoint, but we need it now
            conn = db.get_connection()
            cur = conn.cursor()
            
            # Get or create source_system
            provider_code = state.provider_code or "unknown"
            sql = upsert_ignore("source_systems", ["code", "display_name"], ["code"], schema)
            cur.execute(sql, (provider_code, provider_code.replace("_", " ").title()))
            sql = select_by_columns("source_systems", ["id"], ["code"], schema)
            cur.execute(sql, (provider_code,))
            source_system_id = cur.fetchone()[0]
            
            # Insert spec_document
            sql = upsert_ignore(
                "spec_documents",
                ["source_system_id", "uri", "sha256", "content_type"],
                ["source_system_id", "sha256"],
                schema
            )
            cur.execute(sql, (source_system_id, ref, sha256, content_type))
            sql = select_by_columns("spec_documents", ["id"], ["source_system_id", "sha256"], schema)
            cur.execute(sql, (source_system_id, sha256))
            spec_document_id = cur.fetchone()[0]
            
            conn.commit()
            conn.close()
            
            # Backfill IDs
            spec_doc.id = spec_document_id
            spec_doc.source_system_id = source_system_id

            # Chunk this document
            doc_chunks = _chunk_content(content)

            # Stream chunks to DB immediately
            def chunk_iterator() -> Iterator[Tuple[int, str]]:
                nonlocal global_chunk_index
                for chunk in doc_chunks:
                    yield (global_chunk_index, chunk)
                    global_chunk_index += 1

            # Track URI mapping before streaming
            start_idx = total_chunk_count
            for i in range(len(doc_chunks)):
                chunk_index_to_uri[start_idx + i] = ref

            # Stream chunks to database
            chunk_ids = stream_chunks_to_silver(
                chunk_iterator(),
                spec_document_id,
            )
            all_chunk_ids.extend(chunk_ids)
            total_chunk_count += len(doc_chunks)
            
            # Reset global_chunk_index for next document (we used it in iterator)
            # Actually, no - we want global indices across all docs

            logger.info(f"Streamed {len(doc_chunks)} chunks for {ref} (spec_document_id={spec_document_id})")

        except Exception as e:
            state.errors.append(f"Failed to ingest spec from {ref}: {str(e)}")
            logger.error(f"Failed to ingest spec from {ref}: {e}")

    # In streaming mode, keep doc_chunks empty
    state.doc_chunks = []
    
    # Store chunk IDs for downstream nodes
    state.spec_chunk_ids = all_chunk_ids
    state.chunk_count = total_chunk_count

    # Store mapping in plan for downstream nodes
    if state.plan is not None:
        state.plan["chunk_index_to_spec_document_uri"] = chunk_index_to_uri
        state.plan["streaming_mode"] = True

    # Mark that chunks are already persisted
    state.persisted_ids["chunks_streamed"] = True
    state.persisted_ids["chunk_count"] = total_chunk_count

    state.completed_steps.append("ingest_spec")
    logger.info(f"Streaming ingest complete: {total_chunk_count} chunks streamed to DB")
    return state
