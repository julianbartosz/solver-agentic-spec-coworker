"""
Auth policy implementations for runtime library.

Provides pluggable authentication strategies for IntegrationClient.

Example:
    >>> from integration_coworker_runtime import BearerAuth, IntegrationClient
    >>> 
    >>> client = IntegrationClient(
    ...     base_url="https://api.example.com",
    ...     auth=BearerAuth(env_var="EXAMPLE_API_KEY"),
    ... )
"""
import base64
import os
from typing import Dict, Protocol, runtime_checkable


@runtime_checkable
class BaseAuth(Protocol):
    """Protocol for authentication handlers."""
    
    def get_headers(self) -> Dict[str, str]:
        """Return headers to add to requests for authentication."""
        ...


class NoAuth:
    """No authentication - returns empty headers."""
    
    def get_headers(self) -> Dict[str, str]:
        """Return empty headers."""
        return {}


class BearerAuth:
    """
    Bearer token authentication (OAuth 2.0, JWT, etc.).
    
    Example:
        >>> # From environment variable
        >>> auth = BearerAuth(env_var="STRIPE_API_KEY")
        >>> 
        >>> # Direct token (not recommended for production)
        >>> auth = BearerAuth(token="sk_test_...")
    """
    
    def __init__(
        self,
        token: str = None,
        env_var: str = None,
    ):
        """
        Initialize bearer auth.
        
        Args:
            token: The bearer token directly
            env_var: Environment variable name containing the token
        """
        self._token = token
        self._env_var = env_var
    
    @property
    def token(self) -> str:
        """Get the token, preferring explicit token over env var."""
        if self._token:
            return self._token
        if self._env_var:
            return os.environ.get(self._env_var, "")
        return ""
    
    def get_headers(self) -> Dict[str, str]:
        """Return Authorization header with bearer token."""
        token = self.token
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}


class ApiKeyAuth:
    """
    API key authentication via header.
    
    Example:
        >>> # Standard X-API-Key header
        >>> auth = ApiKeyAuth(env_var="MY_API_KEY")
        >>> 
        >>> # Custom header name
        >>> auth = ApiKeyAuth(env_var="OPENAI_KEY", header="X-OpenAI-Key")
    """
    
    def __init__(
        self,
        key: str = None,
        env_var: str = None,
        header: str = "X-API-Key",
    ):
        """
        Initialize API key auth.
        
        Args:
            key: The API key directly
            env_var: Environment variable name containing the key
            header: Header name to use (default: X-API-Key)
        """
        self._key = key
        self._env_var = env_var
        self.header = header
    
    @property
    def key(self) -> str:
        """Get the API key, preferring explicit key over env var."""
        if self._key:
            return self._key
        if self._env_var:
            return os.environ.get(self._env_var, "")
        return ""
    
    def get_headers(self) -> Dict[str, str]:
        """Return header with API key."""
        key = self.key
        if not key:
            return {}
        return {self.header: key}


class BasicAuth:
    """
    HTTP Basic authentication.
    
    Example:
        >>> auth = BasicAuth(
        ...     username_env_var="API_USERNAME",
        ...     password_env_var="API_PASSWORD",
        ... )
    """
    
    def __init__(
        self,
        username: str = None,
        password: str = None,
        username_env_var: str = None,
        password_env_var: str = None,
    ):
        """
        Initialize basic auth.
        
        Args:
            username: Username directly
            password: Password directly
            username_env_var: Environment variable for username
            password_env_var: Environment variable for password
        """
        self._username = username
        self._password = password
        self._username_env_var = username_env_var
        self._password_env_var = password_env_var
    
    @property
    def username(self) -> str:
        """Get username."""
        if self._username:
            return self._username
        if self._username_env_var:
            return os.environ.get(self._username_env_var, "")
        return ""
    
    @property
    def password(self) -> str:
        """Get password."""
        if self._password:
            return self._password
        if self._password_env_var:
            return os.environ.get(self._password_env_var, "")
        return ""
    
    def get_headers(self) -> Dict[str, str]:
        """Return Authorization header with Base64-encoded credentials."""
        username = self.username
        password = self.password
        if not username:
            return {}
        
        credentials = f"{username}:{password}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        return {"Authorization": f"Basic {encoded}"}
