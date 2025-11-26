"""
GraphRAG Retrieval Module

Implements graph-first retrieval with embeddings for ranking.
Used by align_task_with_kg to find matching workflow templates.

================================================================================
GRAPHRAG SCORING STRATEGY (per design doc Section 5.5)
================================================================================

This module implements a "graph-first, embeddings-second" approach:

1. **Graph Filtering (Mandatory)**: 
   - Filter candidates by provider_code (exact match)
   - Filter by node_type = 'workflow_template'
   - Order by usage_count (popularity) and confidence_score

2. **Graph Scoring (40% weight)**:
   - Boost templates that have edges to known entities/endpoints
   - +0.2 per entity edge (consumes/produces_entity)
   - +0.1 per endpoint edge (uses_endpoint), capped at 0.5

3. **Embedding Scoring (40% weight)**:
   - Compute cosine similarity between task_description and template embedding
   - Falls back to 0.5 default if embeddings unavailable

4. **Exact Match Bonus (20% weight)**:
   - +0.3 if task_slug appears in template key
   - +0.15 for partial/fuzzy match

Combined formula:
  final_score = (graph_score * 0.4) + (embedding_score * 0.4) + exact_match_bonus + 0.1

The 0.1 base score ensures templates that pass graph filtering are considered
even when both graph and embedding scores are low.

================================================================================

Tables used:
- kg.nodes: Core graph nodes (provider, entity, endpoint, workflow_template, task)
- kg.edges: Relationships between nodes
- kg.workflow_steps: Steps within workflow templates
"""
import json
import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple

from integration_coworker.persistence import db
from integration_coworker.config import get_settings
from integration_coworker.domain.models import (
    KGNodeType, 
    KGEdgeRelation,
    KGWorkflowTemplate,
    KGWorkflowStep,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fallback flag: Set USE_IN_MEMORY_KG_FALLBACK=1 to enable legacy in-memory
# templates when the KG is empty. This should NEVER be enabled on demo path.
# ---------------------------------------------------------------------------
def _check_fallback_enabled() -> bool:
    """Check if in-memory KG fallback is enabled (evaluated at call time)."""
    return os.environ.get("USE_IN_MEMORY_KG_FALLBACK", "0") == "1"

# For backwards compatibility, also expose as a constant (evaluated at import)
# but the _check_fallback_enabled() function should be used for dynamic checks
USE_IN_MEMORY_KG_FALLBACK = _check_fallback_enabled()


@dataclass
class KGTemplateMatch:
    """A workflow template match from the KG."""
    template_node_id: int
    template_key: str
    name: str
    description: Optional[str]
    provider_code: str
    properties: Dict[str, Any]
    steps: List[Dict[str, Any]]
    similarity_score: float  # 0-1, higher is better
    graph_score: float  # Based on edge connections
    final_score: float  # Combined score


def _compute_embedding(text: str) -> Optional[List[float]]:
    """Compute embedding for text using OpenAI API (if available)."""
    settings = get_settings()
    if not settings.llm.api_key or settings.llm.use_mock:
        return None
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.llm.api_key)
        response = client.embeddings.create(
            model=settings.llm.embedding_model or "text-embedding-3-small",
            input=text[:8000],
        )
        return response.data[0].embedding
    except Exception as e:
        logger.warning(f"Failed to compute embedding: {e}")
        return None


def _get_workflow_steps(
    cur, 
    template_node_id: int, 
    is_postgres: bool
) -> List[Dict[str, Any]]:
    """Retrieve workflow steps for a template node."""
    if is_postgres:
        cur.execute("""
            SELECT step_key, step_type, position, label, description, config
            FROM kg.workflow_steps
            WHERE template_node_id = %s
            ORDER BY position
        """, (template_node_id,))
    else:
        cur.execute("""
            SELECT step_key, step_type, position, label, description, config
            FROM kg_workflow_steps
            WHERE template_node_id = ?
            ORDER BY position
        """, (template_node_id,))
    
    steps = []
    for row in cur.fetchall():
        # Both Postgres and SQLite now return tuple rows
        step = {
            "key": row[0],      # step_key
            "type": row[1],     # step_type
            "position": row[2], # position
            "label": row[3],    # label
            "description": row[4], # description
        }
        if row[5]:  # config
            step["config"] = row[5] if isinstance(row[5], dict) else json.loads(row[5])
        steps.append(step)
    
    return steps


def _compute_graph_score(
    cur,
    template_node_id: int,
    entity_names: List[str],
    endpoint_paths: List[str],
    provider_code: str,
    is_postgres: bool,
) -> float:
    """
    Compute graph-based score for a template.
    
    Higher score if template is connected to:
    - Entities mentioned in the task
    - Endpoints present in the spec
    """
    score = 0.0
    
    # Check entity connections
    if entity_names:
        entity_keys = [f"entity.{provider_code}.{name}" for name in entity_names]
        if is_postgres:
            cur.execute("""
                SELECT COUNT(*) FROM kg.edges e
                JOIN kg.nodes dst ON e.dst_node_id = dst.id
                WHERE e.src_node_id = %s
                AND e.relation_type IN ('produces_entity', 'consumes_entity')
                AND dst.key = ANY(%s)
            """, (template_node_id, entity_keys))
            result = cur.fetchone()
            count = result[0] if result else 0
        else:
            placeholders = ",".join("?" * len(entity_keys))
            cur.execute(f"""
                SELECT COUNT(*) FROM kg_edges e
                JOIN kg_nodes dst ON e.dst_node_id = dst.id
                WHERE e.src_node_id = ?
                AND e.relation_type IN ('produces_entity', 'consumes_entity')
                AND dst.key IN ({placeholders})
            """, [template_node_id] + entity_keys)
            count = cur.fetchone()[0]
        score += count * 0.2  # 0.2 per matching entity
    
    # Check endpoint connections
    if endpoint_paths:
        if is_postgres:
            cur.execute("""
                SELECT COUNT(*) FROM kg.edges e
                JOIN kg.nodes dst ON e.dst_node_id = dst.id
                WHERE e.src_node_id = %s
                AND e.relation_type = 'uses_endpoint'
                AND dst.node_type = 'endpoint'
                AND dst.provider_code = %s
            """, (template_node_id, provider_code))
            result = cur.fetchone()
            count = result[0] if result else 0
        else:
            cur.execute("""
                SELECT COUNT(*) FROM kg_edges e
                JOIN kg_nodes dst ON e.dst_node_id = dst.id
                WHERE e.src_node_id = ?
                AND e.relation_type = 'uses_endpoint'
                AND dst.node_type = 'endpoint'
                AND dst.provider_code = ?
            """, (template_node_id, provider_code))
            count = cur.fetchone()[0]
        score += min(count * 0.1, 0.5)  # Cap at 0.5
    
    return min(score, 1.0)


def query_kg_templates(
    provider_code: str,
    task_description: str,
    task_slug: Optional[str] = None,
    entity_names: Optional[List[str]] = None,
    endpoint_paths: Optional[List[str]] = None,
    top_k: int = 5,
) -> List[KGTemplateMatch]:
    """
    Query the KG for matching workflow templates using GraphRAG.
    
    GraphRAG Strategy:
    1. Filter by provider_code (graph filter)
    2. Optionally filter by entities/endpoints present (graph filter)
    3. Rank by embedding similarity to task_description
    4. Boost score based on graph connections (edges to entities/endpoints)
    
    Args:
        provider_code: Provider to filter by (e.g., "stripe")
        task_description: Natural language description of the task
        task_slug: Optional exact task slug to match
        entity_names: Optional list of entity names to prefer
        endpoint_paths: Optional list of endpoint paths to prefer
        top_k: Maximum number of matches to return
    
    Returns:
        List of KGTemplateMatch ordered by final_score descending
    """
    matches = []
    
    try:
        db.init_schema()
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        # =====================================================================
        # Step 1: Graph filter - get all workflow templates for this provider
        # =====================================================================
        if is_postgres:
            cur.execute("""
                SELECT id, key, name, description, properties, embedding
                FROM kg.nodes
                WHERE node_type = %s
                AND (provider_code = %s OR provider_code IS NULL)
                ORDER BY usage_count DESC, confidence_score DESC
                LIMIT %s
            """, (KGNodeType.WORKFLOW_TEMPLATE.value, provider_code, top_k * 2))
        else:
            cur.execute("""
                SELECT id, key, name, description, properties, embedding
                FROM kg_nodes
                WHERE node_type = ?
                AND (provider_code = ? OR provider_code IS NULL)
                ORDER BY usage_count DESC, confidence_score DESC
                LIMIT ?
            """, (KGNodeType.WORKFLOW_TEMPLATE.value, provider_code, top_k * 2))
        
        candidates = cur.fetchall()
        
        if not candidates:
            logger.info(f"No KG templates found for provider={provider_code}")
            return []
        
        # =====================================================================
        # Step 2: Compute embedding for task description (for semantic ranking)
        # =====================================================================
        task_embedding = _compute_embedding(task_description) if task_description else None
        
        # =====================================================================
        # Step 3: Score and rank candidates
        # =====================================================================
        for row in candidates:
            # Both Postgres and SQLite now return tuple rows
            # SELECT id, key, name, description, properties, embedding
            node_id = row[0]
            key = row[1]
            name = row[2]
            description = row[3]
            properties_raw = row[4]
            candidate_embedding_raw = row[5]
            
            properties = properties_raw if isinstance(properties_raw, dict) else json.loads(properties_raw or "{}")
            candidate_embedding = candidate_embedding_raw
            if candidate_embedding and not isinstance(candidate_embedding, list):
                candidate_embedding = json.loads(candidate_embedding) if candidate_embedding else None
            
            # Extract provider_code from key (e.g., "template.stripe.create_checkout_session")
            parts = key.split(".")
            template_provider = parts[1] if len(parts) > 1 else provider_code
            
            # Compute similarity score (embedding-based)
            similarity_score = 0.5  # Default if no embeddings
            if task_embedding and candidate_embedding:
                # Cosine similarity
                try:
                    dot_product = sum(a * b for a, b in zip(task_embedding, candidate_embedding))
                    norm_a = sum(a * a for a in task_embedding) ** 0.5
                    norm_b = sum(b * b for b in candidate_embedding) ** 0.5
                    if norm_a > 0 and norm_b > 0:
                        similarity_score = dot_product / (norm_a * norm_b)
                        similarity_score = (similarity_score + 1) / 2  # Normalize to 0-1
                except Exception:
                    pass
            
            # Compute graph score (edge-based)
            graph_score = _compute_graph_score(
                cur,
                node_id,
                entity_names or [],
                endpoint_paths or [],
                provider_code,
                is_postgres,
            )
            
            # Exact match bonus
            exact_match_bonus = 0.0
            if task_slug:
                if task_slug in key:
                    exact_match_bonus = 0.3
                elif task_slug.replace("_", "") in key.replace("_", ""):
                    exact_match_bonus = 0.15
            
            # Combined score: 40% graph, 40% embedding, 20% exact match bonus + base
            # This implements graph-first by weighting graph equally with embeddings
            # but graph filtering already happened (Step 1), so graph has implicit priority
            final_score = (
                graph_score * 0.4 +       # Graph structure weight
                similarity_score * 0.4 +   # Embedding similarity weight  
                exact_match_bonus +        # Exact match bonus (up to 0.3)
                0.1                        # Base score for passing graph filter
            )
            
            # Get workflow steps
            steps = _get_workflow_steps(cur, node_id, is_postgres)
            if not steps:
                # Use steps from properties if available
                steps = properties.get("steps", [])
            
            matches.append(KGTemplateMatch(
                template_node_id=node_id,
                template_key=key,
                name=name,
                description=description,
                provider_code=template_provider,
                properties=properties,
                steps=steps,
                similarity_score=similarity_score,
                graph_score=graph_score,
                final_score=final_score,
            ))
        
        # Sort by final score
        matches.sort(key=lambda m: m.final_score, reverse=True)
        
        logger.info(f"KG GraphRAG query: {len(matches)} templates found for provider={provider_code}")
        if matches:
            logger.info(f"Best match: {matches[0].template_key} (score={matches[0].final_score:.2f})")
        
        return matches[:top_k]
        
    except Exception as e:
        logger.error(f"KG query failed: {e}")
        return []


def has_kg_templates(provider_code: str) -> bool:
    """Check if the KG has any templates for the given provider."""
    try:
        db.init_schema()
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        if is_postgres:
            cur.execute("""
                SELECT COUNT(*) FROM kg.nodes
                WHERE node_type = %s
                AND (provider_code = %s OR provider_code IS NULL)
            """, (KGNodeType.WORKFLOW_TEMPLATE.value, provider_code))
            result = cur.fetchone()
            return result[0] > 0 if result else False
        else:
            cur.execute("""
                SELECT COUNT(*) FROM kg_nodes
                WHERE node_type = ?
                AND (provider_code = ? OR provider_code IS NULL)
            """, (KGNodeType.WORKFLOW_TEMPLATE.value, provider_code))
            result = cur.fetchone()
            return result[0] > 0 if result else False
            
    except Exception as e:
        logger.warning(f"KG template check failed: {e}")
        return False


def query_workflow_templates(
    provider_code: str,
    task_description: str,
    known_entities: Optional[List[str]] = None,
    known_endpoints: Optional[List[str]] = None,
    top_k: int = 5,
    similarity_threshold: float = 0.6,
) -> List[KGWorkflowTemplate]:
    """
    Query the KG for matching workflow templates.
    
    This is the main public API for GraphRAG template retrieval.
    Returns List[KGWorkflowTemplate] domain models (not raw dicts).
    
    GraphRAG Strategy:
    1. Graph filters first: Filter by provider_code
    2. Graph traversal: Prefer templates connected to known entities/endpoints
    3. Embedding similarity: Rank by semantic similarity to task_description
    
    Args:
        provider_code: Provider to filter by (e.g., "stripe", "mock_payments")
        task_description: Natural language description of the task
        known_entities: Optional list of entity names to boost score for
        known_endpoints: Optional list of endpoint paths to boost score for
        top_k: Maximum number of templates to return
        similarity_threshold: Minimum similarity score (0-1) to include
    
    Returns:
        List of KGWorkflowTemplate domain models, ordered by score descending
    """
    # Get raw template matches
    matches = query_kg_templates(
        provider_code=provider_code,
        task_description=task_description,
        task_slug=None,
        entity_names=known_entities,
        endpoint_paths=known_endpoints,
        top_k=top_k,
    )
    
    # Filter by similarity threshold
    filtered = [m for m in matches if m.final_score >= similarity_threshold]
    
    # Convert to domain models
    templates = []
    for match in filtered:
        # Convert step dicts to KGWorkflowStep models
        steps = []
        for i, step_dict in enumerate(match.steps):
            step = KGWorkflowStep(
                id=None,
                template_id=match.template_key,
                step_key=step_dict.get("key", f"step_{i}"),
                step_type=step_dict.get("type", "unknown"),
                position=step_dict.get("position", i),
                label=step_dict.get("label"),
                description=step_dict.get("description"),
                config=step_dict.get("config"),
                endpoint_path=step_dict.get("endpoint_path"),
            )
            steps.append(step)
        
        template = KGWorkflowTemplate(
            template_id=match.template_key,
            name=match.name,
            description=match.description,
            provider_code=match.provider_code,
            task_type=match.properties.get("task_type"),
            steps=steps,
            metadata=match.properties,
        )
        templates.append(template)
    
    return templates
