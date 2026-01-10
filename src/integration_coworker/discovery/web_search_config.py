"""
Web Search Configuration (Slice 4)

Configuration dataclass and loader for web search providers.
All settings default to OFF or conservative values - web search is
privacy-sensitive and must be explicitly enabled.

Extracted from web_search.py for maintainability.

SerpApi Explicit Opt-In (Legal Risk Mitigation):
    SerpApi scrapes Google results, which Google considers a ToS violation.
    To use SerpApi, operators must explicitly set:
        DISCOVERY_WEB_SEARCH_ALLOW_SERPAPI=true
    This ensures conscious acknowledgment of the legal risk.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional


def _parse_domain_list(value: Optional[str]) -> Optional[List[str]]:
    """Parse comma-separated domain list from env var."""
    if not value:
        return None
    domains = [d.strip().lower() for d in value.split(",") if d.strip()]
    return domains if domains else None


@dataclass
class WebSearchConfig:
    """
    Configuration for web search providers.
    
    All settings default to OFF or conservative values. Web search is
    privacy-sensitive and must be explicitly enabled.
    
    Environment Variables:
        DISCOVERY_WEB_SEARCH_ENABLED: Master switch (default: false)
        DISCOVERY_WEB_SEARCH_PROVIDER: "tavily", "serpapi", or "auto" (default: auto)
        TAVILY_API_KEY: Tavily API key (preferred provider)
        SERPAPI_API_KEY: SerpApi API key (secondary, stricter flags)
        DISCOVERY_WEB_SEARCH_ALLOW_SERPAPI: Explicit opt-in for SerpApi (default: false)
        DISCOVERY_WEB_SEARCH_MAX_QUERIES: Max queries per resolution (default: 3)
        DISCOVERY_WEB_SEARCH_MAX_RESULTS: Max results per query (default: 5)
        DISCOVERY_WEB_SEARCH_TIMEOUT_SECONDS: Per-query timeout (default: 10)
        DISCOVERY_WEB_SEARCH_TOTAL_TIMEOUT_SECONDS: Total budget across all queries (default: 45)
        DISCOVERY_WEB_SEARCH_CACHE_TTL_HOURS: Cache duration (default: 24)
        DISCOVERY_WEB_SEARCH_CACHE_DIR: Cache directory override (optional, for testing)
        DISCOVERY_WEB_SEARCH_ALLOW_DOMAINS: Allowlist (comma-separated, optional)
        DISCOVERY_WEB_SEARCH_BLOCK_DOMAINS: Blocklist (comma-separated, optional)
    
    SerpApi Legal Risk:
        SerpApi scrapes Google results, which Google considers a ToS violation.
        Google has sued SerpApi, creating potential legal exposure.
        To use SerpApi, operators must explicitly set:
            DISCOVERY_WEB_SEARCH_ALLOW_SERPAPI=true
    """
    
    # Master switch - disabled by default
    enabled: bool = field(
        default_factory=lambda: os.getenv(
            "DISCOVERY_WEB_SEARCH_ENABLED", "false"
        ).lower() in ("true", "1", "yes")
    )
    
    # Provider selection: "tavily", "serpapi", or "auto"
    # Auto picks Tavily if available, then SerpApi
    provider: Literal["tavily", "serpapi", "auto"] = field(
        default_factory=lambda: os.getenv(
            "DISCOVERY_WEB_SEARCH_PROVIDER", "auto"
        ).lower()  # type: ignore
    )
    
    # API keys
    tavily_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("TAVILY_API_KEY")
    )
    serpapi_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("SERPAPI_API_KEY")
    )
    
    # SerpApi explicit opt-in flag (legal risk mitigation)
    # Must be True to use SerpApi, even if SERPAPI_API_KEY is set
    allow_serpapi: bool = field(
        default_factory=lambda: os.getenv(
            "DISCOVERY_WEB_SEARCH_ALLOW_SERPAPI", "false"
        ).lower() in ("true", "1", "yes")
    )
    
    # Query limits
    max_queries: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_WEB_SEARCH_MAX_QUERIES", "3"))
    )
    max_results: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_WEB_SEARCH_MAX_RESULTS", "5"))
    )
    
    # Timeout per query (seconds)
    timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_WEB_SEARCH_TIMEOUT_SECONDS", "10"))
    )
    
    # Total timeout budget across all queries (seconds) - prevents stall attacks
    total_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_WEB_SEARCH_TOTAL_TIMEOUT_SECONDS", "45"))
    )
    
    # Cache settings
    cache_ttl_hours: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_WEB_SEARCH_CACHE_TTL_HOURS", "24"))
    )
    
    # Cache directory override (optional, for testing)
    # If None, uses DISCOVERY_WEB_SEARCH_CACHE_DIR env var or default ~/.cache/...
    cache_dir: Optional[Path] = field(
        default_factory=lambda: Path(d) if (d := os.getenv("DISCOVERY_WEB_SEARCH_CACHE_DIR")) else None
    )
    
    # Domain filtering (optional, for tightening)
    allow_domains: Optional[List[str]] = field(
        default_factory=lambda: _parse_domain_list(
            os.getenv("DISCOVERY_WEB_SEARCH_ALLOW_DOMAINS")
        )
    )
    block_domains: Optional[List[str]] = field(
        default_factory=lambda: _parse_domain_list(
            os.getenv("DISCOVERY_WEB_SEARCH_BLOCK_DOMAINS")
        )
    )
    
    @property
    def is_available(self) -> bool:
        """Check if web search is enabled and a provider is configured."""
        if not self.enabled:
            return False
        # Tavily is always available if key is set
        if self.tavily_api_key:
            return True
        # SerpApi requires explicit opt-in
        if self.serpapi_api_key and self.allow_serpapi:
            return True
        return False
    
    @property
    def effective_provider(self) -> Optional[str]:
        """
        Get the effective provider to use based on config and available keys.
        
        SerpApi requires allow_serpapi=True in addition to the API key.
        This is for legal risk mitigation (Google lawsuit against SerpApi).
        """
        if not self.enabled:
            return None
        
        if self.provider == "tavily":
            return "tavily" if self.tavily_api_key else None
        elif self.provider == "serpapi":
            # SerpApi requires explicit opt-in
            if self.serpapi_api_key and self.allow_serpapi:
                return "serpapi"
            return None
        else:  # auto
            # Prefer Tavily
            if self.tavily_api_key:
                return "tavily"
            # SerpApi requires explicit opt-in even in auto mode
            if self.serpapi_api_key and self.allow_serpapi:
                return "serpapi"
            return None
    
    @property
    def serpapi_blocked_reason(self) -> Optional[str]:
        """Return reason SerpApi is blocked, or None if not blocked."""
        if self.provider not in ("serpapi", "auto"):
            return None
        if not self.serpapi_api_key:
            return None
        if not self.allow_serpapi:
            return (
                "SerpApi requires explicit opt-in. Set DISCOVERY_WEB_SEARCH_ALLOW_SERPAPI=true "
                "to acknowledge the legal risk (Google lawsuit against SerpApi for ToS violation)."
            )
        return None


def get_web_search_config() -> WebSearchConfig:
    """Get web search configuration from environment."""
    return WebSearchConfig()


__all__ = [
    "WebSearchConfig",
    "get_web_search_config",
]
