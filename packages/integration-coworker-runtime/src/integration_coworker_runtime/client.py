"""
IntegrationClient base class for generated API clients.

Generated clients can extend this base class to get built-in
auth, retry, and rate limiting capabilities.

Example:
    >>> from integration_coworker_runtime import (
    ...     IntegrationClient,
    ...     BearerAuth,
    ...     ExponentialRetry,
    ...     TokenBucketRateLimiter,
    ... )
    >>> 
    >>> client = IntegrationClient(
    ...     base_url="https://api.stripe.com/v1",
    ...     auth=BearerAuth(env_var="STRIPE_API_KEY"),
    ...     retry=ExponentialRetry(max_attempts=3),
    ...     rate_limiter=TokenBucketRateLimiter(rps=25),
    ... )
    >>> 
    >>> result = client.post("/charges", json={"amount": 2000, "currency": "usd"})
"""
import logging
from typing import Any, Dict, Optional

from integration_coworker_runtime.auth import BaseAuth, NoAuth
from integration_coworker_runtime.retry import BaseRetry, NoRetry
from integration_coworker_runtime.rate_limit import BaseRateLimiter, NoRateLimiter

logger = logging.getLogger(__name__)


class IntegrationClient:
    """
    Base class for generated API clients.
    
    Provides pluggable auth, retry, and rate limiting policies.
    Generated clients inherit from this class and configure
    policies in their __init__.
    
    Example:
        >>> class StripeClient(IntegrationClient):
        ...     def __init__(self, api_key: str):
        ...         super().__init__(
        ...             base_url="https://api.stripe.com/v1",
        ...             auth=BearerAuth(token=api_key),
        ...             retry=ExponentialRetry(),
        ...         )
        ...     
        ...     def create_charge(self, amount: int, currency: str) -> dict:
        ...         return self.post("/charges", json={"amount": amount, "currency": currency})
    """
    
    def __init__(
        self,
        base_url: str,
        auth: Optional[BaseAuth] = None,
        retry: Optional[BaseRetry] = None,
        rate_limiter: Optional[BaseRateLimiter] = None,
        timeout: float = 30.0,
    ):
        """
        Initialize the integration client.
        
        Args:
            base_url: Base URL for the API (e.g., "https://api.example.com/v1")
            auth: Authentication handler (default: NoAuth)
            retry: Retry handler (default: NoRetry)
            rate_limiter: Rate limiter (default: NoRateLimiter)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.auth = auth or NoAuth()
        self.retry = retry or NoRetry()
        self.rate_limiter = rate_limiter or NoRateLimiter()
        self.timeout = timeout
        
        # Lazy-load httpx to provide clear error if missing
        self._http = None
    
    @property
    def http(self):
        """Lazy-load httpx client."""
        if self._http is None:
            try:
                import httpx
                self._http = httpx.Client(timeout=self.timeout)
            except ImportError:
                raise ImportError(
                    "httpx is required for IntegrationClient. "
                    "Install it with: pip install httpx"
                )
        return self._http
    
    def close(self) -> None:
        """Close the HTTP client."""
        if self._http is not None:
            self._http.close()
            self._http = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Any:
        """
        Execute an HTTP request with policies applied.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: API path (will be joined with base_url)
            params: Query parameters
            json: JSON body
            headers: Additional headers
            **kwargs: Additional arguments passed to httpx
        
        Returns:
            httpx.Response object
        """
        # Apply rate limiting
        self.rate_limiter.acquire()
        
        # Build headers with auth
        request_headers = dict(headers or {})
        request_headers.update(self.auth.get_headers())
        
        # Build URL
        url = f"{self.base_url}/{path.lstrip('/')}"
        
        def execute():
            return self.http.request(
                method=method,
                url=url,
                params=params,
                json=json,
                headers=request_headers,
                **kwargs,
            )
        
        # Execute with retry
        return self.retry.execute(execute)
    
    def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute GET request and return JSON response."""
        response = self.request("GET", path, params=params, **kwargs)
        response.raise_for_status()
        return response.json()
    
    def post(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute POST request and return JSON response."""
        response = self.request("POST", path, json=json, **kwargs)
        response.raise_for_status()
        return response.json()
    
    def put(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute PUT request and return JSON response."""
        response = self.request("PUT", path, json=json, **kwargs)
        response.raise_for_status()
        return response.json()
    
    def patch(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute PATCH request and return JSON response."""
        response = self.request("PATCH", path, json=json, **kwargs)
        response.raise_for_status()
        return response.json()
    
    def delete(
        self,
        path: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute DELETE request and return JSON response."""
        response = self.request("DELETE", path, **kwargs)
        response.raise_for_status()
        # Some APIs return empty response on DELETE
        if response.content:
            return response.json()
        return {}
