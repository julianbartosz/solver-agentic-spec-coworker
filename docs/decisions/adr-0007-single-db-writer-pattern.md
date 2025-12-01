# ADR-0007: Single DB Writer Pattern

## Status

**Accepted** — Implemented in v1

## Date

2025-12-01

## Context

The Integration Coworker runs a LangGraph workflow with 14+ nodes that transform API specifications into generated integration code. Data flows through two medallion layers:

- **Silver Layer**: Parsed spec data (endpoints, schemas, entities, spec_chunks)
- **Gold Layer**: Integration artifacts (tasks, flow nodes, policies, code_artifacts)

### The Problem

In an agentic workflow, multiple nodes execute in sequence with potential failure points at each step. Without careful design, database writes could occur scattered throughout the graph:

```python
# ANTI-PATTERN: Distributed writes without coordination
def build_silver_api_model(state):
    endpoints = parse_endpoints(spec)
    db.insert("endpoints", endpoints)  # Writes here...
    schemas = parse_schemas(spec)      # ...might fail here
    db.insert("schemas", schemas)      # ...leaving orphaned data
```

This creates several risks:
1. **Partial writes** — If node 7 crashes after nodes 3 and 5 wrote to DB, the database has inconsistent data
2. **Rollback complexity** — No clear transaction boundary for recovery
3. **Dry-run difficulty** — Must check `dry_run` flag in every node that writes
4. **Testing complexity** — Every node with DB access needs database mocking

### Requirements

1. **Atomic semantics** — Either all Silver data persists, or none does
2. **Dry-run support** — Skip all writes when `options.dry_run = True`
3. **ID backfilling** — After persist, in-memory objects must have database IDs for downstream use
4. **Testability** — Easy to test node logic without database setup

## Decision

**Centralize all database writes into exactly three checkpoint nodes:**

| Node | Layer | Tables Written |
|------|-------|----------------|
| `persist_silver_checkpoint` | Silver | source_systems, spec_documents, spec_sections, endpoints, schemas, fields, entities, events, spec_chunks |
| `persist_gold_checkpoint` | Gold | integration_tasks, workflow_templates, flow_nodes, flow_edges, endpoint_bindings, policies, code_artifacts |
| `persist_run_outcome` | Metrics | run_status, rag_metrics, kg_learning rows |

**All other nodes operate exclusively on in-memory `WorkflowState`.**

### Graph Structure

```
plan_run → ingest_spec → detect_and_parse_spec → build_silver_api_model → embed_spec_chunks
                                                                              ↓
                                                          persist_silver_checkpoint ← CHECKPOINT
                                                                              ↓
understand_task → align_task_with_kg → plan_integration_flow → attach_policies → generate_code_and_tests
                                                                                        ↓
                                                                  persist_gold_checkpoint ← CHECKPOINT
                                                                                        ↓
                               persist_kg_learning → [repo nodes] → validate → build_report
                                                                                        ↓
                                                                    persist_run_outcome ← CHECKPOINT
```

### Implementation Rules

From `docs/design-doc-appendices.md` (Appendix C.3):

> - Only `persist_silver_checkpoint`, `persist_gold_checkpoint`, and `persist_run_outcome` may write to the database.
> - Only `apply_repo_integration_changes` may write to the filesystem.
> - All other nodes mutate in-memory drafts only.

### Code Example

```python
# persist_silver_checkpoint.py (simplified)
def persist_silver_checkpoint(state: WorkflowState) -> WorkflowState:
    if state.options.dry_run:
        state.persisted_ids.update({"silver_dry_run": True, ...})
        state.completed_steps.append("persist_silver_checkpoint")
        return state

    conn = db.get_connection()
    cur = conn.cursor()
    
    # 1. Upsert SourceSystem
    cur.execute(upsert_ignore("source_systems", ...), (...))
    source_system_id = cur.fetchone()[0]
    state.source_system.id = source_system_id  # Backfill ID
    
    # 2. Insert SpecDocuments, Endpoints, Schemas, etc.
    # ... (all Silver tables)
    
    conn.commit()
    state.completed_steps.append("persist_silver_checkpoint")
    return state
```

## Alternatives Considered

### 1. Distributed Writes with Per-Node Transactions

Each node writes its outputs within a transaction, commits on success.

```python
def build_silver_api_model(state):
    with db.transaction():
        endpoints = parse_endpoints(spec)
        db.insert("endpoints", endpoints)
        schemas = parse_schemas(spec)
        db.insert("schemas", schemas)
```

| Aspect | Assessment |
|--------|------------|
| Pros | Lower memory footprint, finer-grained recovery |
| Cons | Transaction management complexity, harder dry-run |
| Verdict | Better for v2 multi-user scenarios |

### 2. Event Sourcing

Append immutable events, replay to derive current state.

```python
class EndpointParsed(Event):
    endpoint_id: str
    method: str
    path: str
    
def build_silver_api_model(state):
    for endpoint in parse_endpoints(spec):
        emit(EndpointParsed(endpoint))
```

| Aspect | Assessment |
|--------|------------|
| Pros | Full audit trail, temporal queries, perfect replay |
| Cons | Massive complexity increase, requires event store infrastructure |
| Verdict | Overkill for v1 single-user context |

### 3. Saga Pattern with Compensating Actions

Each node writes immediately but registers a compensating action for rollback.

```python
def build_silver_api_model(state):
    endpoints = parse_endpoints(spec)
    db.insert("endpoints", endpoints)
    state.register_compensate(lambda: db.delete("endpoints", endpoints))
```

| Aspect | Assessment |
|--------|------------|
| Pros | Fine-grained recovery, distributed systems friendly |
| Cons | Compensating action logic is error-prone, not needed for single-process |
| Verdict | Designed for microservices, overkill here |

### 4. Write-Ahead Log (WAL) with Atomic Commit

Log all intended writes, then commit atomically.

| Aspect | Assessment |
|--------|------------|
| Pros | Database-native pattern, excellent recovery |
| Cons | Requires WAL infrastructure, more complex than checkpoint nodes |
| Verdict | Good pattern, but checkpoint nodes achieve similar effect more simply |

## Comparison Matrix

| Criterion | Single Writer | Distributed Txns | Event Sourcing | Saga |
|-----------|--------------|------------------|----------------|------|
| Implementation complexity | **Low** | Medium | High | High |
| Memory footprint | High | **Low** | **Low** | **Low** |
| Failure recovery | Coarse | Fine | **Excellent** | Fine |
| Dry-run support | **Trivial** | Medium | Hard | Medium |
| Audit trail | Weak | Weak | **Excellent** | Medium |
| Fit for v1 | ✅ **Excellent** | Good | Overkill | Overkill |
| Fit for v2 (multi-user) | Poor | **Good** | Excellent | Good |

## Consequences

### Positive

1. **Simple mental model** — "Only 3 nodes write to DB" is easy to remember and enforce
2. **Trivial dry-run** — Check `options.dry_run` in exactly 3 places
3. **Atomic semantics** — Silver checkpoint either fully commits or doesn't
4. **Easy testing** — Mock 3 nodes to test all business logic without database
5. **Clear boundaries** — Silver checkpoint completes before Gold processing begins

### Negative

1. **Memory pressure** — Must hold entire Silver model in memory until checkpoint (~50-100MB for large specs)
2. **All-or-nothing recovery** — If Gold checkpoint fails, lose all work since Silver checkpoint
3. **No incremental progress** — Users don't see "3/10 endpoints persisted" progress updates
4. **Latency batching** — All writes happen at checkpoint, not streamed during processing

### Neutral

1. **ID backfilling** — After persist, must walk state objects to set `.id` fields (implemented, works well)
2. **Schema prefixes** — Postgres uses `spec_silver.` and `integration_gold.` prefixes, SQLite doesn't (handled by `get_engine_type()`)

## Scalability Limits

The pattern works well for v1 constraints:
- Single user, single run
- Specs under 1000 endpoints
- In-memory state under 500MB

It will **not scale** for:
- Multi-user concurrent runs (checkpoint contention)
- Very large specs (memory exhaustion)
- Resumable runs (can't resume from mid-Silver)

## Migration Path to v2

When scalability limits are reached, migrate to **Distributed Writes with Per-Node Transactions**:

### Phase 1: Add Transaction Wrapper

```python
# New: transaction context manager per node
def build_silver_api_model(state):
    with db.node_transaction("build_silver_api_model"):
        # existing logic
        # writes happen inside transaction
```

### Phase 2: Streaming Writes

```python
# Migrate from batch to streaming
for endpoint in parse_endpoints(spec):
    db.insert("endpoints", endpoint)  # Write immediately
    endpoint.id = db.last_insert_id()  # ID available immediately
```

### Phase 3: Checkpoint Nodes Become No-Ops

```python
def persist_silver_checkpoint(state):
    # Verify all Silver data was written by upstream nodes
    # No actual writes needed
    state.completed_steps.append("persist_silver_checkpoint")
    return state
```

### Phase 4: Add Resume Capability

```python
# Track completed nodes in run_status table
def resume_run(run_id):
    last_completed = db.get_last_completed_node(run_id)
    return graph.run_from(last_completed)
```

## Implementation References

### Checkpoint Nodes

- `src/integration_coworker/graph/nodes/persist_silver_checkpoint.py` — Silver layer persistence (~250 LOC)
- `src/integration_coworker/graph/nodes/persist_gold_checkpoint.py` — Gold layer persistence (~230 LOC)
- `src/integration_coworker/graph/nodes/persist_run_outcome.py` — Run metrics persistence

### Graph Wiring

- `src/integration_coworker/graph/runtime.py` (lines 248-294) — Checkpoint node registration and edge definitions

### SQL Helpers

- `src/integration_coworker/persistence/sql_helpers.py` — `upsert_ignore()`, `select_by_columns()`, `get_engine_type()`

### Tests

- `tests/test_m4_persistence.py::test_persistence_writes_to_database` — Verifies checkpoint behavior
- `tests/graph/test_persist_results.py::test_dry_run_no_db_writes` — Verifies dry-run semantics

## Design Doc References

- **Section 5.4**: "Database writes in v1 occur only at three checkpoint nodes"
- **Appendix C.3**: Node contracts specifying which nodes may write

## Related ADRs

- **ADR-0001**: Initial Architecture — Established medallion model requiring Silver/Gold separation
- **ADR-0006**: [Medallion Data Architecture](adr-0006-medallion-data-architecture.md) — Defines Silver/Gold schema boundaries

## Notes

This pattern was chosen for v1 pragmatism. The team acknowledges it's not the most scalable approach but provides the simplest implementation for current requirements. The migration path is documented to guide future evolution when scalability limits are reached.
