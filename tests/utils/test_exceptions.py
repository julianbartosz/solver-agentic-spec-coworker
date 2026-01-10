"""Tests for utils/exceptions.py module.

Production Hardening H-6: Tests verify:
1. is_test_mode() detects test environments correctly
2. Fatal exception handling works as expected
3. Safe cleanup utilities work correctly
4. Decorator-based exception handling works
"""

import asyncio
import os
import pytest
from unittest.mock import patch

from integration_coworker.utils.exceptions import (
    TEST_MODE_ENV_VAR,
    PYTEST_ENV_VAR,
    is_test_mode,
    require_test_mode,
    FATAL_EXCEPTIONS,
    ASYNC_FATAL_EXCEPTIONS,
    reraise_fatal,
    reraise_async_fatal,
    safe_cleanup,
    async_safe_cleanup,
    expect_exceptions,
    async_expect_exceptions,
    FILE_EXCEPTIONS,
)


class TestIsTestMode:
    """Test test mode detection."""
    
    def test_explicit_env_var_true(self):
        """INTEGRATION_COWORKER_TESTING=1 should enable test mode."""
        with patch.dict(os.environ, {TEST_MODE_ENV_VAR: "1"}, clear=False):
            # Clear PYTEST_CURRENT_TEST to test explicit var alone
            env = os.environ.copy()
            env.pop(PYTEST_ENV_VAR, None)
            with patch.dict(os.environ, env, clear=True):
                # Restore just the test var
                os.environ[TEST_MODE_ENV_VAR] = "1"
                assert is_test_mode() is True
    
    def test_explicit_env_var_truthy_values(self):
        """Various truthy values should enable test mode."""
        for value in ("1", "true", "TRUE", "yes", "YES", "True"):
            with patch.dict(os.environ, {TEST_MODE_ENV_VAR: value, PYTEST_ENV_VAR: ""}, clear=False):
                os.environ.pop(PYTEST_ENV_VAR, None)
                result = is_test_mode()
                # Since PYTEST_CURRENT_TEST is set during test run, we may get True
                # The key test is that explicit values work
                assert result is True or os.environ.get(PYTEST_ENV_VAR)
    
    def test_pytest_env_var_fallback(self):
        """PYTEST_CURRENT_TEST should enable test mode as fallback."""
        # This test runs under pytest, so PYTEST_CURRENT_TEST should be set
        # Just verify we're detected as being in test mode
        assert is_test_mode() is True
    
    def test_not_test_mode_when_both_unset(self):
        """Should return False when no test env vars set."""
        # Create clean environment without test markers
        clean_env = {k: v for k, v in os.environ.items()
                    if k not in (TEST_MODE_ENV_VAR, PYTEST_ENV_VAR)}
        
        with patch.dict(os.environ, clean_env, clear=True):
            assert is_test_mode() is False


class TestRequireTestMode:
    """Test require_test_mode assertion."""
    
    def test_no_error_in_test_mode(self):
        """Should not raise when in test mode."""
        # We're running under pytest, so this should not raise
        require_test_mode("test operation")  # Should not raise
    
    def test_error_outside_test_mode(self):
        """Should raise RuntimeError outside test mode."""
        clean_env = {k: v for k, v in os.environ.items()
                    if k not in (TEST_MODE_ENV_VAR, PYTEST_ENV_VAR)}
        
        with patch.dict(os.environ, clean_env, clear=True):
            with pytest.raises(RuntimeError) as exc_info:
                require_test_mode("resetting cache")
            
            assert "resetting cache" in str(exc_info.value)
            assert TEST_MODE_ENV_VAR in str(exc_info.value)


class TestFatalExceptionConstants:
    """Test fatal exception tuple contents."""
    
    def test_fatal_exceptions_tuple(self):
        """FATAL_EXCEPTIONS should include critical system exceptions."""
        assert KeyboardInterrupt in FATAL_EXCEPTIONS
        assert SystemExit in FATAL_EXCEPTIONS
    
    def test_async_fatal_includes_cancelled_error(self):
        """ASYNC_FATAL_EXCEPTIONS should include CancelledError."""
        assert asyncio.CancelledError in ASYNC_FATAL_EXCEPTIONS
        assert KeyboardInterrupt in ASYNC_FATAL_EXCEPTIONS
        assert SystemExit in ASYNC_FATAL_EXCEPTIONS


class TestReraiseFunctions:
    """Test reraise check functions."""
    
    def test_reraise_fatal_keyboard_interrupt(self):
        """reraise_fatal should return True for KeyboardInterrupt."""
        assert reraise_fatal(KeyboardInterrupt()) is True
    
    def test_reraise_fatal_system_exit(self):
        """reraise_fatal should return True for SystemExit."""
        assert reraise_fatal(SystemExit(0)) is True
    
    def test_reraise_fatal_regular_exception(self):
        """reraise_fatal should return False for regular exceptions."""
        assert reraise_fatal(ValueError("test")) is False
        assert reraise_fatal(RuntimeError("test")) is False
    
    def test_reraise_async_fatal_cancelled_error(self):
        """reraise_async_fatal should return True for CancelledError."""
        assert reraise_async_fatal(asyncio.CancelledError()) is True
    
    def test_reraise_async_fatal_keyboard_interrupt(self):
        """reraise_async_fatal should return True for KeyboardInterrupt."""
        assert reraise_async_fatal(KeyboardInterrupt()) is True
    
    def test_reraise_async_fatal_regular_exception(self):
        """reraise_async_fatal should return False for regular exceptions."""
        assert reraise_async_fatal(ValueError("test")) is False


class TestSafeCleanup:
    """Test safe_cleanup utility."""
    
    def test_successful_cleanup(self):
        """Cleanup function should be called."""
        called = []
        
        def cleanup():
            called.append(True)
        
        safe_cleanup(cleanup, "test cleanup")
        assert called == [True]
    
    def test_exception_suppressed(self):
        """Regular exceptions should be suppressed."""
        def failing_cleanup():
            raise ValueError("cleanup failed")
        
        # Should not raise
        safe_cleanup(failing_cleanup, "test cleanup")
    
    def test_keyboard_interrupt_not_suppressed(self):
        """KeyboardInterrupt should be re-raised."""
        def keyboard_interrupt_cleanup():
            raise KeyboardInterrupt()
        
        with pytest.raises(KeyboardInterrupt):
            safe_cleanup(keyboard_interrupt_cleanup, "test cleanup")
    
    def test_system_exit_not_suppressed(self):
        """SystemExit should be re-raised."""
        def system_exit_cleanup():
            raise SystemExit(1)
        
        with pytest.raises(SystemExit):
            safe_cleanup(system_exit_cleanup, "test cleanup")


class TestAsyncSafeCleanup:
    """Test async_safe_cleanup utility."""
    
    @pytest.mark.asyncio
    async def test_sync_cleanup(self):
        """Should handle sync cleanup functions."""
        called = []
        
        def sync_cleanup():
            called.append(True)
        
        await async_safe_cleanup(sync_cleanup, "test")
        assert called == [True]
    
    @pytest.mark.asyncio
    async def test_async_cleanup(self):
        """Should handle async cleanup functions."""
        called = []
        
        async def async_cleanup():
            called.append(True)
        
        await async_safe_cleanup(async_cleanup, "test")
        assert called == [True]
    
    @pytest.mark.asyncio
    async def test_exception_suppressed(self):
        """Regular exceptions should be suppressed."""
        async def failing_cleanup():
            raise ValueError("failed")
        
        # Should not raise
        await async_safe_cleanup(failing_cleanup, "test")
    
    @pytest.mark.asyncio
    async def test_cancelled_error_not_suppressed(self):
        """CancelledError should be re-raised."""
        async def cancelled_cleanup():
            raise asyncio.CancelledError()
        
        with pytest.raises(asyncio.CancelledError):
            await async_safe_cleanup(cancelled_cleanup, "test")


class TestExpectExceptionsDecorator:
    """Test expect_exceptions decorator."""
    
    def test_successful_return(self):
        """Successful function should return normally."""
        @expect_exceptions(ValueError, default=None)
        def good_func():
            return "success"
        
        assert good_func() == "success"
    
    def test_expected_exception_returns_default(self):
        """Expected exception should return default value."""
        @expect_exceptions(ValueError, default="fallback")
        def failing_func():
            raise ValueError("expected")
        
        assert failing_func() == "fallback"
    
    def test_unexpected_exception_propagates(self):
        """Unexpected exception types should propagate."""
        @expect_exceptions(ValueError, default=None)
        def failing_func():
            raise TypeError("unexpected")
        
        with pytest.raises(TypeError):
            failing_func()
    
    def test_keyboard_interrupt_always_propagates(self):
        """KeyboardInterrupt should always propagate."""
        @expect_exceptions(Exception, default=None)
        def failing_func():
            raise KeyboardInterrupt()
        
        with pytest.raises(KeyboardInterrupt):
            failing_func()
    
    def test_multiple_exception_types(self):
        """Should catch multiple specified types."""
        @expect_exceptions(ValueError, TypeError, OSError, default="caught")
        def multi_fail():
            raise OSError("file error")
        
        assert multi_fail() == "caught"


class TestAsyncExpectExceptionsDecorator:
    """Test async_expect_exceptions decorator."""
    
    @pytest.mark.asyncio
    async def test_successful_return(self):
        """Successful async function should return normally."""
        @async_expect_exceptions(ValueError, default=None)
        async def good_func():
            return "success"
        
        assert await good_func() == "success"
    
    @pytest.mark.asyncio
    async def test_expected_exception_returns_default(self):
        """Expected exception should return default value."""
        @async_expect_exceptions(ValueError, default="fallback")
        async def failing_func():
            raise ValueError("expected")
        
        assert await failing_func() == "fallback"
    
    @pytest.mark.asyncio
    async def test_cancelled_error_always_propagates(self):
        """CancelledError should always propagate."""
        @async_expect_exceptions(Exception, default=None)
        async def failing_func():
            raise asyncio.CancelledError()
        
        with pytest.raises(asyncio.CancelledError):
            await failing_func()


class TestExceptionTuples:
    """Test common exception tuples."""
    
    def test_file_exceptions_tuple(self):
        """FILE_EXCEPTIONS should include common file errors."""
        assert OSError in FILE_EXCEPTIONS
        assert IOError in FILE_EXCEPTIONS
        assert PermissionError in FILE_EXCEPTIONS
        assert FileNotFoundError in FILE_EXCEPTIONS
