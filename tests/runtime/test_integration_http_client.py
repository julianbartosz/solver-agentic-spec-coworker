"""
Tests for IntegrationHttpClient - Appendix I.2 compliance.
"""
import pytest
from unittest.mock import Mock, patch
import httpx

from integration_coworker.runtime.http_client import IntegrationHttpClient
from integration_coworker.runtime.exceptions import (
    IntegrationError,
    TransientIntegrationError,
    AuthIntegrationError,
)


@pytest.fixture
def mock_httpx_client():
    """Mock httpx.Client for testing."""
    with patch("integration_coworker.runtime.http_client.httpx.Client") as mock_client_cls:
        mock_instance = Mock()
        mock_client_cls.return_value = mock_instance
        yield mock_instance


def test_initialization():
    """Verify client initializes with correct defaults."""
    client = IntegrationHttpClient(
        base_url="https://api.example.com/v1",
        api_key="sk_test_123",
        timeout_s=60.0,
        retries=5,
    )
    
    assert client.base_url == "https://api.example.com/v1"
    assert client.api_key == "sk_test_123"
    assert client.timeout_s == 60.0
    assert client.retries == 5


def test_base_url_normalization():
    """Ensure trailing slashes are stripped from base_url."""
    client = IntegrationHttpClient(base_url="https://api.example.com/v1/")
    assert client.base_url == "https://api.example.com/v1"


def test_successful_get_request(mock_httpx_client):
    """Test successful GET request with auth header."""
    # Setup mock response
    mock_response = Mock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": "success"}
    mock_httpx_client.request.return_value = mock_response
    
    client = IntegrationHttpClient(
        base_url="https://api.example.com",
        api_key="sk_test_123"
    )
    
    response = client.request("GET", "/payments", params={"limit": 10})
    
    # Verify request was made correctly
    mock_httpx_client.request.assert_called_once()
    call_kwargs = mock_httpx_client.request.call_args[1]
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["url"] == "https://api.example.com/payments"
    assert call_kwargs["params"] == {"limit": 10}
    assert call_kwargs["headers"]["Authorization"] == "Bearer sk_test_123"
    
    assert response.status_code == 200


def test_successful_post_request_with_json(mock_httpx_client):
    """Test successful POST request with JSON body."""
    mock_response = Mock(spec=httpx.Response)
    mock_response.status_code = 201
    mock_httpx_client.request.return_value = mock_response
    
    client = IntegrationHttpClient(base_url="https://api.example.com")
    
    response = client.request(
        "POST",
        "/payments",
        json={"amount": 1000, "currency": "usd"}
    )
    
    call_kwargs = mock_httpx_client.request.call_args[1]
    assert call_kwargs["method"] == "POST"
    assert call_kwargs["json"] == {"amount": 1000, "currency": "usd"}
    assert response.status_code == 201


def test_auth_error_401_raises_exception(mock_httpx_client):
    """Test 401 raises AuthIntegrationError."""
    mock_response = Mock(spec=httpx.Response)
    mock_response.status_code = 401
    mock_response.text = "Invalid API key"
    mock_httpx_client.request.return_value = mock_response
    
    client = IntegrationHttpClient(
        base_url="https://api.example.com",
        api_key="invalid_key"
    )
    
    with pytest.raises(AuthIntegrationError) as exc_info:
        client.request("GET", "/protected")
    
    assert "401" in str(exc_info.value)
    assert "Authentication failed" in str(exc_info.value)


def test_auth_error_403_raises_exception(mock_httpx_client):
    """Test 403 raises AuthIntegrationError."""
    mock_response = Mock(spec=httpx.Response)
    mock_response.status_code = 403
    mock_response.text = "Insufficient permissions"
    mock_httpx_client.request.return_value = mock_response
    
    client = IntegrationHttpClient(base_url="https://api.example.com")
    
    with pytest.raises(AuthIntegrationError) as exc_info:
        client.request("DELETE", "/admin/users/123")
    
    assert "403" in str(exc_info.value)


def test_transient_error_retries_and_succeeds(mock_httpx_client):
    """Test 503 retries and eventually succeeds."""
    # First call returns 503, second succeeds
    mock_response_503 = Mock(spec=httpx.Response)
    mock_response_503.status_code = 503
    
    mock_response_200 = Mock(spec=httpx.Response)
    mock_response_200.status_code = 200
    
    mock_httpx_client.request.side_effect = [mock_response_503, mock_response_200]
    
    client = IntegrationHttpClient(
        base_url="https://api.example.com",
        retries=3
    )
    
    response = client.request("GET", "/status")
    
    # Should have been called twice (1 initial + 1 retry)
    assert mock_httpx_client.request.call_count == 2
    assert response.status_code == 200


def test_transient_error_exhausts_retries(mock_httpx_client):
    """Test 429 exhausts retries and raises TransientIntegrationError."""
    mock_response = Mock(spec=httpx.Response)
    mock_response.status_code = 429
    mock_httpx_client.request.return_value = mock_response
    
    client = IntegrationHttpClient(
        base_url="https://api.example.com",
        retries=2
    )
    
    with pytest.raises(TransientIntegrationError) as exc_info:
        client.request("POST", "/create")
    
    # Should have tried 2 times
    assert mock_httpx_client.request.call_count == 2
    assert "Transient error after 2 attempts" in str(exc_info.value)
    assert "429" in str(exc_info.value)


def test_network_timeout_retries_and_raises(mock_httpx_client):
    """Test network timeout retries and eventually raises TransientIntegrationError."""
    mock_httpx_client.request.side_effect = httpx.TimeoutException("Connection timed out")
    
    client = IntegrationHttpClient(
        base_url="https://api.example.com",
        retries=3
    )
    
    with pytest.raises(TransientIntegrationError) as exc_info:
        client.request("GET", "/slow-endpoint")
    
    # Should have tried 3 times
    assert mock_httpx_client.request.call_count == 3
    assert "Network error after 3 attempts" in str(exc_info.value)


def test_unexpected_exception_raises_integration_error(mock_httpx_client):
    """Test unexpected exceptions are wrapped in IntegrationError."""
    mock_httpx_client.request.side_effect = ValueError("Something went wrong")
    
    client = IntegrationHttpClient(base_url="https://api.example.com")
    
    with pytest.raises(IntegrationError) as exc_info:
        client.request("GET", "/endpoint")
    
    assert "Unexpected error during request" in str(exc_info.value)


def test_custom_headers_preserved(mock_httpx_client):
    """Test custom headers are included in request."""
    mock_response = Mock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_httpx_client.request.return_value = mock_response
    
    client = IntegrationHttpClient(base_url="https://api.example.com")
    
    client.request(
        "GET",
        "/data",
        headers={"X-Custom-Header": "custom-value"}
    )
    
    call_kwargs = mock_httpx_client.request.call_args[1]
    assert call_kwargs["headers"]["X-Custom-Header"] == "custom-value"


def test_custom_auth_header_not_overridden(mock_httpx_client):
    """Test custom Authorization header is not overridden by api_key."""
    mock_response = Mock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_httpx_client.request.return_value = mock_response
    
    client = IntegrationHttpClient(
        base_url="https://api.example.com",
        api_key="default_key"
    )
    
    client.request(
        "GET",
        "/endpoint",
        headers={"Authorization": "Custom token"}
    )
    
    call_kwargs = mock_httpx_client.request.call_args[1]
    assert call_kwargs["headers"]["Authorization"] == "Custom token"


def test_context_manager_closes_client(mock_httpx_client):
    """Test client properly closes when used as context manager."""
    with IntegrationHttpClient(base_url="https://api.example.com") as client:
        assert client is not None
    
    mock_httpx_client.close.assert_called_once()


def test_explicit_close(mock_httpx_client):
    """Test explicit close() call."""
    client = IntegrationHttpClient(base_url="https://api.example.com")
    client.close()
    
    mock_httpx_client.close.assert_called_once()
