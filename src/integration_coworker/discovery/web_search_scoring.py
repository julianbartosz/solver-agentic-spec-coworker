"""
Web Search Scoring and Query Generation (Slice 4)

Heuristic scoring for potential spec URLs and deterministic query generation.
No LLM required - pure rule-based logic.

Extracted from web_search.py for maintainability.
"""

import hashlib
import re
from typing import List, Optional


def generate_spec_search_queries(
    explicit_provider: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    max_queries: int = 3,
) -> List[str]:
    """
    Generate deterministic search queries for OpenAPI spec discovery.
    
    Query patterns:
    - If explicit_provider: "<provider> openapi spec", "<provider> swagger openapi yaml"
    - If keywords only: "openapi spec <keyword1> <keyword2>"
    
    Determinism: Same inputs ALWAYS produce same outputs for reproducibility.
    
    Args:
        explicit_provider: Known API provider (e.g., "stripe", "openai")
        keywords: Action/domain keywords from intent
        max_queries: Maximum number of queries to generate
        
    Returns:
        List of search queries (capped at max_queries)
    """
    queries = []
    
    if explicit_provider:
        provider = explicit_provider.lower().strip()
        # Remove .com, .io etc for cleaner queries
        provider = re.sub(r'\.(com|io|org|net|api)$', '', provider)
        
        queries.extend([
            f"{provider} openapi spec",
            f"{provider} swagger openapi yaml json",
            f"site:github.com {provider} openapi",
        ])
    elif keywords:
        # Keyword-only queries
        kw_str = " ".join(keywords[:3])  # Cap keywords
        queries.extend([
            f"openapi spec {kw_str}",
            f"{kw_str} api swagger openapi",
        ])
    else:
        # No signals - nothing to search
        return []
    
    return queries[:max_queries]


def score_spec_url_heuristic(
    url: str,
    title: str,
    snippet: str,
    provider_hint: Optional[str] = None,
) -> float:
    """
    Score a potential spec URL based on heuristics.
    
    No LLM needed - pure rule-based scoring.
    
    Scoring rules:
    - +0.3 if URL ends with .yaml/.yml/.json
    - +0.2 if host is raw.githubusercontent.com
    - +0.2 if "openapi" or "swagger" in URL path
    - +0.15 if snippet contains "openapi" or "swagger"
    - +0.1 if domain matches provider hint
    - -0.2 if URL looks like documentation page
    - -0.1 if URL is very long (likely deep navigation)
    
    Args:
        url: The URL to score
        title: Search result title
        snippet: Search result snippet
        provider_hint: Expected provider domain
        
    Returns:
        Score between 0.0 and 1.0
    """
    score = 0.5  # Base score
    url_lower = url.lower()
    
    # Positive signals
    if re.search(r'\.(yaml|yml|json)(\?|#|$)', url_lower):
        score += 0.3
    
    if "raw.githubusercontent.com" in url_lower:
        score += 0.2
    
    if re.search(r'(openapi|swagger)', url_lower):
        score += 0.2
    
    snippet_lower = snippet.lower()
    if "openapi" in snippet_lower or "swagger" in snippet_lower:
        score += 0.15
    
    if provider_hint:
        provider_clean = provider_hint.lower().replace(".com", "").replace(".io", "")
        if provider_clean in url_lower:
            score += 0.1
    
    # Negative signals
    if "/docs/" in url_lower or "/documentation/" in url_lower:
        score -= 0.1  # Might be docs page, not raw spec
    
    if len(url) > 200:
        score -= 0.1  # Very long URLs are usually not direct spec links
    
    # Clamp to [0, 1]
    return max(0.0, min(1.0, score))


def hash_query_for_logging(query: str) -> str:
    """
    Hash a query for safe logging (privacy protection).
    
    NEVER log raw queries to avoid leaking user task descriptions to logs.
    Use this function to create an opaque identifier for debugging.
    
    Args:
        query: The raw query string
        
    Returns:
        SHA-256 hash prefix (12 chars) for identification
    """
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "generate_spec_search_queries",
    "score_spec_url_heuristic",
    "hash_query_for_logging",
]
