"""
Circuit Breaker Tests (Production Hardening C-2)

Tests for the circuit breaker implementation to prevent cascading failures
when external LLM APIs are down or degraded.
"""

import asyncio
import pytest
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

from integration_coworker.llm.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerState,
    CircuitOpenError,
    CircuitState,
    get_circuit_breaker,
    reset_circuit_breaker,
    make_circuit_key,
)


@pytest.fixture(autouse=True)
def reset_circuit():
    """Reset circuit breaker before each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


class TestCircuitBreakerBasics:
    """Basic circuit breaker functionality tests."""

    def test_make_circuit_key(self):
        """Test circuit key generation without base_url."""
        assert make_circuit_key("openai", "gpt-4o") == "openai:gpt-4o"
        assert make_circuit_key("anthropic", "claude-3") == "anthropic:claude-3"
        assert make_circuit_key("google", "gemini-2.0") == "google:gemini-2.0"

    def test_make_circuit_key_with_base_url(self):
        """Test circuit key generation with base_url includes scheme+host+port.
        
        Keys should include scheme and port to distinguish:
        - HTTP vs HTTPS endpoints (security posture)
        - Different ports on same host (different services)
        
        Path/query strings are NOT included to avoid key explosion.
        """
        # Full URL with path - path is stripped
        assert make_circuit_key("openai", "gpt-4o", "https://proxy.example.com/v1") == "openai:gpt-4o@https://proxy.example.com"
        
        # URL with explicit port
        assert make_circuit_key("openai", "gpt-4o", "https://api.example.com:8080/v1") == "openai:gpt-4o@https://api.example.com:8080"
        
        # HTTP vs HTTPS get different keys (security distinction)
        key_https = make_circuit_key("openai", "gpt-4o", "https://proxy.example.com")
        key_http = make_circuit_key("openai", "gpt-4o", "http://proxy.example.com")
        assert key_https != key_http
        assert "https://" in key_https
        assert "http://" in key_http
        
        # Different ports get different keys
        key_port_8080 = make_circuit_key("openai", "gpt-4o", "http://localhost:8080/api")
        key_port_9090 = make_circuit_key("openai", "gpt-4o", "http://localhost:9090/api")
        assert key_port_8080 != key_port_9090
        assert ":8080" in key_port_8080
        assert ":9090" in key_port_9090
        
        # None base_url (default)
        assert make_circuit_key("openai", "gpt-4o", None) == "openai:gpt-4o"
        
        # Different hosts get different keys
        key1 = make_circuit_key("openai", "gpt-4o", "https://proxy1.example.com/v1")
        key2 = make_circuit_key("openai", "gpt-4o", "https://proxy2.example.com/v1")
        assert key1 != key2
        assert "proxy1" in key1
        assert "proxy2" in key2

    def test_initial_state_is_closed(self):
        """Test that new circuits start in closed state."""
        cb = CircuitBreaker()
        assert cb.get_state("test:model") == CircuitState.CLOSED

    def test_can_execute_when_closed(self):
        """Test that requests pass when circuit is closed."""
        cb = CircuitBreaker()
        assert cb.can_execute("test:model") is True

    def test_success_resets_failure_count(self):
        """Test that successes reduce failure count."""
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=5))
        
        # Record some failures
        cb.record_failure("test:model")
        cb.record_failure("test:model")
        assert cb.get_metrics("test:model")["failure_count"] == 2
        
        # Success should reduce count
        cb.record_success("test:model")
        assert cb.get_metrics("test:model")["failure_count"] == 1


class TestCircuitTripping:
    """Tests for circuit tripping behavior."""

    def test_circuit_trips_after_threshold(self):
        """Test that circuit trips after failure threshold."""
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))
        
        cb.record_failure("test:model")
        assert cb.get_state("test:model") == CircuitState.CLOSED
        
        cb.record_failure("test:model")
        assert cb.get_state("test:model") == CircuitState.CLOSED
        
        cb.record_failure("test:model")  # Third failure trips circuit
        assert cb.get_state("test:model") == CircuitState.OPEN

    def test_open_circuit_raises_error(self):
        """Test that open circuit raises CircuitOpenError."""
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2, recovery_timeout=60))
        
        cb.record_failure("test:model")
        cb.record_failure("test:model")
        
        with pytest.raises(CircuitOpenError) as exc_info:
            cb.can_execute("test:model")
        
        assert exc_info.value.circuit_key == "test:model"
        assert exc_info.value.time_until_recovery > 0

    def test_circuit_error_message(self):
        """Test CircuitOpenError message format."""
        error = CircuitOpenError("openai:gpt-4o", 30.5)
        assert "openai:gpt-4o" in str(error)
        assert "30.5s" in str(error)


class TestCircuitRecovery:
    """Tests for circuit recovery behavior."""

    def test_circuit_transitions_to_half_open(self):
        """Test that circuit transitions to half-open after timeout."""
        cb = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,  # 100ms for fast test
        ))
        
        cb.record_failure("test:model")
        assert cb.get_state("test:model") == CircuitState.OPEN
        
        # Wait for recovery timeout
        time.sleep(0.15)
        
        # Next can_execute should transition to half-open
        assert cb.can_execute("test:model") is True
        assert cb.get_state("test:model") == CircuitState.HALF_OPEN

    def test_half_open_success_closes_circuit(self):
        """Test that success in half-open state closes circuit."""
        cb = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,
            half_open_requests=1,
        ))
        
        cb.record_failure("test:model")
        time.sleep(0.15)
        cb.can_execute("test:model")  # Transition to half-open
        
        cb.record_success("test:model")
        assert cb.get_state("test:model") == CircuitState.CLOSED
        assert cb.get_metrics("test:model")["failure_count"] == 0

    def test_half_open_failure_reopens_circuit(self):
        """Test that failure in half-open state reopens circuit."""
        cb = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,
            half_open_requests=1,
        ))
        
        cb.record_failure("test:model")
        time.sleep(0.15)
        cb.can_execute("test:model")  # Transition to half-open
        
        cb.record_failure("test:model")
        assert cb.get_state("test:model") == CircuitState.OPEN

    def test_half_open_limits_requests(self):
        """Test that half-open state limits concurrent requests."""
        cb = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,
            half_open_requests=1,
        ))
        
        cb.record_failure("test:model")
        time.sleep(0.15)
        
        # First request allowed
        assert cb.can_execute("test:model") is True
        
        # Second request blocked
        with pytest.raises(CircuitOpenError):
            cb.can_execute("test:model")


class TestThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_failures(self):
        """Test that concurrent failures are counted correctly."""
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=10))
        results = []
        
        def record_failure():
            for _ in range(5):
                cb.record_failure("test:model")
                results.append(cb.get_metrics("test:model")["failure_count"])
        
        threads = [threading.Thread(target=record_failure) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should have recorded 20 failures
        assert cb.get_metrics("test:model")["failure_count"] >= 10  # Circuit tripped at 10

    def test_concurrent_circuit_creation(self):
        """Test that concurrent circuit creation doesn't cause issues."""
        cb = CircuitBreaker()
        keys = [f"provider{i}:model{j}" for i in range(5) for j in range(5)]
        
        def check_circuits():
            for key in keys:
                assert cb.can_execute(key) is True
        
        threads = [threading.Thread(target=check_circuits) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # All circuits should exist
        assert len(cb._circuits) == 25


class TestMultipleCircuits:
    """Tests for per-circuit isolation."""

    def test_circuits_are_independent(self):
        """Test that different circuits are independent."""
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))
        
        cb.record_failure("openai:gpt-4o")
        cb.record_failure("openai:gpt-4o")
        
        # OpenAI circuit should be open
        assert cb.get_state("openai:gpt-4o") == CircuitState.OPEN
        
        # Anthropic circuit should still be closed
        assert cb.get_state("anthropic:claude-3") == CircuitState.CLOSED
        assert cb.can_execute("anthropic:claude-3") is True

    def test_reset_single_circuit(self):
        """Test resetting a single circuit."""
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1))
        
        cb.record_failure("circuit1")
        cb.record_failure("circuit2")
        
        cb.reset("circuit1")
        
        assert cb.get_state("circuit1") == CircuitState.CLOSED
        assert cb.get_state("circuit2") == CircuitState.OPEN

    def test_reset_all_circuits(self):
        """Test resetting all circuits."""
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1))
        
        cb.record_failure("circuit1")
        cb.record_failure("circuit2")
        cb.record_failure("circuit3")
        
        cb.reset()
        
        assert cb.get_state("circuit1") == CircuitState.CLOSED
        assert cb.get_state("circuit2") == CircuitState.CLOSED
        assert cb.get_state("circuit3") == CircuitState.CLOSED


class TestGlobalInstance:
    """Tests for global circuit breaker instance."""

    def test_get_circuit_breaker_returns_singleton(self):
        """Test that get_circuit_breaker returns same instance."""
        cb1 = get_circuit_breaker()
        cb2 = get_circuit_breaker()
        assert cb1 is cb2

    def test_reset_circuit_breaker_creates_new_instance(self):
        """Test that reset creates new instance."""
        cb1 = get_circuit_breaker()
        reset_circuit_breaker()
        cb2 = get_circuit_breaker()
        assert cb1 is not cb2


class TestConfigFromEnv:
    """Tests for environment variable configuration."""

    def test_config_from_env(self):
        """Test that config reads from environment variables."""
        with patch.dict("os.environ", {
            "LLM_CIRCUIT_FAILURE_THRESHOLD": "10",
            "LLM_CIRCUIT_RECOVERY_TIMEOUT": "120",
            "LLM_CIRCUIT_HALF_OPEN_REQUESTS": "3",
        }):
            config = CircuitBreakerConfig()
            assert config.failure_threshold == 10
            assert config.recovery_timeout == 120.0
            assert config.half_open_requests == 3

    def test_config_defaults(self):
        """Test default configuration values."""
        # Clear any env vars
        with patch.dict("os.environ", {}, clear=True):
            config = CircuitBreakerConfig()
            assert config.failure_threshold == 5
            assert config.recovery_timeout == 60.0
            assert config.half_open_requests == 1


class TestIntegrationWithRetry:
    """Tests for integration with retry logic."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_prevents_retries(self):
        """Test that open circuit prevents retry attempts."""
        from integration_coworker.llm.async_client import _retry_async
        
        # Set up circuit breaker to be open
        cb = get_circuit_breaker()
        for _ in range(5):
            cb.record_failure("test:model")
        
        call_count = 0
        
        async def failing_fn():
            nonlocal call_count
            call_count += 1
            raise Exception("Should not be called")
        
        with pytest.raises(CircuitOpenError):
            await _retry_async(failing_fn, circuit_key="test:model")
        
        # Function should never have been called
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_counts_after_retries(self):
        """Test that circuit breaker counts failures after retries exhausted."""
        from integration_coworker.llm.async_client import _retry_async
        from integration_coworker.llm.exceptions import LLMTransientError
        
        reset_circuit_breaker()
        cb = get_circuit_breaker()
        
        call_count = 0
        
        async def failing_fn():
            nonlocal call_count
            call_count += 1
            # Raise a raw exception that looks like a transient error
            # (typed exceptions don't get re-classified)
            raise Exception("503 Service Unavailable")
        
        # This should exhaust retries and record ONE failure
        with pytest.raises(Exception):
            await _retry_async(failing_fn, max_attempts=3, base_delay=0.01, circuit_key="test:model")
        
        # Function called 3 times (initial + 2 retries)
        assert call_count == 3
        
        # Circuit should have recorded 1 failure (not 3)
        metrics = cb.get_metrics("test:model")
        assert metrics["failure_count"] == 1
