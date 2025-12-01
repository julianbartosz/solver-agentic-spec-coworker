"""
align_task_with_kg node – GraphRAG-based workflow template retrieval.

Design Doc Section: Appendix C.3.7

This node queries the persistent Knowledge Graph (kg schema) for workflow
templates that match the current provider + task. It uses graph-first filtering
(provider_code, known entities) and embedding similarity for ranking.

**M5 Architecture**:
- Legacy templates are DEPRECATED (gated behind USE_LEGACY_TEMPLATES=1, default: OFF)
- System uses smarter generic fallback based on HTTP method when KG is empty
- First run uses inference; subsequent runs benefit from KG learning

**V1 Hybrid GraphRAG Enhancement**:
- Uses retrieval.semantic_search for embedding-based scoring
- Combines graph score (40%) + embedding score (40%) + exact-match bonus (20%)
- Preserves KG BFS/DFS for structural queries; semantic search augments, not replaces

The in-memory fallback is only used if USE_IN_MEMORY_KG_FALLBACK=1 is set,
allowing tests to run without a populated KG. On the demo path, this should
fail loudly if the KG is empty/broken.
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
    Endpoint,
)
from integration_coworker.kg import query_workflow_templates, _check_fallback_enabled

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# V1 Hybrid GraphRAG Scoring Functions
# Per V1_GAP_CLOSURE_PLAN.md P0: 40% graph + 40% embedding + 20% exact-match
# ---------------------------------------------------------------------------

def _compute_embedding_score(task_description: str, template: dict) -> float:
    """
    Compute semantic similarity between task description and template.
    
    Uses the retrieval module's embedding functions.
    Returns score in [0, 1] range.
    """
    try:
        from integration_coworker.retrieval.semantic_search import (
            compute_embedding,
            cosine_similarity,
        )

        task_emb = compute_embedding(task_description)
        if not task_emb:
            return 0.5  # Fallback when embeddings unavailable

        # Get template text for embedding
        template_text = f"{template.get('name', '')} {template.get('description', '')}"
        template_emb = template.get("embedding")

        if not template_emb:
            # Compute template embedding on the fly
            template_emb = compute_embedding(template_text)

        if task_emb and template_emb:
            return max(0.0, cosine_similarity(task_emb, template_emb))
        return 0.5  # Fallback
    except Exception as e:
        logger.debug(f"Embedding score computation failed: {e}")
        return 0.5  # Fallback


def _compute_graph_score(template: dict, entities: List[str]) -> float:
    """
    Compute graph-derived score based on structural matching.
    
    Factors:
    - Entity coverage: how many known entities are referenced in template
    - Provider match: templates with matching provider score higher
    """
    score = 0.3  # Base score

    template_name = template.get("name", "").lower()
    template_desc = template.get("description", "").lower()
    template_text = f"{template_name} {template_desc}"

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
) -> float:
    """
    Compute combined score using Hybrid GraphRAG formula.
    
    Formula: 40% graph + 40% embedding + exact-match bonus (up to 20%)
    """
    graph_score = _compute_graph_score(template, entities)
    embedding_score = _compute_embedding_score(task_description, template)
    exact_match_bonus = _compute_exact_match_bonus(template, task_description)

    combined = (graph_score * 0.4) + (embedding_score * 0.4) + exact_match_bonus
    return min(1.0, combined)


def _check_legacy_templates_enabled() -> bool:
    """
    Check if legacy hardcoded templates are enabled.
    
    M5 Architecture: Legacy templates are DEPRECATED.
    Default is OFF (0). Set USE_LEGACY_TEMPLATES=1 to enable for backwards compat.
    """
    return os.environ.get("USE_LEGACY_TEMPLATES", "0") == "1"


# ---------------------------------------------------------------------------
# Legacy in-memory fallback (DEPRECATED - gated by USE_LEGACY_TEMPLATES env var)
# M5: This is only kept for backwards compatibility. New code should use
# _infer_workflow_from_endpoint() for dynamic pattern inference.
# ---------------------------------------------------------------------------
_LEGACY_WORKFLOW_TEMPLATES = {
    # -------------------------------------------------------------------------
    # Stripe Payment Intents Templates
    # -------------------------------------------------------------------------
    ("stripe", "create_payment_intent"): {
        "template_id": "stripe_payment_intent_v1",
        "name": "Stripe Create Payment Intent",
        "description": "Standard flow for creating a Stripe PaymentIntent",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Validate amount, currency, and payment method types"},
            {"key": "call_create_intent", "type": "api_call", "label": "Create PaymentIntent",
             "description": "POST to /v1/payment_intents"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Extract id, client_secret, and status"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
    },
    ("stripe", "confirm_payment_intent"): {
        "template_id": "stripe_confirm_intent_v1",
        "name": "Stripe Confirm Payment Intent",
        "description": "Flow for confirming a PaymentIntent with payment method",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Validate payment_intent_id and payment_method"},
            {"key": "call_confirm", "type": "api_call", "label": "Confirm PaymentIntent",
             "description": "POST to /v1/payment_intents/{id}/confirm"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Extract status and next_action if required"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
    },
    ("stripe", "get_payment_intent"): {
        "template_id": "stripe_get_intent_v1",
        "name": "Stripe Get Payment Intent",
        "description": "Retrieve an existing PaymentIntent by ID",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Ensure payment_intent_id is provided"},
            {"key": "call_get_intent", "type": "api_call", "label": "Get PaymentIntent",
             "description": "GET /v1/payment_intents/{id}"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Return full PaymentIntent object"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
    },
    ("stripe", "cancel_payment_intent"): {
        "template_id": "stripe_cancel_intent_v1",
        "name": "Stripe Cancel Payment Intent",
        "description": "Cancel a PaymentIntent",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Validate payment_intent_id and cancellation_reason"},
            {"key": "call_cancel", "type": "api_call", "label": "Cancel PaymentIntent",
             "description": "POST to /v1/payment_intents/{id}/cancel"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Confirm cancellation status"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
    },
    # Legacy Stripe checkout template (kept for backwards compatibility)
    ("stripe", "create_checkout_session"): {
        "template_id": "stripe_checkout_v1",
        "name": "Stripe Checkout Session Creation",
        "description": "Standard flow for creating a Stripe checkout session",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Validate amount, currency, and URLs"},
            {"key": "call_create_session", "type": "api_call", "label": "Call Create Session",
             "description": "POST to /v1/checkout/sessions"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Extract session_id and checkout_url"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
    },
    # -------------------------------------------------------------------------
    # Mock Payments Templates
    # -------------------------------------------------------------------------
    ("mock_payments", "create_checkout_session"): {
        "template_id": "mock_checkout_v1",
        "name": "Mock Payments Checkout Session",
        "description": "Standard checkout flow for mock payment provider",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input"},
            {"key": "call_create_session", "type": "api_call", "label": "API Call"},
            {"key": "transform_response", "type": "transform", "label": "Transform"},
            {"key": "end", "type": "end", "label": "End"},
        ],
    },
    ("mock_payments", "get_checkout_session"): {
        "template_id": "mock_get_session_v1",
        "name": "Mock Payments Get Checkout Session",
        "description": "Retrieve existing checkout session by ID from mock provider",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Session ID",
             "description": "Ensure session_id is provided and valid format"},
            {"key": "call_get_session", "type": "api_call", "label": "GET Session",
             "description": "GET /checkout/sessions/{session_id}"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Extract and normalize session details"},
            {"key": "end", "type": "end", "label": "Return Session"},
        ],
    },
}


def _query_kg_templates(
    provider: str,
    task_slug: str,
    task_description: str,
    known_endpoints: Optional[List[str]] = None,
    known_entities: Optional[List[str]] = None,
) -> List[dict]:
    """
    Query the persistent KG for workflow templates.
    Returns list of template dicts (matching legacy format for backwards compat).
    
    V1 Enhancement: Templates include _combined_score field computed via hybrid GraphRAG.
    """
    # Attempt GraphRAG query against persistent KG
    # Note: similarity_threshold is set low (0.2) to work with mock LLM mode
    # where embeddings are unavailable. With real embeddings, scores will be higher.
    kg_templates: List[KGWorkflowTemplate] = query_workflow_templates(
        provider_code=provider,
        task_description=task_description,
        known_entities=known_entities,
        known_endpoints=known_endpoints,
        top_k=10,  # Fetch more candidates for scoring
        similarity_threshold=0.1,  # Lower threshold, let hybrid scoring rank
    )

    # Convert KGWorkflowTemplate domain models to legacy dict format
    results = []
    for tmpl in kg_templates:
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
            "embedding": tmpl.embedding if hasattr(tmpl, 'embedding') else None,
        }

        # Compute hybrid score
        template_dict["_combined_score"] = _compute_combined_score(
            template_dict,
            task_description,
            known_entities or [],
        )

        results.append(template_dict)

    # Sort by combined score (highest first)
    results.sort(key=lambda t: t.get("_combined_score", 0), reverse=True)

    # Return top 5
    return results[:5]


def _legacy_in_memory_lookup(provider: str, task_slug: str) -> List[dict]:
    """
    Fallback to in-memory templates.
    
    M5: DEPRECATED - Gated behind USE_LEGACY_TEMPLATES=1 (default: OFF).
    Only used for backwards compatibility during migration.
    """
    if not _check_legacy_templates_enabled():
        logger.debug("Legacy templates disabled (USE_LEGACY_TEMPLATES=0)")
        return []

    # Exact match
    template_key = (provider, task_slug)
    if template_key in _LEGACY_WORKFLOW_TEMPLATES:
        return [_LEGACY_WORKFLOW_TEMPLATES[template_key]]

    # Partial match by keyword overlap
    matches = []
    for (p, t), tmpl in _LEGACY_WORKFLOW_TEMPLATES.items():
        if p == provider and any(word in t for word in task_slug.split("_")):
            matches.append(tmpl)

    return matches


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
    
    Creates a workflow with multiple api_call nodes connected by data flow.
    
    Args:
        endpoints: Available endpoints
        task_description: Original task description
        action_sequence: List of normalized actions in order
        
    Returns:
        List of workflow step dictionaries
    """
    steps: List[dict] = []
    prev_step_id: Optional[str] = None

    for i, action in enumerate(action_sequence):
        endpoint = _find_endpoint_for_action(endpoints, action, task_description)

        if endpoint is None:
            logger.warning(f"No endpoint found for action '{action}' in multi-step flow")
            continue

        step_id = f"step_{i+1}_{action}"

        # Determine step type based on action
        if action in ("validate", "check"):
            step_type = "validate_input"
        elif action in ("get", "list"):
            step_type = "api_call_fetch"
        elif action in ("create",):
            step_type = "api_call_create"
        elif action in ("update",):
            step_type = "api_call_update"
        elif action in ("delete",):
            step_type = "api_call_delete"
        elif action in ("send",):
            step_type = "api_call_notify"
        else:
            step_type = "api_call"

        step = {
            "id": step_id,
            "type": step_type,
            "action": action,
            "endpoint_path": endpoint.path,
            "endpoint_method": endpoint.method,
            "endpoint_operation_id": endpoint.operation_id,
            "depends_on": [prev_step_id] if prev_step_id else [],
            "description": f"{action.capitalize()} via {endpoint.method} {endpoint.path}",
        }

        steps.append(step)
        prev_step_id = step_id

    # Add final response step if we have any steps
    if steps:
        steps.append({
            "id": "return_response",
            "type": "return_result",
            "depends_on": [prev_step_id] if prev_step_id else [],
            "description": "Return final result from multi-step workflow",
        })

    return steps


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

    Contract per Appendix C.3.7:
    - Queries KG (graph-first, then embedding similarity) for matching templates
    - Falls back to legacy in-memory ONLY if USE_LEGACY_TEMPLATES=1
    - Falls back to in-memory KG fallback if USE_IN_MEMORY_KG_FALLBACK=1
    - Populates plan["candidate_templates"] (may be empty, not an error)
    - Builds initial workflow nodes and edges from best template
    
    M5 Enhancement:
    - Uses smarter generic fallback based on HTTP method when no templates found
    - Infers workflow pattern from endpoint structure (POST→create, GET→fetch, etc.)
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
    # ---------------------------------------------------------------------------
    logger.info(
        "align_task_with_kg: querying KG for provider=%s, task=%s",
        provider,
        task_slug,
    )
    candidate_templates = _query_kg_templates(
        provider=provider,
        task_slug=task_slug,
        task_description=task_description,
        known_endpoints=known_endpoints,
        known_entities=known_entities,
    )

    # ---------------------------------------------------------------------------
    # Fallback hierarchy:
    # 1. KG templates (from GraphRAG)
    # 2. Legacy in-memory templates (if USE_LEGACY_TEMPLATES=1)
    # 3. In-memory KG fallback (if USE_IN_MEMORY_KG_FALLBACK=1)
    # 4. Smart generic fallback based on HTTP method (M5 default)
    # ---------------------------------------------------------------------------
    template_source = "kg"

    if not candidate_templates and _check_legacy_templates_enabled():
        logger.info(
            "align_task_with_kg: KG empty; trying legacy templates (USE_LEGACY_TEMPLATES=1)"
        )
        candidate_templates = _legacy_in_memory_lookup(provider, task_slug)
        if candidate_templates:
            template_source = "legacy"

    if not candidate_templates and _check_fallback_enabled():
        logger.warning(
            "align_task_with_kg: KG returned no templates; using in-memory fallback "
            "(USE_IN_MEMORY_KG_FALLBACK=1). This should NOT happen on demo path."
        )
        candidate_templates = _legacy_in_memory_lookup(provider, task_slug)
        if candidate_templates:
            template_source = "fallback"

    if not candidate_templates:
        # M5: Expected on first run - use smart inference from endpoint
        logger.info(
            "align_task_with_kg: No templates for provider=%s. "
            "Using smart HTTP-method-based inference. "
            "KG will learn from this run for future use.",
            provider,
        )
        template_source = "inferred"

    state.plan["candidate_templates"] = candidate_templates
    state.plan["template_source"] = template_source  # M5: Track where template came from

    # ---------------------------------------------------------------------------
    # Build workflow nodes/edges from best template or smart fallback
    # ---------------------------------------------------------------------------
    if candidate_templates:
        best = candidate_templates[0]
        steps = best.get("steps", [])
        logger.info(
            "align_task_with_kg: selected template=%s with %d steps (source=%s)",
            best.get("template_id", "?"),
            len(steps),
            template_source,
        )
    else:
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

    state.completed_steps.append("align_task_with_kg")
    return state
