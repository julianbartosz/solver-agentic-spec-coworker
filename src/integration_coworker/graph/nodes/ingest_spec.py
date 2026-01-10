"""
ingest_spec node — fetches and chunks all spec_refs (primary + supporting).

Implements: Design Doc §3.2 Ingest Spec
Touches: spec_documents, source_refs (Silver layer)

V3 Streaming Mode:
Automatically enabled for large specs (>500KB or >500 chunks) to prevent
memory issues. Can be forced via STREAMING_PERSISTENCE=true/false.

When streaming:
- Chunks are streamed directly to the database
- state.doc_chunks remains empty (memory efficient)
- Memory usage stays <20MB regardless of spec size

V4 Production Hardening:
- HTTP retry with exponential backoff on transient errors (429, 5xx)
- Streaming fetch with configurable size limits
- Content-type validation to reject non-spec responses
- Graceful cancellation support via ShutdownManager

API-002: Multi-Spec Source Reference Handling
- Creates SourceRef objects for each spec
- Links SpecDocument to its SourceRef
- Enables traceability for multi-provider integrations
"""
from pathlib import Path
import hashlib
import logging
import tempfile
import os
from typing import List, Tuple, Iterator, Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception,
)

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import SpecDocument, SourceRef
from integration_coworker.config import (
    should_use_streaming_for_spec,
    is_streaming_persistence_disabled,
    get_fetch_config,
    FetchConfig,
)
from integration_coworker.shutdown import is_shutdown_requested
from integration_coworker.security.ssrf import (
    SSRFBlockedError,
    SSRFConfig,
    validate_url_target,
)

logger = logging.getLogger(__name__)


# =============================================================================
# V4: Production Hardening - Exception Classes
# =============================================================================

class RetryableHTTPError(Exception):
    """HTTP error that should trigger retry (429, 5xx)."""
    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class SpecTooLargeError(Exception):
    """Spec exceeds maximum allowed size."""
    def __init__(self, uri: str, size: int, max_size: int):
        self.uri = uri
        self.size = size
        self.max_size = max_size
        super().__init__(f"Spec from {uri} is {size:,} bytes, exceeds limit of {max_size:,}")


class InvalidContentTypeError(Exception):
    """Content type not in allowlist."""
    def __init__(self, uri: str, content_type: str, allowed: frozenset):
        self.uri = uri
        self.content_type = content_type
        self.allowed = allowed
        super().__init__(
            f"Content-Type '{content_type}' not allowed for {uri}. "
            f"Allowed: {sorted(allowed)}"
        )


class FetchCancelledError(Exception):
    """Fetch was cancelled due to shutdown request."""
    pass


# =============================================================================
# V4: Production Hardening - HTTP Client Management
# =============================================================================

# Global HTTP client for connection pooling (optional optimization)
_http_client: httpx.Client | None = None
_http_client_cleanup_registered: bool = False


def _get_http_client() -> httpx.Client:
    """
    Get or create a shared HTTP client for connection pooling.
    
    The client is created lazily and reused across requests for better
    performance (connection reuse, keep-alive).
    
    H-5: Registers cleanup_http_client() for graceful shutdown:
    1. atexit handler (always works, fallback for ungraceful exit)
    2. ShutdownManager (if available, for graceful async shutdown)
    
    Returns:
        Shared httpx.Client instance
    """
    global _http_client, _http_client_cleanup_registered
    if _http_client is None:
        _http_client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        
        # H-5: Register cleanup on first client creation
        if not _http_client_cleanup_registered:
            _http_client_cleanup_registered = True
            
            # Always register atexit as fallback (works for ungraceful exits)
            import atexit
            atexit.register(cleanup_http_client)
            logger.debug("Registered HTTP client cleanup with atexit")
            
            # Try to register with ShutdownManager for graceful async shutdown
            try:
                from integration_coworker.shutdown import get_shutdown_manager
                manager = get_shutdown_manager()
                # Note: manager may not be setup yet, but callbacks still work
                # when cleanup() is eventually called
                manager.register_cleanup(cleanup_http_client)
                logger.debug("Registered HTTP client cleanup with ShutdownManager")
            except ImportError:
                pass  # ShutdownManager not available
            except Exception as e:
                logger.debug(f"Could not register with ShutdownManager: {e}")
    
    return _http_client


def cleanup_http_client() -> None:
    """
    Close and cleanup the shared HTTP client.
    
    Call this at the end of spec ingestion or during shutdown to release
    connections. Safe to call multiple times.
    
    Note: Also resets the cleanup registration flag for testing purposes.
    In production, this allows re-registration if the client is recreated.
    """
    global _http_client, _http_client_cleanup_registered
    if _http_client is not None:
        try:
            _http_client.close()
        except Exception as e:
            # Item G: Log rather than silently swallow cleanup errors
            logger.debug(f"HTTP client cleanup error (safe to ignore): {e}")
        _http_client = None
    # Reset registration flag so re-creation will register again
    # This is primarily for testing, but also allows proper re-registration
    # if the client is explicitly cleaned up and then used again
    _http_client_cleanup_registered = False


def _parse_retry_after(response: httpx.Response) -> float | None:
    """
    Parse Retry-After header from HTTP response.
    
    Handles both:
    - Seconds format: "Retry-After: 120"
    - HTTP-date format: "Retry-After: Wed, 21 Oct 2015 07:28:00 GMT"
    
    Args:
        response: HTTP response to parse
        
    Returns:
        Delay in seconds, or None if header not present/parseable
    """
    retry_after = response.headers.get("retry-after")
    if not retry_after:
        return None
    
    # Try seconds format first (most common)
    try:
        return float(retry_after)
    except ValueError:
        pass
    
    # Try HTTP-date format
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        retry_date = parsedate_to_datetime(retry_after)
        now = datetime.now(timezone.utc)
        delta = (retry_date - now).total_seconds()
        return max(0.0, delta)  # Don't return negative delays
    except Exception as e:
        # Item G: Log rather than silently fail date parsing
        logger.debug(f"Failed to parse Retry-After as HTTP-date '{retry_after}': {e}")
    
    return None


# =============================================================================
# V4: Production Hardening - Retry Logic
# =============================================================================

def _is_retryable_error(exc: Exception) -> bool:
    """
    Check if exception should trigger retry.
    
    Retryable errors:
    - RetryableHTTPError (429, 5xx status codes)
    - Connection errors (network issues)
    - Timeout errors
    """
    if isinstance(exc, RetryableHTTPError):
        return True
    # Connection errors are retryable
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    return False


def _is_allowed_content_type(content_type: str, allowed: frozenset) -> bool:
    """
    Check if content-type is in allowlist.
    
    Handles variations like "application/json; charset=utf-8".
    """
    if not allowed:
        return True  # No restriction
    
    # Handle charset and other parameters
    base_type = content_type.split(";")[0].strip().lower()
    
    # Check exact match
    if base_type in allowed:
        return True
    
    # Check wildcard (text/*)
    major = base_type.split("/")[0]
    if f"{major}/*" in allowed:
        return True
    
    return False


def _read_file_with_encoding_fallback(file_path: Path) -> str:
    """
    V42-001: Read file content with robust encoding fallback.
    
    Production Problem:
    CSV and Excel files often contain non-UTF-8 characters (e.g., Latin-1 encoded
    non-breaking spaces like 0xa0). The previous hard-coded UTF-8 encoding would
    fail on such files with: 'utf-8' codec can't decode byte 0xa0
    
    Solution:
    Try encodings in preference order, falling back to error-tolerant modes:
    1. UTF-8 (most common, should work for most files)
    2. UTF-8 with BOM (Windows-generated files)
    3. Latin-1/ISO-8859-1 (common for European text, never fails for 0x00-0xFF)
    4. CP1252 (Windows Western European)
    5. UTF-8 with error replacement (last resort, replaces invalid chars)
    
    Args:
        file_path: Path to the file to read
        
    Returns:
        File content as string
    """
    # Encoding preference order - UTF-8 first, then common alternatives
    encodings = [
        "utf-8",
        "utf-8-sig",  # UTF-8 with BOM
        "latin-1",     # ISO-8859-1, never fails for 0x00-0xFF bytes
        "cp1252",      # Windows Western European
    ]
    
    last_error = None
    for encoding in encodings:
        try:
            content = file_path.read_text(encoding=encoding)
            if encoding != "utf-8":
                logger.info(f"[V42-001] Read {file_path.name} with {encoding} encoding (UTF-8 failed)")
            return content
        except UnicodeDecodeError as e:
            last_error = e
            continue
    
    # Last resort: UTF-8 with error replacement
    # This replaces invalid bytes with the Unicode replacement character (�)
    logger.warning(
        f"[V42-001] All standard encodings failed for {file_path.name}, "
        f"using UTF-8 with error replacement. Last error: {last_error}"
    )
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _fetch_spec_content(ref: str, repo_root: Optional[Path] = None) -> tuple[str, str]:
    """
    Fetch content from a spec ref (HTTP URL or local file path).
    
    V4 Production Hardening:
    - HTTP: Uses streaming with retry, size limits, content-type validation
    - Files: Direct read with size check
    
    BUG-006 Fix:
    - Relative file paths are now resolved against repo_root when provided
    - This allows spec paths like "spec/openapi.yaml" to work correctly when
      the coworker is run from a different directory than the target repo
    
    Args:
        ref: Spec reference (URL or file path)
        repo_root: Optional repository root for resolving relative paths
    
    Returns (content, content_type).
    Raises on failure (RetryableHTTPError, SpecTooLargeError, etc.).
    """
    config = get_fetch_config()
    
    if ref.startswith("http://") or ref.startswith("https://"):
        return _fetch_http_content(ref, config)
    else:
        return _fetch_file_content(ref, config, repo_root=repo_root)


def _fetch_file_content(
    ref: str,
    config: FetchConfig,
    repo_root: Optional[Path] = None,
) -> tuple[str, str]:
    """
    Fetch content from local file with size check.
    
    BUG-006 Fix: Path Resolution for Relative Spec Paths
    
    When a relative path is provided (e.g., "spec/openapi.yaml"), it's resolved:
    1. If repo_root is provided and path is relative: resolve against repo_root
    2. If absolute path: use as-is  
    3. Otherwise: resolve against current working directory (legacy behavior)
    
    This allows users to specify spec paths relative to the target repository
    regardless of where the coworker is invoked from.
    
    Args:
        ref: File path (absolute or relative)
        config: Fetch configuration
        repo_root: Optional repository root for resolving relative paths
        
    Returns:
        (content, content_type)
        
    Raises:
        FileNotFoundError: File doesn't exist
        SpecTooLargeError: File exceeds max_bytes
    """
    # BUG-006: Resolve relative paths against repo_root when available
    ref_path = Path(ref)
    
    if ref_path.is_absolute():
        # Absolute path - use directly
        file_path = ref_path
    elif repo_root is not None:
        # Relative path with repo_root - resolve against it
        file_path = (repo_root / ref_path).resolve()
        logger.debug(f"[BUG-006] Resolved '{ref}' relative to repo_root: {file_path}")
    else:
        # Relative path without repo_root - resolve against cwd (legacy behavior)
        file_path = ref_path.resolve()
        logger.debug(f"[BUG-006] Resolved '{ref}' against cwd: {file_path}")
    
    if not file_path.exists():
        # Provide helpful error message with resolution details
        error_msg = f"Spec file not found: {ref}"
        if repo_root is not None and not ref_path.is_absolute():
            error_msg += f" (resolved to: {file_path}, repo_root: {repo_root})"
        raise FileNotFoundError(error_msg)
    
    # Check size before reading
    file_size = file_path.stat().st_size
    if file_size > config.max_bytes:
        raise SpecTooLargeError(ref, file_size, config.max_bytes)
    
    # V42-001: Robust encoding handling for non-UTF-8 files (e.g., CSV with Latin-1)
    # Try encodings in order of preference, falling back to error-tolerant mode
    content = _read_file_with_encoding_fallback(file_path)
    suffix = file_path.suffix.lower()
    if suffix in [".yaml", ".yml"]:
        content_type = "application/yaml"
    elif suffix == ".json":
        content_type = "application/json"
    else:
        content_type = "text/plain"
    return content, content_type


# SSRF configuration for HTTP fetching
_SSRF_CONFIG = SSRFConfig(max_redirects=5)


@retry(
    stop=stop_after_attempt(4),  # 1 initial + 3 retries
    wait=wait_exponential_jitter(initial=1.0, max=30.0, jitter=5.0),
    retry=retry_if_exception(_is_retryable_error),
    reraise=True,
)
def _fetch_http_content(ref: str, config: FetchConfig) -> tuple[str, str]:
    """
    Fetch content from HTTP URL with streaming, size limits, retry, and SSRF protection.
    
    V4 Production Hardening:
    - SSRF protection: validates URL and all redirect targets against blocklist
    - Manual redirect handling to validate each hop
    - Uses httpx streaming to avoid loading full response into memory
    - Validates content-type against allowlist
    - Enforces max_bytes limit during streaming
    - Retries on 429/5xx with exponential backoff + jitter
    - Checks for shutdown signal during large downloads
    
    V5 Security Hardening:
    - Scheme allowlist (http/https only)
    - IP blocklist (private, loopback, link-local, metadata)
    - Redirect hop limit (max 5)
    - DNS resolution validated immediately before request
    
    Residual Risk Note:
    - DNS rebinding between validation and TCP connect is possible but mitigated
      by resolving immediately before validation. For complete protection, deploy
      network egress controls (proxy/firewall blocking RFC1918 + metadata IPs).
    
    Args:
        ref: HTTP URL to fetch
        config: Fetch configuration
        
    Returns:
        (content, content_type)
        
    Raises:
        SSRFBlockedError: URL or redirect targets blocked by SSRF protection
        RetryableHTTPError: 429 or 5xx response (triggers retry)
        httpx.HTTPStatusError: Non-retryable HTTP error (4xx except 429)
        SpecTooLargeError: Response exceeds max_bytes
        InvalidContentTypeError: Response content-type not allowed
        FetchCancelledError: Shutdown requested during fetch
    """
    # SSRF Check 1: Validate initial URL before any request
    validate_url_target(ref, _SSRF_CONFIG)
    
    current_url = ref
    redirect_count = 0
    
    while True:
        try:
            # Use follow_redirects=False so we can validate each hop
            with httpx.stream(
                "GET", current_url,
                timeout=config.timeout,
                follow_redirects=False,  # Manual redirect handling for SSRF safety
            ) as response:
                # Handle redirects manually with SSRF validation
                if response.status_code in (301, 302, 303, 307, 308):
                    redirect_count += 1
                    if redirect_count > _SSRF_CONFIG.max_redirects:
                        raise SSRFBlockedError(
                            ref, 
                            "N/A", 
                            f"Exceeded maximum redirects ({_SSRF_CONFIG.max_redirects})"
                        )
                    
                    location = response.headers.get("location")
                    if not location:
                        raise SSRFBlockedError(ref, "N/A", "Redirect without Location header")
                    
                    # Handle relative redirects
                    if location.startswith("/"):
                        from urllib.parse import urlparse, urlunparse
                        parsed = urlparse(current_url)
                        location = urlunparse((parsed.scheme, parsed.netloc, location, "", "", ""))
                    
                    # SSRF Check 2: Validate redirect target before following
                    logger.debug(f"Validating redirect #{redirect_count}: {location}")
                    validate_url_target(location, _SSRF_CONFIG)
                    
                    current_url = location
                    continue  # Follow the redirect
                
                # Check for retryable status codes BEFORE reading body
                if response.status_code in config.retryable_statuses:
                    raise RetryableHTTPError(
                        response.status_code,
                        f"Retryable status for {ref}"
                    )
                
                # Fail fast on other errors
                response.raise_for_status()
                
                # Validate content-type
                content_type = response.headers.get("content-type", "application/octet-stream")
                base_content_type = content_type.split(";")[0].strip().lower()
                if not _is_allowed_content_type(base_content_type, config.allowed_content_types):
                    raise InvalidContentTypeError(ref, content_type, config.allowed_content_types)
                
                # V5 Memory Optimization: Stream to temp file, then read
                # This avoids holding multiple in-memory copies:
                # - No byte chunks list accumulation
                # - No b"".join() copy
                # - Single file read instead of decode from accumulated bytes
                # Memory footprint: ~1 chunk (64KB) + final string (vs 3x spec size before)
                total_bytes = 0
                temp_fd = None
                temp_path = None
                try:
                    # Create temp file (auto-deleted on close)
                    temp_fd, temp_path = tempfile.mkstemp(suffix=".spec", prefix="ic_fetch_")
                    
                    for chunk in response.iter_bytes(chunk_size=65536):  # 64KB chunks
                        # Check for cancellation during large downloads
                        if is_shutdown_requested():
                            raise FetchCancelledError(f"Fetch of {ref} cancelled due to shutdown")
                        
                        total_bytes += len(chunk)
                        if total_bytes > config.max_bytes:
                            raise SpecTooLargeError(ref, total_bytes, config.max_bytes)
                        
                        os.write(temp_fd, chunk)
                    
                    os.close(temp_fd)
                    temp_fd = None  # Mark as closed
                    
                    # Read content from temp file (single allocation)
                    content = Path(temp_path).read_text(encoding="utf-8")
                    logger.debug(f"Fetched {total_bytes:,} bytes from {ref} (redirects: {redirect_count})")
                    return content, content_type
                    
                finally:
                    # Cleanup: close fd if still open, remove temp file
                    if temp_fd is not None:
                        try:
                            os.close(temp_fd)
                        except OSError:
                            pass
                    if temp_path is not None:
                        try:
                            os.unlink(temp_path)
                        except OSError:
                            pass
                
        except httpx.HTTPStatusError as e:
            # Convert to RetryableHTTPError if appropriate
            if e.response.status_code in config.retryable_statuses:
                raise RetryableHTTPError(e.response.status_code, str(e)) from e
            raise


def _chunk_content(content: str, chunk_size: int = 1000) -> list[str]:
    """
    Split content into chunks on section boundaries or size limit.
    """
    chunks = []
    lines = content.split("\n")
    current_chunk = []
    current_size = 0

    for line in lines:
        current_chunk.append(line)
        current_size += len(line) + 1  # +1 for newline

        # Chunk on section markers or size limit
        if current_size >= chunk_size or line.strip().startswith("paths:") or line.strip().startswith("components:"):
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_size = 0

    # Add remaining content
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks if chunks else [content]


def ingest_spec(state: WorkflowState) -> WorkflowState:
    """
    Ingest all spec_refs (primary + supporting) into spec_documents and doc_chunks.

    API-002: Creates SourceRef objects for each spec, enabling traceability.
    
    V1.1 Spec Caching (FT-001):
    - Computes SHA-256 hash of spec content
    - Checks DB for existing spec_document with matching hash
    - If found (cache hit): Sets state.cache_hit=True, skips persistence
    - If not found (cache miss): Proceeds with normal persistence

    V3 Adaptive Streaming:
    - Automatically streams large specs (>500KB) to DB for memory efficiency
    - Small specs use fast in-memory mode
    - Override with STREAMING_PERSISTENCE=true/false

    Streaming Mode:
    - Chunks are streamed directly to spec_silver.spec_chunks
    - state.doc_chunks remains empty (memory efficient)
    - state.spec_chunk_ids holds the DB IDs instead
    - state.chunk_count holds the count for downstream nodes

    Legacy Mode:
    - Chunks are accumulated in state.doc_chunks (original behavior)
    - All data persisted in persist_silver_checkpoint

    Reads: spec_refs, plan, provider_code, options.no_cache
    Writes: source_refs, spec_documents, doc_chunks (legacy) OR spec_chunk_ids (streaming),
            plan["chunk_index_to_spec_document_uri"], cache_hit
    """
    if not state.spec_refs:
        state.errors.append("No spec_refs provided")
        state.completed_steps.append("ingest_spec")
        return state

    # Ensure outputs are initialized (some test fixtures construct WorkflowState
    # without these lists).
    if not state.spec_documents:
        state.spec_documents = []

    # API-002: Create SourceRef objects for each spec
    if not state.source_refs:
        state.source_refs = []
    
    for ref in state.spec_refs:
        source_ref = SourceRef.from_ref(ref, provider_code=state.provider_code)
        state.source_refs.append(source_ref)
    
    logger.debug(f"Created {len(state.source_refs)} SourceRef objects")

    # V1.1: Check if caching is disabled via CLI flag
    no_cache = False
    if state.options and hasattr(state.options, 'no_cache'):
        no_cache = state.options.no_cache
    
    # If streaming is explicitly disabled, use legacy mode
    if is_streaming_persistence_disabled():
        logger.debug("Streaming persistence disabled, using legacy mode")
        return _ingest_spec_legacy(state, no_cache=no_cache)
    
    # Keep a minimal in-memory handoff for downstream parsing.
    # Tests and some pipeline paths expect ingest_spec to populate pending_specs
    # with raw content, even when streaming persistence is enabled.
    if not getattr(state, "pending_specs", None):
        state.pending_specs = []

    # Fetch all specs first to determine total size
    fetched_specs = []
    total_bytes = 0
    cancelled = False
    
    for ref in state.spec_refs:
        # V4: Check for cancellation between specs
        if is_shutdown_requested():
            logger.warning(f"Shutdown requested during fetch, stopping after {len(fetched_specs)} specs")
            state.warnings.append(f"Ingestion stopped early due to shutdown after {len(fetched_specs)} specs")
            cancelled = True
            break
        
        try:
            # BUG-006: Pass repo_root for relative path resolution
            content, content_type = _fetch_spec_content(ref, repo_root=state.repo_root)
            fetched_specs.append((ref, content, content_type))
            total_bytes += len(content.encode("utf-8"))

            # Lightweight in-memory handoff for parse stage
            state.pending_specs.append({"ref": ref, "content": content, "content_type": content_type})
        except FetchCancelledError as e:
            # Cancellation during fetch is not an error, it's expected shutdown
            logger.warning(f"Fetch cancelled: {e}")
            state.warnings.append(f"Fetch of {ref} cancelled due to shutdown")
            cancelled = True
            break
        except Exception as e:
            state.errors.append(f"Failed to fetch spec from {ref}: {str(e)}")
    
    if not fetched_specs:
        state.errors.append("No specs could be fetched")
        state.completed_steps.append("ingest_spec")
        return state
    
    # V1.1: Check cache BEFORE deciding streaming mode
    if not no_cache:
        cache_result = _check_spec_cache(fetched_specs, state.provider_code)
        if cache_result:
            logger.info(f"Spec cache hit! Using existing spec_document id={cache_result.id}")
            state.spec_documents = [cache_result]
            state.cache_hit = True
            state.completed_steps.append("ingest_spec")
            return state
    
    # Cache miss - proceed with normal ingestion
    state.cache_hit = False
    
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


def _check_spec_cache(
    fetched_specs: List[Tuple[str, str, str]],
    provider_code: str = None,
) -> SpecDocument | None:
    """
    V1.1 (FT-001): Check if specs already exist in the database.
    
    Computes SHA-256 hash of all fetched spec content and checks
    if a matching spec_document exists in the database.
    
    Args:
        fetched_specs: List of (uri, content, content_type) tuples
        provider_code: Optional provider code for filtering
        
    Returns:
        SpecDocument if cache hit, None if cache miss
    """
    from integration_coworker.persistence import db
    from integration_coworker.persistence.sql_helpers import get_engine_type
    
    if not fetched_specs:
        return None
    
    # For now, we only cache single-spec scenarios
    # Multi-spec caching is more complex (need to match ALL specs)
    if len(fetched_specs) > 1:
        logger.debug("Multi-spec scenario, skipping cache check")
        return None
    
    ref, content, content_type = fetched_specs[0]
    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    
    try:
        db.init_schema()
        # V27-003 Fix: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            engine = get_engine_type()
            cur = conn.cursor()
            
            # IMPORTANT: Scope cache hits to provider_code when available.
            # The Silver layer (endpoints/schemas/entities) is keyed by source_system
            # which is provider-specific. Reusing a spec_document row from a different
            # provider_code can yield a "cache hit" but no corresponding endpoints.
            if engine == "postgres":
                if provider_code:
                    cur.execute("""
                        SELECT sd.id, sd.source_system_id, sd.version, sd.uri, sd.content_type, sd.sha256
                        FROM spec_silver.spec_documents sd
                        JOIN spec_silver.source_systems ss ON ss.id = sd.source_system_id
                        WHERE sd.sha256 = %s AND ss.code = %s
                        ORDER BY sd.id DESC
                        LIMIT 1
                    """, (sha256, provider_code))
                else:
                    cur.execute("""
                        SELECT id, source_system_id, version, uri, content_type, sha256
                        FROM spec_silver.spec_documents
                        WHERE sha256 = %s
                        ORDER BY id DESC
                        LIMIT 1
                    """, (sha256,))
            else:
                cur.execute("""
                    SELECT id, source_system_id, version, uri, content_type, sha256
                    FROM spec_documents
                    WHERE sha256 = ?
                    ORDER BY id DESC
                    LIMIT 1
                """, (sha256,))
            
            row = cur.fetchone()
        
        if row:
            logger.info(f"Spec cache hit: sha256={sha256[:16]}... -> id={row[0]}")
            return SpecDocument(
                id=row[0],
                source_system_id=row[1],
                version=row[2],
                uri=row[3],
                content_type=row[4],
                sha256=row[5],
                content=content,  # Provide content for downstream parsing
            )
        else:
            logger.debug(f"Spec cache miss: sha256={sha256[:16]}...")
            return None
            
    except Exception as e:
        logger.warning(f"Spec cache check failed: {e}")
        return None


def _ingest_spec_legacy(state: WorkflowState, no_cache: bool = False) -> WorkflowState:
    """
    Legacy ingest mode: accumulate chunks in memory.
    
    Original behavior - all chunks held in state.doc_chunks until
    persist_silver_checkpoint writes them to DB.
    
    Note: This fetches specs itself. For pre-fetched specs, use
    _ingest_spec_legacy_with_fetched().
    
    Args:
        state: WorkflowState to update
        no_cache: If True, skip cache check (V1.1 FT-001)
    """
    # Fetch specs first
    fetched_specs = []
    for ref in state.spec_refs:
        # V4: Check for cancellation between specs
        if is_shutdown_requested():
            logger.warning(f"Shutdown requested during fetch, stopping after {len(fetched_specs)} specs")
            state.warnings.append(f"Ingestion stopped early due to shutdown after {len(fetched_specs)} specs")
            break
        
        try:
            # BUG-006: Pass repo_root for relative path resolution
            content, content_type = _fetch_spec_content(ref, repo_root=state.repo_root)
            fetched_specs.append((ref, content, content_type))
        except FetchCancelledError as e:
            # Cancellation during fetch is not an error
            logger.warning(f"Fetch cancelled: {e}")
            state.warnings.append(f"Fetch cancelled due to shutdown")
            break
        except Exception as e:
            state.errors.append(f"Failed to fetch spec from {ref}: {str(e)}")
    
    if not fetched_specs:
        state.errors.append("No specs could be fetched")
        state.completed_steps.append("ingest_spec")
        return state
    
    # V1.1: Check cache before proceeding
    if not no_cache:
        cache_result = _check_spec_cache(fetched_specs, state.provider_code)
        if cache_result:
            logger.info(f"Spec cache hit! Using existing spec_document id={cache_result.id}")
            state.spec_documents = [cache_result]
            state.cache_hit = True
            state.completed_steps.append("ingest_spec")
            return state
    
    state.cache_hit = False
    
    all_chunks: list[str] = []
    chunk_index_to_uri: dict[int, str] = {}

    for ref, content, content_type in fetched_specs:
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Create SpecDocument for this ref
        spec_doc = SpecDocument(
            id=None,
            source_system_id=None,
            version="1.0",
            uri=ref,
            content_type=content_type,
            sha256=sha256,
            content=content,
        )
        state.spec_documents.append(spec_doc)

        # Chunk this document
        doc_chunks = _chunk_content(content)

        # Track which chunks belong to which spec document
        start_idx = len(all_chunks)
        for i, chunk in enumerate(doc_chunks):
            chunk_index_to_uri[start_idx + i] = ref
        all_chunks.extend(doc_chunks)

    state.doc_chunks = all_chunks

    # Store mapping in plan for downstream nodes (embed_spec_chunks, persist)
    if state.plan is not None:
        state.plan["chunk_index_to_spec_document_uri"] = chunk_index_to_uri

    state.completed_steps.append("ingest_spec")
    return state


def _ingest_spec_legacy_with_fetched(
    state: WorkflowState,
    fetched_specs: List[Tuple[str, str, str]],
) -> WorkflowState:
    """
    Legacy ingest mode with pre-fetched specs.
    
    API-002: Links each SpecDocument to its SourceRef for traceability.
    V2.1 (GAP-01): Stores raw spec bytes to Bronze layer for audit trail.
    
    Args:
        fetched_specs: List of (uri, content, content_type) tuples
    """
    from integration_coworker.persistence.streaming import stream_raw_spec_to_bronze
    from integration_coworker.persistence import db
    from integration_coworker.persistence.sql_helpers import upsert_ignore, upsert_update, select_by_columns, get_engine_type
    
    all_chunks: list[str] = []
    chunk_index_to_uri: dict[int, str] = {}
    
    # Build URI -> SourceRef mapping (handle backwards compatibility)
    uri_to_source_ref = {}
    for sr in state.source_refs:
        # Handle both SourceRef objects and legacy string refs
        if hasattr(sr, 'uri'):
            uri_to_source_ref[sr.uri] = sr
        elif isinstance(sr, str):
            uri_to_source_ref[sr] = None  # Legacy: no SourceRef object

    # Get or create source_system FIRST so we have the ID for Bronze storage
    db.init_schema()
    engine = get_engine_type()
    schema = "spec_silver" if engine == "postgres" else None
    provider_code = state.provider_code or "unknown"
    # V38-004: Include base_url for URL preservation
    # Use upsert_update to ensure base_url gets updated if source_system exists
    base_url = state.api_base_url or ""
    # V27-003 Fix: Use context manager to prevent connection leaks
    with db.get_connection() as conn:
        cur = conn.cursor()
        sql = upsert_update("source_systems", ["code", "display_name", "base_url"], ["code"], ["base_url"], schema)
        cur.execute(sql, (provider_code, provider_code.replace("_", " ").title(), base_url))
        sql = select_by_columns("source_systems", ["id"], ["code"], schema)
        cur.execute(sql, (provider_code,))
        source_system_id = cur.fetchone()[0]
        conn.commit()

    for ref, content, content_type in fetched_specs:
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # V2.1 (GAP-01): Store raw spec to Bronze layer for audit trail
        try:
            raw_spec_id = stream_raw_spec_to_bronze(
                content=content,
                uri=ref,
                content_type=content_type,
                source_system_id=source_system_id,
            )
            logger.debug(f"Stored raw spec to bronze layer: {ref} (id={raw_spec_id})")
        except Exception as e:
            # Non-fatal: Bronze storage is for audit, not critical path
            logger.warning(f"Failed to store raw spec to bronze layer: {e}")

        # V1.1 (FT-001): Persist spec_document NOW for cache lookups
        # Previously this was only done in persist_silver_checkpoint, but that
        # runs AFTER ingest_spec, so cache checks would always miss.
        # P0.1 Fix: Include repo_root for composite unique constraint
        spec_document_id = None
        repo_root_str = str(state.repo_root) if state.repo_root else "__legacy__"
        # V27-003 Fix: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            cur = conn.cursor()
            # V38-003 Fix: Use ON CONFLICT DO NOTHING without specifying columns
            # The spec_documents table has TWO unique constraints:
            #   1. UNIQUE (source_system_id, sha256)
            #   2. UNIQUE (repo_root, uri)
            # Using ON CONFLICT without columns catches violations on EITHER constraint
            sql = upsert_ignore(
                "spec_documents",
                ["source_system_id", "uri", "sha256", "content_type", "repo_root"],
                None,  # V38-003: DO NOTHING on ANY unique constraint violation
                schema
            )
            cur.execute(sql, (source_system_id, ref, sha256, content_type, repo_root_str))
            # V38-003 Fix: Try both unique constraints to find the row
            sql = select_by_columns("spec_documents", ["id"], ["repo_root", "uri"], schema)
            cur.execute(sql, (repo_root_str, ref))
            row = cur.fetchone()
            if not row:
                # Row exists by (source_system_id, sha256) constraint instead
                sql = select_by_columns("spec_documents", ["id"], ["source_system_id", "sha256"], schema)
                cur.execute(sql, (source_system_id, sha256))
                row = cur.fetchone()
            spec_document_id = row[0] if row else None
            if not spec_document_id:
                raise RuntimeError(f"V38-003: Could not find spec_document after insert for uri={ref}")
            conn.commit()

        # Create SpecDocument for this ref with persisted ID
        spec_doc = SpecDocument(
            id=spec_document_id,
            source_system_id=source_system_id,
            version="1.0",
            uri=ref,
            content_type=content_type,
            sha256=sha256,
            content=content,
        )
        
        # API-002: Link to SourceRef
        source_ref = uri_to_source_ref.get(ref)
        if source_ref:
            spec_doc._source_ref = source_ref
        
        state.spec_documents.append(spec_doc)
        
        # V23-CACHE: Set primary_spec_document_id for FK propagation (survives state_gc)
        if state.primary_spec_document_id is None and spec_document_id is not None:
            state.primary_spec_document_id = spec_document_id
            logger.debug(f"Set primary_spec_document_id={spec_document_id}")

        # Chunk this document
        doc_chunks = _chunk_content(content)

        # Track which chunks belong to which spec document
        start_idx = len(all_chunks)
        for i, chunk in enumerate(doc_chunks):
            chunk_index_to_uri[start_idx + i] = ref
        all_chunks.extend(doc_chunks)

    state.doc_chunks = all_chunks

    # Store mapping in plan for downstream nodes
    if state.plan is not None:
        state.plan["chunk_index_to_spec_document_uri"] = chunk_index_to_uri

    state.completed_steps.append("ingest_spec")
    return state


def _ingest_spec_streaming(state: WorkflowState) -> WorkflowState:
    """
    V3 Streaming ingest mode: stream chunks to DB immediately.
    
    Reduces memory from 200MB+ to <20MB by:
    1. Writing chunks to DB as they're created
    2. Clearing state.doc_chunks (keeping it empty)
    3. Storing chunk IDs in state.spec_chunk_ids instead
    
    Note: This fetches specs itself. For pre-fetched specs, use
    _ingest_spec_streaming_with_fetched().
    """
    fetched_specs = []
    for ref in state.spec_refs:
        # V4: Check for cancellation between specs
        if is_shutdown_requested():
            logger.warning(f"Shutdown requested during fetch, stopping after {len(fetched_specs)} specs")
            state.warnings.append(f"Ingestion stopped early due to shutdown after {len(fetched_specs)} specs")
            break
        
        try:
            # BUG-006: Pass repo_root for relative path resolution
            content, content_type = _fetch_spec_content(ref, repo_root=state.repo_root)
            fetched_specs.append((ref, content, content_type))
        except FetchCancelledError as e:
            logger.warning(f"Fetch cancelled: {e}")
            state.warnings.append(f"Fetch cancelled due to shutdown")
            break
        except Exception as e:
            state.errors.append(f"Failed to fetch spec from {ref}: {str(e)}")
            logger.error(f"Failed to fetch spec from {ref}: {e}")
    
    return _ingest_spec_streaming_with_fetched(state, fetched_specs)


def _ingest_spec_streaming_with_fetched(
    state: WorkflowState,
    fetched_specs: List[Tuple[str, str, str]],
) -> WorkflowState:
    """
    V3 Streaming ingest mode with pre-fetched specs.
    
    V2.1 (GAP-01): Also stores raw spec bytes to Bronze layer for audit trail.
    V4: Added progress tracking and cancellation support for crash recovery.
    
    Args:
        fetched_specs: List of (uri, content, content_type) tuples
    """
    from integration_coworker.persistence import db
    from integration_coworker.persistence.streaming import (
        stream_chunks_to_silver_with_progress,
        stream_raw_spec_to_bronze,
    )
    from integration_coworker.persistence.sql_helpers import upsert_ignore, upsert_update, select_by_columns, get_engine_type

    # Initialize schema for streaming writes
    db.init_schema()
    
    chunk_index_to_uri: dict[int, str] = {}
    all_chunk_ids: list[int] = []
    total_chunk_count = 0
    global_chunk_index = 0

    engine = get_engine_type()
    schema = "spec_silver" if engine == "postgres" else None
    
    # V4: Get run_id for progress tracking (if available)
    run_id = getattr(state, 'run_id', None)

    # Get or create source_system FIRST so we have the ID for all operations
    provider_code = state.provider_code or "unknown"
    # V38-004: Include base_url for URL preservation
    # Use upsert_update to ensure base_url gets updated if source_system exists
    base_url = state.api_base_url or ""
    # V27-003 Fix: Use context manager to prevent connection leaks
    with db.get_connection() as conn:
        cur = conn.cursor()
        sql = upsert_update("source_systems", ["code", "display_name", "base_url"], ["code"], ["base_url"], schema)
        cur.execute(sql, (provider_code, provider_code.replace("_", " ").title(), base_url))
        sql = select_by_columns("source_systems", ["id"], ["code"], schema)
        cur.execute(sql, (provider_code,))
        source_system_id = cur.fetchone()[0]
        conn.commit()

    for ref, content, content_type in fetched_specs:
        # V4: Check for cancellation between specs
        if is_shutdown_requested():
            logger.warning(f"Shutdown requested during streaming ingest")
            state.warnings.append("Streaming ingest stopped early due to shutdown")
            break
        
        try:
            sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

            # V2.1 (GAP-01): Store raw spec to Bronze layer for audit trail
            try:
                raw_spec_id = stream_raw_spec_to_bronze(
                    content=content,
                    uri=ref,
                    content_type=content_type,
                    source_system_id=source_system_id,
                )
                logger.debug(f"Stored raw spec to bronze layer: {ref} (id={raw_spec_id})")
            except Exception as e:
                # Non-fatal: Bronze storage is for audit, not critical path
                logger.warning(f"Failed to store raw spec to bronze layer: {e}")

            # Create SpecDocument for this ref (content stored for parsing, will be cleared later)
            spec_doc = SpecDocument(
                id=None,
                source_system_id=source_system_id,
                version="1.0",
                uri=ref,
                content_type=content_type,
                sha256=sha256,
                content=content,  # Needed for detect_and_parse_spec
            )
            state.spec_documents.append(spec_doc)

            # We need to persist the spec_document to get an ID
            # P0.1 Fix: Include repo_root for composite unique constraint
            # Bug #94 Fix: Use correct conflict columns (repo_root, uri) per migration 002
            # V27-003 Fix: Use context manager to prevent connection leaks
            repo_root_str = str(state.repo_root) if state.repo_root else "__legacy__"
            with db.get_connection() as conn:
                cur = conn.cursor()
                
                # V38-003 Fix: Use ON CONFLICT DO NOTHING without specifying columns
                # The spec_documents table has TWO unique constraints:
                #   1. UNIQUE (source_system_id, sha256)
                #   2. UNIQUE (repo_root, uri)
                sql = upsert_ignore(
                    "spec_documents",
                    ["source_system_id", "uri", "sha256", "content_type", "repo_root"],
                    None,  # V38-003: DO NOTHING on ANY unique constraint violation
                    schema
                )
                cur.execute(sql, (source_system_id, ref, sha256, content_type, repo_root_str))
                # V38-003 Fix: Try both unique constraints to find the row
                sql = select_by_columns("spec_documents", ["id"], ["repo_root", "uri"], schema)
                cur.execute(sql, (repo_root_str, ref))
                row = cur.fetchone()
                if not row:
                    # Row exists by (source_system_id, sha256) constraint instead
                    sql = select_by_columns("spec_documents", ["id"], ["source_system_id", "sha256"], schema)
                    cur.execute(sql, (source_system_id, sha256))
                    row = cur.fetchone()
                spec_document_id = row[0] if row else None
                if not spec_document_id:
                    raise RuntimeError(f"V38-003: Could not find spec_document after insert for uri={ref}")
                
                conn.commit()
            
            # Backfill IDs
            spec_doc.id = spec_document_id
            spec_doc.source_system_id = source_system_id
            
            # V23-CACHE: Set primary_spec_document_id for FK propagation (survives state_gc)
            if state.primary_spec_document_id is None and spec_document_id is not None:
                state.primary_spec_document_id = spec_document_id
                logger.debug(f"Set primary_spec_document_id={spec_document_id}")

            # Chunk this document
            doc_chunks = _chunk_content(content)

            # Stream chunks to DB immediately
            def chunk_iterator() -> Iterator[Tuple[int, str]]:
                nonlocal global_chunk_index
                for chunk in doc_chunks:
                    yield (global_chunk_index, chunk)
                    global_chunk_index += 1

            # Track URI mapping before streaming
            start_idx = total_chunk_count
            for i in range(len(doc_chunks)):
                chunk_index_to_uri[start_idx + i] = ref

            # V4: Stream chunks with progress tracking for crash recovery
            chunk_ids = stream_chunks_to_silver_with_progress(
                chunk_iterator(),
                spec_document_id,
                run_id=run_id,
                total_chunks=len(doc_chunks),
            )
            all_chunk_ids.extend(chunk_ids)
            total_chunk_count += len(doc_chunks)
            
            # Reset global_chunk_index for next document (we used it in iterator)
            # Actually, no - we want global indices across all docs

            logger.info(f"Streamed {len(doc_chunks)} chunks for {ref} (spec_document_id={spec_document_id})")

        except Exception as e:
            state.errors.append(f"Failed to ingest spec from {ref}: {str(e)}")
            logger.error(f"Failed to ingest spec from {ref}: {e}")

    # In streaming mode, keep doc_chunks empty
    state.doc_chunks = []
    
    # Store chunk IDs for downstream nodes
    state.spec_chunk_ids = all_chunk_ids
    state.chunk_count = total_chunk_count

    # Store mapping in plan for downstream nodes
    if state.plan is not None:
        state.plan["chunk_index_to_spec_document_uri"] = chunk_index_to_uri
        state.plan["streaming_mode"] = True

    # Mark that chunks are already persisted
    state.persisted_ids["chunks_streamed"] = True
    state.persisted_ids["chunk_count"] = total_chunk_count

    state.completed_steps.append("ingest_spec")
    logger.info(f"Streaming ingest complete: {total_chunk_count} chunks streamed to DB")
    return state
