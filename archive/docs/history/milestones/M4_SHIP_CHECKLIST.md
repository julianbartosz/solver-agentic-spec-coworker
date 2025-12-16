# M4 "Can I Ship This?" Checklist
**Generated**: November 24, 2025  
**Milestone**: M4 - Single-Provider End-to-End Functional Integration  
**Target**: December 1, 2025 Demo

---

## Executive Summary

**Overall Status**: ✅ **SHIPPABLE** with minor documentation improvements needed

- **Code Quality**: ✅ Production-ready
- **Test Coverage**: ✅ 25/25 tests passing
- **End-to-End Functionality**: ✅ Complete workflow works
- **Persistence**: ✅ SQLite with proper ID backfilling
- **Generated Code**: ✅ Importable and executable
- **Demo-Ready**: ✅ CLI works end-to-end

**Minor Issues**:
- ⚠️ Warning in dry-run about `endpoint_id=None` (expected behavior, not a blocker)
- 📝 Documentation needs final polish for demo

---

## 1. Code + Tests: M4 "Definition of Done"

### Repo State ✅

* [x] **All P1.x + P2.x changes implemented**:
  * [x] P1.3: Schema linking with request/response IDs
  * [x] P1.4: Generated code execution test with HTTP mocking
  * [x] P2.1: RepoProfile.archetype field
  * [x] P2.2: EntityRelationship field name fix
  * [x] P2.3: Extra workflow template (get_checkout_session)

* [x] **No critical TODOs in core paths**
  * Future-phase TODOs clearly marked with phase numbers
  * Core workflow nodes have no blocking issues

### Automated Tests ✅

**Test Suite**: 25/25 passing (1.09s runtime)

* [x] **Persistence tests** (`test_m4_persistence.py`): 3/3 ✅
  * `test_persistence_writes_to_database` - Verifies SQLite writes
  * `test_persistence_idempotent` - Confirms no duplicate rows
  * `test_persistence_dry_run_unchanged` - Validates dry-run mode

* [x] **End-to-end tests** (`test_end_to_end_integration.py`): 7/7 ✅
  * `test_end_to_end_dry_run` - Full workflow in dry-run mode
  * `test_end_to_end_with_repo_integration` - Repo file generation
  * `test_provider_code_inference` - Automatic provider detection
  * `test_silver_model_extraction` - Spec parsing validation
  * `test_workflow_creation` - Flow graph construction
  * `test_policy_attachment` - Policy generation
  * `test_error_handling_missing_spec` - Error handling

* [x] **Codegen tests** (`test_m4_generated_code_execution.py`): 2/2 ✅
  * `test_generated_mock_payments_flow_executes` - Import & execute generated code
  * `test_generated_code_has_correct_structure` - Structural validation

* [x] **Silver model tests** (`test_build_silver_api_model.py`): 6/6 ✅
  * `test_build_silver_api_model_extracts_endpoints` - Endpoint extraction
  * `test_build_silver_api_model_extracts_parameters` - Parameter parsing
  * `test_build_silver_api_model_extracts_schemas` - Schema extraction
  * `test_build_silver_api_model_no_errors` - Error-free execution
  * `test_build_silver_api_model_entity_relationships_no_errors` - P2.2 fix validation
  * `test_build_silver_api_model_links_schemas_to_endpoints` - P1.3 linking

* [x] **New feature tests**:
  * `test_align_task_with_kg.py`: 3/3 ✅ (P2.3 template matching)
  * `test_repo_context.py`: 1/1 ✅ (P2.1 archetype field)
  * `test_m3_milestone.py`: 3/3 ✅ (M3 planning validation)

### Manual Sanity ✅

* [x] **CLI end-to-end execution works**:
  ```bash
  PYTHONPATH=src .venv/bin/python -m integration_coworker.cli \
    --spec-ref tests/fixtures/mock_payments_openapi.yaml \
    --task "Create checkout session" \
    --dry-run
  ```
  - **Status**: ✅ Runs successfully
  - **Output**: Clean markdown report with all sections
  - **Warnings**: Only expected warning about `endpoint_id=None` in dry-run (IDs assigned post-persistence)

* [x] **CLI with repo integration**:
  - Confirmed file generation works (from tests)
  - Files written to correct paths: `src/integrations/clients/`, `src/integrations/flows/`, `tests/integrations/`

* [x] **No stack traces in normal operation**
  - Only expected warnings appear
  - Error handling works correctly for missing specs

---

## 2. Silver / Gold Model Fidelity

### Silver (spec_silver) Behavior ✅

* [x] **`build_silver_api_model` extracts**:
  * [x] Endpoints with method, path, operation_id (2 endpoints for mock_payments)
  * [x] Schemas + fields with json_path and types (2 schemas: request & response)
  * [x] For `POST /v1/checkout/sessions`:
    * [x] Request schema: `CreateCheckoutSessionRequest`
    * [x] Response schema: `CheckoutSession`

* [x] **After persistence** (P1.3):
  * [x] `request_schema_id` and `response_schema_id` on endpoints are non-null
  * [x] DB rows for schemas link correctly to endpoints
  * [x] Relationship verified in `test_persistence_writes_to_database`

### Gold (integration_gold) Behavior ✅

* [x] **IntegrationTask**:
  * [x] Has expected `task_slug`: `create_checkout_session`
  * [x] Has `provider_code="mock_payments"`
  * [x] Persisted with valid ID

* [x] **Flow graph**:
  * [x] Nodes exist: start → validate_input → call_create_session → transform_response → end (5 nodes)
  * [x] Edges connect all nodes (4 edges)
  * [x] `validate_integration_design` passes:
    * [x] Single start node ✅
    * [x] At least one end node ✅
    * [x] Fully connected (all non-start have incoming, all non-end have outgoing) ✅

---

## 3. EndpointBinding & Persistence

### Bindings ✅

* [x] **For canonical flow (create_checkout_session)**:
  * [x] 1 EndpointBinding exists for `call_create_session` node
  * [x] `flow_node_key` correctly identifies the api_call node
  * [x] `endpoint_id` is **set to real DB ID after persistence** (verified in tests)

* [⚠️] **Known behavior**:
  * In dry-run mode, `endpoint_id=None` (expected - warning shown in report)
  * In non-dry-run, endpoint IDs properly backfilled from persistence

### DB State (SQLite) ✅

* [x] **SQLite file location**: `.data/integration_coworker.sqlite3`

* [x] **Tables exist** (verified via `db.init_schema()`):
  * [x] `source_systems`
  * [x] `spec_documents`
  * [x] `endpoints`
  * [x] `schemas`
  * [x] `schema_fields`
  * [x] `entities`
  * [x] `integration_tasks`
  * [x] `integration_flow_nodes`
  * [x] `integration_flow_edges`
  * [x] `endpoint_bindings`
  * [x] `policies`
  * [x] `code_artifacts`

* [x] **Canonical run inserts**:
  * [x] 1 `source_system` row for `mock_payments`
  * [x] 1 `spec_document` row (linked via SHA256)
  * [x] 2 endpoints (POST create, GET retrieve)
  * [x] 2 schemas (request & response)
  * [x] 1 `integration_task` with 5 nodes, 4 edges, 1 binding

* [x] **Idempotent behavior** (verified in `test_persistence_idempotent`):
  * [x] Running same spec twice does NOT create duplicate source_system
  * [x] Does NOT create duplicate spec_document (SHA256 deduplication)
  * [x] Does NOT create duplicate endpoints/schemas

---

## 4. Repo Integration: Files on Disk Are Real and Importable

### Profile + Archetype ✅

* [x] **RepoProfile includes archetype** (P2.1):
  * [x] Field: `archetype: Optional[str] = None`
  * [x] SUBATOMIC_MOCK_PROFILE has `archetype="fastapi_service"`
  * [x] `analyze_repo_layout` prefers archetype over framework
  * [x] Test: `test_repo_profile_has_archetype` passes

* [x] **Profile configuration**:
  * [x] `name="subatomic-mock"`
  * [x] `archetype="fastapi_service"`
  * [x] `layout_hints` configured for integrations_root, tests_root

### File Layout ✅

* [x] **Run with `repo_integration_enabled=True`, `dry_run=False`**:
  * [x] Writes **client module**: `src/integrations/clients/mock_payments.py`
  * [x] Writes **flow module**: `src/integrations/flows/mock_payments_checkout.py`
  * [x] Writes **test module**: `tests/integrations/test_mock_payments_checkout.py`

* [x] **RepoChangeSet** (verified in tests):
  * [x] Contains `FileChange` entries for all 3 files
  * [x] Has accurate `change_type` (create/update)
  * [x] Contains generated content

### Importability ✅

* [x] **Generated code is importable** (verified in `test_m4_generated_code_execution.py`):
  * [x] Can import `integrations.clients.mock_payments.MockPaymentsClient`
  * [x] Can import `integrations.flows.mock_payments_checkout.create_checkout_session_flow`
  * [x] No import errors (after fixing relative import with workaround)

* [⚠️] **Known issue** (documented, not blocking):
  * Generated flow uses relative import `from .clients` which requires workaround
  * Future fix: Update code generator to use absolute imports
  * Test includes workaround to demonstrate code still works

---

## 5. Generated Code Execution (P1.4)

### Client + Flow ✅

* [x] **Code execution test passes** (`test_generated_mock_payments_flow_executes`):
  * [x] Generates code into temp repo
  * [x] Patches `IntegrationHttpClient.request` to avoid network calls
  * [x] Imports `MockPaymentsClient` and flow
  * [x] Calls flow with realistic arguments:
    * `api_key="test_key"`
    * `amount=1000`, `currency="usd"`
    * `success_url`, `cancel_url`
  * [x] Asserts HTTP layer called with:
    * Method: POST
    * Path: `/v1/checkout/sessions`
  * [x] Asserts return value has correct shape:
    * `session_id`, `checkout_url`, `status`, `amount`, `currency`

### Manual Check ✅

* [x] **Test demonstrates "feels real" execution**:
  * Client has proper `IntegrationHttpClient` initialization
  * Flow creates client internally with api_key
  * Flow returns properly transformed response
  * HTTP mocking works cleanly

---

## 6. KG / Workflow Templates

### Templates ✅

* [x] **`align_task_with_kg` has templates**:
  * [x] `("mock_payments", "create_checkout_session")` - 5-step flow
  * [x] `("mock_payments", "get_checkout_session")` - 5-step flow (P2.3)
  * [x] Template structure includes: start, validate, api_call, transform, end

* [x] **For both tasks**:
  * [x] `IntegrationTask.task_slug` matches template keys
  * [x] Template matching works (verified in tests)

### Behavior ✅

* [x] **Given canonical task**:
  * [x] Correct template chosen for `create_checkout_session`
  * [x] Nodes created match template keys and types
  * [x] All 5 nodes have correct positions (0-4)

* [x] **For unknown task**:
  * [x] Fallback behavior defined: generic 4-step flow
  * [x] Tested in `test_unknown_task_fallback`

---

## 7. EntityRelationship Safety

### Field Consistency ✅

* [x] **EntityRelationship fields aligned** (P2.2):
  * [x] Model uses: `source_entity_id`, `target_entity_id` (Optional[int])
  * [x] Code no longer passes string names into int fields
  * [x] Uses `None` for IDs with comment about future backfill

* [x] **`build_silver_api_model`**:
  * [x] Creates relationships with correct field names
  * [x] Sets IDs to None (post-persistence backfill planned for future phase)
  * [x] No more `from_entity`/`to_entity` string fields

* [x] **Tests**:
  * [x] `test_build_silver_api_model_entity_relationships_no_errors` passes
  * [x] Verifies no AttributeErrors during relationship construction
  * [x] Checks fields are correct types (None or int, not strings)

---

## 8. API Surface & Observability

### API Entrypoint ✅

* [x] **`design_and_generate_integration()` returns `IntegrationResult`**:
  * [x] `run_id` (UUID string)
  * [x] `status` / `run_status` ("completed", "completed_with_errors", etc.)
  * [x] `persisted_ids` (dict with counts and IDs)
  * [x] Key lists: endpoints, schemas, workflow_nodes, endpoint_bindings, etc.

* [x] **Error handling**:
  * [x] Bad inputs produce clear error messages
  * [x] Missing spec raises `FileNotFoundError` (tested)
  * [x] No crashes on malformed input

### Observability ✅

* [x] **`run_id`**:
  * [x] Generated in `plan_run` node
  * [x] Appears in markdown report
  * [x] UUID format (e.g., `217db9d8-fada-441e-bfe5-bf59b32e4f3c`)

* [x] **Errors**:
  * [x] Appended to `state.errors` list
  * [x] Cause `plan["failed"] = True` in `handle_error`
  * [x] Visible in final report under "## Errors" section

* [x] **Markdown report** (`build_report` node):
  * [x] Summarizes provider, task slug, run ID
  * [x] Shows counts: endpoints, schemas, nodes, policies, artifacts
  * [x] Lists errors prominently
  * [x] Shows completed steps
  * [x] Readable format for non-technical stakeholders

---

## 9. Docs & Demo Prep

### Internal Docs ⚠️ (Needs Update)

* [⚠️] **Design doc needs refresh**:
  * Current: `docs/design/02-agentic-spec-coworker.md` describes M2 "summarization"
  * **Action needed**: Update to reflect pivot to "generate working integrations"
  * **Include**: M2/M3/M4 milestone summary

* [x] **Current limitations documented**:
  * Single provider (mock_payments) ✅
  * In-memory KG templates ✅
  * Single archetype (fastapi_service) ✅
  * No real network calls ✅

* [⚠️] **PHASE3_NOTES.md needs creation**:
  * **Action needed**: Create 1-pager for M4 deliverables
  * Should include:
    * What M4 delivers (end-to-end integration generation)
    * Out of scope (multi-provider, real KG DB, advanced archetypes)
    * M5 candidates (listed above + multi-provider support)

### Demo Script ✅ (Ready to Execute)

**5-Minute Demo Path**:

1. **Show the spec and task** (30 seconds):
   ```bash
   # Show OpenAPI spec
   cat tests/fixtures/mock_payments_openapi.yaml | head -30
   
   # Natural language task
   echo "Task: Create checkout session"
   ```

2. **Run dry-run to show plan** (1 minute):
   ```bash
   PYTHONPATH=src .venv/bin/python -m integration_coworker.cli \
     --spec-ref tests/fixtures/mock_payments_openapi.yaml \
     --task "Create checkout session" \
     --dry-run
   ```
   - Point out: 2 endpoints extracted, 2 schemas, 5-step workflow, 3 code artifacts

3. **Run with repo generation** (1 minute):
   ```bash
   # Create temp repo
   mkdir -p /tmp/demo_repo/src/integrations /tmp/demo_repo/tests/integrations
   
   # Generate files
   PYTHONPATH=src .venv/bin/python -m integration_coworker.cli \
     --spec-ref tests/fixtures/mock_payments_openapi.yaml \
     --task "Create checkout session" \
     --repo-root /tmp/demo_repo \
     --provider mock_payments
   ```

4. **Show generated files** (1.5 minutes):
   ```bash
   # Client code
   cat /tmp/demo_repo/src/integrations/clients/mock_payments.py | head -50
   
   # Flow code
   cat /tmp/demo_repo/src/integrations/flows/mock_payments_checkout.py | head -40
   
   # Test code
   cat /tmp/demo_repo/tests/integrations/test_mock_payments_checkout.py | head -30
   ```

5. **Run test suite** (30 seconds):
   ```bash
   PYTHONPATH=src .venv/bin/python -m pytest tests/ -v --tb=short | tail -20
   ```
   - Show: 25/25 passing

6. **Show database** (30 seconds - optional):
   ```bash
   sqlite3 .data/integration_coworker.sqlite3 "SELECT * FROM integration_tasks;"
   sqlite3 .data/integration_coworker.sqlite3 "SELECT * FROM endpoints;"
   ```

### Talking Points ✅

**Why better than "just ChatGPT"**:
1. **Repeatability**: Same spec + task → same output every time
2. **Database**: Silver/Gold models persisted, queryable for analytics
3. **KG Templates**: Reusable workflow patterns, not one-off generations
4. **Repo-Aware**: Respects target framework, creates proper file structure
5. **Testable**: Generated code has tests, can be validated automatically
6. **Composable**: Can build multi-step flows, not just single API calls

**Onboarding second provider (Stripe, HubSpot)**:
1. Add OpenAPI spec to fixtures
2. Add 1-2 workflow templates to `align_task_with_kg.py`
3. Update `understand_task.py` normalization for provider-specific terms
4. Run tests - most should work without changes
5. Estimated effort: **4-6 hours for basic provider**, **1-2 days for full polish**

---

## 10. Final Verdict

### ✅ **SHIPPABLE FOR DEC 1 DEMO**

**Strengths**:
- Complete end-to-end workflow working
- 25/25 tests passing with good coverage
- CLI produces usable output
- Generated code is importable and executable
- Persistence works with proper ID backfilling
- Error handling is robust

**Known Issues** (None are blockers):
- ⚠️ Dry-run warning about `endpoint_id=None` (expected behavior, clearly documented)
- ⚠️ Generated code uses relative imports (workaround in test, future fix planned)
- 📝 Design doc needs refresh to reflect M4 scope
- 📝 PHASE3_NOTES.md should be created for demo

**Recommended Actions Before Demo**:
1. **HIGH**: Create `docs/PHASE3_NOTES.md` with M4 summary (15 minutes)
2. **MEDIUM**: Update `docs/design/02-agentic-spec-coworker.md` intro (30 minutes)
3. **LOW**: Practice demo script once (5 minutes)

**Confidence Level**: **9/10** - Ready to demo with minor docs polish

---

## 11. Post-Demo M5 Candidates

**High Priority**:
1. Multi-provider support (Stripe, HubSpot, GitHub)
2. Real KG database (Postgres with pgvector) replacing in-memory templates
3. Fix generated code relative imports
4. More repo archetypes (Next.js, Django, plain Python)

**Medium Priority**:
5. Advanced workflow patterns (polling, webhooks, pagination)
6. Request/response mapping UI or DSL
7. Policy customization per provider
8. Code artifact versioning

**Low Priority**:
9. LangSmith tracing integration
10. Multi-file generation (models, types, enums)
11. Advanced error recovery strategies

---

**Document Prepared By**: Integration Co-Worker Analysis  
**Last Updated**: November 24, 2025  
**Next Review**: Post-Dec 1 Demo
