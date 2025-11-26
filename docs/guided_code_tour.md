# Guided Code Tour: Integration Co-Worker

**Purpose**: A 60-90 minute walkthrough to understand the codebase deeply.  
**How to Use**: Open each file mentioned, read the code, then ask Copilot the questions listed (or explore on your own).

> **💡 Tip**: You can paste any of these questions into Copilot Chat and I will answer them by referencing the actual code in this repo.

---

## Step 1: Entry Points – How Execution Starts

### Files to Open:
- `src/integration_coworker/cli.py`
- `src/integration_coworker/api/entrypoint.py`

### What to Look For:
- **CLI**: How arguments are parsed (`argparse`), how they map to `IntegrationOptions`
- **Entrypoint**: The `design_and_generate_integration()` function signature, how it builds `WorkflowState`, calls the graph, and converts result

### Questions to Explore:
1. What happens if a user runs `--dry-run`? Trace where `options.dry_run` is checked.
2. How does `provider_code` get inferred if the user doesn't provide `--provider`?
3. What's the difference between `IntegrationOptions` and `WorkflowState`?
4. How does the CLI print the final report? (Hint: look at `result.report_markdown`)
5. What does `IntegrationResult` contain, and why is it different from `WorkflowState`?

---

## Step 2: State Management – The Shared Context

### Files to Open:
- `src/integration_coworker/graph/state.py`

### What to Look For:
- `WorkflowState` dataclass: all the fields (inputs, Bronze, Silver, Gold, control)
- `IntegrationResult` dataclass: the public API return type

### Questions to Explore:
1. What fields does `WorkflowState` track, and how do they flow through nodes?
2. Which fields are inputs (set before graph runs), and which are outputs (populated by nodes)?
3. What's the difference between `spec_documents` (Bronze), `endpoints` (Silver), and `integration_task` (Gold)?
4. Why are all ID fields `Optional[int]` instead of just `int`?
5. What's stored in the `plan` dict, and which nodes read/write it?
6. How does `completed_steps` help with debugging or observability?

---

## Step 3: Graph Orchestration – Wiring the Workflow

### Files to Open:
- `src/integration_coworker/graph/runtime.py`

### What to Look For:
- `build_graph()` function: how 17 nodes are registered with LangGraph
- Conditional edges: `should_run_repo_nodes()`, `check_for_errors_after_validation()`
- Linear edges vs. branching edges

### Questions to Explore:
1. How does `plan_run` decide whether to use repo integration? (Look for `plan["use_repo"]`)
2. What's the difference between `add_edge()` and `add_conditional_edges()`?
3. If validation fails, which path does the graph take? (Follow `check_for_errors_after_validation`)
4. What nodes run in the "with_repo" path vs. "without_repo" path?
5. Why does the graph still go to `persist_results` even after `handle_error`?

---

## Step 4: Key Node – plan_run (Initialization)

### Files to Open:
- `src/integration_coworker/graph/nodes/plan_run.py`

### What to Look For:
- UUID generation for `run_id`
- Validation of `spec_refs` length (must be exactly 1)
- Provider code normalization
- `plan["use_repo"]` logic

### Questions to Explore:
1. What happens if a user provides 2 spec_refs? (Trace the ValueError)
2. How is `provider_code` normalized? (Look for `re.sub`)
3. What's the difference between `state.provider_code` and `plan["provider_code"]`?
4. When is `plan["use_repo"]` set to True vs. False?
5. Why does the node append "plan_run" to `state.completed_steps` at the end?

---

## Step 5: Key Node – build_silver_api_model (API Surface Extraction)

### Files to Open:
- `src/integration_coworker/graph/nodes/build_silver_api_model.py`

### What to Look For:
- How `openapi_spec["paths"]` is walked to create Endpoint objects
- How `openapi_spec["components"]["schemas"]` is walked to create Schema/SchemaField objects
- Entity extraction (simple heuristic: schemas with properties become entities)
- Schema linking: temporary `_request_schema_name`, `_response_schema_name` attributes

### Questions to Explore:
1. What OpenAPI fields are extracted into `Endpoint` objects?
2. How are request/response schemas linked to endpoints? (Look for `requestBody` and `responses` parsing)
3. What's the difference between a `Schema` and an `Entity`?
4. Why are schema names stored in temporary attributes (`_request_schema_name`) instead of IDs?
5. How does `EntityRelationship` detection work? (Hint: looks for `$ref` in schema fields)

---

## Step 6: Key Node – understand_task (Task Normalization)

### Files to Open:
- `src/integration_coworker/graph/nodes/understand_task.py`

### What to Look For:
- Task slug normalization (lowercase, underscores only)
- Constraint detection (idempotency, webhooks, max_latency)
- Target operation matching (finds relevant endpoints from task description)

### Questions to Explore:
1. How is `task_slug` created from a natural language task description?
2. What heuristics determine `idempotency_required=True`? (Hint: "create" keyword)
3. How are `input_entities` and `output_entities` inferred?
4. What's stored in `constraints["extra"]["target_operations"]`, and why is it useful?
5. Why does this node create `IntegrationTask` instead of `align_task_with_kg`?

---

## Step 7: Key Node – align_task_with_kg (Template Matching)

### Files to Open:
- `src/integration_coworker/graph/nodes/align_task_with_kg.py`

### What to Look For:
- `WORKFLOW_TEMPLATES` dict: in-memory template storage
- Template matching by `(provider_code, task_slug)` tuple
- Creation of `IntegrationFlowNode` and `IntegrationFlowEdge` from template steps

### Questions to Explore:
1. What providers and tasks have templates defined in `WORKFLOW_TEMPLATES`?
2. How does the node handle unknown tasks (no template match)?
3. What's the structure of a workflow template? (node_key, node_type, position, edges)
4. Why is this an in-memory dict instead of a database query? (Hint: M4 scope)
5. How would you add a new provider template? (Walk through the dict structure)

---

## Step 8: Key Node – plan_integration_flow (Validation & Bindings)

### Files to Open:
- `src/integration_coworker/graph/nodes/plan_integration_flow.py`

### What to Look For:
- Flow structure validation (exactly 1 start, ≥1 end, connectivity, position ordering)
- `EndpointBinding` creation for api_call nodes
- Endpoint matching using `target_operations` from understand_task

### Questions to Explore:
1. What rules does the node enforce about workflow structure?
2. How are `EndpointBinding` objects created, and what fields do they have?
3. Why are `request_mapping` and `response_mapping` empty dicts in M4?
4. How does the node match an api_call node to a specific endpoint?
5. What happens if the flow structure is invalid? (Trace the ValueError)

---

## Step 9: Key Node – generate_code_and_tests (Code Generation)

### Files to Open:
- `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

### What to Look For:
- Template-based generation (not LLM) for predictability
- 3 artifact types: client, flow, test
- References to `IntegrationHttpClient` and exception types

### Questions to Explore:
1. What does the generated client class look like? (Read the template code)
2. How does the flow function implement the workflow nodes?
3. What's included in the generated test file?
4. Why does generated code import from `integration_coworker.runtime.*`?
5. How would you customize the templates for a different framework? (Hint: RepoProfile conventions)

---

## Step 10: Key Node – persist_results (Database Writes)

### Files to Open:
- `src/integration_coworker/graph/nodes/persist_results.py`
- `src/integration_coworker/persistence/db.py`

### What to Look For:
- **persist_results**: INSERT OR IGNORE for all Silver/Gold tables, ID backfilling
- **db.py**: Schema initialization (CREATE TABLE IF NOT EXISTS), connection management

### Questions to Explore:
1. What exactly does `persist_results` write to the DB? (List the tables)
2. How does ID backfilling work? (Follow the `SELECT id` queries after INSERT)
3. What happens in dry_run mode? (Look for `options.dry_run` check)
4. How does the node ensure idempotency? (Hint: INSERT OR IGNORE, UNIQUE constraints)
5. Why is `persist_results` the ONLY node that writes to the DB?
6. What foreign key relationships exist between tables?

---

## Step 11: Domain Models – Silver & Gold

### Files to Open:
- `src/integration_coworker/domain/models.py`

### What to Look For:
- **Silver models**: SpecDocument, Endpoint, Schema, SchemaField, Entity, EndpointParameter, EntityRelationship
- **Gold models**: IntegrationTask, IntegrationFlowNode, IntegrationFlowEdge, EndpointBinding, Policy, CodeArtifact
- Dataclass fields, types, defaults

### Questions to Explore:
1. What's the difference between Silver and Gold models conceptually?
2. Why are all `id` fields `Optional[int]` with `None` as default?
3. What fields does `IntegrationTask` have, and what do they mean?
4. How do `IntegrationFlowNode` and `IntegrationFlowEdge` represent a workflow graph?
5. What's stored in `EndpointBinding.request_mapping` and `response_mapping`? (M4: empty, future: field mappings)
6. Why does `CodeArtifact` have `rel_path` instead of `absolute_path`?

---

## Step 12: Repo Integration – Profiles & Placement

### Files to Open:
- `src/integration_coworker/repo/models.py`
- `src/integration_coworker/repo/profiles.py`

### What to Look For:
- `RepoProfile` dataclass: archetype, conventions, layout_hints, integration_hooks
- `SUBATOMIC_MOCK_PROFILE`: FastAPI service configuration
- Other profiles: FASTAPI_PROFILE, NEXTJS_APP_ROUTER_PROFILE

### Questions to Explore:
1. What fields does `RepoProfile` have, and what are they used for?
2. What's the difference between `archetype` and `framework`?
3. How do `layout_hints` control file placement? (Look at `clients_dir`, `workflows_dir`, `tests_dir`)
4. What are `integration_hooks` used for? (Hint: router/settings marker insertion)
5. How would you define a new profile for Django? (Study the DJANGO_REST_PROFILE structure)

---

## Step 13: Repo Integration – Mock Retriever & Markers

### Files to Open:
- `src/integration_coworker/repo/mock_github.py`
- `src/integration_coworker/repo/helpers.py`

### What to Look For:
- **mock_github.py**: `MockedGithubRepoRetriever` class, `add_file()`, `export_markdown()`
- **helpers.py**: `upsert_block_between_markers()`, `generate_router_block()`, `generate_settings_block()`

### Questions to Explore:
1. How does `MockedGithubRepoRetriever` store files in memory?
2. What does `export_markdown()` produce, and why is it useful for RAG?
3. How do router/settings marker blocks get updated? (Trace `upsert_block_between_markers`)
4. What makes `upsert_block_between_markers()` idempotent?
5. How does `generate_router_block()` construct the FastAPI router registration code?

---

## Step 14: Repo Integration Nodes

### Files to Open:
- `src/integration_coworker/graph/nodes/attach_repo_context.py`
- `src/integration_coworker/graph/nodes/analyze_repo_layout.py`
- `src/integration_coworker/graph/nodes/apply_repo_integration_changes.py`

### What to Look For:
- **attach_repo_context**: Profile inference, repo scanning
- **analyze_repo_layout**: Markdown context generation
- **apply_repo_integration_changes**: RepoChangeSet creation, file path construction

### Questions to Explore:
1. How does `attach_repo_context` infer a RepoProfile if none is provided?
2. What's stored in `state.repo_markdown_context`, and who consumes it?
3. How does `analyze_repo_layout` use `layout_hints` to describe file placement?
4. What's in a `RepoChangeSet`, and how does it differ from just writing files directly?
5. How does `apply_repo_integration_changes` construct file paths for generated code?

---

## Step 15: Runtime Components – HTTP Client

### Files to Open:
- `src/integration_coworker/runtime/http_client.py`
- `src/integration_coworker/runtime/exceptions.py`

### What to Look For:
- `IntegrationHttpClient` class: httpx wrapper, auth injection, auto-retry
- Exception hierarchy: IntegrationError, TransientIntegrationError, AuthIntegrationError

### Questions to Explore:
1. How does `IntegrationHttpClient` inject the Authorization header?
2. What errors does it retry automatically? (Hint: 429, 5xx, timeouts)
3. What errors does it NOT retry? (Hint: 401, 403)
4. Why are there 3 different exception types instead of just one?
5. How does generated client code use `IntegrationHttpClient`? (Look at generate_code_and_tests templates)

---

## Step 16: Testing – Read What Tests Assert

### Files to Open & Run:
- `tests/test_end_to_end_integration.py`
- `tests/test_m4_persistence.py`
- `tests/runtime/test_integration_http_client.py`

### What to Do:
1. **Read the test code** – understand what each test validates
2. **Run individual tests**:
   ```bash
   PYTHONPATH=src .venv/bin/python -m pytest tests/test_end_to_end_integration.py::test_end_to_end_dry_run -v
   PYTHONPATH=src .venv/bin/python -m pytest tests/test_m4_persistence.py::test_persistence_writes_to_database -v
   PYTHONPATH=src .venv/bin/python -m pytest tests/runtime/test_integration_http_client.py::test_successful_get_request -v
   ```
3. **Study the assertions** – what do they prove about the system?

### Questions to Explore:
1. What does `test_end_to_end_dry_run` validate about the full workflow?
2. How does `test_persistence_writes_to_database` confirm IDs are backfilled?
3. What does `test_successful_get_request` prove about the HTTP client?
4. How do tests handle database isolation? (Hint: look at `tests/conftest.py`)
5. What's the difference between unit tests (individual nodes) and integration tests (full graph)?

---

## Step 17: Configuration & LLM Settings

### Files to Open:
- `src/integration_coworker/config/models.yaml`
- `src/integration_coworker/config/__init__.py`

### What to Look For:
- LLM task types: planning, extraction, codegen, summarization
- Model selection: gpt-4.1 vs. gpt-4o-mini
- Temperature settings per task type
- Embedding configuration

### Questions to Explore:
1. Why are different models used for different tasks?
2. Why is temperature=0.0 for extraction but 0.2 for planning?
3. How does `get_llm_config(task_type)` load configuration?
4. What's the embedding dimensionality, and why does it matter? (Hint: pgvector compatibility)
5. Where in the codebase are these configs actually used? (Hint: nodes that call LLMs)

---

## Step 18: Validation & Error Handling

### Files to Open:
- `src/integration_coworker/graph/nodes/validate_integration_design.py`
- `src/integration_coworker/graph/nodes/handle_error.py`
- `src/integration_coworker/graph/nodes/build_report.py`

### What to Look For:
- **validate_integration_design**: Flow structure checks, binding consistency
- **handle_error**: `plan["failed"]=True` flag setting
- **build_report**: Markdown generation, error section

### Questions to Explore:
1. What flow structure rules does `validate_integration_design` enforce?
2. How does it distinguish warnings from critical failures?
3. What does `handle_error` do besides set `plan["failed"]=True`?
4. How does `build_report` format errors in the markdown output?
5. Why does the graph still run `persist_results` and `build_report` after an error?

---

## Step 19: Test Fixtures & Data Quality

### Files to Open:
- `tests/fixtures/mock_payments_openapi.yaml`
- `tests/conftest.py`

### What to Look For:
- **mock_payments_openapi.yaml**: Sample OpenAPI spec structure
- **conftest.py**: Global pytest fixtures (reset_db)

### Questions to Explore:
1. What endpoints are defined in the mock_payments spec?
2. What schemas are defined, and what fields do they have?
3. How does the `reset_db()` fixture ensure test isolation?
4. Why is `autouse=True` important for the fixture?
5. How does the fixture handle database locking errors? (Look for retry logic)

---

## Step 20: Putting It All Together

### Exercise: Trace a Full Run
1. Start with: `design_and_generate_integration(spec_refs=["mock_payments_openapi.yaml"], task_description="Create checkout session", options=IntegrationOptions(dry_run=True))`
2. Trace execution through:
   - `plan_run` → sets run_id, provider_code, plan["use_repo"]=False
   - `ingest_spec` → loads YAML, computes SHA256
   - `build_silver_api_model` → extracts 2 endpoints, 2 schemas
   - `understand_task` → creates IntegrationTask with task_slug="create_checkout_session"
   - `align_task_with_kg` → matches template, creates 5 nodes + 4 edges
   - `generate_code_and_tests` → produces 3 CodeArtifact objects
   - `persist_results` → dry_run=True, logs "would persist" summary
   - `build_report` → generates markdown report
3. Final output: `IntegrationResult` with run_id, task, code_artifacts, report_markdown

### Questions to Ask Yourself:
1. What state fields changed after each node?
2. Where did the provider_code come from?
3. How was the workflow template selected?
4. Why weren't repo nodes executed?
5. What would change if dry_run=False?

---

## Bonus: Ask Copilot Deeper Questions

Now that you've walked through the code, try asking Copilot:

- "Show me how endpoint_bindings are created in plan_integration_flow"
- "What's the difference between Silver and Gold models in this system?"
- "How does persist_results ensure idempotency?"
- "Walk me through the conditional routing for repo integration"
- "How would I add a new provider template?"
- "What happens if validation fails after generate_code_and_tests?"
- "Explain the EntityRelationship field naming issue that was fixed in M4"

Copilot will answer by referencing the actual code you've just studied!

---

## Summary Checklist

After completing this tour, you should be able to:

- [ ] Explain the entry points (CLI vs. API)
- [ ] Describe what's in WorkflowState and how it flows through nodes
- [ ] Trace the graph execution path for a dry-run
- [ ] Explain the difference between Silver and Gold models
- [ ] Describe how workflow templates are matched
- [ ] Understand how persist_results writes to the database
- [ ] Explain how repo integration uses profiles and markers
- [ ] Describe how IntegrationHttpClient handles retries
- [ ] Know where to find key tests and what they validate
- [ ] Be able to add a new provider or workflow template

**Time Estimate**: 60-90 minutes for full tour, 30-45 minutes for highlights only

**Next Steps**: Read `docs/M4_SHIP_CHECKLIST.md` for completeness verification, or `docs/M4_EXECUTIVE_SUMMARY.md` for demo preparation.
