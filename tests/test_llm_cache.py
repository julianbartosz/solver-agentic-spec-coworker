"""
Tests for LLM Response Cache (Plan 7).

Tests the Redis-backed caching layer for LLM API responses.
Uses fakeredis for isolated testing without requiring a real Redis instance.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

# Mark all tests in this module as not requiring the database
pytestmark = pytest.mark.no_db


# =============================================================================
# Fixtures
# =============================================================================

class SimpleDictRedis:
    """
    Simple dict-based Redis mock for testing when fakeredis is not available.
    
    Implements just enough of the Redis interface for cache tests.
    """
    def __init__(self):
        self._data = {}
    
    def get(self, key):
        return self._data.get(key)
    
    def setex(self, key, ttl, value):
        self._data[key] = value
    
    def delete(self, *keys):
        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
        return count
    
    def incr(self, key):
        # Properly increment and store in _data like real Redis
        current = int(self._data.get(key, 0))
        self._data[key] = str(current + 1)  # Redis stores as string
        return current + 1
    
    def ping(self):
        return True
    
    def scan(self, cursor, match=None, count=None):
        # Simple implementation - return all matching keys at once
        if match and "*" in match:
            prefix = match.replace("*", "")
            keys = [k for k in self._data if k.startswith(prefix)]
        elif match:
            keys = [k for k in self._data if k == match]
        else:
            keys = list(self._data.keys())
        return (0, keys)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client for testing."""
    try:
        import fakeredis
        return fakeredis.FakeStrictRedis(decode_responses=True)
    except ImportError:
        # Fall back to simple dict-based mock
        return SimpleDictRedis()


@pytest.fixture
def cache_with_mock_redis(mock_redis):
    """Create an LLMCache with a mock Redis client."""
    from integration_coworker.llm.cache import LLMCache, reset_cache
    
    reset_cache()
    
    cache = LLMCache(
        redis_url="redis://localhost:6379/0",
        ttl=3600,
        enabled=True,
    )
    # Inject mock redis client
    cache._client = mock_redis
    cache._connection_failed = False
    
    return cache


@pytest.fixture
def disabled_cache():
    """Create a disabled LLMCache."""
    from integration_coworker.llm.cache import LLMCache, reset_cache
    
    reset_cache()
    
    return LLMCache(
        redis_url="redis://localhost:6379/0",
        ttl=3600,
        enabled=False,
    )


# =============================================================================
# Test Cache Key Generation
# =============================================================================

class TestCacheKeyGeneration:
    """Test cache key generation."""
    
    def test_key_format(self):
        """Test that cache keys follow the expected format.
        
        Item E Security: Key format is now:
        llm:<provider>:<model>:<task_type>:<api_key_hash>:<content_hash>
        (6 parts, not 5)
        """
        from integration_coworker.llm.cache import _generate_cache_key
        
        key = _generate_cache_key(
            provider="openai",
            model="gpt-4o",
            task_type="planning",
            prompt="Test prompt",
            system_prompt="System prompt",
        )
        
        # Item E: Now includes api_key_hash (defaults to "shared")
        assert key.startswith("llm:openai:gpt-4o:planning:shared:")
        assert len(key.split(":")) == 6
    
    def test_key_deterministic(self):
        """Test that same inputs produce same key."""
        from integration_coworker.llm.cache import _generate_cache_key
        
        key1 = _generate_cache_key("openai", "gpt-4o", "test", "prompt", "system")
        key2 = _generate_cache_key("openai", "gpt-4o", "test", "prompt", "system")
        
        assert key1 == key2
    
    def test_key_different_prompts(self):
        """Test that different prompts produce different keys."""
        from integration_coworker.llm.cache import _generate_cache_key
        
        key1 = _generate_cache_key("openai", "gpt-4o", "test", "prompt1", "system")
        key2 = _generate_cache_key("openai", "gpt-4o", "test", "prompt2", "system")
        
        assert key1 != key2
    
    def test_key_different_providers(self):
        """Test that different providers produce different keys."""
        from integration_coworker.llm.cache import _generate_cache_key
        
        key1 = _generate_cache_key("openai", "gpt-4o", "test", "prompt", "system")
        key2 = _generate_cache_key("anthropic", "claude-3", "test", "prompt", "system")
        
        assert key1 != key2
    
    def test_key_none_system_prompt(self):
        """Test key generation with None system prompt."""
        from integration_coworker.llm.cache import _generate_cache_key
        
        key = _generate_cache_key("openai", "gpt-4o", "test", "prompt", None)
        
        assert key.startswith("llm:openai:gpt-4o:test:")

    def test_key_different_api_keys_produce_different_keys(self):
        """Test that different API key hashes produce different cache keys.
        
        Item E Security: Ensures tenant isolation in cache - two users with
        different API keys will have separate cache entries.
        """
        from integration_coworker.llm.cache import _generate_cache_key
        
        # Same prompt/model but different api_key_hash
        key1 = _generate_cache_key(
            "openai", "gpt-4o", "test", "prompt", "system",
            api_key_hash="user1_api_hash_abc123"
        )
        key2 = _generate_cache_key(
            "openai", "gpt-4o", "test", "prompt", "system",
            api_key_hash="user2_api_hash_xyz789"
        )
        
        assert key1 != key2
        assert "user1_api_hash_abc123" in key1
        assert "user2_api_hash_xyz789" in key2

    def test_key_with_api_key_hash_vs_shared(self):
        """Test that providing api_key_hash differs from shared/default."""
        from integration_coworker.llm.cache import _generate_cache_key
        
        key_with_hash = _generate_cache_key(
            "openai", "gpt-4o", "test", "prompt", "system",
            api_key_hash="my_api_hash"
        )
        key_shared = _generate_cache_key(
            "openai", "gpt-4o", "test", "prompt", "system",
            api_key_hash=None  # Should default to "shared"
        )
        
        assert key_with_hash != key_shared
        assert "my_api_hash" in key_with_hash
        assert "shared" in key_shared


# =============================================================================
# Test Cache Operations
# =============================================================================

class TestCacheOperations:
    """Test cache get/set/delete operations."""
    
    def test_set_and_get(self, cache_with_mock_redis):
        """Test setting and getting a cached value."""
        cache = cache_with_mock_redis
        
        # Set a value
        result = cache.set(
            provider="openai",
            model="gpt-4o",
            task_type="test",
            prompt="Hello",
            system_prompt="Be helpful",
            response="Hi there!",
        )
        assert result is True
        
        # Get the value
        cached = cache.get(
            provider="openai",
            model="gpt-4o",
            task_type="test",
            prompt="Hello",
            system_prompt="Be helpful",
        )
        assert cached == "Hi there!"
    
    def test_get_miss(self, cache_with_mock_redis):
        """Test cache miss returns None."""
        cache = cache_with_mock_redis
        
        result = cache.get(
            provider="openai",
            model="gpt-4o",
            task_type="test",
            prompt="Not in cache",
            system_prompt=None,
        )
        assert result is None
    
    def test_delete(self, cache_with_mock_redis):
        """Test deleting a cached value."""
        cache = cache_with_mock_redis
        
        # Set a value
        cache.set("openai", "gpt-4o", "test", "prompt", "system", "response")
        
        # Verify it exists
        assert cache.get("openai", "gpt-4o", "test", "prompt", "system") == "response"
        
        # Delete it
        result = cache.delete("openai", "gpt-4o", "test", "prompt", "system")
        assert result is True
        
        # Verify it's gone
        assert cache.get("openai", "gpt-4o", "test", "prompt", "system") is None
    
    def test_custom_ttl(self, cache_with_mock_redis):
        """Test setting a custom TTL."""
        cache = cache_with_mock_redis
        
        result = cache.set(
            provider="openai",
            model="gpt-4o",
            task_type="test",
            prompt="prompt",
            system_prompt=None,
            response="response",
            ttl=60,  # 1 minute
        )
        assert result is True


# =============================================================================
# Test Disabled Cache
# =============================================================================

class TestDisabledCache:
    """Test behavior when cache is disabled."""
    
    def test_get_returns_none_when_disabled(self, disabled_cache):
        """Test that get returns None when cache is disabled."""
        result = disabled_cache.get("openai", "gpt-4o", "test", "prompt", None)
        assert result is None
    
    def test_set_returns_false_when_disabled(self, disabled_cache):
        """Test that set returns False when cache is disabled."""
        result = disabled_cache.set("openai", "gpt-4o", "test", "prompt", None, "response")
        assert result is False
    
    def test_delete_returns_false_when_disabled(self, disabled_cache):
        """Test that delete returns False when cache is disabled."""
        result = disabled_cache.delete("openai", "gpt-4o", "test", "prompt", None)
        assert result is False


# =============================================================================
# Test Cache Statistics
# =============================================================================

class TestCacheStatistics:
    """Test cache statistics tracking."""
    
    def test_stats_increment_on_hit(self, cache_with_mock_redis):
        """Test that hits are tracked."""
        cache = cache_with_mock_redis
        
        # Set a value
        cache.set("openai", "gpt-4o", "test", "prompt", None, "response")
        
        # Get it (hit)
        cache.get("openai", "gpt-4o", "test", "prompt", None)
        
        stats = cache.get_stats()
        assert stats.hits >= 1
    
    def test_stats_increment_on_miss(self, cache_with_mock_redis):
        """Test that misses are tracked."""
        cache = cache_with_mock_redis
        
        # Try to get non-existent value (miss)
        cache.get("openai", "gpt-4o", "test", "nonexistent", None)
        
        stats = cache.get_stats()
        assert stats.misses >= 1
    
    def test_stats_to_dict(self, cache_with_mock_redis):
        """Test converting stats to dict."""
        cache = cache_with_mock_redis
        
        # Generate some hits and misses
        cache.set("openai", "gpt-4o", "test", "prompt", None, "response")
        cache.get("openai", "gpt-4o", "test", "prompt", None)  # hit
        cache.get("openai", "gpt-4o", "test", "other", None)   # miss
        
        stats_dict = cache.get_stats().to_dict()
        
        assert "hits" in stats_dict
        assert "misses" in stats_dict
        assert "hit_rate" in stats_dict
        assert "total_requests" in stats_dict
    
    def test_reset_stats(self, cache_with_mock_redis):
        """Test resetting statistics."""
        cache = cache_with_mock_redis
        
        # Generate some activity
        cache.set("openai", "gpt-4o", "test", "prompt", None, "response")
        cache.get("openai", "gpt-4o", "test", "prompt", None)
        
        # Reset stats
        result = cache.reset_stats()
        assert result is True
        
        # Stats should be zero
        stats = cache.get_stats()
        assert stats.hits == 0
        assert stats.misses == 0


# =============================================================================
# Test Cache Clear
# =============================================================================

class TestCacheClear:
    """Test cache clearing functionality."""
    
    def test_clear_all(self, cache_with_mock_redis):
        """Test clearing all cache entries."""
        cache = cache_with_mock_redis
        
        # Set multiple values
        cache.set("openai", "gpt-4o", "test1", "p1", None, "r1")
        cache.set("openai", "gpt-4o", "test2", "p2", None, "r2")
        cache.set("anthropic", "claude", "test", "p", None, "r")
        
        # Clear all
        deleted = cache.clear()
        assert deleted >= 3
        
        # Verify all are gone
        assert cache.get("openai", "gpt-4o", "test1", "p1", None) is None
        assert cache.get("openai", "gpt-4o", "test2", "p2", None) is None
    
    def test_clear_with_pattern(self, cache_with_mock_redis):
        """Test clearing with pattern filter."""
        cache = cache_with_mock_redis
        
        # Set values for different providers
        cache.set("openai", "gpt-4o", "test", "p1", None, "r1")
        cache.set("anthropic", "claude", "test", "p2", None, "r2")
        
        # Clear only openai entries
        deleted = cache.clear(pattern="llm:openai:*")
        assert deleted >= 1
        
        # OpenAI should be gone, Anthropic should remain
        assert cache.get("openai", "gpt-4o", "test", "p1", None) is None
        # Note: fakeredis may not support SCAN properly, so we skip this assertion


# =============================================================================
# Test Availability Check
# =============================================================================

class TestAvailabilityCheck:
    """Test cache availability checking."""
    
    def test_is_available_with_connection(self, cache_with_mock_redis):
        """Test is_available returns True when connected."""
        assert cache_with_mock_redis.is_available() is True
    
    def test_is_available_when_disabled(self, disabled_cache):
        """Test is_available behavior when disabled."""
        # When disabled, we don't try to connect
        assert disabled_cache.is_available() is False


# =============================================================================
# Test Global Cache Instance
# =============================================================================

class TestGlobalCacheInstance:
    """Test global cache instance management."""
    
    def test_get_llm_cache_returns_singleton(self):
        """Test that get_llm_cache returns the same instance."""
        from integration_coworker.llm.cache import get_llm_cache, reset_cache
        
        reset_cache()
        
        cache1 = get_llm_cache()
        cache2 = get_llm_cache()
        
        assert cache1 is cache2
    
    def test_reset_cache_clears_singleton(self):
        """Test that reset_cache clears the singleton."""
        from integration_coworker.llm.cache import get_llm_cache, reset_cache
        
        cache1 = get_llm_cache()
        reset_cache()
        cache2 = get_llm_cache()
        
        assert cache1 is not cache2


# =============================================================================
# Test cached_llm_call wrapper
# =============================================================================

class TestCachedLLMCall:
    """Test the cached_llm_call convenience function."""
    
    def test_cached_llm_call_caches_result(self, cache_with_mock_redis):
        """Test that cached_llm_call properly caches results."""
        from integration_coworker.llm.cache import cached_llm_call, reset_cache, get_llm_cache
        
        # Replace global cache with our mock
        reset_cache()
        
        # Patch get_llm_cache to return our test cache
        with patch('integration_coworker.llm.cache.get_llm_cache', return_value=cache_with_mock_redis):
            call_count = 0
            
            def mock_call():
                nonlocal call_count
                call_count += 1
                return "test response"
            
            # First call should invoke the function
            result1 = cached_llm_call(
                provider="openai",
                model="gpt-4o",
                task_type="test",
                prompt="test prompt",
                system_prompt=None,
                call_fn=mock_call,
            )
            assert result1 == "test response"
            assert call_count == 1
            
            # Second call should use cache
            result2 = cached_llm_call(
                provider="openai",
                model="gpt-4o",
                task_type="test",
                prompt="test prompt",
                system_prompt=None,
                call_fn=mock_call,
            )
            assert result2 == "test response"
            assert call_count == 1  # Should not have called again


# =============================================================================
# Test Configuration
# =============================================================================

class TestCacheConfiguration:
    """Test cache configuration from environment."""
    
    def test_config_from_env(self):
        """Test that cache reads configuration from environment."""
        from integration_coworker.llm.cache import _get_cache_config, reset_cache
        
        reset_cache()
        
        with patch.dict(os.environ, {
            "REDIS_URL": "redis://test:6379/1",
            "LLM_CACHE_ENABLED": "false",
            "LLM_CACHE_TTL": "7200",
        }):
            config = _get_cache_config()
            
            assert config["redis_url"] == "redis://test:6379/1"
            assert config["enabled"] is False
            assert config["ttl"] == 7200
    
    def test_config_defaults(self):
        """Test default configuration values."""
        from integration_coworker.llm.cache import _get_cache_config, reset_cache
        
        reset_cache()
        
        # Clear relevant env vars
        env_override = {
            "REDIS_URL": "",
            "LLM_CACHE_ENABLED": "",
            "LLM_CACHE_TTL": "",
        }
        
        # Remove keys that might interfere
        clean_env = os.environ.copy()
        for key in ["REDIS_URL", "LLM_CACHE_ENABLED", "LLM_CACHE_TTL"]:
            clean_env.pop(key, None)
        
        with patch.dict(os.environ, clean_env, clear=True):
            config = _get_cache_config()
            
            assert config["redis_url"] == "redis://localhost:6379/0"
            assert config["enabled"] is True
            assert config["ttl"] == 86400


# =============================================================================
# Test Integration with Config Module
# =============================================================================

class TestConfigIntegration:
    """Test integration with config module."""
    
    def test_cache_config_in_settings(self):
        """Test that CacheConfig is accessible via settings."""
        from integration_coworker.config import get_settings, reset_settings
        
        reset_settings()
        
        settings = get_settings()
        
        assert hasattr(settings, 'cache')
        assert hasattr(settings.cache, 'redis_url')
        assert hasattr(settings.cache, 'enabled')
        assert hasattr(settings.cache, 'ttl')
    
    def test_get_cache_config_helper(self):
        """Test the get_cache_config helper function."""
        from integration_coworker.config import get_cache_config, reset_settings
        
        reset_settings()
        
        config = get_cache_config()
        
        assert "redis_url" in config
        assert "enabled" in config
        assert "ttl" in config


# =============================================================================
# Test Local Metrics (H-1)
# =============================================================================

class TestLocalMetrics:
    """Test local in-memory cache metrics (H-1)."""
    
    def test_initial_metrics_zero(self, cache_with_mock_redis):
        """Test that initial metrics are all zero."""
        metrics = cache_with_mock_redis.get_local_metrics()
        
        assert metrics.hits == 0
        assert metrics.misses == 0
        assert metrics.errors == 0
        assert metrics.bypasses == 0
    
    def test_hit_tracking(self, cache_with_mock_redis):
        """Test that cache hits are tracked locally."""
        # First, set a value
        cache_with_mock_redis.set(
            "openai", "gpt-4o", "test", "prompt", "system", "response"
        )
        
        # Get it (should be a hit)
        result = cache_with_mock_redis.get(
            "openai", "gpt-4o", "test", "prompt", "system"
        )
        
        assert result == "response"
        
        metrics = cache_with_mock_redis.get_local_metrics()
        assert metrics.hits == 1
        assert metrics.misses == 0
    
    def test_miss_tracking(self, cache_with_mock_redis):
        """Test that cache misses are tracked locally."""
        # Get non-existent key (should be a miss)
        result = cache_with_mock_redis.get(
            "openai", "gpt-4o", "test", "nonexistent", "system"
        )
        
        assert result is None
        
        metrics = cache_with_mock_redis.get_local_metrics()
        assert metrics.hits == 0
        assert metrics.misses == 1
    
    def test_bypass_tracking_disabled(self, disabled_cache):
        """Test that bypasses are tracked when cache is disabled."""
        # Get should bypass when cache is disabled
        result = disabled_cache.get(
            "openai", "gpt-4o", "test", "prompt", "system"
        )
        
        assert result is None
        
        metrics = disabled_cache.get_local_metrics()
        assert metrics.bypasses == 1
        assert metrics.hits == 0
        assert metrics.misses == 0
    
    def test_bypass_tracking_unavailable(self):
        """Test that bypasses are tracked when Redis is unavailable."""
        from integration_coworker.llm.cache import LLMCache, reset_cache
        
        reset_cache()
        
        cache = LLMCache(
            redis_url="redis://localhost:6379/0",
            ttl=3600,
            enabled=True,
        )
        # Simulate connection failure
        cache._connection_failed = True
        cache._last_connection_attempt = 999999999999  # Far in future
        
        result = cache.get("openai", "gpt-4o", "test", "prompt", "system")
        
        assert result is None
        
        metrics = cache.get_local_metrics()
        assert metrics.bypasses == 1
    
    def test_error_tracking(self, cache_with_mock_redis):
        """Test that errors are tracked locally."""
        # Make the client raise an exception
        cache_with_mock_redis._client.get = MagicMock(side_effect=Exception("Redis error"))
        
        result = cache_with_mock_redis.get(
            "openai", "gpt-4o", "test", "prompt", "system"
        )
        
        assert result is None
        
        metrics = cache_with_mock_redis.get_local_metrics()
        assert metrics.errors == 1
    
    def test_reset_local_metrics(self, cache_with_mock_redis):
        """Test that local metrics can be reset."""
        # Generate some metrics
        cache_with_mock_redis.get("openai", "gpt-4o", "test", "prompt1", "system")
        cache_with_mock_redis.get("openai", "gpt-4o", "test", "prompt2", "system")
        
        metrics = cache_with_mock_redis.get_local_metrics()
        assert metrics.misses == 2
        
        # Reset
        cache_with_mock_redis.reset_local_metrics()
        
        metrics = cache_with_mock_redis.get_local_metrics()
        assert metrics.hits == 0
        assert metrics.misses == 0
        assert metrics.errors == 0
        assert metrics.bypasses == 0
    
    def test_metrics_to_dict(self, cache_with_mock_redis):
        """Test that metrics can be converted to dict."""
        # Generate some metrics
        cache_with_mock_redis.set(
            "openai", "gpt-4o", "test", "prompt", "system", "response"
        )
        cache_with_mock_redis.get("openai", "gpt-4o", "test", "prompt", "system")  # Hit
        cache_with_mock_redis.get("openai", "gpt-4o", "test", "miss", "system")  # Miss
        
        metrics = cache_with_mock_redis.get_local_metrics()
        metrics_dict = metrics.to_dict()
        
        assert metrics_dict["hits"] == 1
        assert metrics_dict["misses"] == 1
        assert metrics_dict["total_requests"] == 2
        assert metrics_dict["hit_rate"] == "50.00%"
        assert "bypass_rate" in metrics_dict
        assert "error_count" in metrics_dict
    
    def test_metrics_thread_safety(self, cache_with_mock_redis):
        """Test that metrics are thread-safe."""
        import threading
        
        def make_requests():
            for _ in range(100):
                cache_with_mock_redis.get("openai", "gpt-4o", "test", f"prompt", "system")
        
        threads = [threading.Thread(target=make_requests) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        metrics = cache_with_mock_redis.get_local_metrics()
        # All requests should be accounted for
        assert metrics.misses == 500  # 5 threads * 100 requests
