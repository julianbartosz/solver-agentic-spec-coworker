# Bug Report: Live Demo v25 (2025-12-28)

**Demo Run ID**: `demo-livev25-20251228-152321`  
**Date**: 2025-12-28T15:23:21  
**Environment**: `INTEGRATION_COWORKER_PROFILE=production`, `VALIDATION_PROFILE=live`  
**Target Repo**: `/Users/julianbartosz/git/schoolwork/UPlant/testing-solver-agentic-spec-coworker`

---

## Executive Summary

| Run | Provider | Status | Errors |
|-----|----------|--------|--------|
| `run_4f3bf42a_1766957903` | multilang_stripe_api | ✅ Completed | 0 |
| `run_01a02f14_1766959442` | stripe_api_cache_test | ❌ Completed with errors | 1 |

**Key Issues Found:**
1. **Test/Code Generation Mismatch** - LLM generates tests that expect validation behavior not implemented in flow code
2. **Test Repair Mechanism Failure** - Repair loop generates invalid fixes that still fail
3. **~51% Runtime Gap** - Significant unaccounted time outside nodes and checkpoints
4. **Slow Run Threshold Exceeded** - 458.8s vs 300s threshold

---

## Bug #V25-001: Test/Code Generation Semantic Mismatch

**Severity**: 🔴 HIGH  
**Category**: Code Generation  
**Run ID**: `run_01a02f14_1766959442`  
**Provider**: `stripe_api_cache_test`

### Description

The LLM generates test code that expects validation behavior (`ValueError` for `payload=None`) that the flow code does NOT implement. This is a **semantic mismatch** where the test and implementation are internally inconsistent.

### Evidence

**Generated Test** (`test_stripe_api_cache_test_list_available_products.py:78-87`):
```python
def test_list_available_products_flow_missing_payload(self):
    """Test flow raises error when payload is missing."""
    with pytest.raises(ValueError, match="payload is required"):
        list_available_products_flow(
            api_key="test_api_key",
            payload=None,
        )
```

**Generated Flow** (`stripe_api_cache_test_list_available_products.py:36-38`):
```python
# Step 1: Validate input
if not api_key:
    raise ValueError("api_key is required")
if payload is None:
    payload = {}  # <-- CONVERTS None to empty dict, does NOT raise!
```

### Test Output
```
_ TestStripeApiCacheTestFlow.test_list_available_products_flow_missing_payload _
tests/test_stripe_api_cache_test_list_available_products.py:87: 
    with pytest.raises(ValueError, match="payload is required"):
E   Failed: DID NOT RAISE <class 'ValueError'>
```

### Root Cause Analysis

1. **Inconsistent LLM prompting**: The test generator and code generator receive similar context but make different assumptions about validation requirements
2. **No cross-validation between test and code**: Tests are generated based on docstring/contract assumptions, not the actual generated code
3. **Silent default behavior**: Flow silently handles `None` as valid (empty dict) instead of failing fast

### Suggested Fixes

1. **Option A**: Change flow to match test expectation:
   ```python
   if payload is None:
       raise ValueError("payload is required")
   ```

2. **Option B**: Change test to match flow behavior:
   ```python
   def test_list_available_products_flow_missing_payload(self):
       """Test flow handles None payload by using empty dict."""
       with patch(...) as MockClient:
           mock_client = MockClient.return_value
           mock_client.list_products.return_value = {...}
           result = list_available_products_flow(api_key="key", payload=None)
           assert result["success"] is True
   ```

3. **Option C (Recommended)**: Add post-generation cross-validation step that ensures test assertions match actual code behavior

---

## Bug #V25-002: Test Repair Mechanism Generates Invalid Fixes

**Severity**: 🟠 MEDIUM  
**Category**: Test Repair  
**Run ID**: `run_01a02f14_1766959442`

### Description

The test repair mechanism detected the assertion error but generated a fix that still fails. The repair loop "successfully repaired 1 test assertion errors" but the repaired tests still failed with the same issue.

### Evidence

**Log Sequence**:
```
INFO  [sandbox] Detected 1 test assertion error(s), attempting repair
INFO  [test_repair] Successfully repaired 1 test assertion errors  
INFO  [sandbox] Re-running sandbox with repaired tests
...
WARNING [sandbox] pytest exit_code:1
WARNING [sandbox] Repaired tests still failed
ERROR [generate_code_and_tests] Sandbox validation failed: FAILED: 6/8 gates passed
```

**Sandbox Results**:
- Before repair: `sandbox-e42288ec` - 6/8 gates, pytest FAILED
- After repair: `sandbox-6ed86a72` - 6/8 gates, pytest FAILED (same failure)

### Root Cause Analysis

1. **Repair focused on wrong target**: The LLM repair may have modified the test superficially without understanding the semantic mismatch
2. **No code repair**: The repair loop only repairs tests, not the flow code that caused the mismatch
3. **Missing feedback loop**: The repair doesn't include the actual flow code in context to understand what behavior to expect

### Suggested Fixes

1. Add "code+test co-repair" mode that can modify both
2. Include actual generated code in repair prompt context
3. Add semantic validation that test assertions match code behavior before sandbox runs

---

## Bug #V25-003: Runtime Gap - 51% of Time Unaccounted

**Severity**: 🟡 LOW (Performance)  
**Category**: Runtime Instrumentation  
**Run ID**: `run_4f3bf42a_1766957903`

### Description

The runtime logging shows 79.7% gap between total runtime and sum of node times, with warning:
```
V22-011 GAP DETECTED: 79.7% of runtime unaccounted! 
Total=458823ms, NodeSum=93010ms, Gap=365813ms
```

### Analysis

| Component | Time (s) | % of Total |
|-----------|----------|-----------|
| Node execution | 179.2s | 39.1% |
| Checkpoint overhead | 43.2s | 9.4% |
| **Gap (unaccounted)** | 236.6s | **51.5%** |
| **Total** | 459s | 100% |

### Possible Causes

1. **LangGraph state machine overhead**: Graph traversal and state serialization
2. **Queue wait time**: Async task scheduling between nodes
3. **Memory management**: Large state objects (14-21MB per node) causing GC pressure
4. **Missing timed_node wrappers**: Some operations not instrumented

### Investigation Areas

1. `detect_and_parse_spec` has 16.9s checkpoint alone (largest) - investigate why
2. Add timing instrumentation for LangGraph internal operations
3. Profile memory allocations during graph execution

---

## Bug #V25-004: Slow Run Threshold Exceeded

**Severity**: 🟡 LOW (Performance)  
**Category**: Runtime Performance  
**Run IDs**: `run_4f3bf42a_1766957903`, `run_01a02f14_1766959442`

### Description

Both runs exceeded the 300s threshold:
- `run_4f3bf42a`: 458.8s (53% over threshold)
- `run_01a02f14`: 458.1s (53% over threshold)

### Slow Run Bundle

Location: `/tmp/graph_traces/run_4f3bf42a_1766957903/SLOW_RUN_BUNDLE.json`

### Bottleneck Nodes

| Node | Time | Notes |
|------|------|-------|
| `detect_and_parse_spec` | 75.8s | LLM-heavy, spec parsing |
| `persist_kg_learning` | 6.1s | DB writes |
| `ingest_spec` | 5.8s | File I/O + parsing |
| `build_report` | 3.2s | LLM summary |

---

## Additional Observations

### Exit Code 5 Warnings

Several `pytest_live` gates exited with code 5 (no tests collected):
```
WARNING gate.done pytest_live exit_code:5 stdout_tail="3 deselected in 0.02s"
```

This is expected behavior (no live tests) but generates noise in logs.

### Ruff Auto-Fixed 12 Lint Errors

```
DEBUG gate.done ruff_fix "Found 12 errors (12 fixed, 0 remaining)"
```

This suggests the code generator could produce cleaner code initially.

---

## Recommendations

### High Priority

1. **Fix V25-001**: Add post-generation cross-validation between tests and code
2. **Fix V25-002**: Enhance repair mechanism to include actual code context

### Medium Priority

3. Add instrumentation for LangGraph internal operations to diagnose gap
4. Review `detect_and_parse_spec` checkpoint - why 17s for one checkpoint?

### Low Priority

5. Tune LLM prompts to generate cleaner code (fewer ruff fixes needed)
6. Consider adjusting slow run threshold or adding per-spec thresholds

---

## Appendix: Full Log Locations

- Demo log: `/tmp/demo-livev25-20251228-152321.log`
- Slow run bundle: `/tmp/graph_traces/run_4f3bf42a_1766957903/SLOW_RUN_BUNDLE.json`
- Failure bundle 1: `/var/folders/.../codegen_sandbox_m73_e1gv/FAILURE_BUNDLE.json`
- Failure bundle 2: `/var/folders/.../codegen_sandbox_asph011q/FAILURE_BUNDLE.json`

---

*Report generated from demo log analysis on 2025-12-28*
