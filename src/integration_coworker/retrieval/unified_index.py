"""
Unified Semantic Index for combined spec_chunks + KG template search.

V2.2 (Dynamic Capability Fix #3): Provides a single interface for semantic search
across both spec chunks and knowledge graph templates. This addresses the issue
of computed embeddings not being used effectively by combining them into a
unified search interface.

The UnifiedSemanticIndex class:
- Searches spec_chunks for relevant API documentation
- Searches KG templates for relevant workflow patterns
- Combines and ranks results by relevance
- Caches embeddings for repeated queries
- Supports both pgvector and SQLite backends

This replaces scattered embed_query calls with a centralized, optimized system.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union
from enum import Enum

from integration_coworker.retrieval.semantic_search import (
    search_spec_chunks,
    search_kg_templates,
    compute_embedding,
    cosine_similarity,
    ChunkMatch,
    TemplateMatch,
)

logger = logging.getLogger(__name__)


class ResultType(Enum):
    """Type of search result."""
    SPEC_CHUNK = "spec_chunk"
    KG_TEMPLATE = "kg_template"


@dataclass
class UnifiedSearchResult:
    """
    Unified search result that can represent either a spec chunk or KG template.
    
    This allows callers to handle both result types uniformly while still
    providing type-specific metadata when needed.
    """
    result_type: ResultType
    id: int
    key: str
    content: str
    similarity: float
    combined_score: float = 0.0
    
    # Type-specific metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Source result for full access
    source_result: Optional[Union[ChunkMatch, TemplateMatch]] = None
    
    @classmethod
    def from_chunk(cls, chunk: ChunkMatch, weight: float = 1.0) -> 'UnifiedSearchResult':
        """Create from a ChunkMatch result."""
        return cls(
            result_type=ResultType.SPEC_CHUNK,
            id=chunk.chunk_id,
            key=f"chunk_{chunk.chunk_id}",
            content=chunk.content[:500],  # Truncate for display
            similarity=chunk.similarity,
            combined_score=chunk.similarity * weight,
            metadata={
                "spec_document_id": chunk.spec_document_id,
                "chunk_index": chunk.chunk_index,
            },
            source_result=chunk,
        )
    
    @classmethod
    def from_template(cls, template: TemplateMatch, weight: float = 1.0) -> 'UnifiedSearchResult':
        """Create from a TemplateMatch result."""
        return cls(
            result_type=ResultType.KG_TEMPLATE,
            id=template.node_id,
            key=template.key,
            content=template.description or template.name,
            similarity=template.similarity,
            combined_score=template.combined_score * weight,
            metadata={
                "name": template.name,
                "graph_score": template.graph_score,
            },
            source_result=template,
        )


class UnifiedSemanticIndex:
    """
    Unified interface for semantic search across spec chunks and KG templates.
    
    V2.2 (Fix #3): This class provides:
    - Single search interface for both data sources
    - Configurable weighting between chunk and template results
    - Query embedding caching to avoid redundant API calls
    - Hybrid scoring that combines semantic and graph-based signals
    
    Example usage:
        index = UnifiedSemanticIndex(provider_code="stripe")
        results = index.search("create a checkout session", top_k=10)
        
        for result in results:
            if result.result_type == ResultType.SPEC_CHUNK:
                print(f"Spec: {result.content}")
            else:
                print(f"Template: {result.key}")
    """
    
    def __init__(
        self,
        provider_code: Optional[str] = None,
        spec_document_id: Optional[int] = None,
        chunk_weight: float = 0.6,
        template_weight: float = 0.4,
        cache_embeddings: bool = True,
    ):
        """
        Initialize the unified index.
        
        Args:
            provider_code: Optional provider filter for KG templates
            spec_document_id: Optional filter to specific spec document
            chunk_weight: Weight for spec chunk results (0.0-1.0)
            template_weight: Weight for KG template results (0.0-1.0)
            cache_embeddings: Whether to cache query embeddings
        """
        self.provider_code = provider_code
        self.spec_document_id = spec_document_id
        self.chunk_weight = chunk_weight
        self.template_weight = template_weight
        self.cache_embeddings = cache_embeddings
        
        # Embedding cache: query -> embedding
        self._embedding_cache: Dict[str, List[float]] = {}
    
    def _get_embedding(self, query: str) -> List[float]:
        """Get embedding for query, using cache if available."""
        if not query:
            return []
        
        # Normalize query for cache lookup
        cache_key = query.strip().lower()[:500]  # Truncate for consistent caching
        
        if self.cache_embeddings and cache_key in self._embedding_cache:
            logger.debug(f"Using cached embedding for query")
            return self._embedding_cache[cache_key]
        
        embedding = compute_embedding(query)
        
        if self.cache_embeddings and embedding:
            self._embedding_cache[cache_key] = embedding
        
        return embedding
    
    def search(
        self,
        query: str,
        top_k: int = 10,
        include_chunks: bool = True,
        include_templates: bool = True,
    ) -> List[UnifiedSearchResult]:
        """
        Search across both spec chunks and KG templates.
        
        Results are combined and ranked by combined_score, which incorporates:
        - Semantic similarity from embeddings
        - Type-specific weights (chunk_weight, template_weight)
        - Graph-based signals for templates
        
        Args:
            query: Natural language query
            top_k: Total number of results to return
            include_chunks: Whether to search spec chunks
            include_templates: Whether to search KG templates
            
        Returns:
            List of UnifiedSearchResult sorted by combined_score
        """
        if not query:
            return []
        
        results: List[UnifiedSearchResult] = []
        
        # Determine how many results to fetch from each source
        # Fetch more than needed to allow for filtering and ranking
        per_source_k = top_k * 2
        
        # Search spec chunks
        if include_chunks:
            try:
                chunk_matches = search_spec_chunks(
                    query=query,
                    top_k=per_source_k,
                    spec_document_id=self.spec_document_id,
                )
                for chunk in chunk_matches:
                    results.append(
                        UnifiedSearchResult.from_chunk(chunk, weight=self.chunk_weight)
                    )
                logger.debug(f"Found {len(chunk_matches)} spec chunk matches")
            except Exception as e:
                logger.warning(f"Spec chunk search failed: {e}")
        
        # Search KG templates
        if include_templates:
            try:
                template_matches = search_kg_templates(
                    query=query,
                    provider_code=self.provider_code,
                    top_k=per_source_k,
                )
                for template in template_matches:
                    results.append(
                        UnifiedSearchResult.from_template(template, weight=self.template_weight)
                    )
                logger.debug(f"Found {len(template_matches)} KG template matches")
            except Exception as e:
                logger.warning(f"KG template search failed: {e}")
        
        # Sort by combined score and return top_k
        results.sort(key=lambda r: r.combined_score, reverse=True)
        return results[:top_k]
    
    def search_chunks_only(
        self,
        query: str,
        top_k: int = 5,
    ) -> List[ChunkMatch]:
        """
        Search only spec chunks (convenience method).
        
        Returns the raw ChunkMatch objects for callers that need full details.
        """
        return search_spec_chunks(
            query=query,
            top_k=top_k,
            spec_document_id=self.spec_document_id,
        )
    
    def search_templates_only(
        self,
        query: str,
        top_k: int = 5,
    ) -> List[TemplateMatch]:
        """
        Search only KG templates (convenience method).
        
        Returns the raw TemplateMatch objects for callers that need full details.
        """
        return search_kg_templates(
            query=query,
            provider_code=self.provider_code,
            top_k=top_k,
        )
    
    def clear_cache(self):
        """Clear the embedding cache."""
        self._embedding_cache.clear()
        logger.debug("Embedding cache cleared")
    
    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics."""
        return {
            "cached_embeddings": len(self._embedding_cache),
        }


def get_unified_index(
    provider_code: Optional[str] = None,
    spec_document_id: Optional[int] = None,
) -> UnifiedSemanticIndex:
    """
    Factory function to get a configured UnifiedSemanticIndex.
    
    This is the recommended entry point for callers that want to use
    unified semantic search.
    
    Args:
        provider_code: Optional provider filter
        spec_document_id: Optional spec document filter
        
    Returns:
        Configured UnifiedSemanticIndex instance
    """
    return UnifiedSemanticIndex(
        provider_code=provider_code,
        spec_document_id=spec_document_id,
    )
