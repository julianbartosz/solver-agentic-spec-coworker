"""
SpecSource plugin registry and detection.

This module implements the SpecSource plugin pattern for unified handling of
API specs, file specs, and document guides. All spec sources implement a common
protocol (detect, parse) and can be dynamically registered.

Enterprise-Scale Features (v2.0):
    - File size gating: Preflight rejection of oversized files
    - Timeout enforcement: Cooperative timeout on detect/parse
    - Cancellation support: CancelToken for graceful shutdown
    - Progress tracking: Optional callbacks for long operations

Hard Timeout Features (v2.1):
    - Process-based timeout: Subprocess with SIGTERM/SIGKILL for stuck operations
    - True streaming: Line-by-line parsing without full memory load
    - Path-first policy: Encourages PathContentHandle for large files

Usage:
    from integration_coworker.sources import detect_and_route, SOURCE_REGISTRY
    
    # Simple usage (backwards compatible)
    result = detect_and_route(content, uri, content_type)
    
    # With processing context (enterprise features)
    from integration_coworker.sources.processing_context import ProcessingContext
    
    ctx = ProcessingContext.from_config()
    result = detect_and_route(content, uri, content_type, processing_ctx=ctx)
    
    # Check result type
    if result.source_type == SourceType.API:
        openapi_spec = result.data
    elif result.source_type == SourceType.FILE:
        file_spec, fields = result.data["file_spec"], result.data["fields"]
"""

import logging
from typing import List, Optional, Tuple, Union

from .base import ParsedSpec, SourceType, SpecSource, StreamingSpecSource, supports_streaming
from .content_handle import (
    BytesContentHandle,
    ContentHandle,
    PathContentHandle,
    StreamContentHandle,
    content_handle_from_any,
    content_handle_from_bytes,
    content_handle_from_path,
    content_handle_from_stream,
    managed_content_handle,
)
from .hard_timeout_worker import (
    HardTimeoutConfig,
    HardTimeoutError,
    WorkerError,
    run_with_hard_timeout,
)
from .processing_context import (
    BytesHandleTooLargeError,
    CancelToken,
    CancelledException,
    FileProcessingConfig,
    FileProcessingTimeout,
    FileTooLargeError,
    ProcessingContext,
)

# Use structured logging with extra fields for filtering/alerting
# See docs/BUCKET_2_TECH_DEBT_AND_SCALING.md TD-OBS-001
logger = logging.getLogger(__name__)

# Default streaming threshold: BytesContentHandle above this triggers error
DEFAULT_STREAMING_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10MB

# Registry of all source handlers in detection priority order
# Higher priority sources are checked first
# OpenAPI has highest priority (we want explicit API specs to match first)
# File sources come next
# PDF guide source is last (most permissive matcher)
SOURCE_REGISTRY: List[SpecSource] = []


def register_source(source: SpecSource, priority: int = 50) -> None:
    """
    Register a source handler in the registry.
    
    Args:
        source: A SpecSource implementation
        priority: Higher = checked earlier (0-100)
    """
    SOURCE_REGISTRY.append((priority, source))
    SOURCE_REGISTRY.sort(key=lambda x: x[0], reverse=True)


def get_registered_sources() -> List[SpecSource]:
    """Get all registered sources in priority order."""
    return [source for _, source in SOURCE_REGISTRY]


def detect_and_route(
    content: Union[bytes, str],
    uri: str,
    content_type: str = "",
    processing_ctx: Optional[ProcessingContext] = None,
) -> ParsedSpec:
    """
    Detect content type and route to appropriate source handler.
    
    This is the main entry point for the unified detection system.
    It scores each registered source and uses the highest-confidence match.
    
    Enterprise Features (optional via processing_ctx):
        - File size gating: Rejects oversized files before processing
        - Timeout enforcement: Fails if detection/parsing exceeds limit
        - Cancellation: Checks cancel token between operations
    
    Args:
        content: Raw content (bytes or string)
        uri: Source URI (file path or URL)
        content_type: MIME type if known (optional)
        processing_ctx: Optional ProcessingContext for enterprise features
        
    Returns:
        ParsedSpec with source_type indicating 'api' or 'file'
        
    Raises:
        ValueError: If no source handler matched the content
        FileTooLargeError: If content exceeds size limit (when ctx provided)
        FileProcessingTimeout: If processing exceeds timeout (when ctx provided)
        CancelledException: If cancelled via token (when ctx provided)
    """
    if not SOURCE_REGISTRY:
        raise RuntimeError(
            "No source handlers registered. Import sources to register them."
        )
    
    # Determine content length for logging
    content_length = len(content) if isinstance(content, bytes) else len(content.encode('utf-8'))
    
    # === Enterprise: Preflight size check ===
    size_warnings = []
    if processing_ctx:
        size_warnings = processing_ctx.check_size(content, uri)
        # Any size warnings will be added to ParsedSpec later
    
    logger.debug(
        "source.detection.start",
        extra={
            "uri": uri,
            "content_type": content_type or "(none)",
            "content_length": content_length,
            "has_processing_ctx": processing_ctx is not None,
        }
    )
    
    # Score each source
    scores: List[Tuple[float, int, SpecSource]] = []
    all_scores: dict = {}  # For logging
    
    for priority, source in SOURCE_REGISTRY:
        # === Enterprise: Check cancellation/timeout between sources ===
        if processing_ctx:
            processing_ctx.check()
        
        source_name = source.__class__.__name__
        try:
            score = source.detect(content, uri, content_type)
            all_scores[source_name] = round(score, 3)
            if score > 0:
                scores.append((score, priority, source))
                logger.info(
                    "source.detection.score",
                    extra={
                        "source_class": source_name,
                        "uri": uri,
                        "score": round(score, 3),
                        "priority": priority,
                    }
                )
        except Exception as e:
            logger.warning(
                "source.detection.error",
                extra={
                    "source_class": source_name,
                    "uri": uri,
                    "error": str(e),
                }
            )
    
    if not scores:
        logger.warning(
            "source.detection.rejected",
            extra={
                "uri": uri,
                "all_scores": all_scores,
                "best_score": 0.0,
            }
        )
        raise ValueError(
            f"No source handler matched content from {uri}. "
            f"Registered sources: {[s.__class__.__name__ for _, s in SOURCE_REGISTRY]}"
        )
    
    # Use highest-scoring source (break ties by priority)
    scores.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, best_priority, best_source = scores[0]
    
    logger.info(
        "source.detection.selected",
        extra={
            "source_class": best_source.__class__.__name__,
            "uri": uri,
            "score": round(best_score, 3),
        }
    )
    
    # === Enterprise: Check before parsing ===
    if processing_ctx:
        processing_ctx.check()
    
    # Parse with selected source
    result = best_source.parse(content, uri)
    
    # === Enterprise: Add size warnings to result ===
    if size_warnings:
        result.warnings.extend(size_warnings)
    
    logger.info(
        "source.inference.complete",
        extra={
            "source_class": best_source.__class__.__name__,
            "uri": uri,
            "confidence": round(result.confidence, 3),
            "warning_count": len(result.warnings),
            "error_count": len(result.errors),
        }
    )
    
    return result


def enforce_path_first_policy(
    handle: ContentHandle,
    streaming_threshold_bytes: int = DEFAULT_STREAMING_THRESHOLD_BYTES,
    uri: str = "",
) -> None:
    """
    Enforce path-first policy for large files.
    
    For files above the streaming threshold, BytesContentHandle is rejected
    because it defeats the purpose of streaming (content already in memory).
    
    This guides users to use PathContentHandle instead:
    - Pass file paths to the API
    - Use content_handle_from_path() instead of content_handle_from_bytes()
    
    Args:
        handle: ContentHandle to check
        streaming_threshold_bytes: Size above which BytesContentHandle is rejected
        uri: Optional URI for error messages
        
    Raises:
        BytesHandleTooLargeError: If BytesContentHandle exceeds threshold
    """
    if not isinstance(handle, BytesContentHandle):
        # PathContentHandle and StreamContentHandle are fine at any size
        return
    
    content_size = handle.size_bytes()
    if content_size is None:
        return
    
    if content_size > streaming_threshold_bytes:
        raise BytesHandleTooLargeError(
            content_size_bytes=content_size,
            threshold_bytes=streaming_threshold_bytes,
            uri=uri or handle.uri,
        )


def detect_and_route_handle(
    handle: ContentHandle,
    uri: Optional[str] = None,
    content_type: str = "",
    processing_ctx: Optional[ProcessingContext] = None,
    enforce_path_first: bool = False,
    streaming_threshold_bytes: Optional[int] = None,
    hard_timeout_config: Optional[HardTimeoutConfig] = None,
) -> ParsedSpec:
    """
    Detect content type and route to appropriate source handler using ContentHandle.
    
    This is the CANONICAL entry point for enterprise-scale file processing.
    It enables:
        - PRE-LOAD size checking (reject before memory allocation)
        - True streaming for large files (PathContentHandle)
        - Path-first policy enforcement (optional)
        - Clean resource management via context manager
        - HARD TIMEOUT via subprocess (optional)
        
    Streaming Support (v2.1):
        If the selected source implements StreamingSpecSource, parsing will
        use parse_from_handle() for true streaming without loading the entire
        file into memory.
    
    Path-First Policy (v2.1):
        Set enforce_path_first=True to reject BytesContentHandle for files
        above streaming_threshold_bytes. This guides users to pass file paths
        instead of pre-loaded content, enabling true streaming benefits.
    
    Hard Timeout (v2.1):
        Pass hard_timeout_config to enable process-based hard timeout.
        When mode="process", parsing runs in a subprocess that can be killed
        with SIGTERM/SIGKILL if it exceeds the timeout.
        
        REQUIRES: PathContentHandle (or handle with to_local_path()).
        
        Architecture: SIGTERM → grace_seconds → SIGKILL
    
    Args:
        handle: ContentHandle for the content (PathContentHandle preferred)
        uri: Source URI (defaults to handle.uri if not provided)
        content_type: MIME type if known (optional)
        processing_ctx: Optional ProcessingContext for cooperative timeout/cancellation
        enforce_path_first: If True, reject large BytesContentHandle
        streaming_threshold_bytes: Size threshold for path-first policy (default: 10MB)
        hard_timeout_config: Optional HardTimeoutConfig for process-based timeout
                             None = no hard timeout (use processing_ctx for cooperative)
                             mode="off" = disabled
                             mode="process" = subprocess with SIGTERM/SIGKILL
        
    Returns:
        ParsedSpec with source_type indicating 'api' or 'file'
        
    Raises:
        ValueError: If no source handler matched, or hard timeout requires path
        FileTooLargeError: If content exceeds size limit (PRE-LOAD check!)
        BytesHandleTooLargeError: If BytesContentHandle exceeds streaming threshold
        FileProcessingTimeout: If processing exceeds cooperative timeout
        HardTimeoutError: If processing exceeds hard timeout (SIGTERM/SIGKILL)
        CancelledException: If cancelled via token
    
    Example:
        from integration_coworker.sources import (
            detect_and_route_handle,
            content_handle_from_path,
            ProcessingContext,
            HardTimeoutConfig,
        )
        
        # PRE-LOAD size check - rejects before reading!
        handle = content_handle_from_path("/data/large.csv")
        ctx = ProcessingContext.from_config()
        
        # With path-first enforcement + hard timeout
        result = detect_and_route_handle(
            handle,
            processing_ctx=ctx,
            enforce_path_first=True,
            hard_timeout_config=HardTimeoutConfig(
                mode="process",
                timeout_seconds=60.0,
            ),
        )
    """
    # === HARD TIMEOUT PATH ===
    # If hard timeout is enabled, delegate to subprocess execution
    if hard_timeout_config and hard_timeout_config.is_enabled:
        return _detect_and_route_handle_with_hard_timeout_internal(
            handle=handle,
            uri=uri,
            content_type=content_type,
            processing_ctx=processing_ctx,
            hard_timeout_config=hard_timeout_config,
        )
    if not SOURCE_REGISTRY:
        raise RuntimeError(
            "No source handlers registered. Import sources to register them."
        )
    
    # Use handle's URI if not provided
    if uri is None:
        uri = handle.uri
    
    # === PATH-FIRST POLICY ===
    # Reject BytesContentHandle for large files to encourage PathContentHandle
    if enforce_path_first:
        threshold = streaming_threshold_bytes or DEFAULT_STREAMING_THRESHOLD_BYTES
        enforce_path_first_policy(handle, threshold, uri)
    
    # === CRITICAL: PRE-LOAD size check ===
    # This is the key difference from detect_and_route()
    # We can check size BEFORE loading content into memory!
    size_warnings = []
    if processing_ctx:
        content_size = handle.size_bytes()
        if content_size is not None:
            # Check size using byte count (not actual content)
            # This allows rejection BEFORE memory allocation
            size_warnings = processing_ctx.check_size_bytes(content_size, uri)
    
    logger.debug(
        "source.handle.detection.start",
        extra={
            "uri": uri,
            "content_type": content_type or "(none)",
            "handle_type": type(handle).__name__,
            "size_bytes": handle.size_bytes(),
            "has_processing_ctx": processing_ctx is not None,
            "enforce_path_first": enforce_path_first,
        }
    )
    
    # Score each source
    # For sources supporting streaming, use detect_from_handle()
    # For legacy sources, fall back to sampling bytes
    scores: List[Tuple[float, int, SpecSource]] = []
    all_scores: dict = {}
    
    # Sample content for legacy sources (only if needed)
    # We defer this until we find a non-streaming source
    _sample_cache: Optional[bytes] = None
    
    def get_sample() -> bytes:
        nonlocal _sample_cache
        if _sample_cache is None:
            # Sample first 64KB for detection (enough for most formats)
            with handle.open_bytes() as f:
                _sample_cache = f.read(65536)
        return _sample_cache
    
    for priority, source in SOURCE_REGISTRY:
        if processing_ctx:
            processing_ctx.check()
        
        source_name = source.__class__.__name__
        try:
            # Check if source supports streaming detection
            if supports_streaming(source):
                score = source.detect_from_handle(handle, uri, content_type)
            else:
                # Legacy: use sampled bytes
                score = source.detect(get_sample(), uri, content_type)
            
            all_scores[source_name] = round(score, 3)
            if score > 0:
                scores.append((score, priority, source))
                logger.info(
                    "source.handle.detection.score",
                    extra={
                        "source_class": source_name,
                        "uri": uri,
                        "score": round(score, 3),
                        "streaming": supports_streaming(source),
                    }
                )
        except Exception as e:
            logger.warning(
                "source.handle.detection.error",
                extra={
                    "source_class": source_name,
                    "uri": uri,
                    "error": str(e),
                }
            )
    
    if not scores:
        raise ValueError(
            f"No source handler matched content from {uri}. "
            f"Registered sources: {[s.__class__.__name__ for _, s in SOURCE_REGISTRY]}"
        )
    
    scores.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, best_priority, best_source = scores[0]
    
    # Check if best source supports streaming
    use_streaming = supports_streaming(best_source)
    
    logger.info(
        "source.handle.detection.selected",
        extra={
            "source_class": best_source.__class__.__name__,
            "uri": uri,
            "score": round(best_score, 3),
            "streaming": use_streaming,
        }
    )
    
    if processing_ctx:
        processing_ctx.check()
    
    # Parse with selected source
    # Use streaming parse if available, otherwise fall back to bytes
    if use_streaming:
        # TRUE STREAMING: parse directly from handle
        logger.debug(
            "source.handle.parse.streaming",
            extra={
                "source_class": best_source.__class__.__name__,
                "uri": uri,
            }
        )
        result = best_source.parse_from_handle(handle, uri)
    else:
        # LEGACY: load bytes and parse
        logger.debug(
            "source.handle.parse.legacy",
            extra={
                "source_class": best_source.__class__.__name__,
                "uri": uri,
            }
        )
        content = handle.read_bytes()
        result = best_source.parse(content, uri)
    
    # Add size warnings to result
    if size_warnings:
        result.warnings.extend(size_warnings)
    
    logger.info(
        "source.handle.inference.complete",
        extra={
            "source_class": best_source.__class__.__name__,
            "uri": uri,
            "confidence": round(result.confidence, 3),
            "streaming": use_streaming,
        }
    )
    
    return result


def _detect_and_route_handle_with_hard_timeout_internal(
    handle: ContentHandle,
    uri: Optional[str] = None,
    content_type: str = "",
    processing_ctx: Optional[ProcessingContext] = None,
    hard_timeout_config: Optional[HardTimeoutConfig] = None,
) -> ParsedSpec:
    """
    Internal implementation of hard timeout detection+routing.
    
    Called by detect_and_route_handle() when hard_timeout_config is enabled.
    
    Architecture (SIGTERM → grace → SIGKILL):
        1. Subprocess spawned with parsing function
        2. Main process waits with timeout_seconds
        3. On timeout: send SIGTERM for graceful shutdown
        4. Wait grace_seconds for subprocess to exit
        5. If still alive: send SIGKILL (non-catchable)
        6. Return HardTimeoutError with was_killed=True
    
    REQUIREMENT: handle must have to_local_path() != None
    """
    config = hard_timeout_config or HardTimeoutConfig.from_env()
    uri = uri or handle.uri
    
    logger.info(
        "source.handle.hard_timeout.start",
        extra={
            "uri": uri,
            "timeout_seconds": config.timeout_seconds,
            "handle_type": type(handle).__name__,
        }
    )
    
    # Get local path - REQUIRED for process-based hard timeout
    local_path = handle.to_local_path()
    
    if local_path is None:
        # StreamContentHandle or corrupted handle - cannot use subprocess
        raise ValueError(
            "process hard-timeout requires PathContentHandle "
            "(or StreamContentHandle persisted to temp file). "
            f"Got {type(handle).__name__} with no local path. "
            "Use content_handle_from_path() for files, or persist the stream first."
        )
    
    # Run parsing in subprocess with hard timeout
    # NOTE: _parse_from_path_picklable is MODULE-LEVEL for spawn-safety
    result = run_with_hard_timeout(
        func=_parse_from_path_picklable,
        args=(str(local_path), uri, content_type),
        timeout_seconds=config.timeout_seconds,
        grace_seconds=config.grace_seconds,
        config=config,
    )
    
    logger.info(
        "source.handle.hard_timeout.complete",
        extra={
            "uri": uri,
            "source_type": result.source_type.value,
            "confidence": result.confidence,
        }
    )
    
    return result


def detect_and_route_handle_with_hard_timeout(
    handle: ContentHandle,
    uri: Optional[str] = None,
    content_type: str = "",
    processing_ctx: Optional[ProcessingContext] = None,
    hard_timeout_config: Optional[HardTimeoutConfig] = None,
) -> ParsedSpec:
    """
    LEGACY wrapper - use detect_and_route_handle() with hard_timeout_config instead.
    
    This function is kept for backwards compatibility. The canonical entry point
    is detect_and_route_handle() with the hard_timeout_config parameter.
    
    Example:
        # PREFERRED (canonical entry point):
        result = detect_and_route_handle(
            handle,
            hard_timeout_config=HardTimeoutConfig(mode="process"),
        )
        
        # LEGACY (still works):
        result = detect_and_route_handle_with_hard_timeout(
            handle,
            hard_timeout_config=HardTimeoutConfig(mode="process"),
        )
    """
    return detect_and_route_handle(
        handle=handle,
        uri=uri,
        content_type=content_type,
        processing_ctx=processing_ctx,
        hard_timeout_config=hard_timeout_config,
    )


# Module-level function for subprocess execution (must be picklable)
def _parse_from_path_picklable(
    path_str: str,
    uri: str,
    content_type: str,
) -> ParsedSpec:
    """
    Parse a file from path - picklable version for subprocess.
    
    This function is designed to be called via run_with_hard_timeout().
    It must:
    - Be at module level (not a nested function)
    - Only use picklable arguments
    - Not reference any non-picklable state
    """
    # Import inside function to avoid circular imports in subprocess
    from pathlib import Path
    from integration_coworker.sources import (
        detect_and_route_handle,
        content_handle_from_path,
        ensure_sources_registered,
    )
    
    # CRITICAL: Register sources in subprocess
    ensure_sources_registered()
    
    handle = content_handle_from_path(Path(path_str))
    return detect_and_route_handle(
        handle=handle,
        uri=uri,
        content_type=content_type,
        processing_ctx=None,  # Subprocess can't share context
    )


def detect_source_type(
    content: Union[bytes, str],
    uri: str,
    content_type: str = "",
) -> Optional[SourceType]:
    """
    Detect only the source type without parsing.
    
    Useful for routing decisions without full parsing overhead.
    
    Returns:
        SourceType.API, SourceType.FILE, or None if no match
    """
    if not SOURCE_REGISTRY:
        return None
    
    best_score = 0.0
    best_source = None
    
    for _, source in SOURCE_REGISTRY:
        try:
            score = source.detect(content, uri, content_type)
            if score > best_score:
                best_score = score
                best_source = source
        except Exception:
            pass
    
    if best_source is None:
        return None
    
    # Determine source type based on source class
    # This is a heuristic - sources should set this in their parsed output
    source_name = best_source.__class__.__name__.lower()
    if "openapi" in source_name or "api" in source_name:
        return SourceType.API
    return SourceType.FILE


# Import and register sources on module load
# This is done at the bottom to avoid circular imports
def _register_default_sources() -> None:
    """Register default source handlers."""
    try:
        from .openapi import OpenAPISource
        register_source(OpenAPISource(), priority=90)
    except ImportError:
        logger.debug("OpenAPISource not available")
    
    try:
        from .csv_source import CSVSource
        register_source(CSVSource(), priority=70)
    except ImportError:
        logger.debug("CSVSource not available")
    
    try:
        from .excel import ExcelSource
        register_source(ExcelSource(), priority=65)
    except ImportError:
        logger.debug("ExcelSource not available")
    
    try:
        from .fixed_width import FixedWidthSource
        register_source(FixedWidthSource(), priority=60)
    except ImportError:
        logger.debug("FixedWidthSource not available")
    
    try:
        from .pdf_guide import PDFGuideSource
        register_source(PDFGuideSource(), priority=40)
    except ImportError:
        logger.debug("PDFGuideSource not available")


# Defer registration until first use to avoid import errors during development
_sources_registered = False


def ensure_sources_registered() -> None:
    """Ensure default sources are registered."""
    global _sources_registered
    if not _sources_registered:
        _register_default_sources()
        _sources_registered = True


__all__ = [
    "ParsedSpec",
    "SourceType", 
    "SpecSource",
    "StreamingSpecSource",  # v2.1: Streaming-capable sources
    "supports_streaming",    # v2.1: Check if source supports streaming
    "SOURCE_REGISTRY",
    "register_source",
    "get_registered_sources",
    "detect_and_route",
    "detect_and_route_handle",  # PREFERRED: ContentHandle-based entry point
    "detect_and_route_handle_with_hard_timeout",  # v2.1: With subprocess timeout
    "enforce_path_first_policy",  # v2.1: Reject BytesContentHandle for large files
    "detect_source_type",
    "ensure_sources_registered",
    # ContentHandle abstraction (v2.1)
    "ContentHandle",
    "PathContentHandle",
    "BytesContentHandle",
    "StreamContentHandle",
    "content_handle_from_path",
    "content_handle_from_bytes",
    "content_handle_from_stream",
    "content_handle_from_any",
    "managed_content_handle",
    # Hard timeout (v2.1)
    "HardTimeoutConfig",
    "HardTimeoutError",
    "WorkerError",
    "run_with_hard_timeout",
    # Enterprise-scale processing (v2.0)
    "ProcessingContext",
    "FileProcessingConfig",
    "CancelToken",
    "CancelledException",
    "FileProcessingTimeout",
    "FileTooLargeError",
    "BytesHandleTooLargeError",  # v2.1: Path-first policy violation
]
