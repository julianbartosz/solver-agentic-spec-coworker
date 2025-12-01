from pathlib import Path
from integration_coworker.graph.state import WorkflowState
from integration_coworker.repo.models import RepoChangeSet, FileChange
from integration_coworker.repo.profiles import SUBATOMIC_MOCK_PROFILE
from integration_coworker.codegen.paths import get_layout_dirs, path_to_module, strip_src_prefix

def analyze_repo_layout(state: WorkflowState) -> WorkflowState:
    """
    Reads: repo_root, repo_profile, code_artifacts
    Writes: repo_changes
    
    Maps code artifacts to filesystem paths and creates RepoChangeSet.
    Since generate_code_and_tests already uses RepoProfile layout_hints,
    this node just needs to use rel_path as-is and add router/settings changes.
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

    # Get layout dirs using shared utility (handles workflows_dir/flows_dir key)
    clients_dir, flows_dir, tests_dir = get_layout_dirs(profile)

    # Map artifacts to concrete paths - artifacts already have correct rel_path from codegen
    changes = []

    for artifact in state.code_artifacts:
        # Use the rel_path from the artifact directly (already computed by generate_code_and_tests)
        target_path = artifact.rel_path

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
        from integration_coworker.codegen.naming import derive_flow_module_name

        router_path = Path(state.repo_root) / router_file
        if router_path.exists():
            original_router = router_path.read_text(encoding="utf-8")
        else:
            # Create minimal router file if it doesn't exist
            original_router = "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n"

        # Generate router block with correct import path
        task_slug = state.integration_task.task_slug if state.integration_task else "integration"
        provider = state.provider_code or "unknown"
        flow_module = derive_flow_module_name(provider, task_slug)

        # Compute import module path from flows_dir
        flows_import_module = path_to_module(strip_src_prefix(flows_dir))
        router_block = generate_router_block(provider, task_slug, flows_import_module, flow_module)

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
