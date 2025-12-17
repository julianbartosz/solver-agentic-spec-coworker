# ADR-0006: Medallion Data Architecture

## Status

**Accepted** — Implemented in v1

## Date

2025-12-01

## Context

The Integration Coworker transforms API specifications into generated integration code. This process involves two fundamentally different data domains:

1. **API Specification Data** — Parsed from OpenAPI, AsyncAPI, HTML, PDF docs
2. **Integration Artifacts** — Generated workflows, policies, code, tests

These domains have different:
- **Ownership** — Spec data comes from external sources; artifacts are system-generated
- **Lifecycles** — Specs change when providers update; artifacts change when tasks are re-run
- **Query patterns** — Specs are read-heavy for RAG; artifacts are write-then-read
- **Foreign key relationships** — Artifacts reference spec entities (endpoints, schemas)

### The Problem

Without clear schema separation:
1. **Coupling risk** — Changes to spec parsing could break artifact generation
2. **Query confusion** — JOINs between unrelated concepts
3. **Ownership ambiguity** — Which tables are "source of truth" for what?
4. **Evolution constraints** — Hard to version or migrate one domain independently

### Data Flow Context

```
[OpenAPI/HTML/PDF Specs]
        ↓
   ingest_spec → detect_and_parse_spec → build_silver_api_model → embed_spec_chunks
        ↓                                                              ↓
        └────────────────── SILVER LAYER ─────────────────────────────┘
                                  ↓
   understand_task → align_task_with_kg → plan_integration_flow → generate_code_and_tests
        ↓                                                              ↓
        └────────────────── GOLD LAYER ───────────────────────────────┘
```

## Decision

**Adopt a Medallion Architecture with two primary schemas:**

| Schema | Layer | Purpose | Postgres Prefix |
|--------|-------|---------|-----------------|
| `spec_silver` | Silver | Normalized API specification data | `spec_silver.*` |
| `integration_gold` | Gold | Integration task artifacts | `integration_gold.*` |

Additional supporting schemas:
- `kg` — Knowledge Graph for GraphRAG (workflow templates, patterns)
- `repo_meta` — Repository integration tracking

### Schema Boundary Rules

1. **Silver tables never reference Gold tables** — One-way dependency
2. **Gold tables reference Silver tables via foreign keys** — e.g., `endpoint_id → spec_silver.endpoints`
3. **Cross-schema JOINs are allowed** — Gold queries may JOIN Silver for denormalization
4. **Each schema has independent DDL** — Can be versioned/migrated separately

### Silver Schema (`spec_silver`)

Normalized view of all supported API sources.

#### Core Tables

| Table | Purpose | Unique Key |
|-------|---------|------------|
| `source_systems` | API providers (Stripe, Twilio) | `code` |
| `spec_documents` | Raw spec files with SHA256 | `(source_system_id, sha256)` |
| `spec_sections` | Logical sections for RAG | `(spec_document_id, section_type, path)` |
| `schemas` | Data schemas (request/response bodies) | `(source_system_id, name)` |
| `fields` | Schema fields with JSON paths | `(schema_id, json_path)` |
| `entities` | Domain entities (Customer, Payment) | `(source_system_id, name)` |
| `entity_relationships` | Entity associations | `(source_system_id, from_entity_id, to_entity_id, relationship_type)` |
| `events` | Webhooks and events | `(source_system_id, name)` |
| `endpoints` | API endpoints | `(source_system_id, spec_document_id, path, method)` |
| `endpoint_parameters` | Path/query/header params | `(endpoint_id, name, location)` |
| `spec_chunks` | Chunked text with embeddings | `(spec_document_id, chunk_index)` |

#### Extension Tables (Non-HTTP Sources)

| Table | Purpose | Unique Key |
|-------|---------|------------|
| `file_specs` | CSV/EDI schema metadata | `(source_system_id, name)` |
| `message_specs` | Event/message interfaces (Kafka, SQS) | `(source_system_id, name)` |

### Gold Schema (`integration_gold`)

Integration task artifacts and run tracking.

#### Core Tables

| Table | Purpose | Unique Key |
|-------|---------|------------|
| `integration_tasks` | Task definitions | `(provider_code, task_slug)` |
| `workflow_templates` | Reusable workflow patterns | `(source_system_id, code)` |
| `integration_flow_nodes` | Workflow steps | `(task_id, node_key)` |
| `integration_flow_edges` | Workflow connections | `(task_id, from_node_key, to_node_key)` |
| `endpoint_bindings` | Node-to-endpoint mappings | `(task_id, flow_node_key, endpoint_id)` |
| `policies` | Auth, retry, rate limit configs | `(task_id, policy_type, scope, scope_ref)` |
| `code_artifacts` | Generated code files | `(task_id, rel_path, artifact_type)` |
| `run_status` | Run execution tracking | `run_id` |
| `rag_eval_metrics` | RAG quality metrics | `(run_id, node_name, metric_scope)` |

### Cross-Schema Foreign Keys

Gold → Silver references (one-way):

```sql
-- integration_flow_nodes references Silver endpoints and entities
endpoint_id BIGINT REFERENCES spec_silver.endpoints(id)
entity_id   BIGINT REFERENCES spec_silver.entities(id)

-- endpoint_bindings references Silver endpoints
endpoint_id BIGINT NOT NULL REFERENCES spec_silver.endpoints(id)

-- integration_tasks references Silver source_systems and spec_documents
source_system_id        BIGINT REFERENCES spec_silver.source_systems(id)
target_spec_document_id BIGINT REFERENCES spec_silver.spec_documents(id)
```

### Domain Model Mapping

In-memory dataclasses mirror the schema structure:

```python
# Silver Layer Models (src/integration_coworker/domain/models.py)
@dataclass
class Endpoint:
    id: Optional[int]
    source_system_id: Optional[int]
    spec_document_id: Optional[int]
    path: str
    method: str
    # ...

# Gold Layer Models
@dataclass
class IntegrationFlowNode:
    id: Optional[int]
    task_id: Optional[int]
    node_key: str
    node_type: str
    endpoint_id: Optional[int]  # FK to Silver
    # ...
```

## Alternatives Considered

### 1. Single Schema (Flat Structure)

All tables in one schema without logical separation.

| Aspect | Assessment |
|--------|------------|
| Pros | Simpler DDL, no cross-schema considerations |
| Cons | No ownership clarity, coupling risk, harder to evolve |
| Verdict | Rejected — doesn't scale with complexity |

### 2. Full Bronze/Silver/Gold (Three Layers)

Add explicit Bronze layer for raw, unprocessed data.

```
Bronze: Raw spec bytes, unparsed
Silver: Parsed, normalized
Gold: Generated artifacts
```

| Aspect | Assessment |
|--------|------------|
| Pros | Pure data lineage, full replay capability |
| Cons | Bronze adds overhead with minimal value in v1 |
| Verdict | Deferred — may add Bronze for audit in v2 |

### 3. Microservice-Style Schema Per Domain

Separate databases for specs, tasks, code artifacts, runs.

| Aspect | Assessment |
|--------|------------|
| Pros | Full isolation, independent scaling |
| Cons | Massive complexity, distributed transactions |
| Verdict | Rejected — overkill for single-process system |

### 4. Document Store (MongoDB-style)

Store Silver and Gold as nested JSON documents.

| Aspect | Assessment |
|--------|------------|
| Pros | Schema flexibility, nested queries |
| Cons | Loses relational integrity, harder RAG queries |
| Verdict | Rejected — need foreign keys for correctness |

## Comparison Matrix

| Criterion | Single Schema | Two-Layer (current) | Three-Layer | Microservice |
|-----------|--------------|---------------------|-------------|--------------|
| Implementation complexity | **Low** | Low | Medium | High |
| Ownership clarity | Poor | **Good** | **Excellent** | **Excellent** |
| Evolution flexibility | Poor | **Good** | **Excellent** | **Excellent** |
| Query simplicity | **Good** | **Good** | Medium | Poor |
| Foreign key integrity | **Good** | **Good** | **Good** | Poor |
| Fit for v1 | Poor | **Excellent** | Good | Overkill |

## Consequences

### Positive

1. **Clear ownership** — "Silver = spec data, Gold = artifacts" is easy to communicate
2. **Independent evolution** — Can add Silver tables without touching Gold
3. **Foreign key integrity** — Gold references Silver; never the reverse
4. **Query optimization** — Can index Silver for RAG, Gold for task lookups
5. **SQLite compatibility** — Schemas translate to table prefixes in SQLite fallback

### Negative

1. **Cross-schema JOINs** — Slightly more verbose queries
2. **Schema prefix overhead** — Must use `spec_silver.endpoints` not just `endpoints`
3. **Two DDL files** — Must maintain schema consistency across both

### Neutral

1. **Postgres vs SQLite** — Postgres uses `CREATE SCHEMA`; SQLite uses flat tables (handled by `get_engine_type()`)
2. **pgvector in Silver** — `spec_chunks.embedding` uses `VECTOR(1536)` in Postgres, `TEXT` (JSON) in SQLite

## Schema Interaction Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              spec_silver                                    │
│                                                                             │
│  ┌──────────────┐    ┌────────────────┐    ┌─────────────┐                 │
│  │source_systems│◄───│ spec_documents │◄───│spec_sections│                 │
│  └──────┬───────┘    └───────┬────────┘    └─────────────┘                 │
│         │                    │                                              │
│         ▼                    ▼                                              │
│  ┌──────────┐         ┌──────────┐         ┌────────────┐                  │
│  │ schemas  │◄────────│endpoints │─────────│spec_chunks │                  │
│  └────┬─────┘         └────┬─────┘         └────────────┘                  │
│       │                    │                                                │
│       ▼                    ▼                                                │
│  ┌────────┐         ┌──────────────────┐                                   │
│  │ fields │         │endpoint_parameters│                                   │
│  └────────┘         └──────────────────┘                                   │
│                                                                             │
│  ┌──────────┐    ┌─────────────────────┐    ┌────────┐                     │
│  │ entities │◄───│entity_relationships │    │ events │                     │
│  └──────────┘    └─────────────────────┘    └────────┘                     │
└─────────────────────────────────────────────────────────────────────────────┘
         │                    │
         │    FOREIGN KEYS    │
         ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            integration_gold                                  │
│                                                                             │
│  ┌───────────────────┐                                                      │
│  │ integration_tasks │◄─────────────────────────────────────┐              │
│  └─────────┬─────────┘                                      │              │
│            │                                                 │              │
│            ▼                                                 ▼              │
│  ┌─────────────────────┐    ┌─────────────────────┐   ┌──────────┐         │
│  │integration_flow_nodes│───│integration_flow_edges│   │ policies │         │
│  └──────────┬──────────┘    └─────────────────────┘   └──────────┘         │
│             │                                                               │
│             ▼                                                               │
│  ┌──────────────────┐         ┌────────────────┐      ┌───────────┐        │
│  │ endpoint_bindings │         │ code_artifacts │      │run_status │        │
│  └──────────────────┘         └────────────────┘      └─────┬─────┘        │
│                                                              │              │
│                                                              ▼              │
│                                                    ┌─────────────────┐     │
│                                                    │rag_eval_metrics │     │
│                                                    └─────────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Implementation References

### Schema DDL

- Design doc `Appendix B.2` — Complete Silver schema DDL
- Design doc `Appendix B.3` — Complete Gold schema DDL
- `src/integration_coworker/persistence/db.py` — SQLite schema initialization

### Domain Models

- `src/integration_coworker/domain/models.py` — Silver and Gold dataclasses

### Checkpoint Nodes

- `src/integration_coworker/graph/nodes/persist_silver_checkpoint.py` — Writes Silver tables
- `src/integration_coworker/graph/nodes/persist_gold_checkpoint.py` — Writes Gold tables

### SQL Helpers

- `src/integration_coworker/persistence/sql_helpers.py` — Schema-aware `upsert_ignore()`, `select_by_columns()`

## Design Doc References

- **Section 3.3**: "Medallion Data Architecture — Silver and Gold schemas"
- **Appendix B.2**: Complete Silver schema DDL
- **Appendix B.3**: Complete Gold schema DDL
- **Appendix B.4**: Repository integration metadata schema

## Related ADRs

- **ADR-0001**: Initial Architecture — Established medallion model
- **ADR-0007**: Single DB Writer Pattern — How data flows to these schemas

## v1 Constraints

The following limitations apply to the current medallion implementation.

### In-Memory Embeddings

Embeddings are computed per-run but not persisted to pgvector.

```
Currently stores embeddings in memory; future phases will write to pgvector
for cross-run retrieval.
```

| Constraint | v1 Behavior | v2 Target |
|------------|-------------|-----------|
| Embedding persistence | Not written to DB | Persist `spec_chunks.embedding` to pgvector |
| Cross-run retrieval | Cold start each run | Semantic search across historical specs |
| Vector index | None | `CREATE INDEX USING hnsw (embedding vector_cosine_ops)` |

**Impact**: Each run recomputes embeddings for spec chunks. This adds latency (~2-5 seconds for typical specs) and cost (~$0.01-0.02 per run).

### SQLite Fallback for Tests

Unit tests use SQLite to avoid PostgreSQL dependency.

```python
- FALLBACK: SQLite for tests (USE_SQLITE=true)
# Postgres is configured - must succeed or fail, no silent fallback
```

| SQLite Limitation | Impact on Tests |
|-------------------|-----------------|
| No pgvector | Embeddings stored as JSON text |
| No `JSONB` operators | Some queries simplified |
| Different FK behavior | Cascade deletes may differ |
| No schemas | Tables prefixed instead (`spec_silver_endpoints`) |

**v2 Target**: Keep SQLite for unit tests. Require PostgreSQL for integration tests via CI matrix.

### Deferred Bronze Layer

Raw spec bytes are not persisted.

| What's Missing | Consequence |
|----------------|-------------|
| Original spec files | Cannot replay parsing with improved logic |
| Fetch timestamps | No audit trail of spec changes |
| Content hashes | Cannot detect spec drift |

**v2 Target**: Add `spec_bronze.raw_specs` table when audit requirements justify the storage overhead.

### Source Refs Reserved

Multi-source ingestion is stubbed.

```python
source_refs=[],  # v1: reserved for future use
```

| Source Type | v1 Status | v2 Target |
|-------------|-----------|-----------|
| OpenAPI (HTTP) | Supported | Supported |
| AsyncAPI (events) | Not supported | Add `message_specs` table |
| CSV/EDI schemas | Not supported | Add `file_specs` table |
| Database schemas | Not supported | Add `db_specs` table |

---

## Future Considerations

### Adding Bronze Layer

If audit requirements grow, add Bronze for raw spec bytes:

```sql
CREATE SCHEMA IF NOT EXISTS spec_bronze;

CREATE TABLE spec_bronze.raw_specs (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL,
    uri              TEXT NOT NULL,
    raw_content      BYTEA NOT NULL,  -- Original bytes
    content_type     TEXT NOT NULL,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sha256           TEXT NOT NULL
);
```

This enables:
- Full replay from raw sources
- Audit trail of spec changes
- Re-parsing with improved logic

### Schema Versioning

When breaking schema changes are needed:
1. Create `spec_silver_v2` with new structure
2. Migration script transforms `spec_silver` → `spec_silver_v2`
3. Update code to use v2
4. Drop v1 after verification

## Notes

The two-layer medallion architecture was chosen as the minimal viable structure that provides clear ownership without over-engineering. The Bronze layer was intentionally omitted from v1 to reduce complexity, but the architecture is designed to accommodate it when audit requirements justify the overhead.
