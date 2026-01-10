# Bug Fix Evidence Pack

**Generated**: 2024-12-22 (Updated)
**Status**: ✅ FIXES COMPLETE - Pending Test Matrix
**Last Demo Run**: `logs/demo-final/demo-20251222-085056.log`

---

## Section 1: Git Diff Summary (Ground Truth)

### Files Modified (git diff --stat)
```
migrations/001_baseline_v1.sql                      |   3 +-
src/integration_coworker/codegen/sandbox.py        | 498 ++++++++++++---
src/integration_coworker/graph/nodes/ingest_spec.py |  12 +-
src/integration_coworker/graph/nodes/persist_silver_checkpoint.py |   6 +-
src/integration_coworker/graph/runtime.py           |  42 +-
src/integration_coworker/persistence/db.py          |  66 ++-
src/integration_coworker/persistence/postgres.py    |  84 ++-
src/integration_coworker/persistence/seed_kg.py     | 116 ++--
src/integration_coworker/utils/trace_sanitizer.py   | 150 +++++
```

### Demo Error Summary (grep -E from demo log)
```
ERROR    integration_coworker.graph.nodes.generate_code_and_tests: Sandbox validation failed: FAILED: 2/3 gates passed
WARNING  integration_coworker.persistence.seed_kg: Failed to seed template (x6) - transaction aborted
[LINT] ✗ E501 Line too long (94 > 88)
```

### Final Status
- **Run Completed**: ✅ Yes (21 nodes executed)
- **Success**: ❌ No (sandbox gate failed on E501 line length)
- **Errors**: 1 (sandbox lint)
- **Code Generated**: ✅ 3 artifacts (client, flow, test)

---

## Section 2: P0.1 Composite Key ✅ COMPLETE

### Migration
**File**: `migrations/002_add_repo_root_composite_key.sql` (EXISTS, STAGED)

```sql
ALTER TABLE spec_silver.spec_documents ADD COLUMN IF NOT EXISTS repo_root TEXT;
UPDATE spec_silver.spec_documents SET repo_root = '__legacy__' WHERE repo_root IS NULL;
ALTER TABLE spec_silver.spec_documents ALTER COLUMN repo_root SET NOT NULL;
ALTER TABLE spec_silver.spec_documents ADD CONSTRAINT spec_documents_repo_uri_key UNIQUE (repo_root, uri);
```

### Insert Sites Updated
| File | Change |
|------|--------|
| `ingest_spec.py:849` | Added `"repo_root"` to column list |
| `ingest_spec.py:1023` | Added `"repo_root"` to column list |
| `persist_silver_checkpoint.py:117` | Added `"repo_root"` to column list |

### Identity Model
| Identity | Columns | Purpose |
|----------|---------|---------|
| Content Identity | `(source_system_id, sha256)` | Dedup same spec content |
| Source Identity | `(repo_root, uri)` | Per-repo location uniqueness |

---

## Section 3: P0.2 LangSmith Byte Budget ✅ COMPLETE

### Implementation

**File**: `src/integration_coworker/graph/runtime.py`

```diff
+    from integration_coworker.utils.trace_sanitizer import (
+        is_tracing_healthy,
+        sanitize_trace_data,
+    )
+    
+    # P0.2 Fix: Sanitize inputs/outputs before sending to LangSmith
+    def _sanitize_for_trace(data):
+        """Sanitize data before LangSmith trace upload."""
+        if hasattr(data, '__dict__'):
+            return sanitize_trace_data({
+                "run_id": getattr(data, 'run_id', None),
+                "task_type": getattr(data, 'task_type', None),
+                "provider_code": getattr(data, 'provider_code', None),
+                "status": getattr(data, 'status', None),
+                "completed_steps": list(getattr(data, 'completed_steps', []) or []),
+                "_sanitized": True,
+            }, max_total_bytes=100_000)
+        elif isinstance(data, dict):
+            return sanitize_trace_data(data, max_total_bytes=500_000)
+        return data
+
     @traceable(
         name=node_name,
+        process_inputs=_sanitize_for_trace,  # P0.2: Sanitize before trace
+        process_outputs=_sanitize_for_trace,  # P0.2: Sanitize before trace
     )
```

**File**: `src/integration_coworker/utils/trace_sanitizer.py`

New functions added:
- `serialize_with_budget(data, max_bytes)` - Hard size cap with progressive field dropping
- `batch_traces_with_budget(traces, batch_budget, per_trace_budget)` - Batch splitting
- `wrap_langsmith_ingest(original_ingest)` - Boundary wrapper
- Constants: `BATCH_SIZE_BUDGET = 5_000_000`, `SINGLE_TRACE_BUDGET = 2_000_000`

### Boundary Enforcement
- ✅ Tracing health check at run start (`is_tracing_healthy()`)
- ✅ Reset tracing state at run start (`reset_tracing_state()`)
- ✅ `process_inputs` sanitizes WorkflowState before trace
- ✅ `process_outputs` sanitizes results before trace
- ✅ Graceful degradation after 3 consecutive payload errors

---

## Section 4: P0.3 Connection Pool Discipline ✅ COMPLETE

### Implementation

**File**: `src/integration_coworker/persistence/seed_kg.py`

**Problem**: Single failed INSERT aborted transaction, cascading to all subsequent inserts.

**Fix**: Added `conn.rollback()` on individual failures + context managers everywhere.

```diff
 def seed_knowledge_graph(force: bool = False) -> Tuple[int, int]:
-    conn = get_connection()
-    try:
+    # P0.3: Use context manager for proper connection lifecycle
+    with get_connection() as conn:
         ...
         for template in templates:
             try:
                 template_id = insert_fn(conn, template)
             except Exception as e:
+                # P0.3 Fix: Rollback failed transaction to prevent cascade
                 logger.warning(f"Failed to seed template {template['code']}: {e}")
+                conn.rollback()  # Clear the aborted transaction state
                 skipped += 1
-    finally:
-        conn.close()
```

**Functions converted to context managers**:
- `seed_knowledge_graph()`
- `seed_standard_patterns()`
- `get_template_count()`
- `list_seeded_templates()`
- `list_seeded_patterns()`

---

## Section 5: P1.1 Bare Except AST Check ✅ COMPLETE

### Implementation

**File**: `src/integration_coworker/codegen/sandbox.py`

New functions:
```python
def check_bare_except_ast(code: str, filename: str = "<generated>") -> List[str]:
    """Check for bare 'except:' clauses using AST parsing."""
    import ast
    issues: List[str] = []
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:  # bare except:
                    issues.append(f"{filename}:{node.lineno}: bare 'except:' clause...")
    except SyntaxError:
        pass  # Caught by other gates
    return issues

def check_artifacts_for_bare_except(artifacts: List["ArtifactFile"]) -> GateResult:
    """Gate function for bare except check."""
    ...
```

**Integration point** (step 4.5, before ruff):
```python
# Step 4.5: Check for bare except (defense-in-depth before ruff)
bare_except_result = check_artifacts_for_bare_except(artifacts)
if not bare_except_result.passed:
    final_result = self._fail_with_result(bare_except_result, validated_artifacts)
    return final_result
```

---

## Section 6: Remaining Issues (P1/P2)

### P1.2: ModuleNotFoundError
**Status**: NOT FIXED (low priority, import path issue)

### P1.3: Mock Not Called
**Status**: NOT FIXED (test isolation issue)

### P2.1: endpoint_id=None
**Status**: NOT FIXED (spec parsing edge case)

### P2.2: schema_mapping NoneType
**Status**: NOT FIXED (null check needed)

### P2.3: seed_kg Transaction Abort
**Status**: ✅ FIXED (P0.3 covers this)

---

## Section 7: Decision Matrix

| Bug | Priority | Status | Fix Location |
|-----|----------|--------|--------------|
| P0.1 Composite Key | P0 | ✅ DONE | migrations/002, ingest_spec.py |
| P0.2 LangSmith Byte Budget | P0 | ✅ DONE | runtime.py, trace_sanitizer.py |
| P0.3 Pool Discipline | P0 | ✅ DONE | seed_kg.py (+ rollback) |
| P1.1 Bare Except | P1 | ✅ DONE | sandbox.py (AST check) |
| P1.2 ModuleNotFoundError | P1 | ❌ DEFER | - |
| P1.3 Mock Not Called | P1 | ❌ DEFER | - |
| P2.1 endpoint_id=None | P2 | ❌ DEFER | - |
| P2.2 schema_mapping None | P2 | ❌ DEFER | - |

---

## Section 8: Test Matrix

| Test | Command | Expected | Actual |
|------|---------|----------|--------|
| Multi-root uniqueness | `bd run test-composite-key` | PASS | TODO |
| Large spec trace | `SPEC_PATH=specs/twilio_messaging.yaml bd demo` | No 422 | TODO |
| Transaction rollback | `bd run test-seed-kg-cascade` | No cascade | TODO |
| Bare except rejection | `bd run test-bare-except-ast` | REJECT | TODO |
| E501 resolution | Re-run demo | No E501 | TODO |

---

## Section 9: Next Steps

1. Run test matrix to validate fixes
2. Re-run production demo
3. Verify no 422 errors with Twilio spec
4. Verify no transaction cascade in seed_kg
3. Location: Must intercept actual trace upload, not just state sanitization

**Current trace_sanitizer.py exports**:
```python
sanitize_trace_data()      # Truncates dict values
is_tracing_healthy()       # Checks if disabled
record_tracing_error()     # Tracks failures
```

**Missing**:
```python
# Required: Budget-enforced serialization
def serialize_with_budget(data: dict, max_bytes: int = 5_000_000) -> bytes:
    """Serialize with hard size cap, dropping fields if needed."""
    pass

# Required: Ingest boundary wrapper  
def send_traces_with_budget(traces: List[dict]) -> None:
    """Send traces, respecting per-batch size limit."""
    pass
```

---

## Section 4: P0.3 Connection Pool - CRITICAL GAP

### Current Implementation
- ✅ `get_connection_with_retry()` added to `postgres.py:301-378`
- ✅ `_check_connection()` health check exists at `postgres.py:120`
- ✅ Retry logic in `db.py:352-402` with exponential backoff

### ❌ MISSING: Context Manager Discipline

**Problem**: Many call sites use `conn = db.get_connection()` without `with` statement.

**Evidence** (from demo log):
```
WARNING  integration_coworker.persistence.db: ConnectionWrapper was garbage collected 
without being closed. Use 'with db.get_connection() as conn:' pattern
```

**Audit Required**:
```bash
# Find all non-context-manager connection usage
rg "= db.get_connection\(\)" --type py | grep -v "with "
```

**Fix Required**:
1. Convert all `conn = db.get_connection()` to `with db.get_connection() as conn:`
2. Add test that simulates exception in DB block and asserts connection returned
3. Add deprecation warning to `get_connection()` when not used as context manager

---

## Section 5: P1.1 Bare Except - GAP IDENTIFIED

### Current Implementation
- ✅ `--select E,F,I,W,B` includes E722 in ruff checks (sandbox.py:692)
- ✅ Prompt guidance updated (prompts.py:26)

### ❌ MISSING: Post-Generation AST Check

**Problem**: Ruff E722 has known edge cases where `except: raise` isn't flagged.

**Required Fix**: Add explicit token/AST scan for bare `except:` in generated output.

**Location**: `sandbox.py` after code generation, before ruff runs.

```python
def check_bare_except(code: str) -> List[str]:
    """Reject bare except: that ruff might miss."""
    import ast
    issues = []
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                issues.append(f"Line {node.lineno}: bare 'except:' - must specify exception type")
    except SyntaxError:
        pass  # Will be caught by syntax gate
    return issues
```

---

## Section 6: Remaining P1/P2 Bugs (Not Yet Addressed)

### P1.2: ModuleNotFoundError (clients.integration_http_client)
- **Status**: NOT FIXED
- **Root Cause**: Generated code imports module not in sandbox
- **Required**: Update codegen prompts to use inline client or correct import

### P1.3: Mock Not Called
- **Status**: NOT FIXED  
- **Root Cause**: Mock path doesn't match actual call site
- **Required**: Review test template mock patching

### P2.1: endpoint_id=None
- **Status**: NOT FIXED
- **Evidence**: Logged during codegen
- **Required**: Trace propagation through build chain

### P2.2: schema_mapping NoneType
- **Status**: NOT FIXED
- **Required**: Defensive null checks

### P2.3: seed_kg transaction abort
- **Evidence**: 6 warnings in demo log
- **Root Cause**: Missing rollback on conflict
- **Required**: Add `conn.rollback()` in exception handler

---

## Section 7: Decision Matrix (Required)

| Decision | Options | Current Choice | Rationale | Risk |
|----------|---------|----------------|-----------|------|
| Spec Identity | Content vs Source | Both (composite) | Content dedup + per-repo uniqueness | May need explicit docs |
| Trace Budget | Drop fields vs Truncate | Truncate strings | Preserves structure | May hit limit with many traces |
| Pool Discipline | Warning vs Error | Warning (GC cleanup) | Backward compat | Leaks on exception |
| Bare Except Gate | Ruff only vs AST check | Ruff only | Fast | May miss edge cases |

---

## Section 8: Per-File Refactor Checklist

| File | Change | Status | Test |
|------|--------|--------|------|
| `migrations/002_*.sql` | Staged migration | ✅ Done | Run migration |
| `migrations/001_baseline_v1.sql` | Add repo_root to DDL | ✅ Done | - |
| `ingest_spec.py` | Add repo_root to inserts | ✅ Done | Multi-root test |
| `persist_silver_checkpoint.py` | Add repo_root to inserts | ✅ Done | - |
| `runtime.py` | Tracing health check | ✅ Done | Unit test |
| `trace_sanitizer.py` | Add byte budget | ❌ TODO | Size limit test |
| `db.py` | Retry logic | ✅ Done | Retry test |
| `db.py` | Audit conn usage | ❌ TODO | GC warning test |
| `postgres.py` | get_connection_with_retry | ✅ Done | - |
| `sandbox.py` | E722 in ruff rules | ✅ Done | - |
| `sandbox.py` | AST bare-except check | ❌ TODO | Bare except test |
| `prompts.py` | Exception guidance | ✅ Done | - |

---

## Section 9: Test Matrix (Required Before Merge)

| Test | Command | Expected | Status |
|------|---------|----------|--------|
| Multi-root uniqueness | Demo with 2 different repo_roots, same spec | No duplicate key error | ❌ TODO |
| Large spec trace | Demo with Twilio spec (26MB) | No 422/413 error | ❌ TODO |
| Connection retry | Kill DB mid-query, verify retry | Recovers after retry | ❌ TODO |
| Bare except rejection | Generate code with `except:`, verify gate fails | E722 or AST check fails | ❌ TODO |
| E501 resolution | Verify ruff format fixes long lines | No E501 in output | ❌ TODO |

---

## Next Actions

1. **P0.2**: Add byte budget serializer at trace upload boundary
2. **P0.3**: Audit and fix all non-context-manager connection usage
3. **P1.1**: Add AST-based bare except check
4. **P1.2-P2.x**: Address remaining bugs
5. **Tests**: Run full test matrix with evidence
