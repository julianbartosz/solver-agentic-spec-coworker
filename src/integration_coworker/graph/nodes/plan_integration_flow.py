from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import EndpointBinding

def plan_integration_flow(state: WorkflowState) -> WorkflowState:
    """
    Reads: workflow_nodes, workflow_edges, endpoints, plan["task_brief"]
    Writes: endpoint_bindings
    """
    if not state.workflow_nodes:
        state.errors.append("No workflow_nodes from align_task_with_kg")
        state.completed_steps.append("plan_integration_flow")
        return state
    
    # Find the API call node
    api_call_node = None
    for node in state.workflow_nodes:
        if node.node_type == "api_call":
            api_call_node = node
            break
    
    if not api_call_node:
        state.completed_steps.append("plan_integration_flow")
        return state
    
    # Get task brief from plan
    task_brief = state.plan.get("task_brief", {})
    target_ops = task_brief.get("target_operations", [])
    
    for target_op in target_ops:
        operation_id = target_op.get("operation_id")
        method = target_op.get("method")
        path = target_op.get("path")
        
        # Find corresponding endpoint in state.endpoints
        for endpoint in state.endpoints:
            if endpoint.operation_id == operation_id or \
               (endpoint.method == method and endpoint.path == path):
                # Create binding
                binding = EndpointBinding(
                    id=None,
                    task_id=None,
                    flow_node_key=api_call_node.node_key,
                    endpoint_id=None,  # Will be set during persistence
                    request_mapping={
                        "amount": "$.input.amount",
                        "currency": "$.input.currency",
                        "success_url": "$.input.success_url",
                        "cancel_url": "$.input.cancel_url",
                    },
                    response_mapping={
                        "session_id": "$.response.id",
                        "checkout_url": "$.response.checkout_url",
                    },
                )
                state.endpoint_bindings.append(binding)
                break
    
    state.completed_steps.append("plan_integration_flow")
    return state
