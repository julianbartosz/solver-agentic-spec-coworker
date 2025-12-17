# Architecture Overview: Agentic Integration Designer & Code Generator

**Last Updated**: November 24, 2025  
**Status**: M4 Production-Ready (69/69 tests passing)

---

## 1. High-Level Goal

The **Agentic Integration Designer** takes an API specification (OpenAPI/Swagger) and a plain-English task description, then automatically generates working Python client code, workflow functions, and tests for that specific integration. It builds a normalized "Silver" model of the entire API surface, creates a task-specific "Gold" integration workflow, and optionally wires the generated code into an existing repository using framework-aware placement strategies. All design decisions are persisted to a SQLite database for traceability, versioning, and reuse.

---

## 2. End-to-End Flow

**Entry Points**:
- **CLI**: `integration_coworker.cli` → parses args → calls entrypoint
- **Python API**: `design_and_generate_integration()` in `api/entrypoint.py`

**Execution Path** (17 LangGraph nodes):

1. **Ingestion & Parsing**:
   - `plan_run` → validates inputs, generates run_id, sets flags (use_repo, provider_code)
   - `ingest_spec` → loads spec file/URL, computes SHA256, creates text chunks
   - `detect_and_parse_spec` → identifies format (OpenAPI 3.x), parses to dict

2. **Silver Model (API Surface Extraction)**:
   - `build_silver_api_model` → walks OpenAPI paths/schemas → creates Endpoint, Schema, SchemaField, Entity objects
   - `embed_spec_chunks` → generates 1536-dim embeddings for semantic search (text-embedding-3-small)

3. **Gold Model (Task-Specific Integration)**:
   - `understand_task` → normalizes task description to task_slug, derives constraints (idempotency, webhooks, target operations)
   - `align_task_with_kg` → matches task against in-memory workflow templates (WORKFLOW_TEMPLATES dict)
   - `plan_integration_flow` → builds IntegrationFlowNode/Edge graph, creates EndpointBinding scaffolds, validates flow structure

4. **Policy & Code Generation**:
   - `attach_policies_and_patterns` → attaches auth, retry, rate_limit, logging, idempotency policies
   - **[Conditional Branch]** if `plan["use_repo"]` is True:
     - `attach_repo_context` → scans repo, infers RepoProfile if not provided
     - `analyze_repo_layout` → generates markdown context describing where to place files
     - `apply_repo_integration_changes` → creates RepoChangeSet with file edits (router/settings markers)
   - `generate_code_and_tests` → produces 3 CodeArtifact objects (client, flow, test) using templates

5. **Validation & Persistence**:
   - `validate_integration_design` → enforces flow structure (exactly 1 start, ≥1 end, connectivity, positions)
   - **[Error Handling]** routes to `handle_error` if validation fails, sets `plan["failed"]=True`
   - `persist_results` → **ONLY node that writes to DB** → upserts Silver/Gold records, backfills IDs
   - `build_report` → generates human-readable markdown report

6. **Return**:
   - Graph returns final `WorkflowState`
   - Entrypoint converts to `IntegrationResult` (run_id, status, task, code_artifacts, repo_changes, report_markdown)

---

## 3. Key Modules & Files

### **Entry Points**
- **`src/integration_coworker/api/entrypoint.py`**:
  - `design_and_generate_integration()` → main API function
  - Converts inputs → WorkflowState, runs graph, returns IntegrationResult
- **`src/integration_coworker/cli.py`**:
  - CLI argument parsing (--spec-ref, --task, --dry-run, --repo-root, etc.)
  - Calls entrypoint, prints report markdown to stdout

### **State Management**
- **`src/integration_coworker/graph/state.py`**:
  - `WorkflowState` → single shared state object passed through all nodes
  - Contains: inputs (spec_refs, task_description), Bronze (spec_documents, openapi_spec), Silver (endpoints, schemas, entities), Gold (integration_task, workflow_nodes, code_artifacts), control (plan, errors, run_id)
  - `IntegrationResult` → output dataclass returned to caller

### **Graph Orchestration**
- **`src/integration_coworker/graph/runtime.py`**:
  - `build_graph()` → wires 17 nodes using LangGraph StateGraph
  - Conditional routing: `should_run_repo_nodes()` checks `plan["use_repo"]`
  - Error routing: `check_for_errors_after_validation()` routes to handle_error if needed

### **Node Implementations** (grouped by responsibility)

**Ingestion & Parsing**:
- `graph/nodes/plan_run.py` → run initialization, input validation, flag setting
- `graph/nodes/ingest_spec.py` → file/URL loading, SHA256 computation
- `graph/nodes/detect_and_parse_spec.py` → format detection, YAML/JSON parsing

**Silver Model Extraction**:
- `graph/nodes/build_silver_api_model.py` → OpenAPI → Endpoint/Schema/Entity extraction
- `graph/nodes/embed_spec_chunks.py` → text embedding for semantic search

**Gold Model Planning**:
- `graph/nodes/understand_task.py` → task normalization, IntegrationTask creation
- `graph/nodes/align_task_with_kg.py` → template matching (in-memory WORKFLOW_TEMPLATES)
- `graph/nodes/plan_integration_flow.py` → node/edge creation, binding scaffolds, validation

**Policy & Generation**:
- `graph/nodes/attach_policies_and_patterns.py` → policy attachment (auth, retry, etc.)
- `graph/nodes/generate_code_and_tests.py` → template-based codegen (client, flow, test)

**Repo Integration** (conditional):
- `graph/nodes/attach_repo_context.py` → repo scanning, profile inference
- `graph/nodes/analyze_repo_layout.py` → layout markdown generation
- `graph/nodes/apply_repo_integration_changes.py` → RepoChangeSet creation

**Validation & Persistence**:
- `graph/nodes/validate_integration_design.py` → flow structure validation
- `graph/nodes/persist_results.py` → **ONLY DB writer** → SQLite INSERT/UPDATE
- `graph/nodes/build_report.py` → markdown report generation

**Error Handling**:
- `graph/nodes/handle_error.py` → sets plan["failed"], logs errors

### **Domain Models**
- **`src/integration_coworker/domain/models.py`**:
  - **Silver**: SpecDocument, Endpoint, Schema, SchemaField, Entity, EndpointParameter, EntityRelationship
  - **Gold**: IntegrationTask, IntegrationFlowNode, IntegrationFlowEdge, EndpointBinding, Policy, CodeArtifact
  - All use `@dataclass` with Optional[int] IDs (None until persistence)

### **Persistence Layer**
- **`src/integration_coworker/persistence/db.py`**:
  - `get_connection()` → returns SQLite connection (`.data/integration_coworker.sqlite3`)
  - `init_schema()` → creates 12+ tables (idempotent)
  - `clear_test_data()` → deletes all rows (test isolation)
- **`src/integration_coworker/graph/nodes/persist_results.py`**:
  - Upserts source_systems, spec_documents, endpoints, schemas, entities
  - Inserts integration_tasks, integration_flow_nodes, integration_flow_edges, endpoint_bindings, code_artifacts
  - Backfills IDs into state objects after INSERT
  - Honors dry_run flag (no writes if True)

### **Repo Integration**
- **`src/integration_coworker/repo/models.py`**:
  - `RepoProfile` → framework-specific config (archetype, conventions, layout_hints, integration_hooks)
  - `RepoChangeSet` → collection of FileChange objects (create/update/delete)
  - `MockFile` → in-memory file representation for MockedGithubRepoRetriever
- **`src/integration_coworker/repo/profiles.py`**:
  - `SUBATOMIC_MOCK_PROFILE` → FastAPI service profile (src/integrations/, tests/integrations/)
  - `FASTAPI_PROFILE`, `NEXTJS_APP_ROUTER_PROFILE`, `DJANGO_REST_PROFILE` → other framework templates
  - `detect_profile_from_repo()` → infers profile from file patterns
- **`src/integration_coworker/repo/mock_github.py`**:
  - `MockedGithubRepoRetriever` → in-memory repo representation
  - `export_markdown()` → generates deterministic markdown with file tree + contents
- **`src/integration_coworker/repo/helpers.py`**:
  - `upsert_block_between_markers()` → idempotent marker-based insertion
  - `generate_router_block()`, `generate_settings_block()` → FastAPI-specific code blocks

### **Runtime Components**
- **`src/integration_coworker/runtime/http_client.py`**:
  - `IntegrationHttpClient` → httpx wrapper with auto-retry, auth injection, timeout
  - Retries transient errors (429, 5xx, timeouts) up to 3 times
  - Raises AuthIntegrationError (401/403), TransientIntegrationError (retryable), IntegrationError (unexpected)
- **`src/integration_coworker/runtime/exceptions.py`**:
  - `IntegrationError` (base), `TransientIntegrationError` (retryable), `AuthIntegrationError` (auth failures)

---

## 4. Database & Persistence

**Database**: SQLite file at `.data/integration_coworker.sqlite3`

**Tables** (12 total):

**Silver Layer** (spec_silver):
- `source_systems` → API providers (code, display_name, base_url)
- `spec_documents` → OpenAPI specs (path, sha256, content_type)
- `endpoints` → API operations (method, path, operation_id, summary, request_schema_id, response_schema_id)
- `schemas` → JSON schemas (name, ref)
- `schema_fields` → schema properties (name, json_path, field_type, required)
- `entities` → business objects (name, description)

**Gold Layer** (integration_gold):
- `integration_tasks` → task definitions (task_slug, provider_code, description, target_spec_document_id)
- `integration_flow_nodes` → workflow nodes (node_key, node_type, label, position)
- `integration_flow_edges` → workflow edges (from_node_key, to_node_key, condition)
- `endpoint_bindings` → node→endpoint mappings (flow_node_key, endpoint_id, request_mapping_json, response_mapping_json)
- `policies` → attached policies (policy_type, config_json)
- `code_artifacts` → generated code (artifact_type, language, rel_path, content)

**Key Constraints**:
- Foreign keys enforce referential integrity (e.g., endpoints.spec_document_id → spec_documents.id)
- Unique constraints prevent duplicates (e.g., source_systems.code, UNIQUE(spec_document_id, sha256))
- Idempotent INSERT OR IGNORE → running same spec twice doesn't create duplicates

**Persistence Contract**:
- `persist_results` is the **ONLY node** that writes to DB (all other nodes work with in-memory drafts)
- IDs are None in state objects until persist_results backfills them after INSERT
- Dry-run mode (options.dry_run=True) skips all DB writes, populates persisted_ids with "would persist" summary

---

## 5. Repo Integration

**How It Works**:

1. **Profile Detection**:
   - `attach_repo_context` calls `detect_profile_from_repo(repo_root)` if profile not provided
   - Scans for file patterns: `package.json` → Next.js, `setup.py` → Python, `requirements.txt` → Python script
   - Falls back to `SUBATOMIC_MOCK_PROFILE` if no match

2. **File Placement**:
   - `RepoProfile` defines `layout_hints` dict:
     - `clients_dir` → where to place client modules (e.g., `src/integrations/clients/`)
     - `workflows_dir` → where to place flow modules (e.g., `src/integrations/flows/`)
     - `tests_dir` → where to place test modules (e.g., `tests/integrations/`)
   - `apply_repo_integration_changes` uses these hints to construct file paths

3. **Marker-Based Insertion** (Appendix G pattern):
   - `RepoProfile.integration_hooks` defines:
     - `router_file` → path to router file (e.g., `src/app/router.py`)
     - `router_registration_marker` → marker comment (e.g., `# <AUTO_INTEGRATION_MARKER>`)
     - `settings_file`, `settings_marker` → similar for settings
   - `upsert_block_between_markers()` function:
     - If markers exist: replaces content between them
     - If not: appends markers + block at end of file
   - **Idempotent**: running twice produces same result

4. **MockedGithubRepoRetriever**:
   - In-memory representation of repo for markdown export
   - `add_file(path, content)` stores files in dict
   - `export_markdown()` produces deterministic markdown with:
     - Repo header (owner/repo_name)
     - File tree
     - Extension statistics
     - Full file contents (truncated if >100 lines)
   - Used for RAG context in `attach_repo_context` node

**Example Flow** (FastAPI service):
- Profile: `SUBATOMIC_MOCK_PROFILE` (archetype="fastapi_service")
- Generated files:
  - `src/integrations/clients/mock_payments.py` (client class)
  - `src/integrations/flows/mock_payments_checkout.py` (workflow function)
  - `tests/integrations/test_mock_payments_checkout.py` (pytest tests)
- Router update: inserts `router.include_router(...)` between markers in `src/app/router.py`
- Settings update: inserts provider config between markers in `src/app/settings.py`

---

## 6. Testing & Milestones

**Test Suite**: 69 tests, 100% passing, ~1.3s runtime

**Key Test Suites**:

1. **Persistence** (`tests/test_m4_persistence.py`): 3 tests
   - Validates SQLite writes, ID backfilling, idempotency
   - Confirms endpoint_bindings get endpoint_id populated post-persistence

2. **HTTP Client** (`tests/runtime/test_integration_http_client.py`): 14 tests
   - Auth header injection, auto-retry for transient errors, exception types
   - Validates httpx integration, timeout handling, connection management

3. **End-to-End Integration** (`tests/test_end_to_end_integration.py`): 7 tests
   - Full workflow from spec → code → report
   - Dry-run mode, repo integration, provider inference, error handling

4. **Silver Model Extraction** (`tests/test_build_silver_api_model.py`): 6 tests
   - Endpoint/schema/entity extraction from OpenAPI
   - Schema linkage (request_schema_id, response_schema_id)
   - EntityRelationship field correctness (source_entity_id, target_entity_id)

5. **Code Execution** (`tests/test_m4_generated_code_execution.py`): 2 tests
   - Imports generated client/flow, executes with mocked HTTP
   - Validates syntactic correctness and semantic functionality

6. **Repo Integration** (`tests/repo/test_marker_insertion.py`): 8 tests
   - Marker-based block upsert (idempotency, multiline, replacement)
   - Router/settings block generation

7. **Graph Behavior** (`tests/graph/test_error_routing.py`): 4 tests
   - Error routing after validation, handle_error flag setting

**Milestone Progress**:
- **M3** (November 2025): LangGraph orchestration, in-memory KG templates, mocked persistence
- **M4** (November 2025): Real SQLite persistence, ID backfilling, schema linking, code execution validation, repo archetype support
- **M5** (Planned): Multi-provider support, Postgres+pgvector KG, advanced workflow patterns (polling, webhooks, pagination)

**Test Organization**:
- Unit tests: Individual node behavior (validate inputs/outputs)
- Integration tests: Multi-node workflows (e.g., plan_run → understand_task → align)
- End-to-end tests: Full graph execution (CLI → DB → report)
- Fixture tests: Mock data quality (`tests/fixtures/mock_payments_openapi.yaml`)

**CI/CD**:
- Global `tests/conftest.py` fixture ensures DB isolation (reset before each test)
- No flaky tests (DB locking eliminated via connection lifecycle management)
- Deterministic outputs (same input → same code artifacts, same report)

---

## 7. Design Note: KG vs Semantic Search

This system combines **structural graph traversal** over a knowledge graph (KG) with **semantic search** over
embeddings. Each technique has a clearly separated role.

### 7.1 Structural KG Traversal (BFS/DFS)

- Implemented in `integration_coworker.kg` using classic graph algorithms (BFS/DFS).
- Used for **structural questions** about the KG:
  - "What workflow templates are related to this entity or endpoint?"
  - "What is the shortest path between template A and concept B?"
  - "Which tasks connect provider X pattern Y to entity Z?"
- Powers functions like:
  - `find_related_tasks_via_graph`
  - `find_cross_provider_tasks_via_pattern`
  - `get_shortest_path`
- These operations are explainable: they return paths, relation types, and graph distances.

### 7.2 Semantic Search (Embeddings)

- Implemented in `integration_coworker.retrieval.semantic_search`.
- Used for **free-text and similarity** questions:
  - "Which spec chunks best match this task description?"
  - "Among candidate templates, which ones read like what the user asked for?"
- Key entry points:
  - `search_spec_chunks` → semantic retrieval over `spec_chunks` (no KG traversal).
  - `search_kg_templates` → semantic ranking over a candidate set of KG templates.
- Backends:
  - SQLite: cosine similarity computed in Python.
  - Postgres+pgvector (v1 path): similarity pushed into the database when available.

### 7.3 Hybrid Strategy (GraphRAG)

For **template selection**, we use a hybrid "GraphRAG" approach:

1. **Graph filter (KG traversal):**
   - Use BFS/DFS-based helpers to find candidate workflow templates based on provider, entities, patterns,
     and other structural constraints.
2. **Semantic ranking (embeddings):**
   - Embed the task description.
   - Score candidates by semantic similarity to their natural-language metadata.
3. **Combined scoring:**
   - `align_task_with_kg` combines graph-derived scores (distance, pattern coverage) with semantic similarity and
     small exact-match bonuses (e.g., for HTTP method/path or provider code).

This yields candidates that are both **topologically appropriate** in the KG and **linguistically aligned** with the
user’s request, while keeping the reasoning path inspectable.

### 7.4 Non-goals for v1

- We do **not** use semantic search to dynamically traverse or rewire the **LangGraph** workflow itself. Node ordering
  and branching remain explicit and statically defined in `graph/runtime.py`.
- We do **not** replace KG BFS/DFS with pure embedding search for structural queries (neighbors, shortest paths,
  cross-provider bridges). Semantic search **augments** these operations; it does not supersede them.

---

## Quick Reference

**Entry Point**:
```python
from integration_coworker.api.entrypoint import design_and_generate_integration

result = design_and_generate_integration(
    spec_refs=["path/to/openapi.yaml"],
    task_description="Create checkout session",
    provider_code="stripe",  # optional
    repo_root="/path/to/repo",  # optional
    options=IntegrationOptions(dry_run=True)
)
```

**Key State Transitions**:
1. Inputs → WorkflowState (spec_refs, task_description)
2. Bronze → openapi_spec (parsed dict)
3. Silver → endpoints, schemas, entities (normalized API surface)
4. Gold → integration_task, workflow_nodes, code_artifacts (task-specific design)
5. Output → IntegrationResult (run_id, status, artifacts, report)

**Critical Design Principles**:
- **Single DB Writer**: Only `persist_results` writes to database
- **Idempotent Operations**: Running same inputs twice produces same outputs
- **Conditional Routing**: Repo integration only runs if `plan["use_repo"]=True`
- **Error Resilience**: Validation failures route to `handle_error`, still persist and report
- **Template-Driven**: Workflow templates (not LLM) ensure predictable codegen
- **Framework-Aware**: RepoProfile enables different placement strategies per framework

---

**For Deeper Dives**:
- **Flow Details**: See `docs/PHASE3_NOTES.md` for node-by-node walkthrough
- **Milestone Status**: See `docs/M4_SHIP_CHECKLIST.md` for completeness verification
- **Demo Script**: See `docs/M4_EXECUTIVE_SUMMARY.md` for 5-minute presentation flow
