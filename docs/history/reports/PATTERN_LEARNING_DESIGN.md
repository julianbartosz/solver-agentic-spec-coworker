# Dynamic Pattern Learning System — Implementation Reference

**Updated**: 2025-01-15  
**Status**: ✅ Production Ready  
**Audit Reference**: `docs/PATTERN_LEARNING_AUDIT.md`

---

## 1. Overview

This document describes the **implemented** dynamic pattern learning system for cross-provider workflow pattern discovery and reuse.

**Core Value Proposition**: Learn workflow patterns from one API provider (e.g., Petstore), apply them to similar operations on another provider (e.g., GitHub) without manual pattern definition.

---

## 2. Implemented Schema

The authoritative schema is defined in:
- **SQLite**: `src/integration_coworker/persistence/db.py` (lines 900-970)
- **Postgres**: `src/integration_coworker/persistence/postgres.py` (lines 550-650)

### 2.1 Tables Added

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `kg_run_events` / `kg.run_events` | Event log for workflow execution | `run_id`, `event_type`, `activity`, `position`, `attributes` |
| `kg_pattern_candidates` / `kg.pattern_candidates` | Staging for discovered patterns | `candidate_key`, `canonical_sequence`, `support_count`, `status` |
| `kg_pattern_matches` / `kg.pattern_matches` | Match decisions for explainability | `run_id`, `pattern_key`, `match_score`, `match_method` |

### 2.2 Columns Added

| Table | Column | Purpose |
|-------|--------|---------|
| `kg_nodes` / `kg.nodes` | `origin` | Tracks `'seeded'` vs `'learned'` vs `'manual'` patterns |

### 2.3 Indexes

```sql
-- Composite indexes for efficient queries (both SQLite and Postgres)
kg_run_events_run_position_idx ON kg_run_events(run_id, position)
kg_pattern_candidates_status_support_idx ON kg_pattern_candidates(status, support_count DESC)
kg_nodes_type_origin_idx ON kg_nodes(node_type, origin)
```

---

## 3. Configuration Flags

**All pattern learning flags default to OFF for production safety.**

See `src/integration_coworker/config/__init__.py` lines 224-260.

| Flag | Default | Purpose |
|------|---------|---------|
| `PATTERN_LEARNING_ENABLED` | `false` | Master switch for all pattern learning |
| `PATTERN_CAPTURE_EVENTS` | `false` | Record workflow events to `kg_run_events` |
| `PATTERN_DISCOVER_CANDIDATES` | `false` | Mine events for candidate patterns |
| `PATTERN_AUTO_PROMOTE` | `false` | Auto-promote high-confidence candidates |
| `PATTERN_MATCH_LEARNED` | `false` | Include learned patterns in matching |
| `PATTERN_PROMOTION_THRESHOLD` | `3` | Min runs before promotion eligible |
| `PATTERN_MIN_FEEDBACK_SCORE` | `0.6` | Min confidence for promotion |
| `PATTERN_CONFIDENCE_DECAY` | `0.95` | Time-decay factor for unused patterns |

### Enable Pattern Learning (Development/Testing)

```bash
export PATTERN_LEARNING_ENABLED=true
export PATTERN_CAPTURE_EVENTS=true
export PATTERN_DISCOVER_CANDIDATES=true
export PATTERN_AUTO_PROMOTE=false  # Manual promotion recommended initially
export PATTERN_MATCH_LEARNED=true
```

---

## 4. Implementation Files

### 4.1 Core Module

```
src/integration_coworker/kg/pattern_discovery.py  # ~1200 lines
```

**Public API** (all exported from `pattern_discovery.py`):
- `capture_run_events(run_id, workflow_nodes, provider_code, ...)` → int
- `discover_pattern_candidates(min_support=3)` → List[PatternCandidate]
- `save_pattern_candidates(candidates)` → int
- `promote_pattern_candidate(candidate_key)` → Optional[str]
- `record_pattern_match(run_id, pattern_key, ...)` → Optional[int]
- `get_pattern_candidates(status=None)` → List[Dict]
- `get_learned_patterns()` → List[Dict]
- `purge_old_run_events(days=90)` → int  # Retention cleanup
- `get_event_stats()` → Dict[str, Any]  # Monitoring helper

**Feedback API** (wrappers to `feedback/langsmith_sync.py`):
- `update_pattern_confidence_from_feedback(pattern_key, min_feedback_count=3)` → Optional[float]
- `apply_confidence_decay(decay_factor=0.95, min_confidence=0.1)` → int

> **Note**: Feedback functions delegate to `feedback/langsmith_sync.py`. Unit tests validate
> the confidence update logic; end-to-end LangSmith sync requires `LANGCHAIN_API_KEY` configuration.

**Canonicalization (deterministic JSON encoding inspired by RFC 8785)**:
- `jcs_canonicalize(obj)` → bytes  # Deterministic JSON serialization
- `hash_canonical_sequence(steps)` → str  # Deterministic sequence hash
- `JCSEncodingError`  # Raised for NaN/Infinity per I-JSON

> **Note on RFC 8785 compliance**: Our encoder implements RFC 8785 semantics and is validated
> against the `rfc8785` reference implementation (PyPI). See `TestRFC8785ReferenceComparison`.

### 4.2 Integration Points

| File | Integration |
|------|-------------|
| `graph/nodes/persist_kg_learning.py` | Calls `capture_run_events()` after workflow |
| `graph/nodes/align_task_with_kg.py` | Calls `record_pattern_match()` on template match |
| `feedback/langsmith_sync.py` | **Implementation** of feedback→confidence update (wrapped by pattern_discovery) |
| `config/__init__.py` | Defines all `PATTERN_*` flags |

---

## 5. Algorithm

### 5.1 Canonical Sequence Format

Workflows are canonicalized to enable cross-provider matching:

```python
CanonicalStep = {
    "method": "POST",           # HTTP method
    "action": "create",         # Semantic action (create/read/update/delete/list)
    "resource_type": "pet",     # Normalized resource (from path)
    "path_template": "/{resource}",  # Normalized path pattern
    "position": 1,              # Step position in workflow
}
```

### 5.2 Signature Abstraction Levels

The system uses a **dual-hash approach** for cross-provider pattern matching:

| Signature Type | Included Fields | Purpose | Risk Tradeoff |
|----------------|-----------------|---------|---------------|
| `signature_full` | method + action + resource_type + path_template + position | Unique identification | May miss valid cross-provider matches |
| `signature_abstract` | method + action + position | Cross-provider transfer | May produce false positives across unrelated APIs |

**Operational guidance**:
- Use `signature_full` for deduplication within a single provider
- Use `signature_abstract` for discovering cross-provider opportunities
- Always verify `signature_abstract` matches with additional context (e.g., resource semantics)

### 5.3 Hashing (Deterministic JSON Encoding)

Pattern keys use deterministic hashing inspired by RFC 8785 (JSON Canonicalization Scheme):

```python
def hash_canonical_sequence(steps: List[CanonicalStep]) -> str:
    """
    Generate deterministic hash using RFC 8785-style canonicalization.
    
    Ensures identical sequences produce identical hashes across:
    - Dict ordering (sorted by UTF-16 code units)
    - JSON formatting (minimal escaping)
    - Python versions
    
    Validated against `rfc8785` reference implementation.
    """
    canonical_json = jcs_canonicalize([s.to_dict() for s in steps])
    return hashlib.sha256(canonical_json).hexdigest()[:12]
```

### 5.4 Discovery Logic

1. Query `kg_run_events` grouped by `run_id`
2. Compute canonical sequence per run
3. Hash sequences to find duplicates
4. Sequences appearing in N+ runs → candidates

### 5.5 Promotion Gates

A candidate becomes a pattern when:
1. `support_count >= PATTERN_PROMOTION_THRESHOLD`
2. Average feedback score >= `PATTERN_MIN_FEEDBACK_SCORE`
3. No existing pattern has >80% sequence overlap

---

## 6. Event Attributes (OTEL Semantic Conventions)

Event attributes in `kg_run_events.attributes` use **stable** OpenTelemetry semantic conventions
from the HTTP spans specification.

| Attribute Key | Stability | Description | Example |
|---------------|-----------|-------------|---------|
| `http.request.method` | Stable | HTTP method | `"POST"` |
| `url.path` | Stable | Request path | `"/v1/pets"` |
| `http.response.status_code` | Stable | Response status | `201` |
| `server.address` | Stable | API host | `"api.example.com"` |

Legacy columns (`endpoint_method`, `endpoint_path`) retained for query performance.

### References

- **HTTP Spans Specification (Stable)**: https://opentelemetry.io/docs/specs/semconv/http/http-spans/
- **HTTP Attribute Registry**: https://opentelemetry.io/docs/specs/semconv/attributes-registry/http/
- **Migration Notes**: https://opentelemetry.io/docs/specs/semconv/http/migration-guide/

---

## 7. Testing

### 7.1 Unit Tests (SQLite)

```bash
pytest -m sqlite tests/test_pattern_learning.py -v
```

Covers: imports, flag behavior, confidence adjustment, pattern availability, RFC 8785 compliance.

### 7.2 Integration Tests (Postgres)

```bash
# Start Postgres
docker-compose up -d db

# Wait for healthy
docker-compose exec db pg_isready -U postgres

# Run Postgres tests
pytest -m postgres tests/test_pattern_learning_postgres.py -v
```

Covers: schema existence, cross-provider transfer, feedback loops, determinism.

### 7.3 Combined Suite (CI Gate)

Both suites can run in a single process without env contamination:

```bash
pytest tests/test_pattern_learning.py tests/test_pattern_learning_postgres.py -v
```

### 7.4 CI Release Gate

**These commands must all pass for a release:**

```bash
# 1. SQLite pattern learning (40 tests)
pytest -m sqlite tests/test_pattern_learning.py -v --tb=short

# 2. Postgres pattern learning (13 tests, requires docker-compose db)
pytest -m postgres tests/test_pattern_learning_postgres.py -v --tb=short

# 3. Combined run (verifies no env contamination)
pytest tests/test_pattern_learning.py tests/test_pattern_learning_postgres.py -q
```

**Test counts as of 2025-01-15:**
- SQLite: 40 tests
- Postgres: 13 tests
- Total: 53 tests

---

## 8. Operational Considerations

### 8.1 Event Retention

`kg_run_events` will grow with usage. Use the built-in retention function:

```python
from integration_coworker.kg.pattern_discovery import purge_old_run_events, get_event_stats

# Check current stats
stats = get_event_stats()
print(f"Total events: {stats['total_events']}, spanning {stats['oldest_event']} to {stats['newest_event']}")

# Purge events older than 30 days
deleted = purge_old_run_events(days=30)
print(f"Deleted {deleted} old events")
```

### 8.2 Dedupe Constraint

Events have a unique constraint to prevent double-capture on retries:

```sql
-- Implemented in both db.py and postgres.py
CREATE UNIQUE INDEX kg_run_events_dedup_idx ON kg_run_events(run_id, position, activity, event_type);
```

### 8.3 Monitoring

Key metrics to track:
- `kg_run_events` row count (should plateau with retention)
- `kg_pattern_candidates` pending count (discovery backlog)
- Pattern promotion rate (candidates → patterns)
- Match hit rate (learned vs seeded patterns)

---

## 9. Future Work

1. **Module split**: Refactor `pattern_discovery.py` into submodules
2. **Graph mining**: Add DAG pattern discovery for complex workflows
3. **Feedback UI**: Surface pattern matches for human review
4. **Export**: OTEL trace export for external analysis

---

## 10. References

### Standards
- [RFC 8785: JSON Canonicalization Scheme](https://datatracker.ietf.org/doc/html/rfc8785)
- [RFC 7493: I-JSON (Internet JSON)](https://datatracker.ietf.org/doc/html/rfc7493)

### OpenTelemetry
- [HTTP Spans Specification (Stable)](https://opentelemetry.io/docs/specs/semconv/http/http-spans/)
- [HTTP Attribute Registry](https://opentelemetry.io/docs/specs/semconv/attributes-registry/http/)
- [HTTP Migration Guide](https://opentelemetry.io/docs/specs/semconv/http/migration-guide/)

### Reference Implementations
- [rfc8785 Python Package](https://pypi.org/project/rfc8785/) — Used for compliance validation
- [Cyberphone JCS Reference](https://github.com/cyberphone/json-canonicalization)

### Background
- [Process Mining Manifesto](https://www.tf-pm.org/resources/process-mining-manifesto)
- [Feature Toggles (Martin Fowler)](https://martinfowler.com/articles/feature-toggles.html)
