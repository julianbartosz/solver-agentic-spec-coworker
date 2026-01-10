# LangSmith Feedback-Based KG Learning Implementation Plan

**Date:** December 5, 2025  
**Status:** ✅ Implemented (P1-P4 Complete)  
**Last Updated:** January 5, 2026

---

## Implementation Status

| Phase | Description | Status |
|-------|-------------|--------|
| P1 | Infrastructure (models, schema, module skeleton) | ✅ Complete |
| P2 | LangSmith Sync + E2E confidence tests | ✅ Complete |
| P3a | Wire lint feedback (static_analysis_gate) | ✅ Complete |
| P3b | Scheduled sync documentation | ✅ Complete |
| P4 | Confidence decay for unused patterns | ✅ Complete |

**CLI Commands:**
- `kg-confidence` - View template confidence scores
- `kg-decay` - Apply decay to unused patterns
- `kg-maintenance` - Combined sync + decay operation

**Operations Docs:** See [docs/operations/feedback_sync.md](../operations/feedback_sync.md)

---

## 1. Problem Statement

The Knowledge Graph (KG) currently learns from successful runs by:
- Persisting workflow templates, steps, and bindings
- Incrementing `usage_count` when templates are reused
- Storing embeddings for semantic search

**The Gap**: There's no quality signal. A template used 100 times that produces bad output gets the same treatment as one that produces good output.

---

## 2. Alternative Solutions Analyzed

### Option A: Direct LangSmith Feedback Sync (Recommended ✅)

**Description**: Use LangSmith's `create_feedback()` / `list_feedback()` APIs to:
1. Allow users/operators to rate runs (thumbs up/down, 1-5 score)
2. Periodically sync feedback to adjust KG node `confidence_score`
3. Use confidence in GraphRAG scoring

**Pros**:
- Uses existing LangSmith infrastructure (already integrated)
- Feedback is already associated with traces via `run_id`
- Non-blocking async feedback creation
- Supports both human and automated evaluation
- Scales well (LangSmith handles storage)

**Cons**:
- Requires LangSmith API access (paid feature for some tiers)
- Async sync delay between feedback and KG update

**Fit Score**: ⭐⭐⭐⭐⭐ (5/5) - Already using LangSmith extensively

---

### Option B: Local Feedback Table Only

**Description**: Create our own `kg.feedback_records` table without LangSmith integration.

**Pros**:
- No external dependency
- Immediate consistency
- Works offline

**Cons**:
- Loses LangSmith's rich trace context
- Need to build our own feedback UI
- Duplicates functionality LangSmith already provides
- No connection to LLM call quality

**Fit Score**: ⭐⭐ (2/5) - Reinvents the wheel

---

### Option C: Implicit Success/Failure Signals

**Description**: Use automatic signals like:
- Did the code compile? (`py_compile` check)
- Did tests pass? (if run)
- Were there validation errors?

**Pros**:
- Fully automated
- No human in the loop required

**Cons**:
- Limited signal (success != quality)
- False positives (compiles but wrong)
- Can't capture user intent satisfaction

**Fit Score**: ⭐⭐⭐ (3/5) - Good supplement, not primary

---

### Option D: LLM-as-Judge Evaluation

**Description**: Use an LLM to evaluate generated code quality.

**Pros**:
- Automated and detailed
- Can evaluate semantic correctness

**Cons**:
- Expensive (more LLM calls)
- Circular problem (LLM judging LLM)
- Latency
- Still need ground truth

**Fit Score**: ⭐⭐ (2/5) - Expensive and uncertain

---

### Option E: Hybrid: LangSmith + Implicit Signals (Best ✅)

**Description**: Combine Options A and C:
1. LangSmith feedback as primary quality signal (human/automated)
2. Implicit success signals as secondary boost
3. Both flow into `confidence_score`

**Formula**:
```
confidence_score = 
    (langsmith_feedback_avg * 0.6) + 
    (implicit_success_rate * 0.3) + 
    (base_confidence * 0.1)
```

**Pros**:
- Best of both worlds
- Works with or without human feedback
- Graceful degradation

**Fit Score**: ⭐⭐⭐⭐⭐ (5/5) - Maximum flexibility

---

## 3. Recommended Implementation: Hybrid (Option E)

### 3.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         LangSmith                                │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Traces (run_id → trace)                                    │ │
│  │  Feedback (run_id → score, comment)                         │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────┬──────────────────────────────────┘
                               │ sync (async/scheduled)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Integration Coworker                          │
│  ┌─────────────────────┐   ┌────────────────────────────────┐   │
│  │ kg.feedback_records │◄──│ sync_langsmith_feedback()      │   │
│  │ - run_id            │   │ - Fetches feedback via API     │   │
│  │ - template_key      │   │ - Maps to template_key         │   │
│  │ - score             │   │ - Upserts to kg.feedback       │   │
│  │ - feedback_type     │   └────────────────────────────────┘   │
│  │ - source (ls/auto)  │                                        │
│  └─────────┬───────────┘                                        │
│            │                                                     │
│            ▼                                                     │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ update_kg_confidence()                                      │ │
│  │ - Aggregates feedback per template                          │ │
│  │ - Updates kg.nodes.confidence_score                         │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ GraphRAG Scoring (kg/__init__.py)                           │ │
│  │ - Uses confidence_score in ranking                          │ │
│  │ final_score = (graph*0.35) + (embed*0.35) +                 │ │
│  │               (confidence*0.15) + (exact*0.15)              │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 New Database Schema

```sql
-- kg.feedback_records: Stores feedback synced from LangSmith and implicit signals
CREATE TABLE IF NOT EXISTS kg.feedback_records (
    id               BIGSERIAL PRIMARY KEY,
    run_id           TEXT NOT NULL,              -- Our run_id (maps to LangSmith trace)
    template_key     TEXT,                        -- kg.nodes key for the template used
    pattern_key      TEXT,                        -- kg.nodes key for the pattern used
    feedback_type    TEXT NOT NULL,              -- 'thumbs', 'score', 'auto_compile', 'auto_test'
    score            DOUBLE PRECISION NOT NULL,  -- Normalized 0-1 (thumbs: 0/1, score: 0-1)
    comment          TEXT,                        -- Optional feedback comment
    source           TEXT NOT NULL DEFAULT 'langsmith', -- 'langsmith', 'cli', 'auto'
    langsmith_feedback_id TEXT,                   -- Original LangSmith feedback ID
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS kg_feedback_run_idx ON kg.feedback_records(run_id);
CREATE INDEX IF NOT EXISTS kg_feedback_template_idx ON kg.feedback_records(template_key);
CREATE INDEX IF NOT EXISTS kg_feedback_pattern_idx ON kg.feedback_records(pattern_key);

-- kg.confidence_history: Track confidence changes over time (for analysis)
CREATE TABLE IF NOT EXISTS kg.confidence_history (
    id               BIGSERIAL PRIMARY KEY,
    node_key         TEXT NOT NULL,
    old_confidence   DOUBLE PRECISION,
    new_confidence   DOUBLE PRECISION NOT NULL,
    feedback_count   INT NOT NULL,
    reason           TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS kg_confidence_history_node_idx ON kg.confidence_history(node_key);
```

### 3.3 New Domain Models

```python
# In domain/models.py

@dataclass
class FeedbackRecord:
    """A feedback record for a run/template."""
    id: Optional[int] = None
    run_id: str = ""
    template_key: Optional[str] = None
    pattern_key: Optional[str] = None
    feedback_type: str = "thumbs"  # 'thumbs', 'score', 'auto_compile', 'auto_test'
    score: float = 0.0  # 0-1 normalized
    comment: Optional[str] = None
    source: str = "langsmith"  # 'langsmith', 'cli', 'auto'
    langsmith_feedback_id: Optional[str] = None
    created_at: Optional[str] = None


class FeedbackType(str, Enum):
    """Types of feedback that can be recorded."""
    THUMBS = "thumbs"  # Binary: 0 or 1
    SCORE = "score"  # Numeric: 0.0 to 1.0
    AUTO_COMPILE = "auto_compile"  # 0 (fail) or 1 (success)
    AUTO_TEST = "auto_test"  # 0 (fail) or 1 (success)
    AUTO_LINT = "auto_lint"  # 0-1 based on lint score
```

### 3.4 Feedback Module Structure

```
src/integration_coworker/feedback/
├── __init__.py          # Public API: sync_feedback, create_feedback, update_confidence
├── langsmith_sync.py    # LangSmith API integration
├── implicit_signals.py  # Auto-feedback from compile/test/lint results
└── confidence.py        # Confidence score aggregation logic
```

### 3.5 CLI Commands

```bash
# Submit manual feedback
integration-coworker feedback <run_id> --score 0.8 --comment "Good output"
integration-coworker feedback <run_id> --thumbs-up
integration-coworker feedback <run_id> --thumbs-down --comment "Wrong endpoint"

# Sync from LangSmith
integration-coworker feedback-sync              # Sync recent (last 24h)
integration-coworker feedback-sync --days 7     # Sync last 7 days
integration-coworker feedback-sync --run <id>   # Sync specific run

# View feedback and confidence
integration-coworker kg-confidence <template_key>
integration-coworker kg-feedback --provider stripe
```

### 3.6 GraphRAG Scoring Update

Current formula:
```python
final_score = (graph_score * 0.4) + (embedding_score * 0.4) + exact_match_bonus + 0.1
```

New formula:
```python
# confidence_score: 0-1, pulled from kg.nodes
# default 0.5 for templates without feedback
confidence_boost = (confidence_score - 0.5) * 0.3  # -0.15 to +0.15

final_score = (
    (graph_score * 0.35) + 
    (embedding_score * 0.35) + 
    (confidence_boost) +
    exact_match_bonus + 
    0.1
)
```

### 3.7 Confidence Aggregation Algorithm

```python
def compute_confidence_score(feedbacks: List[FeedbackRecord]) -> float:
    """
    Aggregate feedback into a single confidence score (0-1).
    
    Weights:
    - Human feedback (langsmith/cli): 1.0
    - Auto compile: 0.3
    - Auto test: 0.5
    - Auto lint: 0.2
    
    Recent feedback weighted more heavily (exponential decay).
    """
    if not feedbacks:
        return 0.5  # Default neutral confidence
    
    weights = {
        'langsmith': 1.0,
        'cli': 1.0,
        'auto': 0.4,  # Auto signals get lower weight
    }
    
    type_weights = {
        'thumbs': 1.0,
        'score': 1.0,
        'auto_compile': 0.3,
        'auto_test': 0.5,
        'auto_lint': 0.2,
    }
    
    weighted_sum = 0.0
    total_weight = 0.0
    
    now = datetime.now()
    for fb in feedbacks:
        # Recency decay: half-life of 30 days
        age_days = (now - fb.created_at).days
        recency_weight = 0.5 ** (age_days / 30)
        
        source_weight = weights.get(fb.source, 0.5)
        type_weight = type_weights.get(fb.feedback_type, 0.5)
        
        combined_weight = recency_weight * source_weight * type_weight
        weighted_sum += fb.score * combined_weight
        total_weight += combined_weight
    
    if total_weight == 0:
        return 0.5
    
    return min(1.0, max(0.0, weighted_sum / total_weight))
```

---

## 4. Implementation Phases

### Phase 1: Infrastructure (Day 1) ✅
- [x] Add `FeedbackRecord`, `FeedbackType` to domain/models.py
- [x] Add `kg.feedback_records`, `kg.confidence_history` to DDL
- [x] Create `src/integration_coworker/feedback/` module skeleton

### Phase 2: LangSmith Sync (Day 1-2) ✅
- [x] Implement `langsmith_sync.py`:
  - `fetch_recent_feedback()` - Get feedback from LangSmith API
  - `map_feedback_to_templates()` - Link run_id → template_key
  - `sync_feedback_batch()` - Upsert to kg.feedback_records
- [x] Add `feedback-sync` CLI command
- [x] Add E2E Postgres tests for feedback → confidence → KG ranking

### Phase 3: Confidence Scoring (Day 2) ✅
- [x] Implement `confidence.py`:
  - `compute_confidence_score()` - Aggregation algorithm
  - `update_node_confidence()` - Update kg.nodes
  - `update_all_confidences()` - Batch update
- [x] Add `kg.confidence_history` logging
- [x] Update `kg/__init__.py` to use confidence in scoring (10% weight)

### Phase 3a: Lint Feedback Hook (PR #12) ✅
- [x] Wire `safe_record_lint_result()` in `static_analysis_gate.py`
- [x] Single choke point - all lint results flow through this node

### Phase 3b: Scheduled Sync Documentation ✅
- [x] Create `docs/operations/feedback_sync.md`
- [x] Document systemd/cron/K8s/Lambda scheduling options

### Phase 4: Implicit Signals (Day 2-3) ✅
- [x] Implement `implicit_signals.py`:
  - `record_compile_result()` - From validate_integration_design
  - `record_lint_result()` - From static analysis
- [x] Wire into `validate_integration_design` node
- [x] **P4: Confidence Decay** - Apply decay to unused patterns
  - `apply_confidence_decay()` in langsmith_sync.py
  - `kg-decay` CLI command for manual/scheduled decay
  - `kg-maintenance` CLI for combined sync + decay

### Phase 5: CLI & Testing (Day 3) ✅
- [x] Add `feedback` CLI command for manual feedback
- [x] Add `kg-confidence` CLI command
- [x] Add `kg-decay` CLI command (P4)
- [x] Add `kg-maintenance` CLI command (combined operations)
- [x] Write tests for:
  - LangSmith sync (mocked API)
  - Confidence aggregation
  - GraphRAG scoring with confidence
  - E2E Postgres tests (test_feedback_confidence_e2e_postgres.py)

---

## 5. Files to Create/Modify

### New Files
| File | Description |
|------|-------------|
| `src/integration_coworker/feedback/__init__.py` | Public API |
| `src/integration_coworker/feedback/langsmith_sync.py` | LangSmith API sync |
| `src/integration_coworker/feedback/confidence.py` | Confidence aggregation |
| `src/integration_coworker/feedback/implicit_signals.py` | Auto-feedback |
| `tests/test_feedback_sync.py` | Unit tests |

### Modified Files
| File | Changes |
|------|---------|
| `domain/models.py` | Add FeedbackRecord, FeedbackType |
| `persistence/postgres.py` | Add kg.feedback_records DDL |
| `persistence/db.py` | Add SQLite schema for feedback |
| `kg/__init__.py` | Use confidence_score in scoring |
| `cli.py` | Add feedback commands |
| `graph/nodes/persist_run_outcome.py` | Record implicit signals |

---

## 6. Success Metrics

1. **Feedback Coverage**: % of runs with feedback (target: 10%+ for active use)
2. **Confidence Variance**: Non-default confidence scores exist (templates differentiated)
3. **Quality Correlation**: High-confidence templates produce better outputs
4. **Learning Rate**: New providers converge to stable confidence within 10-20 runs

---

## 7. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| LangSmith API rate limits | Batch syncs, exponential backoff |
| No feedback provided | Implicit signals as fallback |
| Gaming the system | Recency decay, source weighting |
| Cold start for new templates | Default 0.5 confidence (neutral) |

---

## 8. Decision

**Recommendation**: Implement Hybrid Option (E)

**Rationale**:
1. Maximizes value from existing LangSmith integration
2. Provides fallback with implicit signals
3. Non-disruptive to existing architecture
4. Scales with usage

**Approved by**: [Pending review]

---
