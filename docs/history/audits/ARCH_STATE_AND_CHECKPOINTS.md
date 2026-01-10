# Architecture: State Management and Checkpoints

**Document Type**: Architecture Decision Record (ADR)
**Last Updated**: 2025-12-26
**Status**: Draft

---

## Overview

This document describes the state management architecture for the Integration Coworker workflow system, including how state flows through nodes, checkpoint persistence, and recommendations for addressing state bloat issues identified in V22 live demos.

---

## Current Architecture

### WorkflowState Dataclass

**Location**: [src/integration_coworker/graph/state.py](../src/integration_coworker/graph/state.py)

The `WorkflowState` is a Python dataclass with ~55 fields that carries all workflow context through the LangGraph execution.

#### Key Large Fields

| Field | Type | Typical Size | Description |
|-------|------|--------------|-------------|
| `repo_snapshot` | `RepoSnapshot` | 1-10 MB | Full file contents from target repo |
| `spec_documents` | `List[SpecDocument]` | 100KB-5MB | Parsed spec content |
| `openapi_spec` | `dict` | 1-20MB | Parsed OpenAPI/AsyncAPI spec |
| `code_artifacts` | `List[CodeArtifact]` | 500KB-2MB | Generated code files |
| `full_markdown` | `str` | 100KB-500KB | Aggregated markdown content |
| `raw_spec_content` | `str` | 1-26MB | Raw spec file content (e.g., Twilio) |

#### State Field Categories

```
┌─────────────────────────────────────────────────────────────────┐
│                        WorkflowState                             │
├─────────────────────────────────────────────────────────────────┤
│ IDENTIFIERS (small, ~500 bytes)                                 │
│   run_id, provider_code, task_description, status               │
├─────────────────────────────────────────────────────────────────┤
│ DOMAIN MODELS (medium, ~100KB-1MB)                              │
│   endpoints, schemas, entities, relationships, policies         │
├─────────────────────────────────────────────────────────────────┤
│ CONTENT (large, ~1-20MB each)                                   │
│   repo_snapshot, spec_documents, openapi_spec, code_artifacts   │
├─────────────────────────────────────────────────────────────────┤
│ TRACKING (small, ~10KB)                                         │
│   completed_steps, node_timings, persisted_ids, llm_token_usage │
└─────────────────────────────────────────────────────────────────┘
```

### State Flow Through Nodes

```
                    ┌───────────────┐
                    │   plan_run    │
                    │ (creates run) │
                    └───────┬───────┘
                            │ state (2KB)
                            ▼
                    ┌───────────────┐
                    │  ingest_spec  │
                    │ (fetches spec)│
                    └───────┬───────┘
                            │ state (700KB) + spec_documents
                            ▼
                    ┌────────────────┐
                    │  detect_parse  │
                    │ (parses spec)  │
                    └───────┬────────┘
                            │ state (700KB) + openapi_spec
                            ▼
                    ┌────────────────┐
                    │ persist_silver │
                    │  (DB write)    │
                    └───────┬────────┘
                            │ state + persisted_ids (35MB estimate*)
                            ▼
                         ... more nodes ...
                            │
                            ▼
                    ┌────────────────┐
                    │  attach_repo   │
                    │ (reads repo)   │
                    └───────┬────────┘
                            │ state + repo_snapshot
                            ▼
                    ┌────────────────┐
                    │  build_report  │
                    │ (final output) │
                    └───────┬────────┘
                            │ state (estimated 4-10MB)
                            ▼
                         [Output]
```

### State Size Estimation - FIXED (V22-001)

**Location**: [src/integration_coworker/graph/runtime.py#L275-L450](../src/integration_coworker/graph/runtime.py#L275)

#### Previous Problem (Fixed 2025-12-26)

The old `_get_state_size()` function used `sys.getsizeof()` which only measured shallow object size, causing exponential growth patterns in logs (2x/4x doubling per node).

#### Current Implementation: Bounded Sampling Estimator

```python
def _get_state_size(state, max_depth=4, max_sample_items=50) -> int:
    """
    Estimate state size using bounded recursive sampling.
    
    V22-001 Fix: Uses deterministic traversal with sorted keys
    and caps at 100MB to prevent absurd values.
    """
    MAX_REPORTED = 100_000_000  # 100MB cap
    return min(_get_state_size_uncapped(state), MAX_REPORTED)
```

**Key Features**:
- Max recursion depth: 4 levels
- Max sample items per container: 50
- Deterministic traversal (sorted keys for dicts/sets)
- 100MB cap to prevent absurd values
- Logs both uncapped and capped values for regression detection

#### Validation Results (from `scripts/validate_v22_001_fix.py`)

| Metric | Before Fix | After Fix |
|--------|------------|-----------|
| Stage 1→2 Growth | 12,232x | 9.5x |
| Stage 2→3 Growth | 2.0x | 1.0x |
| Final Estimate | 139GB | 4.82MB |
| Determinism | Non-deterministic | 100% deterministic |
| Estimation Time | Unknown | 0.2ms |

### Memory Monitoring

**V22-001.1**: Production runs now capture actual memory metrics:

```python
# At workflow start:
_get_memory_diagnostics()  # RSS, tracemalloc, cgroup limits

# At each node boundary:
_emit_node_start(rss_bytes=..., input_bytes_uncapped=..., input_bytes_method=...)
_emit_node_end(rss_bytes=..., output_bytes_uncapped=..., tracemalloc_peak=...)

# At workflow end:
_check_oom_likelihood()  # Checks if usage > 80% of cgroup limit
```

### Checkpoint System

**Location**: [src/integration_coworker/persistence/checkpoints.py](../src/integration_coworker/persistence/checkpoints.py)

Checkpoints save full workflow state after each node for:
1. Resume after crash
2. Debugging/replay
3. Audit trail

#### Current Checkpoint Flow

```
Node Execution
      │
      ▼
┌────────────────┐
│ Node Function  │
│ fn(state) →    │
│    result      │
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ save_checkpoint│
│ (serialize     │
│  full state)   │
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ SQLite BLOB    │
│ checkpoint_    │
│ blobs table    │
└────────────────┘
```

#### Problem: Full State Serialization

Each checkpoint stores the FULL state, not just the delta. For a 20-node workflow:
- If state is 10MB at end
- 20 checkpoints × 10MB = 200MB of checkpoint data

With the buggy estimator, this appeared to be:
- 20 checkpoints × "139GB" = "2.78TB" (impossible)

---

## Recommended Architecture Changes

### Short-Term Fixes (V22-001)

#### 1. Fix State Estimator

Replace `_get_state_size()` with bounded sampling:

```python
def _get_state_size(state, max_depth=3, max_items=100) -> int:
    """Estimate state size using bounded recursive sampling."""
    MAX_REPORTED = 100_000_000  # 100MB cap
    
    def sample(obj, depth):
        if depth > max_depth:
            return 100  # Placeholder
        if obj is None:
            return 0
        if isinstance(obj, (str, bytes)):
            return len(obj)
        if isinstance(obj, (int, float, bool)):
            return 8
        if isinstance(obj, dict):
            items = list(obj.items())[:max_items]
            return sum(sample(k, depth+1) + sample(v, depth+1) for k, v in items)
        if isinstance(obj, (list, tuple)):
            items = list(obj)[:max_items]
            return sum(sample(x, depth+1) for x in items)
        if hasattr(obj, '__dict__'):
            return sample(obj.__dict__, depth+1)
        return sys.getsizeof(obj)
    
    return min(sample(state, 0), MAX_REPORTED)
```

#### 2. Add RSS Monitoring

Log actual process memory alongside estimates:

```python
def _get_rss_mb():
    """Get actual process RSS in MB."""
    import psutil
    return psutil.Process().memory_info().rss / (1024**2)

# In node wrapper:
rss_before = _get_rss_mb()
result = node_fn(state)
rss_after = _get_rss_mb()
logger.info(f"Node {name} RSS delta: {rss_after - rss_before:+.1f}MB")
```

### Medium-Term: Externalize Large Artifacts

#### Design: Blob Storage Reference

Instead of storing large content in state, store references to external storage:

```python
@dataclass
class WorkflowState:
    # Before: store full content
    # repo_snapshot: RepoSnapshot
    
    # After: store reference
    repo_snapshot_ref: Optional[str] = None  # Blob ID or file path
    
    def get_repo_snapshot(self) -> RepoSnapshot:
        """Lazy-load repo snapshot from blob storage."""
        if self.repo_snapshot_ref is None:
            return None
        return blob_store.get(self.repo_snapshot_ref)
    
    def set_repo_snapshot(self, snapshot: RepoSnapshot):
        """Store repo snapshot externally, keep reference."""
        self.repo_snapshot_ref = blob_store.put(snapshot)
```

#### Blob Storage Options

1. **SQLite BLOB table** (simple, existing infrastructure)
2. **File system** (best for large files)
3. **Object storage** (S3/GCS for cloud deployments)

### Long-Term: Delta Checkpoints

#### Design: Store Only Changes

```python
@dataclass
class CheckpointDelta:
    base_checkpoint_id: Optional[str]
    changed_fields: Dict[str, Any]
    timestamp: datetime

def save_checkpoint_delta(state: WorkflowState, previous_state: Optional[WorkflowState]):
    """Save only fields that changed since previous checkpoint."""
    if previous_state is None:
        # First checkpoint: save full state
        return save_full_checkpoint(state)
    
    delta = {}
    for field in fields(state):
        current = getattr(state, field.name)
        previous = getattr(previous_state, field.name)
        if current != previous:
            delta[field.name] = current
    
    return save_delta_checkpoint(delta, previous_checkpoint_id)
```

**Benefits**:
- Dramatic reduction in checkpoint storage (10x-100x)
- Faster checkpoint writes
- Enables efficient history navigation

**Challenges**:
- Requires tracking previous state
- Reconstruction needs loading checkpoint chain

---

## State Field Analysis

### Fields Safe to Externalize

| Field | Current Size | Can Externalize? | Notes |
|-------|--------------|------------------|-------|
| `repo_snapshot` | 1-10MB | ✅ Yes | Primary candidate |
| `raw_spec_content` | 1-26MB | ✅ Yes | Already processed |
| `openapi_spec` | 1-20MB | ✅ Yes | Can reload from raw |
| `spec_documents` | 100KB-5MB | ✅ Yes | Derived from raw |
| `code_artifacts` | 500KB-2MB | ⚠️ Maybe | Need for sandbox |

### Fields That Must Stay In State

| Field | Reason |
|-------|--------|
| `run_id` | Required for all operations |
| `completed_steps` | Track progress |
| `persisted_ids` | DB foreign keys |
| `node_timings` | Performance tracking |
| `options` | Runtime configuration |

---

## LangGraph State Compatibility

### TypedDict Conversion

**Location**: [src/integration_coworker/graph/state_v2.py](../src/integration_coworker/graph/state_v2.py)

LangGraph requires TypedDict for parallel execution. The `dataclass_to_dict()` and `dict_to_dataclass()` functions handle conversion.

#### Current Conversion Flow

```
WorkflowState (dataclass)
        │
        ▼ dataclass_to_dict()
WorkflowStateDict (TypedDict)
        │
        ▼ LangGraph execution
WorkflowStateDict (modified)
        │
        ▼ dict_to_dataclass()
WorkflowState (updated)
```

#### Tracing Exclusions

To prevent LangSmith payload issues, large fields are excluded from traces:

```python
_LARGE_FIELDS_TO_EXCLUDE_FROM_TRACING = {
    "openapi_spec",
    "raw_spec_content",
    "spec_sections",
    "repo_snapshot",
    "full_markdown",
}
```

---

## Metrics and Monitoring

### Current Metrics

| Metric | Location | Issue |
|--------|----------|-------|
| `output_bytes` | Node end event | BUGGY (see V22-001) |
| `input_bytes` | Node start event | BUGGY |
| `duration_ms` | Node timing | Accurate |
| `checkpoint_duration_ms` | Checkpoint save | New in V22 |

### Recommended Additional Metrics

```python
# Process-level memory
"rss_mb": _get_rss_mb(),
"rss_delta_mb": rss_after - rss_before,

# State-level granular
"state_field_sizes": {
    "repo_snapshot": _field_size(state.repo_snapshot),
    "spec_documents": _field_size(state.spec_documents),
    ...
},

# Checkpoint-level
"checkpoint_size_bytes": len(serialized),
"checkpoint_compression_ratio": len(serialized) / uncompressed_size,
```

---

## Migration Path

### Phase 1: Fix Estimator (V22-001)
- Week 1
- Replace `_get_state_size()` with bounded sampling
- Add RSS monitoring
- No breaking changes

### Phase 2: Add Blob Storage
- Week 2-3
- Implement blob storage layer
- Migrate `repo_snapshot` to external storage
- Backward compatible (old state format still works)

### Phase 3: Delta Checkpoints
- Week 4-6
- Implement delta checkpoint algorithm
- Migrate existing checkpoints
- Significant storage reduction

---

## Testing Strategy

### Unit Tests
```python
def test_state_size_bounded():
    """Estimator should cap at reasonable max."""
    ...

def test_repo_snapshot_externalization():
    """Large fields should be stored externally."""
    ...

def test_checkpoint_delta():
    """Delta checkpoints should only store changes."""
    ...
```

### Integration Tests
```python
def test_workflow_memory_usage():
    """Full workflow should stay under 100MB RSS."""
    import psutil
    before = psutil.Process().memory_info().rss
    run_workflow(...)
    after = psutil.Process().memory_info().rss
    assert (after - before) < 100 * 1024 * 1024  # 100MB
```

### Benchmark Tests
```python
def benchmark_checkpoint_size():
    """Compare full vs delta checkpoint sizes."""
    ...
```

---

## References

- [BUG_REPORT_V22_LIVE_DEMO.md](BUG_REPORT_V22_LIVE_DEMO.md) - Bug details
- [PROD_FIX_PLAN_V22.md](PROD_FIX_PLAN_V22.md) - Fix implementation plan
- [runtime.py](../src/integration_coworker/graph/runtime.py) - Node execution
- [state.py](../src/integration_coworker/graph/state.py) - WorkflowState definition
- [state_v2.py](../src/integration_coworker/graph/state_v2.py) - LangGraph TypedDict conversion

---

*Document created: 2025-12-26*
*Author: AI Assistant (based on V22 live demo analysis)*
