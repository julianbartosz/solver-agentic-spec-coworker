"""
Rate limiting implementations for runtime library.

Provides pluggable rate limiting strategies for IntegrationClient.

Example:
    >>> from integration_coworker_runtime import TokenBucketRateLimiter, IntegrationClient
    >>> 
    >>> client = IntegrationClient(
    ...     base_url="https://api.example.com",
    ...     rate_limiter=TokenBucketRateLimiter(rps=10, burst=20),
    ... )
"""
import logging
import threading
import time
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class BaseRateLimiter(Protocol):
    """Protocol for rate limiters."""
    
    def acquire(self) -> None:
        """Block until rate limit allows a request."""
        ...


class NoRateLimiter:
    """No rate limiting - always allows requests immediately."""
    
    def acquire(self) -> None:
        """Always permits immediately."""
        pass


class TokenBucketRateLimiter:
    """
    Token bucket rate limiter.
    
    Classic token bucket algorithm: tokens are added at a fixed rate,
    and each request consumes one token. Requests block when no tokens
    are available.
    
    Example:
        >>> # 10 requests per second, burst up to 20
        >>> limiter = TokenBucketRateLimiter(rps=10, burst=20)
        >>> limiter.acquire()  # Blocks if rate exceeded
    """
    
    def __init__(
        self,
        rps: float = 10.0,
        burst: int = None,
    ):
        """
        Initialize token bucket rate limiter.
        
        Args:
            rps: Requests per second (token refill rate)
            burst: Maximum burst size (bucket capacity). Defaults to rps.
        """
        self.rps = max(0.1, rps)  # Minimum 0.1 rps
        self.burst = burst if burst is not None else int(rps)
        self.burst = max(1, self.burst)
        
        self._tokens = float(self.burst)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
    
    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        
        # Add tokens based on elapsed time
        new_tokens = elapsed * self.rps
        self._tokens = min(self.burst, self._tokens + new_tokens)
        self._last_refill = now
    
    def acquire(self) -> None:
        """Block until a token is available."""
        while True:
            with self._lock:
                self._refill()
                
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                
                # Calculate wait time for next token
                tokens_needed = 1.0 - self._tokens
                wait_time = tokens_needed / self.rps
            
            # Wait outside the lock
            if wait_time > 0:
                logger.debug(f"Rate limited, waiting {wait_time:.3f}s")
                time.sleep(wait_time)


class SlidingWindowRateLimiter:
    """
    Sliding window rate limiter.
    
    Tracks request timestamps in a sliding window and blocks when
    the count exceeds the limit.
    
    Example:
        >>> # 100 requests per minute
        >>> limiter = SlidingWindowRateLimiter(max_requests=100, window_seconds=60)
        >>> limiter.acquire()  # Blocks if rate exceeded
    """
    
    def __init__(
        self,
        max_requests: int = 100,
        window_seconds: float = 60.0,
    ):
        """
        Initialize sliding window rate limiter.
        
        Args:
            max_requests: Maximum requests allowed in the window
            window_seconds: Window size in seconds
        """
        self.max_requests = max(1, max_requests)
        self.window_seconds = max(1.0, window_seconds)
        
        self._timestamps: list = []
        self._lock = threading.Lock()
    
    def _cleanup_old_requests(self, now: float) -> None:
        """Remove timestamps outside the current window."""
        cutoff = now - self.window_seconds
        self._timestamps = [ts for ts in self._timestamps if ts > cutoff]
    
    def acquire(self) -> None:
        """Block until the rate limit allows a request."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._cleanup_old_requests(now)
                
                if len(self._timestamps) < self.max_requests:
                    self._timestamps.append(now)
                    return
                
                # Calculate wait time until oldest request expires
                oldest = self._timestamps[0]
                wait_time = (oldest + self.window_seconds) - now
            
            # Wait outside the lock
            if wait_time > 0:
                logger.debug(f"Rate limited (sliding window), waiting {wait_time:.3f}s")
                time.sleep(wait_time)
            else:
                # Edge case: no wait needed, retry immediately
                continue
