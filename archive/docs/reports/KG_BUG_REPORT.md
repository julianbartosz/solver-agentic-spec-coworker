# Knowledge Graph (KG) Bug Report

**Date**: 2024-12-12  
**Tested Environment**: Python 3.11.14, PostgreSQL  
**Test Method**: Production integration runs + diagnostic scripts

---

## Summary

Two KG-related bugs were identified through production testing and diagnostic analysis:

| Bug ID | Severity | Description |
|--------|----------|-------------|
| KG-001 | **HIGH** | `step_bindings` table is never populated |
| KG-002 | **MEDIUM** | Pattern nodes not seeded (only 1/7 exist) |

Additionally, several warning-level issues were identified:

| Issue | Severity | Description |
|-------|----------|-------------|
| WARN-001 | LOW | ConnectionWrapper garbage collection warnings |
| WARN-002 | LOW | New templates created for semantically similar tasks |

---

## Bug Details

### BUG-KG-001: step_bindings Table Never Populated [HIGH]

**Location**: `src/integration_coworker/graph/nodes/persist_kg_learning.py`

**Evidence**:
```
kg.workflow_steps: 10 rows
kg.step_bindings: 0 rows
```

**Root Cause Analysis**:

The `persist_kg_learning` node creates workflow_steps entries but never creates corresponding step_bindings entries. The table schema exists but no code populates it:

```python
# persist_kg_learning.py - creates workflow steps
for wf_node in state.workflow_nodes:
    _upsert_workflow_step(
        cur,
        template_node_id=template_node_id,
        step_key=wf_node.node_key,
        ...
    )
# BUT: No _upsert_step_binding() is ever called!
```

**Impact**:
- GraphRAG cannot recommend specific endpoints for specific workflow steps
- Template reuse lacks endpoint binding information
- Cross-provider learning is incomplete

**Fix Required**:
Add `_upsert_step_binding()` function and call it after creating workflow_steps, iterating over `state.endpoint_bindings` to link:
- `workflow_step_id` (from kg.workflow_steps)
- `endpoint_node_id` (from kg.nodes WHERE node_type='endpoint')

---

### BUG-KG-002: Pattern Nodes Not Seeded [MEDIUM]

**Location**: `src/integration_coworker/persistence/seed_kg.py` and `init_schema()`

**Evidence**:
```
STANDARD_PATTERNS has 7 patterns but only 1 exists in kg.nodes:
  - pattern.crud_create (created via learning)
  
Missing patterns:
  - pattern.crud_read
  - pattern.crud_update
  - pattern.crud_delete
  - pattern.crud_list
  - pattern.nested_resource
  - pattern.batch_operation
```

**Root Cause Analysis**:

The `STANDARD_PATTERNS` dictionary in `kg/__init__.py` defines 7 cross-provider patterns, but these are never seeded into the database. Pattern nodes only appear when:
1. A workflow is detected that matches a pattern during `persist_kg_learning`

The `seed_kg.py` file exists but:
1. It's never called during `init_schema()`
2. It's not run as a migration

**Impact**:
- Cross-provider pattern matching falls back to rule-based inference
- New providers can't benefit from learned patterns
- GraphRAG scoring degrades for providers without prior runs

**Fix Required**:
Either:
1. Call `seed_kg()` during schema initialization (preferred)
2. Add a one-time migration to seed patterns
3. Seed patterns lazily on first query (least intrusive)

---

## Warning-Level Issues

### WARN-001: Connection Garbage Collection Warnings

**Message**: `ConnectionWrapper was garbage collected without being closed. Use 'with db.get_connection() as conn:' pattern for proper cleanup.`

**Location**: Multiple locations in `kg/__init__.py` and other modules

**Impact**: Minor - connections are being rolled back correctly, but resource cleanup is not optimal.

**Fix**: Ensure all `get_connection()` calls use context managers (`with` blocks).

---

### WARN-002: New Templates Created for Similar Tasks

**Observation**: 
- "Send an SMS message" creates `template.twilio_messaging_v1.send_sms_message`
- "Send a text notification" creates `template.twilio_messaging_v1.send_text_notification`

Even though the template from the first task was successfully REUSED during planning, a NEW template was created during persistence.

**Current Behavior** (actually correct by design):
- Template **selection** works: Similar tasks DO find and reuse existing templates for workflow planning
- Template **persistence** creates unique entries per task_slug: Each unique task description gets its own template record

**Impact**: Template proliferation for semantically similar tasks. The KG grows with many similar templates rather than consolidating.

**Assessment**: This is arguably correct behavior - tracking unique task types allows fine-grained learning. However, it could be improved with template deduplication or merging.

---

## Test Results

### Production Run #1: "Send an SMS message using Twilio API"
```
Status: ✅ Success
KG Nodes Created: 54 (1 provider, 1 task, 1 template, 50 endpoints, 1 pattern)
KG Edges Created: 54
Workflow Steps: 5
Step Bindings: 0 ❌
```

### Production Run #2: "Send a text notification to a mobile number"
```
Status: ✅ Success
Template Reuse: ✅ Found and used template from Run #1
Source: "exact" (template matched)
Usage Count Incremented: ✅ (template.twilio_messaging_v1.send_sms_message: 1→1)
New Template Created: ✅ (template.twilio_messaging_v1.send_text_notification)
```

### GraphRAG Query Test
```
Provider: twilio_messaging_v1
Query: "Test task for diagnostic"
Result: ✅ 2 templates returned
Embedding Scoring: ✅ Working
```

---

## KG Health Summary

| Metric | Status |
|--------|--------|
| Node creation | ✅ Working |
| Edge creation | ✅ Working |
| Workflow steps | ✅ Working |
| Step bindings | ❌ **NOT WORKING** |
| Pattern seeding | ⚠️ Partial (1/7) |
| Template embeddings | ✅ Working |
| Template→Pattern edges | ✅ Working |
| Usage tracking | ✅ Working |
| GraphRAG queries | ✅ Working |

---

## Recommended Fixes (Priority Order)

### 1. [HIGH] Fix step_bindings population

```python
# In persist_kg_learning.py, add after workflow_steps creation:

def _upsert_step_binding(
    cur,
    step_id: int,
    endpoint_node_id: int,
    binding_type: str = "primary",
    is_postgres: bool = False,
) -> int:
    """Create a step→endpoint binding in kg.step_bindings."""
    if is_postgres:
        cur.execute("""
            INSERT INTO kg.step_bindings (step_id, endpoint_node_id, binding_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (step_id, endpoint_node_id) DO UPDATE SET
                binding_type = EXCLUDED.binding_type
            RETURNING id
        """, (step_id, endpoint_node_id, binding_type))
        return cur.fetchone()[0]
    # ... SQLite variant
```

### 2. [MEDIUM] Seed standard patterns on init

```python
# In postgres.py init_schema() or db.py init_schema():

from integration_coworker.kg import STANDARD_PATTERNS

def _seed_standard_patterns(cur, is_postgres):
    """Seed STANDARD_PATTERNS if not already present."""
    for pattern_key, pattern_data in STANDARD_PATTERNS.items():
        # Upsert pattern node
        ...
```

### 3. [LOW] Fix connection cleanup

Audit all `get_connection()` calls and ensure they use `with` blocks.

---

## Files Requiring Changes

| File | Change Type | Bug Fixed |
|------|-------------|-----------|
| `persist_kg_learning.py` | Add step_binding population | KG-001 |
| `postgres.py` or `db.py` | Add pattern seeding | KG-002 |
| `kg/__init__.py` | Fix connection cleanup | WARN-001 |

---

## Verification Commands

After fixes are applied, run:

```bash
# Run diagnostic
python scripts/diagnose_kg_bugs.py

# Expected output: 0 bugs found

# Run production integration
python -m integration_coworker.cli run -s specs/twilio_messaging_v1.json -t "Send SMS" -v

# Check step_bindings populated
psql -c "SELECT COUNT(*) FROM kg.step_bindings"
# Expected: > 0

# Check patterns seeded
psql -c "SELECT key FROM kg.nodes WHERE node_type='pattern'"
# Expected: 7 patterns
```
