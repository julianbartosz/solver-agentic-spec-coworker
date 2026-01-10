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
from integration_coworker.repo.io import get_repo_io, RepoIO
from integration_coworker.codegen.paths import join_path, normalize_path
# V36-002: Import repo type detector to prevent FastAPI generation for CLI/library repos
from integration_coworker.codegen.repo_type_detector import (
    detect_repo_type,
    should_generate_fastapi_router,
    RepoType,
    IntegrationStrategy,
)

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
    io: Optional[RepoIO] = None,
    task_requests_router: bool = False,
) -> Optional[FileChange]:
    """
    Compute router file change for auto-wiring.
    
    Per Appendix G: Uses marker-based block insertion.
    
    V36-002: Now checks repo type before generating FastAPI router code.
    CLI tools and libraries will not get FastAPI routers.
    
    V39-006: If task_requests_router is True, generate router regardless of
    repo type detection. User explicitly requested a router.
    
    V41-002: Detects if router_file is a main app file (main.py, app.py) and
    generates appropriate code with inline router definition instead of
    assuming a `router` variable exists.
    
    Args:
        task_requests_router: V39-006 - If True, user explicitly asked for a router
    """
    from integration_coworker.repo.helpers import upsert_block_between_markers, generate_router_block
    from integration_coworker.codegen.paths import path_to_module, strip_src_prefix
    from integration_coworker.codegen.naming import derive_flow_module_name
    
    # V39-006: Check if user explicitly requested a router
    # This overrides repo type detection
    if task_requests_router:
        logger.info(
            "[V39-006] User explicitly requested router - generating regardless of repo type"
        )
    elif not should_generate_fastapi_router(repo_root):
        # V36-002: Skip router generation for CLI tools, libraries, and non-web repos
        repo_detection = detect_repo_type(repo_root)
        logger.info(
            f"[V36-002] Skipping router generation for {repo_detection.repo_type.value} repo "
            f"(strategy={repo_detection.integration_strategy.value})"
        )
        return None
    
    router_path = Path(repo_root) / router_file
    
    # V41-002: Detect if this is a main app file vs a dedicated router file
    # Main app files don't have a pre-existing `router` variable, so we need
    # to generate one inline and register it with `app.include_router()`
    router_basename = Path(router_file).name.lower()
    is_main_app_file = router_basename in ("main.py", "app.py", "__main__.py")
    
    # Also check if file contains FastAPI app instantiation
    if not is_main_app_file and router_path.exists():
        try:
            content = router_path.read_text(encoding="utf-8") if not io else io.read_text(router_file)
            # Check for app = FastAPI() pattern indicating this is a main app file
            if "= FastAPI(" in content or "=FastAPI(" in content:
                is_main_app_file = True
                logger.info(f"[V41-002] Detected {router_file} as main app file (contains FastAPI instance)")
        except Exception:
            pass  # Continue with default detection
    
    target_file_type = "main" if is_main_app_file else "router"
    
    if is_main_app_file:
        logger.info(f"[V41-002] Generating inline router for main app file: {router_file}")
    
    if router_path.exists():
        try:
            # P1: Use io.read_text() if available, else fallback
            if io:
                original_content = io.read_text(router_file)
            else:
                original_content = router_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read router file {router_path}: {e}")
            return None
    else:
        # Create minimal router file
        original_content = "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n"
    
    # V45-PATH-001: Compute flow module import path using get_layout_dirs for consistency
    # This ensures the import path matches where files are actually written
    from integration_coworker.codegen.paths import get_layout_dirs
    _, flows_dir, _ = get_layout_dirs(profile)
    flows_import_module = path_to_module(strip_src_prefix(flows_dir))
    flow_module_name = derive_flow_module_name(provider_code, integration_slug)
    
    # Generate router block
    # V41-002: Pass target_file_type to generate appropriate router code
    router_block = generate_router_block(
        provider_code, integration_slug, flows_import_module, flow_module_name,
        target_file_type=target_file_type
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
    io: Optional[RepoIO] = None,
) -> Optional[FileChange]:
    """
    Compute settings file change for auto-wiring.
    
    Per Appendix G: Uses marker-based block insertion.
    """
    from integration_coworker.repo.helpers import upsert_block_between_markers, generate_settings_block
    
    settings_path = Path(repo_root) / settings_file
    
    if settings_path.exists():
        try:
            # P1: Use io.read_text() if available, else fallback
            if io:
                original_content = io.read_text(settings_file)
            else:
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


def _compute_app_router_registration_change(
    repo_root: str,
    app_file: str,
    app_router_marker: str,
    router_file: str,
    provider_code: str,
    integration_slug: str,
    io: Optional[RepoIO] = None,
) -> Optional[FileChange]:
    """
    Compute app entry point change to register integration router.
    
    BUG-009 FIX: System generated router_file but didn't update main.py
    to include the router with `app.include_router(...)`.
    
    This function generates the FileChange needed to add the router
    import and include_router() call to the main FastAPI app file.
    
    Args:
        repo_root: Repository root path
        app_file: Path to app entry point (e.g., "src/main.py")
        app_router_marker: Marker for auto-generated router registration block
        router_file: Path to the generated router file
        provider_code: Provider code (e.g., "openai")
        integration_slug: Integration task slug (e.g., "summarize")
        io: Optional RepoIO for file reads
        
    Returns:
        FileChange for the app file, or None if no change needed
    """
    from integration_coworker.repo.helpers import (
        upsert_block_between_markers,
        generate_app_router_registration_block,
    )
    
    app_path = Path(repo_root) / app_file
    
    if not app_path.exists():
        logger.warning(f"[BUG-009] App file not found: {app_path}")
        return None
    
    try:
        # Read existing app file
        if io:
            original_content = io.read_text(app_file)
        else:
            original_content = app_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"[BUG-009] Failed to read app file {app_path}: {e}")
        return None
    
    # Generate the router registration block
    registration_block = generate_app_router_registration_block(
        router_file, provider_code, integration_slug
    )
    
    # Insert between markers
    start_marker = app_router_marker or "# BEGIN AUTO-REGISTERED INTEGRATION ROUTERS"
    end_marker = start_marker.replace("BEGIN", "END")
    
    updated_content = upsert_block_between_markers(
        original_content, start_marker, end_marker, registration_block
    )
    
    # Only return a change if content actually changed
    if updated_content == original_content:
        logger.debug(f"[BUG-009] No change needed for {app_file}")
        return None
    
    logger.info(f"[BUG-009] Adding router registration to {app_file}")
    return FileChange(
        rel_path=app_file,
        change_type="update",
        before=original_content,
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

    # P1: Get IO context for policy-enforced reads
    io = get_repo_io()

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
            
            target_path = join_path(target_dir, filename)
        
        # Check if file already exists
        full_path = Path(state.repo_root) / target_path
        change_type = "update" if full_path.exists() else "create"
        
        before_content = None
        if full_path.exists():
            try:
                # P1: Use io.read_text() if available, else fallback
                if io:
                    before_content = io.read_text(target_path)
                else:
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
    
    # V39-006: Check if task explicitly requests a router
    task_requests_router = False
    if state.task_description:
        from integration_coworker.codegen.task_parser import extract_task_requirements
        task_result = extract_task_requirements(state.task_description)
        task_requests_router = task_result.is_router_requested
        if task_requests_router:
            logger.info("[V39-006] Task explicitly requests router/endpoint generation")
    
    # Router insertion (Appendix G)
    router_file = hooks.get("router_file")
    router_marker = hooks.get("router_registration_marker") or hooks.get("router_marker")
    
    # V39-006: If user explicitly requested router but hooks not configured,
    # use sensible defaults for FastAPI
    if task_requests_router and not router_file:
        logger.info("[V39-006] Configuring default router file for explicit router request")
        router_file = "src/routers/__init__.py"
        router_marker = "# AUTO-GENERATED ROUTES"
        # Ensure hooks are updated
        hooks["router_file"] = router_file
        hooks["router_registration_marker"] = router_marker
    
    if router_file and router_marker:
        router_change = _compute_router_change(
            state.repo_root,
            router_file,
            router_marker,
            provider_code,
            integration_slug,
            profile,
            io,
            task_requests_router=task_requests_router,
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
            io,
        )
        if settings_change:
            changes.append(settings_change)
            logger.debug(f"Added settings change: {settings_file}")

    # -------------------------------------------------------------------------
    # BUG-009 FIX: App entry point router registration
    # -------------------------------------------------------------------------
    # After generating the router file, we need to update the main app file
    # (e.g., main.py) to include the router with app.include_router(...)
    app_file = hooks.get("app_file")
    app_router_marker = hooks.get("app_router_marker")
    
    if app_file and router_file:
        app_change = _compute_app_router_registration_change(
            state.repo_root,
            app_file,
            app_router_marker,
            router_file,
            provider_code,
            integration_slug,
            io,
        )
        if app_change:
            changes.append(app_change)
            logger.debug(f"[BUG-009] Added app router registration change: {app_file}")

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
