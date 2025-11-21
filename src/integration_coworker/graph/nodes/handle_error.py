"""
Error handling node for the workflow.

Currently not used in the graph (see TODO in runtime.py),
but imported for future conditional error handling.
"""
from integration_coworker.graph.state import WorkflowState


def handle_error(state: WorkflowState) -> WorkflowState:
    """
    Handle errors that occur during workflow execution.
    
    Reads: errors
    Writes: errors (may add recovery actions), completed_steps
    """
    # For Phase 2: minimal error handling
    # Just mark the step as completed
    state.completed_steps.append("handle_error")
    return state
