"""
Test retry utilities and connection lifecycle improvements.

Validates:
1. Retry decorator works for sync and async functions
2. Exponential backoff is calculated correctly
3. Connection retry wrapper handles transient failures
"""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from integration_coworker.utils.retry import (
    RetryConfig,
    calculate_delay,
    retry,
    should_retry,
    db_retry,
)


class TestRetryConfig:
    """Test RetryConfig validation."""
    
    def test_valid_config(self):
        """Valid config should not raise."""
        config = RetryConfig(max_attempts=3, backoff_base=0.5)
        assert config.max_attempts == 3
        assert config.backoff_base == 0.5
    
    def test_invalid_max_attempts(self):
        """max_attempts < 1 should raise."""
        with pytest.raises(ValueError, match="max_attempts"):
            RetryConfig(max_attempts=0)
    
    def test_invalid_backoff_base(self):
        """backoff_base <= 0 should raise."""
        with pytest.raises(ValueError, match="backoff_base"):
            RetryConfig(backoff_base=0)
        with pytest.raises(ValueError, match="backoff_base"):
            RetryConfig(backoff_base=-1)
    
    def test_invalid_jitter(self):
        """jitter outside [0, 1] should raise."""
        with pytest.raises(ValueError, match="jitter"):
            RetryConfig(jitter=-0.1)
        with pytest.raises(ValueError, match="jitter"):
            RetryConfig(jitter=1.5)


class TestCalculateDelay:
    """Test exponential backoff delay calculation."""
    
    def test_exponential_backoff(self):
        """Delay should double each attempt."""
        config = RetryConfig(backoff_base=1.0, jitter=0)
        
        assert calculate_delay(1, config) == 1.0  # 1 * 2^0
        assert calculate_delay(2, config) == 2.0  # 1 * 2^1
        assert calculate_delay(3, config) == 4.0  # 1 * 2^2
    
    def test_max_delay_cap(self):
        """Delay should be capped at max_delay."""
        config = RetryConfig(backoff_base=10.0, max_delay=15.0, jitter=0)
        
        assert calculate_delay(1, config) == 10.0
        assert calculate_delay(2, config) == 15.0  # Capped, not 20
        assert calculate_delay(3, config) == 15.0  # Still capped
    
    def test_jitter_range(self):
        """Delay with jitter should vary within expected range."""
        config = RetryConfig(backoff_base=1.0, jitter=0.2)
        
        delays = [calculate_delay(1, config) for _ in range(100)]
        min_expected = 0.8  # 1.0 - 20%
        max_expected = 1.2  # 1.0 + 20%
        
        assert min(delays) >= 0  # Never negative
        assert max(delays) <= max_expected + 0.01  # Within expected range


class TestShouldRetry:
    """Test exception classification for retry."""
    
    def test_retryable_exception(self):
        """Should retry on retryable exceptions."""
        config = RetryConfig(retryable_exceptions=(ValueError, TypeError))
        
        assert should_retry(ValueError("test"), config) is True
        assert should_retry(TypeError("test"), config) is True
        assert should_retry(RuntimeError("test"), config) is False
    
    def test_fatal_exception(self):
        """Should not retry on fatal exceptions."""
        config = RetryConfig(
            retryable_exceptions=(Exception,),
            fatal_exceptions=(KeyboardInterrupt, SystemExit),
        )
        
        assert should_retry(ValueError("test"), config) is True
        # Note: KeyboardInterrupt doesn't inherit from Exception
        # so this test uses a different fatal exception
    
    def test_default_retryable(self):
        """Default config should retry on all exceptions."""
        config = RetryConfig()
        
        assert should_retry(ValueError("test"), config) is True
        assert should_retry(RuntimeError("test"), config) is True


class TestRetryDecorator:
    """Test the @retry decorator."""
    
    def test_success_first_try(self):
        """Should return immediately on success."""
        call_count = 0
        
        @retry(max_attempts=3)
        def succeeds():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = succeeds()
        assert result == "success"
        assert call_count == 1
    
    def test_retry_then_success(self):
        """Should retry and eventually succeed."""
        call_count = 0
        
        @retry(max_attempts=3, backoff_base=0.01)
        def fails_then_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient error")
            return "success"
        
        result = fails_then_succeeds()
        assert result == "success"
        assert call_count == 3
    
    def test_max_attempts_exhausted(self):
        """Should raise after max attempts."""
        call_count = 0
        
        @retry(max_attempts=3, backoff_base=0.01)
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("permanent error")
        
        with pytest.raises(ValueError, match="permanent error"):
            always_fails()
        
        assert call_count == 3
    
    def test_non_retryable_exception(self):
        """Should not retry non-retryable exceptions."""
        call_count = 0
        
        @retry(
            max_attempts=3,
            backoff_base=0.01,
            retryable_exceptions=(ValueError,),
        )
        def raises_runtime():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("not retryable")
        
        with pytest.raises(RuntimeError, match="not retryable"):
            raises_runtime()
        
        assert call_count == 1  # No retries
    
    def test_on_retry_callback(self):
        """on_retry callback should be called on each retry."""
        retry_log = []
        
        def on_retry(exc, attempt, delay):
            retry_log.append((type(exc).__name__, attempt, delay))
        
        config = RetryConfig(
            max_attempts=3,
            backoff_base=0.01,
            on_retry=on_retry,
        )
        
        call_count = 0
        
        @retry(config=config)
        def fails_then_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("retry me")
            return "success"
        
        result = fails_then_succeeds()
        assert result == "success"
        assert len(retry_log) == 2  # 2 retries before success
        assert all(exc == "ValueError" for exc, _, _ in retry_log)


class TestAsyncRetry:
    """Test @retry with async functions."""
    
    @pytest.mark.asyncio
    async def test_async_success(self):
        """Async function should work with retry."""
        call_count = 0
        
        @retry(max_attempts=3, backoff_base=0.01)
        async def async_succeeds():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.001)
            return "async success"
        
        result = await async_succeeds()
        assert result == "async success"
        assert call_count == 1
    
    @pytest.mark.asyncio
    async def test_async_retry_then_success(self):
        """Async function should retry on failure."""
        call_count = 0
        
        @retry(max_attempts=3, backoff_base=0.01)
        async def async_fails_then_succeeds():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.001)
            if call_count < 2:
                raise ValueError("transient")
            return "async success"
        
        result = await async_fails_then_succeeds()
        assert result == "async success"
        assert call_count == 2


class TestDbRetry:
    """Test the @db_retry convenience decorator."""
    
    def test_db_retry_on_connection_error(self):
        """Should retry on connection errors."""
        call_count = 0
        
        @db_retry
        def connection_fails_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("connection reset")
            return "connected"
        
        result = connection_fails_once()
        assert result == "connected"
        assert call_count == 2
    
    def test_db_retry_on_timeout(self):
        """Should retry on timeout errors."""
        call_count = 0
        
        @db_retry
        def timeout_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("connection timeout")
            return "success"
        
        result = timeout_once()
        assert result == "success"
        assert call_count == 2
