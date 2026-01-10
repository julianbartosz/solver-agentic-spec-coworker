"""
Postgres index helpers for artifact storage.

This module provides a Postgres-backed index for artifact metadata,
enabling:
- Cross-process artifact discovery
- Fast listing by run_id
- Garbage collection queries
- Audit trail

The index is optional - FilesystemArtifactStore uses a local manifest
as fallback if the index is unavailable.

Schema: integration_gold.run_artifacts
"""

import logging
from typing import List, Optional

from .base import ArtifactRef, ArtifactCodec

logger = logging.getLogger(__name__)


# =============================================================================
# DDL for run_artifacts table
# =============================================================================

RUN_ARTIFACTS_DDL = """
-- run_artifacts: Index of artifacts stored in external storage
-- Per Agent Harness Alignment Plan: artifact metadata for checkpoint resume

CREATE TABLE IF NOT EXISTS integration_gold.run_artifacts (
    id           BIGSERIAL PRIMARY KEY,
    run_id       TEXT NOT NULL,
    key          TEXT NOT NULL,           -- Field name (e.g., "openapi_spec")
    uri          TEXT NOT NULL,           -- Backend-neutral locator (file://..., obj://...)
    sha256       TEXT NOT NULL,           -- Content hash for verification
    size_bytes   BIGINT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/json',
    codec        TEXT NOT NULL DEFAULT 'json',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata     JSONB NOT NULL DEFAULT '{}',
    UNIQUE(run_id, key)                   -- One artifact per (run, field)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_run_artifacts_run_id ON integration_gold.run_artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_sha256 ON integration_gold.run_artifacts(sha256);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_created ON integration_gold.run_artifacts(created_at);
"""


def ensure_table_exists() -> None:
    """
    Ensure the run_artifacts table exists.
    
    Called lazily on first index operation.
    Safe to call multiple times.
    """
    try:
        from integration_coworker.persistence.db import get_engine_type
        
        if get_engine_type() == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(RUN_ARTIFACTS_DDL)
                conn.commit()
            logger.debug("run_artifacts table ensured")
        else:
            # SQLite fallback - create a simpler table
            from integration_coworker.persistence.db import get_connection
            
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS run_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    uri TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    content_type TEXT NOT NULL DEFAULT 'application/json',
                    codec TEXT NOT NULL DEFAULT 'json',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    metadata TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(run_id, key)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_run_artifacts_run_id ON run_artifacts(run_id)")
            conn.commit()
            conn.close()
            logger.debug("run_artifacts table ensured (SQLite)")
            
    except Exception as e:
        logger.warning(f"Failed to ensure run_artifacts table: {e}")


def insert_artifact_ref(ref: ArtifactRef) -> None:
    """
    Insert or update an artifact reference in the index.
    
    Uses UPSERT semantics - if an artifact for (run_id, key) already exists,
    it is replaced.
    """
    ensure_table_exists()
    
    try:
        from integration_coworker.persistence.db import get_engine_type
        import json
        
        if get_engine_type() == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO integration_gold.run_artifacts 
                            (run_id, key, uri, sha256, size_bytes, content_type, codec, created_at, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (run_id, key) 
                        DO UPDATE SET 
                            uri = EXCLUDED.uri,
                            sha256 = EXCLUDED.sha256,
                            size_bytes = EXCLUDED.size_bytes,
                            content_type = EXCLUDED.content_type,
                            codec = EXCLUDED.codec,
                            created_at = EXCLUDED.created_at,
                            metadata = EXCLUDED.metadata
                    """, (
                        ref.run_id,
                        ref.key,
                        ref.uri,
                        ref.sha256,
                        ref.size_bytes,
                        ref.content_type,
                        ref.codec.value,
                        ref.created_at.isoformat(),
                        json.dumps(ref.metadata),
                    ))
                conn.commit()
        else:
            from integration_coworker.persistence.db import get_connection
            
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO run_artifacts 
                    (run_id, key, uri, sha256, size_bytes, content_type, codec, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ref.run_id,
                ref.key,
                ref.uri,
                ref.sha256,
                ref.size_bytes,
                ref.content_type,
                ref.codec.value,
                ref.created_at.isoformat(),
                json.dumps(ref.metadata),
            ))
            conn.commit()
            conn.close()
            
        logger.debug(f"Artifact indexed: run={ref.run_id}, key={ref.key}")
        
    except Exception as e:
        logger.warning(f"Failed to index artifact: {e}")
        raise


def get_artifact_ref(run_id: str, key: str) -> Optional[ArtifactRef]:
    """
    Get an artifact reference by run_id and key.
    
    Returns None if not found.
    """
    ensure_table_exists()
    
    try:
        from integration_coworker.persistence.db import get_engine_type
        from datetime import datetime
        import json
        
        if get_engine_type() == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT uri, sha256, size_bytes, content_type, codec, created_at, metadata
                        FROM integration_gold.run_artifacts
                        WHERE run_id = %s AND key = %s
                    """, (run_id, key))
                    row = cur.fetchone()
                    
            if not row:
                return None
                
            return ArtifactRef(
                run_id=run_id,
                key=key,
                uri=row[0],
                sha256=row[1],
                size_bytes=row[2],
                content_type=row[3],
                codec=ArtifactCodec(row[4]),
                created_at=datetime.fromisoformat(row[5]) if isinstance(row[5], str) else row[5],
                metadata=row[6] if isinstance(row[6], dict) else json.loads(row[6]),
            )
        else:
            from integration_coworker.persistence.db import get_connection
            
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT uri, sha256, size_bytes, content_type, codec, created_at, metadata
                FROM run_artifacts
                WHERE run_id = ? AND key = ?
            """, (run_id, key))
            row = cur.fetchone()
            conn.close()
            
            if not row:
                return None
                
            return ArtifactRef(
                run_id=run_id,
                key=key,
                uri=row[0],
                sha256=row[1],
                size_bytes=row[2],
                content_type=row[3],
                codec=ArtifactCodec(row[4]),
                created_at=datetime.fromisoformat(row[5]),
                metadata=json.loads(row[6]),
            )
            
    except Exception as e:
        logger.warning(f"Failed to get artifact ref: {e}")
        return None


def list_artifacts_by_run(run_id: str) -> List[ArtifactRef]:
    """
    List all artifacts for a run.
    """
    ensure_table_exists()
    
    try:
        from integration_coworker.persistence.db import get_engine_type
        from datetime import datetime
        import json
        
        refs = []
        
        if get_engine_type() == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT key, uri, sha256, size_bytes, content_type, codec, created_at, metadata
                        FROM integration_gold.run_artifacts
                        WHERE run_id = %s
                        ORDER BY created_at
                    """, (run_id,))
                    rows = cur.fetchall()
                    
            for row in rows:
                refs.append(ArtifactRef(
                    run_id=run_id,
                    key=row[0],
                    uri=row[1],
                    sha256=row[2],
                    size_bytes=row[3],
                    content_type=row[4],
                    codec=ArtifactCodec(row[5]),
                    created_at=datetime.fromisoformat(row[6]) if isinstance(row[6], str) else row[6],
                    metadata=row[7] if isinstance(row[7], dict) else json.loads(row[7]),
                ))
        else:
            from integration_coworker.persistence.db import get_connection
            
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT key, uri, sha256, size_bytes, content_type, codec, created_at, metadata
                FROM run_artifacts
                WHERE run_id = ?
                ORDER BY created_at
            """, (run_id,))
            rows = cur.fetchall()
            conn.close()
            
            for row in rows:
                refs.append(ArtifactRef(
                    run_id=run_id,
                    key=row[0],
                    uri=row[1],
                    sha256=row[2],
                    size_bytes=row[3],
                    content_type=row[4],
                    codec=ArtifactCodec(row[5]),
                    created_at=datetime.fromisoformat(row[6]),
                    metadata=json.loads(row[7]),
                ))
                
        return refs
        
    except Exception as e:
        logger.warning(f"Failed to list artifacts: {e}")
        return []


def delete_artifact_ref(ref: ArtifactRef) -> bool:
    """
    Delete an artifact reference from the index.
    
    Returns True if deleted, False if not found.
    """
    ensure_table_exists()
    
    try:
        from integration_coworker.persistence.db import get_engine_type
        
        if get_engine_type() == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        DELETE FROM integration_gold.run_artifacts
                        WHERE run_id = %s AND key = %s
                    """, (ref.run_id, ref.key))
                    deleted = cur.rowcount > 0
                conn.commit()
            return deleted
        else:
            from integration_coworker.persistence.db import get_connection
            
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                DELETE FROM run_artifacts
                WHERE run_id = ? AND key = ?
            """, (ref.run_id, ref.key))
            deleted = cur.rowcount > 0
            conn.commit()
            conn.close()
            return deleted
            
    except Exception as e:
        logger.warning(f"Failed to delete artifact ref: {e}")
        return False


def delete_artifacts_by_run(run_id: str) -> int:
    """
    Delete all artifact references for a run.
    
    Returns number of entries deleted.
    """
    ensure_table_exists()
    
    try:
        from integration_coworker.persistence.db import get_engine_type
        
        if get_engine_type() == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        DELETE FROM integration_gold.run_artifacts
                        WHERE run_id = %s
                    """, (run_id,))
                    deleted = cur.rowcount
                conn.commit()
            return deleted
        else:
            from integration_coworker.persistence.db import get_connection
            
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                DELETE FROM run_artifacts
                WHERE run_id = ?
            """, (run_id,))
            deleted = cur.rowcount
            conn.commit()
            conn.close()
            return deleted
            
    except Exception as e:
        logger.warning(f"Failed to delete artifact refs: {e}")
        return 0


def count_artifacts_by_run(run_id: str) -> int:
    """
    Count artifacts for a run.
    
    Useful for determining if a run has any spooled artifacts.
    """
    ensure_table_exists()
    
    try:
        from integration_coworker.persistence.db import get_engine_type
        
        if get_engine_type() == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(*) FROM integration_gold.run_artifacts
                        WHERE run_id = %s
                    """, (run_id,))
                    return cur.fetchone()[0]
        else:
            from integration_coworker.persistence.db import get_connection
            
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM run_artifacts
                WHERE run_id = ?
            """, (run_id,))
            count = cur.fetchone()[0]
            conn.close()
            return count
            
    except Exception as e:
        logger.warning(f"Failed to count artifacts: {e}")
        return 0


def get_total_artifact_size(run_id: str) -> int:
    """
    Get total size of all artifacts for a run.
    
    Returns sum of size_bytes.
    """
    ensure_table_exists()
    
    try:
        from integration_coworker.persistence.db import get_engine_type
        
        if get_engine_type() == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COALESCE(SUM(size_bytes), 0) FROM integration_gold.run_artifacts
                        WHERE run_id = %s
                    """, (run_id,))
                    return cur.fetchone()[0]
        else:
            from integration_coworker.persistence.db import get_connection
            
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT COALESCE(SUM(size_bytes), 0) FROM run_artifacts
                WHERE run_id = ?
            """, (run_id,))
            total = cur.fetchone()[0]
            conn.close()
            return total
            
    except Exception as e:
        logger.warning(f"Failed to get artifact size: {e}")
        return 0
