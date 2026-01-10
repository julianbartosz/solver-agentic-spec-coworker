"""
Tests for LLM concurrency limiter (Parallelization V2).

Tests the semaphore-based concurrency control that limits concurrent LLM requests.
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock

from integration_coworker.llm.concurrency import (
    acquire_llm_slot,
    get_llm_semaphore,
    reset_llm_semaphore,
    get_concurrency_config,
    get_concurrency_metrics,
    log_concurrency_status,
    with_llm_concurrency,
    ConcurrencyConfig,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset semaphore state before and after each test."""
    reset_llm_semaphore()
    yield
    reset_llm_semaphore()


class TestConcurrencyConfig:
    """Tests for ConcurrencyConfig."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = get_concurrency_config()
        assert config.max_concurrent == 5
        assert config.acquire_timeout == 30.0
    
    def test_config_from_env(self):
        """Test configuration from environment variables."""
        with patch.dict("os.environ", {
            "LLM_MAX_CONCURRENT": "10",
            "LLM_ACQUIRE_TIMEOUT": "60.0",
        }):
            reset_llm_semaphore()
            config = get_concurrency_config()
            assert config.max_concurrent == 10
            assert config.acquire_timeout == 60.0
    
    def test_invalid_max_concurrent_uses_default(self):
        """Test that invalid max_concurrent falls back to default."""
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "0"}):
            reset_llm_semaphore()
            config = get_concurrency_config()
            assert config.max_concurrent == 5  # Default
    
    def test_invalid_timeout_uses_default(self):
        """Test that invalid timeout falls back to default."""
        with patch.dict("os.environ", {"LLM_ACQUIRE_TIMEOUT": "-1"}):
            reset_llm_semaphore()
            config = get_concurrency_config()
            assert config.acquire_timeout == 30.0  # Default


class TestSemaphoreBasics:
    """Basic semaphore functionality tests."""
    
    @pytest.mark.asyncio
    async def test_semaphore_creation(self):
        """Test that semaphore is created with correct limit."""
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "3"}):
            reset_llm_semaphore()
            semaphore = get_llm_semaphore()
            assert semaphore._value == 3
    
    @pytest.mark.asyncio
    async def test_semaphore_singleton(self):
        """Test that get_llm_semaphore returns the same instance."""
        sem1 = get_llm_semaphore()
        sem2 = get_llm_semaphore()
        assert sem1 is sem2
    
    @pytest.mark.asyncio
    async def test_reset_semaphore(self):
        """Test that reset clears the semaphore."""
        sem1 = get_llm_semaphore()
        reset_llm_semaphore()
        sem2 = get_llm_semaphore()
        assert sem1 is not sem2


class TestAcquireLLMSlot:
    """Tests for acquire_llm_slot context manager."""
    
    @pytest.mark.asyncio
    async def test_basic_acquire_release(self):
        """Test basic slot acquisition and release."""
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "1"}):
            reset_llm_semaphore()
            
            async with acquire_llm_slot():
                # We're holding the slot
                metrics = get_concurrency_metrics()
                assert metrics["current_active"] == 1
            
            # Slot is released
            metrics = get_concurrency_metrics()
            assert metrics["current_active"] == 0
    
    @pytest.mark.asyncio
    async def test_concurrent_acquire_respects_limit(self):
        """Test that concurrent acquisitions respect the limit."""
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "2"}):
            reset_llm_semaphore()
            
            max_concurrent_seen = 0
            
            async def acquire_and_track():
                nonlocal max_concurrent_seen
                async with acquire_llm_slot():
                    metrics = get_concurrency_metrics()
                    max_concurrent_seen = max(max_concurrent_seen, metrics["current_active"])
                    await asyncio.sleep(0.05)
            
            # Run 5 concurrent tasks with limit of 2
            await asyncio.gather(*[acquire_and_track() for _ in range(5)])
            
            # Max concurrent should never exceed 2
            assert max_concurrent_seen <= 2
    
    @pytest.mark.asyncio
    async def test_acquire_timeout(self):
        """Test that acquisition times out correctly."""
        with patch.dict("os.environ", {
            "LLM_MAX_CONCURRENT": "1",
            "LLM_ACQUIRE_TIMEOUT": "0.1",
        }):
            reset_llm_semaphore()
            
            async with acquire_llm_slot():
                # While holding the only slot, try to acquire another
                with pytest.raises(asyncio.TimeoutError):
                    async with acquire_llm_slot():
                        pass
    
    @pytest.mark.asyncio
    async def test_custom_timeout_override(self):
        """Test that custom timeout can be passed to acquire_llm_slot."""
        with patch.dict("os.environ", {
            "LLM_MAX_CONCURRENT": "1",
            "LLM_ACQUIRE_TIMEOUT": "10.0",  # Long default
        }):
            reset_llm_semaphore()
            
            async with acquire_llm_slot():
                # Use short custom timeout
                with pytest.raises(asyncio.TimeoutError):
                    async with acquire_llm_slot(timeout=0.1):
                        pass
    
    @pytest.mark.asyncio
    async def test_shutdown_aborts_acquisition(self):
        """Test that shutdown request aborts slot acquisition."""
        with patch("integration_coworker.llm.concurrency.is_shutdown_requested", return_value=True):
            with pytest.raises(RuntimeError, match="Shutdown requested"):
                async with acquire_llm_slot():
                    pass
    
    @pytest.mark.asyncio
    async def test_exception_releases_slot(self):
        """Test that exceptions in the context release the slot."""
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "1"}):
            reset_llm_semaphore()
            
            try:
                async with acquire_llm_slot():
                    raise ValueError("test error")
            except ValueError:
                pass
            
            # Slot should be released despite exception
            metrics = get_concurrency_metrics()
            assert metrics["current_active"] == 0


class TestConcurrencyMetrics:
    """Tests for concurrency metrics tracking."""
    
    @pytest.mark.asyncio
    async def test_total_acquired_increments(self):
        """Test that total_acquired increments correctly."""
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "5"}):
            reset_llm_semaphore()
            
            for i in range(3):
                async with acquire_llm_slot():
                    pass
            
            metrics = get_concurrency_metrics()
            assert metrics["total_acquired"] == 3
    
    @pytest.mark.asyncio
    async def test_peak_active_tracked(self):
        """Test that peak_active tracks the maximum concurrent."""
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "5"}):
            reset_llm_semaphore()
            
            async def hold_slot():
                async with acquire_llm_slot():
                    await asyncio.sleep(0.1)
            
            # Run 3 concurrent holds
            await asyncio.gather(*[hold_slot() for _ in range(3)])
            
            metrics = get_concurrency_metrics()
            assert metrics["peak_active"] == 3
    
    @pytest.mark.asyncio
    async def test_total_timeouts_tracked(self):
        """Test that timeouts are tracked."""
        with patch.dict("os.environ", {
            "LLM_MAX_CONCURRENT": "1",
            "LLM_ACQUIRE_TIMEOUT": "0.05",
        }):
            reset_llm_semaphore()
            
            async with acquire_llm_slot():
                try:
                    async with acquire_llm_slot():
                        pass
                except asyncio.TimeoutError:
                    pass
            
            metrics = get_concurrency_metrics()
            assert metrics["total_timeouts"] == 1
    
    def test_utilization_calculation(self):
        """Test utilization percentage calculation."""
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "10"}):
            reset_llm_semaphore()
            config = get_concurrency_config()
            config.current_active = 5
            
            metrics = get_concurrency_metrics()
            assert metrics["utilization"] == 50.0


class TestWithLLMConcurrencyDecorator:
    """Tests for the @with_llm_concurrency decorator."""
    
    @pytest.mark.asyncio
    async def test_decorator_wraps_function(self):
        """Test that decorator wraps async function with concurrency control."""
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "1"}):
            reset_llm_semaphore()
            
            call_count = 0
            
            @with_llm_concurrency()
            async def mock_llm_call():
                nonlocal call_count
                call_count += 1
                return "response"
            
            result = await mock_llm_call()
            
            assert result == "response"
            assert call_count == 1
            
            metrics = get_concurrency_metrics()
            assert metrics["total_acquired"] == 1
    
    @pytest.mark.asyncio
    async def test_decorator_with_custom_timeout(self):
        """Test decorator with custom timeout parameter."""
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "1"}):
            reset_llm_semaphore()
            
            @with_llm_concurrency(timeout=0.05)
            async def slow_llm_call():
                await asyncio.sleep(1.0)
                return "response"
            
            async with acquire_llm_slot():
                # With slot held and short timeout, decorated function should timeout
                with pytest.raises(asyncio.TimeoutError):
                    await slow_llm_call()


class TestConcurrencyIntegration:
    """Integration tests for concurrency with realistic scenarios."""
    
    @pytest.mark.asyncio
    async def test_multiple_concurrent_llm_calls(self):
        """Simulate multiple concurrent LLM calls."""
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "3"}):
            reset_llm_semaphore()
            
            call_order = []
            max_concurrent = 0
            
            async def mock_llm_call(call_id: int):
                nonlocal max_concurrent
                async with acquire_llm_slot():
                    metrics = get_concurrency_metrics()
                    max_concurrent = max(max_concurrent, metrics["current_active"])
                    call_order.append(f"start_{call_id}")
                    await asyncio.sleep(0.02)  # Simulate API latency
                    call_order.append(f"end_{call_id}")
                    return f"response_{call_id}"
            
            # Run 10 concurrent calls with limit of 3
            results = await asyncio.gather(*[mock_llm_call(i) for i in range(10)])
            
            assert len(results) == 10
            assert max_concurrent <= 3
            assert all(f"response_{i}" in results for i in range(10))
    
    @pytest.mark.asyncio
    async def test_graceful_handling_under_load(self):
        """Test that system handles burst load gracefully."""
        with patch.dict("os.environ", {
            "LLM_MAX_CONCURRENT": "2",
            "LLM_ACQUIRE_TIMEOUT": "5.0",
        }):
            reset_llm_semaphore()
            
            async def fast_call():
                async with acquire_llm_slot():
                    await asyncio.sleep(0.01)
                    return True
            
            # Burst of 20 calls with limit of 2
            results = await asyncio.gather(*[fast_call() for _ in range(20)])
            
            assert all(results)
            
            metrics = get_concurrency_metrics()
            assert metrics["total_acquired"] == 20
            assert metrics["total_timeouts"] == 0
            assert metrics["peak_active"] <= 2


class TestLogConcurrencyStatus:
    """Tests for logging functionality."""
    
    def test_log_concurrency_status(self, caplog):
        """Test that status logging works."""
        reset_llm_semaphore()
        
        import logging
        with caplog.at_level(logging.INFO):
            log_concurrency_status()
        
        assert "LLM Concurrency" in caplog.text
        assert "active=" in caplog.text
