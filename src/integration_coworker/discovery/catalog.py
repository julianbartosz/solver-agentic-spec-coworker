"""
Local Spec Catalog for Discovery (Slice 2)

Provides local-first spec resolution using spec_silver.discovery_* tables.
Falls back to APIs.guru if local catalog misses.

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md:
- Local catalog enables offline discovery
- P50 target: <500ms on warm DB
- Semantic search via pgvector HNSW index
- Curated entries get priority
"""

import asyncio
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional, Tuple

from integration_coworker.discovery.apis_guru import SpecCandidate

logger = logging.getLogger(__name__)

# Maximum results from local catalog
DEFAULT_MAX_RESULTS = 10

# Minimum similarity score for semantic matches
MIN_SEMANTIC_SIMILARITY = 0.3

# Boost factors for curated and quality tiers
CURATED_BOOST = 1.5
PREMIUM_BOOST = 1.3


@dataclass
class CatalogMatch:
    """
    A match from the local spec catalog.
    
    Attributes:
        provider_id: Database ID
        provider_key: Unique key (domain:slug)
        domain: Provider domain
        display_name: Human-readable name
        spec_url: URL to the preferred spec
        spec_format: OpenAPI/Swagger format
        similarity: Semantic similarity score (0-1)
        score: Combined score including quality boosts
        is_curated: Whether this is a curated entry
        quality_tier: Quality tier (premium, standard, untrusted)
    """
    provider_id: int
    provider_key: str
    domain: str
    display_name: str
    spec_url: str
    spec_format: str
    similarity: float
    score: float
    is_curated: bool = False
    quality_tier: str = "standard"
    
    def to_spec_candidate(self) -> SpecCandidate:
        """Convert to SpecCandidate for compatibility with existing resolver."""
        return SpecCandidate(
            provider=self.domain,
            api_name=self.display_name,
            spec_url=self.spec_url,
            spec_format=self.spec_format,
            score=self.score,
            # Note: source is tracked at DiscoveryResult level, not SpecCandidate
        )


@contextmanager
def _get_catalog_connection() -> Generator:
    """
    Get database connection for catalog operations.
    
    Returns a context manager that yields a connection or None.
    """
    try:
        from integration_coworker.persistence.postgres import get_connection
        with get_connection() as conn:
            yield conn
    except Exception as e:
        logger.debug(f"Could not get database connection: {e}")
        yield None


async def search_local_catalog(
    query: str,
    provider_hint: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    include_untrusted: bool = False,
) -> List[CatalogMatch]:
    """
    Search the local spec catalog for matching APIs.
    
    Uses a combination of:
    1. Exact provider/alias matching (highest priority)
    2. Full-text search on provider metadata
    3. Semantic similarity via pgvector embeddings
    
    Args:
        query: Natural language search query
        provider_hint: Optional provider domain/name hint
        keywords: Additional search keywords
        max_results: Maximum results to return
        include_untrusted: Whether to include untrusted tier entries
        
    Returns:
        List of CatalogMatch sorted by score (descending)
        
    Example:
        >>> results = await search_local_catalog("process payment with stripe")
        >>> results[0].display_name
        'Stripe API'
    """
    with _get_catalog_connection() as conn:
        if conn is None:
            logger.debug("No database connection, local catalog unavailable")
            return []
        
        try:
            results = []
            
            # Strategy 1: Exact provider match
            if provider_hint:
                exact_matches = _search_exact_match(conn, provider_hint, max_results)
                results.extend(exact_matches)
            
            # Strategy 2: Full-text search
            search_terms = _build_search_terms(query, provider_hint, keywords)
            if search_terms:
                fts_matches = _search_full_text(conn, search_terms, max_results, include_untrusted)
                results.extend(fts_matches)
            
            # Strategy 3: Semantic search (if embeddings available)
            semantic_matches = await _search_semantic(conn, query, max_results, include_untrusted)
            results.extend(semantic_matches)
            
            # Deduplicate and score
            seen_keys = set()
            unique_results = []
            for match in results:
                if match.provider_key not in seen_keys:
                    seen_keys.add(match.provider_key)
                    # Apply quality boosts
                    boosted_score = _apply_quality_boost(match)
                    match.score = boosted_score
                    unique_results.append(match)
            
            # Sort by score and limit
            unique_results.sort(key=lambda m: m.score, reverse=True)
            return unique_results[:max_results]
            
        except Exception as e:
            logger.warning(f"Local catalog search failed: {e}")
            return []


def _build_search_terms(
    query: str,
    provider_hint: Optional[str],
    keywords: Optional[List[str]],
) -> str:
    """Build full-text search terms from query and hints."""
    terms = []
    
    if provider_hint:
        terms.append(provider_hint)
    
    if keywords:
        terms.extend(keywords[:5])  # Limit keywords
    
    # Extract key terms from query
    query_words = query.lower().split()
    stop_words = {"the", "a", "an", "to", "for", "with", "using", "via", "api", "integration"}
    query_terms = [w for w in query_words if w not in stop_words and len(w) > 2]
    terms.extend(query_terms[:5])
    
    return " | ".join(set(terms))  # OR search


def _search_exact_match(
    conn,
    provider_hint: str,
    max_results: int,
) -> List[CatalogMatch]:
    """Search for exact provider or alias match."""
    matches = []
    hint_lower = provider_hint.lower()
    
    with conn.cursor() as cur:
        # Search providers by domain/slug
        cur.execute("""
            SELECT 
                p.id, p.provider_key, p.domain, p.display_name,
                p.is_curated, p.quality_tier,
                s.spec_url, s.spec_format
            FROM spec_silver.discovery_providers p
            JOIN spec_silver.discovery_specs s ON s.provider_id = p.id AND s.is_preferred = TRUE
            WHERE LOWER(p.domain) = %s 
               OR LOWER(p.slug) = %s
               OR LOWER(p.display_name) LIKE %s
            LIMIT %s
        """, (hint_lower, hint_lower, f"%{hint_lower}%", max_results))
        
        for row in cur.fetchall():
            matches.append(CatalogMatch(
                provider_id=row[0],
                provider_key=row[1],
                domain=row[2],
                display_name=row[3],
                is_curated=row[4],
                quality_tier=row[5],
                spec_url=row[6],
                spec_format=row[7],
                similarity=1.0,  # Exact match
                score=1.0,
            ))
        
        # Also search aliases
        cur.execute("""
            SELECT 
                p.id, p.provider_key, p.domain, p.display_name,
                p.is_curated, p.quality_tier,
                s.spec_url, s.spec_format,
                a.alias
            FROM spec_silver.discovery_aliases a
            JOIN spec_silver.discovery_providers p ON p.id = a.provider_id
            JOIN spec_silver.discovery_specs s ON s.provider_id = p.id AND s.is_preferred = TRUE
            WHERE LOWER(a.alias) = %s OR LOWER(a.alias) LIKE %s
            ORDER BY a.priority DESC
            LIMIT %s
        """, (hint_lower, f"%{hint_lower}%", max_results))
        
        for row in cur.fetchall():
            matches.append(CatalogMatch(
                provider_id=row[0],
                provider_key=row[1],
                domain=row[2],
                display_name=row[3],
                is_curated=row[4],
                quality_tier=row[5],
                spec_url=row[6],
                spec_format=row[7],
                similarity=0.95,  # Alias match slightly lower
                score=0.95,
            ))
    
    return matches


def _search_full_text(
    conn,
    search_terms: str,
    max_results: int,
    include_untrusted: bool,
) -> List[CatalogMatch]:
    """Search using PostgreSQL full-text search."""
    matches = []
    
    quality_filter = "" if include_untrusted else "AND p.quality_tier != 'untrusted'"
    
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT 
                p.id, p.provider_key, p.domain, p.display_name,
                p.is_curated, p.quality_tier,
                s.spec_url, s.spec_format,
                ts_rank(to_tsvector('english', COALESCE(p.search_text, '')), 
                        plainto_tsquery('english', %s)) AS rank
            FROM spec_silver.discovery_providers p
            JOIN spec_silver.discovery_specs s ON s.provider_id = p.id AND s.is_preferred = TRUE
            WHERE to_tsvector('english', COALESCE(p.search_text, '')) @@ plainto_tsquery('english', %s)
            {quality_filter}
            ORDER BY rank DESC
            LIMIT %s
        """, (search_terms, search_terms, max_results))
        
        for row in cur.fetchall():
            rank = float(row[8]) if row[8] else 0.0
            # Normalize rank to 0-1 range (heuristic)
            similarity = min(1.0, rank * 2)
            
            matches.append(CatalogMatch(
                provider_id=row[0],
                provider_key=row[1],
                domain=row[2],
                display_name=row[3],
                is_curated=row[4],
                quality_tier=row[5],
                spec_url=row[6],
                spec_format=row[7],
                similarity=similarity,
                score=similarity,
            ))
    
    return matches


async def _search_semantic(
    conn,
    query: str,
    max_results: int,
    include_untrusted: bool,
) -> List[CatalogMatch]:
    """Search using pgvector semantic similarity."""
    matches = []
    
    # Compute query embedding
    try:
        query_embedding = await _compute_query_embedding(query)
        if not query_embedding:
            return []
    except Exception as e:
        logger.debug(f"Could not compute query embedding: {e}")
        return []
    
    quality_filter = "" if include_untrusted else "AND p.quality_tier != 'untrusted'"
    
    # Get ef_search from config for HNSW accuracy/latency tradeoff
    try:
        from integration_coworker.config import get_settings
        ef_search = get_settings().discovery_hnsw_ef_search
    except Exception:
        ef_search = 40  # pgvector default
    
    with conn.cursor() as cur:
        # Set HNSW ef_search for this query (accuracy vs latency tradeoff)
        # Higher values = better recall, slower queries
        cur.execute(f"SET LOCAL hnsw.ef_search = {ef_search}")
        
        # Use cosine similarity (1 - distance) with HNSW index
        cur.execute(f"""
            SELECT 
                p.id, p.provider_key, p.domain, p.display_name,
                p.is_curated, p.quality_tier,
                s.spec_url, s.spec_format,
                1 - (p.embedding <=> %s::vector) AS similarity
            FROM spec_silver.discovery_providers p
            JOIN spec_silver.discovery_specs s ON s.provider_id = p.id AND s.is_preferred = TRUE
            WHERE p.embedding IS NOT NULL
            {quality_filter}
            ORDER BY p.embedding <=> %s::vector
            LIMIT %s
        """, (query_embedding, query_embedding, max_results))
        
        for row in cur.fetchall():
            similarity = float(row[8]) if row[8] else 0.0
            
            if similarity < MIN_SEMANTIC_SIMILARITY:
                continue
            
            matches.append(CatalogMatch(
                provider_id=row[0],
                provider_key=row[1],
                domain=row[2],
                display_name=row[3],
                is_curated=row[4],
                quality_tier=row[5],
                spec_url=row[6],
                spec_format=row[7],
                similarity=similarity,
                score=similarity,
            ))
    
    return matches


async def _compute_query_embedding(query: str) -> Optional[List[float]]:
    """Compute embedding for search query."""
    try:
        import openai
        
        client = openai.AsyncOpenAI()
        response = await client.embeddings.create(
            input=[query],
            model="text-embedding-3-small",
        )
        return response.data[0].embedding
        
    except ImportError:
        logger.debug("openai package not installed")
        return None
    except Exception as e:
        logger.debug(f"Embedding computation failed: {e}")
        return None


def _apply_quality_boost(match: CatalogMatch) -> float:
    """Apply quality-based score boosts."""
    score = match.similarity
    
    if match.is_curated:
        score *= CURATED_BOOST
    elif match.quality_tier == "premium":
        score *= PREMIUM_BOOST
    
    return min(1.0, score)  # Cap at 1.0


def search_local_catalog_sync(
    query: str,
    provider_hint: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> List[CatalogMatch]:
    """Synchronous wrapper for search_local_catalog."""
    return asyncio.run(search_local_catalog(query, provider_hint, keywords, max_results))


def get_provider_by_domain(domain: str) -> Optional[CatalogMatch]:
    """
    Get a provider directly by domain.
    
    Useful for direct lookups when provider is known.
    """
    with _get_catalog_connection() as conn:
        if conn is None:
            return None
        
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    p.id, p.provider_key, p.domain, p.display_name,
                    p.is_curated, p.quality_tier,
                    s.spec_url, s.spec_format
                FROM spec_silver.discovery_providers p
                JOIN spec_silver.discovery_specs s ON s.provider_id = p.id AND s.is_preferred = TRUE
                WHERE LOWER(p.domain) = LOWER(%s)
                LIMIT 1
            """, (domain,))
            
            row = cur.fetchone()
            if row:
                return CatalogMatch(
                    provider_id=row[0],
                    provider_key=row[1],
                    domain=row[2],
                    display_name=row[3],
                    is_curated=row[4],
                    quality_tier=row[5],
                    spec_url=row[6],
                    spec_format=row[7],
                    similarity=1.0,
                    score=1.0,
                )
            return None


def is_catalog_available() -> bool:
    """Check if the local catalog is available and populated."""
    with _get_catalog_connection() as conn:
        if conn is None:
            return False
        
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM spec_silver.discovery_providers")
                count = cur.fetchone()[0]
                return count > 0
        except Exception:
            return False
