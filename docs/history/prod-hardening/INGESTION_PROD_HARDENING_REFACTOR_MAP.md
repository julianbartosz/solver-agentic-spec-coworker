# API Spec Ingestion Pipeline: Refactor Map

> **Created**: 2025-12-19  
> **Prerequisites**: `INGESTION_PROD_HARDENING_AUDIT.md`, `INGESTION_PROD_HARDENING_PLAN.md`

---

## Refactor Checklist Table

| File | Functions/Classes to Change | Type | Risk | Downstream Impacts | Test Coverage |
|------|----------------------------|------|------|-------------------|---------------|
| `config/__init__.py` | `Settings`, `get_fetch_config()` | ADD | Low | All fetch callers | `test_config.py` |
| `graph/nodes/ingest_spec.py` | `_fetch_spec_content()`, `FetchConfig`, `RetryableHTTPError` | REFACTOR | **High** | All ingestion paths | `test_ingest_spec.py`, `test_streaming_ingest.py` |
| `graph/nodes/ingest_spec.py` | `_ingest_spec_streaming_with_fetched()` | REFACTOR | Medium | Streaming mode | `test_streaming_ingest.py` |
| `persistence/streaming.py` | `stream_chunks_to_silver()` → `stream_chunks_to_silver_with_progress()` | REFACTOR | **High** | Chunk persistence | `test_streaming_persistence.py` |
| `persistence/streaming.py` | Add cancellation polling | ADD | Medium | All streaming callers | `test_cancellation.py` |
| `shutdown.py` | No changes | - | - | - | - |
| `graph/nodes/embed_spec_chunks.py` | Add cancellation check | ADD | Low | Embedding loop | `test_embed_chunks.py` |
| `tests/test_ingest_retry.py` | New file | ADD | Low | None | N/A |
| `tests/test_ingest_streaming_fetch.py` | New file | ADD | Low | None | N/A |
| `tests/test_ingest_cancellation.py` | New file | ADD | Low | None | N/A |
| `tests/test_chunk_checkpoint_resume.py` | New file | ADD | Low | None | N/A |

---

## Detailed Change Specifications

### 1. `src/integration_coworker/config/__init__.py`

#### 1.1 Add FetchConfig to Settings (lines ~270)

```python
# ADD after streaming_threshold_chunks (line 261)

# Fetch configuration for remote spec downloads
fetch_max_bytes: int = field(
    default_factory=lambda: int(os.getenv("FETCH_MAX_BYTES", "52428800"))  # 50MB
)
fetch_stream_threshold: int = field(
    default_factory=lambda: int(os.getenv("FETCH_STREAM_THRESHOLD", "1048576"))  # 1MB
)
fetch_retryable_statuses: str = field(
    default_factory=lambda: os.getenv("FETCH_RETRYABLE_STATUSES", "429,500,502,503,504")
)
fetch_allowed_content_types: str = field(
    default_factory=lambda: os.getenv(
        "FETCH_ALLOWED_CONTENT_TYPES",
        "application/json,application/yaml,text/yaml,text/plain,application/xml,text/xml,application/x-yaml"
    )
)
```

#### 1.2 Add get_fetch_config() Function (after line 500)

```python
@dataclass
class FetchConfig:
    """Configuration for remote spec fetching."""
    max_bytes: int = 52428800  # 50MB
    stream_threshold: int = 1048576  # 1MB
    timeout: float = 30.0
    max_retries: int = 3
    retry_backoff: float = 1.0
    retryable_statuses: set[int] = field(default_factory=lambda: {429, 500, 502, 503, 504})
    allowed_content_types: set[str] = field(default_factory=lambda: {
        "application/json", "application/yaml", "text/yaml", 
        "text/plain", "application/xml", "text/xml", "application/x-yaml"
    })


def get_fetch_config() -> FetchConfig:
    """Get fetch configuration from settings."""
    settings = get_settings()
    
    retryable = {int(s.strip()) for s in settings.fetch_retryable_statuses.split(",") if s.strip()}
    allowed_ct = {ct.strip() for ct in settings.fetch_allowed_content_types.split(",") if ct.strip()}
    
    return FetchConfig(
        max_bytes=settings.fetch_max_bytes,
        stream_threshold=settings.fetch_stream_threshold,
        timeout=float(settings.http_timeout),
        max_retries=settings.http_max_retries,
        retry_backoff=settings.http_retry_backoff,
        retryable_statuses=retryable,
        allowed_content_types=allowed_ct,
    )
```

**Risk**: Low  
**Downstream Impact**: None until ingest_spec.py updated  
**Tests**: Add to `tests/test_config.py`

---

### 2. `src/integration_coworker/graph/nodes/ingest_spec.py`

#### 2.1 Add Imports (top of file, after line 20)

```python
# ADD imports
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception
from integration_coworker.config import get_fetch_config, FetchConfig
from integration_coworker.shutdown import is_shutdown_requested
```

#### 2.2 Add Exception Classes (after imports)

```python
class RetryableHTTPError(Exception):
    """HTTP error that should trigger retry."""
    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class SpecTooLargeError(Exception):
    """Spec exceeds maximum allowed size."""
    pass


class InvalidContentTypeError(Exception):
    """Content type not in allowlist."""
    pass
```

#### 2.3 Replace `_fetch_spec_content()` (lines 35-57)

```python
def _is_retryable_error(exc: Exception) -> bool:
    """Check if exception should trigger retry."""
    if isinstance(exc, RetryableHTTPError):
        return True
    # Connection errors are retryable
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    return False


def _fetch_spec_content(ref: str) -> tuple[str, str]:
    """
    Fetch content from a spec ref (HTTP URL or local file path).
    
    For HTTP: Uses streaming with size limits and retry logic.
    For files: Direct read with size check.
    
    Returns (content, content_type).
    Raises on failure.
    """
    config = get_fetch_config()
    
    if ref.startswith("http://") or ref.startswith("https://"):
        return _fetch_http_content(ref, config)
    else:
        return _fetch_file_content(ref, config)


def _fetch_file_content(ref: str, config: FetchConfig) -> tuple[str, str]:
    """Fetch content from local file with size check."""
    file_path = Path(ref)
    if not file_path.exists():
        raise FileNotFoundError(f"Spec file not found: {ref}")
    
    # Check size before reading
    file_size = file_path.stat().st_size
    if file_size > config.max_bytes:
        raise SpecTooLargeError(
            f"Spec file {ref} is {file_size:,} bytes, exceeds limit of {config.max_bytes:,}"
        )
    
    content = file_path.read_text(encoding="utf-8")
    suffix = file_path.suffix.lower()
    if suffix in [".yaml", ".yml"]:
        content_type = "application/yaml"
    elif suffix == ".json":
        content_type = "application/json"
    else:
        content_type = "text/plain"
    return content, content_type


@retry(
    stop=stop_after_attempt(4),  # 1 initial + 3 retries
    wait=wait_exponential_jitter(initial=1.0, max=30.0, jitter=5.0),
    retry=retry_if_exception(_is_retryable_error),
    reraise=True,
)
def _fetch_http_content(ref: str, config: FetchConfig) -> tuple[str, str]:
    """
    Fetch content from HTTP URL with streaming, size limits, and retry.
    
    Uses httpx streaming to avoid loading full response into memory.
    Validates content-type against allowlist.
    Enforces max_bytes limit during streaming.
    Retries on 429/5xx with exponential backoff.
    """
    try:
        with httpx.stream(
            "GET", ref, 
            timeout=config.timeout, 
            follow_redirects=True
        ) as response:
            # Check for retryable status codes
            if response.status_code in config.retryable_statuses:
                raise RetryableHTTPError(
                    response.status_code, 
                    f"Retryable status for {ref}"
                )
            
            # Fail fast on client errors
            response.raise_for_status()
            
            # Validate content-type
            content_type = response.headers.get("content-type", "application/octet-stream")
            base_content_type = content_type.split(";")[0].strip().lower()
            if not _is_allowed_content_type(base_content_type, config.allowed_content_types):
                raise InvalidContentTypeError(
                    f"Content-Type '{content_type}' not allowed for {ref}. "
                    f"Allowed: {config.allowed_content_types}"
                )
            
            # Stream with size limit
            chunks = []
            total_bytes = 0
            for chunk in response.iter_bytes(chunk_size=65536):
                # Check for cancellation during large downloads
                if is_shutdown_requested():
                    raise CancelledError("Fetch cancelled due to shutdown")
                
                total_bytes += len(chunk)
                if total_bytes > config.max_bytes:
                    raise SpecTooLargeError(
                        f"Spec from {ref} exceeds {config.max_bytes:,} bytes limit"
                    )
                chunks.append(chunk)
            
            content = b"".join(chunks).decode("utf-8")
            logger.debug(f"Fetched {total_bytes:,} bytes from {ref}")
            return content, content_type
            
    except httpx.HTTPStatusError as e:
        # Convert to RetryableHTTPError if appropriate
        if e.response.status_code in config.retryable_statuses:
            raise RetryableHTTPError(e.response.status_code, str(e)) from e
        raise


def _is_allowed_content_type(content_type: str, allowed: set[str]) -> bool:
    """Check if content-type is in allowlist."""
    if not allowed:
        return True  # No restriction
    
    # Handle variations (application/json vs application/json; charset=utf-8)
    base_type = content_type.split(";")[0].strip().lower()
    
    # Check exact match
    if base_type in allowed:
        return True
    
    # Check wildcard (text/*)
    major = base_type.split("/")[0]
    if f"{major}/*" in allowed:
        return True
    
    return False


class CancelledError(Exception):
    """Operation was cancelled."""
    pass
```

**Risk**: HIGH - This is the core fetch path  
**Downstream Impact**: All ingestion paths (streaming and legacy)  
**Tests**: `test_ingest_retry.py`, `test_ingest_streaming_fetch.py`

#### 2.4 Update Multi-Spec Loop (lines 160-172) - Add Cancellation Check

```python
# Replace existing loop with cancellation-aware version
for ref in state.spec_refs:
    # Check for cancellation between specs
    if is_shutdown_requested():
        logger.warning(f"Shutdown requested, stopping after {len(fetched_specs)} specs")
        state.warnings.append("Ingestion stopped early due to shutdown")
        break
    
    try:
        content, content_type = _fetch_spec_content(ref)
        fetched_specs.append((ref, content, content_type))
        total_bytes += len(content.encode("utf-8"))
        state.pending_specs.append({"ref": ref, "content": content, "content_type": content_type})
    except Exception as e:
        state.errors.append(f"Failed to fetch spec from {ref}: {str(e)}")
```

---

### 3. `src/integration_coworker/persistence/streaming.py`

#### 3.1 Add Imports (top of file)

```python
# ADD import
from integration_coworker.shutdown import is_shutdown_requested
```

#### 3.2 Create New Function `stream_chunks_to_silver_with_progress()` (after `stream_chunks_to_silver`)

```python
def stream_chunks_to_silver_with_progress(
    chunks: Iterator[Tuple[int, str]],
    spec_document_id: int,
    run_id: Optional[str] = None,
    total_chunks: Optional[int] = None,
    batch_size: int = CHUNK_BATCH_SIZE,
) -> List[int]:
    """
    Stream content chunks to spec_silver.spec_chunks with progress tracking.
    
    V4 Enhancement: Per-batch commit with checkpoint for crash recovery.
    
    Args:
        chunks: Iterator of (chunk_index, content) tuples
        spec_document_id: FK to spec_documents
        run_id: Optional run identifier for progress tracking/resume
        total_chunks: Total chunks expected (for progress reporting)
        batch_size: Number of chunks per batch write/commit
    
    Returns:
        List of chunk IDs in order
    
    Resume behavior:
        If run_id provided and progress exists, skips chunks <= last_committed_id.
        
    Idempotent: Uses (spec_document_id, chunk_index) as unique key.
    """
    engine = get_engine_type()
    schema = _get_schema("silver")
    
    # V4: Load checkpoint for resume
    phase = f"chunks:{spec_document_id}"
    start_after = -1  # Start from index 0 by default
    if run_id:
        progress = load_streaming_progress(run_id, phase)
        if progress and progress.get("last_committed_id") is not None:
            start_after = progress["last_committed_id"]
            logger.info(
                f"Resuming chunk streaming from index={start_after} "
                f"for spec_document_id={spec_document_id}, run_id={run_id}"
            )
    
    conn = get_connection()
    cur = conn.cursor()
    
    chunk_ids = []
    batch = []
    last_committed_index = start_after
    chunks_skipped = 0
    chunks_written = 0
    
    try:
        for chunk_index, content in chunks:
            # Skip already-persisted chunks (resume support)
            if chunk_index <= start_after:
                chunks_skipped += 1
                continue
            
            batch.append((spec_document_id, chunk_index, content))
            
            if len(batch) >= batch_size:
                # Write batch
                ids = _write_chunk_batch(cur, batch, engine, schema)
                chunk_ids.extend(ids)
                chunks_written += len(batch)
                
                # Commit batch
                conn.commit()
                last_committed_index = chunk_index
                
                # Save progress checkpoint
                if run_id:
                    save_streaming_progress(
                        run_id, phase, last_committed_index, 
                        total_chunks or 0
                    )
                
                # Check for cancellation
                if is_shutdown_requested():
                    logger.warning(
                        f"Shutdown during chunk streaming at index={last_committed_index}, "
                        f"written={chunks_written}, remaining will be skipped"
                    )
                    break
                
                batch = []
        
        # Write remaining batch (if not cancelled)
        if batch and not is_shutdown_requested():
            ids = _write_chunk_batch(cur, batch, engine, schema)
            chunk_ids.extend(ids)
            chunks_written += len(batch)
            conn.commit()
            last_committed_index = batch[-1][1]  # Last chunk_index in batch
            
            if run_id:
                save_streaming_progress(
                    run_id, phase, last_committed_index,
                    total_chunks or 0
                )
        
        conn.close()
        
        # Clear progress on successful completion (not cancelled)
        if run_id and not is_shutdown_requested():
            clear_streaming_progress(run_id, phase)
        
        logger.info(
            f"Streamed {chunks_written} chunks to silver "
            f"(spec_document_id={spec_document_id}, skipped={chunks_skipped})"
        )
        return chunk_ids
        
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Failed to stream chunks: {e}")
        raise
```

#### 3.3 Update `_ingest_spec_streaming_with_fetched()` to Use New Function

In `ingest_spec.py`, change line ~448:

```python
# OLD:
chunk_ids = stream_chunks_to_silver(chunk_iterator(), spec_document_id)

# NEW:
chunk_ids = stream_chunks_to_silver_with_progress(
    chunk_iterator(),
    spec_document_id,
    run_id=getattr(state, 'run_id', None),
    total_chunks=len(doc_chunks),
)
```

**Risk**: HIGH - Core persistence path  
**Downstream Impact**: All streaming ingestion  
**Tests**: `test_chunk_checkpoint_resume.py`

---

### 4. `src/integration_coworker/graph/nodes/embed_spec_chunks.py`

#### 4.1 Add Cancellation Check in `_embed_spec_chunks_streaming()` (around line 380)

```python
# ADD import at top
from integration_coworker.shutdown import is_shutdown_requested

# In _embed_spec_chunks_streaming, inside the for loop (line ~355):
for spec_doc_id in spec_doc_ids:
    # Check for cancellation between documents
    if is_shutdown_requested():
        logger.warning(f"Shutdown requested during embedding, stopping")
        break
    
    logger.info(f"Streaming embeddings for spec_document_id={spec_doc_id}")
    # ... rest of loop
```

Also add cancellation check in batch processing:

```python
# In the batch processing loop (around line 365):
if len(batch_chunks) >= EMBEDDING_BATCH_SIZE:
    # Check cancellation before expensive embedding call
    if is_shutdown_requested():
        logger.warning("Shutdown requested before embedding batch")
        break
    
    embedded, failed = _process_embedding_batch(...)
```

**Risk**: Low  
**Downstream Impact**: Embedding pipeline  
**Tests**: Extend `test_embed_chunks.py`

---

## New Test Files

### 5. `tests/test_ingest_retry.py`

```python
"""Tests for HTTP fetch retry logic in ingest_spec."""

import pytest
import httpx
from unittest.mock import patch, MagicMock
from integration_coworker.graph.nodes.ingest_spec import (
    _fetch_http_content,
    RetryableHTTPError,
    _is_retryable_error,
)
from integration_coworker.config import FetchConfig


class TestRetryLogic:
    """Test HTTP retry behavior."""
    
    def test_retries_on_503(self):
        """Verify 503 triggers retry."""
        # Mock responses: 503, 503, 200
        pass
    
    def test_retries_on_429_rate_limit(self):
        """Verify 429 triggers retry with backoff."""
        pass
    
    def test_fails_fast_on_404(self):
        """Verify 404 does NOT retry."""
        pass
    
    def test_fails_fast_on_401(self):
        """Verify 401 does NOT retry."""
        pass
    
    def test_retries_on_connection_error(self):
        """Verify connection errors trigger retry."""
        pass
    
    def test_max_retries_exceeded(self):
        """Verify error raised after max retries."""
        pass
    
    def test_backoff_timing(self):
        """Verify exponential backoff with jitter."""
        pass
```

### 6. `tests/test_ingest_streaming_fetch.py`

```python
"""Tests for streaming fetch with size limits."""

import pytest
from integration_coworker.graph.nodes.ingest_spec import (
    _fetch_http_content,
    SpecTooLargeError,
    InvalidContentTypeError,
)
from integration_coworker.config import FetchConfig


class TestStreamingFetch:
    """Test streaming fetch behavior."""
    
    def test_enforces_max_bytes(self):
        """Verify specs exceeding max_bytes are rejected."""
        pass
    
    def test_accepts_valid_content_types(self):
        """Verify valid content types are accepted."""
        pass
    
    def test_rejects_html_content_type(self):
        """Verify HTML responses are rejected."""
        pass
    
    def test_cancellation_during_fetch(self):
        """Verify cancellation interrupts large fetch."""
        pass
    
    def test_file_size_check(self):
        """Verify local file size is checked before reading."""
        pass
```

### 7. `tests/test_ingest_cancellation.py`

```python
"""Tests for cancellation behavior during ingestion."""

import pytest
from unittest.mock import patch
from integration_coworker.graph.nodes.ingest_spec import ingest_spec
from integration_coworker.graph.state import WorkflowState
from integration_coworker.shutdown import get_shutdown_manager, reset_shutdown_manager


class TestIngestionCancellation:
    """Test cancellation during various ingestion phases."""
    
    @pytest.fixture(autouse=True)
    def reset_shutdown(self):
        """Reset shutdown manager between tests."""
        reset_shutdown_manager()
        yield
        reset_shutdown_manager()
    
    def test_cancellation_between_specs(self):
        """Verify cancellation stops multi-spec ingestion cleanly."""
        pass
    
    def test_cancellation_during_chunk_streaming(self):
        """Verify cancellation commits partial chunks."""
        pass
    
    def test_progress_saved_on_cancellation(self):
        """Verify progress checkpoint updated on cancel."""
        pass
    
    def test_resume_after_cancellation(self):
        """Verify resume continues from cancellation point."""
        pass
```

### 8. `tests/test_chunk_checkpoint_resume.py`

```python
"""Tests for chunk streaming checkpoint/resume."""

import pytest
from integration_coworker.persistence.streaming import (
    stream_chunks_to_silver_with_progress,
    load_streaming_progress,
    save_streaming_progress,
    clear_streaming_progress,
)


class TestChunkCheckpointResume:
    """Test chunk streaming with checkpoint/resume."""
    
    def test_progress_saved_per_batch(self):
        """Verify progress checkpoint after each batch."""
        pass
    
    def test_resume_skips_persisted_chunks(self):
        """Verify resume skips already-persisted chunks."""
        pass
    
    def test_idempotent_on_restart(self):
        """Verify restart doesn't create duplicates."""
        pass
    
    def test_progress_cleared_on_completion(self):
        """Verify progress cleared after successful completion."""
        pass
    
    def test_partial_batch_committed_on_crash(self):
        """Simulate crash mid-batch, verify committed portion."""
        pass
```

---

## Downstream Impact Analysis

### Changes That May Break Existing Behavior

| Change | Impact | Mitigation |
|--------|--------|------------|
| New exceptions (`SpecTooLargeError`, etc.) | Error handling code may not catch | Add to exports, document |
| Content-type validation | May reject previously-accepted specs | Configurable allowlist |
| Max bytes limit | May reject large valid specs | Configurable, default 50MB |
| Per-batch chunk commits | Transaction semantics change | Progress tracking handles gaps |

### Files That May Need Adjustment

| File | Reason | Action |
|------|--------|--------|
| `tests/test_ingest_spec.py` | New exception types | Update assertions |
| `tests/test_streaming_ingest.py` | Function signature changes | Update mocks |
| `cli.py` | New error types to display | Add error handling |
| `api/entrypoint.py` | New error types | Add to error responses |

---

## Implementation Order

1. **Phase 1**: Config knobs (`config/__init__.py`)
2. **Phase 2**: Fetch retry + streaming (`ingest_spec.py` fetch functions)
3. **Phase 3**: Chunk checkpointing (`streaming.py`)
4. **Phase 4**: Cancellation plumbing (all files)
5. **Phase 5**: Integration tests

Each phase is independently deployable and testable.
