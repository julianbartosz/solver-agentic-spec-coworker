# Bug Fix Execution Plan (Artifact C)

**Implementation Checklist for Production Demo Fixes**

---

## Phase 1: P0.1 - Duplicate Key Constraint (Priority 1)

### Step 1.1: Create Migration
- [ ] Create `migrations/002_remove_uri_unique.sql`
- [ ] Migration drops `spec_documents_uri_key` constraint

### Step 1.2: Update SQLite Schema
- [ ] Update `src/integration_coworker/persistence/db.py`
- [ ] Remove `UNIQUE(uri)` from SQLite spec_documents table
- [ ] Keep `UNIQUE(source_system_id, sha256)`

### Step 1.3: Add Test
- [ ] Create `tests/persistence/test_spec_documents_upsert.py`
- [ ] Test: Same URI, different source_system_id succeeds
- [ ] Test: Same source_system_id + sha256 is deduplicated

### Step 1.4: Validate
- [ ] Run: `pytest tests/persistence/test_spec_documents_upsert.py -v`
- [ ] Run existing persistence tests

---

## Phase 2: P0.3 - Connection Lifecycle (Priority 2)

### Step 2.1: Add Retry Utilities
- [ ] Create `src/integration_coworker/utils/retry.py`
- [ ] Implement `@retry` decorator with exponential backoff
- [ ] Add configurable max_attempts and base delay

### Step 2.2: Add Connection Health Check
- [ ] Update `src/integration_coworker/persistence/postgres.py`
- [ ] Add `verify_connection_health()` function
- [ ] Add `get_connection_with_retry()` wrapper

### Step 2.3: Add Connection Wrapper Improvements
- [ ] Update `src/integration_coworker/persistence/db.py`
- [ ] Ensure proper cleanup in all exit paths
- [ ] Add connection state validation

### Step 2.4: Add Test
- [ ] Create `tests/persistence/test_connection_retry.py`
- [ ] Test: Connection retry on failure
- [ ] Test: Health check before query

### Step 2.5: Validate
- [ ] Run: `pytest tests/persistence/test_connection_retry.py -v`
- [ ] Run: `pytest tests/persistence/ -v`

---

## Phase 3: P0.2 - LangSmith Payload Size (Priority 3)

### Step 3.1: Create Sanitizer Module
- [ ] Create `src/integration_coworker/utils/trace_sanitizer.py`
- [ ] Implement `sanitize_trace_data()`
- [ ] Add recursive truncation with max_str_len
- [ ] Add `[TRUNCATED]` markers for visibility

### Step 3.2: Integrate with Runtime
- [ ] Update `src/integration_coworker/runtime.py`
- [ ] Register sanitizer as LangSmith callback
- [ ] Add size check before trace upload

### Step 3.3: Add Test
- [ ] Create `tests/utils/test_trace_sanitizer.py`
- [ ] Test: Large string truncation
- [ ] Test: Nested dict handling
- [ ] Test: Total payload size limit

### Step 3.4: Validate
- [ ] Run: `pytest tests/utils/test_trace_sanitizer.py -v`

---

## Phase 4: P1.1 - Bare Except Fix (Priority 4)

### Step 4.1: Update Codegen Prompts
- [ ] Update `src/integration_coworker/codegen/prompts.py`
- [ ] Replace all `except:` with `except Exception:`
- [ ] Update test templates

### Step 4.2: Update Flow Templates
- [ ] Update `src/integration_coworker/graph/nodes/generate_code_and_tests.py`
- [ ] Fix exception handling patterns in templates

### Step 4.3: Add Test
- [ ] Add test to verify generated code has no bare except
- [ ] Run: `ruff check src/integration_coworker/codegen/`

---

## Phase 5: P1.2 - Module Import Resolution (Priority 5)

### Step 5.1: Update Sandbox PYTHONPATH
- [ ] Update `src/integration_coworker/codegen/sandbox.py`
- [ ] Add `integrations/clients` to PYTHONPATH in _run_command

### Step 5.2: Validate
- [ ] Run sandbox execution with generated client imports

---

## Phase 6: Production Validation

### Step 6.1: Run Unit Tests
```bash
pytest tests/ -v --tb=short
```

### Step 6.2: Run Integration Tests
```bash
VALIDATION_PROFILE=live pytest tests/integration/ -v -m integration_live
```

### Step 6.3: Run Demo Showcase
```bash
./scripts/demo-final-showcase.sh --live --fresh
```

### Step 6.4: Verify All Gates Pass
- [ ] 7/7 sandbox gates pass
- [ ] No duplicate key errors
- [ ] No LangSmith 422 errors
- [ ] No connection lost errors

---

## Rollback Plan

### If P0.1 Fix Causes Issues
```sql
-- Rollback migration (re-add constraint)
ALTER TABLE spec_silver.spec_documents 
ADD CONSTRAINT spec_documents_uri_key UNIQUE (uri);
```

### If P0.3 Fix Causes Issues
- Revert retry wrapper
- Increase pool max_size as temporary fix

### If P0.2 Fix Causes Issues
- Disable sanitizer via environment flag
- Set `LANGCHAIN_TRACING_V2=false` as emergency override

---

## Success Criteria

| Criterion | Validation |
|-----------|------------|
| No duplicate key errors | Demo runs 3x consecutively without error |
| LangSmith traces upload | Check LangSmith dashboard for runs |
| No connection errors | 0 ResourceWarning messages in logs |
| All tests pass | `pytest tests/ -v` exits 0 |
| Demo passes | `./scripts/demo-final-showcase.sh --live` succeeds |
