# Bug Fix Analysis: Connection Pool Exhaustion & FK Constraint

## Bug #7: Connection Pool Exhaustion

### Problem Statement
`db.get_connection()` returns a raw pooled connection via `pool.getconn()`, but callers use a non-context-manager pattern (`conn = db.get_connection(); ... ; conn.close()`). If exceptions occur before `close()`, connections leak. The pool has `max_size=10` with `timeout=1.0`, leading to exhaustion during rapid runs.

### Alternative Solutions

#### Option A: Increase Pool Size + Longer Timeout
**Description**: Simply increase `max_size` to 20-50 and `timeout` to 5.0s.

**Pros**:
- Minimal code changes (2 lines)
- Quick fix

**Cons**:
- Doesn't fix the root cause (connection leaks)
- Just delays the problem
- Wastes database resources
- Not scalable for concurrent users

**Scalability**: ❌ Poor - masks the problem

---

#### Option B: Add Context Manager to db.get_connection()
**Description**: Make `db.get_connection()` return a context manager that ensures cleanup.

**Pros**:
- Root cause fix
- Pythonic pattern
- Works for both SQLite and Postgres

**Cons**:
- Requires updating ALL callers (40+ locations)
- Breaking API change
- High risk of missing some callers

**Scalability**: ✅ Good if done completely

---

#### Option C: Make db.get_connection() Auto-Close (Wrapper)
**Description**: Create a connection wrapper that auto-closes when garbage collected, with explicit `close()` as backup.

**Pros**:
- Backward compatible (no caller changes)
- GC ensures cleanup even on exceptions
- Works with existing code patterns

**Cons**:
- Relies on GC timing (unpredictable)
- Complex implementation
- May not work for long-lived connections

**Scalability**: ⚠️ Medium - GC timing unreliable

---

#### Option D: Hybrid Approach - New Context Manager + Deprecation
**Description**: 
1. Add `db.connection()` as context manager (new API)
2. Keep `db.get_connection()` but add deprecation warning + auto-return on GC
3. Increase pool size as buffer

**Pros**:
- Non-breaking (backward compatible)
- Encourages migration to safe pattern
- Immediate relief via pool increase
- Long-term migration path

**Cons**:
- Two APIs temporarily
- More code to maintain

**Scalability**: ✅ Excellent - provides migration path

---

#### Option E: Use psycopg_pool's Proper Pattern
**Description**: The postgres.py already has `get_connection()` as context manager! The issue is `db.get_connection()` bypasses it by calling `pool.getconn()` directly. Fix: make db.get_connection() delegate properly.

**Pros**:
- Uses existing infrastructure correctly
- Minimal changes to db.py
- Proper pool lifecycle management
- psycopg_pool handles all edge cases

**Cons**:
- `db.get_connection()` return type changes (context manager)
- Need to update all callers to use `with` pattern

**Scalability**: ✅ Excellent - leverages battle-tested library

---

### Recommended Solution: Option E + Gradual Migration

**Rationale**:
1. postgres.py already has the right pattern (`get_connection()` as context manager)
2. The problem is db.py bypasses it with raw `pool.getconn()`
3. Fix db.py to return a context manager
4. Add backward-compatible wrapper that works with both patterns

**Implementation**:
1. Create `ConnectionWrapper` class that:
   - Implements context manager protocol
   - Auto-returns to pool on `__exit__`
   - Also supports `.close()` for backward compatibility
   - Delegates all other calls to underlying connection

---

## Bug #8: run_status FK Constraint Error

### Problem Statement
`persist_run_outcome` inserts into `run_status` with `task_id` referencing `integration_tasks.id`. However, `task_id` comes from `state.persisted_ids.get("task_id")` which may reference a deleted/non-existent row.

### Root Cause Analysis
The error `Key (task_id)=(36) is not present in table "integration_tasks"` means:
1. `integration_tasks` rows are being deleted between runs (likely during tests or cleanup)
2. Or `persist_gold_checkpoint` isn't running before `persist_run_outcome`
3. Or there's a race condition in concurrent runs

### Alternative Solutions

#### Option A: Make task_id Nullable
**Description**: Change `task_id BIGINT REFERENCES ...` to `task_id BIGINT NULL REFERENCES ...` or remove the FK.

**Pros**:
- Simple DDL change
- Allows orphaned run_status records
- Won't fail on missing task

**Cons**:
- Loses referential integrity
- Orphaned records harder to query
- May mask bugs where task creation fails

**Scalability**: ⚠️ Medium - trades correctness for stability

---

#### Option B: INSERT run_status BEFORE task, Update Later
**Description**: Insert run_status with NULL task_id at workflow start, update with real task_id after persist_gold_checkpoint.

**Pros**:
- Always has run_status record
- FK constraint still works (NULL is allowed)
- Better observability (can track failed runs)

**Cons**:
- Requires two writes to run_status
- Need to track run_id earlier in workflow
- More complex logic

**Scalability**: ✅ Good - proper lifecycle management

---

#### Option C: ON DELETE SET NULL
**Description**: Add `ON DELETE SET NULL` to the FK constraint.

**Pros**:
- Keeps FK for integrity when task exists
- Doesn't fail when task is deleted
- Database handles cleanup

**Cons**:
- Only helps on DELETE, not initial INSERT
- Doesn't fix the root issue

**Scalability**: ❌ Poor - doesn't solve the problem

---

#### Option D: Create Dummy Task if Missing
**Description**: In persist_run_outcome, check if task_id exists and create a placeholder task if not.

**Pros**:
- Ensures FK is always valid
- Run tracking always works

**Cons**:
- Creates garbage data
- Hides bugs in gold checkpoint
- Complex logic

**Scalability**: ❌ Poor - creates garbage data

---

#### Option E: Defensive Check + Skip FK Insert
**Description**: Check if task_id exists in DB before insert. If not, insert without task_id (NULL).

**Pros**:
- Defensive programming
- Doesn't create garbage
- Still captures run status

**Cons**:
- Extra DB query
- May mask underlying bugs

**Scalability**: ⚠️ Medium

---

#### Option F: Fix Root Cause - Proper Workflow Order
**Description**: The real issue is that `task_id` in `persisted_ids` references a task that doesn't exist yet or was deleted. We should:
1. Ensure `persist_gold_checkpoint` always runs before `persist_run_outcome`
2. Validate `task_id` exists before using it
3. Make `task_id` nullable but log when it happens

**Pros**:
- Fixes root cause
- Maintains data integrity when possible
- Degrades gracefully when not

**Cons**:
- More complex implementation

**Scalability**: ✅ Excellent - proper lifecycle

---

### Recommended Solution: Option F (Root Cause) + Option A (Safety)

**Rationale**:
1. Make `task_id` nullable as safety valve (prevents crashes)
2. Add validation in `persist_run_outcome` to log when task_id is missing
3. The workflow graph should ensure proper ordering, but DB should be resilient

**Implementation**:
1. Update DDL to make `task_id` nullable
2. Add defensive check in persist_run_outcome
3. Log warning when task_id is NULL (indicates workflow bug)
