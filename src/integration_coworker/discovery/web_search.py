"""
Web Search Providers for Spec Discovery (Slice 4)

Optional web search integration using Tavily or SerpApi to find OpenAPI specs
when local catalog and APIs.guru do not return high-confidence candidates.

PRIVACY WARNING:
    This feature sends task-derived queries to third-party search services.
    It is DISABLED by default and must be explicitly enabled via:
    - DISCOVERY_WEB_SEARCH_ENABLED=true
    - TAVILY_API_KEY or SERPAPI_API_KEY

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md Slice 4:
- All outbound HTTP goes through hardened HTTP settings (SSRF protection)
- Results are untrusted suggestions - URLs must still pass validation
- Query count and result caps enforced
- Simple disk cache to avoid repeated API calls
- Total timeout budget prevents stall attacks

Security Contract:
    - NEVER trust URLs from search results directly
    - ALL URLs must be validated via validate_spec_url() before use
    - Queries are rate-limited and time-bounded
    - API keys are never logged or exposed
    - Raw queries are never logged (use hash_query_for_logging)

Legal Notice (SerpApi):
    SerpApi scrapes search engine results, which has faced legal scrutiny.
    See: https://www.theverge.com/news/848365/google-scraper-lawsuit-serpapi
    SerpApi is kept behind stricter flags and is NOT the default provider.

Module Structure (refactored for maintainability):
    - web_search_config.py: Configuration dataclass and loader
    - web_search_types.py: WebSearchHit, WebSearchResult data classes
    - web_search_cache.py: Disk cache for search results
    - web_search_providers.py: Tavily and SerpApi provider implementations
    - web_search_scoring.py: URL scoring heuristics and query generation
    - web_search.py: Orchestration and re-exports (this file)
"""

import logging
import time
from typing import List, Optional

# Re-export from submodules for backwards compatibility
from integration_coworker.discovery.web_search_config import (
    WebSearchConfig,
    get_web_search_config,
)
from integration_coworker.discovery.web_search_types import (
    WebSearchHit,
    WebSearchResult,
)
from integration_coworker.discovery.web_search_cache import (
    WebSearchCache,
    get_web_search_cache,
    reset_web_search_cache,
)
from integration_coworker.discovery.web_search_providers import (
    WebSearchProvider,
    TavilySearchProvider,
    SerpApiSearchProvider,
    get_web_search_provider,
)
from integration_coworker.discovery.web_search_scoring import (
    generate_spec_search_queries,
    score_spec_url_heuristic,
    hash_query_for_logging,
)

logger = logging.getLogger(__name__)


def _is_domain_allowed(url: str, config: WebSearchConfig) -> bool:
    """
    Check if URL's domain passes domain filters.

    Block takes precedence over allow.
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = (parsed.hostname or "").lower()
    except Exception:
        return False

    # Check blocklist first (block takes precedence)
    if config.block_domains:
        for blocked in config.block_domains:
            if blocked in domain:
                return False

    # Check allowlist (if set, URL must match)
    if config.allow_domains:
        for allowed in config.allow_domains:
            if allowed in domain:
                return True
        return False  # Allowlist set but no match

    return True


async def search_web_for_specs(
    explicit_provider: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    config: Optional[WebSearchConfig] = None,
) -> List[WebSearchHit]:
    """
    Search web for OpenAPI specs using configured provider.

    This is the main entry point for web search. It:
    1. Checks if web search is enabled and configured
    2. Generates queries based on provider/keywords
    3. Executes searches with caching and total timeout budget
    4. Returns aggregated results (deduplicated by URL)

    SECURITY:
    - All returned URLs are UNTRUSTED - must be validated via validate_spec_url()
    - Queries are never logged directly (use hash_query_for_logging)
    - Total timeout budget prevents stall attacks

    Args:
        explicit_provider: Known provider name (e.g., "stripe")
        keywords: Task keywords
        config: Optional config override

    Returns:
        List of WebSearchHit (may be empty if disabled/unconfigured)
    """
    if config is None:
        config = get_web_search_config()

    if not config.is_available:
        logger.debug("Web search not available (disabled or no API key)")
        return []

    provider = get_web_search_provider(config)
    if not provider:
        return []

    cache = get_web_search_cache(ttl_hours=config.cache_ttl_hours)

    # Generate queries
    queries = generate_spec_search_queries(
        explicit_provider=explicit_provider,
        keywords=keywords,
        max_queries=config.max_queries,
    )

    if not queries:
        return []

    logger.info(f"Web search: {len(queries)} queries via {provider.name}")

    # Total timeout budget across all queries (prevents stall attacks)
    deadline = time.monotonic() + config.total_timeout_seconds

    def remaining_time() -> float:
        return max(0.0, deadline - time.monotonic())

    # Execute queries
    all_hits: List[WebSearchHit] = []
    seen_urls: set = set()

    for query in queries:
        # Check total budget
        if remaining_time() <= 0:
            logger.warning("Web search: total timeout budget exhausted")
            break

        # Check cache first (privacy: log hash, not query)
        query_hash = hash_query_for_logging(query)
        cached = cache.get(provider.name, query)
        if cached:
            logger.debug(f"Cache hit for query hash {query_hash}")
            for hit in cached.hits:
                if hit.url not in seen_urls:
                    all_hits.append(hit)
                    seen_urls.add(hit.url)
            continue

        # Calculate per-query timeout (remaining budget capped at config value)
        per_query_timeout = min(config.timeout_seconds, int(remaining_time()))
        if per_query_timeout <= 0:
            logger.warning("Web search: insufficient budget for next query")
            break

        # Execute search
        logger.debug(f"Executing query hash {query_hash} (timeout: {per_query_timeout}s)")
        result = await provider.search(
            query,
            max_results=config.max_results,
            timeout_seconds=per_query_timeout,
        )

        # Cache result (even failures, to avoid hammering on errors)
        cache.set(provider.name, query, result)

        # Collect hits
        for hit in result.hits:
            if hit.url not in seen_urls:
                # Apply domain filtering
                if _is_domain_allowed(hit.url, config):
                    all_hits.append(hit)
                    seen_urls.add(hit.url)

    logger.info(f"Web search returned {len(all_hits)} unique hits")
    return all_hits


__all__ = [
    # Config
    "WebSearchConfig",
    "get_web_search_config",
    # Data types
    "WebSearchHit",
    "WebSearchResult",
    # Cache
    "WebSearchCache",
    "get_web_search_cache",
    "reset_web_search_cache",
    # Provider interface
    "WebSearchProvider",
    # Providers
    "TavilySearchProvider",
    "SerpApiSearchProvider",
    "get_web_search_provider",
    # High-level API
    "generate_spec_search_queries",
    "search_web_for_specs",
    "score_spec_url_heuristic",
    # Privacy helpers
    "hash_query_for_logging",
]
