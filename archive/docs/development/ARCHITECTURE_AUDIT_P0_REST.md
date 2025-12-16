# Architecture Audit: Remaining P0 Questions

> **Date**: December 10, 2025  
> **Scope**: All P0 questions from ARCHITECTURE_AUDIT_QUESTIONS.md NOT covered in ARCHITECTURE_AUDIT_RUNTIME_DATA.md  
> **Status**: Audit findings ready for review before doc edits

---

## Coverage Summary

### Already Covered in ARCHITECTURE_AUDIT_RUNTIME_DATA.md

| View | Questions Covered | Reference |
|------|-------------------|-----------|
| Runtime / Workflow | Q1, Q2, Q3, Q4, Q5 | (Runtime Q1-Q5) |
| Data / State | Q1, Q2, Q3, Q4 | (Data Q1-Q4) |
| Doc-Drift Detection | Q1 (Node count) | Partial |

### Answered in This Document

| View | Questions Covered |
|------|-------------------|
| Logical / Module | Q1, Q2 |
| Persistence / Storage | Q1, Q2, Q3 |
| LLM / External Services | Q1, Q2, Q3, Q4 |
| Operational / CLI-API | Q1, Q2, Q3, Q4 |
| Configuration / Environment | Q1, Q2, Q3 |
| Doc-Drift Detection | Q2, Q3, Q4 (remaining) |
| Cross-Cutting Concerns | Q9, Q10 |

---

## Logical / Module View – P0 Audit

### Q1: What are the top-level packages under `src/integration_coworker/` and what is the single responsibility of each?

**Answer**: The package has 12 top-level subdirectories:

| Package | Single Responsibility |
|---------|----------------------|
| `api/` | Public Python API entrypoint (`design_and_generate_integration()`) and types |
| `cli.py` | CLI entrypoint (Typer-based), command parsing, demo mode |
| `codegen/` | Code generation utilities, templates, security validation |
| `config/` | Configuration loading (Settings, archetypes), models.yaml, env vars |
| `domain/` | Domain models (Silver/Gold dataclasses, enums) |
| `feedback/` | Feedback hooks, LangSmith sync, confidence scoring |
| `graph/` | LangGraph workflow: `runtime.py`, `state.py`, `nodes/` |
| `kg/` | Knowledge Graph traversal and GraphRAG integration |
| `llm/` | LLM client abstraction, caching, safety, sanitization |
| `parsers/` | Spec format parsers (OpenAPI, HTML, PDF) |
| `persistence/` | Database abstraction (Postgres, SQLite), streaming, checkpoints |
| `repo/` | Repository integration (profile detection, file patching) |
| `retrieval/` | Semantic search, embedding utilities |
| `runtime/` | Async helpers, execution utilities |
| `spec/` | Spec loading and normalization |
| `ui/` | Streamlit web UI (optional) |

**Evidence**: `src/integration_coworker/` directory listing

**Classification**: **Docs incomplete** — ARCHITECTURE.md lists component tables but doesn't give single-responsibility descriptions for all packages.

---

### Q2: How do `api/`, `graph/`, `domain/`, `persistence/`, `llm/`, `config/`, and `repo/` relate to each other in terms of import dependencies?

**Answer**: Import dependency flow (simplified):

```
cli.py
   └── api/entrypoint.py
          └── graph/runtime.py
                 ├── graph/nodes/*.py
                 │      ├── domain/models.py (Silver/Gold types)
                 │      ├── persistence/db.py (writes)
                 │      ├── llm/client.py (LLM calls)
                 │      ├── config/__init__.py (archetypes)
                 │      └── repo/helpers.py (repo integration)
                 └── graph/state.py
                        └── domain/models.py
```

**Key relationships**:
- `api/` imports `graph/` (workflow execution)
- `graph/nodes/` imports `domain/`, `persistence/`, `llm/`, `config/`, `repo/`
- `config/` is a leaf dependency (imported by many, imports few)
- `llm/` imports `config/` for archetypes
- `persistence/` imports `config/` for database settings

**Evidence**: 
- `api/entrypoint.py`: lines 10-13 (imports `graph.runtime`, `graph.state`)
- `graph/runtime.py`: lines 20-40 (imports nodes)
- Node files: import statements

**Classification**: **Docs incomplete** — No import dependency diagram in ARCHITECTURE.md

---

## Persistence / Storage View – P0 Audit

### Q1: What tables exist in the database schema?

**Answer**: From `persistence/db.py` `_init_sqlite_schema()` (lines 315-575):

**spec_silver schema** (Bronze/Silver tables):
- `source_systems` — API providers (stripe, github, etc.)
- `raw_specs` — Bronze layer: raw spec content
- `spec_documents` — Parsed spec documents
- `spec_sections` — Logical sections within specs
- `schemas` — JSON schemas
- `fields` — Schema fields
- `entities` — Business objects
- `entity_relationships` — Entity-to-entity relationships
- `events` — Async events
- `endpoints` — API operations
- `endpoint_parameters` — Path/query/header parameters
- `spec_chunks` — Text chunks with embeddings (for RAG)

**integration_gold schema**:
- `integration_tasks` — Task definitions
- `workflow_templates` — Reusable workflow templates
- `integration_flow_nodes` — Workflow nodes
- `integration_flow_edges` — Workflow edges
- `endpoint_bindings` — Node→endpoint mappings
- `policies` — Auth/retry/rate-limit policies
- `code_artifacts` — Generated code files
- `run_status` — Run tracking
- `run_checkpoints` — Node-level checkpoints
- `rag_eval_metrics` — RAG evaluation metrics

**repo_meta schema**:
- `repo_integrations` — Repo integration records
- `repo_files` — Tracked files in repos

**kg schema** (Knowledge Graph):
- `provider_scoring_config` — Per-provider scoring weights
- `kg_nodes` — Graph nodes
- `kg_edges` — Graph edges
- `kg_workflow_steps` — Workflow step definitions
- `kg_step_bindings` — Step-to-endpoint bindings
- `kg_feedback_records` — Feedback from LangSmith
- `kg_confidence_history` — Confidence score history

**Total**: ~27 tables

**Evidence**: `persistence/db.py`: lines 315-575 (`_init_sqlite_schema()`)

**Classification**: **Docs incomplete** — ARCHITECTURE.md lists Silver/Gold table names but omits Bronze (`raw_specs`), repo_meta, kg, and feedback tables.

---

### Q2: What is the difference between Postgres (production) and SQLite (tests)?

**Answer**:

| Aspect | Postgres | SQLite |
|--------|----------|--------|
| **Vector storage** | `VECTOR(1536)` type (pgvector) | JSON text (no native vector ops) |
| **Schemas** | Separate schemas: `spec_bronze`, `spec_silver`, `integration_gold`, `repo_meta`, `kg` | Single namespace (no schemas) |
| **Similarity search** | Native `<=>` operator (L2 distance) | Python-side cosine calculation |
| **Connection pooling** | `psycopg_pool.ConnectionPool` | No pooling (single connection) |
| **Concurrency** | Multi-user safe | Single-user, file locking |
| **Deprecation** | Recommended for production | Deprecated outside of tests |

**SQLite deprecation warning**: `_warn_sqlite_deprecation()` in `db.py` (lines 153-177) emits a `DeprecationWarning` when `USE_SQLITE=true` is used outside of pytest.

**Evidence**: 
- `persistence/db.py`: lines 153-177 (deprecation), 205-223 (engine detection)
- `persistence/postgres.py`: pgvector operations

**Classification**: **Matches current docs** — ARCHITECTURE.md correctly states SQLite is default for local, Postgres for production.

---

### Q3: What does `get_connection()` return and how does `ConnectionWrapper` ensure proper cleanup?

**Answer**: `get_connection()` (lines 225-268) returns a `ConnectionWrapper` instance.

**ConnectionWrapper** (lines 62-132):
- Wraps raw SQLite `Connection` or Postgres connection from pool
- Implements context manager (`__enter__`/`__exit__`)
- `close()` returns Postgres connection to pool or closes SQLite connection
- `__del__` safety net: warns once per session if garbage collected without `close()`
- Tracks `_closed` flag to prevent double-close

**Usage patterns supported**:
```python
# Recommended: context manager
with db.get_connection() as conn:
    cur = conn.cursor()
    ...

# Legacy: manual close
conn = db.get_connection()
try:
    ...
finally:
    conn.close()
```

**Evidence**: `persistence/db.py`: lines 62-132 (`ConnectionWrapper`), 225-268 (`get_connection`)

**Classification**: **Docs incomplete** — ConnectionWrapper not documented in ARCHITECTURE.md

---

## LLM / External Services View – P0 Audit

### Q1: How does an archetype YAML file translate to an LLM call?

**Answer**: The archetype→LLM call flow:

1. **Load archetype**: `load_archetype(node_name)` in `config/__init__.py` (lines 379-410)
   - Loads `config/archetypes/{node_name}.archetype.yaml`
   - Merges with `_base.archetype.yaml`
   - Applies env var overrides (`_apply_env_overrides()`)

2. **Get client**: `get_llm_client_for_node(node_name)` in `llm/client.py` (lines 795-815)
   - Calls `load_archetype()` then `get_llm_client_for_archetype()`

3. **Create provider client**: `get_llm_client_for_archetype()` (lines 700-760)
   - Reads `model.provider` from archetype
   - Falls back through provider chain (OpenAI → Anthropic → Google → Mock)
   - Creates appropriate client: `OpenAILLMClient`, `AnthropicLLMClient`, `GoogleLLMClient`, or `MockLLMClient`

4. **Make call**: `client.complete(prompt)` or `call_llm_for_node(node_name, prompt)` (lines 857-877)
   - Hardens system prompt via `harden_system_prompt()` (SEC-001)
   - Checks Redis cache
   - Calls provider API via LangChain
   - Caches result

**Evidence**:
- `config/__init__.py`: lines 379-410 (`load_archetype`)
- `llm/client.py`: lines 700-815 (`get_llm_client_for_archetype`, `get_llm_client_for_node`)

**Classification**: **Docs incomplete** — ARCHITECTURE.md mentions archetypes but doesn't document the full flow.

---

### Q2: What providers are supported and how is provider selection determined per node?

**Answer**: 

**Supported providers** (from `llm/client.py`):
- `openai` — GPT-4o, GPT-4o-mini, o1, etc.
- `anthropic` — Claude Sonnet 4.5, Claude 3.5 Sonnet, etc.
- `google` — Gemini 2.5 Flash, Gemini 2.5 Pro, etc.
- `mock` — Deterministic test responses

**Provider selection** (per node):
1. **Primary**: Read `model.provider` from `config/archetypes/{node_name}.archetype.yaml`
2. **Override**: `LLM_PROVIDER` env var overrides archetype
3. **Fallback chain**: If primary provider API key missing:
   - For `anthropic` primary: anthropic → openai → google
   - For `openai` primary: openai → anthropic → google
   - For `google` primary: google → openai → anthropic
4. **Final fallback**: `MockLLMClient` if no API keys

**Evidence**:
- `llm/client.py`: lines 660-760 (`get_llm_client_for_archetype`)
- `config/archetypes/` directory: 8 node-specific archetypes

**Classification**: **Docs incomplete** — ARCHITECTURE.md doesn't document fallback chain or Google support.

---

### Q3: What is the difference between `call_llm()`, `call_llm_for_node()`, and async variant?

**Answer**:

| Function | Purpose | Status |
|----------|---------|--------|
| `call_llm(prompt, task_type)` | Legacy function, uses `models.yaml` | **Deprecated** (emits `DeprecationWarning`) |
| `call_llm_for_node(node_name, prompt)` | Archetype-based, per-node config | **Recommended** |
| `call_llm_async_for_node()` | Async version (not found in codebase) | **Not implemented** |

**`call_llm()` deprecation** (lines 820-848):
```python
warnings.warn(
    "call_llm(task_type) is deprecated. Use call_llm_for_node(node_name) instead ...",
    DeprecationWarning,
    stacklevel=2,
)
```

**Evidence**: `llm/client.py`: lines 820-877

**Classification**: **Docs incomplete** — Deprecation status of `call_llm()` not documented.

---

### Q4: What is the archetype YAML structure?

**Answer**: From `config/archetypes/_base.archetype.yaml`:

```yaml
version: "1.0"

model:
  provider: openai        # openai, anthropic, google, mock
  name: gpt-4o            # Model name
  temperature: 0.7        # 0.0-1.0
  max_tokens: 2000        # Max output tokens

prompting:
  format: toon            # Token-Oriented Object Notation
  strategy: default       # chain_of_thought, extraction, etc.

retrieval:
  top_k: 5                # Number of chunks to retrieve
  graph_radius: 0         # BFS radius for KG traversal
  token_budget: 2000      # Max tokens for context

parallelism:
  num_samples: 1          # Number of parallel generations
  max_parallel_samples: 1 # Max concurrent

metadata:
  version: "1.0"
  trace_enabled: true     # LangSmith tracing
```

**Node-specific overrides**: Each node archetype (e.g., `understand_task.archetype.yaml`) overrides relevant sections while inheriting from `_base`.

**Evidence**: `config/archetypes/_base.archetype.yaml` (full file)

**Classification**: **Docs incomplete** — ARCHITECTURE.md doesn't document archetype YAML schema.

---

## Operational / CLI-API View – P0 Audit

### Q1: What is the full CLI surface?

**Answer**: From `cli.py`:

| Command | Purpose |
|---------|---------|
| `run` | Main workflow execution (lines 255-380) |
| `demo` | Quick demo with mock spec (lines 383-420) |
| `demo-v1` | Golden Path demo with timing table (lines 425-550) |
| `resume` | Resume interrupted run (lines 645-700) |
| `status` | Show config and DB status (lines 550-640) |
| `init-db` | Initialize schema and seed KG (lines 700-770) |
| `health` | Health check all components (lines 775-940) |
| `kg-dump` | Dump KG contents (lines 1050-1200) |
| `kg-query` | Query KG via BFS/DFS (lines 1200-1380) |
| `feedback` | Record human feedback (lines 1700-1780) |
| `feedback-sync` | Sync feedback from LangSmith (lines 1785-1900) |
| `ui` | Launch Streamlit UI (lines 1575-1620) |

**Key options for `run`**:
- `--spec-ref` / `-s`: Spec URL or path (repeatable)
- `--task` / `-t`: Task description
- `--repo-root` / `-r`: Target repository path
- `--dry-run` / `-n`: Don't persist to DB
- `--policy-mode`: `inline` (default) or `runtime`
- `--auto-resume`: Auto-resume interrupted runs
- `--resume`: Resume specific run by ID
- `--json` / `-j`: JSON output
- `--verbose` / `-v`: Debug logging

**Evidence**: `cli.py`: Typer command definitions throughout file

**Classification**: **Docs incomplete** — ARCHITECTURE.md mentions CLI but doesn't list all commands/options.

---

### Q2: What is the signature and contract of `design_and_generate_integration()`?

**Answer**: From `api/entrypoint.py` (lines 17-87):

```python
def design_and_generate_integration(
    spec_refs: Iterable[str],           # Spec URLs or file paths
    task_description: str,              # Natural language task
    provider_code: Optional[str] = None, # e.g., "stripe", "github"
    repo_root: Optional[Path] = None,   # Target repo for wiring
    repo_profile: Optional['RepoProfile'] = None,  # Explicit profile
    options: Optional[Union[IntegrationOptions, dict]] = None,
) -> IntegrationResult:
```

**Contract**:
1. Normalizes `options` (dict → `IntegrationOptions` if needed)
2. Creates initial `WorkflowState`
3. Generates unique `run_id` before workflow (Bug #68 fix)
4. Calls `run_workflow(state)`
5. Returns `IntegrationResult` with all artifacts

**Evidence**: `api/entrypoint.py`: lines 17-87

**Classification**: **Matches current docs** — ARCHITECTURE.md Quick Reference shows correct signature.

---

### Q3: What fields does `IntegrationOptions` contain?

**Answer**: From `api/types.py` (lines 14-54):

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `repo_integration_enabled` | bool | True | Enable repo analysis/wiring |
| `dry_run` | bool | False | Skip DB persistence |
| `override_provider_code` | str | None | Force provider code |
| `override_task_slug` | str | None | Force task slug |
| `override_llm_model` | str | None | Force LLM model |
| `override_max_tokens` | int | None | Force max tokens |
| `policy_mode` | Literal["inline", "runtime"] | "inline" | Codegen style (V2.1) |
| `no_cache` | bool | False | Skip spec caching (V1.1) |
| `strict_codegen` | bool | False | Enable strict validation (V1.1) |
| `constrained_codegen` | bool | False | Constrain LLM hallucination (V2.2) |

**Evidence**: `api/types.py`: lines 14-54

**Classification**: **Docs incomplete** — ARCHITECTURE.md doesn't list all `IntegrationOptions` fields.

---

### Q4: What fields does `IntegrationResult` contain?

**Answer**: From `api/types.py` (lines 57-105):

| Field | Type | Purpose |
|-------|------|---------|
| `run_id` | str | Unique run identifier |
| `task` | IntegrationTask | Gold layer task definition |
| `code_artifacts` | List[CodeArtifact] | Generated code files |
| `repo_changes` | RepoChangeSet | File changes for repo |
| `report_markdown` | str | Human-readable report |
| `persisted_ids` | dict | DB IDs and persistence status |
| `endpoints` | List | Silver endpoints |
| `schemas` | List | Silver schemas |
| `entities` | List | Silver entities |
| `workflow_nodes` | List | Gold flow nodes |
| `workflow_edges` | List | Gold flow edges |
| `endpoint_bindings` | List | Node→endpoint bindings |
| `policies` | List | Inferred policies (V2.1) |
| `cache_hit` | bool | Spec cache hit (V1.1) |
| `spec_documents` | List | Parsed spec docs |
| `doc_chunks` | List | Text chunks |
| `plan` | dict | Run plan |
| `errors` | List[str] | Error messages |
| `completed_steps` | List[str] | Completed node names |
| `provider_code` | str | Inferred provider |

**Evidence**: `api/types.py`: lines 57-105

**Classification**: **Docs incomplete** — ARCHITECTURE.md only mentions key fields, not the full result structure.

---

## Configuration / Environment View – P0 Audit

### Q1: What environment variables are required vs. optional?

**Answer**: From `config/__init__.py` and `cli.py`:

**Required** (for real LLM calls):
| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI API calls (or Anthropic/Google as fallback) |

**Required** (for production):
| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Postgres connection (e.g., `postgresql://user:pass@host:5432/db`) |

**Optional**:
| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | None | Anthropic Claude models |
| `GOOGLE_API_KEY` | None | Google Gemini models |
| `USE_SQLITE` | false | Use SQLite instead of Postgres |
| `USE_MOCK_LLM` | false | Use mock LLM (no API calls) |
| `LLM_PROVIDER` | openai | Default LLM provider |
| `LLM_MODEL` | gpt-4o | Default LLM model |
| `LLM_BASE_URL` | None | Custom LLM endpoint (Azure, etc.) |
| `REDIS_URL` | redis://localhost:6379/0 | LLM cache |
| `LLM_CACHE_ENABLED` | true | Enable LLM caching |
| `LLM_CACHE_TTL` | 86400 | Cache TTL (seconds) |
| `LANGCHAIN_TRACING_V2` | false | Enable LangSmith tracing |
| `LANGCHAIN_API_KEY` | None | LangSmith API key |
| `LANGCHAIN_PROJECT` | default | LangSmith project name |
| `STREAMING_PERSISTENCE` | auto | Streaming mode (auto/true/false) |
| `STREAMING_THRESHOLD_BYTES` | 500000 | Auto-stream threshold |
| `PARALLEL_WORKFLOW` | false | Enable parallel graph |

**Evidence**: 
- `config/__init__.py`: lines 1-50 (docstring), 60-150 (dataclasses)
- `llm/client.py`: lines 1-50 (docstring)

**Classification**: **Docs incomplete** — ARCHITECTURE.md doesn't have a complete env var reference.

---

### Q2: What is the precedence order for settings?

**Answer**: From `config/__init__.py`:

1. **Environment variable** (highest priority)
   - e.g., `LLM_PROVIDER`, `LLM_MODEL` override archetype
   
2. **Archetype YAML** (`config/archetypes/{node}.archetype.yaml`)
   - Node-specific overrides inherit from `_base`
   
3. **`models.yaml`** (deprecated, still read by `get_llm_config()`)

4. **Hardcoded defaults** (lowest priority)
   - e.g., `gpt-4o`, `temperature: 0.7`

**Override mechanism** (`_apply_env_overrides()`, lines 435-475):
- `LLM_PROVIDER` → `model.provider`
- `LLM_MODEL` → `model.name` (only if compatible with provider)
- `USE_MOCK_LLM=true` → `model.provider = "mock"`

**Bug #26 Fix**: When `LLM_PROVIDER` changes provider, model name is reset to new provider's default if incompatible (e.g., `claude-*` model with `openai` provider).

**Evidence**: `config/__init__.py`: lines 435-475 (`_apply_env_overrides`)

**Classification**: **Docs incomplete** — Precedence order not documented.

---

### Q3: How does `get_settings()` work and what is the `Settings` dataclass structure?

**Answer**: From `config/__init__.py`:

**`get_settings()`** (lines 225-232):
- Returns singleton `Settings` instance (lazy-loaded)
- Created via `Settings.from_env()` which reads env vars

**`Settings` dataclass** (lines 175-220):
```python
@dataclass
class Settings:
    database: DatabaseConfig   # url, use_sqlite, sqlite_path
    llm: LLMConfig            # api_key, base_url, default_model, mode
    embedding: EmbeddingConfig # model, dimensions, batch_size
    cache: CacheConfig        # redis_url, enabled, ttl
    
    http_timeout: int = 30
    http_max_retries: int = 3
    http_retry_backoff: float = 1.0
    
    streaming_persistence: str = "auto"
    streaming_threshold_bytes: int = 500000
    streaming_threshold_chunks: int = 500
```

**Validation**: `settings.validate()` returns list of warnings (e.g., "Using SQLite instead of Postgres")

**Evidence**: `config/__init__.py`: lines 65-230

**Classification**: **Docs incomplete** — Settings structure not documented.

---

## Doc-Drift Detection – P0 Audit (Remaining)

### Q2: ARCHITECTURE.md claims "MAX SPEC SIZE: ~20 MB per document (design target, not runtime-enforced)"—is this accurate?

**Answer**: **Accurate** (design target only, no enforcement).

**Evidence**:
- Grep search found no `MAX_SPEC_SIZE` constant in code
- `docs/AGENTIC_BEHAVIOR_BOUNDS.md` line 20: "Specs > 20MB | ✅ Section 2.4 | ⚠️ Untested (no limits)"
- V3 Streaming Persistence (lines 195-220 in `config/__init__.py`) handles large specs but doesn't enforce a limit

**Conclusion**: The ~20MB is a design target, not enforced. Streaming persistence allows larger specs.

**Classification**: **Matches current docs** — ARCHITECTURE.md correctly says "not runtime-enforced".

---

### Q3: ARCHITECTURE.md claims "Idempotent Operations: Running same inputs twice produces same outputs"—what code enforces this?

**Answer**: **Partially accurate, with caveats**.

**What enforces idempotency**:
1. **Spec caching**: SHA256 deduplication (`persistence/streaming.py` line 66)
2. **DB upserts**: `INSERT OR IGNORE` / `ON CONFLICT DO NOTHING` for deduplication
3. **Marker-based insertion**: `upsert_block_between_markers()` in `repo/helpers.py` (line 20)
4. **Run ID uniqueness**: `state.run_id = f"run_{uuid.uuid4().hex[:8]}_{timestamp}"` (Bug #68 fix)

**What breaks idempotency**:
1. **LLM variability**: Temperature > 0 produces different outputs
2. **Timestamps**: `run_id` includes timestamp, so re-runs have different IDs
3. **Non-deterministic LLM**: Even with temp=0, LLM outputs vary slightly

**Evidence**:
- `persistence/streaming.py`: line 66 (SHA256 deduplication)
- `repo/helpers.py`: line 20 (marker-based insertion)
- ADR-0009 (line 240): "Re-running a node with the same inputs produces the same outputs"

**Classification**: **Docs incorrect** — The claim is aspirational for persistence but not true for LLM outputs. Should be qualified.

---

### Q4: ARCHITECTURE.md claims "Generated code is syntactically valid: AST validation + template fallback"—where is AST validation implemented?

**Answer**: **Accurate**.

**AST validation** implemented in `codegen/security.py`:
- `validate_syntax(code)` (lines 281-300): Uses `ast.parse()` to check Python syntax
- Alias: `check_syntax = validate_syntax` (line 300)

**Template fallback** implemented in `graph/nodes/generate_code_and_tests.py`:
- Line 348-349: `if not _validate_python_syntax(clean_refined): logger.warning(...)`
- Line 402: Retry logic with `fix_result.fixed_code`
- Lines 1024-1041: Strict mode validation with fallback

**Fallback behavior**:
1. Validate LLM output with AST
2. If invalid in non-strict mode: log warning, use template
3. If invalid in strict mode: raise `ValueError`

**Evidence**:
- `codegen/security.py`: lines 281-300 (`validate_syntax`)
- `graph/nodes/generate_code_and_tests.py`: lines 346-350, 1020-1045

**Classification**: **Matches current docs** — AST validation and template fallback exist as documented.

---

## Cross-Cutting Concerns – P0 Audit

### Q9: What is the complete end-to-end data flow?

**Answer**: Complete flow from spec → report:

**Phase 1: Ingestion (Bronze → Silver)**
1. `plan_run`: Validates inputs → `state.run_id`, `state.plan`
2. `ingest_spec`: Loads spec content → `state.spec_documents`, `state.doc_chunks`
3. `detect_and_parse_spec`: Parses format → `state.openapi_spec`, `state.spec_sections`
4. `build_silver_api_model`: Extracts API surface → `state.endpoints`, `state.schemas`, `state.entities`
5. `embed_spec_chunks`: Generates embeddings → `state.spec_chunk_embeddings`
6. `persist_silver_checkpoint`: Writes to DB → `state.persisted_ids`

**Phase 2: Planning (Silver → Gold)**
7. `understand_task`: Parses task → `state.integration_task`
8. `align_task_with_kg`: Matches KG patterns → `state.workflow_template`
9. `plan_integration_flow`: Designs workflow → `state.workflow_nodes`, `state.workflow_edges`, `state.endpoint_bindings`

**Phase 3: Generation (Gold → Code)**
10. `attach_policies_and_patterns`: Infers policies → `state.policies`
11. `generate_code_and_tests`: Produces code → `state.code_artifacts`
12. `persist_gold_checkpoint`: Writes Gold to DB
13. `persist_kg_learning`: Updates KG with learned patterns

**Phase 4: Repository (Optional)**
14. `attach_repo_context`: Loads profile → `state.repo_profile`
15. `analyze_repo_layout`: Scans structure → `state.repo_snapshot`
16. `apply_repo_integration_changes`: Generates patches → `state.repo_changes`

**Phase 5: Finalization**
17. `validate_integration_design`: Checks structure → `state.errors`
18. `handle_error` (conditional): On errors → `state.plan["failed"]`
19. `build_report`: Generates markdown → `state.report_markdown`
20. `persist_run_outcome`: Records final status

**Evidence**: `graph/runtime.py` `build_graph()` (lines 310-414)

**Classification**: **Matches current docs** — ARCHITECTURE.md Data Flow section is accurate.

---

### Q10: What is the minimum viable environment to run successfully?

**Answer**:

**Minimum for mock/test runs**:
```bash
USE_SQLITE=true
USE_MOCK_LLM=true
```

**Minimum for real LLM runs**:
```bash
USE_SQLITE=true
OPENAI_API_KEY=sk-...
```

**Minimum for production**:
```bash
DATABASE_URL=postgresql://user:pass@localhost:5432/integration_coworker
OPENAI_API_KEY=sk-...
# Optional but recommended:
ANTHROPIC_API_KEY=sk-ant-...
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=...
```

**Database setup**:
1. Run `integration-coworker init-db` to create schema
2. For Postgres: Ensure pgvector extension: `CREATE EXTENSION IF NOT EXISTS vector;`

**Evidence**: 
- `cli.py` `health` command validation
- `config/__init__.py` `Settings.validate()`

**Classification**: **Docs incomplete** — ARCHITECTURE.md references GETTING_STARTED.md but doesn't summarize min viable env.

---

## P0 Doc-Drift Index

| Area/View | Short Description | Question ID | Evidence |
|-----------|-------------------|-------------|----------|
| **Runtime / Workflow** | Node count off by one (21 not 22) | Runtime Q1 | `runtime.py:310-414` |
| **Runtime / Workflow** | Checkpoint recovery mechanism undocumented | Runtime Q4 | `runtime.py:194-293` |
| **Runtime / Workflow** | Parallel execution (`PARALLEL_WORKFLOW`) missing | Runtime Q6 | `runtime.py:432-539` |
| **Runtime / Workflow** | Error conditional edge (`check_for_errors_after_validation`) missing | Runtime Q3 | `runtime.py:408-413` |
| **Data / State** | Bronze layer oversimplified (4 fields, not just `openapi_spec`) | Data Q1 | `graph/state.py` |
| **Data / State** | ~35 fields, not ~50 | Data Q1 | `graph/state.py` |
| **Logical / Module** | Package responsibilities not documented | Logical Q1 | `src/integration_coworker/` |
| **Logical / Module** | Import dependency flow undocumented | Logical Q2 | Various imports |
| **Persistence** | Bronze, repo_meta, kg, feedback tables missing from docs | Persistence Q1 | `persistence/db.py:315-575` |
| **Persistence** | ConnectionWrapper undocumented | Persistence Q3 | `persistence/db.py:62-132` |
| **LLM** | Archetype→LLM call flow undocumented | LLM Q1 | `config/__init__.py`, `llm/client.py` |
| **LLM** | Provider fallback chain undocumented | LLM Q2 | `llm/client.py:700-760` |
| **LLM** | Google Gemini support undocumented | LLM Q2 | `llm/client.py:525-620` |
| **LLM** | `call_llm()` deprecation status undocumented | LLM Q3 | `llm/client.py:820-848` |
| **LLM** | Archetype YAML schema undocumented | LLM Q4 | `config/archetypes/_base.archetype.yaml` |
| **CLI-API** | Full CLI command list incomplete | CLI Q1 | `cli.py` |
| **CLI-API** | `IntegrationOptions` fields incomplete | CLI Q3 | `api/types.py:14-54` |
| **CLI-API** | `IntegrationResult` fields incomplete | CLI Q4 | `api/types.py:57-105` |
| **Config** | Environment variables reference missing | Config Q1 | `config/__init__.py` |
| **Config** | Settings precedence order undocumented | Config Q2 | `config/__init__.py:435-475` |
| **Config** | `Settings` dataclass structure undocumented | Config Q3 | `config/__init__.py:175-220` |
| **Doc-Drift** | "Idempotent Operations" claim needs qualification | Doc-Drift Q3 | LLM variability |
| **Cross-Cutting** | Minimum viable environment undocumented | Cross Q10 | `cli.py`, `config/` |

---

## Summary Statistics

- **Total P0 Questions in ARCHITECTURE_AUDIT_QUESTIONS.md**: 35
- **Already covered in ARCHITECTURE_AUDIT_RUNTIME_DATA.md**: 10
- **Newly answered in this document**: 20
- **Remaining (not P0 or need more investigation)**: 5

---

## Next Steps for P1 Audit

The following P1 questions should be addressed next:

1. **Runtime / Workflow P1**:
   - Q6: `NODE_DEPENDENCIES` and `get_skip_cascade()`
   - Q7: `sync_embed_task` parallel node
   - Q8: `WORKFLOW_NODE_ORDER` uses
   - Q9: LangSmith run context setup

2. **Persistence / Storage P1**:
   - Q4: What each `persist_*` node writes
   - Q5: `streaming.py` thresholds and auto-enable
   - Q6: `db.py` vs `postgres.py` vs `sql_helpers.py`
   - Q7: LangGraph checkpoint tables

3. **LLM / External Services P1**:
   - Q5: LLM cache key structure
   - Q6: `with_retry()` retry logic
   - Q7: `harden_system_prompt()` details
   - Q8: LLM modes (REAL, MOCK, RECORD, REPLAY)

4. **Security / Safety P1**:
   - Q1: `sanitizer.py` patterns
   - Q2: Sanitization levels
   - Q3: Content policy
   - Q4: API key handling
   - Q5: Codegen security tests

5. **Observability P1**:
   - Q1: LangSmith configuration
   - Q2: Trace metadata
   - Q3: Token usage tracking
   - Q4: `persist_run_outcome` details

**Do NOT start P1 answers yet** — await review of this P0 audit.
