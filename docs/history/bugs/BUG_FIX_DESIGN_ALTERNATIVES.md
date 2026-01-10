# Bug Fix Design Alternatives (Artifact B)

**Decision Matrix for Production Bug Fixes**

---

## P0.1: Duplicate Key Constraint

### Alternative 1: Remove UNIQUE(uri) ⭐ RECOMMENDED

**Approach**: Drop the `UNIQUE (uri)` constraint, keep only `UNIQUE (source_system_id, sha256)`.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐⭐⭐⭐⭐ | Single migration file change |
| Risk | ⭐⭐⭐⭐⭐ | No data loss, schema narrowing |
| Backward Compatibility | ⭐⭐⭐⭐⭐ | Existing queries unaffected |
| Correctness | ⭐⭐⭐⭐⭐ | (source_system_id, sha256) is proper dedup key |

**Implementation**:
```sql
-- New migration: 002_remove_uri_unique.sql
ALTER TABLE spec_silver.spec_documents DROP CONSTRAINT spec_documents_uri_key;
```

### Alternative 2: Composite UNIQUE(source_system_id, uri)

**Approach**: Change to per-provider URI uniqueness.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐⭐⭐⭐ | Requires constraint drop + add |
| Risk | ⭐⭐⭐ | More complex migration |
| Backward Compatibility | ⭐⭐⭐⭐ | May require existing data cleanup |
| Correctness | ⭐⭐⭐⭐ | Redundant with sha256 key |

### Alternative 3: Hash-based Synthetic Key

**Approach**: Generate `hash(uri + repo_root)` as primary key component.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐⭐ | Requires code changes in multiple nodes |
| Risk | ⭐⭐ | Hash collisions possible |
| Backward Compatibility | ⭐ | Breaks existing data model |
| Correctness | ⭐⭐⭐ | Overengineered for this problem |

### Decision: Alternative 1

**Rationale**: The `(source_system_id, sha256)` constraint already provides proper deduplication. URI uniqueness is unnecessary and actively harmful for multi-run scenarios.

---

## P0.2: LangSmith Payload Size

### Alternative 1: Pre-trace Sanitization ⭐ RECOMMENDED

**Approach**: Add sanitizer module that truncates/redacts large values before LangSmith upload.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐⭐⭐⭐ | New module + callback registration |
| Risk | ⭐⭐⭐⭐⭐ | No core logic changes |
| Observability | ⭐⭐⭐⭐ | Retains trace structure, marks truncation |
| Performance | ⭐⭐⭐⭐⭐ | Runs only on trace upload |

**Implementation**:
```python
# trace_sanitizer.py
MAX_PAYLOAD_BYTES = 5_000_000  # 5MB (half of LangSmith limit)

def sanitize_trace_data(data: dict) -> dict:
    """Truncate large values in trace data."""
    return _recursive_truncate(data, max_str_len=100_000)
```

### Alternative 2: Disable Tracing for Large Specs

**Approach**: Check spec size before run, disable LANGCHAIN_TRACING_V2 for large specs.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐⭐⭐⭐⭐ | Simple env toggle |
| Risk | ⭐⭐⭐⭐ | Lose observability for large runs |
| Observability | ⭐⭐ | Complete trace loss |
| Performance | ⭐⭐⭐⭐⭐ | No trace overhead |

### Alternative 3: Streaming Trace Upload

**Approach**: Chunk traces for incremental upload.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐ | Requires deep LangSmith SDK changes |
| Risk | ⭐ | Unsupported by current SDK |
| Observability | ⭐⭐⭐⭐⭐ | Full trace preservation |
| Performance | ⭐⭐ | Multiple network round-trips |

### Decision: Alternative 1

**Rationale**: Sanitization preserves trace structure while respecting limits. Clear `[TRUNCATED]` markers maintain debuggability.

---

## P0.3: psycopg Connection Lifecycle

### Alternative 1: Retry Wrapper + Health Check ⭐ RECOMMENDED

**Approach**: Add retry decorator for connection acquisition and pre-query health check.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐⭐⭐⭐ | New decorator + health check |
| Risk | ⭐⭐⭐⭐⭐ | Graceful degradation |
| Reliability | ⭐⭐⭐⭐⭐ | Auto-recovers from transient failures |
| Performance | ⭐⭐⭐⭐ | Minimal overhead (check only on acquire) |

**Implementation**:
```python
@retry(max_attempts=3, backoff=ExponentialBackoff(base=0.5))
def get_connection_with_retry():
    conn = db.get_connection()
    _verify_connection_health(conn)
    return conn
```

### Alternative 2: Connection Pool Monitoring

**Approach**: Add Prometheus metrics for pool health, alert on exhaustion.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐⭐⭐ | Requires metrics infrastructure |
| Risk | ⭐⭐⭐⭐ | Reactive, not preventive |
| Reliability | ⭐⭐⭐ | Visibility without recovery |
| Performance | ⭐⭐⭐⭐⭐ | Minimal overhead |

### Alternative 3: Per-Request Connections

**Approach**: Disable pooling, create fresh connection per request.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐⭐⭐⭐ | Pool config change |
| Risk | ⭐⭐⭐ | Connection storms under load |
| Reliability | ⭐⭐⭐⭐ | No stale connection issues |
| Performance | ⭐⭐ | High connection overhead |

### Decision: Alternative 1

**Rationale**: Retry wrapper provides resilience without sacrificing pool efficiency. Health check prevents using dead connections.

---

## P1.1: Ruff E722 (Bare Except)

### Alternative 1: Template Fix ⭐ RECOMMENDED

**Approach**: Update codegen prompts to emit `except Exception:` instead of bare `except:`.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐⭐⭐⭐⭐ | String replacement in prompts |
| Risk | ⭐⭐⭐⭐⭐ | Low risk change |
| Correctness | ⭐⭐⭐⭐⭐ | Follows Python best practices |

### Decision: Alternative 1

---

## P1.2: Module Import Resolution

### Alternative 1: Sandbox PYTHONPATH Fix ⭐ RECOMMENDED

**Approach**: Ensure sandbox adds `src/integrations/clients` to PYTHONPATH.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐⭐⭐⭐ | Modify _run_command env |
| Risk | ⭐⭐⭐⭐ | Well-understood fix |
| Correctness | ⭐⭐⭐⭐⭐ | Matches repo structure |

### Alternative 2: Relative Imports in Templates

**Approach**: Generate code with relative imports.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Implementation Complexity | ⭐⭐⭐ | Template complexity increase |
| Risk | ⭐⭐⭐ | Harder to maintain |
| Correctness | ⭐⭐⭐⭐ | More portable |

### Decision: Alternative 1

---

## Implementation Priority Order

| Priority | Bug | Fix | Estimated Effort |
|----------|-----|-----|------------------|
| 1 | P0.1 | Remove UNIQUE(uri) | 15 min |
| 2 | P0.3 | Connection retry wrapper | 45 min |
| 3 | P0.2 | Trace sanitizer | 30 min |
| 4 | P1.1 | Bare except fix | 10 min |
| 5 | P1.2 | PYTHONPATH fix | 15 min |

**Total Estimated Time**: ~2 hours
