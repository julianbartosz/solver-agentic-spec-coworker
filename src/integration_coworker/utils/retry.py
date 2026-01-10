"""
Retry utilities for resilient database and network operations.

Provides decorators and helpers for automatic retry with exponential backoff.
Used for connection lifecycle management (P0.3 fix).

Usage:
    from integration_coworker.utils.retry import retry, RetryConfig

    @retry(max_attempts=3, backoff_base=0.5)
    def get_data():
        ...

    # Or with custom config:
    config = RetryConfig(max_attempts=5, backoff_base=1.0, max_delay=30.0)
    
    @retry(config=config)
    def fetch_remote():
        ...
"""
import asyncio
import functools
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, Type, TypeVar, Union

logger = logging.getLogger(__name__)

# Type variable for decorated function return type
T = TypeVar("T")


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    
    # Maximum number of attempts (including initial attempt)
    max_attempts: int = 3
    
    # Base delay for exponential backoff (seconds)
    backoff_base: float = 0.5
    
    # Maximum delay between retries (seconds)
    max_delay: float = 30.0
    
    # Jitter factor (0.0 to 1.0) - adds randomness to prevent thundering herd
    jitter: float = 0.1
    
    # Exception types to retry on (default: all exceptions)
    retryable_exceptions: tuple = field(default_factory=lambda: (Exception,))
    
    # Exception types to never retry on
    fatal_exceptions: tuple = field(default_factory=tuple)
    
    # Callback for logging/metrics on retry
    on_retry: Optional[Callable[[Exception, int, float], None]] = None
    
    def __post_init__(self):
        """Validate configuration."""
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.backoff_base <= 0:
            raise ValueError("backoff_base must be positive")
        if self.max_delay <= 0:
            raise ValueError("max_delay must be positive")
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be between 0 and 1")


def calculate_delay(attempt: int, config: RetryConfig) -> float:
    """
    Calculate delay for next retry with exponential backoff and jitter.
    
    Args:
        attempt: Current attempt number (1-indexed)
        config: Retry configuration
        
    Returns:
        Delay in seconds
    """
    # Exponential backoff: base * 2^(attempt-1)
    delay = config.backoff_base * (2 ** (attempt - 1))
    
    # Cap at max_delay
    delay = min(delay, config.max_delay)
    
    # Add jitter
    if config.jitter > 0:
        jitter_amount = delay * config.jitter
        delay += random.uniform(-jitter_amount, jitter_amount)
        delay = max(0, delay)  # Ensure non-negative
    
    return delay


def should_retry(exception: Exception, config: RetryConfig) -> bool:
    """
    Determine if an exception should trigger a retry.
    
    Args:
        exception: The exception that was raised
        config: Retry configuration
        
    Returns:
        True if retry should be attempted
    """
    # Never retry fatal exceptions
    if isinstance(exception, config.fatal_exceptions):
        return False
    
    # Retry if exception is in retryable list
    return isinstance(exception, config.retryable_exceptions)


def retry(
    max_attempts: int = 3,
    backoff_base: float = 0.5,
    max_delay: float = 30.0,
    jitter: float = 0.1,
    retryable_exceptions: tuple = (Exception,),
    fatal_exceptions: tuple = (),
    config: Optional[RetryConfig] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for automatic retry with exponential backoff.
    
    Supports both sync and async functions.
    
    Args:
        max_attempts: Maximum number of attempts
        backoff_base: Base delay for exponential backoff (seconds)
        max_delay: Maximum delay between retries (seconds)
        jitter: Jitter factor (0.0 to 1.0)
        retryable_exceptions: Exception types to retry on
        fatal_exceptions: Exception types to never retry on
        config: Optional RetryConfig to use instead of individual params
        
    Returns:
        Decorated function with retry behavior
        
    Example:
        @retry(max_attempts=3, backoff_base=0.5)
        def fetch_data():
            return requests.get(url).json()
        
        @retry(retryable_exceptions=(ConnectionError, TimeoutError))
        async def async_fetch():
            async with session.get(url) as resp:
                return await resp.json()
    """
    if config is None:
        config = RetryConfig(
            max_attempts=max_attempts,
            backoff_base=backoff_base,
            max_delay=max_delay,
            jitter=jitter,
            retryable_exceptions=retryable_exceptions,
            fatal_exceptions=fatal_exceptions,
        )
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                last_exception: Optional[Exception] = None
                
                for attempt in range(1, config.max_attempts + 1):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e
                        
                        if not should_retry(e, config):
                            logger.debug(
                                f"[retry] {func.__name__}: Non-retryable exception {type(e).__name__}"
                            )
                            raise
                        
                        if attempt == config.max_attempts:
                            logger.warning(
                                f"[retry] {func.__name__}: Max attempts ({config.max_attempts}) "
                                f"exhausted, final error: {e}"
                            )
                            raise
                        
                        delay = calculate_delay(attempt, config)
                        logger.info(
                            f"[retry] {func.__name__}: Attempt {attempt}/{config.max_attempts} "
                            f"failed with {type(e).__name__}: {e}. Retrying in {delay:.2f}s"
                        )
                        
                        if config.on_retry:
                            config.on_retry(e, attempt, delay)
                        
                        await asyncio.sleep(delay)
                
                # Should never reach here, but satisfy type checker
                raise last_exception  # type: ignore
            
            return async_wrapper  # type: ignore
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> T:
                last_exception: Optional[Exception] = None
                
                for attempt in range(1, config.max_attempts + 1):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e
                        
                        if not should_retry(e, config):
                            logger.debug(
                                f"[retry] {func.__name__}: Non-retryable exception {type(e).__name__}"
                            )
                            raise
                        
                        if attempt == config.max_attempts:
                            logger.warning(
                                f"[retry] {func.__name__}: Max attempts ({config.max_attempts}) "
                                f"exhausted, final error: {e}"
                            )
                            raise
                        
                        delay = calculate_delay(attempt, config)
                        logger.info(
                            f"[retry] {func.__name__}: Attempt {attempt}/{config.max_attempts} "
                            f"failed with {type(e).__name__}: {e}. Retrying in {delay:.2f}s"
                        )
                        
                        if config.on_retry:
                            config.on_retry(e, attempt, delay)
                        
                        time.sleep(delay)
                
                # Should never reach here, but satisfy type checker
                raise last_exception  # type: ignore
            
            return sync_wrapper  # type: ignore
    
    return decorator


# Pre-configured retry for database operations
DB_RETRY_CONFIG = RetryConfig(
    max_attempts=3,
    backoff_base=0.5,
    max_delay=10.0,
    jitter=0.1,
    # Common database connection errors
    retryable_exceptions=(
        ConnectionError,
        TimeoutError,
        OSError,  # Includes connection reset
    ),
)


def db_retry(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator for database operations with predefined retry config.
    
    Retries on connection errors with exponential backoff.
    
    Example:
        @db_retry
        def get_user(user_id: int):
            with db.get_connection() as conn:
                return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    """
    return retry(config=DB_RETRY_CONFIG)(func)
