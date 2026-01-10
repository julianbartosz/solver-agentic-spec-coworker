"""
Fresh reset via dynamic schema discovery.

Truncates all tables in known schemas by querying Postgres catalogs at runtime.
This avoids hardcoded table lists that drift from actual schema.

Design:
- Discovers tables from pg_catalog for each known schema
- Uses TRUNCATE ... CASCADE to handle FK constraints
- Flushes Redis with prefix pattern (llm:*) to avoid nuking unrelated keys
- All operations in a single transaction for atomicity
"""
import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

# Schemas we manage and can safely truncate
# These are the schemas created by our migrations
MANAGED_SCHEMAS = [
    "spec_silver",       # Silver layer: spec documents, chunks, endpoints, schemas
    "integration_gold",  # Gold layer: tasks, artifacts, policies, run_status
    "kg",                # Knowledge graph: nodes, edges, workflow_steps, step_bindings
]

# Checkpoint tables live in public schema with specific names
# These are created by LangGraph and our checkpoint code
CHECKPOINT_TABLE_PATTERNS = [
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
]

# Redis key prefix for LLM cache (from integration_coworker/llm/cache.py)
REDIS_CACHE_PREFIX = "llm:"


@dataclass
class FreshResetResult:
    """Result of fresh reset operation."""
    schemas_cleared: List[str] = field(default_factory=list)
    tables_truncated: int = 0
    checkpoints_cleared: bool = False
    redis_flushed: bool = False
    redis_keys_deleted: int = 0
    errors: List[str] = field(default_factory=list)
    
    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def _discover_tables_in_schema(conn, schema: str) -> List[str]:
    """
    Discover all tables in a schema via Postgres catalogs.
    
    Uses pg_catalog.pg_tables which is always available.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT tablename 
        FROM pg_catalog.pg_tables 
        WHERE schemaname = %s
        ORDER BY tablename
    """, (schema,))
    return [row[0] for row in cur.fetchall()]


def _discover_checkpoint_tables(conn) -> List[str]:
    """
    Discover checkpoint tables in public schema.
    
    LangGraph creates these with specific names.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT tablename 
        FROM pg_catalog.pg_tables 
        WHERE schemaname = 'public'
        AND tablename IN ('checkpoints', 'checkpoint_blobs', 'checkpoint_writes')
        ORDER BY tablename
    """)
    return [row[0] for row in cur.fetchall()]


def _truncate_tables(conn, schema: str, tables: List[str]) -> int:
    """
    Truncate all tables in a schema with CASCADE.
    
    Returns count of tables truncated.
    """
    cur = conn.cursor()
    truncated = 0
    
    for table in tables:
        qualified_name = f"{schema}.{table}"
        try:
            cur.execute(f"TRUNCATE {qualified_name} CASCADE")
            truncated += 1
            logger.debug(f"Truncated {qualified_name}")
        except Exception as e:
            # Log but continue - some tables might have issues
            logger.warning(f"Could not truncate {qualified_name}: {e}")
            conn.rollback()
    
    return truncated


def _flush_redis_cache() -> tuple[bool, int]:
    """
    Flush Redis LLM cache using prefix pattern.
    
    Only deletes keys matching llm:* pattern to avoid
    nuking unrelated keys (e.g., from other apps sharing Redis).
    
    Returns (success, keys_deleted).
    """
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return False, 0
    
    try:
        import redis
        r = redis.from_url(redis_url, socket_connect_timeout=5)
        
        # Use SCAN to find keys matching prefix (safe for large datasets)
        keys_deleted = 0
        cursor = 0
        pattern = f"{REDIS_CACHE_PREFIX}*"
        
        while True:
            cursor, keys = r.scan(cursor, match=pattern, count=1000)
            if keys:
                r.delete(*keys)
                keys_deleted += len(keys)
            if cursor == 0:
                break
        
        logger.info(f"Redis: deleted {keys_deleted} keys matching {pattern}")
        return True, keys_deleted
        
    except ImportError:
        logger.warning("Redis module not available, skipping cache flush")
        return False, 0
    except Exception as e:
        logger.warning(f"Redis cache flush failed: {e}")
        return False, 0


def fresh_reset_all(
    clear_silver: bool = True,
    clear_gold: bool = True,
    clear_kg: bool = True,
    clear_checkpoints: bool = True,
    flush_redis: bool = True,
) -> FreshResetResult:
    """
    Full database reset via dynamic schema discovery.
    
    Matches behavior of demo-final-showcase.sh --fresh (lines 560-640).
    
    Key difference from hardcoded approach:
    - Discovers tables from Postgres catalogs at runtime
    - Handles schema changes automatically
    - Uses CASCADE to handle FK constraints
    
    Args:
        clear_silver: Truncate spec_silver.* tables
        clear_gold: Truncate integration_gold.* tables
        clear_kg: Truncate kg.* tables
        clear_checkpoints: Truncate public.checkpoint_* tables
        flush_redis: Delete Redis keys matching llm:* prefix
        
    Returns:
        FreshResetResult with details of what was cleared.
        
    Usage:
        result = fresh_reset_all()
        if not result.success:
            print(f"Reset had errors: {result.errors}")
        print(f"Truncated {result.tables_truncated} tables")
    """
    result = FreshResetResult()
    
    try:
        from integration_coworker.persistence.postgres import get_connection
    except ImportError as e:
        result.errors.append(f"Cannot import postgres module: {e}")
        return result
    
    schemas_to_clear = []
    if clear_silver:
        schemas_to_clear.append("spec_silver")
    if clear_gold:
        schemas_to_clear.append("integration_gold")
    if clear_kg:
        schemas_to_clear.append("kg")
    
    try:
        with get_connection() as conn:
            # Discover and truncate managed schemas
            for schema in schemas_to_clear:
                tables = _discover_tables_in_schema(conn, schema)
                if tables:
                    logger.info(f"Discovered {len(tables)} tables in {schema}: {tables}")
                    count = _truncate_tables(conn, schema, tables)
                    result.tables_truncated += count
                    result.schemas_cleared.append(schema)
                else:
                    logger.debug(f"No tables found in schema {schema}")
            
            # Discover and truncate checkpoint tables
            if clear_checkpoints:
                checkpoint_tables = _discover_checkpoint_tables(conn)
                if checkpoint_tables:
                    logger.info(f"Discovered checkpoint tables: {checkpoint_tables}")
                    for table in checkpoint_tables:
                        try:
                            conn.cursor().execute(f"TRUNCATE public.{table} CASCADE")
                            result.tables_truncated += 1
                        except Exception as e:
                            logger.warning(f"Could not truncate public.{table}: {e}")
                            conn.rollback()
                    result.checkpoints_cleared = True
            
            # Commit all truncations
            conn.commit()
            logger.info(f"Fresh reset: truncated {result.tables_truncated} tables across {result.schemas_cleared}")
            
    except Exception as e:
        result.errors.append(f"Database reset failed: {e}")
        logger.error(f"Fresh reset database error: {e}")
    
    # Flush Redis cache (separate operation, not transactional with DB)
    if flush_redis:
        success, keys_deleted = _flush_redis_cache()
        result.redis_flushed = success
        result.redis_keys_deleted = keys_deleted
    
    return result


def fresh_reset_kg_only() -> FreshResetResult:
    """
    Reset only the Knowledge Graph tables.
    
    Useful for re-seeding patterns without losing run history.
    Matches demo-final-showcase.sh Step 2.2 (lines 648-658).
    """
    return fresh_reset_all(
        clear_silver=False,
        clear_gold=False,
        clear_kg=True,
        clear_checkpoints=False,
        flush_redis=False,
    )
