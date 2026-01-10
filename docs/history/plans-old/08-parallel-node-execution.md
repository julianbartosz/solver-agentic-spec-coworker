# Implementation Plan: Parallel Node Execution (LangGraph)

## 1. Overview

**Objective:** Optimize workflow execution by running independent nodes in parallel using LangGraph's native parallelism features, reducing end-to-end run time.

**Priority:** Medium  
**Estimated Effort:** 4-6 hours implementation + testing  
**Risk Level:** High (graph structure change, potential race conditions)

---

## 2. Current Sequential Flow

```
plan_run → ingest_spec → detect_and_parse_spec → build_silver_api_model
    → embed_spec_chunks → persist_silver_checkpoint → understand_task
    → align_task_with_kg → plan_integration_flow → attach_policies_and_patterns
    → generate_code_and_tests → persist_gold_checkpoint → persist_kg_learning
    → attach_repo_context → analyze_repo_layout → apply_repo_integration_changes
    → validate_integration_design → build_report → persist_run_outcome
```

**Current Execution Time (typical):**
- Spec parsing: ~2-5s
- Embedding: ~5-15s (depends on spec size)
- LLM calls: ~30-60s (4 nodes × 8-15s each)
- Persistence: ~2-5s
- **Total: ~45-90s**

---

## 3. Parallelization Opportunities

### 3.1 Identified Parallel-Safe Subgraphs

#### Opportunity 1: Embedding + Task Understanding
After `build_silver_api_model`, both `embed_spec_chunks` and `understand_task` can run concurrently:

```
                          ┌─── embed_spec_chunks ───┐
build_silver_api_model ──┤                          ├── persist_silver_checkpoint
                          └─── understand_task ─────┘
```

**Savings:** ~8-15s (embedding time hidden behind LLM call)

#### Opportunity 2: Parallel Persistence
The three persistence nodes can run in parallel:

```
                           ┌─── persist_silver_checkpoint ───┐
generate_code_and_tests ──┤─── persist_gold_checkpoint ──────├── validate_integration_design
                           └─── persist_kg_learning ─────────┘
```

**Savings:** ~2-4s (I/O parallelization)

#### Opportunity 3: Code + Repo Analysis (when repo enabled)
If repo integration is enabled, repo analysis can start during code generation:

```
                                ┌─── generate_code_and_tests ───┐
attach_policies_and_patterns ──┤                                 ├── apply_changes
                                └─── analyze_repo_layout ────────┘
```

**Savings:** ~3-5s (repo analysis time)

### 3.2 Estimated Total Savings

| Scenario | Current | Optimized | Savings |
|----------|---------|-----------|---------|
| Small spec, no repo | 45s | 35s | 22% |
| Large spec, no repo | 75s | 55s | 27% |
| Large spec, with repo | 90s | 65s | 28% |

---

## 4. LangGraph Implementation

### 4.1 StateGraph Parallel Branches

LangGraph supports parallel execution via conditional branching that converges:

```python
from langgraph.graph import StateGraph, END

def build_optimized_workflow():
    workflow = StateGraph(WorkflowState)
    
    # Sequential start
    workflow.add_node("plan_run", plan_run)
    workflow.add_node("ingest_spec", ingest_spec)
    workflow.add_node("detect_and_parse_spec", detect_and_parse_spec)
    workflow.add_node("build_silver_api_model", build_silver_api_model)
    
    # Parallel branch 1: Embedding + Task Understanding
    workflow.add_node("embed_spec_chunks", embed_spec_chunks)
    workflow.add_node("understand_task", understand_task)
    workflow.add_node("sync_after_embed_task", sync_parallel_results)
    
    # Continue sequential
    workflow.add_node("align_task_with_kg", align_task_with_kg)
    # ... rest of workflow
    
    # Edges
    workflow.add_edge("plan_run", "ingest_spec")
    workflow.add_edge("ingest_spec", "detect_and_parse_spec")
    workflow.add_edge("detect_and_parse_spec", "build_silver_api_model")
    
    # Parallel split
    workflow.add_edge("build_silver_api_model", "embed_spec_chunks")
    workflow.add_edge("build_silver_api_model", "understand_task")
    
    # Parallel join
    workflow.add_edge("embed_spec_chunks", "sync_after_embed_task")
    workflow.add_edge("understand_task", "sync_after_embed_task")
    
    # Continue after join
    workflow.add_edge("sync_after_embed_task", "align_task_with_kg")
    
    return workflow.compile()
```

### 4.2 State Synchronization Node

```python
def sync_parallel_results(state: WorkflowState) -> WorkflowState:
    """
    Synchronization point after parallel execution.
    
    LangGraph automatically merges parallel branch states. This node:
    1. Validates both branches completed successfully
    2. Resolves any state conflicts
    3. Logs parallel execution metrics
    """
    # Verify both branches executed
    embed_completed = "embed_spec_chunks" in state.completed_steps
    task_completed = "understand_task" in state.completed_steps
    
    if not embed_completed:
        state.warnings.append("embed_spec_chunks did not complete in parallel branch")
    if not task_completed:
        state.warnings.append("understand_task did not complete in parallel branch")
    
    # Log timing metrics
    if hasattr(state, '_parallel_timings'):
        logger.info(f"Parallel execution timings: {state._parallel_timings}")
    
    state.completed_steps.append("sync_after_embed_task")
    return state
```

---

## 5. File Changes

### 5.1 Modified Files

| File | Changes |
|------|---------|
| `src/integration_coworker/graph/runtime.py` | Restructure graph with parallel branches |
| `src/integration_coworker/graph/state.py` | Add parallel timing fields |
| `src/integration_coworker/config/__init__.py` | Add `PARALLEL_ENABLED` config |

### 5.2 New Files

| File | Purpose | LOC |
|------|---------|-----|
| `src/integration_coworker/graph/parallel.py` | Parallel execution helpers | ~100 |
| `tests/test_parallel_execution.py` | Parallel execution tests | ~150 |

---

## 6. Implementation Details

### 6.1 Updated `runtime.py`

```python
# =============================================================================
# Parallel Execution Configuration
# =============================================================================

PARALLEL_ENABLED = os.getenv("PARALLEL_WORKFLOW", "true").lower() == "true"

# Node dependency graph updated for parallel execution
NODE_DEPENDENCIES_PARALLEL: Dict[str, List[str]] = {
    "plan_run": [],
    "ingest_spec": ["plan_run"],
    "detect_and_parse_spec": ["ingest_spec"],
    "build_silver_api_model": ["detect_and_parse_spec"],
    
    # Parallel branch 1
    "embed_spec_chunks": ["build_silver_api_model"],
    "understand_task": ["build_silver_api_model"],
    
    # Sync point
    "sync_embed_task": ["embed_spec_chunks", "understand_task"],
    
    # Continue sequential
    "align_task_with_kg": ["sync_embed_task"],
    "plan_integration_flow": ["align_task_with_kg"],
    
    # ... etc
}


def build_workflow(checkpointer=None) -> Any:
    """
    Build the LangGraph workflow.
    
    Uses parallel execution if PARALLEL_WORKFLOW=true.
    """
    if PARALLEL_ENABLED:
        return _build_parallel_workflow(checkpointer)
    else:
        return _build_sequential_workflow(checkpointer)


def _build_parallel_workflow(checkpointer=None) -> Any:
    """Build workflow with parallel branches."""
    from langgraph.graph import StateGraph
    
    workflow = StateGraph(WorkflowState)
    
    # Add all nodes
    workflow.add_node("plan_run", plan_run.plan_run)
    workflow.add_node("ingest_spec", ingest_spec.ingest_spec)
    workflow.add_node("detect_and_parse_spec", detect_and_parse_spec.detect_and_parse_spec)
    workflow.add_node("build_silver_api_model", build_silver_api_model.build_silver_api_model)
    workflow.add_node("embed_spec_chunks", embed_spec_chunks.embed_spec_chunks)
    workflow.add_node("understand_task", understand_task.understand_task)
    workflow.add_node("sync_embed_task", _sync_embed_task)
    # ... add remaining nodes
    
    # Set entry point
    workflow.set_entry_point("plan_run")
    
    # Sequential edges
    workflow.add_edge("plan_run", "ingest_spec")
    workflow.add_edge("ingest_spec", "detect_and_parse_spec")
    workflow.add_edge("detect_and_parse_spec", "build_silver_api_model")
    
    # PARALLEL SPLIT: build_silver_api_model -> [embed, understand]
    # Use conditional edge that routes to BOTH branches
    workflow.add_conditional_edges(
        "build_silver_api_model",
        _parallel_route,  # Returns list of next nodes
        {
            "embed": "embed_spec_chunks",
            "understand": "understand_task",
        }
    )
    
    # PARALLEL JOIN: [embed, understand] -> sync
    workflow.add_edge("embed_spec_chunks", "sync_embed_task")
    workflow.add_edge("understand_task", "sync_embed_task")
    
    # Continue sequential after sync
    workflow.add_edge("sync_embed_task", "align_task_with_kg")
    # ... add remaining edges
    
    return workflow.compile(checkpointer=checkpointer)


def _parallel_route(state: WorkflowState) -> List[str]:
    """Route to both parallel branches."""
    return ["embed", "understand"]


def _sync_embed_task(state: WorkflowState) -> WorkflowState:
    """Sync point after embed + understand parallel execution."""
    state.completed_steps.append("sync_embed_task")
    return state
```

### 6.2 State Updates for Parallel Tracking

```python
# In state.py

@dataclass
class WorkflowState:
    # ... existing fields ...
    
    # Parallel execution tracking
    parallel_branch_times: Dict[str, float] = field(default_factory=dict)
    parallel_sync_points: List[str] = field(default_factory=list)
```

---

## 7. Testing Strategy

### 7.1 Unit Tests

```python
# tests/test_parallel_execution.py

def test_parallel_branch_both_execute():
    """Verify both branches execute in parallel mode."""
    
def test_parallel_sync_waits_for_both():
    """Verify sync point waits for both branches."""
    
def test_parallel_state_merge():
    """Verify state is correctly merged after parallel execution."""
    
def test_parallel_error_in_one_branch():
    """Verify error handling when one parallel branch fails."""
    
def test_sequential_fallback():
    """Verify sequential mode works when parallel disabled."""
    
def test_parallel_timing_metrics():
    """Verify timing metrics are recorded for parallel branches."""
```

### 7.2 Integration Tests

```python
def test_full_workflow_parallel():
    """Run full workflow in parallel mode."""
    
def test_full_workflow_sequential():
    """Run full workflow in sequential mode."""
    
def test_parallel_vs_sequential_timing():
    """Compare execution time between modes."""
```

---

## 8. Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `PARALLEL_WORKFLOW` | `true` | Enable parallel node execution |
| `PARALLEL_LOG_TIMING` | `false` | Log detailed timing for each branch |

---

## 9. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Race condition in state | High | LangGraph handles state merging |
| One branch fails, other continues | Medium | Sync point validates both branches |
| Harder to debug | Medium | Detailed logging + timing metrics |
| Checkpoint complexity | Medium | Checkpoint at sync points only |
| Resource contention (LLM rate limits) | Medium | Already have retry logic |

---

## 10. Rollout Plan

1. **Phase 1:** Implement parallel infrastructure (disabled by default)
2. **Phase 2:** Enable parallel embed+understand (flag controlled)
3. **Phase 3:** Measure performance improvement in production
4. **Phase 4:** Enable parallel persistence nodes
5. **Phase 5:** Make parallel default after validation

---

## 11. Monitoring

### Metrics to Track:
- Parallel vs sequential execution time
- Branch completion times
- Sync point wait times
- Error rate per branch

### LangSmith Tracing:
- Parallel branches will show as concurrent spans
- Sync points clearly visible in trace

---

## 12. Alternative Approaches Considered

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **LangGraph native** | Built-in, maintains state | Requires graph restructure | ✅ Chosen |
| asyncio.gather | Simple for I/O | Manual orchestration | ❌ |
| Thread pool | Good for CPU-bound | Complex state sharing | ❌ |
| Celery workers | Scalable | Heavy infrastructure | ❌ Overkill |

---

## 13. Files to Create/Modify

```
CREATE:
  src/integration_coworker/graph/parallel.py    (~100 LOC)
  tests/test_parallel_execution.py              (~150 LOC)
  docs/features/parallel-execution.md           (~50 LOC)

MODIFY:
  src/integration_coworker/graph/runtime.py     (~150 LOC changes)
  src/integration_coworker/graph/state.py       (+10 LOC)
  src/integration_coworker/config/__init__.py   (+5 LOC)
```

**Total New Code:** ~300 LOC
**Total Modified Code:** ~165 LOC

---

## 14. Dependency Graph Visualization

### Sequential (Current)
```
A → B → C → D → E → F → G → H → I
```

### Parallel (Proposed)
```
                    ┌─ D ─┐
A → B → C ────────┤       ├── F → G → H → I
                    └─ E ─┘
                    
                    ┌─ G ─┐
            ──────┤       ├── I
                    └─ H ─┘
```

Where:
- A-C: Spec ingestion (sequential, must be ordered)
- D: embed_spec_chunks (I/O bound, no LLM)
- E: understand_task (LLM bound)
- F: Sync point
- G-I: Remaining workflow
