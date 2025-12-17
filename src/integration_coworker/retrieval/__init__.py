"""
Semantic retrieval module for spec chunks and KG nodes.
Implements design doc Section 6.1 - Hybrid GraphRAG strategy.

This module provides:
- search_spec_chunks: Pure semantic retrieval over spec_chunks table
- search_kg_templates: Hybrid GraphRAG (graph filter + semantic rank) over KG templates
- compute_embedding: Compute embeddings using the same client as embed_spec_chunks
- cosine_similarity: Vector similarity computation
"""
from .semantic_search import (
    search_spec_chunks,
    search_kg_templates,
    compute_embedding,
    cosine_similarity,
    ChunkMatch,
    TemplateMatch,
)

__all__ = [
    "search_spec_chunks",
    "search_kg_templates",
    "compute_embedding",
    "cosine_similarity",
    "ChunkMatch",
    "TemplateMatch",
]
