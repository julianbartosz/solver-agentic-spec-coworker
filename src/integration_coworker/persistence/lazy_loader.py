"""
Lazy loading for large data from database.

V3 Implementation: Load chunks and embeddings on-demand from the database
instead of carrying them in WorkflowState memory.

This module is used when STREAMING_PERSISTENCE=true to reduce memory footprint
from 200MB+ to <20MB for large specs.

Key principle: Never load all chunks into memory at once. Use generators
and pagination for memory-efficient iteration.

Design Doc Reference: V3_STREAMING_PERSISTENCE_PLAN.md
"""
from typing import Iterator, List, Optional, Tuple, Dict, Any, Generator
import logging
import json

from integration_coworker.persistence.db import get_connection, get_engine_type

logger = logging.getLogger(__name__)

# Default batch size for lazy loading iterators
DEFAULT_BATCH_SIZE = 50


def load_raw_spec_content(raw_spec_id: int) -> Optional[str]:
    """
    Load raw spec content from spec_bronze.raw_specs.
    
    Used by detect_and_parse_spec when streaming is enabled. Loads the
    raw content that was previously streamed by ingest_spec.
    
    Args:
        raw_spec_id: The raw_specs.id
    
    Returns:
        The raw content string, or None if not found
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT raw_content FROM spec_bronze.raw_specs WHERE id = %s
            """, (raw_spec_id,))
        else:
            cur.execute("""
                SELECT raw_content FROM raw_specs WHERE id = ?
            """, (raw_spec_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if row:
            # raw_content is stored as BLOB/BYTEA, decode to string
            content = row[0]
            if isinstance(content, bytes):
                return content.decode("utf-8")
            return content
        return None
        
    except Exception as e:
        conn.close()
        logger.error(f"Failed to load raw spec {raw_spec_id}: {e}")
        return None


def load_spec_document_content(spec_document_id: int) -> Optional[str]:
    """
    Load spec document content from spec_silver.spec_documents.
    
    Alternative to load_raw_spec_content when content is stored in
    spec_documents table.
    
    Args:
        spec_document_id: The spec_documents.id
    
    Returns:
        The document content string, or None if not found
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT content FROM spec_silver.spec_documents WHERE id = %s
            """, (spec_document_id,))
        else:
            cur.execute("""
                SELECT content FROM spec_documents WHERE id = ?
            """, (spec_document_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if row:
            return row[0]
        return None
        
    except Exception as e:
        conn.close()
        logger.error(f"Failed to load spec document {spec_document_id}: {e}")
        return None


def iter_chunks(
    spec_document_id: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    include_embedding: bool = False,
) -> Generator[Tuple[int, int, str, Optional[List[float]]], None, None]:
    """
    Iterate over chunks for a spec document in batches.
    
    Memory-efficient iterator that loads chunks in batches from the database.
    Never loads all chunks into memory at once.
    
    This is the primary interface for embed_spec_chunks when streaming is
    enabled. It loads chunk content on-demand for embedding.
    
    Args:
        spec_document_id: The spec_documents.id
        batch_size: Number of chunks to load per database query
        include_embedding: If True, also load embedding vector
    
    Yields:
        Tuple of (chunk_id, chunk_index, content, embedding)
        embedding is None unless include_embedding=True and embedding exists
    """
    engine = get_engine_type()
    offset = 0
    
    while True:
        conn = get_connection()
        cur = conn.cursor()
        
        try:
            if engine == "postgres":
                if include_embedding:
                    cur.execute("""
                        SELECT id, chunk_index, content, embedding
                        FROM spec_silver.spec_chunks 
                        WHERE spec_document_id = %s
                        ORDER BY chunk_index
                        LIMIT %s OFFSET %s
                    """, (spec_document_id, batch_size, offset))
                else:
                    cur.execute("""
                        SELECT id, chunk_index, content
                        FROM spec_silver.spec_chunks 
                        WHERE spec_document_id = %s
                        ORDER BY chunk_index
                        LIMIT %s OFFSET %s
                    """, (spec_document_id, batch_size, offset))
            else:
                if include_embedding:
                    cur.execute("""
                        SELECT id, chunk_index, content, embedding
                        FROM spec_chunks 
                        WHERE spec_document_id = ?
                        ORDER BY chunk_index
                        LIMIT ? OFFSET ?
                    """, (spec_document_id, batch_size, offset))
                else:
                    cur.execute("""
                        SELECT id, chunk_index, content
                        FROM spec_chunks 
                        WHERE spec_document_id = ?
                        ORDER BY chunk_index
                        LIMIT ? OFFSET ?
                    """, (spec_document_id, batch_size, offset))
            
            rows = cur.fetchall()
            conn.close()
            
            if not rows:
                break
            
            for row in rows:
                if include_embedding:
                    chunk_id, chunk_index, content, embedding_json = row
                    embedding = None
                    if embedding_json:
                        try:
                            embedding = json.loads(embedding_json)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    yield (chunk_id, chunk_index, content, embedding)
                else:
                    chunk_id, chunk_index, content = row
                    yield (chunk_id, chunk_index, content, None)
            
            offset += batch_size
            
            # If we got fewer than batch_size, we're done
            if len(rows) < batch_size:
                break
                
        except Exception as e:
            conn.close()
            logger.error(f"Failed to iterate chunks at offset {offset}: {e}")
            break


def iter_chunks_for_embedding(
    spec_document_id: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Generator[Tuple[int, int, str], None, None]:
    """
    Iterate over chunks that need embeddings (embedding is NULL).
    
    Specialized iterator for embed_spec_chunks that only returns chunks
    without embeddings.
    
    Args:
        spec_document_id: The spec_documents.id
        batch_size: Number of chunks to load per database query
    
    Yields:
        Tuple of (chunk_id, chunk_index, content)
    """
    engine = get_engine_type()
    offset = 0
    
    while True:
        conn = get_connection()
        cur = conn.cursor()
        
        try:
            if engine == "postgres":
                cur.execute("""
                    SELECT id, chunk_index, content
                    FROM spec_silver.spec_chunks 
                    WHERE spec_document_id = %s AND embedding IS NULL
                    ORDER BY chunk_index
                    LIMIT %s OFFSET %s
                """, (spec_document_id, batch_size, offset))
            else:
                cur.execute("""
                    SELECT id, chunk_index, content
                    FROM spec_chunks 
                    WHERE spec_document_id = ? AND embedding IS NULL
                    ORDER BY chunk_index
                    LIMIT ? OFFSET ?
                """, (spec_document_id, batch_size, offset))
            
            rows = cur.fetchall()
            conn.close()
            
            if not rows:
                break
            
            for row in rows:
                yield (row[0], row[1], row[2])
            
            offset += batch_size
            
            if len(rows) < batch_size:
                break
                
        except Exception as e:
            conn.close()
            logger.error(f"Failed to iterate chunks for embedding at offset {offset}: {e}")
            break


def get_chunk_by_id(chunk_id: int) -> Optional[Dict[str, Any]]:
    """
    Load a single chunk by ID.
    
    Used when a specific chunk is needed (e.g., for context in prompts).
    
    Args:
        chunk_id: The spec_chunks.id
    
    Returns:
        Dict with keys: id, spec_document_id, chunk_index, content, embedding
        or None if not found
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT id, spec_document_id, chunk_index, content, embedding
                FROM spec_silver.spec_chunks WHERE id = %s
            """, (chunk_id,))
        else:
            cur.execute("""
                SELECT id, spec_document_id, chunk_index, content, embedding
                FROM spec_chunks WHERE id = ?
            """, (chunk_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if row:
            embedding = None
            if row[4]:
                try:
                    embedding = json.loads(row[4])
                except (json.JSONDecodeError, TypeError):
                    pass
            
            return {
                "id": row[0],
                "spec_document_id": row[1],
                "chunk_index": row[2],
                "content": row[3],
                "embedding": embedding,
            }
        return None
        
    except Exception as e:
        conn.close()
        logger.error(f"Failed to load chunk {chunk_id}: {e}")
        return None


def get_chunks_by_ids(chunk_ids: List[int]) -> List[Dict[str, Any]]:
    """
    Load multiple chunks by IDs.
    
    More efficient than calling get_chunk_by_id repeatedly.
    
    Args:
        chunk_ids: List of spec_chunks.id values
    
    Returns:
        List of chunk dicts (same structure as get_chunk_by_id)
    """
    if not chunk_ids:
        return []
    
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            # Use ANY for Postgres
            cur.execute("""
                SELECT id, spec_document_id, chunk_index, content, embedding
                FROM spec_silver.spec_chunks 
                WHERE id = ANY(%s)
                ORDER BY chunk_index
            """, (chunk_ids,))
        else:
            # SQLite doesn't have ANY, use IN
            placeholders = ",".join("?" * len(chunk_ids))
            cur.execute(f"""
                SELECT id, spec_document_id, chunk_index, content, embedding
                FROM spec_chunks 
                WHERE id IN ({placeholders})
                ORDER BY chunk_index
            """, chunk_ids)
        
        rows = cur.fetchall()
        conn.close()
        
        results = []
        for row in rows:
            embedding = None
            if row[4]:
                try:
                    embedding = json.loads(row[4])
                except (json.JSONDecodeError, TypeError):
                    pass
            
            results.append({
                "id": row[0],
                "spec_document_id": row[1],
                "chunk_index": row[2],
                "content": row[3],
                "embedding": embedding,
            })
        
        return results
        
    except Exception as e:
        conn.close()
        logger.error(f"Failed to load chunks: {e}")
        return []


def get_chunk_count(spec_document_id: int) -> int:
    """
    Get the total number of chunks for a spec document.
    
    Used by WorkflowState to track chunk count without loading content.
    
    Args:
        spec_document_id: The spec_documents.id
    
    Returns:
        Number of chunks
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT COUNT(*) FROM spec_silver.spec_chunks 
                WHERE spec_document_id = %s
            """, (spec_document_id,))
        else:
            cur.execute("""
                SELECT COUNT(*) FROM spec_chunks 
                WHERE spec_document_id = ?
            """, (spec_document_id,))
        
        count = cur.fetchone()[0]
        conn.close()
        return count
        
    except Exception as e:
        conn.close()
        logger.error(f"Failed to get chunk count: {e}")
        return 0


def get_embedding_count(spec_document_id: int) -> int:
    """
    Get the number of chunks with embeddings for a spec document.
    
    Used to track embedding progress without loading data.
    
    Args:
        spec_document_id: The spec_documents.id
    
    Returns:
        Number of chunks with non-null embeddings
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT COUNT(*) FROM spec_silver.spec_chunks 
                WHERE spec_document_id = %s AND embedding IS NOT NULL
            """, (spec_document_id,))
        else:
            cur.execute("""
                SELECT COUNT(*) FROM spec_chunks 
                WHERE spec_document_id = ? AND embedding IS NOT NULL
            """, (spec_document_id,))
        
        count = cur.fetchone()[0]
        conn.close()
        return count
        
    except Exception as e:
        conn.close()
        logger.error(f"Failed to get embedding count: {e}")
        return 0


def get_spec_document_ids_for_source(source_system_id: int) -> List[int]:
    """
    Get all spec document IDs for a source system.
    
    Used when WorkflowState needs to track which specs have been processed
    without storing full content.
    
    Args:
        source_system_id: The source_systems.id
    
    Returns:
        List of spec_documents.id values
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT id FROM spec_silver.spec_documents 
                WHERE source_system_id = %s
                ORDER BY id
            """, (source_system_id,))
        else:
            cur.execute("""
                SELECT id FROM spec_documents 
                WHERE source_system_id = ?
                ORDER BY id
            """, (source_system_id,))
        
        rows = cur.fetchall()
        conn.close()
        
        return [row[0] for row in rows]
        
    except Exception as e:
        conn.close()
        logger.error(f"Failed to get spec document IDs: {e}")
        return []
