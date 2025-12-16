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
    
    V1.1 Spec Caching (FT-001):
    - Computes SHA-256 hash of spec content
    - Checks DB for existing spec_document with matching hash
    - If found (cache hit): Sets state.cache_hit=True, skips persistence
    - If not found (cache miss): Proceeds with normal persistence

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

    Reads: spec_refs, plan, provider_code, options.no_cache
    Writes: source_refs, spec_documents, doc_chunks (legacy) OR spec_chunk_ids (streaming),
            plan["chunk_index_to_spec_document_uri"], cache_hit
    """
    if not state.spec_refs:
        state.errors.append("No spec_refs provided")
        state.completed_steps.append("ingest_spec")
        return state

    # Ensure outputs are initialized (some test fixtures construct WorkflowState
    # without these lists).
    if not state.spec_documents:
        state.spec_documents = []

    # API-002: Create SourceRef objects for each spec
    if not state.source_refs:
        state.source_refs = []
    
    for ref in state.spec_refs:
        source_ref = SourceRef.from_ref(ref, provider_code=state.provider_code)
        state.source_refs.append(source_ref)
    
    logger.debug(f"Created {len(state.source_refs)} SourceRef objects")

    # V1.1: Check if caching is disabled via CLI flag
    no_cache = False
    if state.options and hasattr(state.options, 'no_cache'):
        no_cache = state.options.no_cache
    
    # If streaming is explicitly disabled, use legacy mode
    if is_streaming_persistence_disabled():
        logger.debug("Streaming persistence disabled, using legacy mode")
        return _ingest_spec_legacy(state, no_cache=no_cache)
    
    # Keep a minimal in-memory handoff for downstream parsing.
    # Tests and some pipeline paths expect ingest_spec to populate pending_specs
    # with raw content, even when streaming persistence is enabled.
    if not getattr(state, "pending_specs", None):
        state.pending_specs = []

    # Fetch all specs first to determine total size
    fetched_specs = []
    total_bytes = 0
    
    for ref in state.spec_refs:
        try:
            content, content_type = _fetch_spec_content(ref)
            fetched_specs.append((ref, content, content_type))
            total_bytes += len(content.encode("utf-8"))

            # Lightweight in-memory handoff for parse stage
            state.pending_specs.append({"ref": ref, "content": content, "content_type": content_type})
        except Exception as e:
            state.errors.append(f"Failed to fetch spec from {ref}: {str(e)}")
    
    if not fetched_specs:
        state.errors.append("No specs could be fetched")
        state.completed_steps.append("ingest_spec")
        return state
    
    # V1.1: Check cache BEFORE deciding streaming mode
    if not no_cache:
        cache_result = _check_spec_cache(fetched_specs, state.provider_code)
        if cache_result:
            logger.info(f"Spec cache hit! Using existing spec_document id={cache_result.id}")
            state.spec_documents = [cache_result]
            state.cache_hit = True
            state.completed_steps.append("ingest_spec")
            return state
    
    # Cache miss - proceed with normal ingestion
    state.cache_hit = False
    
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


def _check_spec_cache(
    fetched_specs: List[Tuple[str, str, str]],
    provider_code: str = None,
) -> SpecDocument | None:
    """
    V1.1 (FT-001): Check if specs already exist in the database.
    
    Computes SHA-256 hash of all fetched spec content and checks
    if a matching spec_document exists in the database.
    
    Args:
        fetched_specs: List of (uri, content, content_type) tuples
        provider_code: Optional provider code for filtering
        
    Returns:
        SpecDocument if cache hit, None if cache miss
    """
    from integration_coworker.persistence import db
    from integration_coworker.persistence.sql_helpers import get_engine_type
    
    if not fetched_specs:
        return None
    
    # For now, we only cache single-spec scenarios
    # Multi-spec caching is more complex (need to match ALL specs)
    if len(fetched_specs) > 1:
        logger.debug("Multi-spec scenario, skipping cache check")
        return None
    
    ref, content, content_type = fetched_specs[0]
    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    
    try:
        db.init_schema()
        conn = db.get_connection()
        engine = get_engine_type()
        cur = conn.cursor()
        
        # IMPORTANT: Scope cache hits to provider_code when available.
        # The Silver layer (endpoints/schemas/entities) is keyed by source_system
        # which is provider-specific. Reusing a spec_document row from a different
        # provider_code can yield a "cache hit" but no corresponding endpoints.
        if engine == "postgres":
            if provider_code:
                cur.execute("""
                    SELECT sd.id, sd.source_system_id, sd.version, sd.uri, sd.content_type, sd.sha256
                    FROM spec_silver.spec_documents sd
                    JOIN spec_silver.source_systems ss ON ss.id = sd.source_system_id
                    WHERE sd.sha256 = %s AND ss.code = %s
                    ORDER BY sd.id DESC
                    LIMIT 1
                """, (sha256, provider_code))
            else:
                cur.execute("""
                    SELECT id, source_system_id, version, uri, content_type, sha256
                    FROM spec_silver.spec_documents
                    WHERE sha256 = %s
                    ORDER BY id DESC
                    LIMIT 1
                """, (sha256,))
        else:
            cur.execute("""
                SELECT id, source_system_id, version, uri, content_type, sha256
                FROM spec_documents
                WHERE sha256 = ?
                ORDER BY id DESC
                LIMIT 1
            """, (sha256,))
        
        row = cur.fetchone()
        conn.close()
        
        if row:
            logger.info(f"Spec cache hit: sha256={sha256[:16]}... -> id={row[0]}")
            return SpecDocument(
                id=row[0],
                source_system_id=row[1],
                version=row[2],
                uri=row[3],
                content_type=row[4],
                sha256=row[5],
                content=content,  # Provide content for downstream parsing
            )
        else:
            logger.debug(f"Spec cache miss: sha256={sha256[:16]}...")
            return None
            
    except Exception as e:
        logger.warning(f"Spec cache check failed: {e}")
        return None


def _ingest_spec_legacy(state: WorkflowState, no_cache: bool = False) -> WorkflowState:
    """
    Legacy ingest mode: accumulate chunks in memory.
    
    Original behavior - all chunks held in state.doc_chunks until
    persist_silver_checkpoint writes them to DB.
    
    Note: This fetches specs itself. For pre-fetched specs, use
    _ingest_spec_legacy_with_fetched().
    
    Args:
        state: WorkflowState to update
        no_cache: If True, skip cache check (V1.1 FT-001)
    """
    # Fetch specs first
    fetched_specs = []
    for ref in state.spec_refs:
        try:
            content, content_type = _fetch_spec_content(ref)
            fetched_specs.append((ref, content, content_type))
        except Exception as e:
            state.errors.append(f"Failed to fetch spec from {ref}: {str(e)}")
    
    if not fetched_specs:
        state.errors.append("No specs could be fetched")
        state.completed_steps.append("ingest_spec")
        return state
    
    # V1.1: Check cache before proceeding
    if not no_cache:
        cache_result = _check_spec_cache(fetched_specs, state.provider_code)
        if cache_result:
            logger.info(f"Spec cache hit! Using existing spec_document id={cache_result.id}")
            state.spec_documents = [cache_result]
            state.cache_hit = True
            state.completed_steps.append("ingest_spec")
            return state
    
    state.cache_hit = False
    
    all_chunks: list[str] = []
    chunk_index_to_uri: dict[int, str] = {}

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
        state.spec_documents.append(spec_doc)

        # Chunk this document
        doc_chunks = _chunk_content(content)

        # Track which chunks belong to which spec document
        start_idx = len(all_chunks)
        for i, chunk in enumerate(doc_chunks):
            chunk_index_to_uri[start_idx + i] = ref
        all_chunks.extend(doc_chunks)

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
    V2.1 (GAP-01): Stores raw spec bytes to Bronze layer for audit trail.
    
    Args:
        fetched_specs: List of (uri, content, content_type) tuples
    """
    from integration_coworker.persistence.streaming import stream_raw_spec_to_bronze
    from integration_coworker.persistence import db
    from integration_coworker.persistence.sql_helpers import upsert_ignore, select_by_columns, get_engine_type
    
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

    # Get or create source_system FIRST so we have the ID for Bronze storage
    db.init_schema()
    engine = get_engine_type()
    schema = "spec_silver" if engine == "postgres" else None
    provider_code = state.provider_code or "unknown"
    conn = db.get_connection()
    cur = conn.cursor()
    sql = upsert_ignore("source_systems", ["code", "display_name"], ["code"], schema)
    cur.execute(sql, (provider_code, provider_code.replace("_", " ").title()))
    sql = select_by_columns("source_systems", ["id"], ["code"], schema)
    cur.execute(sql, (provider_code,))
    source_system_id = cur.fetchone()[0]
    conn.commit()
    conn.close()

    for ref, content, content_type in fetched_specs:
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # V2.1 (GAP-01): Store raw spec to Bronze layer for audit trail
        try:
            raw_spec_id = stream_raw_spec_to_bronze(
                content=content,
                uri=ref,
                content_type=content_type,
                source_system_id=source_system_id,
            )
            logger.debug(f"Stored raw spec to bronze layer: {ref} (id={raw_spec_id})")
        except Exception as e:
            # Non-fatal: Bronze storage is for audit, not critical path
            logger.warning(f"Failed to store raw spec to bronze layer: {e}")

        # V1.1 (FT-001): Persist spec_document NOW for cache lookups
        # Previously this was only done in persist_silver_checkpoint, but that
        # runs AFTER ingest_spec, so cache checks would always miss.
        spec_document_id = None
        conn = None
        try:
            conn = db.get_connection()
            cur = conn.cursor()
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
        finally:
            if conn:
                conn.close()

        # Create SpecDocument for this ref with persisted ID
        spec_doc = SpecDocument(
            id=spec_document_id,
            source_system_id=source_system_id,
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
    
    V2.1 (GAP-01): Also stores raw spec bytes to Bronze layer for audit trail.
    
    Args:
        fetched_specs: List of (uri, content, content_type) tuples
    """
    from integration_coworker.persistence import db
    from integration_coworker.persistence.streaming import stream_chunks_to_silver, stream_raw_spec_to_bronze
    from integration_coworker.persistence.sql_helpers import upsert_ignore, select_by_columns, get_engine_type

    # Initialize schema for streaming writes
    db.init_schema()
    
    chunk_index_to_uri: dict[int, str] = {}
    all_chunk_ids: list[int] = []
    total_chunk_count = 0
    global_chunk_index = 0

    engine = get_engine_type()
    schema = "spec_silver" if engine == "postgres" else None

    # Get or create source_system FIRST so we have the ID for all operations
    provider_code = state.provider_code or "unknown"
    conn = db.get_connection()
    cur = conn.cursor()
    sql = upsert_ignore("source_systems", ["code", "display_name"], ["code"], schema)
    cur.execute(sql, (provider_code, provider_code.replace("_", " ").title()))
    sql = select_by_columns("source_systems", ["id"], ["code"], schema)
    cur.execute(sql, (provider_code,))
    source_system_id = cur.fetchone()[0]
    conn.commit()
    conn.close()

    for ref, content, content_type in fetched_specs:
        try:
            sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

            # V2.1 (GAP-01): Store raw spec to Bronze layer for audit trail
            try:
                raw_spec_id = stream_raw_spec_to_bronze(
                    content=content,
                    uri=ref,
                    content_type=content_type,
                    source_system_id=source_system_id,
                )
                logger.debug(f"Stored raw spec to bronze layer: {ref} (id={raw_spec_id})")
            except Exception as e:
                # Non-fatal: Bronze storage is for audit, not critical path
                logger.warning(f"Failed to store raw spec to bronze layer: {e}")

            # Create SpecDocument for this ref (content stored for parsing, will be cleared later)
            spec_doc = SpecDocument(
                id=None,
                source_system_id=source_system_id,
                version="1.0",
                uri=ref,
                content_type=content_type,
                sha256=sha256,
                content=content,  # Needed for detect_and_parse_spec
            )
            state.spec_documents.append(spec_doc)

            # We need to persist the spec_document to get an ID
            conn = db.get_connection()
            cur = conn.cursor()
            
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
