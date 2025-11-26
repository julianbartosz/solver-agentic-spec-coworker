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
    
    # P2.1: Prefer archetype over framework for future branching logic
    archetype = profile.archetype or profile.framework
    
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
    router_marker = integration_hooks.get("router_registration_marker")
    settings_file = integration_hooks.get("settings_file")
    settings_marker = integration_hooks.get("settings_marker")
    
    # For FastAPI archetype, update router and settings files
    if archetype == "fastapi_service" and router_file and router_marker:
        from integration_coworker.repo.helpers import upsert_block_between_markers, generate_router_block
        
        router_path = Path(state.repo_root) / router_file
        if router_path.exists():
            original_router = router_path.read_text(encoding="utf-8")
        else:
            # Create minimal router file if it doesn't exist
            original_router = "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n"
        
        # Generate router block
        task_slug = state.integration_task.task_slug if state.integration_task else "integration"
        provider = state.provider_code or "unknown"
        router_block = generate_router_block(provider, task_slug)
        
        # Insert between markers
        start_marker = "# BEGIN AUTO-GENERATED INTEGRATION ROUTES"
        end_marker = "# END AUTO-GENERATED INTEGRATION ROUTES"
        updated_router = upsert_block_between_markers(
            original_router, start_marker, end_marker, router_block
        )
        
        # Add router file change
        changes.append(FileChange(
            rel_path=router_file,
            change_type="update" if router_path.exists() else "create",
            before=original_router if router_path.exists() else None,
            after=updated_router,
        ))
    
    if archetype == "fastapi_service" and settings_file and settings_marker:
        from integration_coworker.repo.helpers import upsert_block_between_markers, generate_settings_block
        
        settings_path = Path(state.repo_root) / settings_file
        if settings_path.exists():
            original_settings = settings_path.read_text(encoding="utf-8")
        else:
            # Create minimal settings file if it doesn't exist
            original_settings = "from typing import Dict\n\nINTEGRATIONS: Dict[str, Any] = {}\n\n"
        
        # Generate settings block
        provider = state.provider_code or "unknown"
        # Try to get base_url from source_system if available
        base_url = None
        if state.source_system and state.source_system.base_url:
            base_url = state.source_system.base_url
        settings_block = generate_settings_block(provider, base_url)
        
        # Insert between markers
        start_marker = "# BEGIN AUTO-GENERATED INTEGRATION SETTINGS"
        end_marker = "# END AUTO-GENERATED INTEGRATION SETTINGS"
        updated_settings = upsert_block_between_markers(
            original_settings, start_marker, end_marker, settings_block
        )
        
        # Add settings file change
        changes.append(FileChange(
            rel_path=settings_file,
            change_type="update" if settings_path.exists() else "create",
            before=original_settings if settings_path.exists() else None,
            after=updated_settings,
        ))
    
    state.repo_changes = RepoChangeSet(
        repo_root=state.repo_root,
        changes=changes,
    )
    
    state.completed_steps.append("analyze_repo_layout")
    return state
