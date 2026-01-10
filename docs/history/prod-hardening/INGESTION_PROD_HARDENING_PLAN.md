# API Spec Ingestion Pipeline: Production Hardening Plan

> **Created**: 2025-12-19  
> **Branch**: `copilot/prod-readiness-v4`  
> **Prerequisite**: `docs/INGESTION_PROD_HARDENING_AUDIT.md`

---

## Design Decisions

For each hardening item, we analyze multiple approaches and select the best one with justification.

---

## A) HTTP Retry Policy for Fetch

### Problem Statement
`_fetch_spec_content()` calls `httpx.get()` once with no retry. Network blips, 429 rate limits, or 5xx errors cause immediate failure.

### Approach 1: httpx Transport Retries
```python
import httpx
from httpx import Timeout, Limits

transport = httpx.HTTPTransport(retries=3)
client = httpx.Client(transport=transport, timeout=Timeout(30.0))
response = client.get(ref)
```

**Pros:**
- Built into httpx, no external dependency
- Handles connection-level retries automatically

**Cons:**
- Only retries on **connection errors**, NOT on 429/5xx responses
- No exponential backoff
- No jitter
- Cannot distinguish retryable vs non-retryable status codes

### Approach 2: tenacity Decorator with Status Filtering
```python
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception

@retry(
    stop=stop_after_attempt(4),  # 1 initial + 3 retries
    wait=wait_exponential_jitter(initial=1, max=30, jitter=5),
    retry=retry_if_exception(is_retryable_error),
)
def _fetch_spec_content_inner(ref: str, timeout: float) -> tuple[str, str]:
    response = httpx.get(ref, timeout=timeout, follow_redirects=True)
    if response.status_code in RETRYABLE_STATUSES:
        raise RetryableHTTPError(response.status_code)
    response.raise_for_status()
    return response.text, response.headers.get("content-type", "application/octet-stream")
```

**Pros:**
- Full control over retryable conditions (status codes, exception types)
- Built-in exponential backoff with jitter
- Well-tested library used across Python ecosystem
- Can log retry attempts
- Already used in `embed_spec_chunks.py` for embedding retries (consistency)

**Cons:**
- External dependency (tenacity)
- Slightly more code

### ✅ **Decision: Approach 2 (tenacity)**

**Rationale:**
1. We need to retry on 429/5xx, not just connection errors
2. Exponential backoff with jitter is essential for rate-limited APIs
3. Consistency with existing embedding retry pattern in the codebase
4. tenacity is already a common dependency in LangChain ecosystem

**Specification:**
- **Retryable statuses**: `429, 500, 502, 503, 504`
- **Non-retryable (fail fast)**: `400, 401, 403, 404` (client errors)
- **Max attempts**: 4 (1 initial + 3 retries)
- **Backoff**: Exponential with jitter, initial=1s, max=30s
- **Timeout**: Use `HTTP_TIMEOUT` setting (default 30s)

---

## B) Remote Streaming Fetch to Avoid Memory Explosion

### Problem Statement
`response.text` loads the entire response into memory. A 100MB spec consumes 100MB+ RAM before any streaming decision.

### Approach 1: httpx Streaming Interface
```python
def _fetch_spec_content_streaming(ref: str, max_bytes: int) -> Iterator[bytes]:
    with httpx.stream("GET", ref, timeout=30.0, follow_redirects=True) as response:
        response.raise_for_status()
        bytes_read = 0
        for chunk in response.iter_bytes(chunk_size=65536):
            bytes_read += len(chunk)
            if bytes_read > max_bytes:
                raise SpecTooLargeError(f"Spec exceeds {max_bytes} bytes")
            yield chunk
```

**Pros:**
- Memory usage bounded by chunk_size (64KB)
- Can enforce max_bytes during streaming
- Natural integration with httpx

**Cons:**
- Need to reassemble content for non-streaming mode
- More complex control flow

### Approach 2: HEAD/Range Strategy
```python
def _fetch_spec_content_smart(ref: str, max_bytes: int, stream_threshold: int) -> tuple[str, str]:
    # Step 1: HEAD to get Content-Length
    head_response = httpx.head(ref, timeout=10.0, follow_redirects=True)
    content_length = int(head_response.headers.get("Content-Length", 0))
    
    if content_length > max_bytes:
        raise SpecTooLargeError(...)
    
    if content_length > stream_threshold:
        # Use streaming
        return _fetch_streaming(ref)
    else:
        # Small spec: load fully
        return _fetch_full(ref)
```

**Pros:**
- Can decide fetch strategy before downloading
- Respects Content-Length header

**Cons:**
- Not all servers support HEAD
- Content-Length can be missing (chunked encoding)
- Requires TWO requests for large specs
- Range requests not universally supported

### ✅ **Decision: Approach 1 (httpx Streaming) with Adaptive Mode**

**Rationale:**
1. Single request (HEAD adds latency and isn't reliable)
2. httpx streaming is well-supported
3. Can still reassemble for small specs efficiently
4. Handles chunked responses (no Content-Length)

**Specification:**
```python
def _fetch_spec_content(ref: str, config: FetchConfig) -> tuple[str, str]:
    """
    Fetch with streaming for safety, reassemble for small specs.
    
    Config:
        max_bytes: Hard cap (default 50MB)
        stream_threshold: Use streaming DB writes above this (default 1MB)
        timeout: Request timeout (default 30s)
        allowed_content_types: Allowlist (default: json, yaml, yml, xml, txt)
    """
    with httpx.stream("GET", ref, timeout=config.timeout, follow_redirects=True) as response:
        response.raise_for_status()
        
        # Validate content-type
        content_type = response.headers.get("content-type", "")
        if not _is_allowed_content_type(content_type, config.allowed_content_types):
            raise InvalidContentTypeError(content_type)
        
        # Stream with size limit
        chunks = []
        total_bytes = 0
        for chunk in response.iter_bytes(chunk_size=65536):
            total_bytes += len(chunk)
            if total_bytes > config.max_bytes:
                raise SpecTooLargeError(f"Spec exceeds {config.max_bytes} bytes limit")
            chunks.append(chunk)
        
        content = b"".join(chunks).decode("utf-8")
        return content, content_type
```

**Safeguards:**
- **Hard max bytes**: 50MB default, configurable via `FETCH_MAX_BYTES`
- **Content-type allowlist**: `application/json`, `application/yaml`, `text/yaml`, `text/plain`, `application/xml`
- **Decompression safety**: httpx handles gzip automatically; cap applies to **decompressed bytes** (safer)
- **Streaming threshold**: 1MB default for deciding DB streaming mode (separate from fetch)

---

## C) Cancellation / Cancel Token

### Problem Statement
SIGTERM during ingestion leaves the system in undefined state. There's no way to gracefully stop mid-operation.

### Approach 1: Thread-Safe Cancellation Token
```python
class CancellationToken:
    def __init__(self):
        self._cancelled = threading.Event()
    
    def cancel(self):
        self._cancelled.set()
    
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()
    
    def check_cancelled(self):
        if self._cancelled.is_set():
            raise CancelledException("Operation cancelled")
```

**Pros:**
- Explicit, can be passed through call stack
- Type-safe
- Can have multiple tokens for different operations

**Cons:**
- New class to maintain
- Must thread through many function signatures
- Parallel concept to existing ShutdownManager

### Approach 2: Reuse ShutdownManager with Polling
```python
from integration_coworker.shutdown import is_shutdown_requested

def stream_chunks_to_silver(...):
    for chunk_index, content in chunks:
        if is_shutdown_requested():
            logger.warning("Shutdown requested during chunk streaming, committing partial progress")
            break  # Commit what we have
        batch.append(...)
```

**Pros:**
- Uses existing infrastructure
- No new types to add
- Already handles SIGTERM/SIGINT
- Thread-safe (asyncio.Event)

**Cons:**
- Global state (singleton pattern)
- Less explicit in function signatures

### ✅ **Decision: Approach 2 (ShutdownManager Polling)**

**Rationale:**
1. ShutdownManager already exists and handles signals correctly
2. Adding a new CancellationToken is parallel infrastructure
3. Global polling is simpler and works for our use case
4. No signature changes needed

**Yield Points (where to poll):**
1. **During fetch streaming**: After each 64KB chunk
2. **Between spec documents**: Before starting next spec in multi-spec loop
3. **Between chunk batches**: After each 100-chunk batch write
4. **Between embedding batches**: After each 25-embedding batch (already has progress, add cancel)

**Cleanup on Cancel:**
- Commit any pending transaction (partial progress is OK due to idempotency)
- Update progress checkpoint (for resume)
- Log cancellation reason and point
- Close database connections

---

## D) Checkpointing/Resume for Chunk Streaming

### Problem Statement
`stream_chunks_to_silver()` commits all chunks at the end. A crash at 90% loses all work.

### Approach 1: Extend streaming_progress Table
```sql
-- Existing table, add chunk phase support
INSERT INTO integration_gold.streaming_progress 
    (run_id, phase, last_committed_id, total_items, started_at, updated_at)
VALUES ($1, 'chunks', $2, $3, NOW(), NOW())
ON CONFLICT (run_id, phase) DO UPDATE SET ...
```

**Pros:**
- Reuses existing table and functions
- Consistent with embedding progress
- No schema migration needed

**Cons:**
- `last_committed_id` semantics differ (chunk_id vs chunk_index)
- Need to track per-spec_document progress (multi-spec)

### Approach 2: New Progress Table with Finer Granularity
```sql
CREATE TABLE integration_gold.chunk_streaming_progress (
    spec_document_id BIGINT NOT NULL,
    last_chunk_index INT NOT NULL,
    total_chunks INT NOT NULL,
    run_id TEXT,
    started_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (spec_document_id)
);
```

**Pros:**
- Cleaner semantics for chunk progress
- Primary key on spec_document_id makes resume query simple
- Separate from embedding progress

**Cons:**
- New table requires migration
- More code to maintain

### ✅ **Decision: Approach 1 (Extend streaming_progress) with Composite Key**

**Rationale:**
1. Existing table has `(run_id, phase)` key; we can use `phase="chunks:{spec_document_id}"`
2. No schema migration needed
3. Consistent progress API
4. Resume logic same as embeddings

**Implementation:**
```python
def stream_chunks_to_silver_with_progress(
    chunks: Iterator[Tuple[int, str]],
    spec_document_id: int,
    run_id: Optional[str] = None,
    batch_size: int = CHUNK_BATCH_SIZE,
) -> List[int]:
    """Stream chunks with per-batch commit and progress tracking."""
    
    # Load checkpoint
    phase = f"chunks:{spec_document_id}"
    start_after = 0
    if run_id:
        progress = load_streaming_progress(run_id, phase)
        if progress:
            start_after = progress["last_committed_id"]  # This is last chunk_index
    
    chunk_ids = []
    batch = []
    last_index = start_after
    
    for chunk_index, content in chunks:
        if chunk_index <= start_after:
            continue  # Skip already persisted
        
        batch.append((spec_document_id, chunk_index, content))
        
        if len(batch) >= batch_size:
            # Write batch
            ids = _write_chunk_batch(cur, batch, engine, schema)
            chunk_ids.extend(ids)
            
            # Commit and save progress
            conn.commit()
            last_index = chunk_index
            if run_id:
                save_streaming_progress(run_id, phase, last_index, total_chunks)
            
            # Check cancellation
            if is_shutdown_requested():
                logger.warning(f"Shutdown during chunk streaming at index {last_index}")
                break
            
            batch = []
    
    # Final batch
    if batch:
        ids = _write_chunk_batch(cur, batch, engine, schema)
        chunk_ids.extend(ids)
        conn.commit()
    
    # Clear progress on completion
    if run_id and not is_shutdown_requested():
        clear_streaming_progress(run_id, phase)
    
    return chunk_ids
```

**Resume Semantics:**
- `last_committed_id` = last successfully committed `chunk_index`
- Skip chunks where `chunk_index <= last_committed_id`
- Idempotent: `ON CONFLICT (spec_document_id, chunk_index) DO NOTHING`

---

## E) Transaction Boundaries / Partial Commit Risk

### Problem Statement
Multi-spec ingestion commits spec_documents independently. If spec 1 succeeds and spec 2 fails, we have partial state.

### Approach 1: Per-Spec Atomic Transactions
```python
for ref, content, content_type in fetched_specs:
    try:
        with db.transaction() as conn:
            # Insert spec_document
            # Stream chunks
            # All or nothing for this spec
            conn.commit()
    except Exception as e:
        state.errors.append(f"Failed {ref}: {e}")
        # Continue to next spec
```

**Pros:**
- Clear failure boundaries
- Partial success is acceptable (multi-provider scenario)
- Simpler recovery (retry failed specs only)
- Better memory: don't hold all specs before any commit

**Cons:**
- Partial state if some specs fail
- Need to track which specs succeeded for idempotent retry

### Approach 2: Single Transaction for All Specs
```python
try:
    with db.transaction() as conn:
        for ref, content, content_type in fetched_specs:
            # Insert spec_document
            # Stream chunks
        conn.commit()  # All or nothing
except Exception as e:
    conn.rollback()
    state.errors.append("Complete ingestion failed")
```

**Pros:**
- Atomic: all or nothing
- Simple mental model

**Cons:**
- Long transaction (problematic for large specs)
- Memory: must hold all data in transaction
- Single failure = total rollback (wasteful)
- Lock contention on long transactions

### ✅ **Decision: Approach 1 (Per-Spec Atomic) with Explicit State Tracking**

**Rationale:**
1. Production reality: multi-spec ingestion can have partial success
2. Long transactions are problematic at scale
3. Idempotent upserts make retry safe
4. Clearer debugging (which spec failed?)

**Implementation:**
- Each spec has its own transaction for `spec_document` + `chunks`
- Track succeeded specs in `state.persisted_ids["succeeded_spec_refs"]`
- Track failed specs in `state.errors` with the ref
- Resume: skip specs already in `succeeded_spec_refs`

**"Done" Definition:**
- A spec is "done" when its `spec_document` row exists AND all chunks are persisted
- Idempotency: `ON CONFLICT ... DO UPDATE SET updated_at = NOW()`
- Safe to retry: upsert semantics prevent duplicates

---

## Summary of Chosen Approaches

| Item | Decision | Key Benefit |
|------|----------|-------------|
| **A) HTTP Retry** | tenacity decorator | Full control over retryable conditions |
| **B) Streaming Fetch** | httpx streaming | Memory safety, size limits |
| **C) Cancellation** | ShutdownManager polling | Reuse existing infrastructure |
| **D) Checkpointing** | Extend streaming_progress | No new table, consistent API |
| **E) Transactions** | Per-spec atomic | Partial success OK, no long transactions |

---

## Acceptance Tests (to be implemented)

### AT-1: HTTP Retry
```python
def test_fetch_retries_on_503():
    """Verify 503 responses trigger retry with backoff."""
    # Mock server returns 503 twice, then 200
    # Assert: 3 requests made, final response used
    
def test_fetch_fails_fast_on_404():
    """Verify 404 does not retry."""
    # Mock server returns 404
    # Assert: only 1 request made, immediate error
```

### AT-2: Streaming Fetch
```python
def test_fetch_enforces_max_bytes():
    """Verify specs exceeding max_bytes are rejected."""
    # Serve 60MB response
    # Assert: SpecTooLargeError raised before 60MB downloaded

def test_fetch_rejects_invalid_content_type():
    """Verify HTML responses are rejected."""
    # Serve text/html
    # Assert: InvalidContentTypeError raised
```

### AT-3: Cancellation
```python
def test_cancel_mid_chunk_streaming():
    """Verify cancellation during chunk streaming commits partial progress."""
    # Start ingestion of large spec
    # Signal shutdown after 50% of chunks
    # Assert: DB has 50% of chunks, progress checkpoint updated
    # Assert: Resume continues from checkpoint
```

### AT-4: Checkpointing
```python
def test_chunk_streaming_resume_after_crash():
    """Verify chunk streaming resumes from last checkpoint."""
    # Stream 100 chunks with batch_size=25
    # Kill process after 50 chunks
    # Restart with same run_id
    # Assert: only chunks 51-100 written, no duplicates
```

### AT-5: Transaction Boundaries
```python
def test_multi_spec_partial_success():
    """Verify partial success in multi-spec ingestion."""
    # Spec 1: valid
    # Spec 2: invalid (malformed)
    # Assert: Spec 1 persisted, Spec 2 error logged
    # Assert: state.errors contains spec 2 failure
    # Assert: state.persisted_ids["succeeded_spec_refs"] contains spec 1
```

---

## File-by-File Changes (Detailed in Step 3)

See `docs/INGESTION_PROD_HARDENING_REFACTOR_MAP.md` for exact function/class changes.

---

## Migration Strategy

1. **No schema changes**: Using existing `streaming_progress` table
2. **Backwards compatible**: New behavior gated behind same env vars
3. **Incremental rollout**: Can deploy fetch retry separately from checkpointing
4. **Feature flags**: Each hardening item can be toggled independently

---

## Next Steps

1. Create refactor map (Step 3)
2. Implement changes incrementally (Step 4)
3. Run production-themed tests (Step 5)
4. Final ranking and recommendations (Step 6)
