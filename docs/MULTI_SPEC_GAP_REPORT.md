# Multi-Spec Gap Report

**Date:** December 1, 2025  
**Status:** AUDIT COMPLETE  
**Target Scenario:** "Create checkout session and send confirmation notification" (mock_payments + mock_notifications)

---

## 1. Executive Summary

Multi-spec **ingestion and Silver model extraction works correctly**. The gaps are in:
1. **KG alignment** - filters by single provider_code, ignoring endpoints from secondary specs
2. **Flow planning** - creates bindings only for the first target_operation
3. **Code generation** - generates a single client for the primary provider only

These gaps prevent multi-call workflows where one task orchestrates endpoints across multiple specs/providers.

---

## 2. Phase-by-Phase Analysis

### 2.1 `ingest_spec` ✅ WORKS

**Single-spec behavior:**
- Fetches content from `spec_refs[0]`, creates `SpecDocument`, chunks content

**Multi-spec behavior:**
- Iterates ALL `spec_refs`, creates `SpecDocument` for each
- Chunks each spec and tracks `chunk_index_to_spec_document_uri` mapping
- All chunks combined into `state.doc_chunks`

**Verdict:** ✅ Fully functional for multi-spec

---

### 2.2 `detect_and_parse_spec` ✅ WORKS

**Single-spec behavior:**
- Parses first `SpecDocument`, stores in `state.openapi_spec`

**Multi-spec behavior:**
- Parses ALL `spec_documents` (line 48-64)
- Tags each with `_source_uri` for tracking (line 64)
- Stores parsed specs list in `state.plan["openapi_specs"]` (line 72-74)
- Primary spec still goes to `state.openapi_spec` for backward compat

**Verdict:** ✅ Fully functional for multi-spec

---

### 2.3 `build_silver_api_model` ✅ WORKS

**Single-spec behavior:**
- Extracts endpoints, schemas, entities from `state.openapi_spec`

**Multi-spec behavior:**
- Reads `state.plan["openapi_specs"]` (line 181-182)
- Iterates ALL specs and calls `_extract_from_spec()` for each (line 194-196)
- Each endpoint/schema/entity is tagged with `_source_uri` attribute (lines 72-73, 92, 104, etc.)
- All entities merged into state lists

**Evidence from tests:** `test_multi_spec.py::test_multi_spec_endpoints_extraction` passes - 4 endpoints from 2 specs

**Verdict:** ✅ Fully functional for multi-spec

---

### 2.4 `understand_task` ⚠️ PARTIAL

**Single-spec behavior:**
- Builds prompt from `state.endpoints` and `state.entities`
- Produces `IntegrationTask` with `target_operations` list

**Multi-spec behavior:**
- Uses ALL endpoints in prompt (line 13-16: `for ep in state.endpoints[:10]`)
- LLM/heuristics CAN identify target_operations from any spec
- BUT: `IntegrationTask` has single `provider_code` field

**Gap:** `IntegrationTask.provider_code` is scalar, not a list. When the task spans two providers (payments + notifications), only the primary provider is recorded.

**Impact:** Low - downstream code can access endpoints via `_source_uri` attribute

---

### 2.5 `align_task_with_kg` ❌ SINGLE-PROVIDER FILTER

**Single-spec behavior:**
- Queries KG with `provider=state.provider_code` (line 162)
- Returns workflow templates matching that provider

**Multi-spec behavior - BROKEN:**
```python
# Line 161-164
provider = state.provider_code or "unknown"  # ← Single provider!
task_slug = state.integration_task.task_slug
...
candidate_templates = _query_kg_templates(
    provider=provider,  # ← Only queries templates for ONE provider
    ...
)
```

**Gap:** 
- KG query uses single `provider` parameter
- No mechanism to query templates that span multiple providers
- Fallback templates (`_LEGACY_WORKFLOW_TEMPLATES`) are keyed by `(provider, task_slug)` tuple

**Impact:** HIGH - templates for notifications will never be found when primary is payments

---

### 2.6 `plan_integration_flow` ❌ SINGLE-BINDING LIMITATION

**Single-spec behavior:**
- Creates `EndpointBinding` for each `api_call` node in workflow
- Matches endpoint from `target_operations[0]` only (line 101-103)

**Multi-spec behavior - BROKEN:**
```python
# Lines 101-103
if target_operations:
    # Try to match first target operation to this node
    target_op = target_operations[0] if target_operations else None
```

**Gaps:**
1. Only uses `target_operations[0]` - ignores additional operations
2. No logic to create multiple `api_call` nodes for multi-step flows
3. `EndpointBinding` lacks `_source_uri` / `spec_document_id` tracking
4. No mechanism to coordinate bindings across multiple providers

**Impact:** HIGH - even if understand_task identifies two operations, only one binding is created

---

### 2.7 `generate_code_and_tests` ❌ SINGLE-CLIENT GENERATION

**Single-spec behavior:**
- Generates client for `state.provider_code`
- Generates flow calling that single client
- Generates tests for that flow

**Multi-spec behavior - BROKEN:**
```python
# Line 82-83
provider_code = state.provider_code or "unknown"
...
client_module = derive_client_module_name(provider_code)  # ← One client only
```

**Gaps:**
1. Generates ONE client per run (primary provider only)
2. Flow code assumes single client instantiation
3. No orchestrator pattern to call multiple clients
4. No mechanism to identify which endpoints need separate clients

**Impact:** HIGH - cannot generate code that calls both payments and notifications APIs

---

## 3. Summary of Gaps

| Node | Status | Gap Description |
|------|--------|-----------------|
| `ingest_spec` | ✅ | None |
| `detect_and_parse_spec` | ✅ | None |
| `build_silver_api_model` | ✅ | None |
| `understand_task` | ⚠️ | `IntegrationTask.provider_code` is scalar |
| `align_task_with_kg` | ❌ | Queries KG for single provider only |
| `plan_integration_flow` | ❌ | Uses only `target_operations[0]`; no multi-binding creation |
| `generate_code_and_tests` | ❌ | Generates single client for primary provider |

---

## 4. Code Locations to Fix

### 4.1 Comments/Tests Claiming "Not in V1"

| File | Line | Text |
|------|------|------|
| `tests/test_trusted_demo_scenarios.py` | 264 | `Note: Multi-spec workflow planning is a known V1 limitation.` |
| `tests/test_trusted_demo_scenarios.py` | 268 | `@pytest.mark.xfail(reason="Multi-spec flow planning is a known V1 limitation")` |
| `tests/test_trusted_demo_scenarios.py` | 328 | `@pytest.mark.xfail(reason="Multi-spec flow planning is a known V1 limitation")` |
| `src/integration_coworker/api/entrypoint.py` | 26 | `spec_refs: List of spec URLs or file paths (v1: exactly one).` |

---

## 5. Desired V1 Behavior for Scenario 2

**Task:** "Create checkout session and send confirmation notification"  
**Specs:** mock_payments + mock_notifications  
**Provider:** None explicit (or "multi" / composite key)

### Expected Workflow Nodes:
```
start → validate_input → call_checkout_api → call_notification_api → end
```

### Expected Endpoint Bindings:
| Binding | Flow Node Key | Endpoint | Source URI |
|---------|---------------|----------|------------|
| 1 | `call_checkout_api` | POST /v1/checkout/sessions | mock_payments_openapi.yaml |
| 2 | `call_notification_api` | POST /v1/notifications | mock_notifications_openapi.yaml |

### Expected Code Artifacts:
1. `mock_payments_client.py` - client for payments API
2. `mock_notifications_client.py` - client for notifications API
3. `create_checkout_and_notify_flow.py` - orchestrator that:
   - Instantiates both clients
   - Calls checkout API
   - Extracts response data
   - Calls notification API with extracted data
4. `test_create_checkout_and_notify_flow.py` - tests for the orchestrator

---

## 6. Implementation Plan

### Phase A: Multi-Endpoint Flow Planning (align_task_with_kg + plan_integration_flow)

#### A.1 Extend `align_task_with_kg` for Multi-Provider Templates

**File:** `src/integration_coworker/graph/nodes/align_task_with_kg.py`

**Changes:**
1. Add multi-provider template lookup:
   ```python
   def _query_multi_provider_templates(
       provider_codes: List[str],  # All distinct providers from endpoints
       task_description: str,
       known_endpoints: List[str],
   ) -> List[dict]:
   ```
2. Build workflow with multiple `api_call` nodes when task description contains conjunctions ("and", ",")
3. Fallback template for multi-provider: `start → validate → api_call_1 → api_call_2 → end`

#### A.2 Extend `plan_integration_flow` for Multiple Bindings

**File:** `src/integration_coworker/graph/nodes/plan_integration_flow.py`

**Changes:**
1. Iterate ALL `target_operations`, not just `[0]`:
   ```python
   for i, api_node in enumerate(api_call_nodes):
       target_op = target_operations[i] if i < len(target_operations) else None
   ```
2. Add `_source_uri` or `_provider_code` to `EndpointBinding`
3. Match endpoints across ALL endpoints, not filtered by primary provider

#### A.3 Update `understand_task` to Identify Multi-Endpoint Tasks

**File:** `src/integration_coworker/graph/nodes/understand_task.py`

**Changes:**
1. Heuristic: detect "and" / "," / "then" in task description
2. LLM prompt: explicitly ask for multiple `target_operations` when task is compound
3. Store distinct provider codes: `constraints["extra"]["provider_codes"]`

---

### Phase B: Multi-Client Code Generation (generate_code_and_tests)

**File:** `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

**Changes:**
1. Group endpoint_bindings by provider (via `_source_uri`):
   ```python
   providers = {}
   for binding in state.endpoint_bindings:
       uri = getattr(binding._matched_endpoint, '_source_uri', None)
       provider = extract_provider_from_uri(uri) or state.provider_code
       providers.setdefault(provider, []).append(binding)
   ```
2. Generate separate client for each provider
3. Generate orchestrator flow that:
   - Imports all client classes
   - Calls them in sequence per workflow_nodes order
   - Passes data between calls (response → next request)
4. Generate test that mocks all clients

---

### Phase C: Test Updates

**File:** `tests/test_trusted_demo_scenarios.py`

1. Remove `@pytest.mark.xfail` from:
   - `test_multi_endpoint_full_graph`
   - `test_multi_endpoint_endpoint_bindings`

2. Update assertions:
   ```python
   # Should have 2 endpoint bindings
   assert len(result.endpoint_bindings) == 2
   
   # Should have 4+ code artifacts (2 clients + flow + test)
   assert len(result.code_artifacts) >= 4
   artifact_types = Counter(a.artifact_type for a in result.code_artifacts)
   assert artifact_types["client"] == 2
   ```

3. Add snapshot verification for multi-endpoint scenario

**File:** `tests/test_multi_spec.py`

Add new tests:
- `test_multi_spec_endpoint_bindings_span_specs`
- `test_multi_spec_code_artifacts_include_multiple_clients`

---

## 7. Effort Estimate

| Phase | Effort | Files Modified |
|-------|--------|----------------|
| A.1 align_task_with_kg | 2-3 hours | 1 |
| A.2 plan_integration_flow | 2-3 hours | 1 |
| A.3 understand_task | 1-2 hours | 1 |
| B. generate_code_and_tests | 3-4 hours | 1 |
| C. Test updates | 1-2 hours | 2 |
| **Total** | **9-14 hours** | **6** |

---

## 8. Risks and Mitigations

1. **Risk:** Breaking existing single-spec tests
   **Mitigation:** All changes are additive; single-provider path unchanged

2. **Risk:** LLM prompt changes degrade single-spec quality
   **Mitigation:** Only modify prompts when task contains conjunctions

3. **Risk:** Generated multi-client code has circular imports
   **Mitigation:** Each client in separate module; orchestrator imports both

---

## 9. Next Steps

1. **Approve this plan** - confirm scope and approach
2. **Implement Phase A** - multi-binding creation
3. **Implement Phase B** - multi-client codegen
4. **Implement Phase C** - test updates and remove xfails
5. **Update docs** - remove "v1: exactly one" language

---

**End of Multi-Spec Gap Report**
