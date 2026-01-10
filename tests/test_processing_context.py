"""
Tests for file processing context (cancellation, timeout, size gating).

Tests the core abstractions for enterprise-scale file processing:
- CancelToken: cooperative cancellation
- TimeoutContext: deadline enforcement
- FileSizeGate: preflight size validation
- ProcessingContext: combined context
- FileProcessingConfig: configuration loading

Per docs/FILE_PROCESSING_HARDENING_DESIGN.md
"""

import os
import threading
import time
from unittest import mock

import pytest

from integration_coworker.sources.processing_context import (
    CancelToken,
    CancelledException,
    FileProcessingConfig,
    FileProcessingError,
    FileProcessingTimeout,
    FileSizeGate,
    FileTooLargeError,
    ProcessingContext,
    TimeoutContext,
)


# =============================================================================
# CancelToken Tests
# =============================================================================


class TestCancelToken:
    """Tests for CancelToken cooperative cancellation."""
    
    def test_initial_state_not_cancelled(self):
        """Token starts in non-cancelled state."""
        token = CancelToken()
        assert not token.is_cancelled
        assert token.reason is None
    
    def test_cancel_sets_cancelled_flag(self):
        """Calling cancel() sets the cancelled flag."""
        token = CancelToken()
        token.cancel("test reason")
        assert token.is_cancelled
        assert token.reason == "test reason"
    
    def test_cancel_with_default_reason(self):
        """Cancel with no reason uses default."""
        token = CancelToken()
        token.cancel()
        assert token.is_cancelled
        assert token.reason == "Processing cancelled"
    
    def test_check_does_not_raise_when_not_cancelled(self):
        """check() passes when not cancelled."""
        token = CancelToken()
        token.check()  # Should not raise
    
    def test_check_raises_when_cancelled(self):
        """check() raises CancelledException when cancelled."""
        token = CancelToken()
        token.cancel("test reason")
        
        with pytest.raises(CancelledException) as exc_info:
            token.check()
        
        assert "test reason" in str(exc_info.value)
    
    def test_cancel_is_idempotent(self):
        """Multiple cancels don't change reason."""
        token = CancelToken()
        token.cancel("first reason")
        token.cancel("second reason")  # Should be ignored
        
        assert token.reason == "first reason"
    
    def test_reset_clears_cancelled_state(self):
        """reset() clears cancelled state for reuse."""
        token = CancelToken()
        token.cancel("test")
        token.reset()
        
        assert not token.is_cancelled
        assert token.reason is None
        token.check()  # Should not raise
    
    def test_thread_safety_cancel_from_another_thread(self):
        """Token can be cancelled from another thread."""
        token = CancelToken()
        cancelled_in_loop = threading.Event()
        
        def worker():
            try:
                for i in range(1000):
                    token.check()
                    time.sleep(0.001)
            except CancelledException:
                cancelled_in_loop.set()
        
        thread = threading.Thread(target=worker)
        thread.start()
        
        time.sleep(0.01)  # Let worker start
        token.cancel("external cancel")
        thread.join(timeout=1.0)
        
        assert cancelled_in_loop.is_set()


# =============================================================================
# TimeoutContext Tests
# =============================================================================


class TestTimeoutContext:
    """Tests for TimeoutContext deadline enforcement."""
    
    def test_initial_state_not_expired(self):
        """Context starts not expired."""
        ctx = TimeoutContext(timeout_seconds=10.0)
        assert not ctx.is_expired
        assert ctx.remaining_seconds > 0
        assert ctx.elapsed_seconds < 1.0
    
    def test_check_does_not_raise_before_deadline(self):
        """check() passes before deadline."""
        ctx = TimeoutContext(timeout_seconds=10.0)
        ctx.check()  # Should not raise
    
    def test_check_raises_after_deadline(self):
        """check() raises FileProcessingTimeout after deadline."""
        ctx = TimeoutContext(timeout_seconds=0.01)  # 10ms
        time.sleep(0.02)  # Wait past deadline
        
        with pytest.raises(FileProcessingTimeout) as exc_info:
            ctx.check()
        
        assert exc_info.value.timeout_seconds == pytest.approx(0.01, abs=0.001)
        assert exc_info.value.elapsed_seconds >= 0.01
    
    def test_elapsed_seconds_increases(self):
        """elapsed_seconds increases over time."""
        ctx = TimeoutContext(timeout_seconds=10.0)
        t0 = ctx.elapsed_seconds
        time.sleep(0.05)
        t1 = ctx.elapsed_seconds
        
        assert t1 > t0
        assert t1 - t0 >= 0.04  # Allow some slack
    
    def test_remaining_seconds_decreases(self):
        """remaining_seconds decreases over time."""
        ctx = TimeoutContext(timeout_seconds=10.0)
        r0 = ctx.remaining_seconds
        time.sleep(0.05)
        r1 = ctx.remaining_seconds
        
        assert r1 < r0
        assert r0 - r1 >= 0.04  # Allow some slack
    
    def test_remaining_seconds_never_negative(self):
        """remaining_seconds is clamped to 0."""
        ctx = TimeoutContext(timeout_seconds=0.01)
        time.sleep(0.05)
        
        assert ctx.remaining_seconds == 0.0
    
    def test_reset_restarts_deadline(self):
        """reset() restarts the timeout."""
        ctx = TimeoutContext(timeout_seconds=0.05)
        time.sleep(0.03)
        
        ctx.reset()
        
        # After reset, should have full timeout again
        assert ctx.remaining_seconds > 0.03
        ctx.check()  # Should not raise


# =============================================================================
# FileSizeGate Tests
# =============================================================================


class TestFileSizeGate:
    """Tests for FileSizeGate preflight validation."""
    
    def test_small_file_returns_empty_warnings(self):
        """Files under warn threshold return no warnings."""
        gate = FileSizeGate(max_size_bytes=100, warn_size_bytes=50)
        content = b"x" * 30  # 30 bytes
        
        warnings = gate.check(content, uri="test.csv")
        
        assert warnings == []
    
    def test_medium_file_returns_warning(self):
        """Files between warn and max threshold return warning."""
        gate = FileSizeGate(max_size_bytes=100, warn_size_bytes=50)
        content = b"x" * 75  # 75 bytes
        
        warnings = gate.check(content, uri="test.csv")
        
        assert len(warnings) == 1
        assert "test.csv" in warnings[0]
        assert "streaming" in warnings[0].lower()
    
    def test_large_file_raises_error(self):
        """Files over max threshold raise FileTooLargeError."""
        gate = FileSizeGate(max_size_bytes=100, warn_size_bytes=50)
        content = b"x" * 150  # 150 bytes
        
        with pytest.raises(FileTooLargeError) as exc_info:
            gate.check(content, uri="large.csv")
        
        assert exc_info.value.file_size_bytes == 150
        assert exc_info.value.max_size_bytes == 100
        assert "large.csv" in str(exc_info.value)
    
    def test_string_content_measured_as_utf8(self):
        """String content is measured as UTF-8 bytes."""
        gate = FileSizeGate(max_size_bytes=100, warn_size_bytes=50)
        content = "x" * 75  # 75 chars = 75 bytes in UTF-8
        
        warnings = gate.check(content, uri="test.csv")
        
        assert len(warnings) == 1
    
    def test_unicode_string_measured_correctly(self):
        """Unicode content measures actual byte size."""
        gate = FileSizeGate(max_size_bytes=100, warn_size_bytes=50)
        # Each emoji is ~4 bytes in UTF-8
        content = "😀" * 20  # 20 * 4 = ~80 bytes
        
        warnings = gate.check(content, uri="test.csv")
        
        assert len(warnings) == 1  # Over 50-byte warn threshold
    
    def test_get_size_bytes(self):
        """get_size() returns correct size for bytes."""
        gate = FileSizeGate()
        assert gate.get_size(b"hello") == 5
    
    def test_get_size_string(self):
        """get_size() returns correct UTF-8 size for string."""
        gate = FileSizeGate()
        assert gate.get_size("hello") == 5
        assert gate.get_size("héllo") == 6  # é is 2 bytes in UTF-8


# =============================================================================
# ProcessingContext Tests
# =============================================================================


class TestProcessingContext:
    """Tests for combined ProcessingContext."""
    
    def test_create_with_defaults(self):
        """Can create context with no arguments."""
        ctx = ProcessingContext()
        
        assert ctx.cancel_token is None
        assert ctx.timeout_ctx is None
        assert ctx.size_gate is None
    
    def test_create_from_config(self):
        """from_config() creates context from FileProcessingConfig."""
        config = FileProcessingConfig(
            max_size_bytes=100,
            parse_timeout_s=60.0,
            enable_cancellation=True,
        )
        
        ctx = ProcessingContext.from_config(config)
        
        assert ctx.cancel_token is not None
        assert ctx.timeout_ctx is not None
        assert ctx.timeout_ctx.timeout_seconds == 60.0
        assert ctx.size_gate is not None
        assert ctx.size_gate.max_size_bytes == 100
    
    def test_check_passes_when_all_ok(self):
        """check() passes when no constraints violated."""
        ctx = ProcessingContext(
            cancel_token=CancelToken(),
            timeout_ctx=TimeoutContext(timeout_seconds=10.0),
        )
        
        ctx.check()  # Should not raise
    
    def test_check_raises_on_cancel(self):
        """check() raises when cancelled."""
        token = CancelToken()
        ctx = ProcessingContext(cancel_token=token)
        
        token.cancel("test")
        
        with pytest.raises(CancelledException):
            ctx.check()
    
    def test_check_raises_on_timeout(self):
        """check() raises when timed out."""
        ctx = ProcessingContext(
            timeout_ctx=TimeoutContext(timeout_seconds=0.01)
        )
        time.sleep(0.02)
        
        with pytest.raises(FileProcessingTimeout):
            ctx.check()
    
    def test_check_on_item_skips_expensive_checks(self):
        """check_on_item() only checks every N items."""
        check_count = 0
        
        class CountingToken(CancelToken):
            def check(self):
                nonlocal check_count
                check_count += 1
                super().check()
        
        token = CountingToken()
        ctx = ProcessingContext(cancel_token=token)
        ctx._check_interval = 10
        
        # Process 100 items
        for i in range(100):
            ctx.check_on_item(i)
        
        # Should have checked every 10 items = 10 checks
        assert check_count == 10
    
    def test_check_on_item_reports_progress(self):
        """check_on_item() calls progress callback."""
        progress_calls = []
        
        def on_progress(processed, total):
            progress_calls.append((processed, total))
        
        ctx = ProcessingContext(progress_callback=on_progress)
        ctx._check_interval = 10
        
        for i in range(50):
            ctx.check_on_item(i, total_estimate=100)
        
        # Should have 5 progress reports (at 0, 10, 20, 30, 40)
        assert len(progress_calls) == 5
        assert progress_calls[0] == (1, 100)  # After item 0
        assert progress_calls[-1] == (41, 100)  # After item 40
    
    def test_check_size_with_gate(self):
        """check_size() uses size gate if present."""
        ctx = ProcessingContext(
            size_gate=FileSizeGate(max_size_bytes=100, warn_size_bytes=50)
        )
        
        warnings = ctx.check_size(b"x" * 75, uri="test.csv")
        
        assert len(warnings) == 1
    
    def test_check_size_without_gate(self):
        """check_size() returns empty list if no gate."""
        ctx = ProcessingContext()
        
        warnings = ctx.check_size(b"x" * 1000, uri="test.csv")
        
        assert warnings == []
    
    def test_is_cancelled_property(self):
        """is_cancelled property reflects token state."""
        token = CancelToken()
        ctx = ProcessingContext(cancel_token=token)
        
        assert not ctx.is_cancelled
        token.cancel()
        assert ctx.is_cancelled
    
    def test_is_expired_property(self):
        """is_expired property reflects timeout state."""
        ctx = ProcessingContext(
            timeout_ctx=TimeoutContext(timeout_seconds=0.01)
        )
        
        assert not ctx.is_expired
        time.sleep(0.02)
        assert ctx.is_expired
    
    def test_elapsed_and_remaining_properties(self):
        """elapsed_seconds and remaining_seconds work."""
        ctx = ProcessingContext(
            timeout_ctx=TimeoutContext(timeout_seconds=10.0)
        )
        
        assert ctx.elapsed_seconds is not None
        assert ctx.remaining_seconds is not None
        assert ctx.remaining_seconds > 0


# =============================================================================
# FileProcessingConfig Tests
# =============================================================================


class TestFileProcessingConfig:
    """Tests for FileProcessingConfig."""
    
    def test_default_values(self):
        """Default config has sensible values."""
        config = FileProcessingConfig()
        
        assert config.max_size_bytes == 500 * 1024 * 1024  # 500MB
        assert config.warn_size_bytes == 50 * 1024 * 1024   # 50MB
        assert config.parse_timeout_s == 300.0               # 5 min
        assert config.detect_timeout_s == 30.0               # 30 sec
        assert config.sample_rows == 1000
        assert config.enable_cancellation is True
    
    def test_from_env_with_no_env_vars(self):
        """from_env() uses defaults when no env vars set."""
        config = FileProcessingConfig.from_env()
        
        # Should match defaults
        assert config.max_size_bytes == 500 * 1024 * 1024
    
    def test_from_env_with_env_vars(self):
        """from_env() reads from environment variables."""
        with mock.patch.dict(os.environ, {
            'FILE_MAX_SIZE_MB': '100',
            'FILE_PARSE_TIMEOUT_S': '60',
            'FILE_SAMPLE_ROWS': '500',
        }):
            config = FileProcessingConfig.from_env()
        
        assert config.max_size_bytes == 100 * 1024 * 1024
        assert config.parse_timeout_s == 60.0
        assert config.sample_rows == 500
    
    def test_from_env_handles_invalid_values(self):
        """from_env() handles invalid values gracefully."""
        with mock.patch.dict(os.environ, {
            'FILE_MAX_SIZE_MB': 'not_a_number',
        }):
            config = FileProcessingConfig.from_env()
        
        # Should use default
        assert config.max_size_bytes == 500 * 1024 * 1024
    
    def test_from_settings_fallback_to_env(self):
        """from_settings() falls back to from_env() if settings unavailable."""
        # This should not raise even if settings module is not configured
        config = FileProcessingConfig.from_settings()
        
        # Should have valid defaults
        assert config.max_size_bytes > 0


# =============================================================================
# Exception Tests
# =============================================================================


class TestExceptions:
    """Tests for custom exceptions."""
    
    def test_cancelled_exception_inheritance(self):
        """CancelledException inherits from FileProcessingError."""
        exc = CancelledException("test")
        assert isinstance(exc, FileProcessingError)
        assert exc.reason == "test"
    
    def test_timeout_exception_attributes(self):
        """FileProcessingTimeout has useful attributes."""
        exc = FileProcessingTimeout(timeout_seconds=60.0, elapsed_seconds=65.0)
        
        assert isinstance(exc, FileProcessingError)
        assert exc.timeout_seconds == 60.0
        assert exc.elapsed_seconds == 65.0
        assert "60.0" in str(exc)
        assert "65.0" in str(exc)
    
    def test_file_too_large_exception_attributes(self):
        """FileTooLargeError has useful attributes and message."""
        exc = FileTooLargeError(
            file_size_bytes=200 * 1024 * 1024,
            max_size_bytes=100 * 1024 * 1024,
            uri="large.csv",
        )
        
        assert isinstance(exc, FileProcessingError)
        assert exc.file_size_bytes == 200 * 1024 * 1024
        assert exc.max_size_bytes == 100 * 1024 * 1024
        assert "large.csv" in str(exc)
        assert "200.0MB" in str(exc)
        assert "100.0MB" in str(exc)
        assert "FILE_MAX_SIZE_MB" in str(exc)  # Actionable hint


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests for processing context."""
    
    def test_realistic_processing_loop(self):
        """Test realistic processing loop with all features."""
        config = FileProcessingConfig(
            parse_timeout_s=5.0,
            max_size_bytes=100 * 1024 * 1024,
            enable_cancellation=True,
            cancel_check_interval_rows=50,
        )
        
        ctx = ProcessingContext.from_config(config)
        
        # Simulate processing 1000 items
        processed = 0
        for i in range(1000):
            ctx.check_on_item(i)
            processed += 1
        
        assert processed == 1000
    
    def test_cancellation_during_processing(self):
        """Test cancellation interrupts processing loop."""
        ctx = ProcessingContext.from_config(FileProcessingConfig(
            enable_cancellation=True,
            cancel_check_interval_rows=10,
        ))
        
        processed = 0
        
        def cancel_after_delay():
            time.sleep(0.01)
            ctx.cancel_token.cancel("test cancel")
        
        cancel_thread = threading.Thread(target=cancel_after_delay)
        cancel_thread.start()
        
        try:
            for i in range(10000):
                ctx.check_on_item(i)
                processed += 1
                time.sleep(0.001)
        except CancelledException:
            pass
        
        cancel_thread.join()
        
        # Should have been interrupted partway through
        assert processed < 10000
    
    def test_size_check_before_processing(self):
        """Test size gate rejects oversized files."""
        config = FileProcessingConfig(
            max_size_bytes=100,
            warn_size_bytes=50,
        )
        ctx = ProcessingContext.from_config(config)
        
        # Small file - OK
        warnings = ctx.check_size(b"x" * 30, "small.csv")
        assert len(warnings) == 0
        
        # Medium file - warning
        warnings = ctx.check_size(b"x" * 75, "medium.csv")
        assert len(warnings) == 1
        
        # Large file - error
        with pytest.raises(FileTooLargeError):
            ctx.check_size(b"x" * 150, "large.csv")
