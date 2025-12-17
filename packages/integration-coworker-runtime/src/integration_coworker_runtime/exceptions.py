"""
Integration Exception Hierarchy

Shared exceptions for generated integration code.
All generated code uses these exception types for consistent error handling.
"""


class IntegrationError(Exception):
    """
    Base exception for all integration errors.
    
    Use this for non-recoverable errors that should halt the integration flow.
    
    Example:
        >>> raise IntegrationError("Failed to parse API response")
    """
    pass


class TransientIntegrationError(IntegrationError):
    """
    Exception for transient/retryable errors.
    
    Examples:
    - Network timeouts
    - Rate limiting (429)
    - Temporary service unavailability (503)
    - Server errors that may resolve (500, 502, 504)
    
    Consumers may choose to retry these errors with backoff.
    
    Example:
        >>> raise TransientIntegrationError("Rate limited, retry after 60s")
    """
    pass


class AuthIntegrationError(IntegrationError):
    """
    Exception for authentication/authorization failures.
    
    Examples:
    - Invalid API key (401)
    - Insufficient permissions (403)
    - Expired tokens
    
    These typically require human intervention to fix credentials.
    
    Example:
        >>> raise AuthIntegrationError("API key is invalid or expired")
    """
    pass
