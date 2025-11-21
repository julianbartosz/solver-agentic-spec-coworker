import os
from integration_coworker.graph.state import WorkflowState
from integration_coworker.config import get_llm_config

def understand_task(state: WorkflowState) -> WorkflowState:
    """
    Reads: task_description, endpoints, schemas
    Writes: workflow_template, plan["task_brief"]
    """
    if not state.task_description:
        state.errors.append("No task_description provided")
        state.completed_steps.append("understand_task")
        return state
    
    config = get_llm_config("extraction")
    
    # For Phase 2: use simple keyword matching for task understanding
    task_lower = state.task_description.lower()
    
    # Simple heuristics for Phase 2
    task_brief = {
        "task_description": state.task_description,
        "target_operations": [],
        "required_inputs": [],
        "expected_outputs": [],
    }
    
    # Match task to endpoints
    for endpoint in state.endpoints:
        operation_id = endpoint.operation_id or ""
        path = endpoint.path or ""
        
        # Keyword matching
        if ("checkout" in task_lower and "checkout" in operation_id.lower()) or \
           ("checkout" in task_lower and "checkout" in path.lower()) or \
           ("session" in task_lower and "session" in operation_id.lower()):
            task_brief["target_operations"].append({
                "operation_id": operation_id,
                "method": endpoint.method,
                "path": endpoint.path,
                "summary": endpoint.summary,
            })
    
    # Derive required inputs from matched endpoints
    if task_brief["target_operations"]:
        # For checkout session creation, typical inputs
        if "checkout" in task_lower and "create" in task_lower:
            task_brief["required_inputs"] = [
                {"name": "amount", "type": "integer"},
                {"name": "currency", "type": "string"},
                {"name": "success_url", "type": "string"},
                {"name": "cancel_url", "type": "string"},
            ]
            task_brief["expected_outputs"] = [
                {"name": "session_id", "type": "string"},
                {"name": "checkout_url", "type": "string"},
            ]
    
    # Create WorkflowTemplate (simple for Phase 2)
    if not state.workflow_template:
        from integration_coworker.domain.models import WorkflowTemplate
        state.workflow_template = WorkflowTemplate(
            id=None,
            source_system_id=None,
            code=f"{state.provider_code}_checkout_workflow",
            name=f"Workflow for {state.provider_code} checkout",
            description=state.task_description,
        )
    
    # Store task brief in plan for use by subsequent nodes
    state.plan["task_brief"] = task_brief
    
    state.completed_steps.append("understand_task")
    return state
