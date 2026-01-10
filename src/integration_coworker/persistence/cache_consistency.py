"""
Cache Consistency Validator - V23-012 Production Fix

This module provides cache-database consistency checking to prevent the scenario
where Redis LLM cache has stale entries but the database has been truncated,
causing slow fallback paths and memory bloat.

Key Problem Addressed:
- Demo --fresh mode clears database but Redis clear can fail silently
- Old checkpoints contain cache_hit=True flags pointing to deleted data
- Cache hydration returns empty results, triggering slow re-parsing
- State bloat accumulates across nodes

Solution:
- Pre-run validation: Check cache vs database consistency before workflow starts
- Runtime validation: Verify cache_hit claims against actual database rows
- Auto-repair: Clear stale cache entries when mismatches detected
- Fail-fast: Surface errors early rather than slow degradation

Usage:
    from integration_coworker.persistence.cache_consistency import (
        validate_cache_consistency,
        clear_stale_cache,
        CacheConsistencyResult,
    )
    
    # Before starting a run
    result = validate_cache_consistency(provider_code="stripe")
    if not result.is_consistent:
        clear_stale_cache(provider_code="stripe")
"""
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Environment variable to control strictness
CACHE_CONSISTENCY_STRICT = os.environ.get("IC_CACHE_CONSISTENCY_STRICT", "true").lower() == "true"


@dataclass
class CacheConsistencyResult:
    """Result of a cache consistency check."""
    is_consistent: bool
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Database state
    db_spec_document_count: int = 0
    db_endpoint_count: int = 0
    db_schema_count: int = 0
    db_chunk_count: int = 0
    
    # Cache state (Redis)
    redis_available: bool = False
    redis_llm_key_count: int = 0
    redis_embedding_key_count: int = 0
    
    # Checkpoint state
    checkpoint_count: int = 0
    stale_checkpoint_count: int = 0
    
    # Detected issues
    issues: List[str] = field(default_factory=list)
    
    # Provider scope
    provider_code: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/persistence."""
        return {
            "is_consistent": self.is_consistent,
            "checked_at": self.checked_at.isoformat(),
            "db_spec_document_count": self.db_spec_document_count,
            "db_endpoint_count": self.db_endpoint_count,
            "db_schema_count": self.db_schema_count,
            "db_chunk_count": self.db_chunk_count,
            "redis_available": self.redis_available,
            "redis_llm_key_count": self.redis_llm_key_count,
            "redis_embedding_key_count": self.redis_embedding_key_count,
            "checkpoint_count": self.checkpoint_count,
            "stale_checkpoint_count": self.stale_checkpoint_count,
            "issues": self.issues,
            "provider_code": self.provider_code,
        }


def _get_redis_client():
    """Get Redis client if available."""
    try:
        import redis
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        client = redis.from_url(redis_url, decode_responses=True)
        # Test connection
        client.ping()
        return client
    except Exception as e:
        logger.debug(f"Redis not available: {e}")
        return None


def _count_redis_keys(client, pattern: str) -> int:
    """Count Redis keys matching pattern."""
    if not client:
        return 0
    try:
        count = 0
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=1000)
            count += len(keys)
            if cursor == 0:
                break
        return count
    except Exception as e:
        logger.warning(f"Failed to count Redis keys for {pattern}: {e}")
        return 0


def _get_db_counts(provider_code: Optional[str] = None) -> Dict[str, int]:
    """Get counts from database tables."""
    from integration_coworker.persistence import db
    from integration_coworker.persistence.sql_helpers import get_engine_type
    
    counts = {
        "spec_documents": 0,
        "endpoints": 0,
        "schemas": 0,
        "chunks": 0,
        "checkpoints": 0,
        "checkpoint_blobs": 0,
    }
    
    try:
        db.init_schema()
        # V27-003 Fix: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            cur = conn.cursor()
            engine = get_engine_type()
            
            if engine == "postgres":
                # Spec documents (optionally filtered by provider)
                if provider_code:
                    cur.execute("""
                        SELECT COUNT(*) FROM spec_silver.spec_documents sd
                        JOIN spec_silver.source_systems ss ON ss.id = sd.source_system_id
                        WHERE ss.code = %s
                    """, (provider_code,))
                else:
                    cur.execute("SELECT COUNT(*) FROM spec_silver.spec_documents")
                counts["spec_documents"] = cur.fetchone()[0]
                
                # Endpoints
                if provider_code:
                    cur.execute("""
                        SELECT COUNT(*) FROM spec_silver.endpoints e
                        JOIN spec_silver.source_systems ss ON ss.id = e.source_system_id
                        WHERE ss.code = %s
                    """, (provider_code,))
                else:
                    cur.execute("SELECT COUNT(*) FROM spec_silver.endpoints")
                counts["endpoints"] = cur.fetchone()[0]
                
                # Schemas
                if provider_code:
                    cur.execute("""
                        SELECT COUNT(*) FROM spec_silver.schemas s
                        JOIN spec_silver.source_systems ss ON ss.id = s.source_system_id
                        WHERE ss.code = %s
                    """, (provider_code,))
                else:
                    cur.execute("SELECT COUNT(*) FROM spec_silver.schemas")
                counts["schemas"] = cur.fetchone()[0]
                
                # Chunks
                cur.execute("SELECT COUNT(*) FROM spec_silver.spec_chunks")
                counts["chunks"] = cur.fetchone()[0]
                
                # Checkpoints - V24-008 FIX: Filter by provider to match spec_documents scope
                # Without this, we get false positives when other providers have checkpoints
                if provider_code:
                    cur.execute("""
                        SELECT COUNT(*) FROM public.checkpoints c
                        WHERE c.thread_id LIKE %s
                    """, (f"%{provider_code}%",))
                else:
                    cur.execute("SELECT COUNT(*) FROM public.checkpoints")
                counts["checkpoints"] = cur.fetchone()[0]
                
                if provider_code:
                    cur.execute("""
                        SELECT COUNT(*) FROM public.checkpoint_blobs cb
                        WHERE cb.thread_id LIKE %s
                    """, (f"%{provider_code}%",))
                else:
                    cur.execute("SELECT COUNT(*) FROM public.checkpoint_blobs")
                counts["checkpoint_blobs"] = cur.fetchone()[0]
            else:
                # SQLite fallback
                cur.execute("SELECT COUNT(*) FROM spec_documents")
                counts["spec_documents"] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM endpoints")
                counts["endpoints"] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM schemas")
                counts["schemas"] = cur.fetchone()[0]
    except Exception as e:
        logger.warning(f"Failed to get DB counts: {e}")
    
    return counts


def _count_stale_checkpoints(provider_code: Optional[str] = None) -> int:
    """
    Count checkpoints that reference cache_hit=True but have no corresponding DB data.
    
    A checkpoint is "stale" if:
    - It has cache_hit=True in state_json
    - But the spec_document_id it references no longer exists in the database
    """
    from integration_coworker.persistence import db
    from integration_coworker.persistence.sql_helpers import get_engine_type
    
    try:
        # V27-003 Fix: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            cur = conn.cursor()
            engine = get_engine_type()
            
            if engine != "postgres":
                return 0  # Only implemented for Postgres
            
            # Find checkpoints with cache_hit=True that reference missing spec_documents
            query = """
                SELECT COUNT(*) FROM integration_gold.run_checkpoints rc
                WHERE rc.state_json->>'cache_hit' = 'true'
                AND NOT EXISTS (
                    SELECT 1 FROM spec_silver.spec_documents sd
                    WHERE sd.id = (rc.state_json->>'primary_spec_document_id')::bigint
                )
            """
            
            if provider_code:
                query = """
                    SELECT COUNT(*) FROM integration_gold.run_checkpoints rc
                    WHERE rc.state_json->>'cache_hit' = 'true'
                    AND rc.state_json->>'provider_code' = %s
                    AND NOT EXISTS (
                        SELECT 1 FROM spec_silver.spec_documents sd
                        WHERE sd.id = (rc.state_json->>'primary_spec_document_id')::bigint
                    )
                """
                cur.execute(query, (provider_code,))
            else:
                cur.execute(query)
            
            count = cur.fetchone()[0]
            return count
    except Exception as e:
        logger.debug(f"Failed to count stale checkpoints: {e}")
        return 0


def validate_cache_consistency(
    provider_code: Optional[str] = None,
    require_redis: bool = False,
) -> CacheConsistencyResult:
    """
    Validate that cache state is consistent with database state.
    
    This is the main entry point for cache consistency checking.
    Call this before starting a workflow run to detect stale cache issues.
    
    Args:
        provider_code: Optional provider to scope the check
        require_redis: If True, treat Redis unavailability as inconsistent
        
    Returns:
        CacheConsistencyResult with details about any inconsistencies
    """
    result = CacheConsistencyResult(
        is_consistent=True,
        provider_code=provider_code,
    )
    
    # 1. Check Redis availability and counts
    redis_client = _get_redis_client()
    result.redis_available = redis_client is not None
    
    if redis_client:
        if provider_code:
            result.redis_llm_key_count = _count_redis_keys(redis_client, f"llm:*{provider_code}*")
            result.redis_embedding_key_count = _count_redis_keys(redis_client, f"emb:*{provider_code}*")
        else:
            result.redis_llm_key_count = _count_redis_keys(redis_client, "llm:*")
            result.redis_embedding_key_count = _count_redis_keys(redis_client, "emb:*")
    elif require_redis:
        result.is_consistent = False
        result.issues.append("Redis required but not available")
    
    # 2. Get database counts
    db_counts = _get_db_counts(provider_code)
    result.db_spec_document_count = db_counts["spec_documents"]
    result.db_endpoint_count = db_counts["endpoints"]
    result.db_schema_count = db_counts["schemas"]
    result.db_chunk_count = db_counts["chunks"]
    result.checkpoint_count = db_counts["checkpoints"]
    
    # 3. Check for stale checkpoints
    result.stale_checkpoint_count = _count_stale_checkpoints(provider_code)
    
    # 4. Detect inconsistencies
    
    # Issue: Redis has LLM cache but database is empty
    if result.redis_llm_key_count > 0 and result.db_endpoint_count == 0:
        result.is_consistent = False
        result.issues.append(
            f"Cache-DB mismatch: Redis has {result.redis_llm_key_count} LLM cache entries "
            f"but database has 0 endpoints. Likely stale cache after DB truncate."
        )
    
    # Issue: Redis has embeddings but database has no chunks
    if result.redis_embedding_key_count > 0 and result.db_chunk_count == 0:
        result.is_consistent = False
        result.issues.append(
            f"Cache-DB mismatch: Redis has {result.redis_embedding_key_count} embedding cache entries "
            f"but database has 0 chunks. Likely stale cache after DB truncate."
        )
    
    # Issue: Stale checkpoints exist
    if result.stale_checkpoint_count > 0:
        result.is_consistent = False
        result.issues.append(
            f"Found {result.stale_checkpoint_count} stale checkpoints with cache_hit=True "
            f"referencing deleted spec_documents."
        )
    
    # Issue: Large number of checkpoints but empty database (likely post-truncate)
    if result.checkpoint_count > 10 and result.db_spec_document_count == 0:
        result.is_consistent = False
        result.issues.append(
            f"Suspicious state: {result.checkpoint_count} checkpoints exist but "
            f"database has 0 spec_documents. Checkpoints may be stale."
        )
    
    if result.issues:
        logger.warning(
            f"[CACHE_CONSISTENCY] Issues detected for provider={provider_code}: "
            f"{result.issues}"
        )
    else:
        logger.debug(
            f"[CACHE_CONSISTENCY] OK for provider={provider_code}: "
            f"db_endpoints={result.db_endpoint_count}, "
            f"redis_llm_keys={result.redis_llm_key_count}"
        )
    
    return result


def clear_stale_cache(
    provider_code: Optional[str] = None,
    clear_redis: bool = True,
    clear_checkpoints: bool = True,
) -> Dict[str, int]:
    """
    Clear stale cache entries to restore consistency.
    
    This is a repair operation - call when validate_cache_consistency() finds issues.
    
    Args:
        provider_code: Optional provider to scope the clear
        clear_redis: Whether to clear Redis LLM/embedding cache
        clear_checkpoints: Whether to clear stale checkpoint records
        
    Returns:
        Dict with counts of cleared items
    """
    cleared = {
        "redis_llm_keys": 0,
        "redis_embedding_keys": 0,
        "stale_checkpoints": 0,
    }
    
    # 1. Clear Redis cache
    if clear_redis:
        redis_client = _get_redis_client()
        if redis_client:
            try:
                if provider_code:
                    # Selective clear for provider
                    for pattern in [f"llm:*{provider_code}*", f"emb:*{provider_code}*"]:
                        cursor = 0
                        while True:
                            cursor, keys = redis_client.scan(cursor=cursor, match=pattern, count=1000)
                            if keys:
                                redis_client.delete(*keys)
                                if "llm:" in pattern:
                                    cleared["redis_llm_keys"] += len(keys)
                                else:
                                    cleared["redis_embedding_keys"] += len(keys)
                            if cursor == 0:
                                break
                else:
                    # Full clear
                    llm_keys = list(redis_client.scan_iter("llm:*", count=1000))
                    if llm_keys:
                        redis_client.delete(*llm_keys)
                        cleared["redis_llm_keys"] = len(llm_keys)
                    
                    emb_keys = list(redis_client.scan_iter("emb:*", count=1000))
                    if emb_keys:
                        redis_client.delete(*emb_keys)
                        cleared["redis_embedding_keys"] = len(emb_keys)
                
                logger.info(
                    f"[CACHE_CONSISTENCY] Cleared Redis: "
                    f"llm={cleared['redis_llm_keys']}, emb={cleared['redis_embedding_keys']}"
                )
            except Exception as e:
                logger.error(f"Failed to clear Redis cache: {e}")
    
    # 2. Clear stale checkpoints
    if clear_checkpoints:
        from integration_coworker.persistence import db
        from integration_coworker.persistence.sql_helpers import get_engine_type
        
        try:
            # V27-003 Fix: Use context manager to prevent connection leaks
            with db.get_connection() as conn:
                cur = conn.cursor()
                engine = get_engine_type()
                
                if engine == "postgres":
                    # Delete stale run_checkpoints
                    if provider_code:
                        cur.execute("""
                            DELETE FROM integration_gold.run_checkpoints
                            WHERE state_json->>'cache_hit' = 'true'
                            AND state_json->>'provider_code' = %s
                            AND NOT EXISTS (
                                SELECT 1 FROM spec_silver.spec_documents sd
                                WHERE sd.id = (state_json->>'primary_spec_document_id')::bigint
                            )
                        """, (provider_code,))
                    else:
                        cur.execute("""
                            DELETE FROM integration_gold.run_checkpoints
                            WHERE state_json->>'cache_hit' = 'true'
                            AND NOT EXISTS (
                                SELECT 1 FROM spec_silver.spec_documents sd
                                WHERE sd.id = (state_json->>'primary_spec_document_id')::bigint
                            )
                        """)
                    
                    cleared["stale_checkpoints"] = cur.rowcount
                    conn.commit()
                    
                    logger.info(
                        f"[CACHE_CONSISTENCY] Cleared {cleared['stale_checkpoints']} stale checkpoints"
                    )
        except Exception as e:
            logger.error(f"Failed to clear stale checkpoints: {e}")
    
    return cleared


def validate_cache_hit_claim(
    spec_document_id: Optional[int],
    provider_code: Optional[str] = None,
) -> bool:
    """
    Validate that a cache_hit claim is backed by actual database data.
    
    Call this in cache hydration paths to detect stale cache_hit flags.
    
    Args:
        spec_document_id: The spec_document ID being claimed as cached
        provider_code: Optional provider code for additional validation
        
    Returns:
        True if the cache hit is valid (data exists), False if stale
    """
    if spec_document_id is None:
        logger.debug("[CACHE_CONSISTENCY] cache_hit claim with no spec_document_id - invalid")
        return False
    
    from integration_coworker.persistence import db
    from integration_coworker.persistence.sql_helpers import get_engine_type
    
    try:
        # V27-003 Fix: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            cur = conn.cursor()
            engine = get_engine_type()
            
            if engine == "postgres":
                # Check spec_document exists
                cur.execute(
                    "SELECT COUNT(*) FROM spec_silver.spec_documents WHERE id = %s",
                    (spec_document_id,)
                )
                doc_exists = cur.fetchone()[0] > 0
                
                if not doc_exists:
                    logger.warning(
                        f"[CACHE_CONSISTENCY] Stale cache_hit: spec_document_id={spec_document_id} "
                        f"not found in database"
                    )
                    return False
                
                # Check endpoints exist for this spec_document
                cur.execute(
                    "SELECT COUNT(*) FROM spec_silver.endpoints WHERE spec_document_id = %s",
                    (spec_document_id,)
                )
                endpoint_count = cur.fetchone()[0]
                
                if endpoint_count == 0:
                    logger.warning(
                        f"[CACHE_CONSISTENCY] Partial cache: spec_document_id={spec_document_id} "
                        f"exists but has 0 endpoints - treating as cache miss"
                    )
                    return False
                
                logger.debug(
                    f"[CACHE_CONSISTENCY] cache_hit validated: spec_document_id={spec_document_id}, "
                    f"endpoints={endpoint_count}"
                )
                return True
            else:
                # SQLite - simpler check
                cur.execute(
                    "SELECT COUNT(*) FROM spec_documents WHERE id = ?",
                    (spec_document_id,)
                )
                exists = cur.fetchone()[0] > 0
                return exists
            
    except Exception as e:
        logger.error(f"[CACHE_CONSISTENCY] Validation failed: {e}")
        return False  # Fail closed - treat as cache miss


def ensure_cache_consistency(
    provider_code: Optional[str] = None,
    auto_repair: bool = True,
) -> CacheConsistencyResult:
    """
    Validate and optionally repair cache consistency before a workflow run.
    
    This is the recommended pre-run hook. It validates cache-database consistency
    and auto-repairs if issues are found.
    
    Args:
        provider_code: Optional provider to scope the check
        auto_repair: If True, automatically clear stale cache on inconsistency
        
    Returns:
        CacheConsistencyResult (will be consistent after auto-repair if enabled)
    """
    result = validate_cache_consistency(provider_code)
    
    if not result.is_consistent and auto_repair:
        logger.warning(
            f"[CACHE_CONSISTENCY] Auto-repairing cache inconsistency for provider={provider_code}"
        )
        cleared = clear_stale_cache(provider_code)
        
        # Re-validate after repair
        result = validate_cache_consistency(provider_code)
        
        if result.is_consistent:
            logger.info(
                f"[CACHE_CONSISTENCY] Repair successful: cleared "
                f"llm={cleared['redis_llm_keys']}, emb={cleared['redis_embedding_keys']}, "
                f"checkpoints={cleared['stale_checkpoints']}"
            )
        else:
            logger.error(
                f"[CACHE_CONSISTENCY] Repair incomplete, remaining issues: {result.issues}"
            )
    
    return result


# Module-level convenience function for pre-run validation
def pre_run_cache_check(provider_code: Optional[str] = None) -> bool:
    """
    Quick pre-run cache consistency check.
    
    Returns True if cache is consistent (safe to proceed).
    Returns False if inconsistent and repair failed.
    
    Usage:
        if not pre_run_cache_check(provider_code):
            raise RuntimeError("Cache inconsistency detected - manual intervention required")
    """
    result = ensure_cache_consistency(provider_code, auto_repair=True)
    
    if not result.is_consistent and CACHE_CONSISTENCY_STRICT:
        logger.error(
            f"[CACHE_CONSISTENCY] STRICT MODE: Blocking run due to unresolved issues: "
            f"{result.issues}"
        )
        return False
    
    return True
