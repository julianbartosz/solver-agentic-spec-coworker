-- Migration 001: Baseline Schema V1
-- Applied: (auto-recorded on execution)
-- Description: Initial schema creation for all schemas:
--   - spec_bronze: Raw ingested specs
--   - spec_silver: Extracted API metadata  
--   - integration_gold: Workflow definitions and artifacts
--   - repo_meta: Repository integration tracking
--   - kg: Knowledge Graph for templates and patterns
--
-- This is the "single source of truth" migration extracted from postgres.py DDL strings.
-- All schema changes going forward should be in numbered migration files.

-- =============================================================================
-- spec_silver schema: Bronze-to-Silver extracted API metadata
-- Per design doc Appendix B.2
-- =============================================================================

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
    repo_root        TEXT NOT NULL DEFAULT '__legacy__',
    UNIQUE (source_system_id, sha256),
    UNIQUE (repo_root, uri)
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
    line_terminator TEXT DEFAULT E'\n';
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

-- =============================================================================
-- integration_gold schema: Planned integration workflows and artifacts
-- Per design doc Appendix B.3
-- =============================================================================

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

-- run_artifacts: External storage index for large checkpoint fields
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

-- Indexes for artifact queries
CREATE INDEX IF NOT EXISTS idx_run_artifacts_run_id ON integration_gold.run_artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_sha256 ON integration_gold.run_artifacts(sha256);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_created ON integration_gold.run_artifacts(created_at);

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

-- =============================================================================
-- repo_meta schema: Repository integration tracking
-- Per design doc Appendix B.4
-- =============================================================================

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

-- =============================================================================
-- kg schema: Knowledge Graph for workflow templates and integration patterns
-- Used by align_task_with_kg for GraphRAG retrieval
-- =============================================================================

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
    origin           TEXT DEFAULT 'seeded',         -- 'seeded', 'learned', 'manual'
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

-- Additional composite indexes for pattern learning queries
CREATE INDEX IF NOT EXISTS kg_run_events_run_position_idx ON kg.run_events(run_id, position);
CREATE INDEX IF NOT EXISTS kg_pattern_candidates_status_support_idx ON kg.pattern_candidates(status, support_count DESC);
CREATE INDEX IF NOT EXISTS kg_nodes_type_origin_idx ON kg.nodes(node_type, origin) WHERE node_type = 'pattern';

-- Dedupe constraint: prevent duplicate events in same run (run_id, position, activity, event_type)
CREATE UNIQUE INDEX IF NOT EXISTS kg_run_events_dedup_idx ON kg.run_events(run_id, position, activity, event_type);
