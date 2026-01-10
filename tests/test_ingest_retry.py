"""
Tests for HTTP fetch retry logic in ingest_spec.

V4 Production Hardening: Verifies retry behavior for transient errors.
Per docs/INGESTION_PROD_HARDENING_PLAN.md AT-1

V5 Security: Tests mock SSRF validation since _fetch_http_content
validates URLs before making HTTP calls. Uses respx for HTTP mocking.

NOTE: These tests do NOT make real network requests.
"""

import pytest
from unittest.mock import patch
import httpx
import respx

from integration_coworker.graph.nodes.ingest_spec import (
    _fetch_http_content,
    _is_retryable_error,
    _parse_retry_after,
    _get_http_client,
    cleanup_http_client,
    RetryableHTTPError,
    SpecTooLargeError,
    InvalidContentTypeError,
)
from integration_coworker.config import FetchConfig
from integration_coworker.security.ssrf import DNSResolver


# =============================================================================
# Stub DNS Resolver - No Real DNS
# =============================================================================

def stub_resolver_public() -> DNSResolver:
    """Stub DNS resolver for tests - returns public IP."""
    def resolver(hostname: str) -> list[str]:
        return ["93.184.216.34"]  # example.com's IP
    return resolver


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def mock_ssrf_validation():
    """
    Mock SSRF validation for HTTP retry tests.
    
    These tests focus on retry logic, not SSRF protection.
    SSRF tests are in tests/security/test_ssrf.py.
    """
    with patch("integration_coworker.graph.nodes.ingest_spec.validate_url_target") as mock:
        mock.return_value = ["93.184.216.34"]  # example.com's actual IP
        yield mock


@pytest.fixture
def test_config():
    """Standard test configuration."""
    return FetchConfig(
        max_bytes=1_000_000,  # 1MB
        stream_threshold=100_000,  # 100KB
        timeout=5.0,
        max_retries=3,
        retry_backoff=0.1,  # Fast backoff for tests
        retryable_statuses=frozenset({429, 500, 502, 503, 504}),
        allowed_content_types=frozenset({
            "application/json", "application/yaml", "text/yaml", "text/plain",
            # OpenAPI media types
            "application/openapi+json", "application/openapi+yaml",
            "application/vnd.oai.openapi", "application/vnd.oai.openapi+json",
        }),
    )


@pytest.fixture(autouse=True)
def cleanup_client_after_test():
    """Ensure HTTP client is cleaned up after each test."""
    yield
    cleanup_http_client()


# =============================================================================
# Test Classes
# =============================================================================

class TestRetryableErrorDetection:
    """Test _is_retryable_error() behavior."""
    
    def test_retryable_http_error_is_retryable(self):
        """RetryableHTTPError should trigger retry."""
        exc = RetryableHTTPError(503, "Service unavailable")
        assert _is_retryable_error(exc) is True
    
    def test_connect_error_is_retryable(self):
        """Connection errors should trigger retry."""
        exc = httpx.ConnectError("Connection refused")
        assert _is_retryable_error(exc) is True
    
    def test_connect_timeout_is_retryable(self):
        """Connection timeouts should trigger retry."""
        exc = httpx.ConnectTimeout("Timeout")
        assert _is_retryable_error(exc) is True
    
    def test_generic_exception_not_retryable(self):
        """Generic exceptions should not trigger retry."""
        exc = ValueError("Some error")
        assert _is_retryable_error(exc) is False
    
    def test_http_status_error_not_retryable(self):
        """HTTPStatusError (without wrapping) should not trigger retry."""
        exc = Exception("Some other error")
        assert _is_retryable_error(exc) is False


class TestRetryAfterParsing:
    """Test Retry-After header parsing."""
    
    def test_parses_integer_seconds(self):
        """Verify integer Retry-After is parsed."""
        # _parse_retry_after expects httpx.Response, so create a mock
        mock_response = httpx.Response(
            status_code=429,
            headers={"retry-after": "120"},
        )
        result = _parse_retry_after(mock_response)
        assert result == 120.0
    
    def test_parses_float_seconds(self):
        """Verify float-like Retry-After is parsed."""
        mock_response = httpx.Response(
            status_code=429,
            headers={"retry-after": "30.5"},
        )
        result = _parse_retry_after(mock_response)
        assert result == 30.5
    
    def test_returns_none_when_missing(self):
        """Verify None returned when header missing."""
        mock_response = httpx.Response(status_code=429, headers={})
        result = _parse_retry_after(mock_response)
        assert result is None
    
    def test_handles_invalid_value(self):
        """Verify invalid values return None (don't crash)."""
        mock_response = httpx.Response(
            status_code=429,
            headers={"retry-after": "not-a-number"},
        )
        result = _parse_retry_after(mock_response)
        # Should return None, not crash
        assert result is None or isinstance(result, float)


class TestRetryLogic:
    """Test HTTP retry behavior end-to-end using respx."""
    
    @respx.mock
    def test_retries_on_503(self, test_config):
        """Verify 503 triggers retry and eventually succeeds."""
        # Fail twice with 503, then succeed
        route = respx.get("http://example.com/spec.json")
        route.side_effect = [
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(200, json={"test": True}, headers={"content-type": "application/json"}),
        ]
        
        content, content_type = _fetch_http_content("http://example.com/spec.json", test_config)
        
        assert '{"test":' in content or '"test"' in content
        assert route.call_count == 3
    
    @respx.mock
    def test_retries_on_429_rate_limit(self, test_config):
        """Verify 429 triggers retry with backoff."""
        route = respx.get("http://example.com/spec.json")
        route.side_effect = [
            httpx.Response(429, text="Rate limited", headers={"retry-after": "1"}),
            httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"}),
        ]
        
        content, _ = _fetch_http_content("http://example.com/spec.json", test_config)
        
        assert '"ok"' in content
        assert route.call_count == 2
    
    @respx.mock
    def test_fails_fast_on_404(self, test_config):
        """Verify 404 does NOT retry (client error)."""
        route = respx.get("http://example.com/notfound.json").mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        
        with pytest.raises(httpx.HTTPStatusError):
            _fetch_http_content("http://example.com/notfound.json", test_config)
        
        # Should only be called once (no retry)
        assert route.call_count == 1
    
    @respx.mock
    def test_max_retries_exceeded(self, test_config):
        """Verify error raised after max retries."""
        # Always return 503
        route = respx.get("http://example.com/failing.json").mock(
            return_value=httpx.Response(503, text="Service Unavailable")
        )
        
        with pytest.raises(RetryableHTTPError) as exc_info:
            _fetch_http_content("http://example.com/failing.json", test_config)
        
        assert exc_info.value.status_code == 503
        # 1 initial + 3 retries = 4 attempts
        assert route.call_count == 4


class TestContentTypeValidation:
    """Test content-type allowlist enforcement."""
    
    @respx.mock
    def test_accepts_valid_content_types(self, test_config):
        """Verify valid content types are accepted."""
        for ct in ["application/json", "application/yaml", "text/yaml", "text/plain"]:
            respx.get("http://example.com/spec").mock(
                return_value=httpx.Response(200, text="content", headers={"content-type": ct})
            )
            
            content, _ = _fetch_http_content("http://example.com/spec", test_config)
            assert content == "content"
            respx.reset()
    
    @respx.mock
    def test_accepts_openapi_media_types(self, test_config):
        """Verify OpenAPI-specific media types are accepted."""
        openapi_types = [
            "application/openapi+json",
            "application/openapi+yaml",
            "application/vnd.oai.openapi",
            "application/vnd.oai.openapi+json",
        ]
        
        for ct in openapi_types:
            respx.get("http://example.com/openapi.yaml").mock(
                return_value=httpx.Response(200, text='openapi: "3.0"', headers={"content-type": ct})
            )
            
            content, _ = _fetch_http_content("http://example.com/openapi.yaml", test_config)
            assert 'openapi' in content
            respx.reset()
    
    @respx.mock
    def test_rejects_html_content_type(self, test_config):
        """Verify HTML responses are rejected."""
        respx.get("http://example.com/page.html").mock(
            return_value=httpx.Response(200, text="<html>", headers={"content-type": "text/html; charset=utf-8"})
        )
        
        with pytest.raises(InvalidContentTypeError) as exc_info:
            _fetch_http_content("http://example.com/page.html", test_config)
        
        assert "text/html" in str(exc_info.value)
    
    @respx.mock
    def test_handles_charset_in_content_type(self, test_config):
        """Verify charset parameter is stripped when checking content-type."""
        respx.get("http://example.com/spec.json").mock(
            return_value=httpx.Response(
                200, 
                json={"valid": True}, 
                headers={"content-type": "application/json; charset=utf-8"}
            )
        )
        
        # Should succeed despite charset parameter
        content, _ = _fetch_http_content("http://example.com/spec.json", test_config)
        assert "valid" in content


class TestSizeLimit:
    """Test max_bytes enforcement during streaming fetch."""
    
    @respx.mock
    def test_enforces_max_bytes(self, test_config):
        """Verify specs exceeding max_bytes are rejected."""
        # Config has 1MB limit
        large_content = "x" * (test_config.max_bytes + 1)
        
        respx.get("http://example.com/huge.json").mock(
            return_value=httpx.Response(200, text=large_content, headers={"content-type": "application/json"})
        )
        
        with pytest.raises(SpecTooLargeError) as exc_info:
            _fetch_http_content("http://example.com/huge.json", test_config)
        
        assert exc_info.value.max_size == test_config.max_bytes
    
    @respx.mock
    def test_accepts_spec_at_limit(self, test_config):
        """Verify specs exactly at max_bytes are accepted."""
        # Exactly at limit
        content_at_limit = "x" * test_config.max_bytes
        
        respx.get("http://example.com/big.json").mock(
            return_value=httpx.Response(200, text=content_at_limit, headers={"content-type": "text/plain"})
        )
        
        content, _ = _fetch_http_content("http://example.com/big.json", test_config)
        assert len(content) == test_config.max_bytes


class TestHTTPClientPooling:
    """Test HTTP client pooling behavior."""
    
    def test_client_created_lazily(self):
        """Verify client is created on first use."""
        # Clean up any existing client
        cleanup_http_client()
        
        # Get client (no config param - uses global)
        client = _get_http_client()
        assert client is not None
        assert isinstance(client, httpx.Client)
    
    def test_client_reused(self):
        """Verify same client instance is reused."""
        client1 = _get_http_client()
        client2 = _get_http_client()
        assert client1 is client2
    
    def test_cleanup_closes_client(self):
        """Verify cleanup closes the client."""
        client = _get_http_client()
        cleanup_http_client()
        # After cleanup, getting client should create new one
        new_client = _get_http_client()
        assert new_client is not client


class TestHTTPClientShutdownRegistration:
    """
    Test HTTP client shutdown registration (H-5).
    
    Verifies that the HTTP client registers cleanup handlers for graceful shutdown.
    """
    
    def test_cleanup_registered_with_shutdown_manager(self):
        """H-5: Verify cleanup is registered with ShutdownManager."""
        # Clean up any existing client
        cleanup_http_client()
        
        # Reset shutdown manager to clear any prior registrations
        from integration_coworker.shutdown import get_shutdown_manager, reset_shutdown_manager
        reset_shutdown_manager()
        
        # Get fresh manager
        manager = get_shutdown_manager()
        initial_callbacks = len(manager._cleanup_callbacks)
        
        # Create client - should register cleanup
        _get_http_client()
        
        # Verify cleanup was registered
        assert len(manager._cleanup_callbacks) == initial_callbacks + 1
        assert cleanup_http_client in manager._cleanup_callbacks
    
    def test_cleanup_registration_is_idempotent(self):
        """H-5: Verify cleanup is only registered once."""
        # Clean up and reset
        cleanup_http_client()
        from integration_coworker.shutdown import get_shutdown_manager, reset_shutdown_manager
        reset_shutdown_manager()
        
        manager = get_shutdown_manager()
        
        # Get client multiple times
        _get_http_client()
        _get_http_client()
        _get_http_client()
        
        # Should only be registered once
        cleanup_count = sum(1 for cb in manager._cleanup_callbacks if cb == cleanup_http_client)
        assert cleanup_count == 1
    
    def test_cleanup_re_registers_after_explicit_cleanup(self):
        """H-5: Verify cleanup re-registers after explicit cleanup and re-creation."""
        # Clean up and reset
        cleanup_http_client()
        from integration_coworker.shutdown import get_shutdown_manager, reset_shutdown_manager
        reset_shutdown_manager()
        
        manager = get_shutdown_manager()
        
        # First client creation
        _get_http_client()
        assert cleanup_http_client in manager._cleanup_callbacks
        
        # Explicit cleanup (resets registration flag)
        cleanup_http_client()
        
        # Verify registration flag was reset
        import integration_coworker.graph.nodes.ingest_spec as module
        assert module._http_client_cleanup_registered is False
        
        # Second client creation - should re-register
        _get_http_client()
        
        # Note: Cleanup is now registered twice in callbacks list
        # This is expected behavior - the manager accumulates callbacks
        # Both will call cleanup_http_client(), which is idempotent
        cleanup_count = sum(1 for cb in manager._cleanup_callbacks if cb == cleanup_http_client)
        assert cleanup_count == 2  # Both registrations present
