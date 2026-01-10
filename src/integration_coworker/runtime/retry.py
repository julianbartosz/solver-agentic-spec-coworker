"""
Retry policy implementations for runtime library.

Provides pluggable retry strategies for IntegrationClient.
"""
import logging
import random
import time
from typing import Callable, Optional, Protocol, Set, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseRetry(Protocol):
    """Protocol for retry handlers."""
    
    def execute(self, func: Callable[[], T]) -> T:
        """Execute a function with retry logic."""
        ...


class NoRetry:
    """No retry - execute once and return/raise."""
    
    def execute(self, func: Callable[[], T]) -> T:
        return func()


class ExponentialRetry:
    """
    Exponential backoff retry with jitter.
    
    Implements exponential backoff with optional jitter to prevent
    thundering herd problems.
    """
    
    # HTTP status codes that are typically retryable
    DEFAULT_RETRYABLE_STATUS_CODES: Set[int] = {
        408,  # Request Timeout
        429,  # Too Many Requests
        500,  # Internal Server Error
        502,  # Bad Gateway
        503,  # Service Unavailable
        504,  # Gateway Timeout
    }
    
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        retryable_status_codes: Optional[Set[int]] = None,
        retryable_exceptions: Optional[tuple] = None,
    ):
        """
        Initialize exponential retry.
        
        Args:
            max_attempts: Maximum number of attempts (including first try)
            base_delay: Initial delay in seconds
            max_delay: Maximum delay cap in seconds
            exponential_base: Base for exponential calculation
            jitter: Whether to add random jitter
            retryable_status_codes: HTTP status codes to retry
            retryable_exceptions: Exception types to retry
        """
        self.max_attempts = max(1, max_attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.retryable_status_codes = (
            retryable_status_codes
            if retryable_status_codes is not None
            else self.DEFAULT_RETRYABLE_STATUS_CODES
        )
        self.retryable_exceptions = retryable_exceptions or (Exception,)
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number (0-indexed)."""
        delay = self.base_delay * (self.exponential_base ** attempt)
        delay = min(delay, self.max_delay)
        
        if self.jitter:
            # Add up to 25% jitter (non-cryptographic use)
            jitter_range = delay * 0.25
            delay += random.uniform(-jitter_range, jitter_range)  # nosec B311
            delay = max(0, delay)
        
        return delay
    
    def _should_retry(self, exception: Exception) -> bool:
        """Determine if the exception is retryable."""
        # Check if it's a retryable exception type
        if not isinstance(exception, self.retryable_exceptions):
            return False
        
        # Check for HTTP response with status code
        response = getattr(exception, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            if status_code is not None:
                return status_code in self.retryable_status_codes
        
        # Default: retry if it's a retryable exception type
        return True
    
    def execute(self, func: Callable[[], T]) -> T:
        """Execute function with exponential backoff retry."""
        last_exception: Optional[Exception] = None
        
        for attempt in range(self.max_attempts):
            try:
                return func()
            except Exception as e:
                last_exception = e
                
                if attempt + 1 >= self.max_attempts:
                    # Last attempt, don't retry
                    logger.warning(
                        f"Retry exhausted after {self.max_attempts} attempts: {e}"
                    )
                    raise
                
                if not self._should_retry(e):
                    # Not a retryable error
                    logger.debug(f"Non-retryable error: {e}")
                    raise
                
                delay = self._calculate_delay(attempt)
                logger.info(
                    f"Retry attempt {attempt + 1}/{self.max_attempts} "
                    f"after {delay:.2f}s delay: {e}"
                )
                time.sleep(delay)
        
        # Should not reach here, but satisfy type checker
        if last_exception:
            raise last_exception
        raise RuntimeError("Unexpected retry loop exit")
