"""
Integration HTTP Client

Simple HTTP client wrapper with built-in auth, retry, and error handling.
This is a simpler alternative to IntegrationClient for cases where
you don't need the full policy-based architecture.

Example:
    >>> from integration_coworker_runtime import IntegrationHttpClient
    >>> 
    >>> client = IntegrationHttpClient(
    ...     base_url="https://api.stripe.com/v1",
    ...     api_key="sk_test_...",
    ... )
    >>> 
    >>> response = client.request("POST", "/charges", json={"amount": 2000})
"""
from typing import Dict, Optional, Any
import httpx

from integration_coworker_runtime.exceptions import (
    IntegrationError,
    TransientIntegrationError,
    AuthIntegrationError,
)


class IntegrationHttpClient:
    """
    Simple HTTP client wrapper for integration code.
    
    Provides consistent auth, retry, timeout, and error handling.
    For more advanced use cases with pluggable policies, use IntegrationClient.
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout_s: float = 30.0,
        retries: int = 3,
    ):
        """
        Initialize HTTP client.
        
        Args:
            base_url: Base URL for API (e.g., "https://api.stripe.com")
            api_key: Optional API key for authentication (Bearer token)
            timeout_s: Request timeout in seconds
            retries: Number of retry attempts for transient failures
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.retries = retries
        self._client = httpx.Client(timeout=timeout_s)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        """
        Make an HTTP request.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (will be appended to base_url)
            params: Query parameters
            json: JSON request body
            headers: Additional headers
        
        Returns:
            httpx.Response object
        
        Raises:
            TransientIntegrationError: For retryable failures (429, 5xx)
            AuthIntegrationError: For authentication failures (401, 403)
            IntegrationError: For other failures
        """
        url = f"{self.base_url}{path}"

        # Prepare headers
        request_headers = headers.copy() if headers else {}
        if self.api_key and "Authorization" not in request_headers:
            request_headers["Authorization"] = f"Bearer {self.api_key}"

        # Attempt request with retries
        last_exception = None
        for attempt in range(self.retries):
            try:
                response = self._client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json,
                    headers=request_headers,
                )

                # Check for auth errors
                if response.status_code in (401, 403):
                    raise AuthIntegrationError(
                        f"Authentication failed: {response.status_code} {response.text}"
                    )

                # Check for retryable errors
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt < self.retries - 1:
                        # Will retry
                        continue
                    else:
                        raise TransientIntegrationError(
                            f"Transient error after {self.retries} attempts: {response.status_code}"
                        )

                # Return successful or other responses
                return response

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exception = e
                if attempt < self.retries - 1:
                    continue
                else:
                    raise TransientIntegrationError(
                        f"Network error after {self.retries} attempts: {str(e)}"
                    )
            except (AuthIntegrationError, TransientIntegrationError):
                # Re-raise our exceptions
                raise
            except Exception as e:
                raise IntegrationError(f"Unexpected error during request: {str(e)}")

        # Should not reach here, but handle edge case
        if last_exception:
            raise TransientIntegrationError(f"Request failed: {str(last_exception)}")
        else:
            raise IntegrationError("Request failed for unknown reason")

    def close(self):
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
