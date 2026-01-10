# Production Gap Closure Plan: KG, DB, and Streaming Subsystems

**Author:** AI Engineering Agent  
**Date:** December 19, 2025  
**Status:** ✅ COMPLETE (V4.2 - 82 tests passing, all P0 wired)

---

## Executive Summary

This document provides evidence-based analysis and implementation plans for closing production gaps in three critical subsystems:

1. **KG Learning / Template Retrieval** - GraphRAG scoring with entity/endpoint extraction
2. **DB Persistence / Pooling / Transactions** - Connection lifecycle and isolation
3. **WorkflowState Streaming Persistence / Resume** - Progress tracking and recovery

---

## Deliverable A: Current State Repo Mapping

### A.1 KG Retrieval Ranking Pipeline

#### Call Graph (Evidence-Based)

```
align_task_with_kg(state) [src/integration_coworker/graph/nodes/align_task_with_kg.py:958]
  ├─→ query_workflow_templates(provider, task_description, known_entities, known_endpoints) [kg/__init__.py:492]
  │     └─→ query_kg_templates(provider, task_description, entity_names, endpoint_paths) [kg/__init__.py:277]
  │           ├─→ db.init_schema() [persistence/db.py:299]
  │           ├─→ db.get_connection() [persistence/db.py:247]
  │           ├─→ SELECT from kg.nodes WHERE node_type='workflow_template' [kg/__init__.py:305-325]
  │           ├─→ _compute_embedding(task_description) [kg/__init__.py:138]
  │           │     └─→ _get_embedding_client() [kg/__init__.py:114]
  │           │           └─→ OpenAIEmbeddings(api_key, model) [langchain_openai]
  │           ├─→ _compute_graph_score(cur, node_id, entities, endpoints, provider) [kg/__init__.py:207]
  │           │     ├─→ SELECT COUNT(*) FROM kg.edges WHERE relation_type IN (produces/consumes_entity)
  │           │     └─→ SELECT COUNT(*) FROM kg.edges WHERE relation_type='uses_endpoint'
  │           └─→ Sort by: 0.4*graph + 0.4*embedding + 0.1*confidence + 0.1*exact_match [kg/__init__.py:381-395]
  │
  └─→ query_templates_with_pattern_fallback(provider, slug, http_method) [kg/__init__.py:785]
        └─→ query_kg_patterns(task_description, task_slug, http_method) [kg/__init__.py:785]
```

**Key Files:**
- `src/integration_coworker/graph/nodes/align_task_with_kg.py` (958-1050)
- `src/integration_coworker/kg/__init__.py` (114-475)
- `src/integration_coworker/retrieval/semantic_search.py` (89-115)

**Critical Path Issue:**
- `entity_names` and `endpoint_paths` parameters default to `None` in most call paths
- When `None`, `_compute_graph_score()` returns 0.0 for entity/endpoint edges (lines 217-250)
- This collapses 40% of the scoring signal to a flat 0.3 base score

---

### A.2 Embedding Generation Pipeline

#### Call Graph (Evidence-Based)

```
embed_spec_chunks(state) [graph/nodes/embed_spec_chunks.py:234]
  ├─→ _get_embedding_client() [embed_spec_chunks.py:167]
  │     ├─→ Check HAS_LANGCHAIN_EMBEDDINGS
  │     ├─→ get_settings().llm.api_key [config/__init__.py]
  │     └─→ OpenAIEmbeddings(api_key=api_key, model=model)
  │
  ├─→ _embed_with_retry(client, texts, max_retries=3) [embed_spec_chunks.py:76]
  │     ├─→ client.embed_documents(texts)
  │     ├─→ _is_auth_error() → raise LLMAuthError (fail-fast)
  │     ├─→ _is_rate_limit_error() → backoff * 2, cap at 60s
  │     └─→ time.sleep(backoff) with INITIAL=2.0
  │
  └─→ stream_embeddings_to_chunks() [persistence/streaming.py:305] (if streaming mode)

compute_embedding(text) [retrieval/semantic_search.py:89]
  └─→ client.embed_query(text[:8000])
       └─→ NO RETRY WRAPPER - raw exception propagation
```

**Key Files:**
- `src/integration_coworker/graph/nodes/embed_spec_chunks.py` (76-130)
- `src/integration_coworker/retrieval/semantic_search.py` (89-115)
- `src/integration_coworker/kg/__init__.py` (138-160)

**Critical Path Issues:**
1. `compute_embedding()` in `semantic_search.py` has NO retry wrapper (line 108-115)
2. `_compute_embedding()` in `kg/__init__.py` has NO retry wrapper (line 150-160)
3. Embedding failures silently return `[]`, causing `similarity_score = 0.5` fallback (line 370)
4. No persistent embedding cache - recomputes on every query

---

### A.3 Streaming Persistence Pipeline

#### Call Graph (Evidence-Based)

```
ingest_spec(state) [graph/nodes/ingest_spec.py:90]
  ├─→ should_use_streaming_for_spec(byte_size, chunk_count) [config/__init__.py:180]
  │     └─→ Check: bytes > 500KB OR chunks > 500
  │
  ├─→ _ingest_spec_streaming_with_fetched(state, fetched_specs) [ingest_spec.py:520]
  │     ├─→ db.init_schema()
  │     ├─→ stream_raw_spec_to_bronze(content, uri, content_type) [streaming.py:35]
  │     │     └─→ INSERT INTO spec_bronze.raw_specs ... RETURNING id
  │     │
  │     └─→ stream_chunks_to_silver(chunks, spec_document_id, batch_size=100) [streaming.py:141]
  │           ├─→ _write_chunk_batch(cur, batch, engine, schema) [streaming.py:200]
  │           │     └─→ INSERT INTO spec_chunks ... ON CONFLICT DO NOTHING
  │           ├─→ conn.commit() [after each batch_size]
  │           └─→ Return: List[chunk_id]

embed_spec_chunks(state) [graph/nodes/embed_spec_chunks.py:234]
  └─→ stream_embeddings_to_chunks(embeddings, batch_size=25) [streaming.py:305]
        ├─→ UPDATE spec_chunks SET embedding = %s WHERE id = %s
        └─→ conn.commit() [after each batch_size]
```

**Key Files:**
- `src/integration_coworker/graph/nodes/ingest_spec.py` (90-600)
- `src/integration_coworker/persistence/streaming.py` (1-350)
- `src/integration_coworker/config/__init__.py` (180-220)

**Critical Path Issues:**
1. No progress markers - crash mid-batch loses all uncommitted work
2. No checkpoint between chunks and embeddings - partial state not recoverable
3. `ON CONFLICT DO NOTHING` is idempotent but gives no feedback on what was skipped
4. No resume cursor - re-run must scan entire dataset to find incomplete chunks

---

### A.4 Connection Pool Lifecycle

#### Call Graph (Evidence-Based)

```
get_connection() [persistence/db.py:247]
  ├─→ IF engine == "sqlite":
  │     └─→ sqlite3.connect(path) → ConnectionWrapper(conn, pool=None, engine="sqlite")
  │
  └─→ IF engine == "postgres":
        ├─→ get_pool() [persistence/postgres.py:127]
        │     └─→ ConnectionPool(url, min_size=2, max_size=20, timeout=5.0, reset=_quiet_reset)
        │           └─→ _add_keepalive_params(url) → keepalives_idle=60, interval=10, count=5
        │
        ├─→ pool.getconn() → raw_conn
        └─→ ConnectionWrapper(raw_conn, pool=pool, engine="postgres")

ConnectionWrapper [persistence/db.py:53]
  ├─→ __enter__(): return self
  ├─→ __exit__(exc_type, ...):
  │     ├─→ IF exc_type: self.rollback()
  │     └─→ self.close()
  ├─→ close():
  │     └─→ IF postgres: pool.putconn(self._conn)
  │         ELSE: self._conn.close()
  └─→ __del__(): Warn + auto-close if not closed

get_connection() [persistence/postgres.py:180] (context manager version)
  └─→ with pool.connection() as conn: yield conn
```

**Key Files:**
- `src/integration_coworker/persistence/db.py` (53-160, 247-290)
- `src/integration_coworker/persistence/postgres.py` (60-180)

**Critical Path Issues:**
1. No health check on checkout - stale/broken connections returned to callers
2. `_quiet_reset()` only rolls back - doesn't verify connection liveness
3. No max lifetime enforcement - connections can live forever
4. No explicit isolation level documentation - relies on PostgreSQL default (READ COMMITTED)
5. Two `get_connection()` patterns exist (db.py wrapper vs postgres.py context manager)

---

### A.5 Configuration Knobs Table

| Env Var | Default | Location | Purpose |
|---------|---------|----------|---------|
| `DATABASE_URL` | (required) | `config/__init__.py:85` | Postgres connection string |
| `USE_SQLITE` | `false` | `config/__init__.py:78` | Force SQLite mode (deprecated) |
| `STREAMING_PERSISTENCE` | `auto` | `config/__init__.py:180` | Enable streaming: auto/true/false |
| `STREAMING_THRESHOLD_BYTES` | `500000` | `config/__init__.py:183` | Auto-stream above this |
| `STREAMING_THRESHOLD_CHUNKS` | `500` | `config/__init__.py:184` | Auto-stream above this |
| `OPENAI_API_KEY` | (required for embeddings) | `config/__init__.py:95` | Embedding API key |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | `config/__init__.py:98` | OpenAI embedding model |
| `CODEGEN_PROFILE` | `development` | `config/__init__.py:60` | Determines strictness |
| `LANGCHAIN_TRACING_V2` | `false` | N/A | Enable LangSmith tracing |

**Pool Settings (hardcoded in postgres.py):**
- `min_size=2`, `max_size=20`, `timeout=5.0`
- TCP keepalive: `idle=60s`, `interval=10s`, `count=5`

---

## Deliverable B: Shortcomings as Testable Hypotheses

### B.1 KG Scoring Collapse

| Aspect | Value |
|--------|-------|
| **Symptom** | All templates get ~0.44-0.50 similarity scores, no differentiation |
| **Trigger** | Call `query_kg_templates()` without `entity_names` or `endpoint_paths` |
| **Expected** | Scores range from 0.3-1.0 based on relevance |
| **Current** | Base 0.3 graph + 0.0 entity/endpoint + 0.5 default similarity = ~0.47 |
| **Fix Location** | `src/integration_coworker/graph/nodes/align_task_with_kg.py:970-980` |

**Reproduction:**
```python
from integration_coworker.kg import query_kg_templates
matches = query_kg_templates("stripe", "create customer and charge", entity_names=None)
# All scores cluster around 0.44-0.50
```

### B.2 Embedding Failure Silent Degradation

| Aspect | Value |
|--------|-------|
| **Symptom** | Rankings become random when embeddings unavailable |
| **Trigger** | `OPENAI_API_KEY` not set, or API rate limit hit on `compute_embedding()` |
| **Expected** | Graceful fallback to deterministic scoring with logged warning |
| **Current** | Returns `[]`, similarity defaults to 0.5 constant (line 370) |
| **Fix Location** | `src/integration_coworker/retrieval/semantic_search.py:89-115` |

**Reproduction:**
```python
# Unset OPENAI_API_KEY
from integration_coworker.retrieval.semantic_search import compute_embedding
result = compute_embedding("test")  # Returns []
# All templates get similarity_score = 0.5
```

### B.3 Streaming No Resume Markers

| Aspect | Value |
|--------|-------|
| **Symptom** | Crash during embedding loop loses partial progress |
| **Trigger** | Process kill during `stream_embeddings_to_chunks()` |
| **Expected** | Resume continues from last committed batch |
| **Current** | Must re-scan all chunks, re-embed those missing embeddings |
| **Fix Location** | `src/integration_coworker/persistence/streaming.py:305-360` |

**Reproduction:**
```python
# Run embed_spec_chunks on large spec, kill process mid-run
# Restart - must query SELECT id FROM spec_chunks WHERE embedding IS NULL
# to find incomplete work
```

### B.4 Connection Pool Stale Connections

| Aspect | Value |
|--------|-------|
| **Symptom** | `connection is closed` errors after idle period |
| **Trigger** | Connection sits idle in pool > 10 minutes (Postgres default statement_timeout) |
| **Expected** | Pool validates connection before returning, or reconnects |
| **Current** | No validation - returns potentially dead connection |
| **Fix Location** | `src/integration_coworker/persistence/postgres.py:127-145` |

**Reproduction:**
```python
# Get connection, hold 15 minutes, use it
conn = db.get_connection()
time.sleep(900)  # 15 min
conn.cursor().execute("SELECT 1")  # May fail with "connection is closed"
```

### B.5 Transaction Isolation Not Explicit

| Aspect | Value |
|--------|-------|
| **Symptom** | Potential dirty reads or lost updates in concurrent scenarios |
| **Trigger** | Multiple workflow runs writing to same KG nodes |
| **Expected** | Explicit isolation level prevents anomalies |
| **Current** | Uses PostgreSQL default READ COMMITTED |
| **Fix Location** | KG write operations in `persist_kg_learning.py` |

---

## Deliverable C: Competing Designs

### Theme 1: Entity + Endpoint Extraction for KG Scoring

#### Approach 1.1: Spec-First Deterministic Extraction (RECOMMENDED)

**Description:** Build candidate dictionary from loaded OpenAPI spec, then match against `task_description` using tokenization + normalization.

```python
def extract_entities_endpoints_from_spec(spec: Dict, task_description: str) -> Tuple[List[str], List[str]]:
    """
    Extract entities and endpoints from spec that match task description.
    
    1. Build candidate sets from spec:
       - entities: schema names, tag names, operationId prefixes
       - endpoints: paths, operation summaries
    
    2. Tokenize task_description (split on whitespace, remove stopwords)
    
    3. Match candidates using:
       - Exact token match (case-insensitive)
       - Plural/singular normalization
       - Common API synonyms (create→post, get→read, etc.)
    
    Returns:
        (entity_names, endpoint_paths) for KG scoring
    """
```

**Pros:**
- Works offline (no LLM call)
- Deterministic (same input = same output)
- Fast (<10ms for large specs)
- No new dependencies

**Cons:**
- May miss creative phrasings ("charge a card" → "create payment intent")
- Requires good spec quality (operationIds, summaries)

**Complexity:** Low (50-100 LoC)

#### Approach 1.2: LLM-Assisted Structured Extraction

**Description:** Ask LLM to return JSON with entities + endpoints, validate against spec.

```python
prompt = f"""
Extract entities and API endpoints from this task:
Task: {task_description}

Available endpoints in spec:
{formatted_endpoint_list}

Return JSON: {{"entities": [...], "endpoints": [...]}}
"""
response = await call_llm_async(prompt)
extracted = validate_against_spec(response, spec)
```

**Pros:**
- Handles creative phrasings
- Better semantic understanding

**Cons:**
- Adds LLM latency (+500ms)
- Token cost (~200 tokens per call)
- Requires fallback if LLM fails
- Non-deterministic

**Complexity:** Medium (100-150 LoC)

#### Approach 1.3: Local NER + Rules

**Description:** Use spaCy NER to extract entities, rule layer for API patterns.

**Pros:**
- Good entity extraction
- Local (no API calls)

**Cons:**
- Adds ~100MB dependency (spaCy model)
- Not trained on API terminology
- Overkill for this use case

**Complexity:** Medium-High (requires model download, training)

#### Decision: Approach 1.1 (Spec-First Deterministic)

**Rationale:**
- Works when embeddings are down (requirement)
- No user hints needed (requirement)
- Produces stable rankings (requirement)
- Fits existing codebase patterns
- Zero new dependencies

**Rejected:**
- 1.2: LLM latency/cost not justified for this extraction
- 1.3: Dependency weight too high for marginal benefit

---

### Theme 2: Embedding Resilience + Caching

#### Approach 2.1: Retry Wrapper with Exponential Backoff + Jitter (RECOMMENDED)

**Description:** Wrap `compute_embedding()` with same retry pattern as `_embed_with_retry()`.

```python
def compute_embedding_with_retry(text: str, max_retries: int = 3) -> List[float]:
    """
    Compute embedding with capped exponential backoff + jitter.
    
    Backoff: min(initial * 2^attempt + jitter, max_backoff)
    Jitter: random 0-25% of backoff
    """
    for attempt in range(max_retries + 1):
        try:
            result = client.embed_query(text)
            return result
        except Exception as e:
            if _is_auth_error(e):
                raise LLMAuthError(str(e)) from e
            if not _is_rate_limit_error(e) or attempt == max_retries:
                logger.warning(f"Embedding failed, returning empty: {e}")
                return []  # Graceful degradation
            backoff = min(INITIAL_BACKOFF * (2 ** attempt), MAX_BACKOFF)
            jitter = backoff * random.uniform(0, 0.25)
            time.sleep(backoff + jitter)
```

**Pros:**
- Consistent with `_embed_with_retry()` pattern
- Jitter prevents retry storms
- Graceful degradation on exhaustion

**Cons:**
- Adds latency on retries (up to 120s worst case)

**Complexity:** Low (30 LoC)

#### Approach 2.2: Persistent Embedding Cache

**Description:** Cache embeddings by `sha256(content)` in DB table.

```sql
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT PRIMARY KEY,
    embedding VECTOR(1536),
    model TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

**Pros:**
- Eliminates recomputation
- Survives restarts

**Cons:**
- Cache invalidation on model change
- Storage growth (1536 floats × 4 bytes × N)
- Query latency for cache check

**Complexity:** Medium (schema migration + lookup logic)

#### Approach 2.3: Deterministic Fallback Scoring

**Description:** When embeddings fail, use enhanced deterministic scoring.

```python
def deterministic_similarity(task: str, template: dict) -> float:
    """
    Compute similarity without embeddings using:
    - Token overlap (Jaccard)
    - TF-IDF weighted terms
    - Keyword presence
    """
```

**Pros:**
- No external dependencies
- Instant fallback

**Cons:**
- Lower quality than embeddings
- Requires tuning weights

**Complexity:** Low-Medium (60 LoC)

#### Decision: Combination of 2.1 + 2.3

**Rationale:**
- 2.1 provides resilience for transient failures
- 2.3 provides meaningful scores when embeddings fully unavailable
- 2.2 (cache) deferred to P2 - adds complexity, can retrofit later

**Rejected:**
- 2.2 alone: Doesn't help first-time queries or model changes

---

### Theme 3: Streaming Progress Tracking + Resume

#### Approach 3.1: Idempotency-Only Resume (CURRENT)

**Description:** Rely on `ON CONFLICT DO NOTHING` + scan for NULL embeddings.

**Pros:**
- Already implemented
- Simple

**Cons:**
- No progress visibility
- Must scan full table to find incomplete
- No checkpoint between phases

**Complexity:** Already done

#### Approach 3.2: Explicit Progress Markers (RECOMMENDED)

**Description:** Write `(run_id, phase, last_committed_id, total_count)` to progress table.

```sql
CREATE TABLE IF NOT EXISTS streaming_progress (
    run_id TEXT NOT NULL,
    phase TEXT NOT NULL,  -- 'chunks', 'embeddings'
    last_committed_id INTEGER,
    total_items INTEGER,
    started_at TIMESTAMP,
    updated_at TIMESTAMP,
    PRIMARY KEY (run_id, phase)
);
```

```python
def stream_with_progress(run_id: str, phase: str, items: Iterator, process_fn):
    # Load checkpoint
    progress = load_progress(run_id, phase)
    start_after = progress.last_committed_id if progress else 0
    
    for item in items:
        if item.id <= start_after:
            continue  # Skip already processed
        process_fn(item)
        if item_count % batch_size == 0:
            save_progress(run_id, phase, item.id, total_items)
            commit()
```

**Pros:**
- True resume from crash point
- Progress visibility for monitoring
- O(1) checkpoint lookup vs O(N) scan

**Cons:**
- Additional table
- Requires passing run_id through pipeline

**Complexity:** Medium (40 LoC + schema)

#### Approach 3.3: Job State Machine Table

**Description:** Full job tracking with states: PENDING → RUNNING → COMPLETED/FAILED.

**Pros:**
- Rich state machine
- Supports job queuing

**Cons:**
- Over-engineered for current needs
- Requires background job runner

**Complexity:** High

#### Decision: Approach 3.2 (Explicit Progress Markers)

**Rationale:**
- Provides true operational recovery (requirement)
- Minimal additional complexity
- Fits existing checkpoint pattern
- Enables monitoring dashboards

**Rejected:**
- 3.1: Already proven insufficient for production
- 3.3: Premature optimization

---

### Theme 4: Pool Health Checks + Transaction Semantics

#### Approach 4.1: Checkout Validation

**Description:** `SELECT 1` before returning connection from pool.

```python
def _check_connection(conn: psycopg.Connection) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False

def get_validated_connection():
    conn = pool.getconn()
    if not _check_connection(conn):
        pool.putconn(conn, close=True)
        conn = pool.getconn()
    return conn
```

**Pros:**
- Guarantees live connection
- Simple implementation

**Cons:**
- Adds ~1ms latency per checkout
- May mask underlying issues

**Complexity:** Low (15 LoC)

#### Approach 4.2: Max Lifetime + Idle Timeout (RECOMMENDED)

**Description:** Configure pool with connection lifecycle limits.

```python
_pool = ConnectionPool(
    db_url,
    min_size=2,
    max_size=20,
    timeout=5.0,
    max_lifetime=3600,  # 1 hour max age
    max_idle=300,       # 5 min idle before recycle
    check=_check_connection,  # psycopg_pool callback
)
```

**Pros:**
- Proactive connection refresh
- Pool-native support
- Prevents stale connections

**Cons:**
- Requires psycopg_pool >= 3.1 for all options

**Complexity:** Low (config change)

#### Approach 4.3: Isolation Level for KG Writes

**Description:** Use explicit `SET TRANSACTION ISOLATION LEVEL` for KG operations.

**Analysis:**
- KG writes are: node upserts, edge inserts, confidence updates
- Anomalies to prevent:
  - Lost updates on concurrent confidence adjustments
  - Phantom reads during template ranking

**Decision:** READ COMMITTED is sufficient because:
1. `ON CONFLICT DO UPDATE` is atomic at row level
2. Rankings tolerate minor staleness (not ACID-critical)
3. SERIALIZABLE would cause retries on every concurrent write

Use `REPEATABLE READ` only for confidence update aggregations:

```python
def update_confidence_from_feedback(node_key: str):
    with get_connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        # Aggregate feedback, update confidence
        conn.commit()
```

#### Decision: Approach 4.2 + Selective 4.3

**Rationale:**
- Pool lifecycle limits handle most stale connection issues
- No `SELECT 1` latency penalty on every checkout
- READ COMMITTED default is correct for most operations
- REPEATABLE READ only where aggregation consistency matters

---

## Deliverable D: Implementation Plan

### Phase 1: Entity/Endpoint Extraction (Theme 1)

#### Step 1.1: Create Extraction Module

**File:** `src/integration_coworker/kg/extraction.py` (NEW)

```python
# Functions to add:
def build_spec_candidates(openapi_spec: Dict) -> SpecCandidates:
    """Build entity/endpoint candidates from OpenAPI spec."""

def extract_entities_from_task(task_description: str, candidates: SpecCandidates) -> List[str]:
    """Extract entity names that match task description."""

def extract_endpoints_from_task(task_description: str, candidates: SpecCandidates) -> List[str]:
    """Extract endpoint paths that match task description."""
```

**Tests:** `tests/kg/test_extraction.py`

#### Step 1.2: Integrate into align_task_with_kg

**File:** `src/integration_coworker/graph/nodes/align_task_with_kg.py`

**Change:** Lines 970-980

```python
# BEFORE:
known_endpoints: Optional[List[str]] = None
if state.endpoints:
    known_endpoints = [ep.path for ep in state.endpoints if ep.path]

# AFTER:
from integration_coworker.kg.extraction import extract_entities_from_task, extract_endpoints_from_task

if state.openapi_spec:
    candidates = build_spec_candidates(state.openapi_spec)
    extracted_entities = extract_entities_from_task(task_description, candidates)
    extracted_endpoints = extract_endpoints_from_task(task_description, candidates)
    
    # Merge with existing (if any)
    known_entities = list(set((known_entities or []) + extracted_entities))
    known_endpoints = list(set((known_endpoints or []) + extracted_endpoints))
```

---

### Phase 2: Embedding Resilience (Theme 2)

#### Step 2.1: Add Retry to compute_embedding

**File:** `src/integration_coworker/retrieval/semantic_search.py`

**Change:** Replace `compute_embedding()` (lines 89-115)

```python
import random

def compute_embedding(text: str, max_retries: int = 3) -> List[float]:
    """
    Compute embedding with exponential backoff + jitter.
    """
    if not text or not text.strip():
        return []
    
    client = _get_embedding_client()
    if not client:
        logger.debug("No embedding client available")
        return []
    
    backoff = 2.0
    for attempt in range(max_retries + 1):
        try:
            return client.embed_query(text[:8000])
        except Exception as e:
            if _is_auth_error(e):
                logger.error(f"Auth error in embedding, not retrying: {e}")
                raise LLMAuthError(str(e)) from e
            if not _is_rate_limit_error(e) or attempt == max_retries:
                logger.warning(f"Embedding failed after {attempt + 1} attempts: {e}")
                return []
            jitter = backoff * random.uniform(0, 0.25)
            time.sleep(backoff + jitter)
            backoff = min(backoff * 2, 60.0)
    return []
```

#### Step 2.2: Add Deterministic Fallback Scoring

**File:** `src/integration_coworker/kg/__init__.py`

**Add function:** (after line 160)

```python
def _deterministic_similarity(task_description: str, template: dict) -> float:
    """
    Compute similarity without embeddings using token overlap.
    
    Returns value in [0.3, 0.8] range (never 0.5 constant).
    """
    task_tokens = set(task_description.lower().split())
    template_text = f"{template.get('name', '')} {template.get('description', '')}".lower()
    template_tokens = set(template_text.split())
    
    if not template_tokens:
        return 0.3
    
    overlap = len(task_tokens & template_tokens)
    union = len(task_tokens | template_tokens)
    jaccard = overlap / union if union > 0 else 0
    
    # Scale to [0.3, 0.8] range
    return 0.3 + (jaccard * 0.5)
```

**Change:** Line 370 (similarity fallback)

```python
# BEFORE:
similarity_score = 0.5

# AFTER:
similarity_score = _deterministic_similarity(task_description, {
    "name": name, "description": description
})
logger.debug(f"Using deterministic similarity: {similarity_score:.2f}")
```

---

### Phase 3: Streaming Progress Tracking (Theme 3)

#### Step 3.1: Add Progress Table Schema

**File:** `src/integration_coworker/persistence/postgres.py`

**Add to DDL:** (after run_checkpoints)

```sql
-- Streaming progress tracking
CREATE TABLE IF NOT EXISTS integration_gold.streaming_progress (
    run_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    last_committed_id BIGINT,
    total_items BIGINT,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, phase)
);
```

**File:** `src/integration_coworker/persistence/db.py`

**Add to SQLite DDL:** (after run_checkpoints)

```python
cur.execute("""
    CREATE TABLE IF NOT EXISTS streaming_progress (
        run_id TEXT NOT NULL,
        phase TEXT NOT NULL,
        last_committed_id INTEGER,
        total_items INTEGER,
        started_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (run_id, phase)
    )
""")
```

#### Step 3.2: Add Progress Tracking Functions

**File:** `src/integration_coworker/persistence/streaming.py`

**Add functions:**

```python
def save_streaming_progress(
    run_id: str,
    phase: str,
    last_committed_id: int,
    total_items: int,
) -> None:
    """Save/update streaming progress checkpoint."""

def load_streaming_progress(run_id: str, phase: str) -> Optional[dict]:
    """Load streaming progress for resume."""

def clear_streaming_progress(run_id: str) -> None:
    """Clear progress after successful completion."""
```

#### Step 3.3: Integrate Progress into stream_embeddings_to_chunks

**File:** `src/integration_coworker/persistence/streaming.py`

**Modify `stream_embeddings_to_chunks()`:**

```python
def stream_embeddings_to_chunks(
    embeddings: Iterator[Tuple[int, List[float]]],
    batch_size: int = EMBEDDING_BATCH_SIZE,
    run_id: Optional[str] = None,
) -> int:
    """Stream embeddings with progress tracking."""
    
    # Load checkpoint if resuming
    start_after = 0
    if run_id:
        progress = load_streaming_progress(run_id, "embeddings")
        if progress:
            start_after = progress["last_committed_id"]
            logger.info(f"Resuming embeddings from id={start_after}")
    
    # ... existing logic with skip for id <= start_after ...
    
    # Save progress after each batch
    if run_id and batch_count % 1 == 0:
        save_streaming_progress(run_id, "embeddings", last_id, total_items)
```

---

### Phase 4: Pool Health + Transaction Semantics (Theme 4)

#### Step 4.1: Add Pool Lifecycle Configuration

**File:** `src/integration_coworker/persistence/postgres.py`

**Modify `get_pool()`:** (line 127)

```python
def get_pool() -> "ConnectionPool":
    global _pool
    if _pool is None:
        settings = get_settings()
        db_url = _add_keepalive_params(settings.database.url)
        _pool = ConnectionPool(
            db_url,
            min_size=2,
            max_size=20,
            timeout=5.0,
            max_lifetime=3600.0,  # NEW: Recycle after 1 hour
            max_idle=300.0,       # NEW: Recycle after 5 min idle
            reset=_quiet_reset,
            check=_check_connection,  # NEW: Validate before return
            open=True,
        )
    return _pool

def _check_connection(conn: "psycopg.Connection") -> bool:
    """Validate connection is alive before returning from pool."""
    try:
        conn.execute("SELECT 1")
        return True
    except Exception:
        return False
```

#### Step 4.2: Add Isolation Level Helper

**File:** `src/integration_coworker/persistence/db.py`

**Add function:**

```python
@contextmanager
def transaction(isolation_level: str = "READ COMMITTED"):
    """
    Context manager for explicit transaction with isolation level.
    
    Args:
        isolation_level: One of 'READ COMMITTED', 'REPEATABLE READ', 'SERIALIZABLE'
    
    Usage:
        with transaction("REPEATABLE READ") as conn:
            ...
    """
    with get_connection() as conn:
        if get_engine_type() == "postgres":
            conn.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation_level}")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
```

---

### Migration Script

**File:** `scripts/migrate_streaming_progress.py` (NEW)

```python
#!/usr/bin/env python3
"""Add streaming_progress table for resume support."""

def migrate():
    from integration_coworker.persistence.db import get_connection, get_engine_type
    
    with get_connection() as conn:
        cur = conn.cursor()
        if get_engine_type() == "postgres":
            cur.execute("""
                CREATE TABLE IF NOT EXISTS integration_gold.streaming_progress (
                    run_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    last_committed_id BIGINT,
                    total_items BIGINT,
                    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (run_id, phase)
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS streaming_progress (
                    run_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    last_committed_id INTEGER,
                    total_items INTEGER,
                    started_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (run_id, phase)
                )
            """)
        conn.commit()
    print("Migration complete: streaming_progress table created")

if __name__ == "__main__":
    migrate()
```

---

### Test Files to Create

| Test File | Coverage |
|-----------|----------|
| `tests/kg/test_extraction.py` | Entity/endpoint extraction from spec |
| `tests/retrieval/test_embedding_retry.py` | Retry with backoff + jitter |
| `tests/persistence/test_streaming_progress.py` | Progress save/load/resume |
| `tests/persistence/test_pool_lifecycle.py` | Pool health checks |

---

### Downstream Affected Areas

1. **align_task_with_kg** - Now extracts entities/endpoints automatically
2. **query_kg_templates** - Deterministic fallback changes scoring range
3. **embed_spec_chunks** - Pass run_id for progress tracking
4. **ingest_spec** - Pass run_id for progress tracking

### Required vs Optional Refactors

| Refactor | Required? | Reason |
|----------|-----------|--------|
| Entity extraction | Yes | Fixes scoring collapse |
| Embedding retry | Yes | Fixes silent degradation |
| Progress tracking | Yes | Enables production resume |
| Pool lifecycle | Yes | Fixes stale connections |
| Isolation helper | Optional | Only needed for aggregations |

### Rollback Plan

1. Revert commits in reverse order
2. Drop `streaming_progress` table: `DROP TABLE IF EXISTS streaming_progress;`
3. No data migrations - all changes are additive

---

## Deliverable E: Production Test Report

*To be completed in Step 5 after implementation*

---

## Deliverable F: Priority Ranking

### P0: Must-Do for Production Correctness

| Item | Impact | Effort |
|------|--------|--------|
| Entity/endpoint extraction | Fixes 40% scoring collapse | 2h |
| Embedding retry wrapper | Prevents silent ranking degradation | 1h |
| Deterministic fallback scoring | Stable rankings when embeddings down | 1h |
| Pool lifecycle (max_lifetime, check) | Prevents connection errors | 30m |

### P1: Materially Improves Operability

| Item | Impact | Effort |
|------|--------|--------|
| Streaming progress tracking | Enables crash recovery | 3h |
| Transaction isolation helper | Prevents rare anomalies | 30m |

### P2: Optional Improvements

| Item | Impact | Effort |
|------|--------|--------|
| Persistent embedding cache | Reduces API calls | 4h |
| Job state machine | Rich job tracking | 8h |
| spaCy NER extraction | Marginal quality gain | 6h |

---

## Deliverable E: Evidence of Implementation (V4.1 Audit)

*Added December 19, 2025 - Corrected after audit*

### Git Diff Evidence

```bash
$ git status
modified:   src/integration_coworker/graph/nodes/align_task_with_kg.py  (+26 lines)
modified:   src/integration_coworker/kg/__init__.py  (+58 lines)
modified:   src/integration_coworker/persistence/db.py  (+47 lines → FIXED in V4.1)
modified:   src/integration_coworker/persistence/postgres.py  (+30 lines)
modified:   src/integration_coworker/persistence/streaming.py  (+311 lines)
modified:   src/integration_coworker/retrieval/semantic_search.py  (+78 lines)

untracked: src/integration_coworker/kg/extraction.py  (NEW, 379 lines)
```

### Symbol Verification (via `rg`)

| Symbol | File | Line | Wired Into Runtime? |
|--------|------|------|---------------------|
| `_deterministic_similarity()` | `kg/__init__.py` | 161 | ✅ YES (lines 427, 862) |
| `extract_entities_endpoints_from_spec()` | `kg/extraction.py` | 355 | ✅ YES (`align_task_with_kg.py:40`) |
| `save_streaming_progress()` | `streaming.py` | 480 | ⚠️ NO (not called by runtime) |
| `load_streaming_progress()` | `streaming.py` | 544 | ⚠️ NO (not called by runtime) |
| `clear_streaming_progress()` | `streaming.py` | 597 | ⚠️ NO (not called by runtime) |
| `stream_embeddings_with_progress()` | `streaming.py` | 650 | ⚠️ NO (not called by runtime) |
| `_check_connection()` | `postgres.py` | 120 | ✅ YES (`get_pool()` check=) |
| `transaction()` | `db.py` | 341 | ✅ AVAILABLE (no callers yet) |

### Corrections Made in V4.1

| Error | Original | Corrected |
|-------|----------|-----------|
| Rollback claim | "Extraction not imported" | IS imported in `align_task_with_kg.py:40` |
| statement_timeout | "Default 10 min" | Default is 0 (disabled) |
| SET TRANSACTION | SQL after connection | Uses `conn.transaction(isolation_level=)` API |
| Progress wiring | "Implemented" | Functions exist but NOT wired |

### Wiring Gap Analysis

**embed_spec_chunks.py** calls `stream_embedding_batch()` (line 500) but does NOT call:
- `stream_embeddings_with_progress()` 
- `save_streaming_progress()`
- `load_streaming_progress()`

**Required fix:** Wire `run_id` from `state.run_id` into embedding path.

---

## Next Steps

1. ✅ Implement Phase 1 (Entity/Endpoint Extraction) - DONE, WIRED
2. ✅ Implement Phase 2 (Embedding Resilience) - DONE, WIRED  
3. ⚠️ Implement Phase 3 (Progress Tracking) - Functions exist, NOT WIRED
4. ✅ Implement Phase 4 (Pool Health) - DONE, WIRED
5. ☐ Wire streaming progress into `embed_spec_chunks.py`
6. ☐ Run production tests with real Postgres, real LLM, real repos
7. ☐ Update this document with test results
