"""
Auth policy implementations for runtime library.

Provides pluggable authentication strategies for IntegrationClient.
"""
import base64
import os
from typing import Dict, Protocol


class BaseAuth(Protocol):
    """Protocol for authentication handlers."""
    
    def get_headers(self) -> Dict[str, str]:
        """Return headers to add to requests for authentication."""
        ...


class NoAuth:
    """No authentication - returns empty headers."""
    
    def get_headers(self) -> Dict[str, str]:
        return {}


class BearerAuth:
    """Bearer token authentication (OAuth 2.0, JWT, etc.)."""
    
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
        token = self.token
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}


class ApiKeyAuth:
    """API key authentication via header."""
    
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
        key = self.key
        if not key:
            return {}
        return {self.header: key}


class BasicAuth:
    """HTTP Basic authentication."""
    
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
        if self._username:
            return self._username
        if self._username_env_var:
            return os.environ.get(self._username_env_var, "")
        return ""
    
    @property
    def password(self) -> str:
        if self._password:
            return self._password
        if self._password_env_var:
            return os.environ.get(self._password_env_var, "")
        return ""
    
    def get_headers(self) -> Dict[str, str]:
        username = self.username
        password = self.password
        if not username:
            return {}
        
        credentials = f"{username}:{password}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        return {"Authorization": f"Basic {encoded}"}
