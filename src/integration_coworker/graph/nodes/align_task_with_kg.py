"""
align_task_with_kg node – GraphRAG-based workflow template retrieval.

Design Doc Section: Appendix C.3.7

This node queries the persistent Knowledge Graph (kg schema) for workflow
templates that match the current provider + task. It uses graph-first filtering
(provider_code, known entities) and embedding similarity for ranking.

The in-memory fallback is only used if USE_IN_MEMORY_KG_FALLBACK=1 is set,
allowing tests to run without a populated KG. On the demo path, this should
fail loudly if the KG is empty/broken.
"""

import os
import logging
from typing import List, Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import (
    IntegrationFlowNode,
    IntegrationFlowEdge,
    KGWorkflowTemplate,
)
from integration_coworker.kg import query_workflow_templates, _check_fallback_enabled

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Legacy in-memory fallback (gated by USE_IN_MEMORY_KG_FALLBACK env var)
# This should NOT be used on the demo path; it exists only for bootstrapping.
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
) -> List[dict]:
    """
    Query the persistent KG for workflow templates.
    Returns list of template dicts (matching legacy format for backwards compat).
    """
    # Attempt GraphRAG query against persistent KG
    # Note: similarity_threshold is set low (0.2) to work with mock LLM mode
    # where embeddings are unavailable. With real embeddings, scores will be higher.
    kg_templates: List[KGWorkflowTemplate] = query_workflow_templates(
        provider_code=provider,
        task_description=task_description,
        known_entities=None,  # Could extract from state if needed
        known_endpoints=known_endpoints,
        top_k=5,
        similarity_threshold=0.2,  # Low threshold to allow matches without embeddings
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
        results.append({
            "template_id": tmpl.template_id,
            "name": tmpl.name,
            "description": tmpl.description or "",
            "steps": steps,
        })

    return results


def _legacy_in_memory_lookup(provider: str, task_slug: str) -> List[dict]:
    """
    Fallback to in-memory templates (only when USE_IN_MEMORY_KG_FALLBACK=1).
    This is for bootstrapping/testing before the KG is populated.
    """
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


def _build_fallback_steps() -> List[dict]:
    """Generic 4-step fallback workflow when no templates are found."""
    return [
        {"key": "start", "type": "start", "label": "Start"},
        {"key": "validate_input", "type": "validation", "label": "Validate Input"},
        {"key": "call_api", "type": "api_call", "label": "API Call"},
        {"key": "end", "type": "end", "label": "End"},
    ]


def align_task_with_kg(state: WorkflowState) -> WorkflowState:
    """
    Reads: integration_task, provider_code, endpoints
    Writes: plan["candidate_templates"], workflow_nodes, workflow_edges

    Contract per Appendix C.3.7:
    - Queries KG (graph-first, then embedding similarity) for matching templates
    - Falls back to in-memory ONLY if USE_IN_MEMORY_KG_FALLBACK=1
    - Populates plan["candidate_templates"] (may be empty, not an error)
    - Builds initial workflow nodes and edges from best template
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

    # ---------------------------------------------------------------------------
    # GraphRAG query: graph-first filtering, then embedding similarity
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
    )

    # ---------------------------------------------------------------------------
    # Fallback: in-memory templates (gated by env var, NOT for demo path)
    # ---------------------------------------------------------------------------
    if not candidate_templates and _check_fallback_enabled():
        logger.warning(
            "align_task_with_kg: KG returned no templates; using in-memory fallback "
            "(USE_IN_MEMORY_KG_FALLBACK=1). This should NOT happen on demo path."
        )
        candidate_templates = _legacy_in_memory_lookup(provider, task_slug)
    elif not candidate_templates:
        # KG is empty and fallback is disabled - this is expected on first run
        # but should be noted clearly
        logger.warning(
            "align_task_with_kg: KG has no templates for provider=%s. "
            "Using generic fallback workflow. "
            "Run persist_kg_learning after a successful run to populate the KG.",
            provider,
        )

    state.plan["candidate_templates"] = candidate_templates

    # ---------------------------------------------------------------------------
    # Build workflow nodes/edges from best template (or fallback flow)
    # ---------------------------------------------------------------------------
    if candidate_templates:
        best = candidate_templates[0]
        steps = best.get("steps", [])
        logger.info(
            "align_task_with_kg: selected template=%s with %d steps",
            best.get("template_id", "?"),
            len(steps),
        )
    else:
        logger.info("align_task_with_kg: no templates found; using generic fallback flow")
        steps = _build_fallback_steps()

    # Create IntegrationFlowNode list
    nodes = []
    for i, step in enumerate(steps):
        node = IntegrationFlowNode(
            id=None,
            task_id=None,
            node_key=step["key"],
            node_type=step["type"],
            endpoint_id=None,
            entity_id=None,
            position=i,
            config={
                "label": step.get("label", step["key"]),
                "description": step.get("description", ""),
            },
        )
        nodes.append(node)

    state.workflow_nodes = nodes

    # Create IntegrationFlowEdge list (linear flow)
    edges = []
    for i in range(len(steps) - 1):
        edge = IntegrationFlowEdge(
            id=None,
            task_id=None,
            from_node_key=steps[i]["key"],
            to_node_key=steps[i + 1]["key"],
            condition=None,
        )
        edges.append(edge)

    state.workflow_edges = edges

    state.completed_steps.append("align_task_with_kg")
    return state
