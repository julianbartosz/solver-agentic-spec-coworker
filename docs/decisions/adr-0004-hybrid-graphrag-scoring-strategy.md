# ADR-0004: Hybrid GraphRAG Scoring Strategy

| Metadata       | Value                                      |
|----------------|--------------------------------------------|
| **Status**     | Accepted                                   |
| **Date**       | 2025-12-01                                 |
| **Deciders**   | Integration Coworker Team                  |
| **Supersedes** | —                                          |
| **Related**    | ADR-0001 (Initial Architecture), design-doc Section 5.5 |

---

## Context

The Integration Coworker's `align_task_with_kg` node must select the best workflow template from the Knowledge Graph (KG) given a natural language task description. This is a **retrieval problem** with multiple competing signals:

1. **Structural relevance**: Does the template connect to the entities/endpoints the user needs?
2. **Semantic relevance**: Is the template's description linguistically similar to the user's task?
3. **Lexical precision**: Do exact keywords (e.g., "checkout", "create") appear in both?

No single retrieval method optimally captures all three signals. We needed an approach that:

- Balances structure and semantics without over-indexing on either
- Remains explainable (decomposable scores for debugging)
- Degrades gracefully when embeddings are unavailable (mock LLM mode)
- Scales to thousands of templates with sub-second latency
- Integrates with the existing PostgreSQL + pgvector infrastructure

---

## Decision

We adopt a **Hybrid GraphRAG Scoring Strategy** that combines graph-based filtering and scoring with embedding-based semantic ranking, plus an exact-match bonus layer.

### Scoring Formula

```
final_score = (graph_score × 0.4) + (embedding_score × 0.4) + exact_match_bonus + base_score
```

| Component            | Weight | Range     | Description                                           |
|----------------------|--------|-----------|-------------------------------------------------------|
| `graph_score`        | 40%    | [0.0–1.0] | Structural relevance via KG edge counts               |
| `embedding_score`    | 40%    | [0.0–1.0] | Cosine similarity between task and template embeddings|
| `exact_match_bonus`  | 20%    | [0.0–0.2] | Bonus for action/resource keyword overlap             |
| `base_score`         | —      | +0.1      | Ensures graph-filtered candidates aren't zeroed out   |

### Two-Phase Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Phase 1: Graph Filtering                     │
│              (SQL WHERE — fast, coarse elimination)             │
├─────────────────────────────────────────────────────────────────┤
│  SELECT * FROM kg.nodes                                         │
│  WHERE node_type = 'workflow_template'                          │
│    AND provider_code = :provider                                │
│  ORDER BY usage_count DESC, confidence_score DESC               │
│  LIMIT 20                                                       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                   Phase 2: Hybrid Scoring                        │
│           (expensive ops on small candidate set)                 │
├─────────────────────────────────────────────────────────────────┤
│  For each candidate:                                            │
│    1. graph_score    = _compute_graph_score(template, entities) │
│    2. embedding_score = cosine(task_emb, template_emb)          │
│    3. exact_bonus    = _compute_exact_match_bonus(template,task)│
│    4. final = 0.4×graph + 0.4×embedding + exact_bonus + 0.1     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                   Return top 5 by final_score DESC
```

### Component Implementations

#### Graph Score (`_compute_graph_score`)

```python
def _compute_graph_score(template: dict, entities: List[str]) -> float:
    score = 0.3  # Base score for passing graph filter
    template_text = f"{template['name']} {template['description']}".lower()
    
    if entities:
        matching = sum(1 for e in entities if e.lower() in template_text)
        coverage = matching / len(entities)
        score += 0.3 * coverage  # Up to +0.3 for full entity coverage
    
    return min(1.0, score)
```

In the full KG path (`kg/__init__.py`), graph score additionally counts:
- **Entity edges**: +0.2 per `produces_entity`/`consumes_entity` edge (capped)
- **Endpoint edges**: +0.1 per `uses_endpoint` edge (capped at 0.5)

#### Embedding Score (`_compute_embedding_score`)

```python
def _compute_embedding_score(task_description: str, template: dict) -> float:
    task_emb = compute_embedding(task_description)
    template_emb = template.get("embedding") or compute_embedding(template_text)
    
    if task_emb and template_emb:
        return max(0.0, cosine_similarity(task_emb, template_emb))
    return 0.5  # Graceful degradation when embeddings unavailable
```

#### Exact Match Bonus (`_compute_exact_match_bonus`)

```python
def _compute_exact_match_bonus(template: dict, task_description: str) -> float:
    bonus = 0.0
    task_lower = task_description.lower()
    template_name = template.get("name", "").lower()
    
    # Action word match: +0.1
    for action in ["create", "get", "update", "delete", "list", "confirm", "cancel"]:
        if action in task_lower and action in template_name:
            bonus += 0.1
            break
    
    # Resource word match: +0.1
    for resource in ["payment", "checkout", "session", "customer", "subscription"]:
        if resource in task_lower and resource in template_name:
            bonus += 0.1
            break
    
    return min(0.2, bonus)
```

---

## Alternatives Considered

### Option A: Pure Embedding Search

**Approach**: Compute query embedding, perform ANN search over template embeddings, return top-k.

| Pros | Cons |
|------|------|
| Simple implementation | No structural awareness—ignores KG topology |
| Fast with vector DB (FAISS, pgvector) | "Similar words" ≠ "correct template" |
| Works without graph population | Requires all templates to have embeddings |

**Verdict**: Rejected. Semantic similarity alone produces false positives when templates have similar descriptions but different structural requirements.

### Option B: Pure Graph Traversal

**Approach**: BFS/DFS from known entities/endpoints, return templates within N hops.

| Pros | Cons |
|------|------|
| Topologically precise | No semantic understanding ("payment" ≠ "checkout") |
| Fully explainable paths | Cold-start problem: empty graph = no results |
| No embedding costs | Brittle to naming variations |

**Verdict**: Rejected. Graph-only retrieval fails when user phrasing differs from template naming conventions.

### Option C: LLM-Based Selection

**Approach**: Present candidate templates to LLM, ask it to rank or select the best match.

| Pros | Cons |
|------|------|
| Excellent semantic understanding | Latency: 500ms–2s per query |
| Handles nuance and context | Cost: $0.01–$0.10 per selection |
| No pre-computation required | Non-deterministic outputs |
| | Black-box reasoning |

**Verdict**: Rejected. Latency and cost are prohibitive for a retrieval step that runs on every request. Reserve LLM for generation, not retrieval.

### Option D: Learned-to-Rank (LTR)

**Approach**: Train an ML model on (query, template, relevance_label) triples to predict scores.

| Pros | Cons |
|------|------|
| Optimal weighting learned from data | Requires labeled training data |
| Adapts to domain-specific signals | Complex training pipeline |
| Industry-proven (Google, Bing) | Cold-start: no data = no model |

**Verdict**: Deferred. Excellent for v2 when usage data accumulates. Premature for initial deployment.

### Option E: Hybrid GraphRAG (Selected)

**Approach**: Graph filter → embedding rank → exact-match boost.

| Pros | Cons |
|------|------|
| Balances structure + semantics | Fixed weights (40/40/20) not learned |
| Graceful degradation (0.5 fallback) | Embedding cost per query |
| Explainable component scores | Entity matching is lexical, not semantic |
| Scales to ~10K templates | Requires graph + embeddings |

**Verdict**: Selected. Best balance of accuracy, explainability, and operational complexity for current scale.

---

## Consequences

### Positive

1. **Balanced Retrieval**: Templates must be both structurally connected AND semantically relevant.

2. **Graceful Degradation**: Mock LLM mode returns `embedding_score=0.5`, allowing development/testing without API keys.

3. **Explainable Scores**: Debugging output shows:
   ```
   template=create_checkout (graph=0.48, emb=0.82, exact=0.2) → final=0.72
   ```

4. **Graph-First Efficiency**: SQL filtering eliminates 95%+ of templates before expensive scoring.

5. **Tuneable**: Weights can be adjusted per domain without code changes (future: config file).

### Negative

1. **Weight Sensitivity**: The 40/40/20 split is empirically chosen. Different providers may benefit from different ratios.

2. **Entity Matching Limitation**: Current implementation uses string containment:
   ```python
   if entity.lower() in template_text  # "user" won't match "customer"
   ```
   Semantic entity matching deferred to v2.

3. **Cold-Start Degradation**: First run has sparse graph → `graph_score ≈ 0.3` for all templates. System improves as `persist_kg_learning` populates edges.

4. **Embedding Dependency**: Real differentiation requires API calls. Mock mode provides uniform 0.5 scores, reducing ranking quality.

---

## Performance Characteristics

| Metric | Current | Target | Notes |
|--------|---------|--------|-------|
| Templates scanned | 10–20 | <50 | Graph filter limits candidates |
| Embedding calls | 1 (query) + N (missing templates) | 1 | Pre-embed templates at persist time |
| Latency (with embeddings) | 100–300ms | <200ms | Dominated by embedding API |
| Latency (mock mode) | 10–50ms | <50ms | No API calls |
| Accuracy (subjective) | ~85% correct top-1 | >90% | Based on test suite coverage |

---

## Scalability Ceiling

The current approach scales to approximately **10,000 templates** with acceptable latency. Beyond that:

| Bottleneck | Symptom | Mitigation |
|------------|---------|------------|
| Linear embedding scan | >500ms latency | Add pgvector ANN index, use HNSW |
| Edge count queries | N+1 DB calls | Batch lookups or denormalize counts |
| Template text matching | CPU-bound | Pre-compute entity→template inverted index |

### Recommended Upgrade Path (if needed)

1. **Phase 1**: Pre-compute template embeddings at `persist_kg_learning` time
2. **Phase 2**: Add `CREATE INDEX ON kg.nodes USING hnsw (embedding vector_cosine_ops)`
3. **Phase 3**: Materialize `graph_score` as column on template nodes (recompute on edge changes)
4. **Phase 4**: Consider Learned-to-Rank when usage telemetry provides training signal

---

## Implementation References

| Component | File | Key Function |
|-----------|------|--------------|
| Combined scoring | `graph/nodes/align_task_with_kg.py` | `_compute_combined_score()` |
| Graph score | `graph/nodes/align_task_with_kg.py` | `_compute_graph_score()` |
| Embedding score | `graph/nodes/align_task_with_kg.py` | `_compute_embedding_score()` |
| Exact match bonus | `graph/nodes/align_task_with_kg.py` | `_compute_exact_match_bonus()` |
| KG GraphRAG query | `kg/__init__.py` | `query_kg_templates()` |
| Semantic search | `retrieval/semantic_search.py` | `compute_embedding()`, `cosine_similarity()` |

---

## Test Coverage

| Test File | Coverage |
|-----------|----------|
| `test_align_task_with_kg.py` | Template matching with legacy fallback |
| `test_graphrag_integration.py` | End-to-end KG population and retrieval |
| `test_cross_provider_patterns.py` | Pattern-level fallback scoring |

Run with:
```bash
USE_SQLITE=true USE_MOCK_LLM=true python -m pytest tests/test_graphrag_integration.py -v
```

## v1 Constraints

The following limitations apply to the current scoring implementation.

### Embedding Fallback Score

When embeddings are unavailable (mock LLM mode, API failure), all templates receive a fixed score.

```python
if task_emb and template_emb:
    return max(0.0, cosine_similarity(task_emb, template_emb))
return 0.5  # Fallback when embeddings unavailable
```

| Scenario | Behavior | Impact |
|----------|----------|--------|
| Mock LLM mode | All templates score 0.5 | Random selection among candidates |
| API timeout | Single template scores 0.5 | Falls back to graph score + exact match |
| Missing template embedding | That template scores 0.5 | May rank lower than it should |

**v2 Target**: Cache embeddings at persist time. Retry with exponential backoff. Surface embedding failures in metrics.

### Fixed Weight Distribution

The 40/40/20 weight split is hardcoded.

```python
final_score = (graph_score × 0.4) + (embedding_score × 0.4) + exact_match_bonus + base_score
```

Different API types may benefit from different weights:

| API Type | Better Weight Profile | Rationale |
|----------|----------------------|-----------|
| Well-structured OpenAPI | Higher graph weight | Rich entity/endpoint metadata |
| Sparse documentation | Higher embedding weight | Names may differ from concepts |
| Keyword-heavy domains | Higher exact match | "checkout", "payment" are precise |

**v2 Target**: Make weights configurable per provider. Consider learned-to-rank when usage data accumulates.

### Legacy Template Code Paths

Three code paths exist for template retrieval:

| Path | Trigger | Status |
|------|---------|--------|
| DB Knowledge Graph | Default | Primary |
| In-memory KG fallback | `USE_IN_MEMORY_KG_FALLBACK=1` | Deprecated |
| Legacy templates dict | `USE_LEGACY_TEMPLATES=1` | Deprecated |

```python
# Fallback hierarchy:
# 1. DB KG (primary)
# 2. Legacy in-memory templates (if USE_LEGACY_TEMPLATES=1)
# 3. In-memory KG fallback (if USE_IN_MEMORY_KG_FALLBACK=1)
```

**v2 Target**: Remove legacy paths once DB KG is stable. Maintain single fallback for cold-start scenarios.

---

## Open Questions

1. **Should weights be configurable per provider?**
   Some APIs may benefit from higher graph weight (well-structured OpenAPI) vs. higher embedding weight (sparse docs).

2. **Should entity matching be semantic?**
   Embedding `entity_name` and comparing to template embeddings would catch "user"↔"customer" equivalence.

3. **Should we log retrieval metrics for LTR training?**
   Capturing (query, selected_template, user_feedback) enables future model training.

---

## Decision Outcome

**Accepted**. The Hybrid GraphRAG approach provides the best balance of:
- Retrieval accuracy (structure + semantics + precision)
- Operational simplicity (no ML training pipeline)
- Graceful degradation (works in mock mode)
- Explainability (decomposed scores for debugging)

The 40/40/20 weight distribution is documented as a tunable parameter. Upgrade paths to ANN indexing and learned ranking are identified for future scale.

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-12-01 | Integration Coworker Team | Initial decision |
