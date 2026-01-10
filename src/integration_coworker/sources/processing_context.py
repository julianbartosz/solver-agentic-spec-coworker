"""
File processing context for enterprise-scale operations.

This module provides the core abstractions for:
- Cancellation: CancelToken for cooperative cancellation
- Timeouts: TimeoutContext for deadline enforcement
- Size Gating: FileSizeGate for preflight file size validation
- Combined: ProcessingContext that composes all three

All abstractions are designed to be:
- Thread-safe
- Low overhead (no subprocess unless opt-in)
- Composable (use any combination)
- Backwards compatible (all parameters optional)

Usage:
    from integration_coworker.sources.processing_context import (
        CancelToken,
        ProcessingContext,
        FileProcessingConfig,
    )
    
    # Create context with defaults
    ctx = ProcessingContext.from_config()
    
    # Or with custom settings
    token = CancelToken()
    ctx = ProcessingContext(
        cancel_token=token,
        timeout_seconds=60.0,
        max_file_size_bytes=100 * 1024 * 1024,
    )
    
    # Use in parse loops
    for i, row in enumerate(rows):
        ctx.check()  # Raises on cancel/timeout
        process(row)
    
    # Cancel from another thread
    token.cancel("User requested stop")

Per docs/FILE_PROCESSING_HARDENING_DESIGN.md
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Union

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class FileProcessingError(Exception):
    """Base exception for file processing errors."""
    pass


class CancelledException(FileProcessingError):
    """Raised when processing is cancelled via CancelToken."""
    
    def __init__(self, reason: str = "Processing cancelled"):
        self.reason = reason
        super().__init__(reason)


class FileProcessingTimeout(FileProcessingError):
    """Raised when processing exceeds timeout."""
    
    def __init__(self, timeout_seconds: float, elapsed_seconds: float):
        self.timeout_seconds = timeout_seconds
        self.elapsed_seconds = elapsed_seconds
        super().__init__(
            f"Processing timed out after {elapsed_seconds:.1f}s "
            f"(limit: {timeout_seconds:.1f}s)"
        )


class FileTooLargeError(FileProcessingError):
    """Raised when file exceeds size limit."""
    
    def __init__(
        self,
        file_size_bytes: int,
        max_size_bytes: int,
        uri: str = "",
    ):
        self.file_size_bytes = file_size_bytes
        self.max_size_bytes = max_size_bytes
        self.uri = uri
        
        file_mb = file_size_bytes / (1024 * 1024)
        max_mb = max_size_bytes / (1024 * 1024)
        
        message = (
            f"File '{uri}' is {file_mb:.1f}MB, exceeds maximum {max_mb:.1f}MB. "
            f"Set FILE_MAX_SIZE_MB environment variable to increase limit, "
            f"or use streaming mode for large files."
        )
        super().__init__(message)


class BytesHandleTooLargeError(FileProcessingError):
    """
    Raised when BytesContentHandle is used for a file exceeding streaming threshold.
    
    This enforces the PATH-FIRST policy: for large files, users must use
    PathContentHandle to enable true streaming. BytesContentHandle already
    has the entire file in memory, defeating streaming benefits.
    
    The error message guides users to:
    1. Use content_handle_from_path() instead of content_handle_from_bytes()
    2. Pass file paths to the API instead of pre-loaded content
    
    Threshold is controlled by FILE_STREAMING_THRESHOLD_MB (default: 10MB)
    """
    
    def __init__(
        self,
        content_size_bytes: int,
        threshold_bytes: int,
        uri: str = "",
    ):
        self.content_size_bytes = content_size_bytes
        self.threshold_bytes = threshold_bytes
        self.uri = uri
        
        size_mb = content_size_bytes / (1024 * 1024)
        threshold_mb = threshold_bytes / (1024 * 1024)
        
        message = (
            f"BytesContentHandle with {size_mb:.1f}MB content exceeds streaming "
            f"threshold ({threshold_mb:.1f}MB). For files this large, use "
            f"PathContentHandle instead to enable true streaming. "
            f"Example: content_handle_from_path('{uri or 'path/to/file'}')"
        )
        super().__init__(message)


# =============================================================================
# CancelToken
# =============================================================================


class CancelToken:
    """
    Token for cooperative cancellation of file processing.
    
    Thread-safe. Can be shared across threads - one thread processes,
    another calls cancel().
    
    Usage:
        token = CancelToken()
        
        # In processing thread
        for row in rows:
            token.check()  # Raises CancelledException if cancelled
            process(row)
        
        # In control thread
        token.cancel("User requested stop")
    """
    
    def __init__(self):
        self._cancelled = threading.Event()
        self._reason: Optional[str] = None
        self._lock = threading.Lock()
    
    def cancel(self, reason: str = "Processing cancelled") -> None:
        """
        Request cancellation.
        
        Thread-safe. Can be called from any thread.
        
        Args:
            reason: Human-readable cancellation reason
        """
        with self._lock:
            if not self._cancelled.is_set():
                self._reason = reason
                self._cancelled.set()
                logger.debug(f"CancelToken: cancellation requested - {reason}")
    
    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancelled.is_set()
    
    @property
    def reason(self) -> Optional[str]:
        """Get cancellation reason, or None if not cancelled."""
        with self._lock:
            return self._reason if self._cancelled.is_set() else None
    
    def check(self) -> None:
        """
        Check if cancelled and raise if so.
        
        Call this periodically in processing loops.
        
        Raises:
            CancelledException: If cancellation was requested
        """
        if self._cancelled.is_set():
            raise CancelledException(self._reason or "Processing cancelled")
    
    def reset(self) -> None:
        """
        Reset token for reuse.
        
        Use with caution - only if you're certain no other code
        is still checking this token.
        """
        with self._lock:
            self._cancelled.clear()
            self._reason = None


# =============================================================================
# TimeoutContext
# =============================================================================


class TimeoutContext:
    """
    Context for deadline-based timeout enforcement.
    
    Uses cooperative checking (not signals or processes) for portability.
    Call check() periodically in processing loops.
    
    Usage:
        ctx = TimeoutContext(timeout_seconds=60.0)
        
        for row in rows:
            ctx.check()  # Raises FileProcessingTimeout if deadline passed
            process(row)
    """
    
    def __init__(self, timeout_seconds: float):
        """
        Initialize timeout context.
        
        Args:
            timeout_seconds: Maximum allowed processing time
        """
        self.timeout_seconds = timeout_seconds
        self.start_time = time.monotonic()
        self.deadline = self.start_time + timeout_seconds
    
    @property
    def elapsed_seconds(self) -> float:
        """Get elapsed time since context creation."""
        return time.monotonic() - self.start_time
    
    @property
    def remaining_seconds(self) -> float:
        """Get remaining time until deadline."""
        return max(0.0, self.deadline - time.monotonic())
    
    @property
    def is_expired(self) -> bool:
        """Check if deadline has passed."""
        return time.monotonic() > self.deadline
    
    def check(self) -> None:
        """
        Check if deadline has passed and raise if so.
        
        Call this periodically in processing loops.
        
        Raises:
            FileProcessingTimeout: If deadline has passed
        """
        if self.is_expired:
            raise FileProcessingTimeout(
                timeout_seconds=self.timeout_seconds,
                elapsed_seconds=self.elapsed_seconds,
            )
    
    def reset(self) -> None:
        """Reset timeout with same duration."""
        self.start_time = time.monotonic()
        self.deadline = self.start_time + self.timeout_seconds


# =============================================================================
# FileSizeGate
# =============================================================================


class FileSizeGate:
    """
    Preflight file size validation.
    
    Checks file size before processing to fail fast on oversized files.
    Returns warnings for large (but acceptable) files.
    
    Usage:
        gate = FileSizeGate(max_size_bytes=100*1024*1024)
        
        warnings = gate.check(content, uri="data.csv")
        # Returns [] if OK, ["warning..."] if large, raises if too large
    """
    
    def __init__(
        self,
        max_size_bytes: int = 500 * 1024 * 1024,   # 500MB default
        warn_size_bytes: int = 50 * 1024 * 1024,   # 50MB default
    ):
        """
        Initialize size gate.
        
        Args:
            max_size_bytes: Maximum allowed file size (raises if exceeded)
            warn_size_bytes: Threshold for warning (returns warning if exceeded)
        """
        self.max_size_bytes = max_size_bytes
        self.warn_size_bytes = warn_size_bytes
    
    def get_size(self, content: Union[bytes, str]) -> int:
        """Get size of content in bytes."""
        if isinstance(content, bytes):
            return len(content)
        else:
            # Approximate size for string (UTF-8 encoded)
            return len(content.encode('utf-8', errors='ignore'))
    
    def check(self, content: Union[bytes, str], uri: str = "") -> List[str]:
        """
        Check file size against limits.
        
        Args:
            content: File content (bytes or string)
            uri: Optional URI for error messages
            
        Returns:
            List of warning messages (empty if no warnings)
            
        Raises:
            FileTooLargeError: If content exceeds max_size_bytes
        """
        size = self.get_size(content)
        
        if size > self.max_size_bytes:
            raise FileTooLargeError(
                file_size_bytes=size,
                max_size_bytes=self.max_size_bytes,
                uri=uri,
            )
        
        warnings = []
        if size > self.warn_size_bytes:
            size_mb = size / (1024 * 1024)
            warnings.append(
                f"File '{uri}' is {size_mb:.1f}MB. "
                f"Consider streaming mode for better memory efficiency."
            )
        
        return warnings


# =============================================================================
# ProcessingContext (Combined)
# =============================================================================


@dataclass
class ProcessingContext:
    """
    Combined context for file processing with cancellation, timeout, and size gating.
    
    Composes CancelToken, TimeoutContext, and FileSizeGate into a single
    convenient interface. All components are optional.
    
    Usage:
        # From config (recommended)
        ctx = ProcessingContext.from_config()
        
        # Manual construction
        ctx = ProcessingContext(
            cancel_token=CancelToken(),
            timeout_seconds=60.0,
        )
        
        # Use in processing
        for row in rows:
            ctx.check()  # Checks all: cancel, timeout, etc.
            process(row)
    """
    
    cancel_token: Optional[CancelToken] = None
    timeout_ctx: Optional[TimeoutContext] = None
    size_gate: Optional[FileSizeGate] = None
    
    # Progress callback: called with (processed_items, total_items_estimate)
    progress_callback: Optional[Callable[[int, Optional[int]], None]] = None
    
    # Internal state
    _items_processed: int = field(default=0, init=False, repr=False)
    _check_interval: int = field(default=100, init=False, repr=False)
    
    @classmethod
    def from_config(cls, config: Optional["FileProcessingConfig"] = None) -> "ProcessingContext":
        """
        Create ProcessingContext from FileProcessingConfig.
        
        Args:
            config: Configuration, or None to use defaults
            
        Returns:
            Configured ProcessingContext
        """
        config = config or FileProcessingConfig()
        
        cancel_token = CancelToken() if config.enable_cancellation else None
        
        timeout_ctx = None
        if config.parse_timeout_s and config.parse_timeout_s > 0:
            timeout_ctx = TimeoutContext(config.parse_timeout_s)
        
        size_gate = None
        if config.max_size_bytes and config.max_size_bytes > 0:
            size_gate = FileSizeGate(
                max_size_bytes=config.max_size_bytes,
                warn_size_bytes=config.warn_size_bytes,
            )
        
        ctx = cls(
            cancel_token=cancel_token,
            timeout_ctx=timeout_ctx,
            size_gate=size_gate,
        )
        ctx._check_interval = config.cancel_check_interval_rows
        
        return ctx
    
    def check(self) -> None:
        """
        Check all active constraints (cancel, timeout).
        
        Call this periodically in processing loops.
        
        Raises:
            CancelledException: If cancelled
            FileProcessingTimeout: If timed out
        """
        if self.cancel_token:
            self.cancel_token.check()
        
        if self.timeout_ctx:
            self.timeout_ctx.check()
    
    def check_on_item(self, item_index: int, total_estimate: Optional[int] = None) -> None:
        """
        Check constraints and optionally report progress.
        
        More efficient than calling check() on every item - only checks
        every N items (configurable via check_interval).
        
        Args:
            item_index: Current item index (0-based)
            total_estimate: Optional total item count for progress
        """
        self._items_processed = item_index + 1
        
        # Only check every N items to reduce overhead
        if item_index % self._check_interval == 0:
            self.check()
            
            # Report progress if callback provided
            if self.progress_callback:
                self.progress_callback(self._items_processed, total_estimate)
    
    def check_size(self, content: Union[bytes, str], uri: str = "") -> List[str]:
        """
        Check file size and return warnings.
        
        Args:
            content: File content to check
            uri: Optional URI for error messages
            
        Returns:
            List of warning messages
            
        Raises:
            FileTooLargeError: If content exceeds max size
        """
        if self.size_gate:
            return self.size_gate.check(content, uri)
        return []
    
    def check_size_bytes(self, size_bytes: int, uri: str = "") -> List[str]:
        """
        Check file size by byte count BEFORE loading content.
        
        This is the PREFERRED method for pre-load size checking.
        Unlike check_size(), this doesn't require the content to be loaded,
        enabling rejection before memory allocation.
        
        Args:
            size_bytes: File size in bytes (e.g., from os.path.getsize or Content-Length)
            uri: Optional URI for error messages
            
        Returns:
            List of warning messages
            
        Raises:
            FileTooLargeError: If size exceeds max size
        """
        if not self.size_gate:
            return []
        
        if size_bytes > self.size_gate.max_size_bytes:
            raise FileTooLargeError(
                file_size_bytes=size_bytes,
                max_size_bytes=self.size_gate.max_size_bytes,
                uri=uri,
            )
        
        warnings = []
        if size_bytes > self.size_gate.warn_size_bytes:
            size_mb = size_bytes / (1024 * 1024)
            warnings.append(
                f"File '{uri}' is {size_mb:.1f}MB. "
                f"Consider streaming mode for better memory efficiency."
            )
        
        return warnings

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self.cancel_token is not None and self.cancel_token.is_cancelled
    
    @property
    def is_expired(self) -> bool:
        """Check if timeout has expired."""
        return self.timeout_ctx is not None and self.timeout_ctx.is_expired
    
    @property
    def elapsed_seconds(self) -> Optional[float]:
        """Get elapsed time if timeout tracking is enabled."""
        return self.timeout_ctx.elapsed_seconds if self.timeout_ctx else None
    
    @property
    def remaining_seconds(self) -> Optional[float]:
        """Get remaining time if timeout tracking is enabled."""
        return self.timeout_ctx.remaining_seconds if self.timeout_ctx else None


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class FileProcessingConfig:
    """
    Configuration for enterprise-scale file processing.
    
    Can be loaded from Settings system or constructed directly.
    
    Environment variable mapping:
    - FILE_MAX_SIZE_MB -> max_size_bytes
    - FILE_WARN_SIZE_MB -> warn_size_bytes
    - FILE_PARSE_TIMEOUT_S -> parse_timeout_s
    - FILE_DETECT_TIMEOUT_S -> detect_timeout_s
    - FILE_CHUNK_SIZE_MB -> chunk_size_bytes
    - FILE_SAMPLE_ROWS -> sample_rows
    - FILE_CANCEL_CHECK_INTERVAL -> cancel_check_interval_rows
    """
    
    # Size gating
    max_size_bytes: int = 500 * 1024 * 1024      # 500MB default
    warn_size_bytes: int = 50 * 1024 * 1024      # 50MB default
    
    # Timeouts
    parse_timeout_s: float = 300.0               # 5 minutes for parsing
    detect_timeout_s: float = 30.0               # 30 seconds for detection
    
    # Streaming (for future use with large files)
    chunk_size_bytes: int = 10 * 1024 * 1024     # 10MB chunks
    sample_rows: int = 1000                       # Rows to sample for inference
    small_file_threshold_bytes: int = 10 * 1024 * 1024  # Files under 10MB use full load
    
    # Cancellation
    enable_cancellation: bool = True
    cancel_check_interval_rows: int = 100        # Check every 100 rows
    
    # Progress reporting
    progress_report_interval_rows: int = 1000    # Report every 1000 rows
    
    @classmethod
    def from_env(cls) -> "FileProcessingConfig":
        """
        Load configuration from environment variables.
        
        Falls back to defaults for missing variables.
        """
        import os
        
        def get_int(name: str, default: int) -> int:
            val = os.environ.get(name)
            if val:
                try:
                    return int(val)
                except ValueError:
                    logger.warning(f"Invalid int for {name}: {val}, using default {default}")
            return default
        
        def get_float(name: str, default: float) -> float:
            val = os.environ.get(name)
            if val:
                try:
                    return float(val)
                except ValueError:
                    logger.warning(f"Invalid float for {name}: {val}, using default {default}")
            return default
        
        def get_bool(name: str, default: bool) -> bool:
            val = os.environ.get(name)
            if val:
                return val.lower() in ('true', '1', 'yes', 'on')
            return default
        
        return cls(
            max_size_bytes=get_int('FILE_MAX_SIZE_MB', 500) * 1024 * 1024,
            warn_size_bytes=get_int('FILE_WARN_SIZE_MB', 50) * 1024 * 1024,
            parse_timeout_s=get_float('FILE_PARSE_TIMEOUT_S', 300.0),
            detect_timeout_s=get_float('FILE_DETECT_TIMEOUT_S', 30.0),
            chunk_size_bytes=get_int('FILE_CHUNK_SIZE_MB', 10) * 1024 * 1024,
            sample_rows=get_int('FILE_SAMPLE_ROWS', 1000),
            small_file_threshold_bytes=get_int('FILE_SMALL_THRESHOLD_MB', 10) * 1024 * 1024,
            enable_cancellation=get_bool('FILE_ENABLE_CANCELLATION', True),
            cancel_check_interval_rows=get_int('FILE_CANCEL_CHECK_INTERVAL', 100),
            progress_report_interval_rows=get_int('FILE_PROGRESS_INTERVAL', 1000),
        )
    
    @classmethod
    def from_settings(cls) -> "FileProcessingConfig":
        """
        Load configuration from Settings system.
        
        Falls back to from_env() if Settings not available.
        """
        try:
            from integration_coworker.config import get_settings
            settings = get_settings()
            
            # Map settings attributes to config
            # Note: Settings may not have all these attributes yet
            return cls(
                max_size_bytes=getattr(settings, 'file_max_size_bytes', 500 * 1024 * 1024),
                warn_size_bytes=getattr(settings, 'file_warn_size_bytes', 50 * 1024 * 1024),
                parse_timeout_s=getattr(settings, 'file_parse_timeout_s', 300.0),
                detect_timeout_s=getattr(settings, 'file_detect_timeout_s', 30.0),
                chunk_size_bytes=getattr(settings, 'file_chunk_size_bytes', 10 * 1024 * 1024),
                sample_rows=getattr(settings, 'file_sample_rows', 1000),
                enable_cancellation=getattr(settings, 'file_enable_cancellation', True),
                cancel_check_interval_rows=getattr(settings, 'file_cancel_check_interval', 100),
            )
        except (ImportError, AttributeError) as e:
            logger.debug(f"Settings not available, falling back to env: {e}")
            return cls.from_env()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Exceptions
    "FileProcessingError",
    "CancelledException",
    "FileProcessingTimeout",
    "FileTooLargeError",
    # Core classes
    "CancelToken",
    "TimeoutContext",
    "FileSizeGate",
    "ProcessingContext",
    "FileProcessingConfig",
]
