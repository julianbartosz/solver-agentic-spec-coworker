"""
Streaming persistence for large data.

V3 Implementation: Writes chunks and embeddings to DB immediately without
accumulating in memory. All functions are transaction-safe and idempotent.

This module is used when STREAMING_PERSISTENCE=true to reduce memory footprint
from 200MB+ to <20MB for large specs.

Design Doc Reference: V3_STREAMING_PERSISTENCE_PLAN.md
"""
from typing import Iterator, List, Optional, Tuple, Dict, Any
import logging
import hashlib
import json

from integration_coworker.persistence.db import get_connection, get_engine_type
from integration_coworker.persistence.sql_helpers import (
    upsert_ignore,
    select_by_columns,
    placeholder,
    table_name,
)

logger = logging.getLogger(__name__)

# Batch sizes for streaming operations
CHUNK_BATCH_SIZE = 100  # Write chunks in batches of 100
EMBEDDING_BATCH_SIZE = 50  # Update embeddings in batches of 50

# Schema prefix for Postgres tables
SILVER_SCHEMA = "spec_silver"
BRONZE_SCHEMA = "spec_bronze"


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
        
        # Insert new raw spec
        if engine == "postgres":
            cur.execute("""
                INSERT INTO spec_bronze.raw_specs 
                    (uri, content_type, sha256, raw_content, source_system_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (source_system_id, sha256) DO NOTHING
                RETURNING id
            """, (uri, content_type, sha256, content.encode("utf-8"), source_system_id))
            result = cur.fetchone()
            if result:
                raw_spec_id = result[0]
            else:
                # Conflict occurred, fetch existing
                cur.execute("""
                    SELECT id FROM spec_bronze.raw_specs 
                    WHERE source_system_id = %s AND sha256 = %s
                """, (source_system_id, sha256,))
                raw_spec_id = cur.fetchone()[0]
        else:
            # SQLite
            cur.execute("""
                INSERT OR IGNORE INTO raw_specs 
                    (uri, content_type, sha256, raw_content, source_system_id)
                VALUES (?, ?, ?, ?, ?)
            """, (uri, content_type, sha256, content.encode("utf-8"), source_system_id))
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


def _write_chunk_batch(
    cur,
    batch: List[Tuple[int, int, str]],
    engine: str,
    schema: Optional[str],
) -> List[int]:
    """Write a batch of chunks and return their IDs."""
    ids = []
    
    for spec_document_id, chunk_index, content in batch:
        if engine == "postgres":
            cur.execute("""
                INSERT INTO spec_silver.spec_chunks 
                    (spec_document_id, chunk_index, content)
                VALUES (%s, %s, %s)
                ON CONFLICT (spec_document_id, chunk_index) DO NOTHING
                RETURNING id
            """, (spec_document_id, chunk_index, content))
            result = cur.fetchone()
            if result:
                ids.append(result[0])
            else:
                # Already exists, fetch ID
                cur.execute("""
                    SELECT id FROM spec_silver.spec_chunks 
                    WHERE spec_document_id = %s AND chunk_index = %s
                """, (spec_document_id, chunk_index))
                ids.append(cur.fetchone()[0])
        else:
            # SQLite
            cur.execute("""
                INSERT OR IGNORE INTO spec_chunks 
                    (spec_document_id, chunk_index, content)
                VALUES (?, ?, ?)
            """, (spec_document_id, chunk_index, content))
            cur.execute("""
                SELECT id FROM spec_chunks 
                WHERE spec_document_id = ? AND chunk_index = ?
            """, (spec_document_id, chunk_index))
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
) -> int:
    """
    Batch update multiple chunk embeddings.
    
    More efficient than calling stream_embedding_update for each chunk.
    
    Args:
        updates: List of (chunk_id, embedding) tuples
    
    Returns:
        Number of successfully updated chunks
    """
    if not updates:
        return 0
    
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    success_count = 0
    
    try:
        for chunk_id, embedding in updates:
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
        
        conn.commit()
        conn.close()
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
