"""
Web Search Cache (Slice 4)

Simple disk-based cache for web search results.
Keyed by (provider, query) hash. TTL enforced on read.

Extracted from web_search.py for maintainability.

PRIVACY NOTE:
    Query strings are hashed (SHA-256) before being used as filenames.
    Full queries are stored in the cache JSON for debugging, but the
    filename reveals nothing about the search terms.
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

from integration_coworker.discovery.web_search_types import (
    WebSearchHit,
    WebSearchResult,
)

logger = logging.getLogger(__name__)


class WebSearchCache:
    """
    Simple disk-based cache for web search results.
    
    Keyed by (provider, query) hash. TTL enforced on read.
    Cache directory: configurable via cache_dir parameter or env var.
    
    Default: ~/.cache/integration_coworker/web_search/
    Override: DISCOVERY_WEB_SEARCH_CACHE_DIR env var or cache_dir parameter
    
    This avoids repeated API calls for identical queries within the TTL window.
    
    Security:
        - Filenames are hashes (no query leakage in filenames)
        - Cache directory permissions should be 0700 (user-only)
    
    Testability:
        - Inject cache_dir=tmp_path in tests to avoid cross-test pollution
    """
    
    def __init__(self, ttl_hours: int = 24, cache_dir: Optional[Path] = None):
        self.ttl_hours = ttl_hours
        
        # Priority: explicit param > env var > default
        if cache_dir is not None:
            self.cache_dir = Path(cache_dir)
        else:
            import os
            env_dir = os.getenv("DISCOVERY_WEB_SEARCH_CACHE_DIR")
            if env_dir:
                self.cache_dir = Path(env_dir)
            else:
                self.cache_dir = Path.home() / ".cache" / "integration_coworker" / "web_search"
        
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _cache_key(self, provider: str, query: str) -> str:
        """Generate cache key from provider and query (SHA-256 hash)."""
        content = f"{provider}:{query}".encode("utf-8")
        return hashlib.sha256(content).hexdigest()[:32]
    
    def _cache_path(self, key: str) -> Path:
        """Get cache file path for key."""
        return self.cache_dir / f"{key}.json"
    
    def get(self, provider: str, query: str) -> Optional[WebSearchResult]:
        """
        Get cached result if valid.
        
        Returns None if not cached or expired.
        """
        key = self._cache_key(provider, query)
        path = self._cache_path(key)
        
        if not path.exists():
            return None
        
        try:
            data = json.loads(path.read_text())
            
            # Check TTL
            cached_at = data.get("cached_at", 0)
            age_hours = (time.time() - cached_at) / 3600
            
            if age_hours > self.ttl_hours:
                # Expired
                path.unlink(missing_ok=True)
                return None
            
            # Reconstruct WebSearchResult
            hits = [
                WebSearchHit(
                    url=h["url"],
                    title=h["title"],
                    snippet=h["snippet"],
                    source=h["source"],
                    rank=h["rank"],
                    raw_score=h.get("raw_score"),
                )
                for h in data.get("hits", [])
            ]
            
            return WebSearchResult(
                success=data.get("success", False),
                hits=hits,
                query=data.get("query", query),
                source=data.get("source", provider),
                error=data.get("error"),
                cached=True,
            )
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Cache read error for {key}: {e}")
            path.unlink(missing_ok=True)
            return None
    
    def set(self, provider: str, query: str, result: WebSearchResult) -> None:
        """Cache a search result."""
        key = self._cache_key(provider, query)
        path = self._cache_path(key)
        
        try:
            data = {
                "cached_at": time.time(),
                "success": result.success,
                "hits": [h.to_dict() for h in result.hits],
                "query": result.query,
                "source": result.source,
                "error": result.error,
            }
            path.write_text(json.dumps(data, indent=2))
        except (OSError, TypeError) as e:
            logger.warning(f"Cache write error for key hash {key[:8]}...: {e}")
    
    def clear(self) -> int:
        """Clear all cached results. Returns count of files removed."""
        count = 0
        for path in self.cache_dir.glob("*.json"):
            try:
                path.unlink()
                count += 1
            except OSError:
                pass
        return count


# Global cache instance
_web_search_cache: Optional[WebSearchCache] = None


def get_web_search_cache(
    ttl_hours: int = 24,
    cache_dir: Optional[Path] = None,
) -> WebSearchCache:
    """
    Get or create the global web search cache.
    
    Args:
        ttl_hours: Cache TTL in hours (default: 24)
        cache_dir: Optional explicit cache directory (for testing).
                   If provided, a NEW cache instance is returned (not the global).
    
    For testing, pass cache_dir=tmp_path to get a fresh cache that won't
    pollute other tests or persist to ~/.cache.
    """
    global _web_search_cache
    
    # If explicit cache_dir provided, return a NEW instance (not global)
    # This is the hermetic testing path
    if cache_dir is not None:
        return WebSearchCache(ttl_hours=ttl_hours, cache_dir=cache_dir)
    
    # Default path: use/create global singleton
    if _web_search_cache is None:
        _web_search_cache = WebSearchCache(ttl_hours=ttl_hours)
    return _web_search_cache


def reset_web_search_cache() -> None:
    """Reset the global cache (for testing)."""
    global _web_search_cache
    _web_search_cache = None


__all__ = [
    "WebSearchCache",
    "get_web_search_cache",
    "reset_web_search_cache",
]
