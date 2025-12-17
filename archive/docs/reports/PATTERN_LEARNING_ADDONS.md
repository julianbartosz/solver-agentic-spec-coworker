# Dynamic Pattern Learning — Post-V1 Add-Ons

**Created**: 2025-01-15  
**Status**: Planning Document (Future Work)  
**Prerequisite**: V1 Production Ready (see `PATTERN_LEARNING_DESIGN.md`)

---

## 1. Current State Summary

### 1.1 What V1 Delivers

| Component | Implementation | Location |
|-----------|---------------|----------|
| **Event capture** | Record workflow events to `kg_run_events` | `pattern_discovery.py` |
| **Canonical hashing** | RFC 8785 JCS deterministic JSON encoding | `jcs_canonicalize()` |
| **Pattern discovery** | Frequency-based mining (`support_count >= N`) | `discover_pattern_candidates()` |
| **Pattern promotion** | Candidate → `kg.nodes` with confidence | `promote_pattern_candidate()` |
| **Pattern matching** | Exact + semantic match recording | `record_pattern_match()` |
| **Feedback integration** | LangSmith sync → confidence adjustment | `update_pattern_confidence_from_feedback()` |
| **Retention** | Purge old events with `purge_old_run_events()` | `purge_old_run_events(days=90)` |
| **Dual-hash abstraction** | `signature_full` + `signature_abstract` | Cross-provider transfer |

### 1.2 Schema (SQLite + Postgres Parity)

| Table | Rows (Typical) | Purpose |
|-------|---------------|---------|
| `kg_run_events` | 10K–1M | Event log (retained 90 days) |
| `kg_pattern_candidates` | 100–10K | Staging for discovered patterns |
| `kg_pattern_matches` | 1K–100K | Match audit trail |
| `kg.nodes` (origin column) | N/A | Tracks `seeded`/`learned`/`manual` |

### 1.3 Test Coverage

| Suite | Tests | Marker |
|-------|-------|--------|
| SQLite unit | 40 | `@pytest.mark.sqlite` |
| Postgres integration | 13 | `@pytest.mark.postgres` |
| **Total** | **53** | |

### 1.4 Feature Flags (All Default OFF)

```
PATTERN_LEARNING_ENABLED, PATTERN_CAPTURE_EVENTS, PATTERN_DISCOVER_CANDIDATES,
PATTERN_AUTO_PROMOTE, PATTERN_MATCH_LEARNED, PATTERN_PROMOTION_THRESHOLD,
PATTERN_MIN_FEEDBACK_SCORE, PATTERN_CONFIDENCE_DECAY
```

---

## 2. Post-V1 Add-Ons by Theme

### 2.1 Pattern Mining Scale

**Problem**: V1 uses flat sequence hashing. As run volume grows (>100K events), discovery becomes slow and memory-bound.

| Add-On | Description | Priority |
|--------|-------------|----------|
| **Incremental mining** | Process only new events since last run; maintain rolling state | High |
| **Partitioned discovery** | Mine per-provider, then cross-provider; reduce search space | Medium |
| **Hot/cold storage** | Archive events >90d to cold storage; keep hot for mining | Medium |
| **DAG pattern mining** | Detect subgraph patterns (not just sequences) for branching flows | Low (post-MVP) |

**Schema impact**: Add `kg_run_events.mined_at` column; add `kg_mining_state` table.

**Risk**: DAG mining is NP-hard in general; need to bound subgraph size.

---

### 2.2 Pattern Quality

**Problem**: V1 promotes patterns purely by frequency. High support ≠ high quality. False positives degrade downstream workflows.

| Add-On | Description | Priority |
|--------|-------------|----------|
| **Semantic validation gate** | LLM check before promotion: "Does this sequence represent a valid workflow?" | High |
| **Overlap/merge detection** | Detect near-duplicate candidates; offer merge UI | High |
| **False-positive controls** | Manual "reject" + auto-decay for unused patterns | Medium |
| **Offline eval harness** | Run labeled runs through matcher; compute precision/recall | Medium |
| **Persisted quality metrics** | Store per-pattern precision/recall in `kg_pattern_metrics` | Low |

**Schema impact**: Add `kg_pattern_candidates.semantic_score`, `kg_pattern_metrics` table.

**Risk**: LLM validation adds latency to promotion path; make async.

---

### 2.3 Human-in-the-Loop (HITL)

**Problem**: V1 auto-promotes or requires CLI intervention. No dashboard for pattern stewardship.

| Add-On | Description | Priority |
|--------|-------------|----------|
| **Review dashboard** | List pending candidates; approve/reject/snooze buttons | High |
| **Audit trail** | Log who approved/rejected; reason; timestamp | High |
| **Merge UI** | Select overlapping patterns → merge into one | Medium |
| **Promotion queue** | Batch review mode with confidence scores | Medium |

**Schema impact**: Add `kg_pattern_reviews` table (reviewer, action, reason, timestamp).

**Risk**: Dashboard requires frontend work; consider Streamlit MVP first.

---

### 2.4 Governance

**Problem**: V1 patterns are mutable. No versioning, no rollback, no deprecation.

| Add-On | Description | Priority |
|--------|-------------|----------|
| **Versioned patterns** | Immutable snapshots; new version on edit | High |
| **Deprecation lifecycle** | Mark patterns deprecated; hide from matching after N days | High |
| **Provenance tracking** | Record source runs that contributed to pattern | Medium |
| **Rollback** | Restore previous version of pattern | Medium |
| **Export snapshots** | Dump pattern set as versioned JSON for audit | Low |

**Schema impact**: Add `kg_pattern_versions` table; add `kg.nodes.deprecated_at`.

**Risk**: Versioning complicates join queries; need materialized "current" view.

---

### 2.5 Operations

**Problem**: V1 has manual retention; no dashboards; no alerts.

| Add-On | Description | Priority |
|--------|-------------|----------|
| **Scheduled retention job** | Cron/Celery task for `purge_old_run_events()` | High |
| **Monitoring dashboard** | Grafana/Prometheus metrics: event count, candidate backlog, promotion rate | High |
| **Alert thresholds** | Slack/PagerDuty on: backlog > N, promotion rate drop, confidence drift | Medium |
| **Health endpoint** | `/health/patterns` returns event stats, candidate counts | Low |

**Schema impact**: None (metrics exported, not stored).

**Risk**: Dashboard work is ongoing maintenance; start with simple `/health` endpoint.

---

### 2.6 Interoperability

**Problem**: Patterns are repo-local. Cannot share learned patterns between teams/repos.

| Add-On | Description | Priority |
|--------|-------------|----------|
| **Export patterns** | CLI/API: dump patterns as JSON with deterministic signatures | High |
| **Import patterns** | CLI/API: load patterns from JSON; dedupe by signature | High |
| **Cross-repo registry** | Central pattern registry (S3/GCS); fetch on demand | Low |
| **OTEL trace export** | Export run events as OTEL traces for external analysis | Low |

**Schema impact**: None (export/import operates on existing tables).

**Risk**: Import conflicts require merge strategy; default to "skip existing".

---

### 2.7 Modularity

**Problem**: `pattern_discovery.py` is 1300 lines. Hard to navigate.

| Add-On | Description | Priority |
|--------|-------------|----------|
| **Module split** | Break into: `canonicalization.py`, `discovery.py`, `promotion.py`, `matching.py` | Medium |
| **Public API stability** | Freeze exported functions; deprecate internal helpers | Medium |

**Schema impact**: None.

**Risk**: Import path changes may break external callers. Add compatibility shims.

---

## 3. Implementation Backlog

### P0 — High Priority (Next Release)

| Ticket | Theme | Files | Schema | Flags | Tests | Acceptance Criteria | Risk |
|--------|-------|-------|--------|-------|-------|---------------------|------|
| **PL-101** | Ops | `scripts/retention_job.py` | — | — | 1 unit | Cron job calls `purge_old_run_events(days=90)` daily | Low |
| **PL-102** | Quality | `pattern_discovery.py` | Add `semantic_score` | — | 3 unit | LLM validation before auto-promote; rejects nonsense | Medium |
| **PL-103** | HITL | `api/pattern_review.py`, Streamlit page | Add `kg_pattern_reviews` | — | 2 integration | Approve/reject from UI; logged to audit table | Medium |
| **PL-104** | Governance | `pattern_discovery.py` | Add `deprecated_at` | — | 2 unit | Deprecated patterns excluded from matching | Low |
| **PL-105** | Interop | `cli/patterns.py` | — | — | 2 unit | `iw patterns export/import` CLI commands | Low |

### P1 — Medium Priority (V1.1+)

| Ticket | Theme | Files | Schema | Flags | Tests | Acceptance Criteria | Risk |
|--------|-------|-------|--------|-------|-------|---------------------|------|
| **PL-201** | Scale | `pattern_discovery.py` | Add `mined_at` | — | 2 unit | Incremental mining only processes new events | Medium |
| **PL-202** | Quality | `pattern_discovery.py` | Add `kg_pattern_metrics` | — | 2 unit | Precision/recall computed on labeled runs | Medium |
| **PL-203** | HITL | Streamlit page | — | — | 1 integration | Merge UI: select N candidates → merge | Medium |
| **PL-204** | Governance | `pattern_discovery.py` | Add `kg_pattern_versions` | — | 3 unit | Version history preserved on edits; rollback works | High |
| **PL-205** | Ops | Grafana JSON, Prometheus exporter | — | — | 1 integration | Dashboard shows event count, backlog, rates | Low |

### P2 — Low Priority (Backlog)

| Ticket | Theme | Files | Schema | Flags | Tests | Acceptance Criteria | Risk |
|--------|-------|-------|--------|-------|-------|---------------------|------|
| **PL-301** | Scale | `pattern_discovery.py` | Add `kg_mining_state` | — | 2 unit | Partitioned mining per provider | Medium |
| **PL-302** | Scale | `pattern_discovery.py` | — | — | 2 unit | Hot/cold archival after 90 days | Low |
| **PL-303** | Scale | New module | — | — | 5 unit | DAG subgraph mining (bounded depth) | High |
| **PL-304** | Interop | Registry client | — | — | 2 integration | Cross-repo pattern registry (S3) | Medium |
| **PL-305** | Interop | `pattern_discovery.py` | — | — | 1 unit | OTEL trace export for runs | Low |
| **PL-306** | Modularity | Split module | — | — | — | `pattern_discovery.py` → 4 submodules | Low |

---

## 4. Risk Summary

| Risk | Mitigation |
|------|------------|
| DAG mining complexity | Bound subgraph depth to 5; use heuristic pruning |
| LLM validation latency | Make async; cache validation results |
| Dashboard maintenance | Start with `/health` endpoint; graduate to Grafana |
| Import conflicts | Default "skip existing"; offer "force" flag |
| Module split breakage | Add compatibility shims for 2 releases |

---

## 5. Non-Goals (Explicitly Out of Scope)

- **Real-time streaming mining**: Patterns discovered batch-mode, not per-event.
- **Cross-database sync**: Patterns do not sync between SQLite/Postgres; pick one.
- **Multi-tenant isolation**: Patterns are per-database; tenant isolation is app-layer concern.

---

## 6. References

- [Process Mining Manifesto](https://www.tf-pm.org/resources/process-mining-manifesto)
- [Feature Toggles (Martin Fowler)](https://martinfowler.com/articles/feature-toggles.html)
- [OTEL Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/)
- [RFC 8785 JCS](https://datatracker.ietf.org/doc/html/rfc8785)
