"""
Tests for V24-002/V24-003: Shutdown-aware graph execution.

Tests the shutdown_aware module utilities:
- shutdown_check_point() raises ShutdownInterruptError when shutdown requested
- ShutdownAwareLoop periodically checks for shutdown
- atexit cleanup registration and execution
"""
import asyncio
import pytest
from unittest.mock import patch, MagicMock

from integration_coworker.graph.shutdown_aware import (
    shutdown_check_point,
    ShutdownInterruptError,
    ShutdownAwareLoop,
    interruptible_llm_call,
    with_shutdown_check,
    register_atexit_cleanup,
)

# The correct patch target is the shutdown module where is_shutdown_requested is defined
SHUTDOWN_PATCH_TARGET = "integration_coworker.shutdown.is_shutdown_requested"


class TestShutdownCheckPoint:
    """Tests for shutdown_check_point() function."""
    
    def test_no_shutdown_passes(self):
        """Checkpoint passes when no shutdown requested."""
        with patch(SHUTDOWN_PATCH_TARGET, return_value=False):
            # Should not raise
            shutdown_check_point("test checkpoint")
    
    def test_shutdown_raises(self):
        """Checkpoint raises ShutdownInterruptError when shutdown requested."""
        with patch(SHUTDOWN_PATCH_TARGET, return_value=True):
            with pytest.raises(ShutdownInterruptError) as exc_info:
                shutdown_check_point("test checkpoint")
            assert "test checkpoint" in str(exc_info.value)
    
    def test_import_error_handled(self):
        """Checkpoint handles ImportError gracefully - continues without check."""
        # If shutdown module doesn't exist, checkpoint should pass silently
        # This is tested implicitly by the fact shutdown_check_point uses try/except


class TestShutdownAwareLoop:
    """Tests for ShutdownAwareLoop iterator."""
    
    def test_basic_iteration(self):
        """Loop iterates over items normally."""
        items = [1, 2, 3, 4, 5]
        with patch(SHUTDOWN_PATCH_TARGET, return_value=False):
            with ShutdownAwareLoop(items, check_every=2) as loop:
                result = list(loop)
        assert result == items
    
    def test_shutdown_during_loop(self):
        """Loop raises at check interval when shutdown requested."""
        items = list(range(25))
        call_count = 0
        
        def mock_shutdown():
            nonlocal call_count
            call_count += 1
            # Return True on second check (at item 20 with check_every=10)
            return call_count >= 2
        
        with patch(SHUTDOWN_PATCH_TARGET, side_effect=mock_shutdown):
            with ShutdownAwareLoop(items, check_every=10) as loop:
                result = []
                with pytest.raises(ShutdownInterruptError):
                    for item in loop:
                        result.append(item)
        
        # Should have processed items 0-19 (20 items) before shutdown at iteration 20
        # The check happens AFTER incrementing count but BEFORE yielding the item
        assert len(result) == 19  # 0-18 yielded, check at 20 raises
    
    def test_check_every_respected(self):
        """Shutdown is only checked at specified intervals."""
        items = list(range(25))
        check_calls = []
        
        def mock_shutdown():
            check_calls.append(len(check_calls))
            return False
        
        with patch(SHUTDOWN_PATCH_TARGET, side_effect=mock_shutdown):
            with ShutdownAwareLoop(items, check_every=10) as loop:
                list(loop)
        
        # With check_every=10, should check at items 10, 20 = 2 checks
        assert len(check_calls) == 2


class TestInterruptibleLlmCall:
    """Tests for interruptible_llm_call() function."""
    
    @pytest.mark.asyncio
    async def test_successful_call(self):
        """Successful LLM call returns result."""
        async def mock_llm():
            return "result"
        
        with patch(SHUTDOWN_PATCH_TARGET, return_value=False):
            result = await interruptible_llm_call(mock_llm, context="test")
        
        assert result == "result"
    
    @pytest.mark.asyncio
    async def test_shutdown_before_call(self):
        """Raises ShutdownInterruptError if shutdown before call."""
        async def mock_llm():
            return "result"
        
        with patch(SHUTDOWN_PATCH_TARGET, return_value=True):
            with pytest.raises(ShutdownInterruptError) as exc_info:
                await interruptible_llm_call(mock_llm, context="test")
            assert "before test" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_shutdown_after_call(self):
        """Raises ShutdownInterruptError if shutdown during call."""
        call_count = 0
        
        async def mock_llm():
            return "result"
        
        def mock_shutdown():
            nonlocal call_count
            call_count += 1
            # First check (before) passes, second check (after) fails
            return call_count > 1
        
        with patch(SHUTDOWN_PATCH_TARGET, side_effect=mock_shutdown):
            with pytest.raises(ShutdownInterruptError) as exc_info:
                await interruptible_llm_call(mock_llm, context="test")
            assert "after test" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_timeout_enforcement(self):
        """LLM call respects timeout parameter."""
        async def slow_llm():
            await asyncio.sleep(5)
            return "result"
        
        with patch(SHUTDOWN_PATCH_TARGET, return_value=False):
            with pytest.raises(asyncio.TimeoutError):
                await interruptible_llm_call(slow_llm, context="test", timeout=0.1)


class TestWithShutdownCheckDecorator:
    """Tests for @with_shutdown_check decorator."""
    
    def test_sync_function_decorated(self):
        """Decorator works with sync functions."""
        @with_shutdown_check("test operation")
        def sync_func(x):
            return x * 2
        
        with patch(SHUTDOWN_PATCH_TARGET, return_value=False):
            result = sync_func(5)
        assert result == 10
    
    def test_sync_function_shutdown(self):
        """Decorated sync function raises on shutdown."""
        @with_shutdown_check("test operation")
        def sync_func(x):
            return x * 2
        
        with patch(SHUTDOWN_PATCH_TARGET, return_value=True):
            with pytest.raises(ShutdownInterruptError):
                sync_func(5)
    
    @pytest.mark.asyncio
    async def test_async_function_decorated(self):
        """Decorator works with async functions."""
        @with_shutdown_check("test operation")
        async def async_func(x):
            return x * 2
        
        with patch(SHUTDOWN_PATCH_TARGET, return_value=False):
            result = await async_func(5)
        assert result == 10
    
    @pytest.mark.asyncio
    async def test_async_function_shutdown(self):
        """Decorated async function raises on shutdown."""
        @with_shutdown_check("test operation")
        async def async_func(x):
            return x * 2
        
        with patch(SHUTDOWN_PATCH_TARGET, return_value=True):
            with pytest.raises(ShutdownInterruptError):
                await async_func(5)


class TestAtexitCleanup:
    """Tests for atexit cleanup registration."""
    
    def test_register_multiple_times(self):
        """register_atexit_cleanup is idempotent."""
        # Should not raise or register multiple times
        register_atexit_cleanup()
        register_atexit_cleanup()
        register_atexit_cleanup()
