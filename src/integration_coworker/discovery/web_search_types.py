"""
Web Search Data Types (Slice 4)

Data classes for web search results.

Extracted from web_search.py for maintainability and to break circular imports.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WebSearchHit:
    """
    A single search result from web search.
    
    URLs from this are UNTRUSTED - they must be validated before use.
    
    Attributes:
        url: The URL from search results (MUST be validated via validate_spec_url!)
        title: Title of the search result
        snippet: Description/snippet from search result
        source: Provider that returned this ("tavily", "serpapi")
        rank: Position in search results (1-indexed)
        raw_score: Provider-specific relevance score (if available)
    """
    url: str
    title: str
    snippet: str
    source: str
    rank: int
    raw_score: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "source": self.source,
            "rank": self.rank,
            "raw_score": self.raw_score,
        }


@dataclass
class WebSearchResult:
    """
    Result of a web search query.
    
    Attributes:
        success: Whether the search completed
        hits: List of search results
        query: The query that was executed
        source: Provider that was used
        error: Error message if failed
        cached: Whether this result came from cache
    """
    success: bool
    hits: List[WebSearchHit] = field(default_factory=list)
    query: str = ""
    source: str = ""
    error: Optional[str] = None
    cached: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "hits": [h.to_dict() for h in self.hits],
            "query": self.query,
            "source": self.source,
            "error": self.error,
            "cached": self.cached,
        }


__all__ = [
    "WebSearchHit",
    "WebSearchResult",
]
