"""
Database connection and schema management for persistence layer.

Per design doc Section 4.3 and Appendix B:
- PRIMARY PATH: Postgres 15 + pgvector for production/demo
- FALLBACK: SQLite for tests (USE_SQLITE=true)

The abstraction layer provides:
- Unified get_connection() that returns appropriate connection type
- Unified init_schema() that creates tables for either backend
- Support for pgvector VECTOR(1536) in Postgres mode

V2: USE_SQLITE is deprecated outside of tests. Production requires Postgres.

V1.1: Added ConnectionWrapper for proper connection lifecycle management.
      Ensures connections are returned to pool even on exceptions.
"""
import os
import sqlite3
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol, Generator, Optional, Union
import logging

from ..config import get_settings

logger = logging.getLogger(__name__)

# Use a file in the project's .data directory for SQLite
DB_DIR = Path(__file__).parent.parent.parent.parent / ".data"
DB_PATH = DB_DIR / "integration_coworker.sqlite3"


class DBConnection(Protocol):
    """Protocol for database connections (SQLite or Postgres)."""
    def cursor(self) -> Any: ...
    def commit(self) -> None: ...
    def close(self) -> None: ...
    def execute(self, sql: str, parameters: Any = ...) -> Any: ...


# Track if we've warned about unclosed connections (to avoid spamming logs)
_unclosed_connection_warned = False


class ConnectionWrapper:
    """
    V1.1: Wrapper that ensures database connections are properly returned to pool.
    
    Supports both context manager and manual close() patterns:
    
    Context manager (recommended):
        with db.get_connection() as conn:
            cur = conn.cursor()
            ...
    
    Manual close (legacy, still works):
        conn = db.get_connection()
        try:
            cur = conn.cursor()
            ...
        finally:
            conn.close()
    
    The wrapper tracks whether close() was called and auto-closes on garbage
    collection if needed (with a warning).
    """
    
    def __init__(self, connection: Any, pool: Optional[Any] = None, engine: str = "sqlite"):
        self._conn = connection
        self._pool = pool  # Postgres pool reference for proper return
        self._engine = engine
        self._closed = False
    
    def cursor(self) -> Any:
        """Get a cursor from the underlying connection."""
        return self._conn.cursor()
    
    def commit(self) -> None:
        """Commit the current transaction."""
        self._conn.commit()
    
    def rollback(self) -> None:
        """Rollback the current transaction."""
        if hasattr(self._conn, 'rollback'):
            self._conn.rollback()
    
    def execute(self, sql: str, parameters: Any = None) -> Any:
        """Execute SQL directly on connection (for SQLite compatibility)."""
        if parameters is not None:
            return self._conn.execute(sql, parameters)
        return self._conn.execute(sql)
    
    def close(self) -> None:
        """
        Return connection to pool (Postgres) or close it (SQLite).
        
        Safe to call multiple times.
        """
        if self._closed:
            return
        
        self._closed = True
        
        if self._engine == "postgres" and self._pool is not None:
            # Return to pool using putconn
            try:
                self._pool.putconn(self._conn)
            except Exception as e:
                logger.warning(f"Failed to return connection to pool: {e}")
        else:
            # SQLite - just close
            try:
                self._conn.close()
            except Exception as e:
                logger.warning(f"Failed to close SQLite connection: {e}")
    
    def __enter__(self) -> 'ConnectionWrapper':
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - ensures connection is returned to pool."""
        if exc_type is not None:
            # Exception occurred - rollback
            self.rollback()
        self.close()
        return None  # Don't suppress exceptions
    
    def __del__(self):
        """
        Garbage collection safety net.
        
        Bug #20 fix: Only warn once per session to avoid log spam.
        The connection IS cleaned up, this is just informational.
        """
        global _unclosed_connection_warned
        
        if not self._closed:
            # Only warn once per session to avoid spam
            if not _unclosed_connection_warned:
                _unclosed_connection_warned = True
                logger.warning(
                    "ConnectionWrapper was garbage collected without being closed. "
                    "Use 'with db.get_connection() as conn:' pattern for proper cleanup. "
                    "(This warning is shown once per session)"
                )
            # Still clean up the connection
            try:
                self.close()
            except Exception:
                pass  # Ignore errors during GC cleanup
    
    # Delegate attribute access to underlying connection
    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def get_engine_type() -> str:
    """
    Get the database engine type based on config.
    
    Returns 'postgres' or 'sqlite'.
    """
    settings = get_settings()
    return settings.database.engine_type


# =============================================================================
# Table Name Helpers (PL-001: Pattern Learning)
# =============================================================================
# SQLite uses underscore prefix (kg_nodes), Postgres uses schema (kg.nodes).
# These helpers ensure consistent table naming across backends.

# Table name mappings: base_name -> (sqlite_name, postgres_name)
_KG_TABLE_NAMES = {
    "nodes": ("kg_nodes", "kg.nodes"),
    "edges": ("kg_edges", "kg.edges"),
    "workflow_steps": ("kg_workflow_steps", "kg.workflow_steps"),
    "step_bindings": ("kg_step_bindings", "kg.step_bindings"),
    "feedback_records": ("kg_feedback_records", "kg.feedback_records"),
    "run_events": ("kg_run_events", "kg.run_events"),
    "pattern_candidates": ("kg_pattern_candidates", "kg.pattern_candidates"),
    "pattern_matches": ("kg_pattern_matches", "kg.pattern_matches"),
    "confidence_history": ("kg_confidence_history", "kg.confidence_history"),
    "provider_scoring_config": ("kg_provider_scoring_config", "kg.provider_scoring_config"),
}


def kg_table(base_name: str) -> str:
    """
    Get the appropriate KG table name for current engine.
    
    Args:
        base_name: Base table name without prefix (e.g., "nodes", "run_events")
        
    Returns:
        Full table name for current engine (e.g., "kg_nodes" or "kg.nodes")
        
    Example:
        >>> kg_table("run_events")
        'kg.run_events'  # on Postgres
        'kg_run_events'  # on SQLite
    """
    if base_name not in _KG_TABLE_NAMES:
        raise ValueError(f"Unknown KG table: {base_name}. Known tables: {list(_KG_TABLE_NAMES.keys())}")
    
    is_postgres = get_engine_type() == "postgres"
    sqlite_name, postgres_name = _KG_TABLE_NAMES[base_name]
    return postgres_name if is_postgres else sqlite_name


# Track if we've already warned (to avoid spamming logs)
_sqlite_deprecation_warned = False


def _warn_sqlite_deprecation() -> None:
    """
    Emit a deprecation warning if SQLite is used outside of pytest.
    
    Per V2 Implementation Plan Section 6.2:
    - USE_SQLITE is deprecated for production use
    - Only suppress warning when PYTEST_CURRENT_TEST is set
    """
    global _sqlite_deprecation_warned
    
    # Don't warn during pytest runs
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    
    # Only warn once per process
    if _sqlite_deprecation_warned:
        return
    _sqlite_deprecation_warned = True
    
    warnings.warn(
        "USE_SQLITE=true is deprecated for production use. "
        "Postgres with pgvector is required for production. "
        "SQLite mode will be removed in a future version.",
        DeprecationWarning,
        stacklevel=4,  # Show caller's location, not this function
    )
    logger.warning(
        "SQLite mode is deprecated. "
        "Configure DATABASE_URL for Postgres in production."
    )


def get_sqlite_connection() -> sqlite3.Connection:
    """
    Get a connection to the SQLite database.
    
    Ensures the .data directory exists and creates the database file if needed.
    
    Note: This is for internal use. External callers should use get_connection().
    """
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_connection() -> ConnectionWrapper:
    """
    Get a database connection based on config.
    
    V1.1: Returns ConnectionWrapper for proper lifecycle management.
    Supports both context manager and manual close() patterns.
    
    Recommended usage (context manager):
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT ...")
            conn.commit()
    
    Legacy usage (still works):
        conn = db.get_connection()
        try:
            cur = conn.cursor()
            ...
            conn.commit()
        finally:
            conn.close()
    
    Routing logic:
    - If USE_SQLITE=true: returns SQLite connection
    - If USE_SQLITE is not true and DATABASE_URL is set: returns Postgres connection
    - If Postgres is selected but dependencies are missing: raises RuntimeError (no silent fallback)
    - If neither is configured: raises RuntimeError
    
    Returns:
        A ConnectionWrapper around SQLite or Postgres connection.
        
    Raises:
        RuntimeError: If Postgres is configured but dependencies are missing,
                      or if no database is configured.
    """
    settings = get_settings()
    engine = get_engine_type()

    if engine == "sqlite":
        # USE_SQLITE=true explicitly requested
        # V2: Emit deprecation warning unless running in pytest
        _warn_sqlite_deprecation()
        conn = get_sqlite_connection()
        return ConnectionWrapper(conn, pool=None, engine="sqlite")

    if engine == "postgres":
        # Postgres is configured - must succeed or fail, no silent fallback
        try:
            from .postgres import get_pool
        except ImportError as e:
            raise RuntimeError(
                f"Postgres is configured (USE_SQLITE is not 'true') but required dependencies are missing: {e}\n"
                f"Install with: pip install 'psycopg[binary]' psycopg_pool\n"
                f"Or set USE_SQLITE=true for SQLite mode."
            ) from e

        try:
            pool = get_pool()
            raw_conn = pool.getconn()
            return ConnectionWrapper(raw_conn, pool=pool, engine="postgres")
        except Exception as e:
            raise RuntimeError(
                f"Failed to connect to Postgres database: {e}\n"
                f"DATABASE_URL: {settings.database.url[:50]}...\n"
                f"Ensure Postgres is running and DATABASE_URL is correct.\n"
                f"Or set USE_SQLITE=true for SQLite mode."
            ) from e

    # Should not reach here, but fail explicitly if we do
    raise RuntimeError(
        f"No database configured. Set DATABASE_URL for Postgres or USE_SQLITE=true for SQLite.\n"
        f"Current engine_type: {engine}"
    )


def init_schema() -> None:
    """
    Initialize database schema (idempotent).
    
    For Postgres: Uses postgres.py init_postgres_schema() for full DDL.
    For SQLite: Creates equivalent tables (without pgvector).
    
    Safe to call multiple times.
    
    Raises:
        RuntimeError: If Postgres is configured but dependencies are missing.
    """
    engine = get_engine_type()

    if engine == "postgres":
        try:
            from .postgres import init_postgres_schema
        except ImportError as e:
            raise RuntimeError(
                f"Postgres is configured but required dependencies are missing: {e}\n"
                f"Install with: pip install 'psycopg[binary]' psycopg_pool\n"
                f"Or set USE_SQLITE=true for SQLite mode."
            ) from e

        init_postgres_schema()
        return

    # SQLite schema (for tests and development)
    _init_sqlite_schema()


def _init_sqlite_schema() -> None:
    """
    Initialize SQLite schema with tables matching design doc structure.
    
    Note: SQLite does not support pgvector, so embeddings are stored as TEXT (JSON).
    """
    conn = get_sqlite_connection()
    cur = conn.cursor()

    # =========================================================================
    # spec_silver tables
    # =========================================================================

    # Source systems (providers like stripe, mock_payments)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS source_systems (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            display_name TEXT,
            base_url TEXT
        )
    """)

    # Raw specs (Bronze layer - per V2 Implementation Plan Section 5.2)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS raw_specs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER NOT NULL,
            uri TEXT NOT NULL,
            raw_content BLOB NOT NULL,
            content_type TEXT NOT NULL,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            sha256 TEXT NOT NULL,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            UNIQUE(source_system_id, sha256)
        )
    """)

    # Spec documents
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spec_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER NOT NULL,
            version TEXT DEFAULT '1.0',
            uri TEXT NOT NULL,
            content_type TEXT,
            sha256 TEXT NOT NULL,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            UNIQUE(source_system_id, sha256)
        )
    """)

    # Spec sections (per design doc Appendix B.2)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spec_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spec_document_id INTEGER NOT NULL,
            section_type TEXT NOT NULL,
            title TEXT,
            path TEXT,
            start_offset INTEGER,
            end_offset INTEGER,
            content TEXT NOT NULL,
            FOREIGN KEY (spec_document_id) REFERENCES spec_documents(id)
        )
    """)

    # Schemas (Silver layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schemas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            ref TEXT,
            description TEXT,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            UNIQUE(source_system_id, name)
        )
    """)

    # Fields (per design doc Appendix B.2)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            json_path TEXT NOT NULL,
            type TEXT NOT NULL,
            format TEXT,
            required INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            FOREIGN KEY (schema_id) REFERENCES schemas(id),
            UNIQUE(schema_id, json_path)
        )
    """)

    # Entities (Silver layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            schema_id INTEGER,
            description TEXT,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            FOREIGN KEY (schema_id) REFERENCES schemas(id),
            UNIQUE(source_system_id, name)
        )
    """)

    # Entity relationships (per design doc Appendix B.2)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entity_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER NOT NULL,
            from_entity_id INTEGER NOT NULL,
            to_entity_id INTEGER NOT NULL,
            relationship_type TEXT NOT NULL,
            description TEXT,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            FOREIGN KEY (from_entity_id) REFERENCES entities(id),
            FOREIGN KEY (to_entity_id) REFERENCES entities(id),
            UNIQUE(source_system_id, from_entity_id, to_entity_id, relationship_type)
        )
    """)

    # Events (per design doc Appendix B.2)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            payload_schema_id INTEGER,
            entity_id INTEGER,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            FOREIGN KEY (payload_schema_id) REFERENCES schemas(id),
            FOREIGN KEY (entity_id) REFERENCES entities(id),
            UNIQUE(source_system_id, name)
        )
    """)

    # Endpoints (Silver layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS endpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER NOT NULL,
            spec_document_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            method TEXT NOT NULL,
            operation_id TEXT,
            summary TEXT,
            description TEXT,
            request_schema_id INTEGER,
            response_schema_id INTEGER,
            auth_required INTEGER NOT NULL DEFAULT 0,
            pagination_style TEXT,
            rate_limit_bucket TEXT,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            FOREIGN KEY (spec_document_id) REFERENCES spec_documents(id),
            FOREIGN KEY (request_schema_id) REFERENCES schemas(id),
            FOREIGN KEY (response_schema_id) REFERENCES schemas(id),
            UNIQUE(source_system_id, spec_document_id, path, method)
        )
    """)

    # Endpoint parameters (per design doc Appendix B.2)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS endpoint_parameters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            required INTEGER NOT NULL DEFAULT 0,
            schema_ref TEXT,
            description TEXT,
            FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
            UNIQUE(endpoint_id, name, location)
        )
    """)

    # Spec chunks with embeddings (SQLite stores as JSON text)
    # Per design doc Appendix B.2 - spec_chunks for RAG
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spec_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spec_document_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding TEXT,
            FOREIGN KEY (spec_document_id) REFERENCES spec_documents(id),
            UNIQUE(spec_document_id, chunk_index)
        )
    """)

    # =========================================================================
    # File spec tables (Silver layer - File Integration V1)
    # Per docs/FILE_INTEGRATION_V1_PLAN.md Section 5.3
    # =========================================================================

    # file_specs: CSV/EDI/Excel file metadata
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_specs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            file_type TEXT NOT NULL,
            spec_document_id INTEGER,
            encoding TEXT DEFAULT 'utf-8',
            delimiter TEXT,
            has_header INTEGER DEFAULT 1,
            line_terminator TEXT DEFAULT '\\n',
            quote_char TEXT DEFAULT '"',
            escape_char TEXT,
            description TEXT,
            version TEXT,
            sample_uri TEXT,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id) ON DELETE CASCADE,
            FOREIGN KEY (spec_document_id) REFERENCES spec_documents(id),
            UNIQUE(source_system_id, name)
        )
    """)

    # file_fields: Fields/columns within a file spec
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_spec_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            field_type TEXT NOT NULL,
            position INTEGER NOT NULL,
            start_position INTEGER,
            length INTEGER,
            format_mask TEXT,
            nullable INTEGER DEFAULT 1,
            default_value TEXT,
            validation_regex TEXT,
            description TEXT,
            sample_values TEXT DEFAULT '[]',
            inference_confidence REAL DEFAULT 1.0,
            FOREIGN KEY (file_spec_id) REFERENCES file_specs(id) ON DELETE CASCADE,
            UNIQUE(file_spec_id, name)
        )
    """)

    # record_layouts: For multi-record fixed-width files
    cur.execute("""
        CREATE TABLE IF NOT EXISTS record_layouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_spec_id INTEGER NOT NULL,
            record_type TEXT NOT NULL,
            identifier_field TEXT,
            identifier_value TEXT,
            record_length INTEGER,
            position INTEGER DEFAULT 0,
            min_occurrences INTEGER DEFAULT 0,
            max_occurrences INTEGER,
            description TEXT,
            FOREIGN KEY (file_spec_id) REFERENCES file_specs(id) ON DELETE CASCADE,
            UNIQUE(file_spec_id, record_type)
        )
    """)

    # file_validation_rules: Validation rules for file data
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_validation_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_spec_id INTEGER NOT NULL,
            field_name TEXT,
            rule_type TEXT NOT NULL,
            rule_config TEXT DEFAULT '{}',
            error_message TEXT,
            severity TEXT DEFAULT 'error',
            FOREIGN KEY (file_spec_id) REFERENCES file_specs(id) ON DELETE CASCADE
        )
    """)

    # file_field_mappings: Map file fields to entity fields
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_field_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_field_id INTEGER NOT NULL,
            entity_id INTEGER,
            entity_field_name TEXT NOT NULL,
            transform_expression TEXT,
            description TEXT,
            FOREIGN KEY (file_field_id) REFERENCES file_fields(id) ON DELETE CASCADE,
            FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE SET NULL
        )
    """)

    # Indexes for file-related tables
    cur.execute("CREATE INDEX IF NOT EXISTS idx_file_fields_spec ON file_fields(file_spec_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_record_layouts_spec ON record_layouts(file_spec_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_file_validation_rules_spec ON file_validation_rules(file_spec_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_file_field_mappings_field ON file_field_mappings(file_field_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_file_field_mappings_entity ON file_field_mappings(entity_id)")

    # =========================================================================
    # integration_gold tables
    # =========================================================================

    # Integration tasks (Gold layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS integration_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_code TEXT NOT NULL,
            task_slug TEXT NOT NULL,
            description TEXT NOT NULL,
            source_system_id INTEGER,
            target_spec_document_id INTEGER,
            input_entities TEXT DEFAULT '[]',
            output_entities TEXT DEFAULT '[]',
            constraints_json TEXT DEFAULT '{}',
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            FOREIGN KEY (target_spec_document_id) REFERENCES spec_documents(id),
            UNIQUE(provider_code, task_slug)
        )
    """)

    # Workflow templates (per design doc Appendix B.3)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS workflow_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            UNIQUE(source_system_id, code)
        )
    """)

    # Integration flow nodes (Gold layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS integration_flow_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            node_key TEXT NOT NULL,
            node_type TEXT NOT NULL,
            endpoint_id INTEGER,
            entity_id INTEGER,
            position INTEGER NOT NULL,
            config TEXT DEFAULT '{}',
            FOREIGN KEY (task_id) REFERENCES integration_tasks(id),
            FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
            FOREIGN KEY (entity_id) REFERENCES entities(id),
            UNIQUE(task_id, node_key)
        )
    """)

    # Integration flow edges (Gold layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS integration_flow_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            from_node_key TEXT NOT NULL,
            to_node_key TEXT NOT NULL,
            condition TEXT,
            FOREIGN KEY (task_id) REFERENCES integration_tasks(id),
            UNIQUE(task_id, from_node_key, to_node_key)
        )
    """)

    # Endpoint bindings (Gold layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS endpoint_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            flow_node_key TEXT NOT NULL,
            endpoint_id INTEGER NOT NULL,
            request_mapping TEXT NOT NULL,
            response_mapping TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES integration_tasks(id),
            FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
            UNIQUE(task_id, flow_node_key, endpoint_id)
        )
    """)

    # Policies (per design doc Appendix B.3)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            policy_type TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_ref TEXT,
            config TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES integration_tasks(id),
            UNIQUE(task_id, policy_type, scope, scope_ref)
        )
    """)

    # Code artifacts (Gold layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS code_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            artifact_type TEXT NOT NULL,
            rel_path TEXT NOT NULL,
            language TEXT NOT NULL,
            module_name TEXT,
            content TEXT NOT NULL,
            sha256 TEXT,
            FOREIGN KEY (task_id) REFERENCES integration_tasks(id),
            UNIQUE(task_id, rel_path, artifact_type)
        )
    """)

    # Run status (per design doc Appendix B.3)
    # Note: task_id is nullable to support standalone runs without a pre-created task
    # FK constraint removed to avoid ordering issues during concurrent operations
    cur.execute("""
        CREATE TABLE IF NOT EXISTS run_status (
            run_id TEXT PRIMARY KEY,
            task_id INTEGER,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at TEXT,
            error_summary TEXT,
            langsmith_run_id TEXT
        )
    """)

    # Run checkpoints (per V2 Implementation Plan Section 3.5)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS run_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            node_name TEXT NOT NULL,
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (run_id) REFERENCES run_status(run_id),
            UNIQUE(run_id, node_name)
        )
    """)

    # RAG eval metrics (per design doc Appendix B.3)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rag_eval_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            provider_code TEXT NOT NULL,
            task_slug TEXT NOT NULL,
            node_name TEXT NOT NULL,
            metric_scope TEXT NOT NULL,
            retrieved_chunk_count INTEGER,
            used_chunk_count INTEGER,
            est_context_tokens INTEGER,
            graph_radius INTEGER,
            top_k INTEGER,
            coverage_score REAL,
            precision_score REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (run_id) REFERENCES run_status(run_id)
        )
    """)

    # =========================================================================
    # repo_meta tables
    # =========================================================================

    # Integrations (per design doc Appendix B.4)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS repo_integrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_code TEXT NOT NULL,
            task_slug TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            repo_root TEXT NOT NULL,
            profile_name TEXT NOT NULL,
            first_run_id TEXT,
            last_run_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(provider_code, task_slug, repo_name)
        )
    """)

    # Repo files (per design doc Appendix B.4)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS repo_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            integration_id INTEGER NOT NULL,
            rel_path TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            last_run_id TEXT,
            last_change_type TEXT,
            last_sha256 TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (integration_id) REFERENCES repo_integrations(id),
            UNIQUE(integration_id, rel_path, artifact_type)
        )
    """)

    # =========================================================================
    # kg (Knowledge Graph) tables for GraphRAG
    # =========================================================================

    # Provider scoring config (per V2 Implementation Plan Section 5.2)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS provider_scoring_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_code TEXT NOT NULL UNIQUE,
            graph_weight REAL NOT NULL DEFAULT 0.4,
            embedding_weight REAL NOT NULL DEFAULT 0.4,
            exact_match_weight REAL NOT NULL DEFAULT 0.2,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # KG nodes (core graph nodes)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_type TEXT NOT NULL,
            provider_code TEXT,
            key TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            properties TEXT DEFAULT '{}',
            embedding TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            confidence_score REAL DEFAULT 1.0,
            usage_count INTEGER DEFAULT 0,
            last_used_at TEXT,
            source_run_id TEXT,
            UNIQUE(node_type, key)
        )
    """)

    # KG edges (relationships between nodes)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kg_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src_node_id INTEGER NOT NULL,
            dst_node_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            properties TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            source_run_id TEXT,
            FOREIGN KEY (src_node_id) REFERENCES kg_nodes(id),
            FOREIGN KEY (dst_node_id) REFERENCES kg_nodes(id),
            UNIQUE(src_node_id, dst_node_id, relation_type)
        )
    """)

    # KG workflow steps
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kg_workflow_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_node_id INTEGER NOT NULL,
            step_key TEXT NOT NULL,
            step_type TEXT NOT NULL,
            position INTEGER NOT NULL,
            label TEXT,
            description TEXT,
            config TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (template_node_id) REFERENCES kg_nodes(id),
            UNIQUE(template_node_id, step_key)
        )
    """)

    # KG step bindings
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kg_step_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            step_id INTEGER NOT NULL,
            endpoint_node_id INTEGER,
            endpoint_path TEXT,
            endpoint_method TEXT,
            request_mapping TEXT DEFAULT '{}',
            response_mapping TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (step_id) REFERENCES kg_workflow_steps(id),
            FOREIGN KEY (endpoint_node_id) REFERENCES kg_nodes(id),
            UNIQUE(step_id, endpoint_node_id)
        )
    """)

    # ==========================================================================
    # Feedback and Learning Tables
    # Per docs/decisions/FEEDBACK_LEARNING_IMPLEMENTATION.md
    # ==========================================================================

    # KG feedback records
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kg_feedback_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            template_key TEXT,
            pattern_key TEXT,
            feedback_type TEXT NOT NULL,
            score REAL NOT NULL,
            comment TEXT,
            source TEXT NOT NULL DEFAULT 'langsmith',
            langsmith_feedback_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            synced_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Create unique index for deduplication
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS kg_feedback_dedup_idx 
        ON kg_feedback_records(run_id, langsmith_feedback_id)
        WHERE langsmith_feedback_id IS NOT NULL
    """)

    # KG confidence history
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kg_confidence_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_key TEXT NOT NULL,
            old_confidence REAL,
            new_confidence REAL NOT NULL,
            feedback_count INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Create indexes for feedback tables
    cur.execute("CREATE INDEX IF NOT EXISTS kg_feedback_run_idx ON kg_feedback_records(run_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS kg_feedback_template_idx ON kg_feedback_records(template_key)")
    cur.execute("CREATE INDEX IF NOT EXISTS kg_confidence_history_node_idx ON kg_confidence_history(node_key)")

    # ==========================================================================
    # Dynamic Pattern Learning Tables (PL-001)
    # Per docs/PATTERN_LEARNING_DESIGN.md
    # ==========================================================================

    # KG run events - event log for pattern discovery
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kg_run_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            activity TEXT NOT NULL,
            activity_type TEXT,
            position INTEGER,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            provider_code TEXT,
            endpoint_path TEXT,
            endpoint_method TEXT,
            attributes TEXT DEFAULT '{}',
            FOREIGN KEY (run_id) REFERENCES run_status(run_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kg_run_events_run_idx ON kg_run_events(run_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS kg_run_events_activity_idx ON kg_run_events(activity)")
    
    # Add unique constraint to prevent double-capture on retries
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS kg_run_events_dedupe_idx 
        ON kg_run_events(run_id, position, event_type, activity)
    """)

    # KG pattern candidates - staging table for discovered patterns
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kg_pattern_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT,
            canonical_sequence TEXT NOT NULL,
            signature_version TEXT DEFAULT 'v1',
            signature_hash TEXT,
            support_count INTEGER NOT NULL DEFAULT 1,
            first_seen_run_id TEXT,
            last_seen_run_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            promoted_pattern_key TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kg_pattern_candidates_status_idx ON kg_pattern_candidates(status)")

    # KG pattern matches - record match decisions for explainability
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kg_pattern_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            pattern_key TEXT NOT NULL,
            match_score REAL NOT NULL,
            match_method TEXT NOT NULL,
            explanation TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (run_id) REFERENCES run_status(run_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS kg_pattern_matches_run_idx ON kg_pattern_matches(run_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS kg_pattern_matches_pattern_idx ON kg_pattern_matches(pattern_key)")

    # Add origin column to kg_nodes if not exists (migration-safe)
    try:
        cur.execute("ALTER TABLE kg_nodes ADD COLUMN origin TEXT DEFAULT 'seeded'")
    except Exception:
        pass  # Column already exists

    # Backfill NULL origin values to 'seeded' (defensive migration)
    cur.execute("UPDATE kg_nodes SET origin = 'seeded' WHERE origin IS NULL")
    
    # Additional composite indexes for pattern learning queries
    cur.execute("CREATE INDEX IF NOT EXISTS kg_run_events_run_position_idx ON kg_run_events(run_id, position)")
    cur.execute("CREATE INDEX IF NOT EXISTS kg_pattern_candidates_status_support_idx ON kg_pattern_candidates(status, support_count DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS kg_nodes_type_origin_idx ON kg_nodes(node_type, origin)")

    conn.commit()
    conn.close()
    
    # KG-002 Fix: Seed standard patterns into kg_nodes
    # This enables cross-provider pattern matching from the start
    try:
        from integration_coworker.persistence.seed_kg import seed_standard_patterns
        added, skipped = seed_standard_patterns()
        if added > 0:
            logger.info(f"Seeded {added} standard patterns into kg_nodes")
    except Exception as e:
        # Don't fail init if pattern seeding fails - it's not critical
        logger.warning(f"Failed to seed standard patterns: {e}")

    logger.info("SQLite schema initialized successfully (including kg tables)")


def clear_test_data() -> None:
    """
    Clear all data from tables (for testing).
    
    Preserves schema but removes all rows.
    Works for both SQLite and Postgres.
    
    Raises:
        RuntimeError: If Postgres is configured but dependencies are missing.
    """
    engine = get_engine_type()

    if engine == "postgres":
        try:
            from .postgres import get_connection as pg_get_connection
        except ImportError as e:
            raise RuntimeError(
                f"Postgres is configured but required dependencies are missing: {e}\n"
                f"Install with: pip install 'psycopg[binary]' psycopg_pool\n"
                f"Or set USE_SQLITE=true for SQLite mode."
            ) from e

        with pg_get_connection() as conn:
            with conn.cursor() as cur:
                # Delete in reverse dependency order
                # PL-001: Pattern learning tables first
                cur.execute("DELETE FROM kg.pattern_matches")
                cur.execute("DELETE FROM kg.pattern_candidates")
                cur.execute("DELETE FROM kg.run_events")
                # Feedback tables (no foreign keys)
                cur.execute("DELETE FROM kg.confidence_history")
                cur.execute("DELETE FROM kg.feedback_records")
                # KG tables
                cur.execute("DELETE FROM kg.step_bindings")
                cur.execute("DELETE FROM kg.workflow_steps")
                cur.execute("DELETE FROM kg.edges")
                cur.execute("DELETE FROM kg.nodes")
                cur.execute("DELETE FROM kg.provider_scoring_config")
                # Then repo_meta
                cur.execute("DELETE FROM repo_meta.files")
                cur.execute("DELETE FROM repo_meta.integrations")
                cur.execute("DELETE FROM integration_gold.rag_eval_metrics")
                cur.execute("DELETE FROM integration_gold.run_checkpoints")
                cur.execute("DELETE FROM integration_gold.run_status")
                cur.execute("DELETE FROM integration_gold.code_artifacts")
                cur.execute("DELETE FROM integration_gold.policies")
                cur.execute("DELETE FROM integration_gold.endpoint_bindings")
                cur.execute("DELETE FROM integration_gold.integration_flow_edges")
                cur.execute("DELETE FROM integration_gold.integration_flow_nodes")
                cur.execute("DELETE FROM integration_gold.workflow_templates")
                cur.execute("DELETE FROM integration_gold.integration_tasks")
                cur.execute("DELETE FROM spec_silver.spec_chunks")
                cur.execute("DELETE FROM spec_silver.endpoint_parameters")
                cur.execute("DELETE FROM spec_silver.endpoints")
                cur.execute("DELETE FROM spec_silver.events")
                cur.execute("DELETE FROM spec_silver.entity_relationships")
                cur.execute("DELETE FROM spec_silver.entities")
                cur.execute("DELETE FROM spec_silver.fields")
                cur.execute("DELETE FROM spec_silver.schemas")
                cur.execute("DELETE FROM spec_silver.spec_sections")
                cur.execute("DELETE FROM spec_silver.spec_documents")
                cur.execute("DELETE FROM spec_bronze.raw_specs")
                cur.execute("DELETE FROM spec_silver.source_systems")
            conn.commit()
        return

    # SQLite path
    conn = get_sqlite_connection()
    cur = conn.cursor()

    # Delete in reverse dependency order
    # PL-001: Pattern learning tables first
    cur.execute("DELETE FROM kg_pattern_matches")
    cur.execute("DELETE FROM kg_pattern_candidates")
    cur.execute("DELETE FROM kg_run_events")
    # Feedback tables
    cur.execute("DELETE FROM kg_confidence_history")
    cur.execute("DELETE FROM kg_feedback_records")
    # KG tables
    cur.execute("DELETE FROM kg_step_bindings")
    cur.execute("DELETE FROM kg_workflow_steps")
    cur.execute("DELETE FROM kg_edges")
    cur.execute("DELETE FROM kg_nodes")
    cur.execute("DELETE FROM provider_scoring_config")
    # Then rest
    cur.execute("DELETE FROM repo_files")
    cur.execute("DELETE FROM repo_integrations")
    cur.execute("DELETE FROM rag_eval_metrics")
    cur.execute("DELETE FROM run_checkpoints")
    cur.execute("DELETE FROM run_status")
    cur.execute("DELETE FROM code_artifacts")
    cur.execute("DELETE FROM policies")
    cur.execute("DELETE FROM endpoint_bindings")
    cur.execute("DELETE FROM integration_flow_edges")
    cur.execute("DELETE FROM integration_flow_nodes")
    cur.execute("DELETE FROM workflow_templates")
    cur.execute("DELETE FROM integration_tasks")
    cur.execute("DELETE FROM spec_chunks")
    cur.execute("DELETE FROM endpoint_parameters")
    cur.execute("DELETE FROM endpoints")
    cur.execute("DELETE FROM events")
    cur.execute("DELETE FROM entity_relationships")
    cur.execute("DELETE FROM entities")
    cur.execute("DELETE FROM fields")
    cur.execute("DELETE FROM schemas")
    cur.execute("DELETE FROM spec_sections")
    cur.execute("DELETE FROM spec_documents")
    cur.execute("DELETE FROM raw_specs")
    cur.execute("DELETE FROM source_systems")

    conn.commit()
    conn.close()
