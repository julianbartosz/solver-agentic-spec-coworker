"""
Database connection and schema management for persistence layer.

Uses SQLite for M4 milestone (single canonical provider).
"""
import sqlite3
from pathlib import Path
from typing import Optional

# Use a file in the project's .data directory for SQLite
DB_DIR = Path(__file__).parent.parent.parent.parent / ".data"
DB_PATH = DB_DIR / "integration_coworker.sqlite3"


def get_connection() -> sqlite3.Connection:
    """
    Get a connection to the SQLite database.
    
    Ensures the .data directory exists and creates the database file if needed.
    """
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema() -> None:
    """
    Initialize database schema (idempotent).
    
    Creates tables if they don't exist. Safe to call multiple times.
    """
    conn = get_connection()
    cur = conn.cursor()
    
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
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            content_type TEXT,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            UNIQUE(source_system_id, sha256)
        )
    """)
    
    # Endpoints (Silver layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS endpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spec_document_id INTEGER NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            operation_id TEXT,
            summary TEXT,
            request_schema_id INTEGER,
            response_schema_id INTEGER,
            FOREIGN KEY (spec_document_id) REFERENCES spec_documents(id),
            UNIQUE(spec_document_id, method, path)
        )
    """)
    
    # Schemas (Silver layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schemas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spec_document_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            ref TEXT,
            FOREIGN KEY (spec_document_id) REFERENCES spec_documents(id),
            UNIQUE(spec_document_id, name)
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
    
    # Integration tasks (Gold layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS integration_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER NOT NULL,
            task_slug TEXT NOT NULL,
            provider_code TEXT NOT NULL,
            description TEXT,
            target_spec_document_id INTEGER,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            FOREIGN KEY (target_spec_document_id) REFERENCES spec_documents(id),
            UNIQUE(source_system_id, task_slug)
        )
    """)
    
    # Integration flow nodes (Gold layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS integration_flow_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            node_key TEXT NOT NULL,
            node_type TEXT NOT NULL,
            label TEXT,
            position INTEGER NOT NULL,
            FOREIGN KEY (task_id) REFERENCES integration_tasks(id),
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
            FOREIGN KEY (task_id) REFERENCES integration_tasks(id)
        )
    """)
    
    # Endpoint bindings (Gold layer)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS endpoint_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            flow_node_key TEXT NOT NULL,
            endpoint_id INTEGER,
            request_mapping_json TEXT,
            response_mapping_json TEXT,
            FOREIGN KEY (task_id) REFERENCES integration_tasks(id),
            FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
            UNIQUE(task_id, flow_node_key)
        )
    """)
    
    conn.commit()
    conn.close()


def clear_test_data() -> None:
    """
    Clear all data from tables (for testing).
    
    Preserves schema but removes all rows.
    """
    conn = get_connection()
    cur = conn.cursor()
    
    # Delete in reverse dependency order
    cur.execute("DELETE FROM endpoint_bindings")
    cur.execute("DELETE FROM integration_flow_edges")
    cur.execute("DELETE FROM integration_flow_nodes")
    cur.execute("DELETE FROM integration_tasks")
    cur.execute("DELETE FROM entities")
    cur.execute("DELETE FROM schemas")
    cur.execute("DELETE FROM endpoints")
    cur.execute("DELETE FROM spec_documents")
    cur.execute("DELETE FROM source_systems")
    
    conn.commit()
    conn.close()
