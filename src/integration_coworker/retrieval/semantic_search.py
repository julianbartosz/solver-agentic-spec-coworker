"""
Semantic search over embeddings stored in DB.

Functions:
- search_spec_chunks(query: str, top_k: int, spec_document_id: Optional[int]) -> List[ChunkMatch]
- search_kg_templates(query: str, provider_code: str, top_k: int) -> List[TemplateMatch]
- compute_embedding(text: str) -> List[float]
- cosine_similarity(a: List[float], b: List[float]) -> float

Implementation:
- Uses LangChain OpenAIEmbeddings for automatic LangSmith tracing (V2.1)
- For **SQLite backend**: computes query embedding, then calculates cosine similarity in Python.
- For **Postgres+pgvector backend**: pushes similarity computation into the DB using native
  pgvector operators (<=> for cosine distance, <-> for L2 distance).
- Returns top_k results with similarity scores.

Non-goal: this module does *not* replace KG BFS/DFS for structural queries like
shortest paths; it layers semantic search on top of those graph operations.
"""

import json
import logging
import math
from dataclasses import dataclass
from typing import List, Optional

from integration_coworker.persistence import db

logger = logging.getLogger(__name__)


def _is_postgres_backend() -> bool:
    """Check if we're using Postgres backend (with potential pgvector support)."""
    try:
        engine_type = db.get_engine_type()
        return engine_type == "postgres"
    except Exception:
        return False


def _format_vector_literal(embedding: List[float]) -> str:
    """Format embedding as Postgres vector literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


@dataclass
class ChunkMatch:
    """Result from searching spec_chunks by semantic similarity."""
    chunk_id: int
    spec_document_id: Optional[int]
    chunk_index: int
    content: str
    similarity: float


@dataclass
class TemplateMatch:
    """Result from searching KG workflow templates by semantic similarity."""
    node_id: int
    key: str
    name: str
    description: Optional[str]
    similarity: float
    # Graph-derived fields for hybrid scoring
    graph_score: float = 0.0
    combined_score: float = 0.0


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """
    Compute cosine similarity between two vectors.
    
    Returns a value between -1 and 1, where:
    - 1 means identical direction
    - 0 means orthogonal
    - -1 means opposite direction
    """
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


def compute_embedding(text: str) -> List[float]:
    """
    Compute embedding for query text using same client as embed_spec_chunks.
    
    Returns 1536-dim vector or empty list on failure.
    Uses LangChain OpenAIEmbeddings when available for LangSmith tracing.
    """
    if not text or not text.strip():
        return []

    # Import here to avoid circular deps
    try:
        from integration_coworker.graph.nodes.embed_spec_chunks import _get_embedding_client
    except ImportError:
        logger.warning("Could not import embedding client")
        return []

    client = _get_embedding_client()
    if not client:
        logger.debug("No embedding client available for semantic search")
        return []

    try:
        # Truncate long text to avoid API limits
        truncated = text[:8000]
        result = client.embed_query(truncated)
        return result
    except Exception as e:
        logger.warning(f"Failed to compute query embedding: {e}")
        return []


def search_spec_chunks(
    query: str,
    top_k: int = 5,
    spec_document_id: Optional[int] = None,
) -> List[ChunkMatch]:
    """
    Search spec_chunks by semantic similarity.
    
    This is **pure semantic retrieval** - no KG BFS/DFS here.
    Used by understand_task to fetch relevant spec context.
    
    Args:
        query: Natural language query
        top_k: Number of results to return
        spec_document_id: Optional filter to specific document
    
    Returns:
        List of ChunkMatch sorted by similarity (highest first)
    """
    query_embedding = compute_embedding(query)
    if not query_embedding:
        logger.debug("No query embedding, returning empty results")
        return []

    # Use pgvector-native search when available
    if _is_postgres_backend():
        return _search_spec_chunks_pgvector(query_embedding, top_k, spec_document_id)

    # Fallback to Python-based cosine similarity for SQLite
    return _search_spec_chunks_python(query_embedding, top_k, spec_document_id)


def _search_spec_chunks_pgvector(
    query_embedding: List[float],
    top_k: int,
    spec_document_id: Optional[int],
) -> List[ChunkMatch]:
    """
    Search spec_chunks using pgvector native similarity operators.
    
    Uses <=> operator (cosine distance) for similarity:
    - cosine_distance = 1 - cosine_similarity
    - So similarity = 1 - cosine_distance
    """
    try:
        from integration_coworker.persistence.postgres import get_connection

        vector_literal = _format_vector_literal(query_embedding)

        with get_connection() as conn:
            with conn.cursor() as cur:
                # pgvector <=> operator returns cosine distance (0-2 range)
                # similarity = 1 - distance
                if spec_document_id:
                    cur.execute("""
                        SELECT id, spec_document_id, chunk_index, content,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM spec_silver.spec_chunks
                        WHERE spec_document_id = %s AND embedding IS NOT NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                    """, (vector_literal, spec_document_id, vector_literal, top_k))
                else:
                    cur.execute("""
                        SELECT id, spec_document_id, chunk_index, content,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM spec_silver.spec_chunks
                        WHERE embedding IS NOT NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                    """, (vector_literal, vector_literal, top_k))

                rows = cur.fetchall()

                return [
                    ChunkMatch(
                        chunk_id=row[0],
                        spec_document_id=row[1],
                        chunk_index=row[2],
                        content=row[3] or "",
                        similarity=float(row[4]) if row[4] else 0.0,
                    )
                    for row in rows
                ]
    except Exception as e:
        logger.warning(f"pgvector search failed, falling back to Python: {e}")
        return _search_spec_chunks_python(query_embedding, top_k, spec_document_id)


def _search_spec_chunks_python(
    query_embedding: List[float],
    top_k: int,
    spec_document_id: Optional[int],
) -> List[ChunkMatch]:
    """
    Search spec_chunks using Python-based cosine similarity (SQLite fallback).
    """
    try:
        conn = db.get_connection()
        cur = conn.cursor()

        # Fetch all chunks with embeddings (or filtered by doc)
        if spec_document_id:
            cur.execute("""
                SELECT id, spec_document_id, chunk_index, content, embedding
                FROM spec_chunks
                WHERE spec_document_id = ? AND embedding IS NOT NULL
            """, (spec_document_id,))
        else:
            cur.execute("""
                SELECT id, spec_document_id, chunk_index, content, embedding
                FROM spec_chunks
                WHERE embedding IS NOT NULL
            """)

        rows = cur.fetchall()

        # Score each chunk
        matches = []
        for row in rows:
            chunk_id, doc_id, chunk_idx, content, embedding_json = row
            if not embedding_json:
                continue

            try:
                chunk_embedding = json.loads(embedding_json)
            except (json.JSONDecodeError, TypeError):
                continue

            similarity = cosine_similarity(query_embedding, chunk_embedding)

            matches.append(ChunkMatch(
                chunk_id=chunk_id,
                spec_document_id=doc_id,
                chunk_index=chunk_idx,
                content=content or "",
                similarity=similarity,
            ))

        # Sort by similarity and return top_k
        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches[:top_k]

    except Exception as e:
        logger.warning(f"Error searching spec chunks: {e}")
        return []


def search_kg_templates(
    query: str,
    provider_code: Optional[str] = None,
    top_k: int = 5,
    entity_ids: Optional[List[str]] = None,
    pattern_ids: Optional[List[str]] = None,
) -> List[TemplateMatch]:
    """
    Hybrid GraphRAG search over KG workflow templates.
    
    Responsibilities:
    - Use KG traversal functions for **structural filtering** of candidate templates
      (provider, entities, patterns, task type).
    - Use embeddings for **semantic ranking** of those candidates against the task
      description.
    - Preserve explainability by returning graph- and embedding-related scores.
    
    Non-goal: this function does *not* replace KG BFS/DFS for structural queries like
    shortest paths; it layers semantic search on top of those graph operations.
    
    Args:
        query: Task description to match against templates
        provider_code: Optional provider filter
        top_k: Number of results to return
        entity_ids: Optional list of entity IDs for graph filtering
        pattern_ids: Optional list of pattern IDs for graph filtering
    
    Returns:
        List of TemplateMatch sorted by combined score (highest first)
    """
    query_embedding = compute_embedding(query)

    # Use pgvector-native search when available and we have embeddings
    if _is_postgres_backend() and query_embedding:
        return _search_kg_templates_pgvector(query_embedding, query, provider_code, top_k)

    # Fallback to Python-based similarity
    return _search_kg_templates_python(query_embedding, query, provider_code, top_k)


def _search_kg_templates_pgvector(
    query_embedding: List[float],
    query: str,
    provider_code: Optional[str],
    top_k: int,
) -> List[TemplateMatch]:
    """
    Search KG templates using pgvector native similarity operators.
    """
    try:
        from integration_coworker.persistence.postgres import get_connection

        vector_literal = _format_vector_literal(query_embedding)

        with get_connection() as conn:
            with conn.cursor() as cur:
                if provider_code:
                    cur.execute("""
                        SELECT id, key, name, description,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM kg.kg_nodes
                        WHERE node_type = 'workflow_template'
                          AND (provider_code = %s OR provider_code IS NULL)
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                    """, (vector_literal, provider_code, vector_literal, top_k))
                else:
                    cur.execute("""
                        SELECT id, key, name, description,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM kg.kg_nodes
                        WHERE node_type = 'workflow_template'
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                    """, (vector_literal, vector_literal, top_k))

                rows = cur.fetchall()

                matches = []
                for row in rows:
                    semantic_score = float(row[4]) if row[4] else 0.0
                    # V2: Graph score based on provider match.
                    # When a provider_code is supplied, treat it as a strong match signal.
                    # When absent, use a small baseline so routing isn't penalized to zero.
                    graph_score = 0.5 if provider_code else 0.3
                    combined_score = (
                        graph_score * 0.4 +
                        semantic_score * 0.6
                    )

                    matches.append(TemplateMatch(
                        node_id=row[0],
                        key=row[1] or "",
                        name=row[2] or "",
                        description=row[3],
                        similarity=semantic_score,
                        graph_score=graph_score,
                        combined_score=combined_score,
                    ))

                # Sort by combined score
                matches.sort(key=lambda m: m.combined_score, reverse=True)
                return matches[:top_k]

    except Exception as e:
        logger.warning(f"pgvector KG search failed, falling back to Python: {e}")
        return _search_kg_templates_python(query_embedding, query, provider_code, top_k)


def _search_kg_templates_python(
    query_embedding: List[float],
    query: str,
    provider_code: Optional[str],
    top_k: int,
) -> List[TemplateMatch]:
    """
    Search KG templates using Python-based cosine similarity (SQLite fallback).
    
    V2: Uses configurable scoring weights from config.
    """
    try:
        conn = db.get_connection()
        cur = conn.cursor()

        # Fetch workflow template nodes with optional provider filter
        if provider_code:
            cur.execute("""
                SELECT id, key, name, description, embedding
                FROM kg_nodes
                WHERE node_type = 'workflow_template'
                  AND (provider_code = ? OR provider_code IS NULL)
            """, (provider_code,))
        else:
            cur.execute("""
                SELECT id, key, name, description, embedding
                FROM kg_nodes
                WHERE node_type = 'workflow_template'
            """)

        rows = cur.fetchall()

        matches = []
        for row in rows:
            node_id, key, name, description, embedding_json = row

            # Compute semantic score
            if query_embedding and embedding_json:
                try:
                    node_embedding = json.loads(embedding_json)
                    semantic_score = max(0, cosine_similarity(query_embedding, node_embedding))
                except (json.JSONDecodeError, TypeError):
                    semantic_score = _keyword_similarity(query, name, description)
            else:
                # Fallback: keyword matching
                semantic_score = _keyword_similarity(query, name, description)

            # V2: Graph score based on provider match.
            # When no provider_code is supplied, we still apply a small baseline
            # graph score so template routing isn't penalized to zero.
            graph_score = 0.5 if provider_code else 0.3

            # V2: Combined score
            combined_score = (
                graph_score * 0.4 +
                semantic_score * 0.6
            )

            matches.append(TemplateMatch(
                node_id=node_id,
                key=key or "",
                name=name or "",
                description=description,
                similarity=semantic_score,
                graph_score=graph_score,
                combined_score=combined_score,
            ))

        # Sort by combined score and return top_k
        matches.sort(key=lambda m: m.combined_score, reverse=True)
        return matches[:top_k]

    except Exception as e:
        logger.warning(f"Error searching KG templates: {e}")
        return []


def _keyword_similarity(query: str, name: str, description: Optional[str]) -> float:
    """
    Fallback keyword-based similarity when embeddings unavailable.
    
    Uses Jaccard similarity on word sets.
    """
    if not query:
        return 0.0

    query_words = set(query.lower().split())
    text = f"{name or ''} {description or ''}".lower()
    text_words = set(text.split())

    if not query_words or not text_words:
        return 0.0

    intersection = len(query_words & text_words)
    union = len(query_words | text_words)

    if union == 0:
        return 0.0

    return intersection / union
