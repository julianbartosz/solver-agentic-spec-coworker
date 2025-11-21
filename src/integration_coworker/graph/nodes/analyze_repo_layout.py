from pathlib import Path
from integration_coworker.graph.state import WorkflowState
from integration_coworker.repo.models import RepoChangeSet, FileChange
from integration_coworker.repo.profiles import SUBATOMIC_MOCK_PROFILE

def analyze_repo_layout(state: WorkflowState) -> WorkflowState:
    """
    Reads: repo_root, repo_profile, code_artifacts
    Writes: repo_changes
    """
    if not state.repo_root:
        state.completed_steps.append("analyze_repo_layout")
        return state

    # Default to mock profile if not set
    if not state.repo_profile:
        state.repo_profile = SUBATOMIC_MOCK_PROFILE

    profile = state.repo_profile
    layout_hints = profile.layout_hints or {}
    
    # Map artifacts to concrete paths using profile
    changes = []
    
    for artifact in state.code_artifacts:
        artifact_type = artifact.artifact_type
        rel_path = artifact.rel_path
        
        # Determine target path based on artifact type and profile
        if artifact_type == "client":
            # Use profile's clients_dir or default
            clients_dir = layout_hints.get("clients_dir", "src/integrations/clients")
            # Extract filename from rel_path
            filename = rel_path.split("/")[-1]
            target_path = f"{clients_dir}/{filename}"
        
        elif artifact_type == "flow":
            flows_dir = layout_hints.get("flows_dir", "src/integrations/flows")
            filename = rel_path.split("/")[-1]
            target_path = f"{flows_dir}/{filename}"
        
        elif artifact_type == "test":
            tests_dir = layout_hints.get("tests_dir", "tests/integrations")
            filename = rel_path.split("/")[-1]
            target_path = f"{tests_dir}/{filename}"
        
        else:
            # Use rel_path as-is
            target_path = rel_path
        
        # Check if file already exists
        full_path = Path(state.repo_root) / target_path
        change_type = "update" if full_path.exists() else "create"
        before_content = full_path.read_text(encoding="utf-8") if full_path.exists() else None
        
        change = FileChange(
            rel_path=target_path,
            change_type=change_type,
            before=before_content,
            after=artifact.content,
        )
        changes.append(change)
    
    # Check if we need to update router file or settings file
    integration_hooks = profile.integration_hooks or {}
    router_file = integration_hooks.get("router_file")
    settings_file = integration_hooks.get("settings_file")
    
    # For Phase 2: add TODOs for router/settings integration
    # Real implementation would parse and insert imports
    
    state.repo_changes = RepoChangeSet(
        repo_root=state.repo_root,
        changes=changes,
    )
    
    state.completed_steps.append("analyze_repo_layout")
    return state
