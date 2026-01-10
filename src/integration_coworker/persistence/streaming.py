"""
Streaming persistence for large data.

V3 Implementation: Writes chunks and embeddings to DB immediately without
accumulating in memory. All functions are transaction-safe and idempotent.

V4 Enhancement: Added progress tracking for crash recovery/resume.
See docs/PROD_GAP_CLOSURE_PLAN_KG_DB_STREAMING.md Phase 3.

This module is used when STREAMING_PERSISTENCE=true to reduce memory footprint
from 200MB+ to <20MB for large specs.

Design Doc Reference: V3_STREAMING_PERSISTENCE_PLAN.md
"""
from typing import Iterator, List, Optional, Tuple, Dict, Any
from datetime import datetime
import logging
import hashlib
import json

from integration_coworker.persistence.db import get_connection, get_engine_type
from integration_coworker.persistence.sql_helpers import (
    select_by_columns,
    placeholder,
    table_name,
)
from integration_coworker.persistence.upsert import InsertDoNothingBuilder

logger = logging.getLogger(__name__)

# Batch sizes for streaming operations
CHUNK_BATCH_SIZE = 100  # Write chunks in batches of 100
# Bug #58 fix: Reduced batch size from 50 to 25 to avoid rate limits
EMBEDDING_BATCH_SIZE = 25  # Update embeddings in batches of 25

# Schema prefix for Postgres tables
SILVER_SCHEMA = "spec_silver"
BRONZE_SCHEMA = "spec_bronze"
GOLD_SCHEMA = "integration_gold"


def _get_schema(layer: str = "silver") -> Optional[str]:
    """Get schema prefix based on engine type."""
    engine = get_engine_type()
    if engine != "postgres":
        return None
    return SILVER_SCHEMA if layer == "silver" else BRONZE_SCHEMA


def stream_raw_spec_to_bronze(
    content: str,
    uri: str,
    content_type: str,
    source_system_id: int,
) -> int:
    """
    Stream raw spec content to spec_bronze.raw_specs immediately.
    
    This is called by ingest_spec when streaming is enabled, allowing
    the raw content to be written to DB and cleared from memory.
    
    Args:
        content: Raw spec content (string)
        uri: Source URI (file path or URL)
        content_type: MIME type of the content
        source_system_id: FK to source_systems (required)
    
    Returns:
        raw_spec_id: The ID of the inserted/existing record
    
    Idempotent: Uses SHA256 hash for deduplication.
    """
    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    
    engine = get_engine_type()
    schema = _get_schema("bronze")
    conn = get_connection()
    cur = conn.cursor()
    
    insert_builder = InsertDoNothingBuilder(
        "raw_specs",
        ["uri", "content_type", "sha256", "raw_content", "source_system_id"],
        conflict_columns=["source_system_id", "sha256"],
        schema=schema,
    )

    try:
        # First, check if already exists (using unique constraint columns)
        if engine == "postgres":
            sql = """SELECT id FROM spec_bronze.raw_specs 
                     WHERE source_system_id = %s AND sha256 = %s"""
            cur.execute(sql, (source_system_id, sha256))
        else:
            sql = "SELECT id FROM raw_specs WHERE source_system_id = ? AND sha256 = ?"
            cur.execute(sql, (source_system_id, sha256))
        row = cur.fetchone()
        
        if row:
            conn.close()
            logger.debug(f"Raw spec already exists: {uri} (id={row[0]})")
            return row[0]
        
        # Insert new raw spec (idempotent)
        sql, params = insert_builder.render(
            {
                "uri": uri,
                "content_type": content_type,
                "sha256": sha256,
                "raw_content": content.encode("utf-8"),
                "source_system_id": source_system_id,
            }
        )
        cur.execute(sql, params)

        # Fetch ID deterministically
        if engine == "postgres":
            cur.execute(
                """
                    SELECT id FROM spec_bronze.raw_specs 
                    WHERE source_system_id = %s AND sha256 = %s
                """,
                (source_system_id, sha256),
            )
        else:
            cur.execute("SELECT id FROM raw_specs WHERE sha256 = ?", (sha256,))
        raw_spec_id = cur.fetchone()[0]
        
        conn.commit()
        conn.close()
        
        logger.info(f"Streamed raw spec to bronze: {uri} (id={raw_spec_id})")
        return raw_spec_id
        
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Failed to stream raw spec: {e}")
        raise


def stream_chunks_to_silver(
    chunks: Iterator[Tuple[int, str]],
    spec_document_id: int,
    batch_size: int = CHUNK_BATCH_SIZE,
) -> List[int]:
    """
    Stream content chunks to spec_silver.spec_chunks immediately.
    
    This is called by ingest_spec when streaming is enabled. Chunks are
    written in batches for efficiency, with embeddings left as NULL
    to be filled by embed_spec_chunks later.
    
    Args:
        chunks: Iterator of (chunk_index, content) tuples
        spec_document_id: FK to spec_documents
        batch_size: Number of chunks per batch write
    
    Returns:
        List of chunk IDs in order
    
    Idempotent: Uses (spec_document_id, chunk_index) as unique key.
    """
    engine = get_engine_type()
    schema = _get_schema("silver")
    conn = get_connection()
    cur = conn.cursor()
    
    chunk_ids = []
    batch = []
    
    try:
        for chunk_index, content in chunks:
            batch.append((spec_document_id, chunk_index, content))
            
            if len(batch) >= batch_size:
                ids = _write_chunk_batch(cur, batch, engine, schema)
                chunk_ids.extend(ids)
                batch = []
        
        # Write remaining batch
        if batch:
            ids = _write_chunk_batch(cur, batch, engine, schema)
            chunk_ids.extend(ids)
        
        conn.commit()
        conn.close()
        
        logger.info(f"Streamed {len(chunk_ids)} chunks to silver (spec_document_id={spec_document_id})")
        return chunk_ids
        
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Failed to stream chunks: {e}")
        raise


def stream_chunks_to_silver_with_progress(
    chunks: Iterator[Tuple[int, str]],
    spec_document_id: int,
    run_id: Optional[str] = None,
    total_chunks: Optional[int] = None,
    batch_size: int = CHUNK_BATCH_SIZE,
) -> List[int]:
    """
    Stream content chunks to spec_silver.spec_chunks with progress tracking.
    
    V4 Enhancement: Per-batch commit with checkpoint for crash recovery.
    
    This version commits after each batch and saves progress, enabling:
    - Resume from crash/shutdown
    - Cancellation with partial commit
    - Progress visibility
    
    Args:
        chunks: Iterator of (chunk_index, content) tuples
        spec_document_id: FK to spec_documents
        run_id: Optional run identifier for progress tracking/resume
        total_chunks: Total chunks expected (for progress reporting)
        batch_size: Number of chunks per batch write/commit
    
    Returns:
        List of chunk IDs in order
    
    Resume behavior:
        If run_id provided and progress exists, skips chunks <= last_committed_index.
        
    Idempotent: Uses (spec_document_id, chunk_index) as unique key.
    """
    from integration_coworker.shutdown import is_shutdown_requested
    
    engine = get_engine_type()
    schema = _get_schema("silver")
    
    # V4: Load checkpoint for resume
    phase = f"chunks:{spec_document_id}"
    start_after = -1  # Start from index 0 by default
    if run_id:
        progress = load_streaming_progress(run_id, phase)
        if progress and progress.get("last_committed_id") is not None:
            start_after = progress["last_committed_id"]
            logger.info(
                f"Resuming chunk streaming from index={start_after} "
                f"for spec_document_id={spec_document_id}, run_id={run_id}"
            )
    
    conn = get_connection()
    cur = conn.cursor()
    
    chunk_ids = []
    batch = []
    last_committed_index = start_after
    chunks_skipped = 0
    chunks_written = 0
    cancelled = False
    
    try:
        for chunk_index, content in chunks:
            # Skip already-persisted chunks (resume support)
            if chunk_index <= start_after:
                chunks_skipped += 1
                continue
            
            batch.append((spec_document_id, chunk_index, content))
            
            if len(batch) >= batch_size:
                # Write batch
                ids = _write_chunk_batch(cur, batch, engine, schema)
                chunk_ids.extend(ids)
                chunks_written += len(batch)
                
                # Commit batch
                conn.commit()
                last_committed_index = chunk_index
                
                # Save progress checkpoint
                if run_id:
                    save_streaming_progress(
                        run_id, phase, last_committed_index,
                        total_chunks or 0
                    )
                
                # Check for cancellation
                if is_shutdown_requested():
                    logger.warning(
                        f"Shutdown during chunk streaming at index={last_committed_index}, "
                        f"written={chunks_written}, remaining will be skipped"
                    )
                    cancelled = True
                    break
                
                batch = []
        
        # Write remaining batch (if not cancelled)
        if batch and not cancelled:
            ids = _write_chunk_batch(cur, batch, engine, schema)
            chunk_ids.extend(ids)
            chunks_written += len(batch)
            conn.commit()
            last_committed_index = batch[-1][1]  # Last chunk_index in batch (from tuple)
            
            if run_id:
                save_streaming_progress(
                    run_id, phase, last_committed_index,
                    total_chunks or 0
                )
        
        conn.close()
        
        # Clear progress on successful completion (not cancelled)
        if run_id and not cancelled:
            clear_streaming_progress(run_id, phase)
        
        logger.info(
            f"Streamed {chunks_written} chunks to silver "
            f"(spec_document_id={spec_document_id}, skipped={chunks_skipped})"
        )
        return chunk_ids
        
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Failed to stream chunks: {e}")
        raise


def _write_chunk_batch(
    cur,
    batch: List[Tuple[int, int, str]],
    engine: str,
    schema: Optional[str],
) -> List[int]:
    """Write a batch of chunks and return their IDs."""
    ids = []
    insert_builder = InsertDoNothingBuilder(
        "spec_chunks",
        ["spec_document_id", "chunk_index", "content"],
        conflict_columns=["spec_document_id", "chunk_index"],
        schema=schema,
    )

    for spec_document_id, chunk_index, content in batch:
        sql, params = insert_builder.render(
            {
                "spec_document_id": spec_document_id,
                "chunk_index": chunk_index,
                "content": content,
            }
        )
        cur.execute(sql, params)

        if engine == "postgres":
            cur.execute(
                """
                    SELECT id FROM spec_silver.spec_chunks 
                    WHERE spec_document_id = %s AND chunk_index = %s
                """,
                (spec_document_id, chunk_index),
            )
        else:
            cur.execute(
                """
                    SELECT id FROM spec_chunks 
                    WHERE spec_document_id = ? AND chunk_index = ?
                """,
                (spec_document_id, chunk_index),
            )
        ids.append(cur.fetchone()[0])

    return ids


def stream_embedding_update(
    chunk_id: int,
    embedding: List[float],
) -> bool:
    """
    Update a single chunk's embedding in the database.
    
    This is called by embed_spec_chunks when streaming is enabled.
    Each embedding is written immediately after computation, avoiding
    accumulation in memory.
    
    Args:
        chunk_id: The spec_chunks.id to update
        embedding: The embedding vector (1536 dim for OpenAI)
    
    Returns:
        True if update succeeded, False otherwise
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        embedding_json = json.dumps(embedding)
        
        if engine == "postgres":
            cur.execute("""
                UPDATE spec_silver.spec_chunks 
                SET embedding = %s
                WHERE id = %s
            """, (embedding_json, chunk_id))
        else:
            cur.execute("""
                UPDATE spec_chunks 
                SET embedding = ?
                WHERE id = ?
            """, (embedding_json, chunk_id))
        
        conn.commit()
        conn.close()
        return True
        
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Failed to update embedding for chunk {chunk_id}: {e}")
        return False


def stream_embedding_batch(
    updates: List[Tuple[int, List[float]]],
    run_id: Optional[str] = None,
    phase: str = "embeddings",
    total_items: Optional[int] = None,
) -> int:
    """
    Batch update multiple chunk embeddings with optional progress tracking.
    
    V4 Enhancement: Added progress tracking for resume support.
    When run_id is provided:
    - Loads last checkpoint and skips already-processed IDs
    - Saves progress after successful batch commit
    - Clears progress on completion (when all items processed)
    
    Args:
        updates: List of (chunk_id, embedding) tuples
        run_id: Optional run identifier for progress tracking
        phase: Progress phase name (default "embeddings")
        total_items: Total items to process (for progress %; uses len(updates) if None)
    
    Returns:
        Number of successfully updated chunks (excluding skipped)
    """
    if not updates:
        return 0
    
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    # V4: Load checkpoint if run_id provided
    skip_until = 0
    if run_id:
        progress = load_streaming_progress(run_id, phase)
        if progress and progress.get("last_committed_id"):
            skip_until = progress["last_committed_id"]
            logger.info(f"Resuming {phase} from chunk_id={skip_until} for run_id={run_id}")
    
    success_count = 0
    last_id = skip_until
    effective_total = total_items if total_items is not None else len(updates)
    
    try:
        for chunk_id, embedding in updates:
            # V4: Skip already-processed chunks (resume support)
            if chunk_id <= skip_until:
                continue
            
            embedding_json = json.dumps(embedding)
            
            if engine == "postgres":
                cur.execute("""
                    UPDATE spec_silver.spec_chunks 
                    SET embedding = %s
                    WHERE id = %s
                """, (embedding_json, chunk_id))
            else:
                cur.execute("""
                    UPDATE spec_chunks 
                    SET embedding = ?
                    WHERE id = ?
                """, (embedding_json, chunk_id))
            
            success_count += 1
            last_id = chunk_id
        
        conn.commit()
        conn.close()
        
        # V4: Save progress checkpoint after commit
        if run_id and success_count > 0:
            save_streaming_progress(run_id, phase, last_id, effective_total)
            logger.debug(f"Saved progress: run_id={run_id}, phase={phase}, last_id={last_id}")
        
        logger.debug(f"Batch updated {success_count} embeddings")
        return success_count
        
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Failed to batch update embeddings: {e}")
        return success_count


def get_chunk_ids_for_spec(spec_document_id: int) -> List[int]:
    """
    Get all chunk IDs for a spec document.
    
    Used by nodes that need to know which chunks exist without
    loading all content into memory.
    
    Args:
        spec_document_id: The spec_documents.id
    
    Returns:
        List of chunk IDs in chunk_index order
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT id FROM spec_silver.spec_chunks 
                WHERE spec_document_id = %s
                ORDER BY chunk_index
            """, (spec_document_id,))
        else:
            cur.execute("""
                SELECT id FROM spec_chunks 
                WHERE spec_document_id = ?
                ORDER BY chunk_index
            """, (spec_document_id,))
        
        rows = cur.fetchall()
        conn.close()
        
        return [row[0] for row in rows]
        
    except Exception as e:
        conn.close()
        logger.error(f"Failed to get chunk IDs: {e}")
        return []


def get_chunks_without_embeddings(spec_document_id: int) -> List[Tuple[int, int, str]]:
    """
    Get chunks that don't have embeddings yet.
    
    Used by embed_spec_chunks to identify which chunks need embedding.
    
    Args:
        spec_document_id: The spec_documents.id
    
    Returns:
        List of (chunk_id, chunk_index, content) tuples
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT id, chunk_index, content 
                FROM spec_silver.spec_chunks 
                WHERE spec_document_id = %s AND embedding IS NULL
                ORDER BY chunk_index
            """, (spec_document_id,))
        else:
            cur.execute("""
                SELECT id, chunk_index, content 
                FROM spec_chunks 
                WHERE spec_document_id = ? AND embedding IS NULL
                ORDER BY chunk_index
            """, (spec_document_id,))
        
        rows = cur.fetchall()
        conn.close()
        
        return [(row[0], row[1], row[2]) for row in rows]
        
    except Exception as e:
        conn.close()
        logger.error(f"Failed to get chunks without embeddings: {e}")
        return []


# ---------------------------------------------------------------------------
# V4 Enhancement: Progress Tracking for Resume Support
# See docs/PROD_GAP_CLOSURE_PLAN_KG_DB_STREAMING.md Phase 3
# ---------------------------------------------------------------------------

def init_streaming_progress_table() -> None:
    """
    Initialize the streaming_progress table if it doesn't exist.
    
    Called automatically by save_streaming_progress.
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                CREATE TABLE IF NOT EXISTS integration_gold.streaming_progress (
                    run_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    last_committed_id BIGINT,
                    total_items BIGINT,
                    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (run_id, phase)
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS streaming_progress (
                    run_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    last_committed_id INTEGER,
                    total_items INTEGER,
                    started_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (run_id, phase)
                )
            """)
        conn.commit()
        conn.close()
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.warning(f"Failed to create streaming_progress table: {e}")


def save_streaming_progress(
    run_id: str,
    phase: str,
    last_committed_id: int,
    total_items: int,
) -> bool:
    """
    Save/update streaming progress checkpoint.
    
    V4 Enhancement: Enables crash recovery by tracking progress.
    
    Args:
        run_id: Unique identifier for the workflow run (e.g., run_id or spec_document_id)
        phase: Processing phase ('chunks' or 'embeddings')
        last_committed_id: Last successfully committed item ID
        total_items: Total number of items to process
        
    Returns:
        True if save succeeded
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        # Ensure table exists
        init_streaming_progress_table()
        
        if engine == "postgres":
            cur.execute("""
                INSERT INTO integration_gold.streaming_progress 
                    (run_id, phase, last_committed_id, total_items, started_at, updated_at)
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (run_id, phase) 
                DO UPDATE SET 
                    last_committed_id = EXCLUDED.last_committed_id,
                    total_items = EXCLUDED.total_items,
                    updated_at = NOW()
            """, (run_id, phase, last_committed_id, total_items))
        else:
            # SQLite upsert
            cur.execute("""
                INSERT INTO streaming_progress 
                    (run_id, phase, last_committed_id, total_items, started_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT (run_id, phase) 
                DO UPDATE SET 
                    last_committed_id = excluded.last_committed_id,
                    total_items = excluded.total_items,
                    updated_at = datetime('now')
            """, (run_id, phase, last_committed_id, total_items))
        
        conn.commit()
        conn.close()
        logger.debug(f"Saved streaming progress: run_id={run_id}, phase={phase}, last_id={last_committed_id}")
        return True
        
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Failed to save streaming progress: {e}")
        return False


def load_streaming_progress(run_id: str, phase: str) -> Optional[Dict[str, Any]]:
    """
    Load streaming progress for resume.
    
    V4 Enhancement: Enables resuming from last checkpoint after crash.
    
    Args:
        run_id: Unique identifier for the workflow run
        phase: Processing phase ('chunks' or 'embeddings')
        
    Returns:
        Dict with keys: run_id, phase, last_committed_id, total_items, started_at, updated_at
        or None if no progress found
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT run_id, phase, last_committed_id, total_items, started_at, updated_at
                FROM integration_gold.streaming_progress
                WHERE run_id = %s AND phase = %s
            """, (run_id, phase))
        else:
            cur.execute("""
                SELECT run_id, phase, last_committed_id, total_items, started_at, updated_at
                FROM streaming_progress
                WHERE run_id = ? AND phase = ?
            """, (run_id, phase))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "run_id": row[0],
            "phase": row[1],
            "last_committed_id": row[2],
            "total_items": row[3],
            "started_at": row[4],
            "updated_at": row[5],
        }
        
    except Exception as e:
        conn.close()
        logger.warning(f"Failed to load streaming progress: {e}")
        return None


def clear_streaming_progress(run_id: str, phase: Optional[str] = None) -> bool:
    """
    Clear progress after successful completion.
    
    V4 Enhancement: Cleanup after successful processing.
    
    Args:
        run_id: Unique identifier for the workflow run
        phase: Optional - clear specific phase, or all phases if None
        
    Returns:
        True if clear succeeded
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if phase:
            if engine == "postgres":
                cur.execute("""
                    DELETE FROM integration_gold.streaming_progress
                    WHERE run_id = %s AND phase = %s
                """, (run_id, phase))
            else:
                cur.execute("""
                    DELETE FROM streaming_progress
                    WHERE run_id = ? AND phase = ?
                """, (run_id, phase))
        else:
            if engine == "postgres":
                cur.execute("""
                    DELETE FROM integration_gold.streaming_progress
                    WHERE run_id = %s
                """, (run_id,))
            else:
                cur.execute("""
                    DELETE FROM streaming_progress
                    WHERE run_id = ?
                """, (run_id,))
        
        conn.commit()
        conn.close()
        logger.debug(f"Cleared streaming progress: run_id={run_id}, phase={phase or 'all'}")
        return True
        
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Failed to clear streaming progress: {e}")
        return False


def stream_embeddings_with_progress(
    run_id: str,
    updates: Iterator[Tuple[int, List[float]]],
    total_items: int,
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> int:
    """
    Stream embeddings with progress tracking for resume support.
    
    V4 Enhancement: Tracks progress and supports resuming from crash.
    
    Args:
        run_id: Unique identifier for the workflow run
        updates: Iterator of (chunk_id, embedding) tuples
        total_items: Total number of embeddings to process
        batch_size: Number of embeddings per batch commit
        
    Returns:
        Number of successfully updated chunks
    """
    # Load checkpoint if resuming
    start_after = 0
    progress = load_streaming_progress(run_id, "embeddings")
    if progress and progress.get("last_committed_id"):
        start_after = progress["last_committed_id"]
        logger.info(f"Resuming embeddings from id={start_after} for run_id={run_id}")
    
    engine = get_engine_type()
    success_count = 0
    batch_count = 0
    last_id = start_after
    
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        for chunk_id, embedding in updates:
            # Skip already processed
            if chunk_id <= start_after:
                continue
            
            embedding_json = json.dumps(embedding)
            
            if engine == "postgres":
                cur.execute("""
                    UPDATE spec_silver.spec_chunks 
                    SET embedding = %s
                    WHERE id = %s
                """, (embedding_json, chunk_id))
            else:
                cur.execute("""
                    UPDATE spec_chunks 
                    SET embedding = ?
                    WHERE id = ?
                """, (embedding_json, chunk_id))
            
            success_count += 1
            batch_count += 1
            last_id = chunk_id
            
            # Commit and save progress every batch
            if batch_count >= batch_size:
                conn.commit()
                save_streaming_progress(run_id, "embeddings", last_id, total_items)
                batch_count = 0
        
        # Final commit
        if batch_count > 0:
            conn.commit()
            save_streaming_progress(run_id, "embeddings", last_id, total_items)
        
        conn.close()
        
        # Clear progress on completion
        if success_count > 0:
            clear_streaming_progress(run_id, "embeddings")
        
        logger.info(f"Streamed {success_count} embeddings with progress tracking (run_id={run_id})")
        return success_count
        
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Failed to stream embeddings with progress: {e}")
        # Progress is already saved at last checkpoint
        return success_count