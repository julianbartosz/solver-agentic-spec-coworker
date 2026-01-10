"""
Spec Auto-Discovery Module (Slice 2/3/4)

Enables natural language task descriptions to resolve to OpenAPI specs
via local catalog, APIs.guru registry, and optional web search fallback.

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md:
- Slice 1: APIs.guru registry only (completed)
- Slice 2: Local-first catalog with APIs.guru fallback (completed)
- Slice 3: LLM structured intent extraction (completed)
- Slice 4: Optional web search via Tavily/SerpApi (current)
- Strict OpenAPI validation (openapi-spec-validator)
- Feature flag controlled (DISCOVERY_ENABLED, DISCOVERY_WEB_SEARCH_ENABLED)

Usage:
    from integration_coworker.discovery import resolve_spec_from_task
    
    result = await resolve_spec_from_task("Process payment with Stripe")
    if result.spec_url:
        spec_refs = [result.spec_url]

Web Search (Slice 4):
    Web search is DISABLED by default. To enable:
    - DISCOVERY_WEB_SEARCH_ENABLED=true
    - TAVILY_API_KEY=... (preferred)
    - SERPAPI_API_KEY=... (alternative, stricter flags)
    
    PRIVACY: Web search sends task-derived queries to third parties.
"""

import logging

from integration_coworker.discovery.intent import (
    IntentAnalysis,
    analyze_discovery_intent,
)
from integration_coworker.discovery.apis_guru import (
    SpecCandidate,
    search_apis_guru,
    rank_candidates,
)
from integration_coworker.discovery.validator import (
    ValidationResult,
    validate_spec_url,
)
from integration_coworker.discovery.resolver import (
    DiscoveryResult,
    resolve_spec_from_task,
)
from integration_coworker.discovery.http_client import (
    FetchResult,
    hardened_fetch,
    hardened_fetch_sync,
)

logger = logging.getLogger(__name__)

# Optional: Local catalog (requires Postgres)
try:
    from integration_coworker.discovery.catalog import (
        CatalogMatch,
        search_local_catalog,
        is_catalog_available,
        get_provider_by_domain,
    )
    _CATALOG_AVAILABLE = True
except ImportError:
    _CATALOG_AVAILABLE = False
    CatalogMatch = None
    search_local_catalog = None
    is_catalog_available = lambda: False
    get_provider_by_domain = None

# Optional: Web search providers (Slice 4)
try:
    from integration_coworker.discovery.web_search import (
        WebSearchConfig,
        WebSearchHit,
        WebSearchResult,
        WebSearchProvider,
        TavilySearchProvider,
        SerpApiSearchProvider,
        get_web_search_config,
        get_web_search_provider,
        search_web_for_specs,
    )
    _WEB_SEARCH_AVAILABLE = True
except ImportError:
    _WEB_SEARCH_AVAILABLE = False
    WebSearchConfig = None
    WebSearchHit = None
    WebSearchResult = None
    WebSearchProvider = None
    TavilySearchProvider = None
    SerpApiSearchProvider = None
    get_web_search_config = None
    get_web_search_provider = None
    search_web_for_specs = None


class DiscoveryConfigurationError(Exception):
    """Raised when discovery is enabled but required dependencies are missing."""
    pass


def check_discovery_requirements() -> None:
    """
    Check that all required dependencies are available for discovery.
    
    When DISCOVERY_ENABLED=true, this validates:
    1. openapi-spec-validator is installed (unless DISCOVERY_ALLOW_WEAK_VALIDATION=true)
    
    Raises:
        DiscoveryConfigurationError: If required dependencies are missing
    """
    from integration_coworker.config import get_settings
    
    settings = get_settings()
    
    if not settings.discovery_enabled:
        return  # Discovery disabled, no validation needed
    
    # Check for openapi-spec-validator
    try:
        import openapi_spec_validator  # noqa: F401
    except ImportError:
        if not settings.discovery_allow_weak_validation:
            raise DiscoveryConfigurationError(
                "DISCOVERY_ENABLED=true but openapi-spec-validator is not installed. "
                "Install with: pip install 'solver-agentic-spec-coworker[discovery]' "
                "Or set DISCOVERY_ALLOW_WEAK_VALIDATION=true to proceed with structural checks only."
            )
        else:
            logger.warning(
                "openapi-spec-validator not installed. "
                "Proceeding with weak validation (DISCOVERY_ALLOW_WEAK_VALIDATION=true). "
                "This may allow invalid specs through."
            )


__all__ = [
    # Intent
    "IntentAnalysis",
    "analyze_discovery_intent",
    # APIs.guru
    "SpecCandidate",
    "search_apis_guru",
    "rank_candidates",
    # Validator
    "ValidationResult",
    "validate_spec_url",
    # HTTP Client (centralized, hardened)
    "FetchResult",
    "hardened_fetch",
    "hardened_fetch_sync",
    # Resolver (main entry point)
    "DiscoveryResult",
    "resolve_spec_from_task",
    # Local catalog (optional)
    "CatalogMatch",
    "search_local_catalog",
    "is_catalog_available",
    "get_provider_by_domain",
    # Web search (optional, Slice 4)
    "WebSearchConfig",
    "WebSearchHit",
    "WebSearchResult",
    "WebSearchProvider",
    "TavilySearchProvider",
    "SerpApiSearchProvider",
    "get_web_search_config",
    "get_web_search_provider",
    "search_web_for_specs",
    # Configuration
    "DiscoveryConfigurationError",
    "check_discovery_requirements",
]
