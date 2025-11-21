from integration_coworker.graph.state import WorkflowState
from integration_coworker.repo.context import filesystem_repo_context_provider

def attach_repo_context(state: WorkflowState) -> WorkflowState:
    """
    Reads: repo_root, repo_profile
    Writes: repo_snapshot, repo_markdown_context
    """
    if not state.repo_root:
        state.completed_steps.append("attach_repo_context")
        return state
    
    try:
        # Use filesystem provider to build repo snapshot
        snapshot = filesystem_repo_context_provider(state.repo_root)
        state.repo_snapshot = snapshot
        state.repo_markdown_context = snapshot.full_markdown if snapshot else None
    except Exception as e:
        state.errors.append(f"Failed to attach repo context: {str(e)}")
    
    state.completed_steps.append("attach_repo_context")
    return state
