"""LLM exception hierarchy for fail-fast semantics.

This module defines typed exceptions for LLM errors to enable:
1. Auth fail-fast: LLMAuthError is FATAL and never retried
2. Retryable classification: Rate limits and transient errors can be retried
3. Centralized error handling: Runtime wrapper can classify errors consistently

Per Production Readiness Plan v4:
- LLMAuthError: 401/403 → FATAL, no retry, no fallback
- LLMRateLimitError: 429 → Retryable with backoff
- LLMTransientError: 5xx, timeout → Retryable with backoff
"""

from typing import Optional


class LLMError(Exception):
    """Base class for LLM errors.
    
    All LLM-specific errors should inherit from this class to enable
    consistent error handling in the runtime wrapper.
    """
    
    def __init__(self, message: str, provider: Optional[str] = None, status_code: Optional[int] = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class LLMAuthError(LLMError):
    """Authentication/authorization failure (401/403).
    
    This error is:
    - NON-RETRYABLE: Auth errors won't resolve with retries
    - FATAL: Should fail the entire run immediately
    - NO FALLBACK: Should not produce skeleton/fallback code
    
    Common causes:
    - Invalid API key (401)
    - Expired API key (401)
    - Insufficient permissions (403)
    - Organization access denied (403)
    """
    pass


class LLMRateLimitError(LLMError):
    """Rate limit hit (429).
    
    This error is:
    - RETRYABLE: Should retry with exponential backoff
    - NOT FATAL: Can continue after backoff
    
    Common causes:
    - Too many requests per minute
    - Token quota exceeded (temporary)
    - Concurrent request limit
    """
    
    def __init__(
        self,
        message: str,
        provider: Optional[str] = None,
        retry_after: Optional[float] = None,
    ):
        super().__init__(message, provider=provider, status_code=429)
        self.retry_after = retry_after


class LLMTransientError(LLMError):
    """Transient server error (5xx, timeout, connection error).
    
    This error is:
    - RETRYABLE: Should retry with exponential backoff
    - NOT FATAL: Can continue after retry
    
    Common causes:
    - Server overload (503)
    - Gateway timeout (504)
    - Internal server error (500)
    - Network timeout
    - Connection reset
    """
    pass


class LLMContentFilterError(LLMError):
    """Content was blocked by provider's safety filter.
    
    This error is:
    - NON-RETRYABLE: Same content will be blocked again
    - RECOVERABLE: May continue with modified content or skip
    
    Common causes:
    - Prompt triggered safety filter
    - Response contained blocked content
    """
    pass


class LLMContextLengthError(LLMError):
    """Request exceeded model's context length limit.
    
    This error is:
    - NON-RETRYABLE: Same request will fail again
    - RECOVERABLE: May continue with truncated/chunked content
    
    Common causes:
    - Prompt too long
    - Response would exceed limit
    """
    pass


def classify_llm_exception(exc: Exception, provider: Optional[str] = None) -> LLMError:
    """Classify a generic exception into a typed LLM exception.
    
    This function examines exception messages and types to determine
    the appropriate LLMError subclass. Used to convert provider-specific
    exceptions into our typed hierarchy.
    
    Args:
        exc: The exception to classify
        provider: Optional provider name for context
        
    Returns:
        An appropriate LLMError subclass instance
    """
    error_str = str(exc).lower()
    
    # Auth errors (401/403) - FATAL
    if any(pattern in error_str for pattern in [
        "401",
        "403",
        "unauthorized",
        "authentication",
        "invalid api key",
        "invalid_api_key",
        "incorrect api key",
        "api key not valid",
        "permission denied",
        "access denied",
        "invalid_request_error",  # OpenAI uses this for auth issues
    ]):
        return LLMAuthError(str(exc), provider=provider)
    
    # Rate limit (429) - Retryable
    if any(pattern in error_str for pattern in [
        "429",
        "rate_limit",
        "rate limit",
        "too many requests",
        "quota exceeded",
        "requests per minute",
        "tokens per minute",
    ]):
        return LLMRateLimitError(str(exc), provider=provider)
    
    # Content filter - Recoverable
    if any(pattern in error_str for pattern in [
        "content_filter",
        "content filter",
        "safety",
        "blocked",
        "flagged",
    ]):
        return LLMContentFilterError(str(exc), provider=provider)
    
    # Context length - Recoverable
    if any(pattern in error_str for pattern in [
        "context_length",
        "context length",
        "maximum context",
        "token limit",
        "too long",
    ]):
        return LLMContextLengthError(str(exc), provider=provider)
    
    # Server errors (5xx) - Retryable
    if any(pattern in error_str for pattern in [
        "500",
        "502",
        "503",
        "504",
        "internal server error",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "overloaded",
    ]):
        return LLMTransientError(str(exc), provider=provider)
    
    # Network/timeout errors - Retryable
    if any(pattern in error_str for pattern in [
        "timeout",
        "timed out",
        "connection",
        "network",
        "reset by peer",
        "broken pipe",
    ]):
        return LLMTransientError(str(exc), provider=provider)
    
    # Default: treat unknown errors as transient (retryable)
    # This is safer than treating them as fatal
    return LLMTransientError(str(exc), provider=provider)


class LLMBudgetExceededError(LLMError):
    """LLM call budget exceeded (production safety gate).
    
    This error is:
    - NON-RETRYABLE: Budget is a hard cap
    - FATAL: Should fail the entire run immediately
    - NO FALLBACK: Prevents runaway costs
    
    Triggered when:
    - PROD_E2E_MAX_LLM_CALLS is exceeded
    - Per-spec timeout is exceeded
    - Total wall-clock timeout is exceeded
    """
    
    def __init__(
        self,
        message: str,
        calls_made: int = 0,
        calls_limit: int = 0,
        provider: Optional[str] = None,
    ):
        super().__init__(message, provider=provider)
        self.calls_made = calls_made
        self.calls_limit = calls_limit


__all__ = [
    "LLMError",
    "LLMAuthError",
    "LLMRateLimitError",
    "LLMTransientError",
    "LLMContentFilterError",
    "LLMContextLengthError",
    "LLMBudgetExceededError",
    "classify_llm_exception",
]
