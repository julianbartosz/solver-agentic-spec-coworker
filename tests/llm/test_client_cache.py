"""
Tests for LLM client caching with bounds.

Production Readiness v4 - P1-2:
Tests session-based caching with LRU eviction.
"""

import pytest
from unittest.mock import patch, MagicMock

from integration_coworker.llm.client import (
    LLMClientCacheKey,
    get_client_cache_info,
    clear_client_cache,
    _get_cached_client_by_key,
    _LLM_CLIENT_CACHE_SIZE,
)


class TestLLMClientCacheKey:
    """Test LLMClientCacheKey dataclass."""
    
    def test_cache_key_hashable(self):
        """Test cache key is hashable."""
        key = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="default",
            mode="real",
            api_key_hash="abc123",
        )
        # Should not raise
        hash(key)
    
    def test_cache_key_frozen(self):
        """Test cache key is immutable."""
        key = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="default",
            mode="real",
            api_key_hash="abc123",
        )
        with pytest.raises(AttributeError):
            key.provider = "anthropic"
    
    def test_cache_key_equality(self):
        """Test cache key equality."""
        key1 = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="default",
            mode="real",
            api_key_hash="abc123",
        )
        key2 = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="default",
            mode="real",
            api_key_hash="abc123",
        )
        assert key1 == key2
        assert hash(key1) == hash(key2)
    
    def test_cache_key_inequality(self):
        """Test cache key inequality."""
        key1 = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="default",
            mode="real",
            api_key_hash="abc123",
        )
        key2 = LLMClientCacheKey(
            provider="anthropic",
            model="claude-3-opus",
            task_type="default",
            mode="real",
            api_key_hash="def456",
        )
        assert key1 != key2
    
    def test_cache_key_different_api_key_hash_not_equal(self):
        """Test cache keys with different api_key_hash are NOT equal.
        
        CRITICAL: This prevents credential mixup across configs.
        """
        key1 = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="default",
            mode="real",
            api_key_hash="abc123",
        )
        key2 = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="default",
            mode="real",
            api_key_hash="xyz789",  # Different key hash
        )
        assert key1 != key2
        assert hash(key1) != hash(key2)
    
    def test_hash_api_key_deterministic(self):
        """Test hash_api_key is deterministic."""
        hash1 = LLMClientCacheKey.hash_api_key("sk-test-key-12345")
        hash2 = LLMClientCacheKey.hash_api_key("sk-test-key-12345")
        assert hash1 == hash2
    
    def test_hash_api_key_different_for_different_keys(self):
        """Test hash_api_key produces different hashes for different keys."""
        hash1 = LLMClientCacheKey.hash_api_key("sk-key-one")
        hash2 = LLMClientCacheKey.hash_api_key("sk-key-two")
        assert hash1 != hash2
    
    def test_hash_api_key_empty_key(self):
        """Test hash_api_key handles empty key."""
        hash_empty = LLMClientCacheKey.hash_api_key("")
        assert hash_empty == "no_key"
    
    def test_hash_api_key_none_key(self):
        """Test hash_api_key handles None-like empty string."""
        # In practice, API keys come from env vars which return ""
        hash_empty = LLMClientCacheKey.hash_api_key("")
        assert hash_empty == "no_key"
    
    def test_cache_key_default_values(self):
        """Test cache key default values."""
        key = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="default",
            mode="real",
            api_key_hash="abc123",
        )
        assert key.temperature == 0.7
        assert key.max_tokens == 2000
    
    def test_cache_key_custom_values(self):
        """Test cache key with custom values."""
        key = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="codegen",
            mode="real",
            api_key_hash="abc123",
            temperature=0.2,
            max_tokens=4000,
        )
        assert key.temperature == 0.2
        assert key.max_tokens == 4000


class TestClientCacheInfo:
    """Test cache info functions."""
    
    def setup_method(self):
        """Clear cache before each test."""
        clear_client_cache()
    
    def teardown_method(self):
        """Clear cache after each test."""
        clear_client_cache()
    
    def test_cache_info_returns_dict(self):
        """Test cache info returns dict with expected keys."""
        info = get_client_cache_info()
        
        assert isinstance(info, dict)
        assert "hits" in info
        assert "misses" in info
        assert "maxsize" in info
        assert "currsize" in info
    
    def test_cache_info_maxsize(self):
        """Test cache reports correct maxsize."""
        info = get_client_cache_info()
        
        assert info["maxsize"] == _LLM_CLIENT_CACHE_SIZE
    
    def test_cache_info_initial_state(self):
        """Test cache starts empty."""
        info = get_client_cache_info()
        
        assert info["currsize"] == 0
    
    def test_clear_cache_resets_stats(self):
        """Test clear_client_cache resets cache."""
        # Access cache to populate stats
        key = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="test",
            mode="real",
            api_key_hash="abc123",
        )
        _get_cached_client_by_key(key)
        
        info_before = get_client_cache_info()
        assert info_before["misses"] >= 1
        
        clear_client_cache()
        
        info_after = get_client_cache_info()
        # After clear, stats are reset
        assert info_after["hits"] == 0
        assert info_after["misses"] == 0


class TestCacheLRUBehavior:
    """Test LRU cache behavior."""
    
    def setup_method(self):
        """Clear cache before each test."""
        clear_client_cache()
    
    def teardown_method(self):
        """Clear cache after each test."""
        clear_client_cache()
    
    def test_cache_hit_increases_hits(self):
        """Test cache hit increments hit counter."""
        key = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="test",
            mode="real",
            api_key_hash="abc123",
        )
        
        # First call is a miss
        _get_cached_client_by_key(key)
        info1 = get_client_cache_info()
        assert info1["misses"] == 1
        
        # Second call with same key is a hit
        _get_cached_client_by_key(key)
        info2 = get_client_cache_info()
        assert info2["hits"] == 1
    
    def test_different_keys_are_misses(self):
        """Test different keys cause cache misses."""
        key1 = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o",
            task_type="test1",
            mode="real",
            api_key_hash="abc123",
        )
        key2 = LLMClientCacheKey(
            provider="openai",
            model="gpt-4o-mini",
            task_type="test2",
            mode="real",
            api_key_hash="abc123",
        )
        
        _get_cached_client_by_key(key1)
        _get_cached_client_by_key(key2)
        
        info = get_client_cache_info()
        assert info["misses"] == 2
        assert info["currsize"] == 2
    
    def test_cache_bounded_by_maxsize(self):
        """Test cache is bounded by maxsize."""
        # Fill cache beyond maxsize
        for i in range(_LLM_CLIENT_CACHE_SIZE + 5):
            key = LLMClientCacheKey(
                provider="openai",
                model=f"model-{i}",
                task_type="test",
                mode="real",
                api_key_hash="abc123",
            )
            _get_cached_client_by_key(key)
        
        info = get_client_cache_info()
        # Cache should not exceed maxsize
        assert info["currsize"] <= info["maxsize"]


class TestCacheThreadSafety:
    """Test cache thread safety (via lru_cache)."""
    
    def setup_method(self):
        """Clear cache before each test."""
        clear_client_cache()
    
    def teardown_method(self):
        """Clear cache after each test."""
        clear_client_cache()
    
    def test_concurrent_access_doesnt_raise(self):
        """Test concurrent access is handled (lru_cache has internal locking)."""
        import threading
        
        errors = []
        
        def worker(worker_id: int):
            try:
                for i in range(10):
                    key = LLMClientCacheKey(
                        provider="openai",
                        model=f"model-{i % 5}",
                        task_type=f"task-{worker_id}",
                        mode="real",
                        api_key_hash="abc123",
                    )
                    _get_cached_client_by_key(key)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        
        # No errors should occur
        assert len(errors) == 0
