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
    Writes: plan["failed"], completed_steps
    
    Contract per Appendix C.3.17:
    - Sets plan["failed"] = True
    - Does NOT write to DB or disk
    """
    # Set failure flag
    state.plan["failed"] = True
    
    # Mark as completed
    state.completed_steps.append("handle_error")
    return state
