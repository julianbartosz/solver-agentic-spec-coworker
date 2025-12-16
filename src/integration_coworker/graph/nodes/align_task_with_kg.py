"""
align_task_with_kg node – GraphRAG-based workflow template retrieval.

Design Doc Section: Appendix C.3.7

This node queries the persistent Knowledge Graph (kg schema) for workflow
templates that match the current provider + task. It uses graph-first filtering
(provider_code, known entities) and embedding similarity for ranking.

**V2 Architecture**:
- Legacy templates REMOVED (per ADR-0004)
- System uses smarter generic fallback based on HTTP method when KG is empty
- First run uses inference; subsequent runs benefit from KG learning
- Run 'scripts/bootstrap_kg.py' to seed initial templates

**V1 Hybrid GraphRAG Enhancement**:
- Uses retrieval.semantic_search for embedding-based scoring
- Combines graph score (40%) + embedding score (40%) + exact-match bonus (20%)
- Preserves KG BFS/DFS for structural queries; semantic search augments, not replaces
"""

import os
import re
import logging
from typing import List, Optional, Tuple

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import (
    IntegrationFlowNode,
    IntegrationFlowEdge,
    KGWorkflowTemplate,
    WorkflowTemplate,
    Endpoint,
)
from integration_coworker.kg import (
    query_workflow_templates,
    query_templates_with_pattern_fallback,
    STANDARD_PATTERNS,
)
from integration_coworker.kg.pattern_discovery import record_pattern_match

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# V2 Hybrid GraphRAG Scoring Functions
# Per V1_GAP_CLOSURE_PLAN.md P0: 40% graph + 40% embedding + 20% exact-match
# V2: Uses configurable weights per provider (ADR-0006)
# ---------------------------------------------------------------------------

def _compute_embedding_score(
    task_description: str,
    template: dict,
    strict_mode: bool = False,
) -> float:
    """
    Compute semantic similarity between task description and template.
    
    Uses the retrieval module's embedding functions.
    Returns score in [0, 1] range.
    
    V2 Changes:
    - Returns 0.0 on failure instead of 0.5 (per ADR-0006)
    - Supports strict_mode to raise EmbeddingUnavailableError
    
    Args:
        task_description: The task to match against template
        template: Template dict with optional "embedding" field
        strict_mode: If True, raise error when embeddings unavailable
        
    Returns:
        Score in [0, 1] range
        
    Raises:
        EmbeddingUnavailableError: If strict_mode and embeddings unavailable
    """
    try:
        from integration_coworker.retrieval.semantic_search import (
            compute_embedding,
            cosine_similarity,
        )
        from integration_coworker.runtime.exceptions import EmbeddingUnavailableError

        task_emb = compute_embedding(task_description)
        if not task_emb:
            if strict_mode:
                raise EmbeddingUnavailableError(
                    "Failed to compute task embedding. "
                    "Check OPENAI_API_KEY or disable strict mode."
                )
            logger.debug("Embedding unavailable for task; using 0.0 score")
            return 0.0  # V2: Return 0.0 on failure

        # Get template text for embedding
        template_text = f"{template.get('name', '')} {template.get('description', '')}"
        template_emb = template.get("embedding")

        if not template_emb:
            # Compute template embedding on the fly
            template_emb = compute_embedding(template_text)

        if task_emb and template_emb:
            return max(0.0, cosine_similarity(task_emb, template_emb))
        
        if strict_mode:
            from integration_coworker.runtime.exceptions import EmbeddingUnavailableError
            raise EmbeddingUnavailableError("Template embedding unavailable")
        return 0.0  # V2: Return 0.0 on failure
        
    except Exception as e:
        # Re-raise EmbeddingUnavailableError in strict mode
        if "EmbeddingUnavailableError" in type(e).__name__:
            raise
        logger.debug(f"Embedding score computation failed: {e}")
        if strict_mode:
            from integration_coworker.runtime.exceptions import EmbeddingUnavailableError
            raise EmbeddingUnavailableError(str(e)) from e
        return 0.0  # V2: Return 0.0 on failure


def _compute_graph_score(
    template: dict,
    entities: List[str],
    provider_code: Optional[str] = None,
) -> float:
    """
    Compute graph-based relevance score using actual KG edges.
    
    V2.1 (Section 13.8): Queries kg_edges table for real relationship data.
    
    Scoring factors:
    - Edge density: More edges to relevant entities = higher score
    - Edge types: uses_endpoint (1.0), references_entity (0.7), belongs_to (0.3)
    - Provider match: Bonus if template is from same provider
    
    Args:
        template: Template dict with node_id field
        entities: List of entity names from task understanding
        provider_code: Current provider for bonus scoring
        
    Returns:
        Score in [0, 1] range
    """
    template_node_id = template.get("node_id")
    if not template_node_id:
        # Fall back to text matching if no node_id
        return _compute_graph_score_fallback(template, entities, provider_code)
    
    try:
        from integration_coworker.persistence import db
        
        # Edge type weights per ADR-0004
        EDGE_WEIGHTS = {
            "uses_endpoint": 1.0,
            "references_entity": 0.7,
            "has_step": 0.5,
            "belongs_to_provider": 0.3,
            "similar_to": 0.2,
        }
        
        db.init_schema()
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        # Count edges by type from template node
        if is_postgres:
            cur.execute("""
                SELECT e.relation_type, COUNT(*) as cnt
                FROM kg.edges e
                WHERE e.src_node_id = %s OR e.dst_node_id = %s
                GROUP BY e.relation_type
            """, (template_node_id, template_node_id))
        else:
            cur.execute("""
                SELECT relation_type, COUNT(*) as cnt
                FROM kg_edges
                WHERE src_node_id = ? OR dst_node_id = ?
                GROUP BY relation_type
            """, (template_node_id, template_node_id))
        
        edge_counts = {row[0]: row[1] for row in cur.fetchall()}
        
        # Count edges to entity nodes if we have entities
        entity_edge_count = 0
        if entities and is_postgres:
            # Only do this for Postgres which has proper node lookup
            entity_placeholders = ','.join(['%s'] * len(entities))
            cur.execute(f"""
                SELECT COUNT(*) FROM kg.edges e
                JOIN kg.nodes n ON e.dst_node_id = n.id
                WHERE e.src_node_id = %s
                AND lower(n.key) IN ({entity_placeholders})
            """, [template_node_id] + [e.lower() for e in entities])
            entity_edge_count = cur.fetchone()[0]
        
        if not is_postgres:
            conn.close()
        
        # Calculate weighted score
        total_weight = 0.0
        for edge_type, count in edge_counts.items():
            weight = EDGE_WEIGHTS.get(edge_type, 0.1)
            total_weight += count * weight
        
        # Add entity relevance bonus
        if entities:
            entity_bonus = min(1.0, entity_edge_count / len(entities))
            total_weight += entity_bonus * 2.0
        
        # Normalize to [0, 1] - assuming max reasonable score is 10
        normalized_score = min(1.0, total_weight / 10.0)
        
        # Provider match bonus
        template_provider = template.get("provider_code")
        if provider_code and template_provider == provider_code:
            normalized_score = min(1.0, normalized_score + 0.1)
        
        return normalized_score
        
    except Exception as e:
        logger.warning(f"KG edge query failed, falling back to text match: {e}")
        return _compute_graph_score_fallback(template, entities, provider_code)


def _compute_graph_score_fallback(
    template: dict,
    entities: List[str],
    provider_code: Optional[str] = None,
) -> float:
    """
    Fallback graph score using text matching.
    
    V2.1: Used when KG edge query fails or template has no node_id.
    """
    score = 0.0

    # Provider match bonus
    template_provider = template.get("provider_code")
    if template_provider and template_provider == provider_code:
        score += 0.3
    elif provider_code:
        score += 0.1

    template_name = template.get("name", "").lower()
    template_desc = template.get("description", "").lower()
    template_steps = " ".join(s.get("label", "") for s in template.get("steps", []))
    template_text = f"{template_name} {template_desc} {template_steps}".lower()

    # Entity coverage bonus
    if entities:
        matching_entities = sum(
            1 for entity in entities
            if entity.lower() in template_text
        )
        entity_coverage = matching_entities / len(entities) if entities else 0
        score += 0.3 * entity_coverage

    return min(1.0, score)


def _compute_exact_match_bonus(template: dict, task_description: str) -> float:
    """
    Compute bonus for exact keyword matches.
    
    Provides up to 0.2 bonus for:
    - Provider match
    - Action word match (create, get, update, delete)
    - Resource word match
    """
    bonus = 0.0
    task_lower = task_description.lower()
    template_name = template.get("name", "").lower()
    template_id = template.get("template_id", "").lower()

    # Action word matching
    action_words = ["create", "get", "update", "delete", "list", "confirm", "cancel"]
    for action in action_words:
        if action in task_lower and action in template_name:
            bonus += 0.1
            break

    # Resource word matching
    resource_words = ["payment", "checkout", "session", "customer", "subscription", "invoice"]
    for resource in resource_words:
        if resource in task_lower and (resource in template_name or resource in template_id):
            bonus += 0.1
            break

    return min(0.2, bonus)


def _compute_combined_score(
    template: dict,
    task_description: str,
    entities: List[str],
    provider_code: Optional[str] = None,
    strict_embeddings: bool = False,
) -> float:
    """
    Compute combined score using configurable Hybrid GraphRAG formula.
    
    V2: Uses per-provider weights from config instead of hardcoded 40/40/20.
    
    Args:
        template: Template dict to score
        task_description: Task description to match against
        entities: List of known entity names for graph scoring
        provider_code: Optional provider for weight lookup and graph scoring
        strict_embeddings: If True, raise error when embeddings unavailable
        
    Returns:
        Combined score in [0, 1] range
    """
    from integration_coworker.config import get_scoring_weights
    
    weights = get_scoring_weights(provider_code)
    
    graph_score = _compute_graph_score(template, entities, provider_code)
    embedding_score = _compute_embedding_score(
        task_description, template, strict_mode=strict_embeddings
    )
    exact_match_bonus = _compute_exact_match_bonus(template, task_description)

    combined = (
        graph_score * weights["graph"] +
        embedding_score * weights["embedding"] +
        exact_match_bonus * weights["exact_match"]
    )
    return min(1.0, combined)


def _query_kg_templates(
    provider: str,
    task_slug: str,
    task_description: str,
    known_endpoints: Optional[List[str]] = None,
    known_entities: Optional[List[str]] = None,
    primary_http_method: Optional[str] = None,
) -> Tuple[List[dict], List[dict], str]:
    """
    Query the persistent KG for workflow templates with pattern fallback.
    
    V1.1 FT-005: Cross-provider pattern matching
    - First queries provider-specific templates
    - Falls back to cross-provider patterns if no templates found
    - Returns (templates, patterns, source) tuple
    
    Returns:
        Tuple of:
        - template dicts (matching legacy format)
        - pattern dicts (from pattern matching)
        - source: "exact", "pattern", or "combined"
    """
    # Use the enhanced API with pattern fallback
    templates, patterns, source = query_templates_with_pattern_fallback(
        provider_code=provider,
        task_description=task_description,
        known_entities=known_entities,
        known_endpoints=known_endpoints,
        http_method=primary_http_method,
        top_k=10,
        similarity_threshold=0.1,  # Lower threshold, let hybrid scoring rank
    )
    
    # Convert KGWorkflowTemplate domain models to legacy dict format
    template_dicts = []
    for tmpl in templates:
        steps = []
        for step in (tmpl.steps or []):
            steps.append({
                "key": step.step_key,
                "type": step.step_type,
                "label": step.label,
                "description": step.description or "",
            })

        template_dict = {
            "template_id": tmpl.template_id,
            "name": tmpl.name,
            "description": tmpl.description or "",
            "steps": steps,
            "embedding": getattr(tmpl, 'embedding', None),
            "provider_code": provider,
            "node_id": getattr(tmpl, 'node_id', None),
        }

        # Compute hybrid score with configurable weights
        template_dict["_combined_score"] = _compute_combined_score(
            template_dict,
            task_description,
            known_entities or [],
            provider_code=provider,
        )

        template_dicts.append(template_dict)

    # Sort by combined score (highest first)
    template_dicts.sort(key=lambda t: t.get("_combined_score", 0), reverse=True)

    # Convert patterns to dict format
    pattern_dicts = []
    for pattern in patterns:
        pattern_dict = {
            "template_id": pattern.pattern_key,
            "name": pattern.pattern_name,
            "description": pattern.description,
            "steps": pattern.steps,
            "_combined_score": pattern.confidence,
            "source": pattern.source,
            "provider_examples": pattern.provider_examples,
        }
        pattern_dicts.append(pattern_dict)

    return template_dicts[:5], pattern_dicts[:5], source


# ---------------------------------------------------------------------------
# P1: Multi-Endpoint Flow Detection
# ---------------------------------------------------------------------------

# Common multi-step patterns in task descriptions
_MULTI_STEP_PATTERNS = [
    # Connectors that indicate sequential operations
    (r"\b(?:and\s+then|then|afterwards?|after\s+that|followed\s+by|subsequently)\b", True),
    # Action pairs that imply multi-step
    (r"\b(?:create|add|insert)\b.*\b(?:send|notify|email|alert)\b", True),
    (r"\b(?:fetch|get|retrieve|load)\b.*\b(?:update|modify|change)\b", True),
    (r"\b(?:validate|check|verify)\b.*\b(?:create|save|store)\b", True),
    (r"\b(?:delete|remove)\b.*\b(?:notify|log|archive)\b", True),
    # Multiple action verbs separated by comma or "and"
    (r"\b(?:create|update|delete|send|fetch|get|list|add)\b.*(?:,|\band\b).*\b(?:create|update|delete|send|fetch|get|list|add)\b", True),
]


def _detect_multi_step_pattern(task_description: str) -> Tuple[bool, List[str]]:
    """
    Detect if task description implies multiple sequential API operations.
    
    Returns:
        Tuple of (is_multi_step, action_sequence)
        - is_multi_step: True if multiple operations detected
        - action_sequence: List of action verbs in order (e.g., ["create", "send"])
    """

    task_lower = task_description.lower()

    # Check for multi-step patterns
    is_multi_step = False
    for pattern, _ in _MULTI_STEP_PATTERNS:
        if re.search(pattern, task_lower, re.IGNORECASE):
            is_multi_step = True
            break

    if not is_multi_step:
        return (False, [])

    # Extract action sequence
    action_keywords = [
        "create", "add", "insert", "post", "submit",
        "update", "modify", "change", "patch", "put",
        "delete", "remove", "destroy",
        "get", "fetch", "retrieve", "load", "list", "find",
        "send", "notify", "email", "alert", "publish",
        "validate", "check", "verify",
    ]

    # Find all action keywords in order of appearance
    actions_found: List[Tuple[int, str]] = []
    for action in action_keywords:
        match = re.search(rf"\b{action}\b", task_lower)
        if match:
            actions_found.append((match.start(), action))

    # Sort by position and extract unique actions
    actions_found.sort(key=lambda x: x[0])
    action_sequence = []
    seen = set()
    for _, action in actions_found:
        # Normalize synonyms
        normalized = _normalize_action(action)
        if normalized not in seen:
            action_sequence.append(normalized)
            seen.add(normalized)

    return (len(action_sequence) > 1, action_sequence)


def _normalize_action(action: str) -> str:
    """Normalize action keywords to canonical forms."""
    action_map = {
        "add": "create", "insert": "create", "post": "create", "submit": "create",
        "modify": "update", "change": "update", "patch": "update", "put": "update",
        "remove": "delete", "destroy": "delete",
        "fetch": "get", "retrieve": "get", "load": "get", "find": "get", "list": "get",
        "notify": "send", "email": "send", "alert": "send", "publish": "send",
        "check": "validate", "verify": "validate",
    }
    return action_map.get(action, action)


def _find_endpoint_for_action(
    endpoints: List[Endpoint],
    action: str,
    task_description: str,
) -> Optional[Endpoint]:
    """
    Find the best matching endpoint for a given action.
    
    Args:
        endpoints: List of available endpoints
        action: Normalized action verb (create, get, update, delete, send, validate)
        task_description: Original task for context
        
    Returns:
        Best matching endpoint or None
    """
    # Map actions to HTTP methods
    action_to_methods = {
        "create": ["POST"],
        "get": ["GET"],
        "update": ["PUT", "PATCH"],
        "delete": ["DELETE"],
        "send": ["POST"],  # Sending typically uses POST
        "validate": ["GET", "POST"],  # Validation can be either
    }

    preferred_methods = action_to_methods.get(action, ["GET", "POST"])

    # Score each endpoint
    scored_endpoints: List[Tuple[float, Endpoint]] = []
    task_lower = task_description.lower()

    for ep in endpoints:
        score = 0.0

        # Method match
        if ep.method.upper() in preferred_methods:
            score += 2.0

        # Path relevance
        path_lower = ep.path.lower()
        if action in path_lower:
            score += 1.0

        # Operation ID or summary match
        op_id = (ep.operation_id or "").lower()
        summary = (ep.summary or "").lower()

        if action in op_id:
            score += 1.5
        if action in summary:
            score += 1.0

        # Check if task keywords appear in endpoint
        task_words = set(task_lower.split())
        path_words = set(path_lower.replace("/", " ").replace("_", " ").replace("-", " ").split())
        overlap = len(task_words & path_words)
        score += overlap * 0.3

        if score > 0:
            scored_endpoints.append((score, ep))

    if not scored_endpoints:
        return None

    # Return highest scoring endpoint
    scored_endpoints.sort(key=lambda x: x[0], reverse=True)
    return scored_endpoints[0][1]


def _infer_multi_endpoint_workflow(
    endpoints: List[Endpoint],
    task_description: str,
    action_sequence: List[str],
) -> List[dict]:
    """
    Infer a multi-step workflow from multiple endpoints.
    
    V2.1 Fix: Generates proper DAG with start/end nodes and canonical types.
    
    Creates a workflow with:
    - Single "start" node
    - One "api_call" node per matched endpoint
    - Transform nodes between API calls for data mapping
    - Single "end" node
    
    Args:
        endpoints: Available endpoints
        task_description: Original task description
        action_sequence: List of normalized actions in order
        
    Returns:
        List of workflow step dictionaries with proper structure
    """
    steps: List[dict] = []
    
    # Step 1: Add start node
    steps.append({
        "key": "start",
        "type": "start",
        "label": "Start",
        "description": "Entry point for multi-endpoint workflow",
    })
    
    # Step 2: Add input validation
    steps.append({
        "key": "validate_input",
        "type": "validation",
        "label": "Validate Input",
        "description": "Validate request parameters and authorization",
    })
    
    api_call_count = 0
    
    # Step 3: Add API call nodes for each action
    for i, action in enumerate(action_sequence):
        endpoint = _find_endpoint_for_action(endpoints, action, task_description)
        
        if endpoint is None:
            logger.warning(f"No endpoint found for action '{action}' in multi-step flow")
            continue
        
        api_call_count += 1
        step_key = f"api_call_{api_call_count}"
        
        # Create API call node (use canonical "api_call" type)
        steps.append({
            "key": step_key,
            "type": "api_call",  # Always use canonical type
            "label": f"{action.capitalize()}: {endpoint.method} {endpoint.path}",
            "description": endpoint.summary or f"{action.capitalize()} via {endpoint.method} {endpoint.path}",
            "endpoint_path": endpoint.path,
            "endpoint_method": endpoint.method,
            "endpoint_operation_id": endpoint.operation_id,
            "action": action,
        })
    
    # Step 4: Add final transform if we have API calls
    if api_call_count > 0:
        steps.append({
            "key": "transform_response",
            "type": "transform",
            "label": "Transform Response",
            "description": "Combine and format results from multi-step flow",
        })
    
    # Step 5: Add end node
    steps.append({
        "key": "end",
        "type": "end",
        "label": "End",
        "description": "Return final result from multi-endpoint workflow",
    })
    
    return steps


def _build_edges_for_multi_endpoint_flow(steps: List[dict]) -> List[dict]:
    """
    Build edges for multi-endpoint workflow steps.
    
    Creates linear edge chain with proper connectivity:
    start → validate → api_call_1 → api_call_2 → ... → transform → end
    
    Args:
        steps: List of step dicts from _infer_multi_endpoint_workflow
        
    Returns:
        List of edge dicts with from_node_key and to_node_key
    """
    edges = []
    
    # Filter to get ordered step keys
    step_keys = [s["key"] for s in steps]
    
    # Create linear edge chain
    for i in range(len(step_keys) - 1):
        edges.append({
            "from_node_key": step_keys[i],
            "to_node_key": step_keys[i + 1],
            "edge_type": "default",
            "condition": None,
        })
    
    return edges


def _infer_workflow_from_endpoint(
    endpoint: Optional[Endpoint],
    task_description: str,
) -> List[dict]:
    """
    Infer workflow steps from endpoint structure (M5: dynamic, no hardcoding).
    
    This is the smarter generic fallback that uses HTTP method and endpoint
    structure to generate an appropriate workflow pattern.
    
    Pattern mapping:
    - POST with request body → validate_input → create_resource → return_created
    - GET with path params → validate_id → fetch_resource → return_or_404
    - GET without params → build_query → list_resources → paginate_response
    - PUT/PATCH → validate_input → fetch_existing → update_resource
    - DELETE → validate_id → delete_resource → confirm_deleted
    """
    steps = [{"key": "start", "type": "start", "label": "Start"}]

    if not endpoint:
        # No endpoint info - use basic fallback
        steps.extend([
            {"key": "validate_input", "type": "validation", "label": "Validate Input"},
            {"key": "call_api", "type": "api_call", "label": "API Call"},
            {"key": "end", "type": "end", "label": "End"},
        ])
        return steps

    method = (endpoint.method or "GET").upper()
    path = endpoint.path or ""
    has_path_params = "{" in path
    # Check for request body by looking at request_schema_id (not request_body attribute)
    has_request_body = bool(getattr(endpoint, 'request_schema_id', None))

    # Infer action from method
    if method == "POST":
        # Create operation
        steps.append({
            "key": "validate_input",
            "type": "validation",
            "label": "Validate Create Input",
            "description": f"Validate request body for {path}",
        })
        steps.append({
            "key": "call_create",
            "type": "api_call",
            "label": f"POST {path}",
            "description": endpoint.summary or f"Create resource at {path}",
        })
        steps.append({
            "key": "transform_response",
            "type": "transform",
            "label": "Extract Created Resource",
            "description": "Return the created resource with ID",
        })

    elif method == "GET" and has_path_params:
        # Fetch single resource
        steps.append({
            "key": "validate_id",
            "type": "validation",
            "label": "Validate Resource ID",
            "description": "Ensure path parameters are provided",
        })
        steps.append({
            "key": "call_get",
            "type": "api_call",
            "label": f"GET {path}",
            "description": endpoint.summary or f"Fetch resource from {path}",
        })
        steps.append({
            "key": "handle_not_found",
            "type": "transform",
            "label": "Handle Not Found",
            "description": "Return resource or 404 error",
        })

    elif method == "GET":
        # List resources
        steps.append({
            "key": "build_query",
            "type": "validation",
            "label": "Build Query Parameters",
            "description": "Construct filter and pagination params",
        })
        steps.append({
            "key": "call_list",
            "type": "api_call",
            "label": f"GET {path}",
            "description": endpoint.summary or f"List resources from {path}",
        })
        steps.append({
            "key": "paginate_response",
            "type": "transform",
            "label": "Handle Pagination",
            "description": "Extract items and pagination metadata",
        })

    elif method in ("PUT", "PATCH"):
        # Update operation
        steps.append({
            "key": "validate_input",
            "type": "validation",
            "label": "Validate Update Input",
            "description": f"Validate ID and request body for {path}",
        })
        steps.append({
            "key": "call_update",
            "type": "api_call",
            "label": f"{method} {path}",
            "description": endpoint.summary or f"Update resource at {path}",
        })
        steps.append({
            "key": "transform_response",
            "type": "transform",
            "label": "Return Updated Resource",
            "description": "Return the updated resource",
        })

    elif method == "DELETE":
        # Delete operation
        steps.append({
            "key": "validate_id",
            "type": "validation",
            "label": "Validate Resource ID",
            "description": "Ensure resource ID is provided",
        })
        steps.append({
            "key": "call_delete",
            "type": "api_call",
            "label": f"DELETE {path}",
            "description": endpoint.summary or f"Delete resource at {path}",
        })
        steps.append({
            "key": "confirm_deleted",
            "type": "transform",
            "label": "Confirm Deletion",
            "description": "Return deletion confirmation",
        })

    else:
        # Unknown method - generic fallback
        steps.append({
            "key": "validate_input",
            "type": "validation",
            "label": "Validate Input",
        })
        steps.append({
            "key": "call_api",
            "type": "api_call",
            "label": f"{method} {path}",
            "description": endpoint.summary or "",
        })
        steps.append({
            "key": "transform_response",
            "type": "transform",
            "label": "Transform Response",
        })

    steps.append({"key": "end", "type": "end", "label": "Return Result"})
    return steps


def _build_fallback_steps() -> List[dict]:
    """Generic 4-step fallback workflow when no templates are found."""
    return [
        {"key": "start", "type": "start", "label": "Start"},
        {"key": "validate_input", "type": "validation", "label": "Validate Input"},
        {"key": "call_api", "type": "api_call", "label": "API Call"},
        {"key": "end", "type": "end", "label": "End"},
    ]


def _find_matching_endpoint(
    endpoints: List[Endpoint],
    task_description: str,
) -> Optional[Endpoint]:
    """
    Find the endpoint that best matches the task description.
    
    Uses simple heuristics:
    1. Look for operationId match
    2. Look for summary/description match
    3. Look for path keyword match
    """
    if not endpoints:
        return None

    task_lower = task_description.lower()

    # Extract action keywords
    action_keywords = {
        "create": ["create", "add", "new", "post"],
        "get": ["get", "fetch", "retrieve", "read", "show"],
        "list": ["list", "all", "search", "find"],
        "update": ["update", "edit", "modify", "patch", "change"],
        "delete": ["delete", "remove", "destroy"],
    }

    detected_action = None
    for action, keywords in action_keywords.items():
        if any(kw in task_lower for kw in keywords):
            detected_action = action
            break

    # Score each endpoint
    best_score = 0
    best_endpoint = None

    for ep in endpoints:
        score = 0
        method = (ep.method or "").upper()
        path = (ep.path or "").lower()
        summary = (ep.summary or "").lower()
        op_id = (ep.operation_id or "").lower()

        # Method-action alignment
        method_action_map = {
            "POST": "create",
            "GET": "get" if "{" in (ep.path or "") else "list",
            "PUT": "update",
            "PATCH": "update",
            "DELETE": "delete",
        }
        if detected_action and method_action_map.get(method) == detected_action:
            score += 3

        # Keyword match in operationId, summary, or path
        task_words = set(task_lower.split())
        for word in task_words:
            if len(word) > 3:  # Skip short words
                if word in op_id:
                    score += 2
                if word in summary:
                    score += 1
                if word in path:
                    score += 1

        if score > best_score:
            best_score = score
            best_endpoint = ep

    return best_endpoint


def align_task_with_kg(state: WorkflowState) -> WorkflowState:
    """
    Reads: integration_task, provider_code, endpoints
    Writes: plan["candidate_templates"], workflow_nodes, workflow_edges

    V2 Architecture (per ADR-0004):
    - Queries KG exclusively (no hardcoded legacy templates)
    - Uses HTTP-method-based inference when KG is empty
    - Populates plan["candidate_templates"] (may be empty, not an error)
    - Builds initial workflow nodes and edges from best template or inference
    - KG learns from each run for future improvement
    
    Run 'scripts/bootstrap_kg.py' to seed common workflow templates.
    """
    if not state.integration_task:
        state.errors.append("No integration_task from understand_task")
        state.completed_steps.append("align_task_with_kg")
        return state

    provider = state.provider_code or "unknown"
    task_slug = state.integration_task.task_slug
    task_description = state.integration_task.description or task_slug

    # Collect known endpoint paths for graph filtering
    known_endpoints: Optional[List[str]] = None
    if state.endpoints:
        known_endpoints = [ep.path for ep in state.endpoints if ep.path]

    # Collect known entity names for hybrid scoring
    known_entities: Optional[List[str]] = None
    if state.entities:
        known_entities = [e.name for e in state.entities if e.name]

    # ---------------------------------------------------------------------------
    # GraphRAG query: graph-first filtering, then hybrid scoring (V1 enhancement)
    # V1.1 FT-005: Now uses pattern fallback for cross-provider learning
    # ---------------------------------------------------------------------------
    logger.info(
        "align_task_with_kg: querying KG for provider=%s, task=%s",
        provider,
        task_slug,
    )
    
    # Determine primary HTTP method from task description for pattern matching
    primary_http_method = None
    task_lower = task_description.lower()
    if any(word in task_lower for word in ["create", "add", "new", "post"]):
        primary_http_method = "POST"
    elif any(word in task_lower for word in ["update", "modify", "edit", "patch"]):
        primary_http_method = "PUT"
    elif any(word in task_lower for word in ["delete", "remove"]):
        primary_http_method = "DELETE"
    elif any(word in task_lower for word in ["get", "fetch", "list", "retrieve"]):
        primary_http_method = "GET"
    
    candidate_templates, candidate_patterns, template_source = _query_kg_templates(
        provider=provider,
        task_slug=task_slug,
        task_description=task_description,
        known_endpoints=known_endpoints,
        known_entities=known_entities,
        primary_http_method=primary_http_method,
    )

    # ---------------------------------------------------------------------------
    # V2 Architecture: KG-only with smart inference fallback
    # V1.1 FT-005: Cross-provider pattern matching for knowledge transfer
    # 
    # On first run (KG empty), use smart inference from endpoint structure
    # OR cross-provider patterns. KG learns from each run.
    # Run 'scripts/bootstrap_kg.py' to seed initial templates if desired.
    # ---------------------------------------------------------------------------
    if not candidate_templates and candidate_patterns:
        # V1.1 FT-005: Use cross-provider pattern when no provider-specific templates
        logger.info(
            "align_task_with_kg: No provider-specific templates for provider=%s. "
            "Using cross-provider pattern matching. Found %d patterns.",
            provider,
            len(candidate_patterns),
        )
    elif not candidate_templates:
        # No templates or patterns - will use smart inference from endpoint
        logger.info(
            "align_task_with_kg: No templates or patterns for provider=%s. "
            "Using smart HTTP-method-based inference. "
            "KG will learn from this run for future use.",
            provider,
        )
        template_source = "inferred"

    state.plan["candidate_templates"] = candidate_templates
    state.plan["candidate_patterns"] = candidate_patterns  # V1.1: Store patterns too
    state.plan["template_source"] = template_source  # Track where template came from

    # ---------------------------------------------------------------------------
    # Build workflow nodes/edges from best template, pattern, or smart fallback
    # V1.1 FT-005: Now considers cross-provider patterns as intermediate option
    # ---------------------------------------------------------------------------
    steps = None
    
    if candidate_templates:
        # Best case: provider-specific template found
        best = candidate_templates[0]
        steps = best.get("steps", [])
        logger.info(
            "align_task_with_kg: selected template=%s with %d steps (source=%s)",
            best.get("template_id", "?"),
            len(steps),
            template_source,
        )
        # V1.1: Track matched pattern for learning
        if best.get("template_id"):
            state.plan["matched_template_id"] = best.get("template_id")
            
            # V1.2 Pattern Learning: Record template match for confidence tracking
            run_id = state.plan.get("run_id") or (state.metadata.get("run_id") if hasattr(state, "metadata") else None)
            if run_id:
                try:
                    record_pattern_match(
                        run_id=run_id,
                        pattern_key=best.get("template_id"),
                        match_score=best.get("score", 0.9),
                        match_method="kg_template",
                        explanation={
                            "provider_code": provider,
                            "task_description": task_description,
                            "http_method": primary_http_method,
                            "step_count": len(steps),
                            "origin": best.get("origin", "seeded"),
                            "source": template_source,
                        },
                    )
                    logger.debug(
                        "align_task_with_kg: recorded template match for template_id=%s",
                        best.get("template_id"),
                    )
                except Exception as e:
                    # Don't fail the workflow if match recording fails
                    logger.warning("Failed to record template match: %s", e)
        
        # Bug #59 fix: Create WorkflowTemplate object and set on state
        # This ensures persist_gold_checkpoint can persist the template
        template_code = best.get("template_id") or best.get("code") or f"template_{provider}"
        template_name = best.get("name") or template_code
        template_desc = best.get("description") or f"Workflow template for {task_description}"
        state.workflow_template = WorkflowTemplate(
            id=None,  # Will be set by persist_gold_checkpoint
            source_system_id=None,  # Will be set by persist_gold_checkpoint
            code=template_code,
            name=template_name,
            description=template_desc,
        )
        logger.info(f"Created workflow_template: code={template_code}, name={template_name}")
    
    elif candidate_patterns:
        # V1.1 FT-005: Use cross-provider pattern
        best_pattern = candidate_patterns[0]
        steps = best_pattern.get("steps", [])
        logger.info(
            "align_task_with_kg: selected pattern=%s with %d steps (source=%s, examples=%s)",
            best_pattern.get("template_id", "?"),
            len(steps),
            best_pattern.get("source", "builtin"),
            best_pattern.get("provider_examples", []),
        )
        # V1.1: Track matched pattern for learning
        state.plan["matched_pattern_id"] = best_pattern.get("template_id")
        
        # V1.2 Pattern Learning: Record pattern match for confidence tracking
        pattern_id = best_pattern.get("template_id")
        if pattern_id:
            # Get run_id from state metadata if available
            run_id = state.plan.get("run_id") or (state.metadata.get("run_id") if hasattr(state, "metadata") else None)
            pattern_origin = best_pattern.get("source", "seeded")  # 'seeded', 'learned', 'builtin'
            if run_id:
                try:
                    record_pattern_match(
                        run_id=run_id,
                        pattern_key=pattern_id,
                        match_score=best_pattern.get("score", 0.8),
                        match_method="cross_provider_pattern",
                        explanation={
                            "provider_code": provider,
                            "task_description": task_description,
                            "http_method": primary_http_method,
                            "step_count": len(steps),
                            "origin": pattern_origin,
                        },
                    )
                    logger.debug(
                        "align_task_with_kg: recorded pattern match for pattern_id=%s, run_id=%s",
                        pattern_id,
                        run_id,
                    )
                except Exception as e:
                    # Don't fail the workflow if match recording fails
                    logger.warning("Failed to record pattern match: %s", e)
    
    if not steps:
        # M5: Use smarter fallback that infers from endpoint structure
        # P1: First check for multi-step patterns ("create X and send Y")
        is_multi_step, action_sequence = _detect_multi_step_pattern(task_description)

        if is_multi_step and state.endpoints and len(action_sequence) > 1:
            logger.info(
                "align_task_with_kg: detected multi-step pattern with actions=%s",
                action_sequence,
            )
            steps = _infer_multi_endpoint_workflow(
                endpoints=state.endpoints,
                task_description=task_description,
                action_sequence=action_sequence,
            )
            if steps:
                logger.info(
                    "align_task_with_kg: generated multi-step workflow with %d steps",
                    len(steps),
                )
            else:
                # Multi-step detection failed to produce steps; fall through to single-endpoint
                is_multi_step = False

        if not is_multi_step or not steps:
            # Single-endpoint fallback
            matching_endpoint = _find_matching_endpoint(
                state.endpoints or [],
                task_description,
            )
            if matching_endpoint:
                logger.info(
                    "align_task_with_kg: inferring workflow from endpoint %s %s",
                    matching_endpoint.method,
                    matching_endpoint.path,
                )
                steps = _infer_workflow_from_endpoint(matching_endpoint, task_description)
            else:
                logger.info("align_task_with_kg: no matching endpoint; using basic fallback")
                steps = _build_fallback_steps()

    # Guard: Ensure steps have start and end nodes (templates/patterns may not include them)
    if steps:
        has_start = any(s.get("type") == "start" or s.get("key") == "start" for s in steps)
        has_end = any(s.get("type") == "end" or s.get("key") == "end" for s in steps)
        
        if not has_start:
            steps.insert(0, {"key": "start", "type": "start", "label": "Start"})
            logger.debug("align_task_with_kg: added missing start node")
        
        if not has_end:
            steps.append({"key": "end", "type": "end", "label": "End"})
            logger.debug("align_task_with_kg: added missing end node")

    # Create IntegrationFlowNode list
    # Note: Multi-step workflows use "id" while legacy uses "key"
    nodes = []
    for i, step in enumerate(steps):
        # Handle both multi-step format (id) and legacy format (key)
        node_key = step.get("id") or step.get("key", f"step_{i}")
        node_type = step.get("type", "api_call")

        # Build config with available metadata
        config = {
            "label": step.get("label") or step.get("description") or node_key,
            "description": step.get("description", ""),
        }

        # Add multi-step specific fields if present
        if "endpoint_path" in step:
            config["endpoint_path"] = step["endpoint_path"]
            config["endpoint_method"] = step.get("endpoint_method", "GET")
            config["endpoint_operation_id"] = step.get("endpoint_operation_id")
        if "action" in step:
            config["action"] = step["action"]
        if "depends_on" in step:
            config["depends_on"] = step["depends_on"]

        node = IntegrationFlowNode(
            id=None,
            task_id=None,
            node_key=node_key,
            node_type=node_type,
            endpoint_id=None,
            entity_id=None,
            position=i,
            config=config,
        )
        nodes.append(node)

    state.workflow_nodes = nodes

    # Create IntegrationFlowEdge list
    # For multi-step workflows, use depends_on; for legacy, use linear order
    edges = []
    for i in range(len(steps) - 1):
        from_key = steps[i].get("id") or steps[i].get("key", f"step_{i}")
        to_key = steps[i + 1].get("id") or steps[i + 1].get("key", f"step_{i+1}")
        edge = IntegrationFlowEdge(
            id=None,
            task_id=None,
            from_node_key=from_key,
            to_node_key=to_key,
            condition=None,
        )
        edges.append(edge)

    state.workflow_edges = edges

    # Bug #59 fix: If no workflow_template was set from KG but we inferred a workflow,
    # create a template for persistence so persist_gold_checkpoint can save it
    if not state.workflow_template and steps:
        # Create an inferred workflow template
        template_source = state.plan.get("template_source", "inferred")
        inferred_code = f"inferred_{provider}_{task_description[:30].replace(' ', '_').lower()}"
        state.workflow_template = WorkflowTemplate(
            id=None,
            source_system_id=None,
            code=inferred_code,
            name=f"Inferred: {task_description[:50]}",
            description=f"Workflow inferred from endpoint structure for task: {task_description}",
        )
        logger.info(f"Created inferred workflow_template: code={inferred_code}")

    state.completed_steps.append("align_task_with_kg")
    return state
