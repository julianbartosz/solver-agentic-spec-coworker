# Implementation Plan: Behavior Bounds Fixes

**Created:** Auto-generated from behavior bounds analysis  
**Priority:** P0 (Critical) → P1 (High) → P2 (Medium)

---

## Executive Summary

This plan addresses **3 confirmed gaps** discovered during behavior bounds exploration:

| ID | Priority | Issue | Impact |
|----|----------|-------|--------|
| FIX-001 | P0 | Multi-step workflow DAG validation fails | Critical - blocks multi-endpoint workflows |
| FIX-002 | P1 | Policies not exposed in IntegrationResult | Moderate - incomplete API |
| FIX-003 | P2 | Embedding unavailability classified as error | Minor - misleading diagnostics |

---

## FIX-001: Multi-Step Workflow DAG Validation (P0 Critical)

### Problem Statement

When a task like "create a user and send a notification" is detected as multi-step:
1. `_detect_multi_step_pattern()` correctly returns `(True, ["create", "send"])`
2. `_infer_multi_endpoint_workflow()` generates steps **without** start/end nodes
3. `_validate_dag_structure()` rejects the DAG with: `"Flow must have exactly one start node, found 0"`

### Root Cause Analysis

**Working single-endpoint pattern** (`_infer_workflow_from_endpoint`):
```python
# Line 548 - START added first
steps = [{"key": "start", "type": "start", "label": "Start"}]
# ... middle steps ...
# Line 687 - END added last
steps.append({"key": "end", "type": "end", "label": "Return Result"})
```

**Broken multi-endpoint pattern** (`_infer_multi_endpoint_workflow`):
```python
# Line 477 - NO start node
steps: List[dict] = []
# Line 501-520 - Uses wrong type values
step = {
    "id": step_id,                    # Wrong: should be "key"
    "type": step_type,                # Values like "api_call_create" instead of "api_call"
    ...
}
# Line 522-527 - return_result instead of end
steps.append({
    "id": "return_response",          # Wrong: should be "key"
    "type": "return_result",          # Wrong: should be "end"
    ...
})
```

**DAG validator expectations** (`plan_integration_flow.py`):
```python
# Line 52 - Checks for node_type == "start"
start_nodes = [n for n in nodes if n.node_type == "start"]
# Line 57 - Checks for node_type == "end"
end_nodes = [n for n in nodes if n.node_type == "end"]
```

### Files to Modify

| File | Changes | Lines |
|------|---------|-------|
| `src/integration_coworker/graph/nodes/align_task_with_kg.py` | Fix `_infer_multi_endpoint_workflow()` | 458-527 |

### Implementation Details

#### Option A: Minimal Fix (RECOMMENDED)

Add start/end nodes and normalize keys/types in `_infer_multi_endpoint_workflow`:

```python
def _infer_multi_endpoint_workflow(
    endpoints: List[Endpoint],
    task_description: str,
    action_sequence: List[str],
) -> List[dict]:
    """
    Infer a multi-step workflow from multiple endpoints.
    
    Creates a workflow with multiple api_call nodes connected by data flow.
    Now includes proper start/end nodes for DAG validation.
    """
    # START NODE - required for DAG validation
    steps: List[dict] = [
        {"key": "start", "type": "start", "label": "Start"}
    ]
    
    prev_step_key: Optional[str] = "start"  # Connect first step to start

    for i, action in enumerate(action_sequence):
        endpoint = _find_endpoint_for_action(endpoints, action, task_description)

        if endpoint is None:
            logger.warning(f"No endpoint found for action '{action}' in multi-step flow")
            continue

        step_key = f"step_{i+1}_{action}"

        # NORMALIZE: Use canonical types from _infer_workflow_from_endpoint
        # Map action-specific types to DAG-valid types
        if action in ("validate", "check"):
            step_type = "validation"  # Not "validate_input"
        else:
            step_type = "api_call"    # Normalize all API calls

        step = {
            "key": step_key,          # Use "key" not "id"
            "type": step_type,
            "action": action,
            "label": f"{action.capitalize()} via {endpoint.method} {endpoint.path}",
            "endpoint_path": endpoint.path,
            "endpoint_method": endpoint.method,
            "endpoint_operation_id": endpoint.operation_id,
            "depends_on": [prev_step_key] if prev_step_key else [],
            "description": f"{action.capitalize()} via {endpoint.method} {endpoint.path}",
        }

        steps.append(step)
        prev_step_key = step_key

    # END NODE - required for DAG validation
    if len(steps) > 1:  # Only if we have steps beyond start
        steps.append({
            "key": "end",
            "type": "end",
            "label": "Return Result",
            "depends_on": [prev_step_key] if prev_step_key else [],
            "description": "Return final result from multi-step workflow",
        })

    return steps
```

#### Option B: Extract Common Node Builder (More Refactoring)

Create a shared helper for constructing workflow nodes:

```python
def _create_workflow_step(
    key: str,
    step_type: str,
    label: str,
    description: str = "",
    endpoint: Optional[Endpoint] = None,
    action: Optional[str] = None,
    depends_on: Optional[List[str]] = None,
) -> dict:
    """Create a standardized workflow step dict."""
    step = {
        "key": key,
        "type": step_type,
        "label": label,
        "description": description,
        "depends_on": depends_on or [],
    }
    if endpoint:
        step["endpoint_path"] = endpoint.path
        step["endpoint_method"] = endpoint.method
        step["endpoint_operation_id"] = endpoint.operation_id
    if action:
        step["action"] = action
    return step
```

**Tradeoff Analysis:**

| Aspect | Option A | Option B |
|--------|----------|----------|
| Lines changed | ~40 | ~80 |
| Risk | Low | Medium |
| Future-proof | Good | Better |
| Testing effort | ~3 tests | ~6 tests |

**Recommendation:** Option A. The minimal fix has lower risk and the node builder abstraction can be added later if more workflow generators emerge.

### Validation Steps

1. **Unit Test:** Multi-step detection → workflow generation → DAG validation passes
2. **Integration Test:** Full flow "create user and send notification" with mock spec
3. **Regression Test:** Single-endpoint workflows still work

### Test Cases to Add

```python
# tests/graph/test_multi_step_workflow.py
def test_multi_step_workflow_has_start_and_end():
    """Verify multi-step workflows pass DAG validation."""
    steps = _infer_multi_endpoint_workflow(
        endpoints=[mock_create_endpoint, mock_notify_endpoint],
        task_description="create a user and send a notification",
        action_sequence=["create", "send"],
    )
    
    # Check structure
    assert steps[0]["key"] == "start"
    assert steps[0]["type"] == "start"
    assert steps[-1]["key"] == "end"
    assert steps[-1]["type"] == "end"
    
    # Build nodes and validate
    nodes = [IntegrationFlowNode(...) for step in steps]
    edges = [...]  # Build edges
    is_valid, errors = _validate_dag_structure(nodes, edges)
    assert is_valid, f"DAG validation failed: {errors}"
```

---

## FIX-002: Expose Policies in IntegrationResult (P1 High)

### Problem Statement

`WorkflowState` has `policies: List[Policy]` populated by `attach_policies_and_patterns`, but `IntegrationResult` does not expose this field, making attached policies invisible to API consumers.

### Files to Modify

| File | Changes | Lines |
|------|---------|-------|
| `src/integration_coworker/api/types.py` | Add `policies` field | 63 |
| `src/integration_coworker/api/entrypoint.py` | Pass policies to result | 58 |

### Implementation Details

#### Step 1: Add field to IntegrationResult

```python
# src/integration_coworker/api/types.py
@dataclass
class IntegrationResult:
    # ... existing fields ...
    
    # M4: Gold artifacts
    workflow_nodes: List = field(default_factory=list)
    workflow_edges: List = field(default_factory=list)
    endpoint_bindings: List = field(default_factory=list)
    
    # NEW: Attached policies (per Section 5.6)
    policies: List = field(default_factory=list)
```

#### Step 2: Populate in entrypoint

```python
# src/integration_coworker/api/entrypoint.py
return IntegrationResult(
    # ... existing fields ...
    endpoint_bindings=final_state.endpoint_bindings,
    # NEW: Pass policies
    policies=final_state.policies,
    # Phase 4: Multi-spec and diagnostics
    spec_documents=final_state.spec_documents,
    # ...
)
```

### Validation Steps

1. **Unit Test:** Result includes policies when `attach_policies_and_patterns` runs
2. **Integration Test:** End-to-end run returns non-empty policies list

---

## FIX-003: Embedding Unavailability Classification (P2 Medium)

### Problem Statement

When embeddings are unavailable (no API key), the system logs an error and appends to `state.errors`, but this should be a warning since the workflow can continue in degraded mode.

### Files to Modify

| File | Changes | Lines |
|------|---------|-------|
| `src/integration_coworker/graph/nodes/embed_spec_chunks.py` | Change error to warning | 225-230 |

### Implementation Details

```python
# Current (problematic):
if not client:
    error_msg = (
        "Embedding client unavailable. "
        "Ensure OPENAI_API_KEY is set and langchain-openai is installed. "
        "For tests, use pytest fixtures with mocked embeddings."
    )
    logger.error(error_msg)
    state.errors.append(error_msg)

# Fixed:
if not client:
    warning_msg = (
        "Embedding client unavailable - continuing in degraded mode. "
        "Semantic search will be limited. "
        "To enable embeddings, set OPENAI_API_KEY and install langchain-openai."
    )
    logger.warning(warning_msg)
    state.warnings.append(warning_msg)
    state.degraded_mode = True
    state.degraded_reason = "embeddings_unavailable"
```

### Design Consideration

This aligns with the V2 design which already has:
- `state.warnings: List[str]` for non-fatal issues
- `state.degraded_mode: bool` for tracking degraded operation
- `state.degraded_reason: Optional[str]` for explaining the degradation

---

## Implementation Order

1. **FIX-001** (P0): Must be fixed first - blocks core functionality
2. **FIX-002** (P1): Simple addition, low risk
3. **FIX-003** (P2): Cosmetic improvement, can be done anytime

## Estimated Effort

| Fix | Effort | Risk |
|-----|--------|------|
| FIX-001 | 2 hours | Medium (touches core workflow generation) |
| FIX-002 | 15 minutes | Very Low (additive change only) |
| FIX-003 | 15 minutes | Very Low (logging classification change) |

---

## Alternative Approaches Considered

### Alternative for FIX-001: Validator Flexibility

**Idea:** Make `_validate_dag_structure` accept "return_result" as an alias for "end".

**Rejected because:**
- Hides the real problem (inconsistent node generation)
- Creates technical debt (two ways to express the same concept)
- Violates the principle of having one canonical representation

### Alternative for FIX-001: Post-processing Normalization

**Idea:** Add a normalization step after `_infer_multi_endpoint_workflow` that converts id→key and return_result→end.

**Rejected because:**
- Adds complexity instead of fixing the source
- Edge generation would still need adjustment
- Better to generate correct output from the start

### Alternative for FIX-002: Separate Policies API

**Idea:** Create `get_policies(run_id)` instead of including in result.

**Rejected because:**
- Adds API complexity
- Requires persistence before policy retrieval works
- Violates the design doc which shows IntegrationResult as complete

---

## Verification Checklist

After implementing all fixes:

- [ ] `pytest tests/` passes
- [ ] Multi-step task "create X and notify Y" produces valid DAG
- [ ] `result.policies` populated when policies attached
- [ ] No false "error" logs when embeddings unavailable
- [ ] `state.degraded_mode` correctly set when running without embeddings

---

## Appendix: Related Code Locations

### Multi-Step Detection Chain
```
_detect_multi_step_pattern (line 316)
  ↓ returns (is_multi_step, action_sequence)
_infer_multi_endpoint_workflow (line 458) 
  ↓ returns steps with WRONG structure
align_task_with_kg (line 868)
  ↓ creates IntegrationFlowNode list
plan_integration_flow._validate_dag_structure (line 32)
  ↓ FAILS: "Flow must have exactly one start node"
```

### Single-Endpoint Working Chain
```
_find_matching_endpoint (line 708)
  ↓ returns best endpoint
_infer_workflow_from_endpoint (line 530)
  ↓ returns steps with CORRECT structure (start → ... → end)
align_task_with_kg (line 892)
  ↓ creates IntegrationFlowNode list
plan_integration_flow._validate_dag_structure
  ↓ PASSES
```
