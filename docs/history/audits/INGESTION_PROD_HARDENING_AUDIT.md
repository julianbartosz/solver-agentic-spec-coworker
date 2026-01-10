# API Spec Ingestion Pipeline: Production Hardening Audit

> **Audit Date**: 2025-12-19  
> **Branch**: `copilot/prod-readiness-v4`  
> **Status**: CONDITIONALLY PRODUCTION-READY → TARGET: PRODUCTION-READY

---

## Executive Summary

This audit documents the exact current state of the API spec ingestion pipeline to guide hardening work. All findings are backed by line citations.

**Current Gaps Preventing Full Production Readiness:**
1. ❌ No HTTP retry for remote fetch failures (single attempt only)
2. ❌ Remote specs loaded fully into memory before streaming decision
3. ❌ No cancellation token plumbing (only shutdown signal, not checkable mid-operation)
4. ❌ No checkpointing for chunk streaming phase (only embeddings have resume)
5. ⚠️ Transaction boundaries are per-spec (good), but multi-spec ingestion can leave partial state

---

## Step 1: Evidence Re-Audit (Line Citations)

### 1.1 `src/integration_coworker/graph/nodes/ingest_spec.py`

#### Fetching Logic (lines 35-57)
```python
def _fetch_spec_content(ref: str) -> tuple[str, str]:
    """Fetch content from a spec ref (HTTP URL or local file path)."""
    if ref.startswith("http://") or ref.startswith("https://"):
        response = httpx.get(ref, timeout=30.0, follow_redirects=True)  # LINE 42
        response.raise_for_status()
        content = response.text  # LINE 44 - FULL CONTENT IN MEMORY
        content_type = response.headers.get("content-type", "application/octet-stream")
        return content, content_type
```

**Findings:**
- **No retry logic**: `httpx.get()` called once with 30s timeout (line 42)
- **No streaming**: `response.text` loads entire response into memory (line 44)
- **No size limit**: No max bytes check before or during fetch
- **No content-type validation**: Accepts any content type (line 45)

#### Streaming Decision Point (lines 186-200)
```python
# Estimate chunks (rough: 1 chunk per 1000 chars)
estimated_chunks = total_bytes // 1000

# Decide streaming mode based on spec size
use_streaming = should_use_streaming_for_spec(total_bytes, estimated_chunks)

if use_streaming:
    logger.info(f"Using streaming mode for {total_bytes:,} bytes ({estimated_chunks} estimated chunks)")
    return _ingest_spec_streaming_with_fetched(state, fetched_specs)
else:
    logger.debug(f"Using legacy mode for {total_bytes:,} bytes")
    return _ingest_spec_legacy_with_fetched(state, fetched_specs)
```

**Critical Issue**: Streaming decision happens AFTER `_fetch_spec_content()` loads full content into memory (line 162). The 500KB threshold (default) protects DB writes but NOT memory during fetch.

#### Multi-Spec Loop (lines 160-172)
```python
for ref in state.spec_refs:
    try:
        content, content_type = _fetch_spec_content(ref)  # LINE 162
        fetched_specs.append((ref, content, content_type))
        total_bytes += len(content.encode("utf-8"))
        state.pending_specs.append({"ref": ref, "content": content, "content_type": content_type})
    except Exception as e:
        state.errors.append(f"Failed to fetch spec from {ref}: {str(e)}")

if not fetched_specs:
    state.errors.append("No specs could be fetched")
```

**Findings:**
- Each spec is fetched sequentially (no parallelism)
- Errors are accumulated, not fatal (partial success allowed)
- All fetched specs held in memory simultaneously before streaming decision

#### Transaction Boundaries (streaming mode, lines 385-460)
```python
# In _ingest_spec_streaming_with_fetched():
for ref, content, content_type in fetched_specs:
    try:
        # ... per-spec processing ...
        
        # Insert spec_document (line 414)
        conn = db.get_connection()
        cur = conn.cursor()
        sql = upsert_ignore(...)
        cur.execute(sql, ...)
        conn.commit()  # LINE 420 - COMMIT PER SPEC
        conn.close()
        
        # Stream chunks to database (line 448)
        chunk_ids = stream_chunks_to_silver(chunk_iterator(), spec_document_id)
```

**Findings:**
- Each `spec_document` insert is committed individually (line 420)
- Chunk streaming has its own transaction boundary (inside `stream_chunks_to_silver`)
- A crash between spec_document commit and chunk streaming leaves orphan spec_document

---

### 1.2 `src/integration_coworker/persistence/streaming.py`

#### Chunk Persistence (lines 135-184)
```python
def stream_chunks_to_silver(
    chunks: Iterator[Tuple[int, str]],
    spec_document_id: int,
    batch_size: int = CHUNK_BATCH_SIZE,  # Default: 100
) -> List[int]:
    """Stream content chunks to spec_silver.spec_chunks immediately."""
    # ...
    try:
        for chunk_index, content in chunks:
            batch.append((spec_document_id, chunk_index, content))
            
            if len(batch) >= batch_size:
                ids = _write_chunk_batch(cur, batch, engine, schema)
                chunk_ids.extend(ids)
                batch = []
        
        # Write remaining batch
        if batch:
            ids = _write_chunk_batch(cur, batch, engine, schema)
            chunk_ids.extend(ids)
        
        conn.commit()  # LINE 175 - SINGLE COMMIT AT END
        conn.close()
```

**Findings:**
- Batches of 100 chunks written, but **single commit at end** (line 175)
- No progress checkpoint during chunk streaming
- If crash at 90% through, ALL chunks are lost (no partial commit)
- Idempotency via `ON CONFLICT (spec_document_id, chunk_index) DO NOTHING`

#### Progress Tracking (lines 360-450)
```python
def save_streaming_progress(
    run_id: str,
    phase: str,
    last_committed_id: int,
    total_items: int,
) -> bool:
    """Save/update streaming progress checkpoint."""
    # ... INSERT INTO integration_gold.streaming_progress ...
```

**Findings:**
- Progress table exists: `streaming_progress (run_id, phase, last_committed_id, total_items)`
- Only used by embedding phase (phase="embeddings")
- **Not used by chunk streaming** - no `save_streaming_progress()` call in `stream_chunks_to_silver()`

#### Embedding Batch Updates (lines 260-310)
```python
def stream_embedding_batch(
    updates: List[Tuple[int, List[float]]],
    run_id: Optional[str] = None,
    phase: str = "embeddings",
    total_items: Optional[int] = None,
) -> int:
    """Batch update multiple chunk embeddings with optional progress tracking."""
    # V4: Load checkpoint if run_id provided
    skip_until = 0
    if run_id:
        progress = load_streaming_progress(run_id, phase)
        if progress and progress.get("last_committed_id"):
            skip_until = progress["last_committed_id"]
```

**Findings:**
- Resume support for embeddings via `last_committed_id`
- Skips already-processed chunk IDs
- Clears progress on completion

---

### 1.3 `src/integration_coworker/graph/nodes/embed_spec_chunks.py`

#### Resume Behavior (lines 327-340)
```python
def _embed_spec_chunks_streaming(state: WorkflowState) -> WorkflowState:
    """V3 Streaming embedding mode: lazy load from DB, stream embeddings to DB."""
    # ...
    # V4: Get run_id for progress tracking
    run_id = getattr(state, 'run_id', None) or None
    
    # V4: Get total chunk count for progress tracking
    total_chunks = state.chunk_count if state.chunk_count > 0 else None
```

**Findings:**
- Uses `state.run_id` for progress tracking (if set)
- Calls `stream_embedding_batch()` which handles resume
- Progress cleared on successful completion

#### Skip Logic (lines 380-395)
```python
def _process_embedding_batch(...) -> Tuple[int, int]:
    """Process a batch of chunks: compute embeddings and stream to DB."""
    # ...
    # Stream to DB with progress tracking (V4)
    success_count = stream_embedding_batch(
        updates,
        run_id=run_id,
        phase="embeddings",
        total_items=total_items,
    )
```

**Findings:**
- Skip logic is inside `stream_embedding_batch()` (not in this function)
- Each batch commits independently
- Failed batches don't block subsequent batches

---

### 1.4 `src/integration_coworker/shutdown.py`

#### Signal Handling (lines 90-130)
```python
async def setup(self) -> None:
    """Setup signal handlers for graceful shutdown."""
    if self._setup_complete:
        return
    
    _ = self.shutdown_event
    loop = asyncio.get_running_loop()
    
    if sys.platform != "win32":
        await self._setup_unix_handlers(loop)
    else:
        self._setup_windows_handlers(loop)
```

**Findings:**
- Registers SIGTERM and SIGINT handlers
- Sets `shutdown_event` asyncio.Event on signal
- Platform-aware (Unix vs Windows)

#### Cancellation Hooks (lines 50-70)
```python
def is_shutdown_requested(self) -> bool:
    """Check if shutdown has been requested (non-blocking)."""
    if self._shutdown_event is None:
        return False
    return self._shutdown_event.is_set()

def request_shutdown(self) -> None:
    """Request graceful shutdown. Thread-safe."""
    if self._shutdown_event is not None and not self._shutdown_event.is_set():
        logger.info("Shutdown requested")
        self._shutdown_event.set()
```

**Findings:**
- `is_shutdown_requested()` is non-blocking poll
- Thread-safe via asyncio.Event
- **NOT used in ingest_spec.py** - no cancellation polling during fetch/chunk/embed

#### How to Plumb Cancellation
```python
# Global function (line 222)
def is_shutdown_requested() -> bool:
    """Check if shutdown has been requested (convenience function)."""
    manager = get_shutdown_manager()
    return manager.is_shutdown_requested()
```

**Integration Points Needed:**
1. During HTTP fetch iteration (if streaming fetch)
2. Between spec documents in multi-spec loop
3. Between chunk batches during streaming
4. Between embedding batches (already has progress, just needs cancel check)

---

### 1.5 Config/Profile/CLI Entry Points

#### Config Settings (config/__init__.py, lines 240-265)
```python
# V3 Streaming Persistence
streaming_persistence: str = field(
    default_factory=lambda: os.getenv("STREAMING_PERSISTENCE", "auto").lower()
)

# Thresholds for auto-streaming
streaming_threshold_bytes: int = field(
    default_factory=lambda: int(os.getenv("STREAMING_THRESHOLD_BYTES", "500000"))  # 500KB
)
streaming_threshold_chunks: int = field(
    default_factory=lambda: int(os.getenv("STREAMING_THRESHOLD_CHUNKS", "500"))  # 500 chunks
)

# HTTP client settings (lines 232-234)
http_timeout: int = field(default_factory=lambda: int(os.getenv("HTTP_TIMEOUT", "30")))
http_max_retries: int = field(default_factory=lambda: int(os.getenv("HTTP_MAX_RETRIES", "3")))
http_retry_backoff: float = field(default_factory=lambda: float(os.getenv("HTTP_RETRY_BACKOFF", "1.0")))
```

**Findings:**
- `HTTP_TIMEOUT`, `HTTP_MAX_RETRIES`, `HTTP_RETRY_BACKOFF` exist but **NOT USED** in `_fetch_spec_content()`
- These are returned by `get_http_client_config()` (line 493) but ingest_spec.py doesn't call it
- Streaming threshold controls DB writes, not fetch behavior

#### CLI Entry Point (cli.py, lines 550-560)
```python
@app.command("run")
def run_command(...):
    # ...
    state = WorkflowState(
        spec_refs=list(spec_ref),  # Convert typer List to regular list
        # ...
    )
```

**Findings:**
- `spec_refs` passed directly from CLI
- No pre-validation of URLs/paths
- No size checking before workflow starts

---

## Step 2: Current Behavior Matrix

| Aspect | Current Behavior | Evidence | Gap? |
|--------|------------------|----------|------|
| **Remote Fetch: Retries** | ❌ No retries | `httpx.get()` single call (line 42) | YES |
| **Remote Fetch: Streaming** | ❌ Full load into memory | `response.text` (line 44) | YES |
| **Remote Fetch: Max Size** | ❌ No limit | No check before/during fetch | YES |
| **Remote Fetch: Content-Type Validation** | ❌ Accepts anything | Line 45: `headers.get("content-type", "application/octet-stream")` | YES |
| **Memory: Large Spec Handling** | ⚠️ Partial | Streaming decision AFTER fetch (line 188) | YES |
| **Checkpointing: Fetch Phase** | ❌ None | No progress tracking during fetch | YES |
| **Checkpointing: Chunk Phase** | ❌ None | Single commit at end of all chunks | YES |
| **Checkpointing: Embed Phase** | ✅ Yes | `streaming_progress` table, resume via `run_id` | NO |
| **Transactionality: Per-Spec** | ✅ Yes | Separate commits per spec_document | NO |
| **Transactionality: Per-Phase** | ⚠️ Partial | Chunks all-or-nothing per spec | PARTIAL |
| **Cancellation: Fetch** | ❌ None | No shutdown check during fetch | YES |
| **Cancellation: Chunk** | ❌ None | No shutdown check in chunk loop | YES |
| **Cancellation: Embed** | ⚠️ Partial | Has progress, no cancel check | PARTIAL |

---

## Step 3: Risk Assessment

### High Risk (P0)
1. **Memory explosion on large remote specs**: A 100MB spec is fully loaded into memory before any streaming decision
2. **No retry on transient network failures**: Single HTTP failure = spec ingestion failure
3. **Chunk loss on crash**: All chunks lost if crash during `stream_chunks_to_silver()`

### Medium Risk (P1)
4. **No graceful cancellation**: SIGTERM during chunk streaming = corrupted state
5. **Partial multi-spec state**: First spec committed, second spec fails = inconsistent DB
6. **No content validation**: Malicious/malformed content accepted without limits

### Lower Risk (P2)
7. **No parallelism for multi-spec**: Sequential fetch, could be parallel
8. **Hardcoded 30s timeout**: Not configurable despite `HTTP_TIMEOUT` setting existing
9. **No decompression safety**: gzip-bombed responses could expand massively

---

## Step 4: Config Knobs Inventory

### Existing (Defined but NOT Used in Ingestion)
| Setting | Env Var | Default | Used In |
|---------|---------|---------|---------|
| `http_timeout` | `HTTP_TIMEOUT` | 30 | `get_http_client_config()` only |
| `http_max_retries` | `HTTP_MAX_RETRIES` | 3 | `get_http_client_config()` only |
| `http_retry_backoff` | `HTTP_RETRY_BACKOFF` | 1.0 | `get_http_client_config()` only |

### Existing (Used)
| Setting | Env Var | Default | Used In |
|---------|---------|---------|---------|
| `streaming_persistence` | `STREAMING_PERSISTENCE` | `auto` | `ingest_spec.py` streaming decision |
| `streaming_threshold_bytes` | `STREAMING_THRESHOLD_BYTES` | 500000 | `should_use_streaming_for_spec()` |
| `streaming_threshold_chunks` | `STREAMING_THRESHOLD_CHUNKS` | 500 | `should_use_streaming_for_spec()` |

### Missing (Need to Add)
| Setting | Proposed Env Var | Default | Purpose |
|---------|------------------|---------|---------|
| `fetch_max_bytes` | `FETCH_MAX_BYTES` | 52428800 (50MB) | Hard cap on downloaded content |
| `fetch_stream_threshold` | `FETCH_STREAM_THRESHOLD` | 1048576 (1MB) | Use streaming fetch above this |
| `fetch_retryable_statuses` | `FETCH_RETRYABLE_STATUSES` | `429,500,502,503,504` | HTTP codes to retry |
| `fetch_content_types` | `FETCH_CONTENT_TYPES` | `application/json,application/yaml,...` | Allowlist for content types |

---

## Appendix: File-to-Capability Map

| File | Capabilities | Lines |
|------|--------------|-------|
| `graph/nodes/ingest_spec.py` | Fetch, chunk, streaming decision | 1-500 |
| `persistence/streaming.py` | Chunk batch writes, progress tracking | 1-500 |
| `graph/nodes/embed_spec_chunks.py` | Embedding with retry, progress | 1-420 |
| `shutdown.py` | Signal handling, cancellation hooks | 1-230 |
| `config/__init__.py` | Settings (HTTP, streaming) | 230-270 |
| `persistence/lazy_loader.py` | Chunk lazy loading for embeddings | 1-100 |

---

## Next Steps

This audit feeds into **Step 2: Design Options Analysis** where we will:
1. Debate retry approaches (httpx transport vs tenacity decorator)
2. Debate streaming fetch approaches (httpx streaming vs HEAD+Range)
3. Debate cancellation approaches (cancel token vs shutdown polling)
4. Debate checkpointing approaches (extend streaming_progress vs new table)
5. Debate transaction boundaries (per-spec vs per-batch vs single)

**Deliverable**: `docs/INGESTION_PROD_HARDENING_PLAN.md`
