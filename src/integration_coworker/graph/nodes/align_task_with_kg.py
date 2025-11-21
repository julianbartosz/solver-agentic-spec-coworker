from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import IntegrationTask, IntegrationFlowNode, IntegrationFlowEdge

def align_task_with_kg(state: WorkflowState) -> WorkflowState:
    """
    Reads: workflow_template, provider_code, task_description, plan["task_brief"]
    Writes: integration_task, workflow_nodes, workflow_edges
    """
    if not state.workflow_template:
        state.errors.append("No workflow_template from understand_task")
        state.completed_steps.append("align_task_with_kg")
        return state
    
    # Get task brief from plan
    task_brief = state.plan.get("task_brief", {})
    
    # Create IntegrationTask
    state.integration_task = IntegrationTask(
        id=None,
        task_slug=f"{state.provider_code}_create_checkout_session",
        description=state.task_description,
        provider_code=state.provider_code,
        source_system_id=None,
        target_spec_document_id=None,
        input_entities=[],
        output_entities=[],
        constraints=task_brief,
    )
    
    # Define workflow nodes
    nodes = [
        IntegrationFlowNode(
            id=None,
            task_id=None,
            node_key="validate_input",
            node_type="validation",
            endpoint_id=None,
            entity_id=None,
            position=0,
            config={"label": "Validate Input", "description": "Validate amount, currency, and URLs"},
        ),
        IntegrationFlowNode(
            id=None,
            task_id=None,
            node_key="call_create_session",
            node_type="api_call",
            endpoint_id=None,
            entity_id=None,
            position=1,
            config={"label": "Call Create Session", "description": "POST to /v1/checkout/sessions"},
        ),
        IntegrationFlowNode(
            id=None,
            task_id=None,
            node_key="transform_response",
            node_type="transform",
            endpoint_id=None,
            entity_id=None,
            position=2,
            config={"label": "Transform Response", "description": "Extract session_id and checkout_url"},
        ),
        IntegrationFlowNode(
            id=None,
            task_id=None,
            node_key="return_result",
            node_type="output",
            endpoint_id=None,
            entity_id=None,
            position=3,
            config={"label": "Return Result", "description": "Return structured result"},
        ),
    ]
    state.workflow_nodes = nodes
    
    # Define edges (linear flow)
    edges = [
        IntegrationFlowEdge(
            id=None,
            task_id=None,
            from_node_key="validate_input",
            to_node_key="call_create_session",
            condition=None,
        ),
        IntegrationFlowEdge(
            id=None,
            task_id=None,
            from_node_key="call_create_session",
            to_node_key="transform_response",
            condition=None,
        ),
        IntegrationFlowEdge(
            id=None,
            task_id=None,
            from_node_key="transform_response",
            to_node_key="return_result",
            condition=None,
        ),
    ]
    state.workflow_edges = edges
    
    state.completed_steps.append("align_task_with_kg")
    return state
