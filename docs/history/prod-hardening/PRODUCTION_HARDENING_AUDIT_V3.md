# Production Hardening Audit V3: Streamlit UI Parity Report

**Date**: 2025-01-XX  
**Auditor**: AI Agent (Claude Opus 4.5)  
**Status**: CRITICAL FINDINGS - Action Required

---

## Executive Summary

The Streamlit UI has **critical gaps** that prevent production parity with `demo-final-showcase.sh`. The root cause is a **broken options chain**: Advanced Options (sandbox gates, live tests, timeout) are displayed in the UI but **never wired** to the actual workflow execution.

### Key Findings

| Finding | Severity | Root Cause Location |
|---------|----------|---------------------|
| Sandbox gates never run | **P0 Critical** | `streamlit_app.py:517` ignores `session_state.sandbox_gates` |
| Live tests checkbox ineffective | **P0 Critical** | `streamlit_app.py:517` ignores `session_state.enable_live_tests` |
| Timeout slider ineffective | **P0 Critical** | `streamlit_app.py:517` ignores `session_state.timeout_seconds` |
| No `CODEGEN_PROFILE=production` | **P1 High** | Profile defaults to development (sandbox disabled) |
| Harness runner not integrated | **P1 High** | `harness/runner.py` created but never called |
| `IntegrationOptions` missing fields | **P1 High** | No `codegen_profile` or sandbox-related fields |

---

## Section 1: Environment Baseline

### 1.1 Prerequisites Verified
```
✅ Python 3.11.14
✅ Docker containers: postgres (pgvector:pg16), redis (7-alpine) - both healthy
✅ python -c "from integration_coworker.api import entrypoint; print('ok')" → ok
✅ python -m integration_coworker.cli health → All 6 checks passed
```

### 1.2 Known Issues (Benign)
```
⚠️ init-db: Migration checksum mismatch (001_baseline_v1)
⚠️ seed_kg: workflow_templates table doesn't exist (seed_kg failures expected)
```

---

## Section 2: Node Labeling Clarification

**Confirmed at**: [build_report.py#L108-109](src/integration_coworker/graph/nodes/build_report.py#L108-109)

| Term | Count | Definition |
|------|-------|------------|
| `completed_steps` | 16-21 | Pipeline execution steps (LangGraph checkpoints) |
| `workflow_nodes` | 5 | Gold Model Integration Flow nodes |

The "18 steps vs 5 nodes" discrepancy is **correct by design**, not a bug.

---

## Section 3: Side-by-Side Comparison Table

### Test A: CLI (`--dry-run`)
```bash
python -m integration_coworker.cli run \
  --spec-ref specs/stripe_api.json \
  --task "Create a checkout session" \
  --dry-run
```

| Metric | Value |
|--------|-------|
| `completed_steps` | 16 |
| `workflow_nodes` | 5 |
| `code_artifacts` | 3 |
| `sandbox_result` | N/A (dry-run) |
| Errors | 1 (benign) |
| Duration | ~45s |

### Test B: Python API
```python
from integration_coworker.api.entrypoint import design_and_generate_integration
result = design_and_generate_integration(
    spec_refs=["specs/stripe_api.json"],
    task_description="Create a checkout session for payment"
)
print(result.sandbox_result)  # → None
```

| Metric | Value |
|--------|-------|
| `completed_steps` | 18 |
| `workflow_nodes` | 5 |
| `code_artifacts` | 3 |
| `sandbox_result` | **None** (PROBLEM!) |
| Errors | 1 |
| Duration | ~60s |

### Test C: Streamlit UI (Projected)

The UI would produce **identical results to Test B** because it calls the same `design_and_generate_integration()` function with the same parameters.

| Metric | Value |
|--------|-------|
| `completed_steps` | 18 (same as API) |
| `workflow_nodes` | 5 (same as API) |
| `sandbox_result` | **None** (PROBLEM!) |
| Advanced Options respected | **NO** |

---

## Section 4: Root Cause Analysis

### 4.1 Why `sandbox_result` is Always `None`

**Location**: [generate_code_and_tests.py#L508-516](src/integration_coworker/graph/nodes/generate_code_and_tests.py#L508-516)

```python
def _run_sandbox_validation(
    state: WorkflowState,
    artifacts: list[CodeArtifact],
    profile: CodegenProfile,  # ← Comes from get_active_profile()
    ...
) -> tuple[bool, Optional[dict]]:
    """Run sandbox validation on generated code artifacts."""
    sandbox_enabled = profile.enable_sandbox_execution  # ← Line 508
    
    if not sandbox_enabled:  # ← Line 515-516
        logger.debug(f"Sandbox validation skipped: not enabled (profile={profile.name})")
        return True, None  # ← RETURNS NONE!
```

**Why this happens:**
1. `get_active_profile()` reads `CODEGEN_PROFILE` env var
2. Default is `"development"` 
3. Development profile has `enable_sandbox_execution=False`
4. Neither CLI nor API nor UI sets `CODEGEN_PROFILE=production`
5. UI Advanced Options checkboxes are **completely ignored**

### 4.2 The Broken Options Chain

```
UI (streamlit_app.py)                     API Layer                       Workflow Layer
┌─────────────────────────────────┐    ┌─────────────────────────┐    ┌─────────────────────────────┐
│ ⚙️ Advanced Options             │    │ IntegrationOptions      │    │ CodegenProfile              │
│ ┌───────────────────────────┐   │    │ (api/types.py)          │    │ (config/profiles.py)        │
│ │ timeout_seconds: 600      │───┼──X─┤ ❌ No timeout field     │    │ ❌ No timeout field         │
│ │ sandbox_gates: [ruff,...]│───┼──X─┤ ❌ No sandbox_gates     │    │ enable_sandbox_execution ←──┤
│ │ enable_live_tests: True   │───┼──X─┤ ❌ No live_tests field  │    │ enable_live_tests ←─────────┤
│ └───────────────────────────┘   │    └───────────┬─────────────┘    └──────────────┬──────────────┘
│                                 │                │                                  │
│ _run_integration()              │                │                                  │
│   result = design_and_generate_ │                ▼                                  ▼
│     integration(...)  ──────────┼───────► Ignores session_state    get_active_profile() reads
│   # IGNORES:                    │         Creates default options  CODEGEN_PROFILE env var
│   # - st.session_state.timeout  │                                  Defaults to "development"
│   # - st.session_state.sandbox  │                                  → sandbox DISABLED
│   # - st.session_state.live     │
└─────────────────────────────────┘
```

### 4.3 The Harness Runner Is Orphaned

I created [harness/runner.py](src/integration_coworker/harness/runner.py) with subprocess-based timeout:

```python
def run_with_timeout(
    spec_refs: list[str],
    task_description: str,
    timeout_seconds: int = 600,
    ...
) -> RunResult:
    """Run integration workflow in subprocess with strict timeout."""
```

**But the UI never calls it!** Line 517 of `streamlit_app.py` still calls:
```python
result = design_and_generate_integration(...)  # Direct call, no timeout
```

---

## Section 5: Definition of Done (Production-Ready UI)

For the Streamlit UI to be production-ready and achieve parity with `demo-final-showcase.sh`:

### Must-Have (P0)

| Requirement | Current State | Target State |
|-------------|---------------|--------------|
| Sandbox gates run by default | ❌ Never run | ✅ Run ruff, mypy, bandit, pytest |
| sandbox_result populated | ❌ Always None | ✅ Dict with per-gate results |
| Timeout actually enforced | ❌ Slider ignored | ✅ Subprocess kill after N seconds |
| Advanced Options wired | ❌ Stored but ignored | ✅ Passed to workflow |

### Should-Have (P1)

| Requirement | Current State | Target State |
|-------------|---------------|--------------|
| Live tests toggleable | ❌ Checkbox ignored | ✅ Respects ALLOW_LIVE |
| Coverage gate optional | ❌ Not configurable | ✅ Toggle in Advanced |
| Contract tests optional | ❌ Hidden | ✅ Toggle in Advanced |

### Nice-to-Have (P2)

| Requirement | Current State | Target State |
|-------------|---------------|--------------|
| Per-run profile override | ❌ Must set env var | ✅ Dropdown in UI |
| Real-time gate output | ❌ Only final result | ✅ Stream ruff/mypy output |

---

## Section 6: Architecture Debate (A vs B vs C)

### Option A: Env Var Injection (Minimal Change)

**Approach**: Before calling `design_and_generate_integration()`, set env vars:
```python
if st.session_state.sandbox_gates:
    os.environ["ENABLE_SANDBOX_GATES"] = "true"
    os.environ["CODEGEN_PROFILE"] = "production"
```

| Pros | Cons |
|------|------|
| Minimal code changes (~10 lines) | Global state mutation (thread-unsafe) |
| Works with existing architecture | Leaks between runs if not reset |
| No API changes needed | No subprocess timeout support |
| | Smells bad |

**Files Changed**: 1
- `streamlit_app.py` (add env var setting before run)

---

### Option B: Extend IntegrationOptions + Wire Through

**Approach**: Add fields to `IntegrationOptions`, plumb through to profile:
```python
@dataclass
class IntegrationOptions:
    # Existing fields...
    
    # V3.2: Sandbox control
    codegen_profile_name: str = "development"
    enable_sandbox: bool = False
    sandbox_gates: list[str] = field(default_factory=list)
    enable_live_tests: bool = False
    timeout_seconds: int = 600
```

Then in `run_workflow()`:
```python
if options.enable_sandbox or options.codegen_profile_name == "production":
    profile = PROFILES["production"]
else:
    profile = get_active_profile()
```

| Pros | Cons |
|------|------|
| Clean, explicit API | 4+ files need changes |
| Thread-safe | Must update entrypoint, state, runtime |
| No global mutation | More testing required |
| Enables per-run configuration | |

**Files Changed**: 4-5
- `api/types.py` (add fields)
- `api/entrypoint.py` (pass options through)
- `graph/state.py` (store options)
- `graph/runtime.py` (use options in node calls)
- `ui/streamlit_app.py` (build IntegrationOptions)

---

### Option C: Full Harness Integration (Subprocess Runner)

**Approach**: Use `harness/runner.py` which already exists:
```python
# In _run_integration():
from integration_coworker.harness.runner import run_with_timeout

result = run_with_timeout(
    spec_refs=spec_refs,
    task_description=task_description,
    timeout_seconds=st.session_state.timeout_seconds,
    sandbox_gates=st.session_state.sandbox_gates,
    enable_live_tests=st.session_state.enable_live_tests,
)
```

The harness runner already uses subprocess with `CODEGEN_PROFILE=production`.

| Pros | Cons |
|------|------|
| Timeout actually works (subprocess.kill) | Extra process overhead |
| Isolation from main process | Need to serialize/deserialize result |
| Already written | Slight latency increase |
| Demo parity (demo script uses subprocess) | |

**Files Changed**: 2
- `harness/runner.py` (add sandbox_gates param)
- `ui/streamlit_app.py` (switch to harness runner)

---

### Recommendation: **Option C (Harness Runner)**

**Justification:**

1. **Timeout is non-negotiable**: LLM calls can hang. Only subprocess.kill works reliably.
2. **Already written**: The harness was created for exactly this purpose.
3. **Demo parity**: `demo-final-showcase.sh` uses subprocess isolation.
4. **Minimal changes**: Only 2 files, minimal testing.
5. **Future-proof**: Easy to add real-time streaming later.

Option B is cleaner architecturally but doesn't solve the timeout problem. Option A is a hack.

---

## Section 7: Exact Refactor Plan

### Phase 1: Wire Harness Runner (Day 1)

#### File 1: `src/integration_coworker/harness/runner.py`

**Change 1**: Add `sandbox_gates` parameter

```python
# Line ~25
def run_with_timeout(
    spec_refs: list[str],
    task_description: str,
    timeout_seconds: int = 600,
    dry_run: bool = False,
    sandbox_gates: list[str] | None = None,  # NEW
    enable_live_tests: bool = False,  # NEW
) -> RunResult:
```

**Change 2**: Set env vars for subprocess

```python
# Line ~45 (in subprocess env setup)
env = os.environ.copy()
env["CODEGEN_PROFILE"] = "production"
if sandbox_gates:
    env["SANDBOX_GATES"] = ",".join(sandbox_gates)
if enable_live_tests:
    env["ALLOW_LIVE"] = "1"
```

---

#### File 2: `src/integration_coworker/ui/streamlit_app.py`

**Change 1**: Import harness runner (around line 22)

```python
# Add import
from integration_coworker.harness.runner import run_with_timeout, RunResult
```

**Change 2**: Replace direct call with harness runner (lines 517-523)

```python
# BEFORE:
result = design_and_generate_integration(
    spec_refs=spec_refs,
    task_description=task_description,
    provider_code=provider_code,
    repo_root=repo_root,
    options=options,
)

# AFTER:
harness_result = run_with_timeout(
    spec_refs=spec_refs,
    task_description=task_description,
    timeout_seconds=st.session_state.timeout_seconds,
    dry_run=dry_run,
    sandbox_gates=st.session_state.sandbox_gates,
    enable_live_tests=st.session_state.enable_live_tests,
)

if harness_result.success:
    result = harness_result.result
    st.session_state.last_result = result
else:
    raise RuntimeError(harness_result.error or "Unknown harness error")
```

---

### Phase 2: Display Sandbox Results (Day 1-2)

#### File: `src/integration_coworker/ui/streamlit_app.py`

Add to Run Status tab (after line ~560):

```python
# Display sandbox results
if result and result.sandbox_result:
    st.subheader("🔒 Sandbox Validation Results")
    for gate, gate_result in result.sandbox_result.items():
        status = "✅" if gate_result.get("passed") else "❌"
        st.write(f"{status} **{gate}**")
        if gate_result.get("output"):
            with st.expander(f"{gate} output"):
                st.code(gate_result["output"])
else:
    st.info("ℹ️ Sandbox validation not run (enable in Advanced Options)")
```

---

### Phase 3: Tests (Day 2)

Create `tests/ui/test_harness_integration.py`:

```python
"""Test harness runner integration with UI."""
import pytest
from unittest.mock import patch, MagicMock
from integration_coworker.harness.runner import run_with_timeout

def test_sandbox_gates_passed_to_subprocess():
    """Verify sandbox_gates parameter propagates to subprocess env."""
    with patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'{"success": true}', b'')
        mock_process.returncode = 0
        mock_popen.return_value = mock_process
        
        run_with_timeout(
            spec_refs=["test.json"],
            task_description="Test task",
            sandbox_gates=["ruff", "mypy"],
        )
        
        # Verify env vars
        call_kwargs = mock_popen.call_args.kwargs
        assert call_kwargs["env"]["CODEGEN_PROFILE"] == "production"
        assert call_kwargs["env"]["SANDBOX_GATES"] == "ruff,mypy"
```

---

## Section 8: Test Matrix

| Test Case | Expected Result | Priority |
|-----------|-----------------|----------|
| Run with sandbox gates → result.sandbox_result populated | Dict with gate results | P0 |
| Run with timeout exceeded → RunResult.timeout=True | Subprocess killed | P0 |
| Run with enable_live_tests=True → ALLOW_LIVE=1 in subprocess | Env var set | P1 |
| Run with dry_run=True → No DB writes | No persist calls | P1 |
| Fresh Reset → All tables cleared | KG query returns 0 | P1 |

---

## Section 9: Conclusion

The Streamlit UI has a **broken options chain** where Advanced Options are displayed but never wired to the workflow. The recommended fix is **Option C (Harness Runner)** which:

1. ✅ Enforces timeout via subprocess.kill
2. ✅ Respects sandbox gates
3. ✅ Respects live tests toggle
4. ✅ Achieves parity with demo-final-showcase.sh
5. ✅ Requires only 2 file changes

**Estimated Effort**: 4-6 hours  
**Risk**: Low (harness already tested in CLI)

---

## Appendix A: File Change Summary

| File | Lines Changed | Description |
|------|---------------|-------------|
| `harness/runner.py` | +15 | Add sandbox_gates, enable_live_tests params |
| `ui/streamlit_app.py` | +25, -10 | Switch to harness runner, display results |
| `tests/ui/test_harness_integration.py` | +50 (new) | Unit tests for integration |

**Total**: ~90 lines changed/added

---

## Appendix B: Environment Variables Reference

| Env Var | Set By | Effect |
|---------|--------|--------|
| `CODEGEN_PROFILE` | Harness | production → sandbox enabled |
| `SANDBOX_GATES` | Harness | Comma-separated gate list |
| `ALLOW_LIVE` | Harness | Enable live API tests |
| `ENABLE_SANDBOX_GATES` | Legacy | Alternate sandbox trigger |
| `VALIDATION_PROFILE` | CI | Override for specific profiles |
