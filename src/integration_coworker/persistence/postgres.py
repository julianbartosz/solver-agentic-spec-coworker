"""
Postgres + pgvector database support.

Per design doc Appendix B, this module provides:
- Connection pooling with psycopg
- Full DDL for spec_silver, integration_gold, and repo_meta schemas
- pgvector VECTOR(1536) for embeddings
- Migration/bootstrap support

Environment Variables:
- DATABASE_URL: Postgres connection string (e.g., postgresql://user:pass@host:5432/db)
"""
from contextlib import contextmanager
from typing import Generator, Optional
import logging

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    HAS_PSYCOPG = True
except ImportError:
    HAS_PSYCOPG = False
    psycopg = None
    ConnectionPool = None

from ..config import get_settings

logger = logging.getLogger(__name__)

# Global connection pool (lazy-initialized)
_pool: Optional["ConnectionPool"] = None


def get_pool() -> "ConnectionPool":
    """
    Get or create the Postgres connection pool.
    
    Uses DATABASE_URL from config.
    """
    global _pool
    
    if not HAS_PSYCOPG:
        raise ImportError(
            "psycopg and psycopg_pool are required for Postgres support. "
            "Install with: pip install 'psycopg[binary]' psycopg_pool"
        )
    
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            settings.database.url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
        )
    
    return _pool


def close_pool() -> None:
    """Close the connection pool (for testing/cleanup)."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_connection() -> Generator["psycopg.Connection", None, None]:
    """
    Get a connection from the pool.
    
    Usage:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM ...")
    """
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


# =============================================================================
# DDL Definitions per Design Doc Appendix B
# =============================================================================

SPEC_SILVER_DDL = """
-- spec_silver schema: Bronze-to-Silver extracted API metadata
-- Per design doc Appendix B.2

CREATE SCHEMA IF NOT EXISTS spec_silver;

-- Enable pgvector extension for embeddings
CREATE EXTENSION IF NOT EXISTS vector;

-- source_systems: Known API providers
CREATE TABLE IF NOT EXISTS spec_silver.source_systems (
    id           BIGSERIAL PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    base_url     TEXT
);

-- spec_documents: Ingested specification files
CREATE TABLE IF NOT EXISTS spec_silver.spec_documents (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    version          TEXT NOT NULL DEFAULT '1.0',
    uri              TEXT NOT NULL,
    content_type     TEXT NOT NULL,
    sha256           TEXT NOT NULL,
    UNIQUE (source_system_id, sha256)
);

-- spec_sections: Logical sections within a spec document
CREATE TABLE IF NOT EXISTS spec_silver.spec_sections (
    id               BIGSERIAL PRIMARY KEY,
    spec_document_id BIGINT NOT NULL REFERENCES spec_silver.spec_documents(id) ON DELETE CASCADE,
    section_type     TEXT NOT NULL,
    title            TEXT,
    path             TEXT,
    start_offset     INT,
    end_offset       INT,
    content          TEXT NOT NULL
);

-- schemas: OpenAPI/JSON schema definitions
CREATE TABLE IF NOT EXISTS spec_silver.schemas (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    ref              TEXT,
    description      TEXT,
    UNIQUE (source_system_id, name)
);

-- fields: Schema field definitions
CREATE TABLE IF NOT EXISTS spec_silver.fields (
    id          BIGSERIAL PRIMARY KEY,
    schema_id   BIGINT NOT NULL REFERENCES spec_silver.schemas(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    json_path   TEXT NOT NULL,
    type        TEXT NOT NULL,
    format      TEXT,
    required    BOOLEAN NOT NULL DEFAULT FALSE,
    description TEXT,
    UNIQUE (schema_id, json_path)
);

-- entities: Domain entities extracted from specs
CREATE TABLE IF NOT EXISTS spec_silver.entities (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    schema_id        BIGINT REFERENCES spec_silver.schemas(id),
    description      TEXT,
    UNIQUE (source_system_id, name)
);

-- entity_relationships: Relationships between entities
CREATE TABLE IF NOT EXISTS spec_silver.entity_relationships (
    id                BIGSERIAL PRIMARY KEY,
    source_system_id  BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    from_entity_id    BIGINT NOT NULL REFERENCES spec_silver.entities(id) ON DELETE CASCADE,
    to_entity_id      BIGINT NOT NULL REFERENCES spec_silver.entities(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL,
    description       TEXT,
    UNIQUE (source_system_id, from_entity_id, to_entity_id, relationship_type)
);

-- events: Webhook/event definitions
CREATE TABLE IF NOT EXISTS spec_silver.events (
    id                BIGSERIAL PRIMARY KEY,
    source_system_id  BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    description       TEXT,
    payload_schema_id BIGINT REFERENCES spec_silver.schemas(id),
    entity_id         BIGINT REFERENCES spec_silver.entities(id),
    UNIQUE (source_system_id, name)
);

-- endpoints: API endpoint definitions
CREATE TABLE IF NOT EXISTS spec_silver.endpoints (
    id                 BIGSERIAL PRIMARY KEY,
    source_system_id   BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    spec_document_id   BIGINT NOT NULL REFERENCES spec_silver.spec_documents(id) ON DELETE CASCADE,
    path               TEXT NOT NULL,
    method             TEXT NOT NULL,
    operation_id       TEXT,
    summary            TEXT,
    description        TEXT,
    request_schema_id  BIGINT REFERENCES spec_silver.schemas(id),
    response_schema_id BIGINT REFERENCES spec_silver.schemas(id),
    auth_required      BOOLEAN NOT NULL DEFAULT FALSE,
    pagination_style   TEXT,
    rate_limit_bucket  TEXT,
    UNIQUE (source_system_id, spec_document_id, path, method)
);

-- endpoint_parameters: Parameter definitions for endpoints
CREATE TABLE IF NOT EXISTS spec_silver.endpoint_parameters (
    id          BIGSERIAL PRIMARY KEY,
    endpoint_id BIGINT NOT NULL REFERENCES spec_silver.endpoints(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    location    TEXT NOT NULL,
    required    BOOLEAN NOT NULL DEFAULT FALSE,
    schema_ref  TEXT,
    description TEXT,
    UNIQUE (endpoint_id, name, location)
);

-- spec_chunks: Text chunks with embeddings for RAG
-- Per design doc Section 6.2 - pgvector VECTOR(1536)
CREATE TABLE IF NOT EXISTS spec_silver.spec_chunks (
    id               BIGSERIAL PRIMARY KEY,
    spec_document_id BIGINT NOT NULL REFERENCES spec_silver.spec_documents(id) ON DELETE CASCADE,
    chunk_index      INT NOT NULL,
    content          TEXT NOT NULL,
    embedding        VECTOR(1536),
    UNIQUE (spec_document_id, chunk_index)
);

-- Create index for vector similarity search
CREATE INDEX IF NOT EXISTS spec_chunks_embedding_idx 
ON spec_silver.spec_chunks 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- file_specs: CSV/EDI file metadata (for non-HTTP specs)
CREATE TABLE IF NOT EXISTS spec_silver.file_specs (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    file_type        TEXT NOT NULL,
    delimiter        TEXT,
    encoding         TEXT DEFAULT 'utf-8',
    header_row       BOOLEAN DEFAULT TRUE,
    schema_id        BIGINT REFERENCES spec_silver.schemas(id),
    description      TEXT,
    UNIQUE (source_system_id, name)
);

-- message_specs: Event/message interface metadata
CREATE TABLE IF NOT EXISTS spec_silver.message_specs (
    id                BIGSERIAL PRIMARY KEY,
    source_system_id  BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    protocol          TEXT NOT NULL,
    topic_pattern     TEXT,
    payload_schema_id BIGINT REFERENCES spec_silver.schemas(id),
    direction         TEXT NOT NULL DEFAULT 'inbound',
    description       TEXT,
    UNIQUE (source_system_id, name)
);
"""

INTEGRATION_GOLD_DDL = """
-- integration_gold schema: Planned integration workflows and artifacts
-- Per design doc Appendix B.3

CREATE SCHEMA IF NOT EXISTS integration_gold;

-- integration_tasks: Top-level task definitions
CREATE TABLE IF NOT EXISTS integration_gold.integration_tasks (
    id                      BIGSERIAL PRIMARY KEY,
    provider_code           TEXT NOT NULL,
    task_slug               TEXT NOT NULL,
    description             TEXT NOT NULL,
    source_system_id        BIGINT REFERENCES spec_silver.source_systems(id),
    target_spec_document_id BIGINT REFERENCES spec_silver.spec_documents(id),
    input_entities          JSONB NOT NULL DEFAULT '[]',
    output_entities         JSONB NOT NULL DEFAULT '[]',
    constraints_json        JSONB NOT NULL DEFAULT '{}',
    UNIQUE (provider_code, task_slug)
);

-- workflow_templates: Reusable workflow patterns
CREATE TABLE IF NOT EXISTS integration_gold.workflow_templates (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id),
    code             TEXT NOT NULL,
    name             TEXT NOT NULL,
    description      TEXT,
    UNIQUE (source_system_id, code)
);

-- integration_flow_nodes: Nodes in a workflow graph
CREATE TABLE IF NOT EXISTS integration_gold.integration_flow_nodes (
    id          BIGSERIAL PRIMARY KEY,
    task_id     BIGINT NOT NULL REFERENCES integration_gold.integration_tasks(id) ON DELETE CASCADE,
    node_key    TEXT NOT NULL,
    node_type   TEXT NOT NULL,
    endpoint_id BIGINT REFERENCES spec_silver.endpoints(id),
    entity_id   BIGINT REFERENCES spec_silver.entities(id),
    position    INT NOT NULL,
    config      JSONB NOT NULL DEFAULT '{}',
    UNIQUE (task_id, node_key)
);

-- integration_flow_edges: Edges connecting workflow nodes
CREATE TABLE IF NOT EXISTS integration_gold.integration_flow_edges (
    id            BIGSERIAL PRIMARY KEY,
    task_id       BIGINT NOT NULL REFERENCES integration_gold.integration_tasks(id) ON DELETE CASCADE,
    from_node_key TEXT NOT NULL,
    to_node_key   TEXT NOT NULL,
    condition     TEXT,
    UNIQUE (task_id, from_node_key, to_node_key)
);

-- endpoint_bindings: Mapping between workflow nodes and API endpoints
CREATE TABLE IF NOT EXISTS integration_gold.endpoint_bindings (
    id               BIGSERIAL PRIMARY KEY,
    task_id          BIGINT NOT NULL REFERENCES integration_gold.integration_tasks(id) ON DELETE CASCADE,
    flow_node_key    TEXT NOT NULL,
    endpoint_id      BIGINT NOT NULL REFERENCES spec_silver.endpoints(id),
    request_mapping  JSONB NOT NULL,
    response_mapping JSONB NOT NULL,
    UNIQUE (task_id, flow_node_key, endpoint_id)
);

-- policies: Attached policies (retry, rate-limit, auth, etc.)
CREATE TABLE IF NOT EXISTS integration_gold.policies (
    id          BIGSERIAL PRIMARY KEY,
    task_id     BIGINT NOT NULL REFERENCES integration_gold.integration_tasks(id) ON DELETE CASCADE,
    policy_type TEXT NOT NULL,
    scope       TEXT NOT NULL,
    scope_ref   TEXT,
    config      JSONB NOT NULL,
    UNIQUE (task_id, policy_type, scope, scope_ref)
);

-- code_artifacts: Generated code files
CREATE TABLE IF NOT EXISTS integration_gold.code_artifacts (
    id            BIGSERIAL PRIMARY KEY,
    task_id       BIGINT NOT NULL REFERENCES integration_gold.integration_tasks(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    rel_path      TEXT NOT NULL,
    language      TEXT NOT NULL,
    module_name   TEXT,
    content       TEXT NOT NULL,
    sha256        TEXT,
    UNIQUE (task_id, rel_path, artifact_type)
);

-- run_status: Execution status tracking
CREATE TABLE IF NOT EXISTS integration_gold.run_status (
    run_id           TEXT PRIMARY KEY,
    task_id          BIGINT REFERENCES integration_gold.integration_tasks(id),
    status           TEXT NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    error_summary    TEXT,
    langsmith_run_id TEXT
);

-- rag_eval_metrics: RAG evaluation metrics per run/node
CREATE TABLE IF NOT EXISTS integration_gold.rag_eval_metrics (
    id                    BIGSERIAL PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES integration_gold.run_status(run_id) ON DELETE CASCADE,
    provider_code         TEXT NOT NULL,
    task_slug             TEXT NOT NULL,
    node_name             TEXT NOT NULL,
    metric_scope          TEXT NOT NULL,
    retrieved_chunk_count INT,
    used_chunk_count      INT,
    est_context_tokens    INT,
    graph_radius          INT,
    top_k                 INT,
    coverage_score        DOUBLE PRECISION,
    precision_score       DOUBLE PRECISION,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

REPO_META_DDL = """
-- repo_meta schema: Repository integration tracking
-- Per design doc Appendix B.4

CREATE SCHEMA IF NOT EXISTS repo_meta;

-- integrations: Links between tasks and repositories
CREATE TABLE IF NOT EXISTS repo_meta.integrations (
    id            BIGSERIAL PRIMARY KEY,
    provider_code TEXT NOT NULL,
    task_slug     TEXT NOT NULL,
    repo_name     TEXT NOT NULL,
    repo_root     TEXT NOT NULL,
    profile_name  TEXT NOT NULL,
    first_run_id  TEXT,
    last_run_id   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider_code, task_slug, repo_name)
);

-- files: Files created/modified by integrations
CREATE TABLE IF NOT EXISTS repo_meta.files (
    id               BIGSERIAL PRIMARY KEY,
    integration_id   BIGINT NOT NULL REFERENCES repo_meta.integrations(id) ON DELETE CASCADE,
    rel_path         TEXT NOT NULL,
    artifact_type    TEXT NOT NULL,
    last_run_id      TEXT,
    last_change_type TEXT,
    last_sha256      TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (integration_id, rel_path, artifact_type)
);
"""


def init_postgres_schema() -> None:
    """
    Initialize all Postgres schemas and tables.
    
    Creates spec_silver, integration_gold, and repo_meta schemas with all tables.
    Safe to call multiple times (uses CREATE IF NOT EXISTS).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Execute DDL in order (respecting foreign key dependencies)
            cur.execute(SPEC_SILVER_DDL)
            cur.execute(INTEGRATION_GOLD_DDL)
            cur.execute(REPO_META_DDL)
        conn.commit()
    
    logger.info("Postgres schema initialized successfully")


def drop_all_schemas() -> None:
    """
    Drop all schemas (for testing only).
    
    WARNING: This will delete all data!
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS repo_meta CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS integration_gold CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS spec_silver CASCADE")
        conn.commit()
    
    logger.warning("All schemas dropped")


def check_connection() -> bool:
    """
    Check if the Postgres connection is working.
    
    Returns True if connection successful, False otherwise.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return True
    except Exception as e:
        logger.error(f"Postgres connection check failed: {e}")
        return False


def check_pgvector() -> bool:
    """
    Check if pgvector extension is available.
    
    Returns True if pgvector is installed, False otherwise.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                result = cur.fetchone()
                return result is not None
    except Exception as e:
        logger.error(f"pgvector check failed: {e}")
        return False
