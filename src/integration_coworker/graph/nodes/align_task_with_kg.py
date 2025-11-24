from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import IntegrationFlowNode, IntegrationFlowEdge


# Temporary in-memory KG stand-in for M3
# TODO: Replace with actual kg.workflow_templates DB queries in future phases
WORKFLOW_TEMPLATES = {
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
}


def align_task_with_kg(state: WorkflowState) -> WorkflowState:
    """
    Reads: integration_task, provider_code, endpoints
    Writes: plan["candidate_templates"], workflow_nodes, workflow_edges
    
    Contract per Appendix C.3.7:
    - Queries KG for matching workflow templates (in-memory for M3)
    - Populates plan["candidate_templates"] (may be empty, not an error)
    - Builds initial workflow nodes and edges from templates
    """
    if not state.integration_task:
        state.errors.append("No integration_task from understand_task")
        state.completed_steps.append("align_task_with_kg")
        return state
    
    # Query in-memory KG for matching templates
    provider = state.provider_code or "unknown"
    task_slug = state.integration_task.task_slug
    
    # Try exact match first
    template_key = (provider, task_slug)
    if template_key in WORKFLOW_TEMPLATES:
        template = WORKFLOW_TEMPLATES[template_key]
        state.plan["candidate_templates"] = [template]
    else:
        # Try partial matches (e.g., checkout-related tasks)
        matches = []
        for (p, t), tmpl in WORKFLOW_TEMPLATES.items():
            if p == provider and any(word in t for word in task_slug.split("_")):
                matches.append(tmpl)
        
        state.plan["candidate_templates"] = matches if matches else []
    
    # Build workflow nodes and edges from first candidate template (or fallback)
    if state.plan["candidate_templates"]:
        template = state.plan["candidate_templates"][0]
        steps = template["steps"]
    else:
        # Fallback: generic 4-step flow
        steps = [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input"},
            {"key": "call_api", "type": "api_call", "label": "API Call"},
            {"key": "end", "type": "end", "label": "End"},
        ]
    
    # Create nodes
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
    
    # Create edges (linear flow between consecutive steps)
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
