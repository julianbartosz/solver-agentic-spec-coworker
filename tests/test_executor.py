"""
Tests for shared executor module (H-3).

Tests the ThreadPoolExecutor lifecycle, configuration, and shutdown registration.
"""

import asyncio
import os
import pytest
from unittest.mock import patch

from integration_coworker.executor import (
    get_executor,
    get_executor_config,
    shutdown_executor,
    run_sync_in_executor,
    run_in_executor_async,
)


class TestExecutorConfig:
    """Test executor configuration from environment variables."""
    
    def test_default_config(self):
        """Test default configuration values."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove any existing env vars
            os.environ.pop("EXECUTOR_MAX_WORKERS", None)
            os.environ.pop("EXECUTOR_THREAD_NAME_PREFIX", None)
            
            config = get_executor_config()
            
            # Default max_workers is min(32, cpu_count + 4)
            cpu_count = os.cpu_count() or 1
            expected_max = min(32, cpu_count + 4)
            assert config["max_workers"] == expected_max
            assert config["thread_name_prefix"] == "SharedExecutor"
    
    def test_custom_max_workers(self):
        """Test custom max_workers from env var."""
        with patch.dict(os.environ, {"EXECUTOR_MAX_WORKERS": "16"}):
            config = get_executor_config()
            assert config["max_workers"] == 16
    
    def test_custom_thread_prefix(self):
        """Test custom thread name prefix from env var."""
        with patch.dict(os.environ, {"EXECUTOR_THREAD_NAME_PREFIX": "MyApp"}):
            config = get_executor_config()
            assert config["thread_name_prefix"] == "MyApp"
    
    def test_invalid_max_workers_uses_default(self):
        """Test invalid max_workers falls back to default."""
        with patch.dict(os.environ, {"EXECUTOR_MAX_WORKERS": "not-a-number"}):
            config = get_executor_config()
            cpu_count = os.cpu_count() or 1
            expected_max = min(32, cpu_count + 4)
            assert config["max_workers"] == expected_max
    
    def test_zero_max_workers_uses_default(self):
        """Test zero max_workers falls back to default."""
        with patch.dict(os.environ, {"EXECUTOR_MAX_WORKERS": "0"}):
            config = get_executor_config()
            cpu_count = os.cpu_count() or 1
            expected_max = min(32, cpu_count + 4)
            assert config["max_workers"] == expected_max


class TestExecutorLifecycle:
    """Test executor lifecycle management."""
    
    def setup_method(self):
        """Ensure clean state before each test."""
        shutdown_executor()
    
    def teardown_method(self):
        """Cleanup after each test."""
        shutdown_executor()
    
    def test_executor_created_lazily(self):
        """Test executor is created on first use."""
        executor = get_executor()
        assert executor is not None
        assert hasattr(executor, "submit")
    
    def test_executor_singleton(self):
        """Test same executor instance is returned."""
        executor1 = get_executor()
        executor2 = get_executor()
        assert executor1 is executor2
    
    def test_executor_shutdown(self):
        """Test executor can be shutdown."""
        executor = get_executor()
        shutdown_executor()
        
        # After shutdown, getting executor creates a new one
        new_executor = get_executor()
        assert new_executor is not executor
    
    def test_shutdown_idempotent(self):
        """Test shutdown can be called multiple times safely."""
        get_executor()
        shutdown_executor()
        shutdown_executor()  # Should not raise
        shutdown_executor()


class TestShutdownManagerRegistration:
    """Test executor cleanup registration with ShutdownManager."""
    
    def setup_method(self):
        """Reset state."""
        shutdown_executor()
        from integration_coworker.shutdown import reset_shutdown_manager
        reset_shutdown_manager()
    
    def teardown_method(self):
        """Cleanup."""
        shutdown_executor()
        from integration_coworker.shutdown import reset_shutdown_manager
        reset_shutdown_manager()
    
    def test_cleanup_registered_with_shutdown_manager(self):
        """H-3: Verify cleanup is registered with ShutdownManager."""
        from integration_coworker.shutdown import get_shutdown_manager
        
        manager = get_shutdown_manager()
        initial_callbacks = len(manager._cleanup_callbacks)
        
        # Create executor - should register cleanup
        get_executor()
        
        # Verify cleanup was registered
        assert len(manager._cleanup_callbacks) == initial_callbacks + 1
        assert shutdown_executor in manager._cleanup_callbacks


class TestRunInExecutor:
    """Test running functions in the executor."""
    
    def setup_method(self):
        """Reset state."""
        shutdown_executor()
    
    def teardown_method(self):
        """Cleanup."""
        shutdown_executor()
    
    def test_run_sync_function(self):
        """Test running a sync function in executor."""
        def add(a, b):
            return a + b
        
        result = run_sync_in_executor(add, 1, 2)
        assert result == 3
    
    def test_run_sync_function_with_kwargs(self):
        """Test running a sync function with keyword arguments."""
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"
        
        result = run_sync_in_executor(greet, "World", greeting="Hi")
        assert result == "Hi, World!"
    
    @pytest.mark.asyncio
    async def test_run_in_executor_async(self):
        """Test running a sync function from async context."""
        def slow_add(a, b):
            return a + b
        
        result = await run_in_executor_async(slow_add, 5, 3)
        assert result == 8
    
    @pytest.mark.asyncio
    async def test_concurrent_async_calls(self):
        """Test concurrent calls from async context."""
        import time
        
        def slow_square(n):
            time.sleep(0.1)
            return n * n
        
        # Run multiple calls concurrently
        start = time.time()
        results = await asyncio.gather(
            run_in_executor_async(slow_square, 2),
            run_in_executor_async(slow_square, 3),
            run_in_executor_async(slow_square, 4),
        )
        elapsed = time.time() - start
        
        assert results == [4, 9, 16]
        # Should complete in ~0.1s (parallel), not ~0.3s (serial)
        assert elapsed < 0.3
