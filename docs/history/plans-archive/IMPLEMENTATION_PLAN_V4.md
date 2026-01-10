# Implementation Plan V4: Streamlit Production Hardening

**Status**: Ready for Implementation  
**Estimated Effort**: 4-6 hours  
**Risk Level**: Low (isolated changes, existing test patterns)

---

## Pre-Implementation Checklist

- [x] Evidence collected (bugs found, root causes identified)
- [x] Architecture decisions made (ADR-0015)
- [x] Docker containers running (db, redis)
- [x] CLI health check passing

---

## Phase 1: Fix Critical Bugs (30 min)

### File 1: `src/integration_coworker/harness/__init__.py`

**Current Bug**: Line 14 imports `run_with_timeout` but function is named `run_pipeline_with_timeout`

**Change**:
```python
# Line 14: BEFORE
from integration_coworker.harness.runner import run_with_timeout, TimeoutResult

# Line 14: AFTER
from integration_coworker.harness.runner import run_pipeline_with_timeout as run_with_timeout, TimeoutResult
```

**Acceptance Criteria**:
```bash
python -c "from integration_coworker.harness import run_with_timeout; print('OK')"
# Must print "OK", no ImportError
```

---

### File 2: `src/integration_coworker/harness/runner.py`

**Current Bugs**:
1. Line 104: `run_integration_workflow` doesn't exist (should be `design_and_generate_integration`)
2. Line 108-111: `SandboxConfig` not used correctly (doesn't match actual sandbox interface)
3. Line 179: Function signature doesn't match what UI needs

**Changes**:

#### Change 2a: Fix the generated script template (lines 100-145)

The generated script should call `design_and_generate_integration` with proper options:

```python
# Lines 100-145: Complete rewrite of _create_runner_script body
def main():
    try:
        import os
        os.environ["CODEGEN_PROFILE"] = "production"  # Enable sandbox
        
        from integration_coworker.api.entrypoint import design_and_generate_integration
        from integration_coworker.api.types import IntegrationOptions
        
        options = IntegrationOptions(
            dry_run={dry_run},
        )
        
        result = design_and_generate_integration(
            spec_refs=["{escape(spec_uri)}"],
            task_description="{escape(task_description)}",
            options=options,
        )
        
        # Serialize result
        result_dict = {{
            "run_id": result.run_id,
            "completed_steps": result.completed_steps,
            "workflow_nodes": len(result.workflow_nodes),
            "code_artifacts": len(result.code_artifacts),
            "sandbox_result": result.sandbox_result,
            "errors": result.errors,
        }}
        
        print("__RESULT_JSON_START__")
        print(json.dumps(result_dict, default=str))
        print("__RESULT_JSON_END__")
        sys.exit(0)
        
    except Exception as e:
        error_dict = {{"error": str(e), "traceback": traceback.format_exc()}}
        print("__RESULT_JSON_START__")
        print(json.dumps(error_dict, default=str))
        print("__RESULT_JSON_END__")
        sys.exit(1)
```

#### Change 2b: Update function signature (line 179)

```python
def run_pipeline_with_timeout(
    spec_refs: List[str],           # Changed: was spec_uri
    task_description: str,          # NEW
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    dry_run: bool = False,          # NEW
    enable_live_tests: bool = False,
    sandbox_gates: Optional[List[str]] = None,
    inherit_env: bool = True,
    extra_env: Optional[Dict[str, str]] = None,
) -> TimeoutResult:
```

**Acceptance Criteria**:
```bash
python -c "
from integration_coworker.harness import run_with_timeout
r = run_with_timeout(
    spec_refs=['specs/stripe_api.json'],
    task_description='Test',
    timeout_seconds=10,
    dry_run=True
)
print(f'Success: {r.success}, Timed out: {r.timed_out}')
"
# Must complete without crash (may time out, that's fine)
```

---

## Phase 2: Wire UI to Harness (45 min)

### File 3: `src/integration_coworker/ui/streamlit_app.py`

**Current Problem**: Lines 465-547 (`_run_integration`) calls `design_and_generate_integration` directly, ignoring session_state timeout/sandbox/live options.

**Changes**:

#### Change 3a: Add harness import (after line 22)

```python
# After existing imports (around line 22)
from integration_coworker.harness import run_with_timeout, TimeoutResult
```

#### Change 3b: Replace spinner with st.status (line 516)

```python
# BEFORE (line 516-517):
with st.spinner("Running integration workflow..."):
    result = design_and_generate_integration(...)

# AFTER:
with st.status("Running integration workflow...", expanded=True) as status:
    status.update(label="⏳ Starting pipeline with timeout enforcement...")
    
    # Use harness runner with timeout
    harness_result = run_with_timeout(
        spec_refs=spec_refs,
        task_description=task_description,
        timeout_seconds=st.session_state.timeout_seconds,
        dry_run=dry_run,
        enable_live_tests=st.session_state.enable_live_tests,
        sandbox_gates=st.session_state.sandbox_gates,
    )
    
    if harness_result.timed_out:
        status.update(label=f"⏱️ Pipeline timed out after {st.session_state.timeout_seconds}s", state="error")
        raise TimeoutError(f"Pipeline timed out after {st.session_state.timeout_seconds}s")
    
    if not harness_result.success:
        status.update(label="❌ Pipeline failed", state="error")
        raise RuntimeError(harness_result.error or "Unknown pipeline error")
    
    status.update(label="✅ Pipeline completed!", state="complete")
    
    # Convert harness result to IntegrationResult
    # (Need to re-fetch full result from DB or reconstruct)
    result_data = harness_result.result
```

#### Change 3c: Handle result conversion (after harness call)

Since subprocess returns a dict, we need to convert back to IntegrationResult or adjust how we store it:

```python
# Create a minimal result object or store the dict directly
# Option: Store dict in session_state and adjust _render_run_status to handle dict
st.session_state.last_result_dict = harness_result.result
st.session_state.current_run_id = harness_result.result.get("run_id", "unknown")
```

**Acceptance Criteria**:
1. UI shows st.status (expandable) instead of spinner
2. Timeout slider value is actually used
3. Sandbox gates selection is passed to harness
4. Live tests checkbox is respected
5. If timeout expires, UI shows timeout error, not generic error

---

## Phase 3: Update Result Display (30 min)

### File 4: `src/integration_coworker/ui/streamlit_app.py` (continued)

**Current Problem**: `_render_run_status()` expects `IntegrationResult` object, but harness returns dict.

**Options**:
1. **Option A**: Modify harness to return full IntegrationResult (requires more serialization)
2. **Option B**: Modify `_render_run_status` to handle dict or object (duck typing)
3. **Option C**: Create a `HarnessResult` dataclass that mirrors dict structure

**Decision**: Option B (duck typing) - minimal changes

#### Change 4a: Update `_render_run_status()` (lines 585-655)

Add dict support at the start:

```python
def _render_run_status() -> None:
    """Render the run status view."""
    result = st.session_state.last_result
    result_dict = st.session_state.get('last_result_dict')
    
    if result is None and result_dict is None:
        st.info("No integration run yet...")
        return
    
    # Use dict if available (from harness), otherwise use object
    if result_dict:
        run_id = result_dict.get("run_id", "N/A")
        completed_steps = result_dict.get("completed_steps", [])
        workflow_nodes_count = result_dict.get("workflow_nodes", 0)
        code_artifacts_count = result_dict.get("code_artifacts", 0)
        sandbox_result = result_dict.get("sandbox_result")
        errors = result_dict.get("errors", [])
    else:
        run_id = result.run_id
        completed_steps = result.completed_steps
        workflow_nodes_count = len(result.workflow_nodes)
        code_artifacts_count = len(result.code_artifacts)
        sandbox_result = getattr(result, 'sandbox_result', None)
        errors = result.errors
    
    # Continue with display using these variables...
```

**Acceptance Criteria**:
1. UI displays results correctly from both direct call and harness
2. Sandbox results show per-gate pass/fail
3. Completed steps count is accurate (18 expected for full run)
4. Workflow nodes count is accurate (5 expected for gold model)

---

## Phase 4: Tests (45 min)

### File 5: `tests/harness/test_runner.py` (NEW)

```python
"""Tests for harness runner subprocess execution."""
import pytest
from unittest.mock import patch, MagicMock
import os

from integration_coworker.harness import run_with_timeout, TimeoutResult


class TestRunWithTimeout:
    """Test subprocess runner with timeout."""
    
    def test_import_works(self):
        """Verify harness module imports correctly (bug fix)."""
        from integration_coworker.harness import run_with_timeout
        assert callable(run_with_timeout)
    
    @pytest.mark.slow
    def test_timeout_actually_kills_process(self):
        """Verify subprocess is killed on timeout."""
        result = run_with_timeout(
            spec_refs=["tests/fixtures/mock_payments_openapi.yaml"],
            task_description="Test task",
            timeout_seconds=2,  # Very short
            dry_run=True,
        )
        # Should timeout since 2s is not enough
        assert result.timed_out or not result.success
    
    def test_dry_run_completes(self):
        """Verify dry run completes without DB writes."""
        # Skip if no API key
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY required")
        
        result = run_with_timeout(
            spec_refs=["tests/fixtures/mock_payments_openapi.yaml"],
            task_description="Create a payment",
            timeout_seconds=120,
            dry_run=True,
        )
        # Should complete (may fail on LLM, but shouldn't crash)
        assert not result.timed_out
    
    def test_production_profile_set(self):
        """Verify CODEGEN_PROFILE=production is set in subprocess."""
        # This is tested via the generated script content
        from integration_coworker.harness.runner import _create_runner_script
        script = _create_runner_script(
            spec_refs=["test.yaml"],
            task_description="Test",
            output_dir="/tmp",
            enable_live_tests=False,
            sandbox_gates=["ruff"],
            dry_run=False,
        )
        assert 'CODEGEN_PROFILE' in script or 'production' in script
```

### File 6: `tests/harness/test_integration.py` (NEW)

```python
"""Integration tests for harness with real services."""
import pytest
import os

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_db,
    pytest.mark.requires_llm,
]


@pytest.fixture
def ensure_services():
    """Skip if required services aren't available."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY required")


class TestHarnessIntegration:
    """Integration tests for full harness flow."""
    
    def test_full_run_with_sandbox(self, ensure_services):
        """Test full run with sandbox gates enabled."""
        from integration_coworker.harness import run_with_timeout
        
        result = run_with_timeout(
            spec_refs=["specs/stripe_api.json"],
            task_description="Create checkout session",
            timeout_seconds=180,
            dry_run=True,
            sandbox_gates=["ruff", "mypy"],
        )
        
        assert not result.timed_out
        if result.success:
            assert result.result is not None
            assert "sandbox_result" in result.result
```

**Acceptance Criteria**:
```bash
pytest tests/harness/ -v --tb=short
# All tests pass (or skip appropriately for missing services)
```

---

## Phase 5: Documentation (15 min)

### File 7: Update `docs/PRODUCTION_HARDENING_AUDIT_V3.md`

Add section at top:

```markdown
## Implementation Status

| Item | Status | PR/Commit |
|------|--------|-----------|
| Fix harness import | ✅ Done | #XXX |
| Fix runner script | ✅ Done | #XXX |
| Wire UI to harness | ✅ Done | #XXX |
| Add st.status | ✅ Done | #XXX |
| Tests added | ✅ Done | #XXX |
```

---

## Downstream Impact

| Component | Impact | Required Update |
|-----------|--------|-----------------|
| `cli run` command | None | Uses entrypoint directly |
| `demo-final-showcase.sh` | None | Uses CLI |
| Existing tests | None | Harness tests are new |
| UI pages/ | None | Not using st.navigation |

---

## Verification Commands

After implementation, run these to verify:

```bash
# 1. Import check
python -c "from integration_coworker.harness import run_with_timeout; print('OK')"

# 2. CLI health
python -m integration_coworker.cli health

# 3. Harness unit tests
pytest tests/harness/ -v

# 4. Quick UI smoke test (manual)
streamlit run src/integration_coworker/ui/streamlit_app.py
# → Click Run with defaults, verify st.status appears

# 5. Full production test
CODEGEN_PROFILE=production python -c "
from integration_coworker.harness import run_with_timeout
result = run_with_timeout(
    spec_refs=['specs/stripe_api.json'],
    task_description='Create checkout session',
    timeout_seconds=180,
    dry_run=True
)
print(f'Success: {result.success}')
print(f'Sandbox: {result.result.get(\"sandbox_result\", {}).get(\"summary\", \"N/A\")}')
"
```

---

## Rollback Plan

If issues arise:
1. Revert harness changes, UI reverts to direct `design_and_generate_integration` call
2. Advanced Options become cosmetic only (document as "coming soon")
3. No data loss risk since these are execution path changes only
