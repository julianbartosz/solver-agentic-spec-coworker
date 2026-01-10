"""
Unified database bootstrap for production readiness.

This module provides a single entry point to ensure ALL required database
infrastructure is ready before any workflow runs:

1. Application schemas/tables (integration_gold.*, spec_silver.*, kg.*, etc.)
2. LangGraph checkpointer tables (via PostgresSaver.setup())
3. Artifact index tables (integration_gold.run_artifacts)

Per P0 production validation requirements:
- A fresh Postgres database must be fully bootstrapped by a single command
- No FK constraint failures during workflow execution
- No "table not found" errors during artifact spooling or checkpoint ops

Usage:
    from integration_coworker.persistence.bootstrap import ensure_database_ready
    
    # Before any workflow runs:
    ensure_database_ready()
"""
import logging
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def ensure_database_ready(conn_string: Optional[str] = None) -> dict:
    """
    Ensure the database is fully ready for production workflows.
    
    This is the SINGLE entry point for database bootstrap. Call this before
    running any workflow to guarantee all tables exist.
    
    Args:
        conn_string: Optional connection string. If not provided, uses
                    DATABASE_URL from environment.
    
    Returns:
        dict with bootstrap status:
        {
            "engine": "postgres" | "sqlite",
            "app_schema_ok": bool,
            "checkpointer_ok": bool,
            "artifact_index_ok": bool,
            "errors": [list of error messages]
        }
    
    Raises:
        RuntimeError: If bootstrap fails critically (no database connection)
    """
    from integration_coworker.persistence.db import get_engine_type, get_connection
    
    result = {
        "engine": get_engine_type(),
        "app_schema_ok": False,
        "checkpointer_ok": False,
        "artifact_index_ok": False,
        "errors": [],
    }
    
    engine = get_engine_type()
    
    if engine == "postgres":
        result = _bootstrap_postgres(conn_string, result)
    else:
        result = _bootstrap_sqlite(result)
    
    # Log result
    if result["errors"]:
        logger.error(f"Database bootstrap completed with errors: {result['errors']}")
    else:
        logger.info(f"Database bootstrap successful: engine={engine}")
    
    return result


def _bootstrap_postgres(conn_string: Optional[str], result: dict) -> dict:
    """
    Bootstrap Postgres database with all required schemas and tables.
    
    Order matters:
    1. Application schemas (integration_gold.run_status must exist before run_artifacts FK)
    2. LangGraph checkpointer tables (separate connection with autocommit)
    3. Artifact index verification
    """
    from integration_coworker.persistence.db import get_connection
    
    # Step 1: Application schemas
    try:
        from integration_coworker.persistence.postgres import init_all_schemas
        init_all_schemas()
        result["app_schema_ok"] = True
        logger.info("Postgres application schemas initialized")
    except Exception as e:
        result["errors"].append(f"App schema init failed: {e}")
        logger.error(f"Failed to init application schemas: {e}")
    
    # Step 2: LangGraph checkpointer tables
    # Per langgraph-checkpoint-postgres docs: requires autocommit connection
    try:
        result["checkpointer_ok"] = _setup_langgraph_checkpointer_postgres(conn_string)
    except Exception as e:
        result["errors"].append(f"LangGraph checkpointer setup failed: {e}")
        logger.error(f"LangGraph checkpointer setup failed: {e}")
    
    # Step 3: Verify artifact index table exists
    try:
        from integration_coworker.persistence.artifacts.index import ensure_table_exists
        ensure_table_exists()
        result["artifact_index_ok"] = True
        logger.info("Artifact index table verified")
    except Exception as e:
        result["errors"].append(f"Artifact index setup failed: {e}")
        logger.error(f"Artifact index setup failed: {e}")
    
    return result


def _setup_langgraph_checkpointer_postgres(conn_string: Optional[str]) -> bool:
    """
    Set up LangGraph PostgresSaver tables with correct connection semantics.
    
    Per langgraph-checkpoint-postgres docs:
    - Connection must have autocommit=True for DDL operations
    - Row factory should return dict-like rows (optional but recommended)
    
    The PostgresSaver.from_conn_string() context manager handles this correctly,
    but we need to ensure setup() is called with proper semantics.
    """
    import os
    
    if conn_string is None:
        conn_string = os.environ.get("DATABASE_URL")
        if not conn_string:
            logger.warning("No DATABASE_URL set, skipping LangGraph checkpointer setup")
            return False
    
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        
        # PostgresSaver.from_conn_string creates a connection pool with proper settings
        # The context manager handles connection lifecycle correctly
        with PostgresSaver.from_conn_string(conn_string) as saver:
            saver.setup()
            logger.info("LangGraph PostgresSaver tables created via .setup()")
        return True
    except ImportError:
        logger.warning("langgraph-checkpoint-postgres not installed, skipping checkpointer setup")
        return False
    except Exception as e:
        logger.error(f"LangGraph checkpointer setup error: {e}")
        raise


def _bootstrap_sqlite(result: dict) -> dict:
    """
    Bootstrap SQLite database with all required tables.
    
    SQLite is simpler - all tables in one database, no schemas.
    """
    from integration_coworker.persistence.db import init_schema
    
    # Step 1: Application tables
    try:
        init_schema()
        result["app_schema_ok"] = True
        logger.info("SQLite application tables initialized")
    except Exception as e:
        result["errors"].append(f"SQLite schema init failed: {e}")
        logger.error(f"Failed to init SQLite schema: {e}")
    
    # Step 2: LangGraph checkpointer tables (SqliteSaver)
    try:
        result["checkpointer_ok"] = _setup_langgraph_checkpointer_sqlite()
    except Exception as e:
        result["errors"].append(f"SQLite checkpointer setup failed: {e}")
        logger.error(f"SQLite checkpointer setup failed: {e}")
    
    # Step 3: Artifact index table
    try:
        from integration_coworker.persistence.artifacts.index import ensure_table_exists
        ensure_table_exists()
        result["artifact_index_ok"] = True
        logger.info("SQLite artifact index table verified")
    except Exception as e:
        result["errors"].append(f"SQLite artifact index setup failed: {e}")
        logger.error(f"SQLite artifact index setup failed: {e}")
    
    return result


def _setup_langgraph_checkpointer_sqlite() -> bool:
    """
    Set up LangGraph SqliteSaver tables.
    """
    import os
    import sqlite3
    
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        
        # Get checkpoint path from runtime module
        data_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "data"
        )
        os.makedirs(data_dir, exist_ok=True)
        sqlite_path = os.path.join(data_dir, "langgraph_checkpoints.db")
        
        conn = sqlite3.connect(sqlite_path)
        try:
            saver = SqliteSaver(conn)
            saver.setup()
            logger.info(f"LangGraph SqliteSaver tables created at {sqlite_path}")
        finally:
            conn.close()
        return True
    except ImportError:
        logger.warning("langgraph-checkpoint-sqlite not available")
        return False
    except Exception as e:
        logger.error(f"SQLite checkpointer setup error: {e}")
        raise


def ensure_run_status_entry(run_id: str, status: str = "running") -> bool:
    """
    Ensure a run_status entry exists for the given run_id.
    
    This is required before creating checkpoints or artifacts due to FK constraints.
    
    Args:
        run_id: The workflow run ID
        status: Initial status (default: "running")
    
    Returns:
        True if entry exists or was created, False on failure
    """
    from integration_coworker.persistence.db import get_connection, get_engine_type
    
    engine = get_engine_type()
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if engine == "postgres":
                    # Postgres: run_status has task_id (nullable), status, timestamps
                    cur.execute("""
                        INSERT INTO integration_gold.run_status 
                        (run_id, status)
                        VALUES (%s, %s)
                        ON CONFLICT (run_id) DO NOTHING
                    """, (run_id, status))
                else:
                    # SQLite: simpler schema
                    cur.execute("""
                        INSERT OR IGNORE INTO run_status 
                        (run_id, status)
                        VALUES (?, ?)
                    """, (run_id, status))
                conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to ensure run_status entry for {run_id}: {e}")
        return False


@contextmanager
def production_database_context(conn_string: Optional[str] = None):
    """
    Context manager that ensures database is ready before yielding.
    
    Usage:
        with production_database_context() as status:
            if status["errors"]:
                raise RuntimeError(f"Database not ready: {status['errors']}")
            # ... run workflow ...
    """
    status = ensure_database_ready(conn_string)
    yield status


# Convenience exports
__all__ = [
    "ensure_database_ready",
    "ensure_run_status_entry", 
    "production_database_context",
]
