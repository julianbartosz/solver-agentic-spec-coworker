"""
GraphRAG Retrieval Module

Implements graph-first retrieval with embeddings for ranking.
Used by align_task_with_kg to find matching workflow templates.

V2.1: Uses LangChain OpenAIEmbeddings for automatic LangSmith tracing.

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

4. **Confidence Scoring (10% weight)**:
   - Use learned confidence_score from feedback aggregation
   - Default 1.0 for templates with no feedback yet
   - Updated by feedback sync from LangSmith

5. **Exact Match Bonus (10% weight)**:
   - +0.2 if task_slug appears in template key
   - +0.1 for partial/fuzzy match

Combined formula:
  final_score = (graph_score * 0.4) + (embedding_score * 0.4) + (confidence * 0.1) + exact_match_bonus + 0.05

The 0.1 base score ensures templates that pass graph filtering are considered
even when both graph and embedding scores are low.

================================================================================
CROSS-PROVIDER PATTERN MATCHING (M5 Enhancement)
================================================================================

When no exact provider-specific templates are found, the system falls back to
pattern-level matching. Patterns are provider-agnostic workflow structures:

- pattern.crud_create: Create a new resource (POST)
- pattern.crud_read: Read a single resource (GET by ID)
- pattern.crud_list: List resources with pagination (GET)
- pattern.crud_update: Update an existing resource (PUT/PATCH)
- pattern.crud_delete: Delete a resource (DELETE)
- pattern.search_filter: Search with query parameters
- pattern.nested_resource: Access sub-resources (e.g., /users/{id}/orders)
- pattern.auth_flow: Authentication/authorization flows

Pattern matching enables:
1. Knowledge transfer between providers (Stripe CRUD → unknown API CRUD)
2. Cold-start handling for new providers
3. Generic fallback when KG lacks provider-specific templates

================================================================================

Tables used:
- kg.nodes: Core graph nodes (provider, entity, endpoint, workflow_template, task, pattern)
- kg.edges: Relationships between nodes
- kg.workflow_steps: Steps within workflow templates
"""
import json
import logging
import os
import threading
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


# =============================================================================
# Metrics: Embedding Fallback Counter (B-005)
# =============================================================================
# Tracks how often deterministic similarity is used instead of embeddings.
# This is a P2 observability metric for production monitoring.
# =============================================================================

_embedding_fallback_count = 0
_embedding_fallback_lock = threading.Lock()


def _increment_embedding_fallback() -> None:
    """Increment the embedding fallback counter (thread-safe)."""
    global _embedding_fallback_count
    with _embedding_fallback_lock:
        _embedding_fallback_count += 1


def get_embedding_fallback_count() -> int:
    """Get the current embedding fallback count."""
    with _embedding_fallback_lock:
        return _embedding_fallback_count


def reset_embedding_fallback_count() -> None:
    """Reset the embedding fallback count (for testing)."""
    global _embedding_fallback_count
    with _embedding_fallback_lock:
        _embedding_fallback_count = 0


# Import LangChain embeddings for LangSmith tracing
try:
    from langchain_openai import OpenAIEmbeddings
    HAS_LANGCHAIN_EMBEDDINGS = True
except ImportError:
    HAS_LANGCHAIN_EMBEDDINGS = False
    OpenAIEmbeddings = None


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


def _get_embedding_client():
    """
    Get LangChain OpenAIEmbeddings client for automatic LangSmith tracing.
    
    Returns an embeddings client or None if unavailable.
    """
    if not HAS_LANGCHAIN_EMBEDDINGS:
        return None
    
    settings = get_settings()
    if not settings.llm.api_key or settings.llm.use_mock:
        return None
    
    try:
        model = settings.llm.embedding_model or "text-embedding-3-small"
        return OpenAIEmbeddings(
            api_key=settings.llm.api_key,
            model=model,
        )
    except Exception as e:
        logger.warning(f"Failed to create LangChain OpenAIEmbeddings client: {e}")
        return None


def _compute_embedding(text: str) -> Optional[List[float]]:
    """
    Compute embedding for text using LangChain OpenAIEmbeddings.
    
    Uses LangChain for automatic LangSmith tracing of embedding calls.
    """
    settings = get_settings()
    if not settings.llm.api_key or settings.llm.use_mock:
        return None

    client = _get_embedding_client()
    if not client:
        return None

    try:
        # Use LangChain's embed_query for single text (traced in LangSmith)
        truncated_text = text[:8000]  # Truncate to avoid token limits
        return client.embed_query(truncated_text)
    except Exception as e:
        logger.warning(f"Failed to compute embedding: {e}")
        return None


def _deterministic_similarity(task_description: str, template_name: str, template_description: Optional[str]) -> float:
    """
    Compute similarity without embeddings using token overlap.
    
    V4 Enhancement: Deterministic fallback for embedding failures.
    See docs/PROD_GAP_CLOSURE_PLAN_KG_DB_STREAMING.md Phase 2.
    
    Returns value in [0.3, 0.8] range (never the 0.5 constant).
    
    Args:
        task_description: User's task description
        template_name: Template name
        template_description: Optional template description
        
    Returns:
        Similarity score between 0.3 and 0.8
    """
    if not task_description:
        return 0.3
    
    # Tokenize and normalize
    import re
    stopwords = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
        "been", "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "must", "shall", "can",
        "this", "that", "these", "those", "it", "its", "i", "we", "you", "they",
    }
    
    def tokenize(text: str) -> set:
        tokens = set(re.split(r'[\s_\-/.,;:!?()"\'\[\]{}]+', text.lower()))
        return {t for t in tokens if t and len(t) >= 2 and t not in stopwords}
    
    task_tokens = tokenize(task_description)
    template_text = f"{template_name or ''} {template_description or ''}"
    template_tokens = tokenize(template_text)
    
    if not template_tokens:
        return 0.3
    
    # Jaccard similarity
    overlap = len(task_tokens & template_tokens)
    union = len(task_tokens | template_tokens)
    jaccard = overlap / union if union > 0 else 0
    
    # Scale to [0.3, 0.8] range to differentiate from embeddings
    result = 0.3 + (jaccard * 0.5)
    return result


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
        # V26-006: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            is_postgres = db.get_engine_type() == "postgres"
            cur = conn.cursor()

            # =====================================================================
            # Step 1: Graph filter - get all workflow templates for this provider
            # =====================================================================
            if is_postgres:
                cur.execute("""
                    SELECT id, key, name, description, properties, embedding, confidence_score
                    FROM kg.nodes
                    WHERE node_type = %s
                    AND (provider_code = %s OR provider_code IS NULL)
                    ORDER BY usage_count DESC, confidence_score DESC
                    LIMIT %s
                """, (KGNodeType.WORKFLOW_TEMPLATE.value, provider_code, top_k * 2))
            else:
                cur.execute("""
                    SELECT id, key, name, description, properties, embedding, confidence_score
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
                # SELECT id, key, name, description, properties, embedding, confidence_score
                node_id = row[0]
                key = row[1]
                name = row[2]
                description = row[3]
                properties_raw = row[4]
                candidate_embedding_raw = row[5]
                confidence_score = row[6] if len(row) > 6 and row[6] is not None else 1.0

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
                else:
                    # V4: Deterministic fallback when embeddings unavailable
                    similarity_score = _deterministic_similarity(task_description, name, description)
                    _increment_embedding_fallback()  # B-005: Track fallback usage
                    logger.debug(f"Using deterministic similarity: {similarity_score:.2f} for {key}")

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
                        exact_match_bonus = 0.2
                    elif task_slug.replace("_", "") in key.replace("_", ""):
                        exact_match_bonus = 0.1

                # Combined score: 40% graph, 40% embedding, 10% confidence, 10% exact match + base
                # This implements graph-first by weighting graph equally with embeddings
                # but graph filtering already happened (Step 1), so graph has implicit priority
                # Confidence score is learned from feedback (LangSmith + implicit signals)
                final_score = (
                    graph_score * 0.4 +           # Graph structure weight
                    similarity_score * 0.4 +       # Embedding similarity weight
                    confidence_score * 0.1 +       # Learned confidence from feedback
                    exact_match_bonus +            # Exact match bonus (up to 0.2)
                    0.05                           # Base score for passing graph filter
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
        # V26-006: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
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
    similarity_threshold: float = 0.3,
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
        similarity_threshold: Minimum similarity score (0-1) to include.
            Default is 0.3 to allow matches even when embeddings are unavailable
            (which gives a base score ~0.4-0.5 from graph + default similarity).
    
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
    # Note: When embeddings are unavailable, default similarity_score is 0.5,
    # and final_score combines 40% graph + 40% similarity + base = ~0.4-0.6
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


# =============================================================================
# Cross-Provider Pattern Matching (M5 Enhancement)
# =============================================================================

# Standard workflow patterns that are provider-agnostic
STANDARD_PATTERNS = {
    "crud_create": {
        "name": "Create Resource",
        "description": "Create a new resource via POST",
        "http_methods": ["POST"],
        "steps": [
            {"key": "validate", "type": "validation", "position": 0},
            {"key": "call_api", "type": "api_call", "position": 1},
            {"key": "handle_response", "type": "transform", "position": 2},
        ],
    },
    "crud_read": {
        "name": "Read Resource",
        "description": "Retrieve a single resource by ID via GET",
        "http_methods": ["GET"],
        "path_pattern": r".*\{[^}]+\}$",  # Path ends with {id}
        "steps": [
            {"key": "call_api", "type": "api_call", "position": 0},
            {"key": "handle_response", "type": "transform", "position": 1},
        ],
    },
    "crud_list": {
        "name": "List Resources",
        "description": "List resources with optional pagination",
        "http_methods": ["GET"],
        "path_pattern": r".*[^}]$",  # Path does NOT end with {id}
        "steps": [
            {"key": "call_api", "type": "api_call", "position": 0},
            {"key": "paginate", "type": "pagination", "position": 1},
            {"key": "handle_response", "type": "transform", "position": 2},
        ],
    },
    "crud_update": {
        "name": "Update Resource",
        "description": "Update an existing resource via PUT/PATCH",
        "http_methods": ["PUT", "PATCH"],
        "steps": [
            {"key": "validate", "type": "validation", "position": 0},
            {"key": "call_api", "type": "api_call", "position": 1},
            {"key": "handle_response", "type": "transform", "position": 2},
        ],
    },
    "crud_delete": {
        "name": "Delete Resource",
        "description": "Delete a resource via DELETE",
        "http_methods": ["DELETE"],
        "steps": [
            {"key": "call_api", "type": "api_call", "position": 0},
            {"key": "handle_response", "type": "transform", "position": 1},
        ],
    },
    "search_filter": {
        "name": "Search/Filter Resources",
        "description": "Search resources with query parameters",
        "http_methods": ["GET"],
        "keywords": ["search", "filter", "query", "find"],
        "steps": [
            {"key": "build_query", "type": "transform", "position": 0},
            {"key": "call_api", "type": "api_call", "position": 1},
            {"key": "paginate", "type": "pagination", "position": 2},
            {"key": "handle_response", "type": "transform", "position": 3},
        ],
    },
    "nested_resource": {
        "name": "Nested Resource Access",
        "description": "Access sub-resources (e.g., /users/{id}/orders)",
        "http_methods": ["GET", "POST"],
        "path_pattern": r".*\{[^}]+\}/[^/]+$",  # Has {id} then more path
        "steps": [
            {"key": "resolve_parent", "type": "api_call", "position": 0},
            {"key": "call_api", "type": "api_call", "position": 1},
            {"key": "handle_response", "type": "transform", "position": 2},
        ],
    },
}


@dataclass
class PatternMatch:
    """A cross-provider pattern match result."""
    pattern_key: str
    pattern_name: str
    description: str
    confidence: float  # How well the task matches this pattern
    steps: List[Dict[str, Any]]
    source: str  # "kg" or "builtin"
    provider_examples: List[str]  # Providers where this pattern was used


def infer_pattern_from_task(
    task_description: str,
    http_method: Optional[str] = None,
    endpoint_path: Optional[str] = None,
) -> List[PatternMatch]:
    """
    Infer matching workflow patterns from task characteristics.
    
    This is used when no provider-specific templates are found.
    Matches based on:
    - HTTP method (POST -> create, DELETE -> delete, etc.)
    - Endpoint path structure ({id} -> single resource, etc.)
    - Keywords in task description
    
    Args:
        task_description: Natural language description of the task
        http_method: HTTP method if known (GET, POST, etc.)
        endpoint_path: Endpoint path if known
    
    Returns:
        List of PatternMatch ordered by confidence
    """
    import re

    matches = []
    task_lower = task_description.lower()

    for pattern_key, pattern in STANDARD_PATTERNS.items():
        confidence = 0.0

        # Check HTTP method match
        if http_method:
            if http_method.upper() in pattern.get("http_methods", []):
                confidence += 0.3
            else:
                # Method mismatch is a strong negative signal
                continue

        # Check path pattern match
        if endpoint_path and "path_pattern" in pattern:
            if re.match(pattern["path_pattern"], endpoint_path):
                confidence += 0.2

        # Check keyword match in task description
        keywords = pattern.get("keywords", [])
        if not keywords:
            # Default keywords based on pattern name
            keywords = pattern_key.replace("_", " ").split()

        keyword_matches = sum(1 for kw in keywords if kw in task_lower)
        if keyword_matches > 0:
            confidence += min(0.3, keyword_matches * 0.1)

        # Check for common action words that indicate patterns
        action_map = {
            "crud_create": ["create", "add", "new", "register", "post"],
            "crud_read": ["get", "fetch", "retrieve", "read", "show"],
            "crud_list": ["list", "all", "index", "browse", "enumerate"],
            "crud_update": ["update", "modify", "edit", "change", "patch"],
            "crud_delete": ["delete", "remove", "destroy", "cancel"],
            "search_filter": ["search", "find", "filter", "query", "lookup"],
        }

        if pattern_key in action_map:
            action_matches = sum(1 for word in action_map[pattern_key] if word in task_lower)
            confidence += min(0.2, action_matches * 0.1)

        # Only include if we have some confidence
        if confidence > 0.1:
            matches.append(PatternMatch(
                pattern_key=f"pattern.{pattern_key}",
                pattern_name=pattern["name"],
                description=pattern["description"],
                confidence=min(confidence, 1.0),
                steps=[dict(s) for s in pattern["steps"]],  # Copy to prevent mutation
                source="builtin",
                provider_examples=[],
            ))

    # Sort by confidence
    matches.sort(key=lambda m: m.confidence, reverse=True)

    return matches


def query_kg_patterns(
    task_description: str,
    task_slug: Optional[str] = None,
    http_method: Optional[str] = None,
    top_k: int = 5,
) -> List[PatternMatch]:
    """
    Query the KG for pattern nodes across all providers.
    
    Pattern nodes are provider-agnostic and can transfer knowledge
    between providers. This is the fallback when no provider-specific
    templates are found.
    
    Args:
        task_description: Natural language description of the task
        task_slug: Optional task slug to match
        http_method: Optional HTTP method to filter by
        top_k: Maximum number of matches to return
    
    Returns:
        List of PatternMatch ordered by confidence
    """
    matches = []

    try:
        db.init_schema()
        # V26-006: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            is_postgres = db.get_engine_type() == "postgres"
            cur = conn.cursor()

            # Query pattern nodes from KG
            if is_postgres:
                cur.execute("""
                    SELECT id, key, name, description, properties, embedding
                    FROM kg.nodes
                    WHERE node_type = %s
                    ORDER BY usage_count DESC, confidence_score DESC
                    LIMIT %s
                """, (KGNodeType.PATTERN.value, top_k * 2))
            else:
                cur.execute("""
                    SELECT id, key, name, description, properties, embedding
                    FROM kg_nodes
                    WHERE node_type = ?
                    ORDER BY usage_count DESC, confidence_score DESC
                    LIMIT ?
                """, (KGNodeType.PATTERN.value, top_k * 2))

            candidates = cur.fetchall()

            # Compute embedding for task description
            task_embedding = _compute_embedding(task_description) if task_description else None

            for row in candidates:
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

                # Compute similarity score
                similarity_score = 0.5
                if task_embedding and candidate_embedding:
                    try:
                        dot_product = sum(a * b for a, b in zip(task_embedding, candidate_embedding))
                        norm_a = sum(a * a for a in task_embedding) ** 0.5
                        norm_b = sum(b * b for b in candidate_embedding) ** 0.5
                        if norm_a > 0 and norm_b > 0:
                            similarity_score = dot_product / (norm_a * norm_b)
                            similarity_score = (similarity_score + 1) / 2
                    except Exception:
                        pass
                else:
                    # V4: Deterministic fallback when embeddings unavailable
                    similarity_score = _deterministic_similarity(task_description, name, description)
                    _increment_embedding_fallback()  # B-005: Track fallback usage

                # Get provider examples (templates that implement this pattern)
                provider_examples = []
                if is_postgres:
                    cur.execute("""
                        SELECT DISTINCT n.provider_code
                        FROM kg.edges e
                        JOIN kg.nodes n ON e.src_node_id = n.id
                        WHERE e.dst_node_id = %s
                        AND e.relation_type = 'implements_pattern'
                        AND n.provider_code IS NOT NULL
                        LIMIT 5
                    """, (node_id,))
                else:
                    cur.execute("""
                        SELECT DISTINCT n.provider_code
                        FROM kg_edges e
                        JOIN kg_nodes n ON e.src_node_id = n.id
                        WHERE e.dst_node_id = ?
                        AND e.relation_type = 'implements_pattern'
                        AND n.provider_code IS NOT NULL
                        LIMIT 5
                    """, (node_id,))
                provider_examples = [r[0] for r in cur.fetchall() if r[0]]

                steps = properties.get("steps", [])

                matches.append(PatternMatch(
                    pattern_key=key,
                    pattern_name=name,
                    description=description,
                    confidence=similarity_score,
                    steps=steps,
                    source="kg",
                    provider_examples=provider_examples,
                ))

    except Exception as e:
        logger.warning(f"KG pattern query failed: {e}")

    # Also include builtin pattern matches
    builtin_matches = infer_pattern_from_task(
        task_description,
        http_method=http_method,
    )

    # Merge and deduplicate (KG patterns take precedence)
    seen_keys = {m.pattern_key for m in matches}
    for builtin in builtin_matches:
        if builtin.pattern_key not in seen_keys:
            matches.append(builtin)

    # Sort by confidence
    matches.sort(key=lambda m: m.confidence, reverse=True)

    return matches[:top_k]


def query_templates_with_pattern_fallback(
    provider_code: str,
    task_description: str,
    known_entities: Optional[List[str]] = None,
    known_endpoints: Optional[List[str]] = None,
    http_method: Optional[str] = None,
    top_k: int = 5,
    similarity_threshold: float = 0.6,
) -> Tuple[List[KGWorkflowTemplate], List[PatternMatch], str]:
    """
    Query templates with automatic fallback to pattern matching.
    
    This is the recommended high-level API that implements the full
    cross-provider learning strategy:
    
    1. First, try to find provider-specific templates
    2. If none found or scores too low, fall back to pattern matching
    3. Return both matches and indicate which source was used
    
    Args:
        provider_code: Provider to search first
        task_description: Natural language task description
        known_entities: Optional entity names to boost
        known_endpoints: Optional endpoint paths to boost
        http_method: Optional HTTP method for pattern matching
        top_k: Max results per category
        similarity_threshold: Min score for templates
    
    Returns:
        Tuple of (templates, patterns, source) where:
        - templates: Provider-specific template matches
        - patterns: Cross-provider pattern matches
        - source: "exact", "pattern", or "combined"
    """
    # Try provider-specific templates first
    templates = query_workflow_templates(
        provider_code=provider_code,
        task_description=task_description,
        known_entities=known_entities,
        known_endpoints=known_endpoints,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
    )

    # If we have good provider-specific matches, use them
    if templates:
        logger.info(f"Found {len(templates)} provider-specific templates for {provider_code}")
        return templates, [], "exact"

    # Fall back to pattern matching
    logger.info(f"No templates for {provider_code}, falling back to pattern matching")
    patterns = query_kg_patterns(
        task_description=task_description,
        http_method=http_method,
        top_k=top_k,
    )

    if patterns:
        logger.info(f"Found {len(patterns)} pattern matches for task")
        return [], patterns, "pattern"

    # Nothing found
    logger.warning(f"No templates or patterns found for provider={provider_code}")
    return [], [], "none"


# =============================================================================
# Graph Traversal for Task Matching (M5 WS2-T2)
# =============================================================================
# Uses graph-specific algorithms (BFS, DFS) for knowledge graph queries,
# NOT semantic/embedding similarity. The KG structure is what matters.

@dataclass
class GraphTaskMatch:
    """A task found via graph traversal."""
    task_key: str
    task_description: str
    provider_code: Optional[str]
    graph_distance: int  # Hop count from starting node
    path: List[str]  # Node keys in the traversal path
    relation_types: List[str]  # Edge types traversed
    associated_templates: List[str]  # Template keys linked to this task


def _bfs_find_related_nodes(
    cur,
    start_node_id: int,
    target_node_type: str,
    max_depth: int,
    is_postgres: bool,
) -> List[Tuple[int, int, List[int], List[str]]]:
    """
    Breadth-First Search to find nodes of a given type.
    
    Args:
        cur: Database cursor
        start_node_id: Node ID to start traversal from
        target_node_type: Type of nodes to find (e.g., "task", "workflow_template")
        max_depth: Maximum hop count
        is_postgres: Whether using Postgres or SQLite
    
    Returns:
        List of (node_id, distance, path_ids, relation_types) tuples
    """
    visited = {start_node_id}
    queue = [(start_node_id, 0, [start_node_id], [])]  # (node_id, depth, path, relations)
    results = []

    while queue:
        current_id, depth, path, relations = queue.pop(0)

        if depth >= max_depth:
            continue

        # Get all connected nodes (both directions)
        if is_postgres:
            cur.execute("""
                SELECT e.dst_node_id, n.node_type, e.relation_type
                FROM kg.edges e
                JOIN kg.nodes n ON e.dst_node_id = n.id
                WHERE e.src_node_id = %s
                UNION
                SELECT e.src_node_id, n.node_type, e.relation_type
                FROM kg.edges e
                JOIN kg.nodes n ON e.src_node_id = n.id
                WHERE e.dst_node_id = %s
            """, (current_id, current_id))
        else:
            cur.execute("""
                SELECT e.dst_node_id, n.node_type, e.relation_type
                FROM kg_edges e
                JOIN kg_nodes n ON e.dst_node_id = n.id
                WHERE e.src_node_id = ?
                UNION
                SELECT e.src_node_id, n.node_type, e.relation_type
                FROM kg_edges e
                JOIN kg_nodes n ON e.src_node_id = n.id
                WHERE e.dst_node_id = ?
            """, (current_id, current_id))

        neighbors = cur.fetchall()

        for neighbor_id, node_type, relation_type in neighbors:
            if neighbor_id in visited:
                continue

            visited.add(neighbor_id)
            new_path = path + [neighbor_id]
            new_relations = relations + [relation_type]

            # Found a target node
            if node_type == target_node_type:
                results.append((neighbor_id, depth + 1, new_path, new_relations))

            # Continue traversal
            queue.append((neighbor_id, depth + 1, new_path, new_relations))

    return results


def _dfs_find_paths(
    cur,
    start_node_id: int,
    end_node_id: int,
    max_depth: int,
    is_postgres: bool,
) -> List[Tuple[List[int], List[str]]]:
    """
    Depth-First Search to find all paths between two nodes.
    
    Args:
        cur: Database cursor
        start_node_id: Starting node ID
        end_node_id: Target node ID
        max_depth: Maximum path length
        is_postgres: Whether using Postgres or SQLite
    
    Returns:
        List of (path_ids, relation_types) tuples
    """
    paths = []
    stack = [(start_node_id, [start_node_id], [], {start_node_id})]

    while stack:
        current_id, path, relations, visited = stack.pop()

        if len(path) > max_depth:
            continue

        if current_id == end_node_id and len(path) > 1:
            paths.append((path, relations))
            continue

        # Get neighbors
        if is_postgres:
            cur.execute("""
                SELECT e.dst_node_id, e.relation_type
                FROM kg.edges e
                WHERE e.src_node_id = %s
                UNION
                SELECT e.src_node_id, e.relation_type
                FROM kg.edges e
                WHERE e.dst_node_id = %s
            """, (current_id, current_id))
        else:
            cur.execute("""
                SELECT e.dst_node_id, e.relation_type
                FROM kg_edges e
                WHERE e.src_node_id = ?
                UNION
                SELECT e.src_node_id, e.relation_type
                FROM kg_edges e
                WHERE e.dst_node_id = ?
            """, (current_id, current_id))

        for neighbor_id, relation_type in cur.fetchall():
            if neighbor_id not in visited:
                new_visited = visited | {neighbor_id}
                stack.append((
                    neighbor_id,
                    path + [neighbor_id],
                    relations + [relation_type],
                    new_visited,
                ))

    return paths


def find_related_tasks_via_graph(
    entity_name: Optional[str] = None,
    endpoint_path: Optional[str] = None,
    pattern_key: Optional[str] = None,
    provider_code: Optional[str] = None,
    max_depth: int = 3,
    top_k: int = 10,
) -> List[GraphTaskMatch]:
    """
    Find related tasks using graph traversal (BFS).
    
    This uses the KG structure to find tasks that share:
    - Common entities (produces/consumes same entity)
    - Common endpoints (uses same API endpoint)
    - Common patterns (implements same workflow pattern)
    
    Graph relationships are what matter here, NOT embedding similarity.
    
    Args:
        entity_name: Find tasks related to this entity
        endpoint_path: Find tasks using this endpoint
        pattern_key: Find tasks implementing this pattern
        provider_code: Optional filter by provider
        max_depth: Maximum graph traversal depth (hops)
        top_k: Maximum results to return
    
    Returns:
        List of GraphTaskMatch ordered by graph distance (closest first)
    """
    matches = []

    try:
        db.init_schema()
        # V26-006: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            is_postgres = db.get_engine_type() == "postgres"
            cur = conn.cursor()

            # Find starting node based on provided anchor
            start_node_id = None

            if entity_name:
                # Find entity node
                if is_postgres:
                    cur.execute("""
                        SELECT id FROM kg.nodes
                        WHERE node_type = %s AND name = %s
                        LIMIT 1
                    """, (KGNodeType.ENTITY.value, entity_name))
                else:
                    cur.execute("""
                        SELECT id FROM kg_nodes
                        WHERE node_type = ? AND name = ?
                        LIMIT 1
                    """, (KGNodeType.ENTITY.value, entity_name))
                result = cur.fetchone()
                if result:
                    start_node_id = result[0]

            elif endpoint_path:
                # Find endpoint node
                if is_postgres:
                    cur.execute("""
                        SELECT id FROM kg.nodes
                        WHERE node_type = %s AND key LIKE %s
                        LIMIT 1
                    """, (KGNodeType.ENDPOINT.value, f"%{endpoint_path}%"))
                else:
                    cur.execute("""
                        SELECT id FROM kg_nodes
                        WHERE node_type = ? AND key LIKE ?
                        LIMIT 1
                    """, (KGNodeType.ENDPOINT.value, f"%{endpoint_path}%"))
                result = cur.fetchone()
                if result:
                    start_node_id = result[0]

            elif pattern_key:
                # Find pattern node
                if is_postgres:
                    cur.execute("""
                        SELECT id FROM kg.nodes
                        WHERE node_type = %s AND key = %s
                        LIMIT 1
                    """, (KGNodeType.PATTERN.value, pattern_key))
                else:
                    cur.execute("""
                        SELECT id FROM kg_nodes
                        WHERE node_type = ? AND key = ?
                        LIMIT 1
                    """, (KGNodeType.PATTERN.value, pattern_key))
                result = cur.fetchone()
                if result:
                    start_node_id = result[0]

            if not start_node_id:
                logger.warning("No starting node found for graph traversal")
                return []

            # BFS to find task nodes
            task_results = _bfs_find_related_nodes(
                cur,
                start_node_id,
                KGNodeType.TASK.value,
                max_depth,
                is_postgres,
            )

            # Convert node IDs to keys and build matches
            for node_id, distance, path_ids, relation_types in task_results:
                # Get node details
                if is_postgres:
                    cur.execute("""
                        SELECT key, name, description, provider_code
                        FROM kg.nodes WHERE id = %s
                    """, (node_id,))
                else:
                    cur.execute("""
                        SELECT key, name, description, provider_code
                        FROM kg_nodes WHERE id = ?
                    """, (node_id,))

                row = cur.fetchone()
                if not row:
                    continue

                key, name, description, node_provider = row

                # Filter by provider if specified
                if provider_code and node_provider != provider_code:
                    continue

                # Get path keys
                path_keys = []
                for pid in path_ids:
                    if is_postgres:
                        cur.execute("SELECT key FROM kg.nodes WHERE id = %s", (pid,))
                    else:
                        cur.execute("SELECT key FROM kg_nodes WHERE id = ?", (pid,))
                    r = cur.fetchone()
                    if r:
                        path_keys.append(r[0])

                # Get associated templates
                if is_postgres:
                    cur.execute("""
                        SELECT n.key FROM kg.edges e
                        JOIN kg.nodes n ON e.dst_node_id = n.id
                        WHERE e.src_node_id = %s AND n.node_type = %s
                        LIMIT 5
                    """, (node_id, KGNodeType.WORKFLOW_TEMPLATE.value))
                else:
                    cur.execute("""
                        SELECT n.key FROM kg_edges e
                        JOIN kg_nodes n ON e.dst_node_id = n.id
                        WHERE e.src_node_id = ? AND n.node_type = ?
                        LIMIT 5
                    """, (node_id, KGNodeType.WORKFLOW_TEMPLATE.value))
                templates = [r[0] for r in cur.fetchall()]

                matches.append(GraphTaskMatch(
                    task_key=key,
                    task_description=description or name,
                    provider_code=node_provider,
                    graph_distance=distance,
                    path=path_keys,
                    relation_types=relation_types,
                    associated_templates=templates,
                ))

            # Sort by distance (closest first)
            matches.sort(key=lambda m: m.graph_distance)

            logger.info(f"Found {len(matches)} related tasks via graph traversal")

            return matches[:top_k]

    except Exception as e:
        logger.error(f"Graph traversal failed: {e}")
        return []


def find_cross_provider_tasks_via_pattern(
    pattern_key: str,
    exclude_provider: Optional[str] = None,
    max_depth: int = 2,
) -> Dict[str, List[GraphTaskMatch]]:
    """
    Find tasks across providers that implement the same pattern.
    
    Uses graph traversal to find:
    pattern -> [implements_pattern] <- other_templates -> tasks
    
    This enables cross-provider learning by finding how different
    providers implement the same workflow pattern.
    
    Args:
        pattern_key: Pattern key (e.g., "pattern.crud_create")
        exclude_provider: Provider to exclude from results
        max_depth: Maximum traversal depth
    
    Returns:
        Dict mapping provider_code -> list of tasks
    """
    matches = find_related_tasks_via_graph(
        pattern_key=pattern_key,
        max_depth=max_depth,
        top_k=50,  # Get more, then group
    )

    # Group by provider
    by_provider: Dict[str, List[GraphTaskMatch]] = {}
    for match in matches:
        if exclude_provider and match.provider_code == exclude_provider:
            continue

        provider = match.provider_code or "unknown"
        if provider not in by_provider:
            by_provider[provider] = []
        by_provider[provider].append(match)

    return by_provider


def get_shortest_path(
    from_node_key: str,
    to_node_key: str,
    max_depth: int = 5,
) -> Optional[Tuple[List[str], List[str]]]:
    """
    Find the shortest path between two nodes in the KG.
    
    Uses BFS for shortest path discovery.
    
    Args:
        from_node_key: Starting node key
        to_node_key: Target node key
        max_depth: Maximum path length to search
    
    Returns:
        Tuple of (path_keys, relation_types) or None if no path found
    """
    try:
        db.init_schema()
        # V26-006: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            is_postgres = db.get_engine_type() == "postgres"
            cur = conn.cursor()

            # Get node IDs
            if is_postgres:
                cur.execute("SELECT id FROM kg.nodes WHERE key = %s", (from_node_key,))
            else:
                cur.execute("SELECT id FROM kg_nodes WHERE key = ?", (from_node_key,))
        from_result = cur.fetchone()

        if is_postgres:
            cur.execute("SELECT id FROM kg.nodes WHERE key = %s", (to_node_key,))
        else:
            cur.execute("SELECT id FROM kg_nodes WHERE key = ?", (to_node_key,))
        to_result = cur.fetchone()

        if not from_result or not to_result:
            return None

        from_id, to_id = from_result[0], to_result[0]

        # BFS for shortest path
        visited = {from_id}
        queue = [(from_id, [from_id], [])]

        while queue:
            current_id, path, relations = queue.pop(0)

            if len(path) > max_depth:
                continue

            if current_id == to_id:
                # Convert IDs to keys
                path_keys = []
                for pid in path:
                    if is_postgres:
                        cur.execute("SELECT key FROM kg.nodes WHERE id = %s", (pid,))
                    else:
                        cur.execute("SELECT key FROM kg_nodes WHERE id = ?", (pid,))
                    r = cur.fetchone()
                    if r:
                        path_keys.append(r[0])
                return (path_keys, relations)

            # Get neighbors
            if is_postgres:
                cur.execute("""
                    SELECT e.dst_node_id, e.relation_type FROM kg.edges e
                    WHERE e.src_node_id = %s
                    UNION
                    SELECT e.src_node_id, e.relation_type FROM kg.edges e
                    WHERE e.dst_node_id = %s
                """, (current_id, current_id))
            else:
                cur.execute("""
                    SELECT e.dst_node_id, e.relation_type FROM kg_edges e
                    WHERE e.src_node_id = ?
                    UNION
                    SELECT e.src_node_id, e.relation_type FROM kg_edges e
                    WHERE e.dst_node_id = ?
                """, (current_id, current_id))

            for neighbor_id, relation_type in cur.fetchall():
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, path + [neighbor_id], relations + [relation_type]))

        return None

    except Exception as e:
        logger.error(f"Shortest path query failed: {e}")
        return None


def get_kg_node_count() -> Dict[str, int]:
    """
    Get counts of nodes by type in the KG.
    
    Useful for health checks and diagnostics.
    
    Returns:
        Dict mapping node_type -> count
    """
    try:
        db.init_schema()
        # V26-006: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            is_postgres = db.get_engine_type() == "postgres"
            cur = conn.cursor()

            if is_postgres:
                cur.execute("""
                    SELECT node_type, COUNT(*)
                    FROM kg.nodes
                    GROUP BY node_type
                """)
            else:
                cur.execute("""
                    SELECT node_type, COUNT(*)
                    FROM kg_nodes
                    GROUP BY node_type
                """)

            return {row[0]: row[1] for row in cur.fetchall()}

    except Exception as e:
        logger.error(f"Failed to get node counts: {e}")
        return {}


def get_node_edge_count(node_id: int) -> int:
    """
    Get the count of edges connected to a node.
    
    V2: Used for graph-based scoring - nodes with more connections
    are considered more relevant/established.
    
    Args:
        node_id: The ID of the node to count edges for
        
    Returns:
        Total count of edges (both incoming and outgoing)
    """
    try:
        db.init_schema()
        # V26-006: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            is_postgres = db.get_engine_type() == "postgres"
            cur = conn.cursor()

            if is_postgres:
                cur.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT id FROM kg.edges WHERE src_node_id = %s
                        UNION ALL
                        SELECT id FROM kg.edges WHERE dst_node_id = %s
                    ) AS edges
                """, (node_id, node_id))
            else:
                cur.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT id FROM kg_edges WHERE src_node_id = ?
                        UNION ALL
                        SELECT id FROM kg_edges WHERE dst_node_id = ?
                    ) AS edges
                """, (node_id, node_id))

            result = cur.fetchone()
            return result[0] if result else 0

    except Exception as e:
        logger.debug(f"Failed to get edge count for node {node_id}: {e}")
        return 0
