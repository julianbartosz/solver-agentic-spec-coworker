"""
LLM Response Cache (Plan 7)

Redis-backed caching for LLM API responses to reduce costs and latency.

Cache Key Format:
    llm:<provider>:<model>:<task_type>:<sha256_hash(prompt+system_prompt)>

Features:
- SHA-256 hash of prompt content for cache keys
- Configurable TTL (default 24 hours)
- Cache statistics tracking (hits, misses, evictions)
- Thread-safe operations
- Graceful degradation if Redis unavailable
- H-1: Local metrics tracking (hits, misses, errors, bypasses)

Environment Variables:
    REDIS_URL: Redis connection URL (default: redis://localhost:6379/0)
    LLM_CACHE_ENABLED: Enable/disable caching (default: true)
    LLM_CACHE_TTL: Cache TTL in seconds (default: 86400 = 24h)

Usage:
    from integration_coworker.llm.cache import get_llm_cache
    
    cache = get_llm_cache()
    
    # Check cache
    cached = cache.get(provider, model, task_type, prompt, system_prompt)
    if cached:
        return cached
    
    # Call API and cache result
    result = call_api(...)
    cache.set(provider, model, task_type, prompt, system_prompt, result)
    
    # Get local metrics (H-1)
    metrics = cache.get_local_metrics()
    print(f"Hits: {metrics.hits}, Misses: {metrics.misses}")
"""

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_CACHE_TTL = 86400  # 24 hours
DEFAULT_CACHE_ENABLED = True

# Cache statistics keys
STATS_HITS_KEY = "llm:cache:stats:hits"
STATS_MISSES_KEY = "llm:cache:stats:misses"
STATS_EVICTIONS_KEY = "llm:cache:stats:evictions"


def _get_cache_config() -> Dict[str, Any]:
    """Get cache configuration from environment."""
    return {
        "redis_url": os.getenv("REDIS_URL", DEFAULT_REDIS_URL),
        "enabled": os.getenv("LLM_CACHE_ENABLED", "true").lower() in ("true", "1", "yes", "on"),
        "ttl": int(os.getenv("LLM_CACHE_TTL", str(DEFAULT_CACHE_TTL))),
    }


def _generate_cache_key(
    provider: str,
    model: str,
    task_type: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    api_key_hash: Optional[str] = None,
) -> str:
    """
    Generate a cache key for an LLM request.
    
    Format: llm:<provider>:<model>:<task_type>:<api_key_hash>:<content_hash>
    
    The content_hash is SHA-256 of the concatenated prompt content.
    
    Item E Security Fix: api_key_hash is included to prevent cross-tenant
    cache leakage. Different API keys get isolated cache entries.
    
    Args:
        provider: LLM provider (openai, anthropic, google)
        model: Model name
        task_type: Task type for namespacing
        prompt: User prompt
        system_prompt: Optional system prompt
        api_key_hash: Hash of API key for tenant isolation (use first 16 chars of SHA256)
    """
    content = f"{system_prompt or ''}|||{prompt}"
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    key_hash = api_key_hash or "shared"  # "shared" for backwards compatibility
    return f"llm:{provider}:{model}:{task_type}:{key_hash}:{content_hash}"


@dataclass
class CacheStats:
    """Cache statistics (from Redis)."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    hit_rate: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        total = self.hits + self.misses
        self.hit_rate = (self.hits / total * 100) if total > 0 else 0.0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "hit_rate": f"{self.hit_rate:.2f}%",
            "total_requests": total,
        }


@dataclass
class CacheMetrics:
    """
    Local in-memory cache metrics (H-1).
    
    Tracks cache operations locally without requiring Redis.
    Thread-safe via atomic integer operations.
    
    Unlike CacheStats (stored in Redis), these are ephemeral and reset
    when the process restarts. Useful for:
    - Monitoring cache effectiveness in real-time
    - Debugging cache issues when Redis is unavailable
    - Tracking bypass/error rates
    """
    hits: int = 0
    misses: int = 0
    errors: int = 0
    bypasses: int = 0  # When cache is disabled or unavailable
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        total = self.hits + self.misses + self.bypasses
        effective_requests = self.hits + self.misses
        hit_rate = (self.hits / effective_requests * 100) if effective_requests > 0 else 0.0
        bypass_rate = (self.bypasses / total * 100) if total > 0 else 0.0
        
        return {
            "hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
            "bypasses": self.bypasses,
            "total_requests": total,
            "hit_rate": f"{hit_rate:.2f}%",
            "bypass_rate": f"{bypass_rate:.2f}%",
            "error_count": self.errors,
        }


@dataclass
class LLMCache:
    """
    Redis-backed LLM response cache.
    
    Provides caching for LLM API responses with:
    - Content-based key generation (SHA-256)
    - Configurable TTL
    - Statistics tracking (Redis + local metrics)
    - Graceful degradation when Redis unavailable
    - H-1: Local metrics tracking (hits, misses, errors, bypasses)
    """
    
    redis_url: str = DEFAULT_REDIS_URL
    ttl: int = DEFAULT_CACHE_TTL
    enabled: bool = DEFAULT_CACHE_ENABLED
    
    _client: Optional[Any] = field(default=None, repr=False)
    _connection_failed: bool = field(default=False, repr=False)
    _last_connection_attempt: float = field(default=0.0, repr=False)
    
    # H-1: Local metrics tracking (thread-safe)
    _metrics_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _local_hits: int = field(default=0, repr=False)
    _local_misses: int = field(default=0, repr=False)
    _local_errors: int = field(default=0, repr=False)
    _local_bypasses: int = field(default=0, repr=False)
    
    # Retry connection every 60 seconds if failed
    CONNECTION_RETRY_INTERVAL = 60.0
    
    def _increment_hit(self) -> None:
        """Increment local hit counter (H-1)."""
        with self._metrics_lock:
            self._local_hits += 1
    
    def _increment_miss(self) -> None:
        """Increment local miss counter (H-1)."""
        with self._metrics_lock:
            self._local_misses += 1
    
    def _increment_error(self) -> None:
        """Increment local error counter (H-1)."""
        with self._metrics_lock:
            self._local_errors += 1
    
    def _increment_bypass(self) -> None:
        """Increment local bypass counter (H-1)."""
        with self._metrics_lock:
            self._local_bypasses += 1
    
    def get_local_metrics(self) -> CacheMetrics:
        """
        Get local in-memory cache metrics (H-1).
        
        Returns:
            CacheMetrics with hits, misses, errors, bypasses
            
        Note: These are ephemeral and reset on process restart.
        For persistent stats, use get_stats() which reads from Redis.
        """
        with self._metrics_lock:
            return CacheMetrics(
                hits=self._local_hits,
                misses=self._local_misses,
                errors=self._local_errors,
                bypasses=self._local_bypasses,
            )
    
    def reset_local_metrics(self) -> None:
        """Reset local in-memory cache metrics (H-1)."""
        with self._metrics_lock:
            self._local_hits = 0
            self._local_misses = 0
            self._local_errors = 0
            self._local_bypasses = 0
    
    def is_enabled(self) -> bool:
        """Check if caching is enabled and client is available."""
        return self.enabled and self._get_client() is not None
    
    def _get_client(self) -> Optional[Any]:
        """Get or create Redis client with connection pooling."""
        if self._client is not None:
            return self._client
        
        # If connection previously failed, wait before retrying
        if self._connection_failed:
            elapsed = time.time() - self._last_connection_attempt
            if elapsed < self.CONNECTION_RETRY_INTERVAL:
                return None
        
        try:
            import redis
            
            self._last_connection_attempt = time.time()
            self._client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
            )
            # Test connection
            self._client.ping()
            self._connection_failed = False
            logger.info(f"Connected to Redis at {self.redis_url}")
            return self._client
            
        except ImportError:
            logger.warning(
                "redis package not installed. LLM cache disabled. "
                "Install with: pip install redis"
            )
            self._connection_failed = True
            return None
            
        except Exception as e:
            logger.warning(f"Failed to connect to Redis at {self.redis_url}: {e}")
            self._connection_failed = True
            return None
    
    def get(
        self,
        provider: str,
        model: str,
        task_type: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        api_key_hash: Optional[str] = None,
    ) -> Optional[str]:
        """
        Get cached LLM response.
        
        Args:
            provider: LLM provider (openai, anthropic, google)
            model: Model name (gpt-4o, claude-3-sonnet, etc.)
            task_type: Task type for namespacing
            prompt: User prompt
            system_prompt: Optional system prompt
            api_key_hash: Hash of API key for tenant isolation
            
        Returns:
            Cached response if found, None otherwise
        """
        if not self.enabled:
            # H-1: Track bypass when cache is disabled
            self._increment_bypass()
            return None
        
        client = self._get_client()
        if client is None:
            # H-1: Track bypass when Redis is unavailable
            self._increment_bypass()
            return None
        
        key = _generate_cache_key(provider, model, task_type, prompt, system_prompt, api_key_hash)
        
        try:
            cached = client.get(key)
            if cached is not None:
                # Increment hit counter (Redis + local)
                client.incr(STATS_HITS_KEY)
                self._increment_hit()  # H-1: Local tracking
                logger.debug(f"Cache HIT for key {key[:50]}...")
                return cached
            else:
                # Increment miss counter (Redis + local)
                client.incr(STATS_MISSES_KEY)
                self._increment_miss()  # H-1: Local tracking
                logger.debug(f"Cache MISS for key {key[:50]}...")
                return None
                
        except Exception as e:
            # H-1: Track error
            self._increment_error()
            logger.warning(f"Cache get failed: {e}")
            return None
    
    def set(
        self,
        provider: str,
        model: str,
        task_type: str,
        prompt: str,
        system_prompt: Optional[str],
        response: str,
        ttl: Optional[int] = None,
        api_key_hash: Optional[str] = None,
    ) -> bool:
        """
        Cache an LLM response.
        
        Args:
            provider: LLM provider
            model: Model name
            task_type: Task type
            prompt: User prompt
            system_prompt: Optional system prompt
            response: LLM response to cache
            ttl: Optional TTL override in seconds
            api_key_hash: Hash of API key for tenant isolation
            
        Returns:
            True if cached successfully, False otherwise
        """
        if not self.enabled:
            return False
        
        client = self._get_client()
        if client is None:
            return False
        
        key = _generate_cache_key(provider, model, task_type, prompt, system_prompt, api_key_hash)
        effective_ttl = ttl or self.ttl
        
        try:
            client.setex(key, effective_ttl, response)
            logger.debug(f"Cached response for key {key[:50]}... (TTL: {effective_ttl}s)")
            return True
            
        except Exception as e:
            # H-1: Track error
            self._increment_error()
            logger.warning(f"Cache set failed: {e}")
            return False
    
    def delete(
        self,
        provider: str,
        model: str,
        task_type: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        api_key_hash: Optional[str] = None,
    ) -> bool:
        """
        Delete a cached response.
        
        Args:
            provider: LLM provider
            model: Model name
            task_type: Task type
            prompt: User prompt
            system_prompt: Optional system prompt
            api_key_hash: Hash of API key for tenant isolation
            
        Returns:
            True if deleted, False otherwise
        """
        if not self.enabled:
            return False
        
        client = self._get_client()
        if client is None:
            return False
        
        key = _generate_cache_key(provider, model, task_type, prompt, system_prompt, api_key_hash)
        
        try:
            deleted = client.delete(key)
            if deleted:
                client.incr(STATS_EVICTIONS_KEY)
            return deleted > 0
            
        except Exception as e:
            # H-1: Track error
            self._increment_error()
            logger.warning(f"Cache delete failed: {e}")
            return False
    
    def get_stats(self) -> CacheStats:
        """
        Get cache statistics.
        
        Returns:
            CacheStats with hits, misses, evictions, hit_rate
            
        Note: For local metrics (no Redis required), use get_local_metrics().
        """
        client = self._get_client()
        if client is None:
            return CacheStats()
        
        try:
            hits = int(client.get(STATS_HITS_KEY) or 0)
            misses = int(client.get(STATS_MISSES_KEY) or 0)
            evictions = int(client.get(STATS_EVICTIONS_KEY) or 0)
            
            return CacheStats(
                hits=hits,
                misses=misses,
                evictions=evictions,
            )
            
        except Exception as e:
            # H-1: Track error
            self._increment_error()
            logger.warning(f"Failed to get cache stats: {e}")
            return CacheStats()
    
    def clear(self, pattern: Optional[str] = None) -> int:
        """
        Clear cached entries.
        
        Args:
            pattern: Optional pattern to match keys (e.g., "llm:openai:*")
                    If None, clears all LLM cache entries.
                    
        Returns:
            Number of keys deleted
        """
        client = self._get_client()
        if client is None:
            return 0
        
        try:
            # Default pattern: all LLM cache keys
            search_pattern = pattern or "llm:*"
            
            # Use SCAN to find keys (safer than KEYS for large datasets)
            deleted = 0
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor, match=search_pattern, count=100)
                if keys:
                    deleted += client.delete(*keys)
                if cursor == 0:
                    break
            
            logger.info(f"Cleared {deleted} cache entries matching '{search_pattern}'")
            return deleted
            
        except Exception as e:
            # H-1: Track error
            self._increment_error()
            logger.warning(f"Cache clear failed: {e}")
            return 0
    
    def reset_stats(self) -> bool:
        """
        Reset cache statistics (Redis-stored).
        
        Returns:
            True if reset successfully, False otherwise
            
        Note: To reset local metrics, use reset_local_metrics().
        """
        client = self._get_client()
        if client is None:
            return False
        
        try:
            client.delete(STATS_HITS_KEY, STATS_MISSES_KEY, STATS_EVICTIONS_KEY)
            logger.info("Cache statistics reset")
            return True
            
        except Exception as e:
            # H-1: Track error
            self._increment_error()
            logger.warning(f"Failed to reset cache stats: {e}")
            return False
    
    def is_available(self) -> bool:
        """
        Check if cache is available and connected.
        
        Returns:
            True if cache is enabled and Redis is connected and responding
        """
        if not self.enabled:
            return False
            
        client = self._get_client()
        if client is None:
            return False
        
        try:
            client.ping()
            return True
        except Exception:
            return False

# Global cache instance (lazy-loaded)
_cache_instance: Optional[LLMCache] = None


def get_llm_cache() -> LLMCache:
    """
    Get the global LLM cache instance.
    
    Lazily creates the cache with configuration from environment variables.
    
    Returns:
        LLMCache instance (may be disabled if Redis unavailable)
    """
    global _cache_instance
    
    if _cache_instance is None:
        config = _get_cache_config()
        _cache_instance = LLMCache(
            redis_url=config["redis_url"],
            ttl=config["ttl"],
            enabled=config["enabled"],
        )
    
    return _cache_instance


def reset_cache() -> None:
    """Reset the global cache instance (for testing)."""
    global _cache_instance
    _cache_instance = None


# =============================================================================
# Cache-aware wrapper functions for use by LLM clients
# =============================================================================

def cached_llm_call(
    provider: str,
    model: str,
    task_type: str,
    prompt: str,
    system_prompt: Optional[str],
    call_fn,
) -> str:
    """
    Execute an LLM call with caching.
    
    This is a convenience wrapper that:
    1. Checks the cache for an existing response
    2. If not found, calls the provided function
    3. Caches the result before returning
    
    Args:
        provider: LLM provider name
        model: Model name
        task_type: Task type for namespacing
        prompt: User prompt
        system_prompt: Optional system prompt
        call_fn: Function that makes the actual LLM API call (returns str)
        
    Returns:
        LLM response (from cache or fresh call)
        
    Example:
        def _make_call():
            return llm.invoke(messages).content
            
        result = cached_llm_call(
            provider="openai",
            model="gpt-4o",
            task_type="planning",
            prompt=prompt,
            system_prompt=system_prompt,
            call_fn=_make_call,
        )
    """
    cache = get_llm_cache()
    
    # Check cache first
    cached = cache.get(provider, model, task_type, prompt, system_prompt)
    if cached is not None:
        return cached
    
    # Call the API
    result = call_fn()
    
    # Cache the result
    cache.set(provider, model, task_type, prompt, system_prompt, result)
    
    return result
