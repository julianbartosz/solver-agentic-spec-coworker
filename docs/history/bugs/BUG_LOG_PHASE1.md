# Phase 1 Bug Log - Production Hardening

**Date**: 2025-12-21  
**Branch**: `prod-gaps-phase1`  
**Author**: AI-supervised remediation  

## Methodology

Only bugs confirmed by **committed tests** are logged here. No "python -c probes" or docs-only entries.

---

## B-001: Checkpoint Version Unknown Allowed in Production

**Status**: ✅ FIXED  
**Severity**: High  
**Gap Reference**: DEEP_PRODUCTION_AUDIT_v2.md Gap F  

### Original Behavior
- `validate_checkpoint_version()` in `runtime.py` allowed `checkpoint_version="unknown"` in production
- Silent data migration risks if checkpoint format changed between versions

### Fix Applied
- File: `src/integration_coworker/graph/runtime.py` lines 302-356
- Production profile (`is_production_profile()=True`) now blocks `"unknown"` checkpoint versions
- Environment override: `ALLOW_UNKNOWN_CHECKPOINT_VERSION=1` allows bypass with WARNING log

### Test Coverage
- File: `tests/graph/test_checkpoint_version_policy.py` (12 tests)
- Key tests:
  - `test_production_blocks_unknown_checkpoint_version`
  - `test_production_blocks_unknown_current_version`
  - `test_production_allows_unknown_with_env_override`
  - `test_development_allows_unknown_checkpoint_version`

### Reproduction
```bash
pytest tests/graph/test_checkpoint_version_policy.py -v
# 12 passed
```

---

## B-002: Strict Gates Warn Instead of Fail on Missing Tools

**Status**: ✅ FIXED  
**Severity**: Critical  
**Gap Reference**: DEEP_PRODUCTION_AUDIT_v2.md Gap D  

### Original Behavior
- `_run_strict_quality_gates()` in `generate_code_and_tests.py` caught `FileNotFoundError` for ruff/mypy
- Only logged WARNING and continued, returning `(True, "")` 
- Production could deploy code without lint/type checks if tools missing

### Fix Applied
- File: `src/integration_coworker/graph/nodes/generate_code_and_tests.py` lines 995-1150
- Added `GateResult` dataclass with `tool_missing` flag
- Refactored to helper functions: `_run_ruff_check()`, `_run_ruff_format_check()`, `_run_mypy_check()`
- Production profile (`enable_strict_gates=True`) now returns `(False, "tool: MISSING")` on FileNotFoundError
- Development profile (`enable_strict_gates=False`) skips gates entirely

### Test Coverage
- File: `tests/graph/test_strict_gates_production.py` (20 tests)
- Key tests:
  - `test_production_profile_fails_on_missing_ruff`
  - `test_production_profile_fails_on_missing_mypy`
  - `test_production_profile_passes_when_all_tools_pass`
  - `test_development_profile_skips_gates`
  - `test_production_aggregates_multiple_failures`

### Reproduction
```bash
pytest tests/graph/test_strict_gates_production.py -v
# 20 passed
```

---

## Summary

| Bug ID | Description | Status | Tests |
|--------|-------------|--------|-------|
| B-001 | Checkpoint unknown allowed in production | ✅ Fixed | 12 |
| B-002 | Strict gates warn instead of fail | ✅ Fixed | 20 |
| B-003 | Tree-sitter unavailable logged at DEBUG | ✅ Fixed | 7 |
| B-004 | HITL gate has no timeout config | ✅ Fixed | 12 |

**Total Tests Added**: 51  
**All Tests Passing**: Yes

---

## B-003: Tree-sitter Unavailable Logged at DEBUG

**Status**: ✅ FIXED  
**Severity**: P1  
**Gap Reference**: DEEP_PRODUCTION_AUDIT_v2.md Gap A  

### Original Behavior
- `syntax_validator.py` logged tree-sitter unavailability at DEBUG level
- Operators wouldn't know syntax validation was degraded without debug logging
- No metric to track tree-sitter availability

### Fix Applied
- File: `src/integration_coworker/codegen/syntax_validator.py` line 51
- Changed `logger.debug` → `logger.warning` for missing tree-sitter
- Added `is_tree_sitter_available()` helper function
- File: `src/integration_coworker/health/server.py` lines 505-515
- Added `syntax_validator_tree_sitter_available` gauge to /metrics endpoint

### Test Coverage
- File: `tests/codegen/test_tree_sitter_availability.py` (7 tests)
- Key tests:
  - `test_is_tree_sitter_available_exists`
  - `test_metrics_includes_tree_sitter_availability`
  - `test_metrics_tree_sitter_value_is_valid`
  - `test_warning_log_format_when_unavailable`

### Reproduction
```bash
pytest tests/codegen/test_tree_sitter_availability.py -v
# 7 passed
```

---

## B-004: HITL Gate Has No Timeout Config

**Status**: ✅ FIXED  
**Severity**: P1  
**Gap Reference**: DEEP_PRODUCTION_AUDIT_v2.md Gap I  

### Original Behavior
- HITL gate could wait indefinitely for human approval
- No way to detect stale HITL requests
- No timestamp recorded when HITL was requested

### Fix Applied
- File: `src/integration_coworker/graph/bounds.py` lines 106-108, 221
- Added `hitl_max_wait_seconds` to `BoundedExecutionConfig` (default: 86400 = 24h)
- Added `HITL_MAX_WAIT_SECONDS` env var support
- File: `src/integration_coworker/graph/nodes/hitl_gate.py` lines 103, 121
- Added `hitl_requested_at` timestamp to interrupt payload

### Test Coverage
- File: `tests/graph/test_hitl_timeout_config.py` (12 tests)
- Key tests:
  - `test_config_has_hitl_max_wait_seconds`
  - `test_hitl_max_wait_from_env`
  - `test_payload_includes_timestamp`
  - `test_can_compute_age_from_timestamp`

### Reproduction
```bash
pytest tests/graph/test_hitl_timeout_config.py -v
# 12 passed
```

---

## Commits on `prod-gaps-phase1`

1. `8c731f9` - fix(docs): correct date in audit doc
2. `068a914` - feat(runtime): block unknown checkpoint version in production profile  
3. `c6e045a` - feat(codegen): strict gates fail on missing tools in production
4. `dc466d2` - docs: add Phase 1 bug log with reproducible test coverage
