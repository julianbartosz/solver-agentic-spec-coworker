````markdown
# ADR-0009: Workflow Recovery Strategy

| Metadata       | Value                                      |
|----------------|--------------------------------------------|
| **Status**     | Proposed                                   |
| **Date**       | 2025-12-04                                 |
| **Deciders**   | Integration Coworker Team                  |
| **Supersedes** | —                                          |
| **Related**    | ADR-0007 (Single DB Writer Pattern)        |

---

## Context

LangGraph workflows can fail mid-execution. When a node fails (LLM timeout, parsing error, DB write failure), the user has three recovery options:

1. **Retry** — Re-run the failing node with the same inputs
2. **Skip** — Continue to the next node, treating the failed node as a no-op
3. **Restart** — Abandon current run and start fresh

The UI exposes all three options. Retry and restart are implemented. Skip is not.

### The Problem

Skip requires:

1. **Checkpoint persistence** — State must be durably stored before each node
2. **Skip markers** — A way to flag nodes as "skipped" in the workflow graph
3. **Dependency analysis** — Knowledge of which downstream nodes depend on the skipped node's outputs
4. **Conditional routing** — Edge logic that bypasses nodes when their inputs are unavailable

None of these exist in v1.

### Current Behavior

```python
def skip_failing_step(context: RecoveryContext) -> IntegrationResult:
    """
    Note: This is a placeholder for future implementation.
    Full skip support requires:
    - Workflow state checkpointing
    - Skip marker propagation
    - Conditional edge routing based on skip markers
    
    For V1, this falls back to a simple retry.
    """
    logger.warning("Skip not implemented; falling back to retry")
    return retry_with_context(context)
```

Users who click "Skip" see a warning and get retry behavior.

---

## Decision

**v1**: Skip falls back to retry. No checkpoint persistence.

**v2**: Implement proper skip with LangGraph checkpointing.

### v1 Rationale

Implementing skip correctly requires:

| Requirement | Effort | Risk |
|-------------|--------|------|
| LangGraph MemorySaver integration | 2-3 days | Medium — version coupling |
| Skip marker propagation | 1-2 days | Low |
| Downstream dependency graph | 2-3 days | Medium — complex for branching workflows |
| UI state synchronization | 1 day | Low |

This is 6-9 days of work with no user-facing value until complete. The retry-based fallback is functional and safe.

### v2 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   LangGraph MemorySaver                         │
│                                                                 │
│  Before each node:                                              │
│    checkpointer.save(run_id, node_name, state)                  │
│                                                                 │
│  On skip request:                                               │
│    state = checkpointer.load(run_id, failing_node)              │
│    state.skipped_nodes.add(failing_node)                        │
│    resume_from_next_node(state)                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### Checkpoint Storage

Options:
1. **PostgreSQL** — `run_checkpoints` table with JSONB state
2. **Redis** — Faster reads, but adds infrastructure
3. **Filesystem** — Simple, but not distributed

v2 will use PostgreSQL to avoid new dependencies.

#### Skip Propagation

```python
@dataclass
class WorkflowState:
    # ... existing fields ...
    skipped_nodes: Set[str] = field(default_factory=set)

def should_skip_node(state: WorkflowState, node_name: str) -> bool:
    """Check if this node's required inputs were skipped."""
    deps = NODE_DEPENDENCIES.get(node_name, set())
    return bool(deps & state.skipped_nodes)
```

#### Dependency Graph

```python
NODE_DEPENDENCIES = {
    "build_silver_api_model": {"detect_and_parse_spec"},
    "understand_task": {"build_silver_api_model"},
    "align_task_with_kg": {"understand_task"},
    "plan_integration_flow": {"align_task_with_kg"},
    "generate_code_and_tests": {"plan_integration_flow"},
    "persist_results": {"generate_code_and_tests"},
    # ...
}
```

If `detect_and_parse_spec` is skipped, all downstream nodes automatically skip.

---

## Alternatives Considered

### 1. Immediate Full Implementation

Implement checkpointing and skip in v1.

| Aspect | Assessment |
|--------|------------|
| Pros | Complete functionality from day one |
| Cons | Delays launch by 1-2 weeks; high risk for a secondary feature |
| Verdict | Rejected — not worth the delay |

### 2. Client-Side State Caching

Store workflow state in browser localStorage.

| Aspect | Assessment |
|--------|------------|
| Pros | No backend changes |
| Cons | State lost on browser close; no cross-device resume |
| Verdict | Rejected — unreliable |

### 3. Disable Skip Button

Remove skip from UI until implemented.

| Aspect | Assessment |
|--------|------------|
| Pros | No confusion about behavior |
| Cons | Users expect skip in error recovery UI |
| Verdict | Rejected — retry fallback with warning is better UX |

---

## Consequences

### Positive

1. **Faster v1 launch** — No checkpoint infrastructure needed
2. **Clear v2 scope** — Implementation path is documented
3. **Safe fallback** — Retry is always valid (idempotent nodes)

### Negative

1. **User confusion** — Skip button doesn't skip (mitigated by warning message)
2. **No partial completion** — Failed runs must retry from the failing node
3. **State loss on crash** — Process termination loses all progress

### Neutral

1. **Retry works** — Most failures are transient (rate limits, timeouts)
2. **Restart works** — Users can always start fresh

---

## v1 Constraints

| Constraint | Current Behavior | v2 Target |
|------------|------------------|-----------|
| Skip action | Falls back to retry | True skip with dependency analysis |
| Checkpoint persistence | None (in-memory only) | PostgreSQL `run_checkpoints` table |
| Resume capability | From failed node only | From any checkpointed node |
| State durability | Lost on process exit | Survives restarts |

---

## Migration Path

### Phase 1: Checkpoint Infrastructure

1. Add `run_checkpoints` table to `integration_gold` schema
2. Integrate LangGraph `MemorySaver` with PostgreSQL backend
3. Save checkpoint before each node execution

### Phase 2: Skip Implementation

1. Add `skipped_nodes` field to `WorkflowState`
2. Implement `NODE_DEPENDENCIES` graph
3. Add skip propagation logic to router
4. Update UI to show "Skipped" status

### Phase 3: Resume from Checkpoint

1. Add CLI/API option: `--resume-from <run_id>`
2. Load last successful checkpoint
3. Continue execution from next node

---

## Implementation References

| File | Current State |
|------|---------------|
| `src/integration_coworker/api/recovery.py` | `skip_failing_step()` falls back to retry |
| `src/integration_coworker/ui/streamlit_app.py` | Skip button shown with warning |
| `src/integration_coworker/graph/runtime.py` | No checkpointing integration |

---

## Related ADRs

- **ADR-0007**: Single DB Writer Pattern — Checkpoints would add a fourth writer
- **ADR-0006**: Medallion Data Architecture — `run_checkpoints` belongs in `integration_gold`

---

## Notes

The retry-based fallback is acceptable because all workflow nodes are designed to be idempotent. Re-running a node with the same inputs produces the same outputs. The main cost is time, not correctness.

Skip becomes valuable when:
1. A node is known to be broken (e.g., external API down)
2. User wants partial results despite failures
3. Long-running workflows need resumability

These scenarios are rare in v1's typical usage pattern (single spec, single task, <30 second runs).

````