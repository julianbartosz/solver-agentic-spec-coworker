"""
Web Search Providers (Slice 4)

Provider implementations for Tavily and SerpApi.

SECURITY CONTRACT:
    - All providers MUST use hardened HTTP settings (trust_env=False)
    - All providers MUST respect timeout and result limits
    - All providers MUST return UNTRUSTED URLs (caller validates)
    - API keys are NEVER logged or exposed

PROVIDER PREFERENCE (per plan):
    - Tavily: PREFERRED - purpose-built for AI agents, cleaner API
    - SerpApi: FALLBACK ONLY - legal risk (Google lawsuit), explicit opt-in

Extracted from web_search.py for maintainability.
"""

import logging
import os
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from integration_coworker.discovery.http_client import (
    get_hardened_timeout,
    get_hardened_limits,
)
from integration_coworker.discovery.web_search_types import (
    WebSearchHit,
    WebSearchResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Retry Configuration (Hardening)
# =============================================================================

# Maximum retry attempts for transient errors
MAX_RETRIES = 2

# Base delay for exponential backoff (seconds)
RETRY_BASE_DELAY = 0.5

# Maximum jitter to add (seconds)
RETRY_MAX_JITTER = 0.3

# Status codes that should trigger retry
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Status codes that should NOT retry (client errors except 429)
NON_RETRYABLE_4XX = frozenset({400, 401, 403, 404, 405, 410, 422})


def _should_retry(status_code: int, attempt: int) -> bool:
    """
    Determine if a request should be retried.
    
    Retry policy (per hardening plan):
    - Retry on 429 (rate limit) and 5xx (server errors)
    - Do NOT retry on 4xx except 429 (client errors are permanent)
    - Bounded retries (MAX_RETRIES)
    """
    if attempt >= MAX_RETRIES:
        return False
    if status_code in NON_RETRYABLE_4XX:
        return False
    return status_code in RETRYABLE_STATUS_CODES


def _get_retry_delay(attempt: int) -> float:
    """
    Calculate retry delay with exponential backoff and jitter.
    
    Formula: base_delay * (2 ^ attempt) + random_jitter
    """
    delay = RETRY_BASE_DELAY * (2 ** attempt)
    jitter = random.uniform(0, RETRY_MAX_JITTER)
    return delay + jitter


# =============================================================================
# Provider Interface
# =============================================================================

class WebSearchProvider(ABC):
    """
    Abstract base class for web search providers.
    
    All providers must:
    1. Use hardened HTTP settings (trust_env=False, explicit timeouts)
    2. Return WebSearchResult with untrusted URLs
    3. Respect timeout and result limits
    4. Never log or expose API keys
    5. Implement retry with bounded attempts
    """
    
    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        timeout_seconds: int = 10,
    ) -> WebSearchResult:
        """
        Execute a search query.
        
        Args:
            query: Search query string
            max_results: Maximum results to return
            timeout_seconds: Per-request timeout
            
        Returns:
            WebSearchResult with hits or error
        """
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging and metrics."""
        pass
    
    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Check if provider has required configuration (API key)."""
        pass


# =============================================================================
# Tavily Provider (PREFERRED)
# =============================================================================

class TavilySearchProvider(WebSearchProvider):
    """
    Tavily search provider implementation.
    
    Tavily is purpose-built for AI agents and provides clean, structured results.
    It is the PREFERRED provider due to:
    - AI-optimized responses
    - Domain include/exclude support
    - Higher reliability for technical queries
    - No legal concerns (unlike SerpApi)
    
    API Reference: https://docs.tavily.com/documentation/api-reference/endpoint/search
    
    Documented Parameters (per plan - use ONLY these):
    - query: Search query string
    - search_depth: "basic" (default) or "advanced"
    - topic: "general" (default) or "news"
    - time_range: Optional time filter
    - max_results: 1-10 (default: 5)
    - include_raw_content: Boolean (default: false)
    
    Security:
    - All HTTP uses hardened settings (trust_env=False)
    - API key is never logged
    - Results are untrusted - URLs must be validated
    """
    
    ENDPOINT = "https://api.tavily.com/search"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Tavily provider.
        
        Args:
            api_key: Tavily API key (falls back to TAVILY_API_KEY env var)
        """
        self._api_key = api_key or os.getenv("TAVILY_API_KEY")
    
    @property
    def name(self) -> str:
        return "tavily"
    
    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)
    
    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        timeout_seconds: int = 10,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> WebSearchResult:
        """
        Search using Tavily API.
        
        Args:
            query: Search query
            max_results: Maximum results to return (1-10)
            timeout_seconds: Request timeout
            include_domains: Optional domain allowlist
            exclude_domains: Optional domain blocklist
            
        Returns:
            WebSearchResult with hits
        """
        if not self._api_key:
            return WebSearchResult(
                success=False,
                query=query,
                source=self.name,
                error="Tavily API key not configured",
            )
        
        # Build request payload with ONLY documented parameters
        payload: Dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "search_depth": "basic",  # Conservative default
            "max_results": min(max_results, 10),  # Tavily max is 10
            "include_raw_content": False,  # Don't need full content
        }
        
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains
        
        # Execute with retry
        last_error: Optional[str] = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                result = await self._post_search(payload, timeout_seconds)
                if result.success:
                    return result
                
                # Check if we should retry
                # For Tavily, we don't have status codes in WebSearchResult
                # so we retry on any transient-looking error
                if attempt < MAX_RETRIES and "timeout" in (result.error or "").lower():
                    delay = _get_retry_delay(attempt)
                    logger.debug(f"Tavily retry {attempt + 1} after {delay:.2f}s")
                    await _async_sleep(delay)
                    continue
                
                return result
                
            except Exception as e:
                last_error = str(e)[:200]
                if attempt < MAX_RETRIES:
                    delay = _get_retry_delay(attempt)
                    logger.debug(f"Tavily retry {attempt + 1} after error: {last_error[:50]}")
                    await _async_sleep(delay)
                    continue
                break
        
        return WebSearchResult(
            success=False,
            query=query,
            source=self.name,
            error=f"Search failed after {MAX_RETRIES + 1} attempts: {last_error}",
        )
    
    async def _post_search(
        self, 
        payload: Dict[str, Any], 
        timeout_seconds: int
    ) -> WebSearchResult:
        """
        Execute POST request to Tavily API.
        
        Uses hardened HTTP settings (trust_env=False, explicit timeouts).
        """
        query = payload.get("query", "")
        
        try:
            # SECURITY: trust_env=False blocks proxy environment variables
            async with httpx.AsyncClient(
                timeout=get_hardened_timeout(timeout_seconds),
                limits=get_hardened_limits(),
                trust_env=False,  # CRITICAL: Block proxy env vars
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    self.ENDPOINT,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                
                if response.status_code != 200:
                    return WebSearchResult(
                        success=False,
                        query=query,
                        source=self.name,
                        error=f"HTTP {response.status_code}",
                    )
                
                data = response.json()
                
        except httpx.TimeoutException:
            return WebSearchResult(
                success=False,
                query=query,
                source=self.name,
                error="Request timeout",
            )
        except Exception as e:
            return WebSearchResult(
                success=False,
                query=query,
                source=self.name,
                error=f"Request error: {str(e)[:100]}",
            )
        
        # Parse results
        hits = []
        results = data.get("results", [])
        
        for i, item in enumerate(results):
            url = item.get("url", "")
            if not url:
                continue
            
            hits.append(WebSearchHit(
                url=url,
                title=item.get("title", ""),
                snippet=item.get("content", "")[:500],  # Truncate long snippets
                source=self.name,
                rank=i + 1,
                raw_score=item.get("score"),
            ))
        
        return WebSearchResult(
            success=True,
            hits=hits,
            query=query,
            source=self.name,
        )


# =============================================================================
# SerpApi Provider (FALLBACK ONLY)
# =============================================================================

class SerpApiSearchProvider(WebSearchProvider):
    """
    SerpApi search provider implementation.
    
    ⚠️  LEGAL WARNING: SerpApi scrapes Google search results, which has faced
    legal challenges. Google sued SerpApi in 2024:
    https://www.theverge.com/news/848365/google-scraper-lawsuit-serpapi
    
    This provider is kept behind stricter flags and is NOT the default.
    USE TAVILY WHEN POSSIBLE.
    
    To use SerpApi, you must:
    1. Set SERPAPI_API_KEY
    2. Set DISCOVERY_WEB_SEARCH_PROVIDER=serpapi (explicit opt-in)
    
    API Reference: https://serpapi.com/search-api
    
    Security:
    - All HTTP uses hardened settings (trust_env=False)
    - API key is never logged
    - Results are untrusted - URLs must be validated
    """
    
    ENDPOINT = "https://serpapi.com/search"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize SerpApi provider.
        
        Args:
            api_key: SerpApi API key (falls back to SERPAPI_API_KEY env var)
        """
        self._api_key = api_key or os.getenv("SERPAPI_API_KEY")
    
    @property
    def name(self) -> str:
        return "serpapi"
    
    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)
    
    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        timeout_seconds: int = 10,
    ) -> WebSearchResult:
        """
        Search using SerpApi.
        
        Args:
            query: Search query
            max_results: Maximum results to return
            timeout_seconds: Request timeout
            
        Returns:
            WebSearchResult with hits
        """
        if not self._api_key:
            return WebSearchResult(
                success=False,
                query=query,
                source=self.name,
                error="SerpApi API key not configured",
            )
        
        # Log warning about legal risk (once per search)
        logger.warning(
            "Using SerpApi for web search. Note: SerpApi scrapes Google results "
            "and faces legal challenges. Consider using Tavily instead."
        )
        
        # Build query parameters
        params = {
            "api_key": self._api_key,
            "engine": "google",
            "q": query,
            "num": min(max_results, 10),
            "output": "json",
        }
        
        # URL with params (API key in URL - don't log this!)
        url = f"{self.ENDPOINT}?{urlencode(params)}"
        
        # Execute with retry
        last_error: Optional[str] = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                result = await self._get_search(url, timeout_seconds, query)
                if result.success:
                    return result
                
                # Check if we should retry
                if attempt < MAX_RETRIES and "timeout" in (result.error or "").lower():
                    delay = _get_retry_delay(attempt)
                    logger.debug(f"SerpApi retry {attempt + 1} after {delay:.2f}s")
                    await _async_sleep(delay)
                    continue
                
                return result
                
            except Exception as e:
                last_error = str(e)[:200]
                if attempt < MAX_RETRIES:
                    delay = _get_retry_delay(attempt)
                    logger.debug(f"SerpApi retry {attempt + 1} after error")
                    await _async_sleep(delay)
                    continue
                break
        
        return WebSearchResult(
            success=False,
            query=query,
            source=self.name,
            error=f"Search failed after {MAX_RETRIES + 1} attempts: {last_error}",
        )
    
    async def _get_search(
        self, 
        url: str, 
        timeout_seconds: int,
        query: str,
    ) -> WebSearchResult:
        """
        Execute GET request to SerpApi.
        
        Uses hardened HTTP settings (trust_env=False, explicit timeouts).
        """
        try:
            # SECURITY: trust_env=False blocks proxy environment variables
            async with httpx.AsyncClient(
                timeout=get_hardened_timeout(timeout_seconds),
                limits=get_hardened_limits(),
                trust_env=False,  # CRITICAL: Block proxy env vars
                follow_redirects=False,
            ) as client:
                response = await client.get(url)
                
                if response.status_code != 200:
                    return WebSearchResult(
                        success=False,
                        query=query,
                        source=self.name,
                        error=f"HTTP {response.status_code}",
                    )
                
                data = response.json()
                
        except httpx.TimeoutException:
            return WebSearchResult(
                success=False,
                query=query,
                source=self.name,
                error="Request timeout",
            )
        except Exception as e:
            return WebSearchResult(
                success=False,
                query=query,
                source=self.name,
                error=f"Request error: {str(e)[:100]}",
            )
        
        # Parse organic results
        hits = []
        organic = data.get("organic_results", [])
        
        for i, item in enumerate(organic):
            url = item.get("link", "")
            if not url:
                continue
            
            hits.append(WebSearchHit(
                url=url,
                title=item.get("title", ""),
                snippet=item.get("snippet", "")[:500],
                source=self.name,
                rank=i + 1,
                raw_score=item.get("position"),
            ))
        
        return WebSearchResult(
            success=True,
            hits=hits,
            query=query,
            source=self.name,
        )


# =============================================================================
# Provider Factory
# =============================================================================

def get_web_search_provider(
    config: "WebSearchConfig"  # Forward ref to avoid circular import
) -> Optional[WebSearchProvider]:
    """
    Get the configured web search provider.
    
    Returns None if web search is disabled or no provider is available.
    
    Provider selection (per hardening plan):
    - "auto" (default): Prefer Tavily, fall back to SerpApi (if explicitly allowed)
    - "tavily": Use Tavily only
    - "serpapi": Use SerpApi only (requires DISCOVERY_WEB_SEARCH_ALLOW_SERPAPI=true)
    
    SerpApi Explicit Opt-In:
        SerpApi scrapes Google results, which Google considers a ToS violation.
        Google has sued SerpApi, creating potential legal exposure.
        To use SerpApi, operators must explicitly set:
            DISCOVERY_WEB_SEARCH_ALLOW_SERPAPI=true
    
    Args:
        config: WebSearchConfig instance
        
    Returns:
        WebSearchProvider instance or None
    """
    provider = config.effective_provider
    
    # Log why SerpApi was blocked if relevant
    if provider is None:
        blocked_reason = config.serpapi_blocked_reason
        if blocked_reason:
            logger.warning(f"SerpApi provider blocked: {blocked_reason}")
    
    if provider == "tavily":
        return TavilySearchProvider(api_key=config.tavily_api_key)
    elif provider == "serpapi":
        return SerpApiSearchProvider(api_key=config.serpapi_api_key)
    
    return None


# =============================================================================
# Utility
# =============================================================================

async def _async_sleep(seconds: float) -> None:
    """Async sleep wrapper for retry delays."""
    import asyncio
    await asyncio.sleep(seconds)


# Import for type hint
from integration_coworker.discovery.web_search_config import WebSearchConfig  # noqa: E402


__all__ = [
    "WebSearchProvider",
    "TavilySearchProvider",
    "SerpApiSearchProvider",
    "get_web_search_provider",
]
