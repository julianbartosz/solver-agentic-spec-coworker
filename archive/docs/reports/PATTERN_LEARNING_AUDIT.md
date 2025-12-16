# Dynamic Domain Pattern Learning System - Audit Report

**Date**: 2024-12-XX  
**Auditor**: GitHub Copilot  
**Scope**: Comprehensive audit of current pattern system before implementing dynamic pattern learning

---

## Executive Summary

The current system has **7 hardcoded CRUD patterns** (`STANDARD_PATTERNS`) that are seeded at DB init time. Templates are learned per-run and stored in `kg.nodes`, but **no new patterns are dynamically created** - only existing patterns are matched and tracked. This creates a significant limitation: the system cannot learn domain-specific patterns (e.g., "webhook_subscribe", "oauth_flow", "bulk_import") that emerge from real-world usage.

---

## Deliverable A: Pattern Lifecycle Map

### Current Pattern Flow

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        PATTERN LIFECYCLE (CURRENT)                          │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. SEEDING (init_schema)                                                    │
│     └── persistence/db.py:860 → seed_kg.py:seed_standard_patterns()          │
│         └── Inserts 7 hardcoded patterns from kg/__init__.py:STANDARD_PATTERNS│
│                                                                              │
│  2. MATCHING (align_task_with_kg)                                            │
│     ├── align_task_with_kg.py:998 → _query_kg_templates()                    │
│     │   └── First tries provider-specific templates from kg.nodes            │
│     └── kg/__init__.py:727 → query_kg_patterns()                             │
│         ├── Queries pattern nodes from kg.nodes by node_type='pattern'       │
│         └── Falls back to kg/__init__.py:648 → infer_pattern_from_task()     │
│             └── Uses hardcoded STANDARD_PATTERNS for rule-based matching     │
│                                                                              │
│  3. LEARNING (persist_kg_learning)                                           │
│     ├── persist_kg_learning.py:702 → _detect_pattern_for_workflow()          │
│     │   └── Matches workflow to EXISTING patterns only (no new creation)     │
│     ├── persist_kg_learning.py:730 → Creates IMPLEMENTS_PATTERN edge         │
│     │   └── Links template to pattern: template → pattern                    │
│     └── persist_kg_learning.py:868 → _increment_usage_count()                │
│         └── Increments usage_count for matched pattern                       │
│                                                                              │
│  4. FEEDBACK (langsmith_sync.py)                                             │
│     ├── Syncs LangSmith feedback to kg_feedback_records                      │
│     └── Can adjust confidence_score on kg.nodes (not implemented fully)      │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Gap Analysis

| Component | Current State | Gap |
|-----------|---------------|-----|
| Pattern Creation | Static at init | No dynamic pattern discovery |
| Pattern Matching | Rule-based + embedding | Good, but limited to 7 patterns |
| Pattern Evolution | None | No clustering, refinement, or merging |
| Cross-Provider Transfer | Partial | Works via IMPLEMENTS_PATTERN edges |
| Confidence Learning | Schema exists | Not wired to adjust scores |

---

## Deliverable B: Module List

### Pattern-Related Modules

| Module | File | Purpose | Lines of Interest |
|--------|------|---------|-------------------|
| **kg/__init__.py** | `src/integration_coworker/kg/__init__.py` | Pattern definitions, inference, KG queries | 553-730 (STANDARD_PATTERNS, infer_pattern_from_task, query_kg_patterns) |
| **align_task_with_kg.py** | `src/integration_coworker/graph/nodes/align_task_with_kg.py` | Pattern matching during workflow planning | 38 (import), 939-1073 (main logic) |
| **persist_kg_learning.py** | `src/integration_coworker/graph/nodes/persist_kg_learning.py` | KG persistence, pattern detection | 100-190 (_detect_pattern_for_workflow), 700-870 (pattern linking) |
| **seed_kg.py** | `src/integration_coworker/persistence/seed_kg.py` | Pattern seeding at init | 390-500 (seed_standard_patterns) |
| **db.py** | `src/integration_coworker/persistence/db.py` | SQLite KG schema | 737-830 (kg_nodes, kg_edges, etc.) |
| **postgres.py** | `src/integration_coworker/persistence/postgres.py` | Postgres KG schema | Schema mirrors db.py |
| **domain/models.py** | `src/integration_coworker/domain/models.py` | KGNodeType, KGEdgeRelation enums | 300-450 (KG models) |
| **langsmith_sync.py** | `src/integration_coworker/feedback/langsmith_sync.py` | Feedback sync for learning | 1-250 (feedback handling) |

### Supporting Modules

| Module | Purpose | Relevance |
|--------|---------|-----------|
| **llm/client.py** | LLM calls with LangSmith tracing | Could use for pattern extraction |
| **retrieval/** | Semantic search infrastructure | Pattern embedding queries |
| **graph/runtime.py** | LangGraph workflow builder | Node registration |

---

## Deliverable C: Seeded vs Learned Patterns

### Seeded Patterns (7 total, hardcoded)

| Pattern Key | HTTP Methods | Path Pattern | Description |
|-------------|--------------|--------------|-------------|
| `crud_create` | POST | - | Create new resource with validation |
| `crud_read` | GET | `.*\{[^}]+\}$` | Retrieve single resource by ID |
| `crud_list` | GET | `.*[^}]$` | List resources with pagination |
| `crud_update` | PUT, PATCH | - | Update existing resource |
| `crud_delete` | DELETE | - | Delete resource |
| `search_filter` | GET | - | Search with query params |
| `nested_resource` | GET, POST | `.*\{[^}]+\}/[^/]+$` | Sub-resources (/users/{id}/orders) |

**Seeding Location**: `persistence/seed_kg.py:416` inserts into `kg.nodes` with:
- `node_type = "pattern"`
- `properties.seeded = true`
- `confidence_score = 1.0`

### Learned Patterns (0 - Not Implemented)

**Current behavior**: The system can **match** existing patterns but cannot **create** new ones.

In `persist_kg_learning.py:702`:
```python
if pattern_key and pattern_key in STANDARD_PATTERNS:
    pattern = STANDARD_PATTERNS[pattern_key]
    # Creates IMPLEMENTS_PATTERN edge, but doesn't create new patterns
```

**What's Missing**:
1. No pattern clustering from similar workflows
2. No pattern extraction from repeated workflow structures
3. No pattern generalization (e.g., "send_notification" from SMS/Email/Push)
4. No confidence decay for unused patterns

---

## Deliverable D: DB Backends

### SQLite Schema (from db.py:737-830)

```sql
-- Core KG nodes table
CREATE TABLE kg_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_type TEXT NOT NULL,        -- 'pattern', 'workflow_template', 'endpoint', etc.
    provider_code TEXT,             -- NULL for cross-provider patterns
    key TEXT NOT NULL,              -- e.g., "pattern.crud_create"
    name TEXT NOT NULL,
    description TEXT,
    properties TEXT DEFAULT '{}',   -- JSON with steps, http_methods, etc.
    embedding TEXT,                 -- JSON array for SQLite
    confidence_score REAL DEFAULT 1.0,
    usage_count INTEGER DEFAULT 0,
    last_used_at TEXT,
    source_run_id TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(node_type, key)
);

-- KG edges for relationships
CREATE TABLE kg_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_node_id INTEGER NOT NULL,
    dst_node_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,    -- 'implements_pattern', 'derived_from', etc.
    weight REAL DEFAULT 1.0,
    properties TEXT DEFAULT '{}',
    UNIQUE(src_node_id, dst_node_id, relation_type)
);

-- Workflow steps within templates
CREATE TABLE kg_workflow_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_node_id INTEGER NOT NULL,
    step_key TEXT NOT NULL,
    step_type TEXT NOT NULL,        -- 'api_call', 'validation', 'transform'
    position INTEGER NOT NULL,
    UNIQUE(template_node_id, step_key)
);

-- Step to endpoint bindings
CREATE TABLE kg_step_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id INTEGER NOT NULL,
    endpoint_node_id INTEGER,
    endpoint_path TEXT,
    endpoint_method TEXT
);

-- Feedback records for learning
CREATE TABLE kg_feedback_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    template_key TEXT,
    pattern_key TEXT,
    feedback_type TEXT NOT NULL,    -- 'thumbs', 'score', 'auto_compile'
    score REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'langsmith'
);

-- Confidence history for auditing
CREATE TABLE kg_confidence_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_key TEXT NOT NULL,
    old_confidence REAL,
    new_confidence REAL NOT NULL,
    feedback_count INTEGER DEFAULT 0,
    reason TEXT
);
```

### PostgreSQL Schema (from postgres.py)

Same structure as SQLite but with:
- Schema namespacing: `kg.nodes`, `kg.edges`, etc.
- Native JSONB for properties
- **pgvector** for embeddings: `embedding vector(1536)`
- Proper foreign key constraints

### Current KG State (from demo run)

```
Nodes: 758 total
  - provider: 3 (stripe, github, twilio)
  - task: 3
  - workflow_template: 4
  - pattern: 7 (seeded)
  - entity: ~100
  - endpoint: ~600

Edges: 756 total
  - belongs_to_provider: most common
  - implements_pattern: 4 (one per template)
  - uses_endpoint: workflow→endpoint links
```

---

## Critical Findings

### 🔴 Blocker: No Dynamic Pattern Creation

The `_detect_pattern_for_workflow()` function only matches against `STANDARD_PATTERNS` - it cannot create new patterns from observed workflows.

**Impact**: System cannot learn domain-specific patterns like:
- `webhook_subscribe` (POST to /webhooks + GET to verify)
- `oauth_flow` (authorize → callback → token_exchange)
- `batch_operation` (upload → poll_status → download_result)
- `paginated_export` (iterate → accumulate → export)

### 🟡 Partial: Feedback → Confidence Loop

Tables exist (`kg_feedback_records`, `kg_confidence_history`) but:
- Confidence adjustment is not wired in `persist_kg_learning`
- No mechanism to promote high-confidence learned patterns
- No mechanism to demote/archive low-confidence patterns

### 🟢 Working: Template Learning

Provider-specific workflow templates ARE learned and persisted:
- Created in `persist_kg_learning.py:585` as `workflow_template` nodes
- Steps stored in `kg_workflow_steps`
- Can be retrieved via `query_workflow_templates()`

---

## Recommended Architecture for Dynamic Pattern Learning

### Option 1: LLM-Assisted Pattern Extraction (Recommended)

Add a new node `extract_patterns` that:
1. Analyzes successful workflow runs (thumbs-up or auto-compile=success)
2. Uses LLM to identify recurring structural patterns
3. Creates new `pattern` nodes with `seeded=false`
4. Requires N confirmations before full activation

**Pros**: Can extract complex, domain-specific patterns
**Cons**: LLM cost, potential hallucinations

### Option 2: Statistical Clustering

Add offline process that:
1. Clusters workflow_template nodes by step structure
2. Extracts common subsequences
3. Creates patterns from clusters with N+ members

**Pros**: No LLM cost, deterministic
**Cons**: Misses semantic patterns, requires critical mass

### Option 3: Hybrid (Best of Both)

1. Use clustering for pattern candidates
2. Use LLM to name/describe patterns
3. Use feedback to validate/promote

---

## Next Steps

1. **Choose Architecture**: Recommend Option 3 (Hybrid) for balance
2. **Schema Changes**: Add `seeded` boolean to `kg.nodes` (already exists in properties)
3. **New Module**: `src/integration_coworker/kg/pattern_discovery.py`
4. **New Node**: `graph/nodes/discover_patterns.py` (optional, can be offline)
5. **Wire Feedback Loop**: Connect feedback → confidence adjustment

---

## Appendix: Key Code Locations

```python
# Pattern definitions
src/integration_coworker/kg/__init__.py:553-630  # STANDARD_PATTERNS dict

# Pattern matching
src/integration_coworker/kg/__init__.py:648-720  # infer_pattern_from_task()
src/integration_coworker/kg/__init__.py:727-850  # query_kg_patterns()

# Pattern detection during learning
src/integration_coworker/graph/nodes/persist_kg_learning.py:100-190

# Pattern seeding
src/integration_coworker/persistence/seed_kg.py:395-500

# Feedback tables
src/integration_coworker/persistence/db.py:814-860
src/integration_coworker/feedback/langsmith_sync.py
```
