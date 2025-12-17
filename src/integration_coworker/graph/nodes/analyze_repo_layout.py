"""
Analyze Repo Layout Node

Per V2 Design Spec Appendix D.7:
- Reads: repo_root, repo_profile, code_artifacts, provider_code, integration_task
- Writes: repo_changes (RepoChangeSet)
- Uses _dir_for_artifact() to resolve paths based on RepoProfile
- Computes router/settings changes for hook-enabled repos
- Does NOT write to disk (that's apply_repo_integration_changes)
"""
import logging
from pathlib import Path
from typing import Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.repo.models import RepoChangeSet, FileChange, RepoProfile

logger = logging.getLogger(__name__)


# =============================================================================
# Path Resolution (V2 Spec Compliance)
# =============================================================================


def _dir_for_artifact(
    profile: RepoProfile,
    artifact_type: str,
    provider_code: str,
    integration_slug: str,
) -> str:
    """
    Resolve the target directory for a given artifact type based on RepoProfile.

    Per V2 Design Spec Appendix D.7:
    Resolution order:
    1. layout_hints[<type>_dir] if present (e.g., "clients_dir")
    2. Archetype-specific defaults
    3. Generic fallbacks under integrations/

    Args:
        profile: RepoProfile with layout_hints
        artifact_type: "client", "workflow"/"flow", or "test"
        provider_code: e.g., "stripe", "mock_payments"
        integration_slug: e.g., "create_checkout_session"

    Returns:
        Directory path (relative to repo root)
    """
    hints = profile.layout_hints or {}
    
    # Normalize artifact type
    if artifact_type == "flow":
        artifact_type = "workflow"
    
    # Key mapping: client -> clients_dir, workflow -> workflows_dir
    key = f"{artifact_type}s_dir"
    
    # Priority 1: Explicit layout_hints
    if key in hints:
        return hints[key]
    
    # Also check singular form and alternate names
    alt_keys = {
        "clients_dir": ["client_dir"],
        "workflows_dir": ["workflow_dir", "flows_dir", "flow_dir", "services_dir"],
        "tests_dir": ["test_dir"],
    }
    for alt in alt_keys.get(key, []):
        if alt in hints:
            return hints[alt]
    
    # Priority 2: Use integrations_root + conventions from profile
    integrations_root = profile.integrations_root or "integrations"
    tests_root = profile.tests_root or "tests"
    
    # Check conventions for patterns
    conventions = profile.conventions or {}
    
    if artifact_type == "client":
        pattern = conventions.get("client_module_pattern", "clients/{provider}.py")
        # Extract directory from pattern
        if "/" in pattern:
            subdir = pattern.rsplit("/", 1)[0].format(provider=provider_code, task=integration_slug)
            return f"{integrations_root}/{subdir}"
        return f"{integrations_root}/clients"
    
    if artifact_type == "workflow":
        pattern = conventions.get("flow_module_pattern", "flows/{provider}_{task}.py")
        if "/" in pattern:
            subdir = pattern.rsplit("/", 1)[0].format(provider=provider_code, task=integration_slug)
            return f"{integrations_root}/{subdir}"
        return f"{integrations_root}/flows"
    
    if artifact_type == "test":
        pattern = conventions.get("test_module_pattern", "test_{provider}_{task}.py")
        if "/" in pattern:
            subdir = pattern.rsplit("/", 1)[0].format(provider=provider_code, task=integration_slug)
            return f"{tests_root}/{subdir}"
        return tests_root
    
    # Fallback
    return integrations_root


def _get_artifact_filename(
    profile: RepoProfile,
    artifact_type: str,
    provider_code: str,
    task_slug: str,
) -> str:
    """
    Derive the filename for an artifact based on conventions.
    
    Args:
        profile: RepoProfile with conventions
        artifact_type: "client", "workflow"/"flow", or "test"
        provider_code: e.g., "stripe"
        task_slug: e.g., "create_checkout_session"
    
    Returns:
        Filename (not path)
    """
    conventions = profile.conventions or {}
    lang = profile.language or "python"
    ext = ".ts" if lang in ("typescript", "javascript") else ".py"
    
    if artifact_type == "flow":
        artifact_type = "workflow"
    
    pattern_key = {
        "client": "client_module_pattern",
        "workflow": "flow_module_pattern",
        "test": "test_module_pattern",
    }.get(artifact_type)
    
    default_patterns = {
        "client": f"{{provider}}{ext}",
        "workflow": f"{{provider}}_{{task}}{ext}",
        "test": f"test_{{provider}}_{{task}}{ext}",
    }
    
    pattern = conventions.get(pattern_key, default_patterns.get(artifact_type, f"{{provider}}{ext}"))
    
    # Extract just the filename part
    if "/" in pattern:
        pattern = pattern.rsplit("/", 1)[1]
    
    return pattern.format(provider=provider_code, task=task_slug)


# =============================================================================
# Router/Settings Change Computation
# =============================================================================


def _compute_router_change(
    repo_root: str,
    router_file: str,
    router_marker: str,
    provider_code: str,
    integration_slug: str,
    profile: RepoProfile,
) -> Optional[FileChange]:
    """
    Compute router file change for auto-wiring.
    
    Per Appendix G: Uses marker-based block insertion.
    """
    from integration_coworker.repo.helpers import upsert_block_between_markers, generate_router_block
    from integration_coworker.codegen.paths import path_to_module, strip_src_prefix
    from integration_coworker.codegen.naming import derive_flow_module_name
    
    router_path = Path(repo_root) / router_file
    
    if router_path.exists():
        try:
            original_content = router_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read router file {router_path}: {e}")
            return None
    else:
        # Create minimal router file
        original_content = "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n"
    
    # Compute flow module import path
    flows_dir = _dir_for_artifact(profile, "workflow", provider_code, integration_slug)
    flows_import_module = path_to_module(strip_src_prefix(flows_dir))
    flow_module_name = derive_flow_module_name(provider_code, integration_slug)
    
    # Generate router block
    router_block = generate_router_block(
        provider_code, integration_slug, flows_import_module, flow_module_name
    )
    
    # Insert between markers
    start_marker = "# BEGIN AUTO-GENERATED INTEGRATION ROUTES"
    end_marker = "# END AUTO-GENERATED INTEGRATION ROUTES"
    
    updated_content = upsert_block_between_markers(
        original_content, start_marker, end_marker, router_block
    )
    
    return FileChange(
        rel_path=router_file,
        change_type="update" if router_path.exists() else "create",
        before=original_content if router_path.exists() else None,
        after=updated_content,
    )


def _compute_settings_change(
    repo_root: str,
    settings_file: str,
    settings_marker: str,
    provider_code: str,
    base_url: Optional[str] = None,
) -> Optional[FileChange]:
    """
    Compute settings file change for auto-wiring.
    
    Per Appendix G: Uses marker-based block insertion.
    """
    from integration_coworker.repo.helpers import upsert_block_between_markers, generate_settings_block
    
    settings_path = Path(repo_root) / settings_file
    
    if settings_path.exists():
        try:
            original_content = settings_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read settings file {settings_path}: {e}")
            return None
    else:
        # Create minimal settings file
        original_content = "from typing import Dict, Any\n\nINTEGRATIONS: Dict[str, Any] = {}\n\n"
    
    # Generate settings block
    settings_block = generate_settings_block(provider_code, base_url)
    
    # Insert between markers
    start_marker = "# BEGIN AUTO-GENERATED INTEGRATION SETTINGS"
    end_marker = "# END AUTO-GENERATED INTEGRATION SETTINGS"
    
    updated_content = upsert_block_between_markers(
        original_content, start_marker, end_marker, settings_block
    )
    
    return FileChange(
        rel_path=settings_file,
        change_type="update" if settings_path.exists() else "create",
        before=original_content if settings_path.exists() else None,
        after=updated_content,
    )


# =============================================================================
# Main Node Function (V2 Spec Aligned)
# =============================================================================


def analyze_repo_layout(state: WorkflowState) -> WorkflowState:
    """
    Build a RepoChangeSet from code_artifacts.

    Per V2 Design Spec Appendix D.7:
    - Assert repo_root, repo_profile, and integration_task are set
    - Use _dir_for_artifact() to resolve paths based on profile
    - Compute router/settings changes based on integration_hooks
    - Does NOT write to disk

    Reads:
        - repo_root
        - repo_profile
        - code_artifacts
        - provider_code
        - integration_task (for task_slug)

    Writes:
        - repo_changes (RepoChangeSet)
        - Appends "analyze_repo_layout" to completed_steps
    """
    # -------------------------------------------------------------------------
    # V2 Spec: Assertions (previously pragmatic fallbacks)
    # -------------------------------------------------------------------------
    if not state.repo_root:
        # For non-repo runs, just complete the step
        state.completed_steps.append("analyze_repo_layout")
        return state
    
    # V2 Spec: repo_profile MUST be set
    if state.repo_profile is None:
        error_msg = (
            "repo_profile must be set for repo runs. "
            "Ensure attach_repo_context runs before analyze_repo_layout."
        )
        logger.error(error_msg)
        state.errors.append(error_msg)
        state.completed_steps.append("analyze_repo_layout")
        return state
    
    # V2 Spec: integration_task MUST be set
    if state.integration_task is None:
        error_msg = (
            "integration_task must be set before repo analysis. "
            "Ensure understand_task runs before analyze_repo_layout."
        )
        logger.error(error_msg)
        state.errors.append(error_msg)
        state.completed_steps.append("analyze_repo_layout")
        return state

    profile = state.repo_profile
    provider_code = state.provider_code or getattr(state.integration_task, 'provider_code', None) or "unknown"
    integration_slug = state.integration_task.task_slug

    changes: list[FileChange] = []

    # -------------------------------------------------------------------------
    # V2 Spec: Use _dir_for_artifact() for path resolution
    # -------------------------------------------------------------------------
    for artifact in state.code_artifacts:
        # Determine artifact type
        artifact_type = artifact.artifact_type
        
        # V2 Spec: Compose path from _dir_for_artifact + artifact filename
        # If artifact already has a complete rel_path (from codegen), use it
        # Otherwise, compute from profile conventions
        if artifact.rel_path and "/" in artifact.rel_path:
            # Artifact already has full path from codegen
            target_path = artifact.rel_path
        else:
            # Compute path using _dir_for_artifact
            target_dir = _dir_for_artifact(
                profile,
                artifact_type=artifact_type,
                provider_code=provider_code,
                integration_slug=integration_slug,
            )
            
            # Get filename from artifact or derive from conventions
            if artifact.rel_path and "/" not in artifact.rel_path:
                filename = artifact.rel_path
            else:
                filename = _get_artifact_filename(
                    profile, artifact_type, provider_code, integration_slug
                )
            
            target_path = f"{target_dir}/{filename}"
        
        # Check if file already exists
        full_path = Path(state.repo_root) / target_path
        change_type = "update" if full_path.exists() else "create"
        
        before_content = None
        if full_path.exists():
            try:
                before_content = full_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read existing file {full_path}: {e}")

        change = FileChange(
            rel_path=target_path,
            change_type=change_type,
            before=before_content,
            after=artifact.content,
        )
        changes.append(change)
        
        logger.debug(f"Mapped artifact {artifact_type} -> {target_path}")

    # -------------------------------------------------------------------------
    # V2 Spec: Router/Settings insertion based on integration_hooks
    # -------------------------------------------------------------------------
    hooks = profile.integration_hooks or {}
    archetype = profile.archetype or profile.framework
    
    # Router insertion (Appendix G)
    router_file = hooks.get("router_file")
    router_marker = hooks.get("router_registration_marker") or hooks.get("router_marker")
    
    if router_file and router_marker:
        router_change = _compute_router_change(
            state.repo_root,
            router_file,
            router_marker,
            provider_code,
            integration_slug,
            profile,
        )
        if router_change:
            changes.append(router_change)
            logger.debug(f"Added router change: {router_file}")
    
    # Settings insertion (Appendix G)
    settings_file = hooks.get("settings_file")
    settings_marker = hooks.get("settings_marker")
    
    if settings_file and settings_marker:
        # Get base_url from source_system if available
        base_url = None
        if state.source_system and hasattr(state.source_system, 'base_url'):
            base_url = state.source_system.base_url
        
        settings_change = _compute_settings_change(
            state.repo_root,
            settings_file,
            settings_marker,
            provider_code,
            base_url,
        )
        if settings_change:
            changes.append(settings_change)
            logger.debug(f"Added settings change: {settings_file}")

    # -------------------------------------------------------------------------
    # Build RepoChangeSet
    # -------------------------------------------------------------------------
    state.repo_changes = RepoChangeSet(
        repo_root=state.repo_root,
        changes=changes,
    )
    
    logger.info(
        f"Analyzed repo layout: {len(changes)} changes "
        f"(profile: {profile.name}, source: {getattr(profile, 'profile_source', 'unknown')})"
    )

    state.completed_steps.append("analyze_repo_layout")
    return state
