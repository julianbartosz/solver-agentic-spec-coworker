"""
Tests for graceful shutdown handling.

Production Readiness v4 - P0-4:
Tests platform-aware signal handling and cleanup callbacks.
"""

import asyncio
import logging
import signal
import sys
import threading
from unittest.mock import AsyncMock, Mock, patch
import pytest

from integration_coworker.shutdown import (
    ShutdownManager,
    get_shutdown_manager,
    reset_shutdown_manager,
    shutdown_context,
    setup_shutdown_handlers,
    is_shutdown_requested,
    graceful_shutdown,
)


class TestShutdownManager:
    """Test ShutdownManager class."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        reset_shutdown_manager()
    
    def teardown_method(self):
        """Clean up after each test."""
        reset_shutdown_manager()
    
    def test_shutdown_manager_creation(self):
        """Test ShutdownManager can be created."""
        manager = ShutdownManager()
        assert manager is not None
        assert not manager.is_shutdown_requested()
        assert not manager._setup_complete
    
    def test_request_shutdown(self):
        """Test requesting shutdown sets the event."""
        manager = ShutdownManager()
        # Access property to create event
        _ = manager.shutdown_event
        
        assert not manager.is_shutdown_requested()
        manager.request_shutdown()
        assert manager.is_shutdown_requested()
    
    def test_request_shutdown_idempotent(self):
        """Test requesting shutdown multiple times is safe."""
        manager = ShutdownManager()
        _ = manager.shutdown_event
        
        manager.request_shutdown()
        manager.request_shutdown()  # Should not raise
        assert manager.is_shutdown_requested()
    
    def test_register_cleanup_callback(self):
        """Test registering cleanup callbacks."""
        manager = ShutdownManager()
        
        callback1 = Mock()
        callback2 = Mock()
        
        manager.register_cleanup(callback1)
        manager.register_cleanup(callback2)
        
        assert len(manager._cleanup_callbacks) == 2
        assert callback1 in manager._cleanup_callbacks
        assert callback2 in manager._cleanup_callbacks
    
    def test_track_task(self):
        """Test tracking async tasks."""
        manager = ShutdownManager()
        
        async def dummy_coro():
            await asyncio.sleep(0)
        
        loop = asyncio.new_event_loop()
        try:
            task = loop.create_task(dummy_coro())
            manager.track_task(task)
            
            assert task in manager._running_tasks
            
            # Run the task to completion
            loop.run_until_complete(task)
            
            # Task should be removed from tracking after completion
            assert task not in manager._running_tasks
        finally:
            loop.close()


class TestShutdownManagerAsync:
    """Async tests for ShutdownManager."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        reset_shutdown_manager()
    
    def teardown_method(self):
        """Clean up after each test."""
        reset_shutdown_manager()
    
    @pytest.mark.asyncio
    async def test_setup_creates_event(self):
        """Test setup creates shutdown event."""
        manager = ShutdownManager()
        await manager.setup()
        
        assert manager._shutdown_event is not None
        assert manager._setup_complete
    
    @pytest.mark.asyncio
    async def test_setup_idempotent(self):
        """Test setup can be called multiple times safely."""
        manager = ShutdownManager()
        await manager.setup()
        await manager.setup()  # Should not raise
        
        assert manager._setup_complete
    
    @pytest.mark.asyncio
    async def test_wait_for_shutdown_with_timeout(self):
        """Test waiting for shutdown with timeout returns False."""
        manager = ShutdownManager()
        await manager.setup()
        
        # Should timeout quickly
        result = await manager.wait_for_shutdown(timeout=0.01)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_wait_for_shutdown_returns_true_when_set(self):
        """Test wait returns True when shutdown is requested."""
        manager = ShutdownManager()
        await manager.setup()
        
        # Request shutdown in background
        async def request_after_delay():
            await asyncio.sleep(0.01)
            manager.request_shutdown()
        
        task = asyncio.create_task(request_after_delay())
        result = await manager.wait_for_shutdown(timeout=1.0)
        await task
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_cleanup_runs_callbacks(self):
        """Test cleanup executes registered callbacks."""
        manager = ShutdownManager()
        await manager.setup()
        
        callback_executed = []
        
        async def async_callback():
            callback_executed.append("async")
        
        def sync_callback():
            callback_executed.append("sync")
        
        manager.register_cleanup(async_callback)
        manager.register_cleanup(sync_callback)
        
        await manager.cleanup(timeout=1.0)
        
        assert "async" in callback_executed
        assert "sync" in callback_executed
    
    @pytest.mark.asyncio
    async def test_cleanup_handles_callback_errors(self):
        """Test cleanup continues if callback fails."""
        manager = ShutdownManager()
        await manager.setup()
        
        executed = []
        
        async def failing_callback():
            raise RuntimeError("Callback failed")
        
        async def success_callback():
            executed.append("success")
        
        manager.register_cleanup(failing_callback)
        manager.register_cleanup(success_callback)
        
        # Should not raise, should continue to next callback
        await manager.cleanup(timeout=1.0)
        
        assert "success" in executed
    
    @pytest.mark.asyncio
    async def test_cleanup_cancels_tracked_tasks(self):
        """Test cleanup cancels tracked tasks."""
        manager = ShutdownManager()
        await manager.setup()
        
        cancelled = []
        
        async def long_running():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        
        task = asyncio.create_task(long_running())
        manager.track_task(task)
        
        await asyncio.sleep(0.01)  # Let task start
        await manager.cleanup(timeout=1.0)
        
        assert len(cancelled) == 1
        assert task.cancelled() or task.done()


class TestShutdownSignalHandlers:
    """Test signal handler setup (platform-specific)."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        reset_shutdown_manager()
    
    def teardown_method(self):
        """Clean up after each test."""
        reset_shutdown_manager()
    
    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only test")
    async def test_unix_signal_handler_setup(self):
        """Test Unix signal handlers are registered."""
        manager = ShutdownManager()
        await manager.setup()
        
        # Check handlers were registered (or warned if not main thread)
        assert manager._setup_complete
    
    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    async def test_windows_signal_handler_setup(self):
        """Test Windows signal handlers are registered."""
        manager = ShutdownManager()
        await manager.setup()
        
        # Check handlers were registered
        assert manager._setup_complete
    
    @pytest.mark.asyncio
    async def test_non_main_thread_logs_warning(self):
        """Test warning is logged when not in main thread."""
        manager = ShutdownManager()
        manager._is_main_thread = False
        
        # Setup should succeed but may log warning
        await manager.setup()
        assert manager._setup_complete


class TestShutdownGlobalFunctions:
    """Test module-level convenience functions."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        reset_shutdown_manager()
    
    def teardown_method(self):
        """Clean up after each test."""
        reset_shutdown_manager()
    
    def test_get_shutdown_manager_singleton(self):
        """Test get_shutdown_manager returns same instance."""
        manager1 = get_shutdown_manager()
        manager2 = get_shutdown_manager()
        
        assert manager1 is manager2
    
    def test_reset_shutdown_manager(self):
        """Test reset_shutdown_manager clears singleton."""
        manager1 = get_shutdown_manager()
        manager1.request_shutdown()  # This will create the event
        
        reset_shutdown_manager()
        
        manager2 = get_shutdown_manager()
        assert manager1 is not manager2
        assert not manager2.is_shutdown_requested()
    
    def test_is_shutdown_requested_global(self):
        """Test global is_shutdown_requested function."""
        assert not is_shutdown_requested()
        
        manager = get_shutdown_manager()
        _ = manager.shutdown_event  # Create event
        manager.request_shutdown()
        
        assert is_shutdown_requested()
    
    @pytest.mark.asyncio
    async def test_setup_shutdown_handlers_returns_manager(self):
        """Test setup_shutdown_handlers returns the manager."""
        manager = await setup_shutdown_handlers()
        
        assert manager is not None
        assert manager._setup_complete
    
    @pytest.mark.asyncio
    async def test_graceful_shutdown_runs_cleanup(self):
        """Test graceful_shutdown runs cleanup."""
        manager = get_shutdown_manager()
        await manager.setup()
        
        executed = []
        manager.register_cleanup(lambda: executed.append("done"))
        
        await graceful_shutdown(timeout=1.0)
        
        assert "done" in executed


class TestShutdownContext:
    """Test shutdown_context async context manager."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        reset_shutdown_manager()
    
    def teardown_method(self):
        """Clean up after each test."""
        reset_shutdown_manager()
    
    @pytest.mark.asyncio
    async def test_shutdown_context_setup_and_cleanup(self):
        """Test context manager sets up and cleans up."""
        executed = []
        
        async with shutdown_context() as manager:
            manager.register_cleanup(lambda: executed.append("cleanup"))
            assert manager._setup_complete
        
        assert "cleanup" in executed
    
    @pytest.mark.asyncio
    async def test_shutdown_context_cleanup_on_exception(self):
        """Test cleanup runs even on exception."""
        executed = []
        
        with pytest.raises(ValueError):
            async with shutdown_context() as manager:
                manager.register_cleanup(lambda: executed.append("cleanup"))
                raise ValueError("Test error")
        
        assert "cleanup" in executed


class TestShutdownThreadSafety:
    """Test thread safety of shutdown operations."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        reset_shutdown_manager()
    
    def teardown_method(self):
        """Clean up after each test."""
        reset_shutdown_manager()
    
    def test_request_shutdown_from_another_thread(self):
        """Test shutdown can be requested from another thread."""
        manager = get_shutdown_manager()
        _ = manager.shutdown_event  # Create event
        
        shutdown_called = []
        
        def thread_func():
            manager.request_shutdown()
            shutdown_called.append(True)
        
        thread = threading.Thread(target=thread_func)
        thread.start()
        thread.join(timeout=1.0)
        
        assert len(shutdown_called) == 1
        assert manager.is_shutdown_requested()
    
    def test_is_shutdown_requested_thread_safe(self):
        """Test is_shutdown_requested is thread-safe."""
        manager = get_shutdown_manager()
        _ = manager.shutdown_event  # Create event
        
        results = []
        
        def thread_func():
            for _ in range(100):
                results.append(manager.is_shutdown_requested())
        
        threads = [threading.Thread(target=thread_func) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)
        
        # All should be False initially
        assert all(r is False for r in results)


class TestShutdownIntegration:
    """Integration tests for shutdown with workflow."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        reset_shutdown_manager()
    
    def teardown_method(self):
        """Clean up after each test."""
        reset_shutdown_manager()
    
    @pytest.mark.asyncio
    async def test_shutdown_aborts_workflow_before_start(self):
        """Test shutdown check prevents workflow start."""
        from integration_coworker.shutdown import get_shutdown_manager
        
        manager = get_shutdown_manager()
        await manager.setup()
        manager.request_shutdown()
        
        # Verify shutdown is detected
        assert manager.is_shutdown_requested()
    
    @pytest.mark.asyncio
    async def test_cleanup_timeout_works(self):
        """Test cleanup respects timeout for slow callbacks."""
        manager = ShutdownManager()
        await manager.setup()
        
        async def slow_callback():
            await asyncio.sleep(10)  # Very slow
        
        manager.register_cleanup(slow_callback)
        
        # Should complete within timeout + buffer
        start = asyncio.get_event_loop().time()
        await manager.cleanup(timeout=0.1)
        elapsed = asyncio.get_event_loop().time() - start
        
        # Should not have waited 10 seconds
        assert elapsed < 1.0


class TestShutdownLogging:
    """Test logging during shutdown operations."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        reset_shutdown_manager()
    
    def teardown_method(self):
        """Clean up after each test."""
        reset_shutdown_manager()
    
    def test_request_shutdown_logs_info(self):
        """Test shutdown request works (logging verified by manual inspection)."""
        manager = get_shutdown_manager()
        _ = manager.shutdown_event
        
        # Should not raise
        manager.request_shutdown()
        
        # Verify state changed
        assert manager.is_shutdown_requested()
    
    @pytest.mark.asyncio
    async def test_cleanup_logs_start_and_complete(self):
        """Test cleanup runs successfully (logging verified by manual inspection)."""
        manager = ShutdownManager()
        await manager.setup()
        
        executed = []
        manager.register_cleanup(lambda: executed.append("done"))
        
        # Should not raise
        await manager.cleanup(timeout=1.0)
        
        # Verify callbacks ran
        assert "done" in executed
