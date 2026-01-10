"""
Tests for Section 3.11: Runtime Policy Library

Tests auth, retry, and rate limiting implementations.
"""
import base64
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from integration_coworker.runtime import (
    IntegrationClient,
    NoAuth,
    BearerAuth,
    ApiKeyAuth,
    BasicAuth,
    NoRetry,
    ExponentialRetry,
    NoRateLimiter,
    TokenBucketRateLimiter,
    SlidingWindowRateLimiter,
)


class TestNoAuth:
    """Tests for NoAuth."""
    
    def test_returns_empty_headers(self):
        auth = NoAuth()
        assert auth.get_headers() == {}


class TestBearerAuth:
    """Tests for BearerAuth."""
    
    def test_with_direct_token(self):
        auth = BearerAuth(token="my-secret-token")
        headers = auth.get_headers()
        assert headers == {"Authorization": "Bearer my-secret-token"}
    
    def test_with_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_TOKEN", "env-token-value")
        auth = BearerAuth(env_var="TEST_TOKEN")
        headers = auth.get_headers()
        assert headers == {"Authorization": "Bearer env-token-value"}
    
    def test_direct_token_priority(self, monkeypatch):
        """Direct token takes priority over env var."""
        monkeypatch.setenv("TEST_TOKEN", "env-token")
        auth = BearerAuth(token="direct-token", env_var="TEST_TOKEN")
        headers = auth.get_headers()
        assert headers == {"Authorization": "Bearer direct-token"}
    
    def test_empty_token_returns_empty(self):
        auth = BearerAuth()
        assert auth.get_headers() == {}
    
    def test_missing_env_var(self):
        auth = BearerAuth(env_var="NONEXISTENT_TOKEN_VAR")
        assert auth.get_headers() == {}


class TestApiKeyAuth:
    """Tests for ApiKeyAuth."""
    
    def test_with_direct_key(self):
        auth = ApiKeyAuth(key="api-key-123")
        headers = auth.get_headers()
        assert headers == {"X-API-Key": "api-key-123"}
    
    def test_custom_header(self):
        auth = ApiKeyAuth(key="api-key-123", header="Authorization")
        headers = auth.get_headers()
        assert headers == {"Authorization": "api-key-123"}
    
    def test_with_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "key-from-env")
        auth = ApiKeyAuth(env_var="MY_API_KEY")
        headers = auth.get_headers()
        assert headers == {"X-API-Key": "key-from-env"}
    
    def test_empty_key_returns_empty(self):
        auth = ApiKeyAuth()
        assert auth.get_headers() == {}


class TestBasicAuth:
    """Tests for BasicAuth."""
    
    def test_with_credentials(self):
        auth = BasicAuth(username="user", password="pass")
        headers = auth.get_headers()
        
        expected = base64.b64encode(b"user:pass").decode("utf-8")
        assert headers == {"Authorization": f"Basic {expected}"}
    
    def test_with_env_vars(self, monkeypatch):
        monkeypatch.setenv("BASIC_USER", "envuser")
        monkeypatch.setenv("BASIC_PASS", "envpass")
        
        auth = BasicAuth(
            username_env_var="BASIC_USER",
            password_env_var="BASIC_PASS",
        )
        headers = auth.get_headers()
        
        expected = base64.b64encode(b"envuser:envpass").decode("utf-8")
        assert headers == {"Authorization": f"Basic {expected}"}
    
    def test_no_username_returns_empty(self):
        auth = BasicAuth(password="only-password")
        assert auth.get_headers() == {}
    
    def test_empty_password_works(self):
        auth = BasicAuth(username="user")
        headers = auth.get_headers()
        
        expected = base64.b64encode(b"user:").decode("utf-8")
        assert headers == {"Authorization": f"Basic {expected}"}


class TestNoRetry:
    """Tests for NoRetry."""
    
    def test_executes_once(self):
        call_count = [0]
        
        def func():
            call_count[0] += 1
            return "result"
        
        retry = NoRetry()
        result = retry.execute(func)
        
        assert result == "result"
        assert call_count[0] == 1
    
    def test_raises_immediately(self):
        def func():
            raise ValueError("test error")
        
        retry = NoRetry()
        with pytest.raises(ValueError, match="test error"):
            retry.execute(func)


class TestExponentialRetry:
    """Tests for ExponentialRetry."""
    
    def test_success_on_first_try(self):
        call_count = [0]
        
        def func():
            call_count[0] += 1
            return "success"
        
        retry = ExponentialRetry(max_attempts=3)
        result = retry.execute(func)
        
        assert result == "success"
        assert call_count[0] == 1
    
    def test_retries_on_failure(self):
        call_count = [0]
        
        def func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("temporary error")
            return "success"
        
        retry = ExponentialRetry(max_attempts=3, base_delay=0.01)
        result = retry.execute(func)
        
        assert result == "success"
        assert call_count[0] == 3
    
    def test_exhausts_retries(self):
        call_count = [0]
        
        def func():
            call_count[0] += 1
            raise ValueError("persistent error")
        
        retry = ExponentialRetry(max_attempts=3, base_delay=0.01)
        
        with pytest.raises(ValueError, match="persistent error"):
            retry.execute(func)
        
        assert call_count[0] == 3
    
    def test_non_retryable_exception(self):
        """Non-retryable exceptions should not be retried."""
        call_count = [0]
        
        def func():
            call_count[0] += 1
            raise KeyboardInterrupt()
        
        # Only retry ValueError
        retry = ExponentialRetry(
            max_attempts=3,
            base_delay=0.01,
            retryable_exceptions=(ValueError,),
        )
        
        with pytest.raises(KeyboardInterrupt):
            retry.execute(func)
        
        assert call_count[0] == 1
    
    def test_delay_calculation(self):
        retry = ExponentialRetry(
            base_delay=1.0,
            exponential_base=2.0,
            max_delay=10.0,
            jitter=False,
        )
        
        assert retry._calculate_delay(0) == 1.0   # 1 * 2^0 = 1
        assert retry._calculate_delay(1) == 2.0   # 1 * 2^1 = 2
        assert retry._calculate_delay(2) == 4.0   # 1 * 2^2 = 4
        assert retry._calculate_delay(3) == 8.0   # 1 * 2^3 = 8
        assert retry._calculate_delay(4) == 10.0  # 1 * 2^4 = 16, capped at 10


class TestNoRateLimiter:
    """Tests for NoRateLimiter."""
    
    def test_always_allows(self):
        limiter = NoRateLimiter()
        
        # Should complete immediately
        start = time.monotonic()
        for _ in range(100):
            limiter.acquire()
        elapsed = time.monotonic() - start
        
        assert elapsed < 0.1  # Should be nearly instant


class TestTokenBucketRateLimiter:
    """Tests for TokenBucketRateLimiter."""
    
    def test_allows_burst(self):
        """Should allow immediate burst up to capacity."""
        limiter = TokenBucketRateLimiter(rps=10, burst=5)
        
        start = time.monotonic()
        for _ in range(5):
            limiter.acquire()
        elapsed = time.monotonic() - start
        
        assert elapsed < 0.1  # Burst should be fast
    
    def test_rate_limits_after_burst(self):
        """Should rate limit after burst is exhausted."""
        limiter = TokenBucketRateLimiter(rps=100, burst=2)  # 100 rps = 10ms per token
        
        start = time.monotonic()
        # First 2 are burst, 3rd requires waiting
        for _ in range(3):
            limiter.acquire()
        elapsed = time.monotonic() - start
        
        # Should take at least ~10ms for the 3rd request
        assert elapsed >= 0.008  # Allow some tolerance


class TestSlidingWindowRateLimiter:
    """Tests for SlidingWindowRateLimiter."""
    
    def test_allows_within_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=1.0)
        
        start = time.monotonic()
        for _ in range(10):
            limiter.acquire()
        elapsed = time.monotonic() - start
        
        assert elapsed < 0.1  # Should be nearly instant
    
    def test_blocks_over_limit(self):
        """Should block when limit is exceeded."""
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=0.1)
        
        # First 2 should be instant
        limiter.acquire()
        limiter.acquire()
        
        # 3rd should wait for window to slide
        start = time.monotonic()
        limiter.acquire()
        elapsed = time.monotonic() - start
        
        assert elapsed >= 0.05  # Should wait at least half the window


class TestIntegrationClient:
    """Tests for IntegrationClient base class."""
    
    def test_initialization(self):
        client = IntegrationClient(base_url="https://api.example.com/v1")
        
        assert client.base_url == "https://api.example.com/v1"
        assert isinstance(client.auth, NoAuth)
        assert isinstance(client.retry, NoRetry)
        assert isinstance(client.rate_limiter, NoRateLimiter)
    
    def test_base_url_trailing_slash_stripped(self):
        client = IntegrationClient(base_url="https://api.example.com/v1/")
        assert client.base_url == "https://api.example.com/v1"
    
    def test_custom_policies(self):
        auth = BearerAuth(token="test")
        retry = ExponentialRetry()
        rate_limiter = TokenBucketRateLimiter()
        
        client = IntegrationClient(
            base_url="https://api.example.com",
            auth=auth,
            retry=retry,
            rate_limiter=rate_limiter,
        )
        
        assert client.auth is auth
        assert client.retry is retry
        assert client.rate_limiter is rate_limiter
    
    def test_context_manager(self):
        """Should support context manager protocol."""
        with IntegrationClient(base_url="https://api.example.com") as client:
            assert client.base_url == "https://api.example.com"
        
        # After context exit, http client should be closed
        assert client._http is None
    
    def test_httpx_lazy_load(self):
        """HTTP client should not be loaded until first request."""
        client = IntegrationClient(base_url="https://api.example.com")
        assert client._http is None  # Not loaded yet


class TestRuntimeIntegration:
    """Integration tests for the runtime library."""
    
    def test_imports_work(self):
        """All exports should be importable."""
        from integration_coworker.runtime import (
            IntegrationClient,
            BaseAuth,
            NoAuth,
            BearerAuth,
            ApiKeyAuth,
            BasicAuth,
            BaseRetry,
            NoRetry,
            ExponentialRetry,
            BaseRateLimiter,
            NoRateLimiter,
            TokenBucketRateLimiter,
            SlidingWindowRateLimiter,
        )
        
        assert IntegrationClient is not None
    
    def test_generated_client_pattern(self):
        """Test the pattern for generated clients."""
        
        class GeneratedStripeClient(IntegrationClient):
            """Example of a generated client."""
            
            def __init__(self):
                super().__init__(
                    base_url="https://api.stripe.com/v1",
                    auth=BearerAuth(env_var="STRIPE_API_KEY"),
                    retry=ExponentialRetry(max_attempts=3),
                    rate_limiter=TokenBucketRateLimiter(rps=25),
                )
            
            def create_checkout_session(self, amount: int, currency: str = "usd"):
                """Example method that would call the API."""
                # In real code: return self.post("checkout/sessions", json={...})
                pass
        
        client = GeneratedStripeClient()
        assert client.base_url == "https://api.stripe.com/v1"
        assert isinstance(client.auth, BearerAuth)
        assert isinstance(client.retry, ExponentialRetry)
        assert isinstance(client.rate_limiter, TokenBucketRateLimiter)
