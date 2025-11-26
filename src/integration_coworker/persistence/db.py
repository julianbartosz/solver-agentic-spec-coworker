"""
Database connection and schema management for persistence layer.

Per design doc Section 4.3 and Appendix B:
- PRIMARY PATH: Postgres 15 + pgvector for production/demo
- FALLBACK: SQLite for tests (USE_SQLITE=true)

The abstraction layer provides:
- Unified get_connection() that returns appropriate connection type
- Unified init_schema() that creates tables for either backend
- Support for pgvector VECTOR(1536) in Postgres mode
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Union, Generator, Any, Protocol
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


def get_engine_type() -> str:
    """
    Get the database engine type based on config.
    
    Returns 'postgres' or 'sqlite'.
    """
    settings = get_settings()
    return settings.database.engine_type


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


def get_connection() -> DBConnection:
    """
    Get a database connection based on config.
    
    Routing logic:
    - If USE_SQLITE=true: returns SQLite connection
    - If USE_SQLITE is not true and DATABASE_URL is set: returns Postgres connection
    - If Postgres is selected but dependencies are missing: raises RuntimeError (no silent fallback)
    - If neither is configured: raises RuntimeError
    
    Returns:
        A database connection (SQLite or Postgres).
        
    Raises:
        RuntimeError: If Postgres is configured but dependencies are missing,
                      or if no database is configured.
    """
    settings = get_settings()
    engine = get_engine_type()
    
    if engine == "sqlite":
        # USE_SQLITE=true explicitly requested
        return get_sqlite_connection()
    
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
            return pool.getconn()
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS run_status (
            run_id TEXT PRIMARY KEY,
            task_id INTEGER,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at TEXT,
            error_summary TEXT,
            langsmith_run_id TEXT,
            FOREIGN KEY (task_id) REFERENCES integration_tasks(id)
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
    
    conn.commit()
    conn.close()
    
    logger.info("SQLite schema initialized successfully")


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
                cur.execute("DELETE FROM repo_meta.files")
                cur.execute("DELETE FROM repo_meta.integrations")
                cur.execute("DELETE FROM integration_gold.rag_eval_metrics")
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
                cur.execute("DELETE FROM spec_silver.source_systems")
            conn.commit()
        return
    
    # SQLite path
    conn = get_sqlite_connection()
    cur = conn.cursor()
    
    # Delete in reverse dependency order
    cur.execute("DELETE FROM repo_files")
    cur.execute("DELETE FROM repo_integrations")
    cur.execute("DELETE FROM rag_eval_metrics")
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
    cur.execute("DELETE FROM source_systems")
    
    conn.commit()
    conn.close()
