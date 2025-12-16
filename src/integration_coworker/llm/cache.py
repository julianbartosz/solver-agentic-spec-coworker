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
"""

import hashlib
import json
import logging
import os
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
) -> str:
    """
    Generate a cache key for an LLM request.
    
    Format: llm:<provider>:<model>:<task_type>:<hash>
    
    The hash is SHA-256 of the concatenated prompt content.
    """
    content = f"{system_prompt or ''}|||{prompt}"
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    return f"llm:{provider}:{model}:{task_type}:{content_hash}"


@dataclass
class CacheStats:
    """Cache statistics."""
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
class LLMCache:
    """
    Redis-backed LLM response cache.
    
    Provides caching for LLM API responses with:
    - Content-based key generation (SHA-256)
    - Configurable TTL
    - Statistics tracking
    - Graceful degradation when Redis unavailable
    """
    
    redis_url: str = DEFAULT_REDIS_URL
    ttl: int = DEFAULT_CACHE_TTL
    enabled: bool = DEFAULT_CACHE_ENABLED
    
    _client: Optional[Any] = field(default=None, repr=False)
    _connection_failed: bool = field(default=False, repr=False)
    _last_connection_attempt: float = field(default=0.0, repr=False)
    
    # Retry connection every 60 seconds if failed
    CONNECTION_RETRY_INTERVAL = 60.0
    
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
    ) -> Optional[str]:
        """
        Get cached LLM response.
        
        Args:
            provider: LLM provider (openai, anthropic, google)
            model: Model name (gpt-4o, claude-3-sonnet, etc.)
            task_type: Task type for namespacing
            prompt: User prompt
            system_prompt: Optional system prompt
            
        Returns:
            Cached response if found, None otherwise
        """
        if not self.enabled:
            return None
        
        client = self._get_client()
        if client is None:
            return None
        
        key = _generate_cache_key(provider, model, task_type, prompt, system_prompt)
        
        try:
            cached = client.get(key)
            if cached is not None:
                # Increment hit counter
                client.incr(STATS_HITS_KEY)
                logger.debug(f"Cache HIT for key {key[:50]}...")
                return cached
            else:
                # Increment miss counter
                client.incr(STATS_MISSES_KEY)
                logger.debug(f"Cache MISS for key {key[:50]}...")
                return None
                
        except Exception as e:
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
            
        Returns:
            True if cached successfully, False otherwise
        """
        if not self.enabled:
            return False
        
        client = self._get_client()
        if client is None:
            return False
        
        key = _generate_cache_key(provider, model, task_type, prompt, system_prompt)
        effective_ttl = ttl or self.ttl
        
        try:
            client.setex(key, effective_ttl, response)
            logger.debug(f"Cached response for key {key[:50]}... (TTL: {effective_ttl}s)")
            return True
            
        except Exception as e:
            logger.warning(f"Cache set failed: {e}")
            return False
    
    def delete(
        self,
        provider: str,
        model: str,
        task_type: str,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> bool:
        """
        Delete a cached response.
        
        Args:
            provider: LLM provider
            model: Model name
            task_type: Task type
            prompt: User prompt
            system_prompt: Optional system prompt
            
        Returns:
            True if deleted, False otherwise
        """
        if not self.enabled:
            return False
        
        client = self._get_client()
        if client is None:
            return False
        
        key = _generate_cache_key(provider, model, task_type, prompt, system_prompt)
        
        try:
            deleted = client.delete(key)
            if deleted:
                client.incr(STATS_EVICTIONS_KEY)
            return deleted > 0
            
        except Exception as e:
            logger.warning(f"Cache delete failed: {e}")
            return False
    
    def get_stats(self) -> CacheStats:
        """
        Get cache statistics.
        
        Returns:
            CacheStats with hits, misses, evictions, hit_rate
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
            logger.warning(f"Cache clear failed: {e}")
            return 0
    
    def reset_stats(self) -> bool:
        """
        Reset cache statistics.
        
        Returns:
            True if reset successfully, False otherwise
        """
        client = self._get_client()
        if client is None:
            return False
        
        try:
            client.delete(STATS_HITS_KEY, STATS_MISSES_KEY, STATS_EVICTIONS_KEY)
            logger.info("Cache statistics reset")
            return True
            
        except Exception as e:
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
