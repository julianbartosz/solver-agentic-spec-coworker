"""
APIs.guru Client for Spec Discovery (Slice 1)

Fetches and searches the APIs.guru directory to resolve provider names
to OpenAPI specification URLs.

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md Section 1A:
- Fetch from REST API: https://api.apis.guru/v2/list.json
- Cache with TTL (memory + optional disk)
- Pure ranking function for testability
- Return openapiUrl/swaggerUrl from payload

Reference: https://github.com/APIs-guru/openapi-directory
Note: APIs.guru explicitly recommends using their REST API over GitHub raw URLs.

SECURITY NOTE:
All HTTP fetches MUST go through hardened_fetch() for SSRF protection.
Do not use httpx directly in this module.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from integration_coworker.config import get_settings

logger = logging.getLogger(__name__)

# APIs.guru REST API endpoint
APIS_GURU_LIST_URL = "https://api.apis.guru/v2/list.json"

# Cache configuration (from environment or defaults)
CACHE_TTL_SECONDS = int(os.getenv("DISCOVERY_CACHE_TTL_HOURS", "24")) * 3600
CACHE_DIR = Path(os.getenv("DISCOVERY_CACHE_DIR", "/tmp/integration-coworker-discovery-cache"))

# In-memory cache
_memory_cache: Dict[str, Tuple[float, Any]] = {}


@dataclass
class SpecCandidate:
    """
    A candidate spec from APIs.guru directory.
    
    Attributes:
        provider: Provider domain (e.g., "stripe.com", "openai.com")
        api_name: Human-readable API name
        spec_url: URL to the OpenAPI/Swagger spec
        spec_format: Format version ("openapi_3" or "swagger_2")
        description: API description from directory
        score: Ranking score (higher = better match)
        version: API version string
    """
    
    provider: str
    api_name: str
    spec_url: str
    spec_format: str = "openapi_3"
    description: str = ""
    score: float = 0.0
    version: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "provider": self.provider,
            "api_name": self.api_name,
            "spec_url": self.spec_url,
            "spec_format": self.spec_format,
            "description": self.description,
            "score": self.score,
            "version": self.version,
        }


def _get_cache_path() -> Path:
    """Get path to disk cache file."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / "apis_guru_list.json"


def _load_disk_cache() -> Optional[Dict[str, Any]]:
    """Load cached directory from disk if valid."""
    cache_path = _get_cache_path()
    if not cache_path.exists():
        return None
    
    try:
        stat = cache_path.stat()
        age_seconds = time.time() - stat.st_mtime
        if age_seconds > CACHE_TTL_SECONDS:
            logger.debug(f"Disk cache expired (age={age_seconds:.0f}s > TTL={CACHE_TTL_SECONDS}s)")
            return None
        
        with open(cache_path, "r") as f:
            data = json.load(f)
            logger.debug(f"Loaded APIs.guru directory from disk cache ({len(data)} entries)")
            return data
    except Exception as e:
        logger.warning(f"Failed to load disk cache: {e}")
        return None


def _save_disk_cache(data: Dict[str, Any]) -> None:
    """Save directory to disk cache."""
    try:
        cache_path = _get_cache_path()
        with open(cache_path, "w") as f:
            json.dump(data, f)
        logger.debug(f"Saved APIs.guru directory to disk cache ({len(data)} entries)")
    except Exception as e:
        logger.warning(f"Failed to save disk cache: {e}")


def _get_memory_cache(key: str) -> Optional[Any]:
    """Get value from memory cache if not expired."""
    if key in _memory_cache:
        timestamp, data = _memory_cache[key]
        if time.time() - timestamp < CACHE_TTL_SECONDS:
            return data
        else:
            del _memory_cache[key]
    return None


def _set_memory_cache(key: str, data: Any) -> None:
    """Set value in memory cache with current timestamp."""
    _memory_cache[key] = (time.time(), data)


async def fetch_apis_guru_directory(
    force_refresh: bool = False,
    timeout_seconds: float = 30.0,
) -> Dict[str, Any]:
    """
    Fetch the APIs.guru directory (list of all APIs).
    
    Uses a cascading cache strategy:
    1. Memory cache (fastest)
    2. Disk cache (survives restarts)
    3. Live HTTP fetch via hardened_fetch (SSRF-safe)
    
    On HTTP failure, returns stale cache if available.
    
    Args:
        force_refresh: Bypass cache and fetch fresh data
        timeout_seconds: HTTP request timeout
        
    Returns:
        Dict mapping provider domains to API metadata
        
    Raises:
        RuntimeError: If fetch fails and no cache available
    """
    # Import here to avoid circular imports
    from integration_coworker.discovery.http_client import hardened_fetch
    
    cache_key = "apis_guru_directory"
    
    # Check memory cache first
    if not force_refresh:
        cached = _get_memory_cache(cache_key)
        if cached is not None:
            logger.debug("Using memory-cached APIs.guru directory")
            return cached
        
        # Check disk cache
        cached = _load_disk_cache()
        if cached is not None:
            _set_memory_cache(cache_key, cached)
            return cached
    
    # Fetch from API using hardened_fetch (SSRF-safe)
    try:
        logger.info(f"Fetching APIs.guru directory from {APIS_GURU_LIST_URL}")
        
        # Use larger size limit for directory (it's ~30MB)
        result = await hardened_fetch(
            APIS_GURU_LIST_URL,
            timeout_seconds=timeout_seconds,
            max_bytes=50 * 1024 * 1024,  # 50MB limit for directory
            allow_http=False,  # APIs.guru is HTTPS
        )
        
        if not result.success:
            raise RuntimeError(f"Failed to fetch APIs.guru directory: {result.error}")
        
        data = json.loads(result.content)
        
        # Update caches
        _set_memory_cache(cache_key, data)
        _save_disk_cache(data)
        
        logger.info(f"Fetched APIs.guru directory: {len(data)} providers")
        return data
        
    except Exception as e:
        logger.warning(f"Failed to fetch APIs.guru directory: {e}")
        
        # Try stale cache as fallback
        stale = _load_disk_cache()
        if stale is not None:
            logger.warning("Using stale disk cache after fetch failure")
            _set_memory_cache(cache_key, stale)
            return stale
        
        # Check memory cache even if expired
        if cache_key in _memory_cache:
            _, data = _memory_cache[cache_key]
            logger.warning("Using expired memory cache after fetch failure")
            return data
        
        raise


def _normalize_provider_name(name: str) -> str:
    """Normalize provider name for comparison."""
    # Lowercase, remove common suffixes
    normalized = name.lower().strip()
    for suffix in [".com", ".io", ".org", ".net", ".co", ".ai"]:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
    # Remove common prefixes
    for prefix in ["api.", "www.", "apis."]:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    return normalized


def _calculate_match_score(
    provider_key: str,
    api_info: Dict[str, Any],
    provider_hint: Optional[str],
    keywords: List[str],
) -> float:
    """
    Calculate how well an API matches the search criteria.
    
    Scoring factors:
    - Provider name exact/partial match (0.5 base)
    - Title/description keyword matches (0.3 base)
    - Keyword density in description (0.2 base)
    """
    score = 0.0
    
    # Get API metadata
    versions = api_info.get("versions", {})
    if not versions:
        return 0.0
    
    preferred_version = api_info.get("preferred", list(versions.keys())[0])
    version_info = versions.get(preferred_version, {})
    info = version_info.get("info", {})
    
    title = info.get("title", "").lower()
    description = info.get("description", "").lower()
    
    # Normalize provider key
    provider_normalized = _normalize_provider_name(provider_key)
    
    # Provider name matching
    if provider_hint:
        hint_normalized = _normalize_provider_name(provider_hint)
        
        # Bug #1 Fix: Boost exact matches significantly to override keyword noise
        # Exact match
        if provider_normalized == hint_normalized:
            score += 2.0  # Was 0.8 - Make this the dominant factor
        # Provider contains hint (e.g. "openweathermap" in "openweathermap.com")
        elif hint_normalized in provider_normalized:
             score += 1.5 # Was 0.6
        # Provider normalized contains hint
        elif provider_normalized in hint_normalized:
            score += 1.0 # Was 0.6
        # Title contains provider hint
        elif hint_normalized in title:
            score += 0.8
    
    # Keyword matching
    if keywords:
        matched_keywords = 0
        total_matches = 0
        
        for keyword in keywords:
            kw_lower = keyword.lower()
            
            # Check provider name
            if kw_lower in provider_normalized:
                matched_keywords += 1
                total_matches += 2  # Provider match worth more
            
            # Check title
            if kw_lower in title:
                matched_keywords += 1
                total_matches += 1
            
            # Check description
            if kw_lower in description:
                matched_keywords += 1
                total_matches += 0.5
        
        if matched_keywords > 0:
            # Normalize by number of keywords
            keyword_score = min(total_matches / len(keywords), 1.0) * 0.5
            score += keyword_score
    
    return min(score, 1.0)


def rank_candidates(
    directory: Dict[str, Any],
    provider_hint: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    max_results: int = 10,
) -> List[SpecCandidate]:
    """
    Rank API candidates from the directory based on search criteria.
    
    Pure function for testability - no HTTP calls.
    
    Args:
        directory: APIs.guru directory dict (from fetch_apis_guru_directory)
        provider_hint: Provider name hint (e.g., "stripe", "openai")
        keywords: Domain keywords to match (e.g., ["payment", "subscription"])
        max_results: Maximum candidates to return
        
    Returns:
        List of SpecCandidate sorted by score (highest first)
    """
    keywords = keywords or []
    candidates: List[SpecCandidate] = []
    
    for provider_key, api_info in directory.items():
        score = _calculate_match_score(provider_key, api_info, provider_hint, keywords)
        
        # Skip zero-score candidates
        if score < 0.1:
            continue
        
        # Extract spec URL from preferred version
        versions = api_info.get("versions", {})
        if not versions:
            continue
        
        preferred_version = api_info.get("preferred", list(versions.keys())[0])
        version_info = versions.get(preferred_version, {})
        
        # Prefer openapiUrl over swaggerUrl (3.x vs 2.0)
        spec_url = version_info.get("openapiUrl") or version_info.get("swaggerUrl")
        if not spec_url:
            continue
        
        # Determine spec format
        spec_format = "openapi_3" if version_info.get("openapiUrl") else "swagger_2"
        
        # Get metadata
        info = version_info.get("info", {})
        
        candidate = SpecCandidate(
            provider=provider_key,
            api_name=info.get("title", provider_key),
            spec_url=spec_url,
            spec_format=spec_format,
            description=info.get("description", "")[:500],  # Bounded
            score=score,
            version=preferred_version,
        )
        candidates.append(candidate)
    
    # Sort by score descending
    candidates.sort(key=lambda c: c.score, reverse=True)
    
    # Return top N
    return candidates[:max_results]


async def search_apis_guru(
    provider_hint: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    max_results: int = 10,
    force_refresh: bool = False,
) -> List[SpecCandidate]:
    """
    Search APIs.guru directory for matching specs.
    
    Args:
        provider_hint: Provider name hint (e.g., "stripe", "openai")
        keywords: Domain keywords to match
        max_results: Maximum candidates to return
        force_refresh: Bypass cache
        
    Returns:
        List of SpecCandidate sorted by relevance
        
    Raises:
        httpx.HTTPError: If fetch fails and no cache available
    """
    directory = await fetch_apis_guru_directory(force_refresh=force_refresh)
    return rank_candidates(directory, provider_hint, keywords, max_results)


def search_apis_guru_sync(
    provider_hint: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    max_results: int = 10,
    force_refresh: bool = False,
) -> List[SpecCandidate]:
    """
    Synchronous wrapper for search_apis_guru.
    
    For use in sync contexts (e.g., tests, CLI).
    """
    return asyncio.run(search_apis_guru(provider_hint, keywords, max_results, force_refresh))
