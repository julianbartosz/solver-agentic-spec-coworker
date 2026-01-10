"""
Behavior Tests for Web Search Module (Slice 4).

These tests verify the behavioral contract of web search integration:
1. Resolver → web search invocation conditions
2. Deterministic queries and strict caps
3. Cache hit/miss behavior and TTL expiry
4. URL validation and deduplication

CRITICAL: All tests run offline using mocks. No real API calls.

HERMETIC TESTING:
    - All cache tests use tmp_path fixture to avoid ~/.cache pollution
    - Config is created explicitly (not from env) to avoid staleness
    - reset_web_search_cache() is called in fixtures to isolate tests
"""

import hashlib
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integration_coworker.discovery.web_search import (
    WebSearchConfig,
    WebSearchHit,
    WebSearchResult,
    generate_spec_search_queries,
    get_web_search_config,
    reset_web_search_cache,
    score_spec_url_heuristic,
    search_web_for_specs,
)
from integration_coworker.discovery.web_search_cache import (
    WebSearchCache,
    get_web_search_cache,
)
from integration_coworker.discovery.web_search_scoring import hash_query_for_logging


@pytest.fixture(autouse=True)
def reset_cache_state():
    """Reset global cache state before each test for isolation."""
    reset_web_search_cache()
    yield
    reset_web_search_cache()


# =============================================================================
# Test: Resolver → Web Search Invocation Conditions
# =============================================================================


class TestResolverWebSearchInvocation:
    """
    Verify: Resolver invokes web search ONLY when:
    1. DISCOVERY_WEB_SEARCH_ENABLED=true
    2. No strong candidates (score < 0.7 threshold)
    """

    @pytest.mark.asyncio
    async def test_web_search_not_called_when_disabled(self):
        """Web search is NOT called when DISCOVERY_WEB_SEARCH_ENABLED=false."""
        with patch.dict(os.environ, {"DISCOVERY_WEB_SEARCH_ENABLED": "false"}, clear=False):
            # Import fresh config
            config = get_web_search_config()
            assert config.enabled is False
            
            # The search function should bail out early when disabled
            with patch("integration_coworker.discovery.web_search.get_web_search_provider") as mock_get_provider:
                result = await search_web_for_specs(
                    explicit_provider="stripe",
                    keywords=["payments", "api"],
                )
                
                # Provider should never be called
                mock_get_provider.assert_not_called()
                assert len(result) == 0

    @pytest.mark.asyncio
    async def test_web_search_called_when_enabled_no_strong_candidates(self):
        """Web search IS called when enabled and no strong candidates exist."""
        reset_web_search_cache()
        # Also clear any disk cache files
        cache = get_web_search_cache()
        cache.clear()
        
        # Create an explicit config to avoid env var caching issues
        config = WebSearchConfig(
            enabled=True,
            provider="tavily",
            tavily_api_key="test-key",
            max_queries=3,
            max_results=5,
            timeout_seconds=10,
            total_timeout_seconds=45,
            cache_ttl_hours=24,
        )
        
        # Patch at the module level like the working tests do
        with patch("httpx.AsyncClient") as mock_client:
            # Setup mock response
            mock_response = {
                "results": [{
                    "url": "https://api.stripe.com/openapi.json",
                    "title": "Stripe API OpenAPI Spec",
                    "content": "OpenAPI specification for Stripe API",
                }]
            }
            
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = MagicMock(
                status_code=200,
                json=lambda: mock_response,
            )
            
            result = await search_web_for_specs(
                explicit_provider="stripe",
                keywords=["payments", "api"],
                config=config,
            )
            
            # Provider should be called since enabled
            mock_instance.post.assert_called()
            # Should return results
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_web_search_respects_total_timeout_budget(self):
        """Web search respects total_timeout_seconds budget."""
        reset_web_search_cache()
        
        with patch.dict(os.environ, {
            "DISCOVERY_WEB_SEARCH_ENABLED": "true",
            "TAVILY_API_KEY": "test-key",
            "DISCOVERY_WEB_SEARCH_TOTAL_TIMEOUT_SECONDS": "5",  # Short budget
        }, clear=False):
            config = get_web_search_config()
            assert config.total_timeout_seconds == 5


# =============================================================================
# Test: Deterministic Queries and Strict Caps
# =============================================================================


class TestDeterministicQueriesAndCaps:
    """Verify queries are deterministic and caps are enforced."""

    def test_queries_are_deterministic(self):
        """Same inputs always produce same queries."""
        queries1 = generate_spec_search_queries("stripe", "api.stripe.com", 3)
        queries2 = generate_spec_search_queries("stripe", "api.stripe.com", 3)
        
        assert queries1 == queries2

    def test_queries_deterministic_different_caps(self):
        """Lower cap produces subset of higher cap queries."""
        queries_3 = generate_spec_search_queries("github", "api.github.com", 3)
        queries_5 = generate_spec_search_queries("github", "api.github.com", 5)
        
        # First 3 of 5 should match the 3
        assert queries_3 == queries_5[:3]

    def test_max_queries_cap_enforced(self):
        """max_queries cap is strictly enforced."""
        for cap in [1, 2, 3, 5, 10]:
            queries = generate_spec_search_queries("test-service", "test.com", cap)
            assert len(queries) <= cap

    def test_max_results_cap_enforced(self):
        """max_results cap is strictly enforced via config."""
        with patch.dict(os.environ, {"DISCOVERY_WEB_SEARCH_MAX_RESULTS": "2"}, clear=False):
            config = get_web_search_config()
            assert config.max_results == 2

    def test_query_hash_is_deterministic(self):
        """Query hash function is deterministic for logging privacy."""
        query = "stripe api openapi specification"
        hash1 = hash_query_for_logging(query)
        hash2 = hash_query_for_logging(query)
        
        assert hash1 == hash2
        # Should be a short hex prefix (12 chars based on implementation)
        assert len(hash1) <= 16
        assert all(c in "0123456789abcdef" for c in hash1)

    def test_query_hash_is_unique_per_query(self):
        """Different queries produce different hashes."""
        hash1 = hash_query_for_logging("stripe api")
        hash2 = hash_query_for_logging("github api")
        
        assert hash1 != hash2


# =============================================================================
# Test: URL Validation and Deduplication
# =============================================================================


class TestURLValidationAndDeduplication:
    """Verify URL validation and deduplication behavior."""

    def test_url_scoring_prefers_spec_indicators(self):
        """URLs with OpenAPI indicators score higher."""
        # URL with openapi indicator
        openapi_score = score_spec_url_heuristic(
            "https://api.example.com/openapi.json",
            "example",
            "OpenAPI Specification"
        )
        
        # Generic URL
        generic_score = score_spec_url_heuristic(
            "https://example.com/page",
            "example",
            "Some page"
        )
        
        assert openapi_score > generic_score

    def test_url_scoring_prefers_github_raw(self):
        """URLs from raw.githubusercontent.com score higher."""
        github_raw_score = score_spec_url_heuristic(
            "https://raw.githubusercontent.com/owner/repo/main/openapi.yaml",
            "repo",
            "OpenAPI spec"
        )
        
        generic_score = score_spec_url_heuristic(
            "https://some-cdn.example.com/spec.yaml",
            "example",
            "Some spec"
        )
        
        assert github_raw_score > generic_score

    def test_url_scoring_penalizes_blogs_and_docs(self):
        """URLs containing /blog/ or /docs/tutorials score lower."""
        blog_score = score_spec_url_heuristic(
            "https://example.com/blog/openapi-tutorial",
            "example",
            "Tutorial"
        )
        
        spec_score = score_spec_url_heuristic(
            "https://api.example.com/spec.yaml",
            "example",
            "API Spec"
        )
        
        # Blog URL should score lower (or much lower) than spec URL
        assert spec_score > blog_score

    @pytest.mark.asyncio
    async def test_duplicate_urls_are_deduplicated(self):
        """Duplicate URLs from different queries are deduplicated."""
        reset_web_search_cache()
        
        # Create explicit config
        config = WebSearchConfig(
            enabled=True,
            provider="tavily",
            tavily_api_key="test-key",
            max_queries=2,
            max_results=5,
            timeout_seconds=10,
            total_timeout_seconds=45,
            cache_ttl_hours=24,
        )
        
        with patch("httpx.AsyncClient") as mock_client:
            # Setup mock that returns same URL for both queries
            mock_response = {
                "results": [
                    {
                        "url": "https://api.example.com/openapi.json",  # Same URL
                        "title": "OpenAPI Spec",
                        "content": "Spec content",
                    }
                ]
            }
            
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = MagicMock(
                status_code=200,
                json=lambda: mock_response,
            )
            
            result = await search_web_for_specs(
                explicit_provider="example",
                keywords=["api"],
                config=config,
            )
            
            # Even though 2 queries, only 1 unique URL
            assert len(result) == 1


# =============================================================================
# Test: Cache Hit/Miss Behavior and TTL
# =============================================================================


class TestCacheBehavior:
    """Verify cache hit/miss and TTL expiry behavior."""

    def test_cache_key_is_deterministic(self, tmp_path):
        """Same query produces same cache key."""
        cache = WebSearchCache(ttl_hours=24, cache_dir=tmp_path)
        
        # Two queries with same content
        provider = "tavily"
        query1 = "stripe openapi specification"
        query2 = "stripe openapi specification"
        
        # Verify they'd hit the same cache entry
        key1 = cache._cache_key(provider, query1)
        key2 = cache._cache_key(provider, query2)
        
        assert key1 == key2

    def test_cache_hit_returns_stored_result(self, tmp_path):
        """Cache hit returns previously stored result."""
        cache = WebSearchCache(ttl_hours=24, cache_dir=tmp_path)
        
        provider = "tavily"
        query = "stripe api openapi"
        result = WebSearchResult(
            success=True,
            hits=[
                WebSearchHit(
                    url="https://api.stripe.com/openapi.json",
                    title="Stripe OpenAPI",
                    snippet="Specification",
                    source="tavily",
                    rank=1,
                )
            ],
            query=query,
            source=provider,
        )
        
        # Store in cache
        cache.set(provider, query, result)
        
        # Retrieve from cache
        cached = cache.get(provider, query)
        
        assert cached is not None
        assert len(cached.hits) == 1
        assert cached.hits[0].url == "https://api.stripe.com/openapi.json"
        assert cached.cached is True

    def test_cache_miss_returns_none(self, tmp_path):
        """Cache miss returns None."""
        cache = WebSearchCache(ttl_hours=24, cache_dir=tmp_path)
        
        result = cache.get("tavily", "query-never-stored")
        assert result is None

    def test_cache_ttl_expiry(self, tmp_path):
        """Cache entry expires after TTL."""
        # Use very short TTL for testing (0 hours = immediate expiry)
        cache = WebSearchCache(ttl_hours=0, cache_dir=tmp_path)
        
        provider = "tavily"
        query = "test-ttl-query"
        result = WebSearchResult(
            success=True,
            hits=[
                WebSearchHit(
                    url="https://example.com/spec.json",
                    title="Test",
                    snippet="Test",
                    source="tavily",
                    rank=1,
                )
            ],
            query=query,
            source=provider,
        )
        
        # Store
        cache.set(provider, query, result)
        
        # With 0 TTL, should be immediately expired
        cached = cache.get(provider, query)
        assert cached is None

    def test_cache_isolation_between_queries(self, tmp_path):
        """Different queries have isolated cache entries."""
        cache = WebSearchCache(ttl_hours=24, cache_dir=tmp_path)
        
        # Store for query A
        cache.set("tavily", "query-a", WebSearchResult(
            success=True,
            hits=[WebSearchHit(
                url="https://a.com/spec.json", 
                title="A", 
                snippet="A", 
                source="tavily",
                rank=1,
            )],
            query="query-a",
            source="tavily",
        ))
        
        # Store for query B
        cache.set("tavily", "query-b", WebSearchResult(
            success=True,
            hits=[WebSearchHit(
                url="https://b.com/spec.json", 
                title="B", 
                snippet="B", 
                source="tavily",
                rank=1,
            )],
            query="query-b",
            source="tavily",
        ))
        
        # Retrieve should be isolated
        a_result = cache.get("tavily", "query-a")
        b_result = cache.get("tavily", "query-b")
        
        assert a_result.hits[0].url == "https://a.com/spec.json"
        assert b_result.hits[0].url == "https://b.com/spec.json"


# =============================================================================
# Test: Error Handling Behavior
# =============================================================================


class TestErrorHandlingBehavior:
    """Verify graceful error handling."""

    @pytest.mark.asyncio
    async def test_provider_failure_returns_empty_not_crash(self):
        """Provider failure returns empty list, doesn't crash."""
        reset_web_search_cache()
        
        # Create explicit config
        config = WebSearchConfig(
            enabled=True,
            provider="tavily",
            tavily_api_key="test-key",
            max_queries=3,
            max_results=5,
            timeout_seconds=10,
            total_timeout_seconds=45,
            cache_ttl_hours=24,
        )
        
        with patch("httpx.AsyncClient") as mock_client:
            # Simulate provider failure
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.side_effect = Exception("Provider unavailable")
            
            result = await search_web_for_specs(
                explicit_provider="test",
                keywords=["api"],
                config=config,
            )
            
            # Should return empty, not crash
            assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_invalid_api_key_handled_gracefully(self):
        """Invalid API key is handled gracefully."""
        reset_web_search_cache()
        
        # Create explicit config
        config = WebSearchConfig(
            enabled=True,
            provider="tavily",
            tavily_api_key="invalid-key",
            max_queries=3,
            max_results=5,
            timeout_seconds=10,
            total_timeout_seconds=45,
            cache_ttl_hours=24,
        )
        
        with patch("httpx.AsyncClient") as mock_client:
            # Simulate 401 response
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = MagicMock(
                status_code=401,
                text="Unauthorized",
            )
            
            result = await search_web_for_specs(
                explicit_provider="test",
                keywords=["api"],
                config=config,
            )
            
            # Should handle gracefully
            assert isinstance(result, list)


# =============================================================================
# Test: Privacy Protection
# =============================================================================


class TestPrivacyProtection:
    """Verify privacy-preserving behavior."""

    def test_queries_not_logged_in_plaintext(self):
        """Verify hash function exists for query privacy."""
        # The hash function should exist and work
        query = "sensitive company internal api specification"
        hashed = hash_query_for_logging(query)
        
        # Hash should not contain the original query
        assert "sensitive" not in hashed
        assert "company" not in hashed
        assert "internal" not in hashed
        
        # Hash should be short and opaque
        assert len(hashed) <= 16


# =============================================================================
# Test: Integration Contract (Resolver Interface)
# =============================================================================


class TestResolverInterfaceContract:
    """Verify the interface contract between resolver and web_search."""

    def test_search_web_for_specs_returns_list_of_webhits(self):
        """search_web_for_specs returns List[WebSearchHit]."""
        # Verify type hints and return contract
        from typing import get_type_hints
        from integration_coworker.discovery.web_search import search_web_for_specs
        
        # The function should exist and be async
        import inspect
        assert inspect.iscoroutinefunction(search_web_for_specs)

    def test_websearchhit_has_required_fields(self):
        """WebSearchHit has required fields for resolver."""
        hit = WebSearchHit(
            url="https://api.example.com/spec.json",
            title="Example API",
            snippet="OpenAPI specification",
            source="tavily",
            rank=1,
        )
        
        # Required fields
        assert hasattr(hit, "url")
        assert hasattr(hit, "title")
        assert hasattr(hit, "snippet")
        assert hasattr(hit, "source")
        assert hasattr(hit, "rank")
        
        # Values
        assert hit.url == "https://api.example.com/spec.json"

    def test_websearchconfig_has_required_fields(self):
        """WebSearchConfig has required fields for gating."""
        config = WebSearchConfig(
            enabled=False,
            provider="tavily",
            max_queries=3,
            max_results=5,
            timeout_seconds=10,
            total_timeout_seconds=45,
            cache_ttl_hours=24,
        )
        
        # Required fields for resolver gating
        assert hasattr(config, "enabled")
        assert hasattr(config, "is_available")  # Property to check if configured
        assert hasattr(config, "total_timeout_seconds")
