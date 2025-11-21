from integration_coworker.graph.state import WorkflowState

def validate_integration_design(state: WorkflowState) -> WorkflowState:
    """
    Reads: integration_task, workflow_nodes, code_artifacts, errors
    Writes: validation results (adds to errors if issues found)
    """
    # For Phase 2: simple validation checks
    
    # Check that we have core components
    if not state.integration_task:
        state.errors.append("Validation failed: No integration_task defined")
    
    if not state.workflow_nodes:
        state.errors.append("Validation failed: No workflow_nodes defined")
    
    if not state.code_artifacts:
        state.errors.append("Validation failed: No code_artifacts generated")
    
    # Check that endpoints were found
    if not state.endpoints:
        state.errors.append("Validation failed: No endpoints extracted from spec")
    
    # For Phase 2, these are warnings, not hard failures
    # Real implementation would do deeper validation
    
    state.completed_steps.append("validate_integration_design")
    return state
