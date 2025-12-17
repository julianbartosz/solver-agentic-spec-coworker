"""Tests for integration-coworker-runtime package."""
import os
import pytest
from unittest.mock import patch, MagicMock


class TestExceptions:
    """Test exception hierarchy."""
    
    def test_exception_hierarchy(self):
        from integration_coworker_runtime import (
            IntegrationError,
            TransientIntegrationError,
            AuthIntegrationError,
        )
        
        assert issubclass(TransientIntegrationError, IntegrationError)
        assert issubclass(AuthIntegrationError, IntegrationError)
    
    def test_exception_messages(self):
        from integration_coworker_runtime import IntegrationError
        
        e = IntegrationError("Test error")
        assert str(e) == "Test error"


class TestAuth:
    """Test authentication handlers."""
    
    def test_no_auth(self):
        from integration_coworker_runtime import NoAuth
        
        auth = NoAuth()
        assert auth.get_headers() == {}
    
    def test_bearer_auth_with_token(self):
        from integration_coworker_runtime import BearerAuth
        
        auth = BearerAuth(token="test-token")
        assert auth.get_headers() == {"Authorization": "Bearer test-token"}
    
    def test_bearer_auth_with_env_var(self):
        from integration_coworker_runtime import BearerAuth
        
        with patch.dict(os.environ, {"TEST_TOKEN": "env-token"}):
            auth = BearerAuth(env_var="TEST_TOKEN")
            assert auth.get_headers() == {"Authorization": "Bearer env-token"}
    
    def test_bearer_auth_empty(self):
        from integration_coworker_runtime import BearerAuth
        
        auth = BearerAuth()
        assert auth.get_headers() == {}
    
    def test_api_key_auth(self):
        from integration_coworker_runtime import ApiKeyAuth
        
        auth = ApiKeyAuth(key="test-key")
        assert auth.get_headers() == {"X-API-Key": "test-key"}
    
    def test_api_key_auth_custom_header(self):
        from integration_coworker_runtime import ApiKeyAuth
        
        auth = ApiKeyAuth(key="test-key", header="X-Custom-Key")
        assert auth.get_headers() == {"X-Custom-Key": "test-key"}
    
    def test_basic_auth(self):
        from integration_coworker_runtime import BasicAuth
        import base64
        
        auth = BasicAuth(username="user", password="pass")
        headers = auth.get_headers()
        
        expected = base64.b64encode(b"user:pass").decode("utf-8")
        assert headers == {"Authorization": f"Basic {expected}"}
    
    def test_basic_auth_no_username(self):
        from integration_coworker_runtime import BasicAuth
        
        auth = BasicAuth(password="pass")
        assert auth.get_headers() == {}


class TestRetry:
    """Test retry handlers."""
    
    def test_no_retry(self):
        from integration_coworker_runtime import NoRetry
        
        retry = NoRetry()
        result = retry.execute(lambda: "success")
        assert result == "success"
    
    def test_no_retry_raises(self):
        from integration_coworker_runtime import NoRetry
        
        retry = NoRetry()
        with pytest.raises(ValueError, match="test error"):
            retry.execute(lambda: (_ for _ in ()).throw(ValueError("test error")))
    
    def test_exponential_retry_success(self):
        from integration_coworker_runtime import ExponentialRetry
        
        retry = ExponentialRetry(max_attempts=3)
        result = retry.execute(lambda: "success")
        assert result == "success"
    
    def test_exponential_retry_retries_on_failure(self):
        from integration_coworker_runtime import ExponentialRetry
        
        attempts = []
        
        def flaky_func():
            attempts.append(1)
            if len(attempts) < 3:
                raise Exception("Transient error")
            return "success"
        
        retry = ExponentialRetry(max_attempts=5, base_delay=0.01)
        result = retry.execute(flaky_func)
        
        assert result == "success"
        assert len(attempts) == 3
    
    def test_exponential_retry_exhausted(self):
        from integration_coworker_runtime import ExponentialRetry
        
        retry = ExponentialRetry(max_attempts=2, base_delay=0.01)
        
        with pytest.raises(Exception, match="Always fails"):
            retry.execute(lambda: (_ for _ in ()).throw(Exception("Always fails")))


class TestRateLimiter:
    """Test rate limiters."""
    
    def test_no_rate_limiter(self):
        from integration_coworker_runtime import NoRateLimiter
        
        limiter = NoRateLimiter()
        # Should not block
        for _ in range(100):
            limiter.acquire()
    
    def test_token_bucket_allows_burst(self):
        from integration_coworker_runtime import TokenBucketRateLimiter
        
        limiter = TokenBucketRateLimiter(rps=10, burst=5)
        
        # Should allow burst of 5
        for _ in range(5):
            limiter.acquire()
    
    def test_sliding_window_allows_max_requests(self):
        from integration_coworker_runtime import SlidingWindowRateLimiter
        
        limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60)
        
        # Should allow max_requests
        for _ in range(5):
            limiter.acquire()


class TestIntegrationClient:
    """Test IntegrationClient base class."""
    
    def test_client_initialization(self):
        from integration_coworker_runtime import IntegrationClient
        
        client = IntegrationClient(base_url="https://api.example.com/v1")
        assert client.base_url == "https://api.example.com/v1"
    
    def test_client_strips_trailing_slash(self):
        from integration_coworker_runtime import IntegrationClient
        
        client = IntegrationClient(base_url="https://api.example.com/v1/")
        assert client.base_url == "https://api.example.com/v1"
    
    def test_client_context_manager(self):
        from integration_coworker_runtime import IntegrationClient
        
        with IntegrationClient(base_url="https://api.example.com") as client:
            assert client.base_url == "https://api.example.com"


class TestIntegrationHttpClient:
    """Test IntegrationHttpClient."""
    
    def test_http_client_initialization(self):
        from integration_coworker_runtime import IntegrationHttpClient
        
        client = IntegrationHttpClient(
            base_url="https://api.example.com",
            api_key="test-key",
        )
        assert client.base_url == "https://api.example.com"
        assert client.api_key == "test-key"
    
    def test_http_client_context_manager(self):
        from integration_coworker_runtime import IntegrationHttpClient
        
        with IntegrationHttpClient(base_url="https://api.example.com") as client:
            assert client.base_url == "https://api.example.com"
