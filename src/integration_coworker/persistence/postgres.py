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
import atexit
from contextlib import contextmanager
from typing import Generator, Optional
import logging

try:
    import psycopg
    from psycopg_pool import ConnectionPool
    HAS_PSYCOPG = True
    # Bug #49 fix: Suppress "rolling back returned connection" warnings from psycopg_pool
    # These are informational but noisy - our _quiet_reset handles rollbacks properly
    # Note: The actual logger is "psycopg.pool" (not "psycopg_pool")
    logging.getLogger('psycopg.pool').setLevel(logging.CRITICAL)
    logging.getLogger('psycopg_pool').setLevel(logging.CRITICAL)
except ImportError:
    HAS_PSYCOPG = False
    psycopg = None
    ConnectionPool = None

from ..config import get_settings

logger = logging.getLogger(__name__)

# Global connection pool (lazy-initialized)
_pool: Optional["ConnectionPool"] = None


def _cleanup_pool():
    """
    Cleanup handler to close the pool on exit.
    
    Bug #23 fix: Made more resilient to thread errors during Python shutdown.
    When Python is shutting down, threads may be in inconsistent state
    causing RuntimeError("cannot join current thread"). This is harmless
    since the process is exiting anyway.
    """
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except RuntimeError as e:
            # Bug #23: Ignore "cannot join current thread" during shutdown
            if "cannot join" in str(e).lower():
                pass  # Expected during Python shutdown
            else:
                pass  # Still ignore - we're shutting down anyway
        except Exception:
            pass  # Ignore all errors during cleanup
        finally:
            _pool = None


# Register cleanup handler
atexit.register(_cleanup_pool)


def _quiet_reset(conn: "psycopg.Connection") -> None:
    """
    Reset handler for connection pool that silently rolls back dirty connections.
    
    Bug #49 fix: Prevents "rolling back returned connection" warning spam from 
    psycopg_pool when connections are returned in INTRANS state. This happens
    when code paths don't explicitly commit/rollback, which is common in 
    read-only operations and error paths.
    
    This is safe because:
    1. We're only rolling back uncommitted changes (no data loss)
    2. If the caller wanted to commit, they should have done so explicitly
    3. The warning provides no actionable information (we know some code paths
       don't commit, it's by design for read operations)
    """
    from psycopg.pq import TransactionStatus
    
    # Check if connection is in a transaction state (not IDLE)
    if conn.info.transaction_status != TransactionStatus.IDLE:
        # Connection is in a transaction - silently rollback
        try:
            conn.rollback()
        except Exception:
            pass  # Ignore rollback errors - connection may be broken


def _add_keepalive_params(url: str) -> str:
    """
    Add TCP keepalive parameters to a PostgreSQL connection URL.
    
    This prevents 'connection is closed' errors during long-running workflows
    by sending periodic keepalive probes.
    
    Keepalive settings:
    - keepalives=1: Enable TCP keepalives
    - keepalives_idle=60: Start keepalive probes after 60s idle
    - keepalives_interval=10: Send probes every 10s
    - keepalives_count=5: Consider connection dead after 5 failed probes
    """
    if "keepalives" not in url:
        separator = "&" if "?" in url else "?"
        keepalive_params = (
            f"{separator}keepalives=1"
            "&keepalives_idle=60"
            "&keepalives_interval=10"
            "&keepalives_count=5"
        )
        return url + keepalive_params
    return url


def get_pool() -> "ConnectionPool":
    """
    Get or create the Postgres connection pool.
    
    Uses DATABASE_URL from config.
    
    Note: Does NOT use dict_row factory - returns tuple rows for consistency
    with SQLite's Row factory (which supports both index and column name access).
    
    Bug #49 fix: Uses custom reset handler to suppress "rolling back" warnings.
    Connection keepalive: Adds TCP keepalive to prevent connection timeouts.
    """
    global _pool

    if not HAS_PSYCOPG:
        raise ImportError(
            "psycopg and psycopg_pool are required for Postgres support. "
            "Install with: pip install 'psycopg[binary]' psycopg_pool"
        )

    if _pool is None:
        settings = get_settings()
        # Add keepalive parameters to prevent connection timeouts
        db_url = _add_keepalive_params(settings.database.url)
        _pool = ConnectionPool(
            db_url,
            min_size=2,
            max_size=20,  # V1.1: Increased from 10 for headroom
            timeout=5.0,  # V1.1: Increased from 1.0 for reliability
            open=True,  # V1.2: Explicit open=True to fix psycopg_pool deprecation warning
            reset=_quiet_reset,  # Bug #49: Suppress rollback warnings
            # No row_factory - use default tuple rows for consistency with SQLite
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
    
    Bug #49 fix: Suppress spurious rollback warnings when connection is in autocommit.
    """
    import warnings
    pool = get_pool()
    with pool.connection() as conn:
        # Suppress psycopg's "executing rollback" warning for autocommit connections
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*executing rollback.*")
            yield conn


# =============================================================================
# DDL Definitions per Design Doc Appendix B
# =============================================================================

SPEC_SILVER_DDL = """
-- spec_silver schema: Bronze-to-Silver extracted API metadata
-- Per design doc Appendix B.2

CREATE SCHEMA IF NOT EXISTS spec_silver;
CREATE SCHEMA IF NOT EXISTS spec_bronze;

-- Enable pgvector extension for embeddings
CREATE EXTENSION IF NOT EXISTS vector;

-- source_systems: Known API providers
CREATE TABLE IF NOT EXISTS spec_silver.source_systems (
    id           BIGSERIAL PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    base_url     TEXT
);

-- raw_specs: Bronze layer - raw ingested spec content
-- Per V2 Implementation Plan Section 5.1
CREATE TABLE IF NOT EXISTS spec_bronze.raw_specs (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    uri              TEXT NOT NULL,
    raw_content      BYTEA NOT NULL,
    content_type     TEXT NOT NULL,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sha256           TEXT NOT NULL,
    UNIQUE(source_system_id, sha256)
);

CREATE INDEX IF NOT EXISTS idx_raw_specs_sha256 ON spec_bronze.raw_specs(sha256);

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

-- Extend file_specs with additional columns (idempotent)
ALTER TABLE spec_silver.file_specs ADD COLUMN IF NOT EXISTS 
    spec_document_id BIGINT REFERENCES spec_silver.spec_documents(id);
ALTER TABLE spec_silver.file_specs ADD COLUMN IF NOT EXISTS 
    line_terminator TEXT DEFAULT E'\\n';
ALTER TABLE spec_silver.file_specs ADD COLUMN IF NOT EXISTS 
    quote_char TEXT DEFAULT '"';
ALTER TABLE spec_silver.file_specs ADD COLUMN IF NOT EXISTS 
    escape_char TEXT;
ALTER TABLE spec_silver.file_specs ADD COLUMN IF NOT EXISTS 
    version TEXT;
ALTER TABLE spec_silver.file_specs ADD COLUMN IF NOT EXISTS 
    sample_uri TEXT;

-- file_fields: Fields/columns within a file spec
CREATE TABLE IF NOT EXISTS spec_silver.file_fields (
    id                    BIGSERIAL PRIMARY KEY,
    file_spec_id          BIGINT NOT NULL REFERENCES spec_silver.file_specs(id) ON DELETE CASCADE,
    name                  TEXT NOT NULL,
    field_type            TEXT NOT NULL,
    position              INT NOT NULL,
    start_position        INT,              -- For fixed-width: start byte (1-indexed)
    length                INT,              -- For fixed-width: field length
    format_mask           TEXT,             -- e.g., "YYYYMMDD", "###.##"
    nullable              BOOLEAN DEFAULT TRUE,
    default_value         TEXT,
    validation_regex      TEXT,
    description           TEXT,
    sample_values         JSONB DEFAULT '[]',
    inference_confidence  DOUBLE PRECISION DEFAULT 1.0,
    UNIQUE (file_spec_id, name)
);

-- record_layouts: For multi-record fixed-width files
CREATE TABLE IF NOT EXISTS spec_silver.record_layouts (
    id                BIGSERIAL PRIMARY KEY,
    file_spec_id      BIGINT NOT NULL REFERENCES spec_silver.file_specs(id) ON DELETE CASCADE,
    record_type       TEXT NOT NULL,
    identifier_field  TEXT,
    identifier_value  TEXT,
    record_length     INT,
    position          INT DEFAULT 0,        -- Order in file (0=any, 1=first, -1=last)
    min_occurrences   INT DEFAULT 0,
    max_occurrences   INT,
    description       TEXT,
    UNIQUE (file_spec_id, record_type)
);

-- file_validation_rules: Validation rules for file data
CREATE TABLE IF NOT EXISTS spec_silver.file_validation_rules (
    id            BIGSERIAL PRIMARY KEY,
    file_spec_id  BIGINT NOT NULL REFERENCES spec_silver.file_specs(id) ON DELETE CASCADE,
    field_name    TEXT,                     -- NULL for file-level rules
    rule_type     TEXT NOT NULL,            -- required, range, regex, lookup, cross_field, etc.
    rule_config   JSONB NOT NULL DEFAULT '{}',
    error_message TEXT,
    severity      TEXT DEFAULT 'error'      -- error, warning, info
);

-- file_field_mappings: Map file fields to entity fields
CREATE TABLE IF NOT EXISTS spec_silver.file_field_mappings (
    id                    BIGSERIAL PRIMARY KEY,
    file_field_id         BIGINT NOT NULL REFERENCES spec_silver.file_fields(id) ON DELETE CASCADE,
    entity_id             BIGINT REFERENCES spec_silver.entities(id) ON DELETE SET NULL,
    entity_field_name     TEXT NOT NULL,
    transform_expression  TEXT,             -- e.g., "UPPER(value)", "DATE(value, '%Y%m%d')"
    description           TEXT
);

-- Indexes for file-related tables
CREATE INDEX IF NOT EXISTS idx_file_fields_spec ON spec_silver.file_fields(file_spec_id);
CREATE INDEX IF NOT EXISTS idx_record_layouts_spec ON spec_silver.record_layouts(file_spec_id);
CREATE INDEX IF NOT EXISTS idx_file_validation_rules_spec ON spec_silver.file_validation_rules(file_spec_id);
CREATE INDEX IF NOT EXISTS idx_file_field_mappings_field ON spec_silver.file_field_mappings(file_field_id);
CREATE INDEX IF NOT EXISTS idx_file_field_mappings_entity ON spec_silver.file_field_mappings(entity_id);

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
-- V1.1: task_id is nullable to allow recording runs even when task persistence fails
CREATE TABLE IF NOT EXISTS integration_gold.run_status (
    run_id           TEXT PRIMARY KEY,
    task_id          BIGINT NULL REFERENCES integration_gold.integration_tasks(id) ON DELETE SET NULL,
    status           TEXT NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    error_summary    TEXT,
    langsmith_run_id TEXT
);

-- run_checkpoints: Workflow state checkpoints for recovery
-- Per V2 Implementation Plan Section 3.5
CREATE TABLE IF NOT EXISTS integration_gold.run_checkpoints (
    id         SERIAL PRIMARY KEY,
    run_id     TEXT NOT NULL REFERENCES integration_gold.run_status(run_id) ON DELETE CASCADE,
    node_name  TEXT NOT NULL,
    state_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(run_id, node_name)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_run_id ON integration_gold.run_checkpoints(run_id);

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

# =============================================================================
# Knowledge Graph (KG) Schema DDL
# Per design doc: Graph-first retrieval with embeddings for ranking
# =============================================================================

KG_DDL = """
-- kg schema: Knowledge Graph for workflow templates and integration patterns
-- Used by align_task_with_kg for GraphRAG retrieval

CREATE SCHEMA IF NOT EXISTS kg;

-- provider_scoring_config: Provider-specific scoring weights for search
-- Per V2 Implementation Plan Section 5.1
CREATE TABLE IF NOT EXISTS kg.provider_scoring_config (
    id                  SERIAL PRIMARY KEY,
    provider_code       TEXT NOT NULL UNIQUE,
    graph_weight        REAL NOT NULL DEFAULT 0.4,
    embedding_weight    REAL NOT NULL DEFAULT 0.4,
    exact_match_weight  REAL NOT NULL DEFAULT 0.2,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- kg_nodes: Core KG nodes representing entities, tasks, endpoints, templates
-- Types: 'provider', 'entity', 'endpoint', 'workflow_template', 'task'
CREATE TABLE IF NOT EXISTS kg.nodes (
    id               BIGSERIAL PRIMARY KEY,
    node_type        TEXT NOT NULL,  -- 'provider', 'entity', 'endpoint', 'workflow_template', 'task'
    provider_code    TEXT,           -- Provider this node belongs to (nullable for cross-provider nodes)
    key              TEXT NOT NULL,  -- Unique key like "stripe.create_checkout_session"
    name             TEXT NOT NULL,
    description      TEXT,
    properties       JSONB NOT NULL DEFAULT '{}',  -- Flexible properties (e.g., steps for templates)
    embedding        VECTOR(1536),   -- Optional embedding for semantic search
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Governance fields
    confidence_score DOUBLE PRECISION DEFAULT 1.0,  -- How reliable is this node (0-1)
    usage_count      INT DEFAULT 0,                 -- How often this node was used
    last_used_at     TIMESTAMPTZ,
    source_run_id    TEXT,                          -- Which run created/updated this node
    UNIQUE (node_type, key)
);

-- kg_edges: Relationships between KG nodes
-- Relation types: 'uses_endpoint', 'produces_entity', 'consumes_entity', 
--                 'similar_to', 'composed_of', 'precedes'
CREATE TABLE IF NOT EXISTS kg.edges (
    id             BIGSERIAL PRIMARY KEY,
    src_node_id    BIGINT NOT NULL REFERENCES kg.nodes(id) ON DELETE CASCADE,
    dst_node_id    BIGINT NOT NULL REFERENCES kg.nodes(id) ON DELETE CASCADE,
    relation_type  TEXT NOT NULL,  -- e.g., 'uses_endpoint', 'produces_entity'
    weight         DOUBLE PRECISION DEFAULT 1.0,  -- Edge weight for graph traversal
    properties     JSONB NOT NULL DEFAULT '{}',   -- Additional edge metadata
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_run_id  TEXT,
    UNIQUE (src_node_id, dst_node_id, relation_type)
);

-- Create indexes for efficient GraphRAG queries
CREATE INDEX IF NOT EXISTS kg_nodes_type_idx ON kg.nodes(node_type);
CREATE INDEX IF NOT EXISTS kg_nodes_provider_idx ON kg.nodes(provider_code);
CREATE INDEX IF NOT EXISTS kg_nodes_key_idx ON kg.nodes(key);
CREATE INDEX IF NOT EXISTS kg_edges_src_idx ON kg.edges(src_node_id);
CREATE INDEX IF NOT EXISTS kg_edges_dst_idx ON kg.edges(dst_node_id);
CREATE INDEX IF NOT EXISTS kg_edges_relation_idx ON kg.edges(relation_type);

-- Vector similarity index for semantic search on node embeddings
CREATE INDEX IF NOT EXISTS kg_nodes_embedding_idx 
ON kg.nodes 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- kg_workflow_steps: Detailed workflow step definitions for templates
-- Links workflow template nodes to their constituent steps
CREATE TABLE IF NOT EXISTS kg.workflow_steps (
    id                   BIGSERIAL PRIMARY KEY,
    template_node_id     BIGINT NOT NULL REFERENCES kg.nodes(id) ON DELETE CASCADE,
    step_key             TEXT NOT NULL,
    step_type            TEXT NOT NULL,  -- 'start', 'validation', 'api_call', 'transform', 'end'
    position             INT NOT NULL,
    label                TEXT,
    description          TEXT,
    config               JSONB NOT NULL DEFAULT '{}',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (template_node_id, step_key)
);

-- kg_step_bindings: Bindings from workflow steps to endpoints
CREATE TABLE IF NOT EXISTS kg.step_bindings (
    id               BIGSERIAL PRIMARY KEY,
    step_id          BIGINT NOT NULL REFERENCES kg.workflow_steps(id) ON DELETE CASCADE,
    endpoint_node_id BIGINT REFERENCES kg.nodes(id) ON DELETE SET NULL,  -- Links to endpoint in KG
    endpoint_path    TEXT,          -- Fallback if endpoint node not in KG
    endpoint_method  TEXT,
    request_mapping  JSONB NOT NULL DEFAULT '{}',
    response_mapping JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (step_id, endpoint_node_id)
);

CREATE INDEX IF NOT EXISTS kg_workflow_steps_template_idx ON kg.workflow_steps(template_node_id);
CREATE INDEX IF NOT EXISTS kg_step_bindings_step_idx ON kg.step_bindings(step_id);

-- =============================================================================
-- Feedback and Learning Tables
-- Per docs/decisions/FEEDBACK_LEARNING_IMPLEMENTATION.md
-- =============================================================================

-- kg.feedback_records: Stores feedback synced from LangSmith and implicit signals
CREATE TABLE IF NOT EXISTS kg.feedback_records (
    id                    BIGSERIAL PRIMARY KEY,
    run_id                TEXT NOT NULL,
    template_key          TEXT,                        -- kg.nodes key for the template used
    pattern_key           TEXT,                        -- kg.nodes key for the pattern used
    feedback_type         TEXT NOT NULL,               -- 'thumbs', 'score', 'auto_compile', 'auto_test', 'auto_lint'
    score                 DOUBLE PRECISION NOT NULL,   -- Normalized 0-1
    comment               TEXT,
    source                TEXT NOT NULL DEFAULT 'langsmith',  -- 'langsmith', 'cli', 'auto', 'api'
    langsmith_feedback_id TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Unique constraint: one feedback per run + langsmith_feedback_id combo
CREATE UNIQUE INDEX IF NOT EXISTS kg_feedback_run_ls_idx 
ON kg.feedback_records(run_id, langsmith_feedback_id) 
WHERE langsmith_feedback_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS kg_feedback_run_idx ON kg.feedback_records(run_id);
CREATE INDEX IF NOT EXISTS kg_feedback_template_idx ON kg.feedback_records(template_key);
CREATE INDEX IF NOT EXISTS kg_feedback_pattern_idx ON kg.feedback_records(pattern_key);

-- kg.confidence_history: Track confidence changes over time for analysis
CREATE TABLE IF NOT EXISTS kg.confidence_history (
    id               BIGSERIAL PRIMARY KEY,
    node_key         TEXT NOT NULL,
    old_confidence   DOUBLE PRECISION,
    new_confidence   DOUBLE PRECISION NOT NULL,
    feedback_count   INT NOT NULL DEFAULT 0,
    reason           TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS kg_confidence_history_node_idx ON kg.confidence_history(node_key);
CREATE INDEX IF NOT EXISTS kg_confidence_history_time_idx ON kg.confidence_history(created_at);

-- =============================================================================
-- Dynamic Pattern Learning Tables (PL-001)
-- Per docs/PATTERN_LEARNING_DESIGN.md
-- =============================================================================

-- kg.run_events: Event log for pattern discovery
CREATE TABLE IF NOT EXISTS kg.run_events (
    id               BIGSERIAL PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES integration_gold.run_status(run_id) ON DELETE CASCADE,
    event_type       TEXT NOT NULL,              -- 'step_start', 'step_complete', 'step_error'
    activity         TEXT NOT NULL,              -- step_key (e.g., "understand_task", "call_api")
    activity_type    TEXT,                       -- step_type (e.g., "api_call", "validation")
    position         INT,
    timestamp        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider_code    TEXT,
    endpoint_path    TEXT,                       -- for api_call steps
    endpoint_method  TEXT,                       -- for api_call steps
    attributes       JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS kg_run_events_run_idx ON kg.run_events(run_id);
CREATE INDEX IF NOT EXISTS kg_run_events_activity_idx ON kg.run_events(activity);
CREATE INDEX IF NOT EXISTS kg_run_events_provider_idx ON kg.run_events(provider_code);

-- kg.pattern_candidates: Staging table for discovered patterns before promotion
CREATE TABLE IF NOT EXISTS kg.pattern_candidates (
    id                   BIGSERIAL PRIMARY KEY,
    candidate_key        TEXT NOT NULL UNIQUE,
    name                 TEXT NOT NULL,
    description          TEXT,
    canonical_sequence   JSONB NOT NULL,             -- Array of step signatures
    support_count        INT NOT NULL DEFAULT 1,     -- How many runs match
    first_seen_run_id    TEXT,
    last_seen_run_id     TEXT,
    status               TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'promoted', 'rejected', 'merged'
    promoted_pattern_key TEXT,                       -- If promoted, the kg.nodes key
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS kg_pattern_candidates_status_idx ON kg.pattern_candidates(status);

-- kg.pattern_matches: Record match decisions for explainability
CREATE TABLE IF NOT EXISTS kg.pattern_matches (
    id               BIGSERIAL PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES integration_gold.run_status(run_id) ON DELETE CASCADE,
    pattern_key      TEXT NOT NULL,              -- kg.nodes key
    match_score      DOUBLE PRECISION NOT NULL,
    match_method     TEXT NOT NULL,              -- 'exact', 'semantic', 'rule'
    explanation      JSONB,                      -- Match details for debugging
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS kg_pattern_matches_run_idx ON kg.pattern_matches(run_id);
CREATE INDEX IF NOT EXISTS kg_pattern_matches_pattern_idx ON kg.pattern_matches(pattern_key);

-- Add origin column to kg.nodes (migration-safe using DO block)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'kg' AND table_name = 'nodes' AND column_name = 'origin'
    ) THEN
        ALTER TABLE kg.nodes ADD COLUMN origin TEXT DEFAULT 'seeded';
    END IF;
END $$;

-- Backfill NULL origin values to 'seeded' (defensive migration)
UPDATE kg.nodes SET origin = 'seeded' WHERE origin IS NULL;

-- Additional composite indexes for pattern learning queries
CREATE INDEX IF NOT EXISTS kg_run_events_run_position_idx ON kg.run_events(run_id, position);
CREATE INDEX IF NOT EXISTS kg_pattern_candidates_status_support_idx ON kg.pattern_candidates(status, support_count DESC);
CREATE INDEX IF NOT EXISTS kg_nodes_type_origin_idx ON kg.nodes(node_type, origin) WHERE node_type = 'pattern';

-- Dedupe constraint: prevent duplicate events in same run (run_id, position, activity, event_type)
CREATE UNIQUE INDEX IF NOT EXISTS kg_run_events_dedup_idx ON kg.run_events(run_id, position, activity, event_type);
"""


def _apply_full_schema(conn) -> None:
    """Run all DDL statements for Postgres schemas using an open connection."""
    with conn.cursor() as cur:
        cur.execute(SPEC_SILVER_DDL)
        cur.execute(INTEGRATION_GOLD_DDL)
        cur.execute(REPO_META_DDL)
        cur.execute(KG_DDL)
    conn.commit()


def init_all_schemas(conn=None) -> None:
    """
    Initialize all Postgres schemas and tables.

    If a connection is provided, uses it directly; otherwise manages its own.
    Creates spec_silver, integration_gold, repo_meta, and kg schemas with all tables.
    Also seeds STANDARD_PATTERNS into kg.nodes (KG-002 fix).
    Safe to call multiple times (uses CREATE IF NOT EXISTS).
    """
    if conn is None:
        with get_connection() as managed_conn:
            _apply_full_schema(managed_conn)
    else:
        _apply_full_schema(conn)

    logger.info("Postgres schema initialized successfully (including kg schema)")
    
    # KG-002 Fix: Seed standard patterns into kg.nodes
    # This enables cross-provider pattern matching from the start
    try:
        from integration_coworker.persistence.seed_kg import seed_standard_patterns
        added, skipped = seed_standard_patterns()
        if added > 0:
            logger.info(f"Seeded {added} standard patterns into kg.nodes")
    except Exception as e:
        # Don't fail init if pattern seeding fails - it's not critical
        logger.warning(f"Failed to seed standard patterns: {e}")


def init_postgres_schema() -> None:
    """Backward-compatible alias for init_all_schemas."""
    init_all_schemas()


def drop_all_schemas() -> None:
    """
    Drop all schemas (for testing only).
    
    WARNING: This will delete all data!
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS kg CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS repo_meta CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS integration_gold CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS spec_silver CASCADE")
        conn.commit()

    logger.warning("All schemas dropped (including kg)")


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
