from integration_coworker.graph.state import WorkflowState
from integration_coworker.repo.context import filesystem_repo_context_provider
from integration_coworker.repo.profiles import detect_profile_from_repo


def attach_repo_context(state: WorkflowState) -> WorkflowState:
    """
    Reads: repo_root, repo_profile (optional)
    Writes: repo_snapshot, repo_markdown_context, repo_profile (if needed)
    
    Contract per Appendix C.3.10:
    - Uses filesystem_repo_context_provider to build snapshot
    - Infers repo_profile when None
    - Sets repo_markdown_context from snapshot.full_markdown
    """
    if not state.repo_root:
        state.completed_steps.append("attach_repo_context")
        return state
    
    try:
        # Use filesystem provider to build repo snapshot
        snapshot = filesystem_repo_context_provider(state.repo_root)
        # Note: repo_snapshot is internal/temporary, not persisted per spec
        state.repo_snapshot = snapshot
        state.repo_markdown_context = snapshot.full_markdown if snapshot else None
        
        # Infer repo_profile if not explicitly provided
        if state.repo_profile is None:
            state.repo_profile = detect_profile_from_repo(state.repo_root)
            
    except Exception as e:
        state.errors.append(f"Failed to attach repo context: {str(e)}")
    
    state.completed_steps.append("attach_repo_context")
    return state
