import logging
from pathlib import Path

from integration_coworker.graph.state import WorkflowState
from integration_coworker.repo.context import repo_context_from_source

logger = logging.getLogger(__name__)


def attach_repo_context(state: WorkflowState) -> WorkflowState:
    """
    Reads: repo_root, repo_profile (optional), repo_source (optional)
    Writes: repo_snapshot, repo_markdown_context, repo_profile (if needed)
    
    Contract per Appendix C.3.10 + ADR-0002:
    - Uses repo_context_from_source to build snapshot
    - Infers repo_profile using config-first approach (ADR-0002)
    - Sets repo_markdown_context from snapshot.full_markdown
    
    Config-First Profile Resolution (ADR-0002):
    1. .integration-coworker.yaml config file (priority 1)
    2. LLM inference to generate config (priority 2)
    3. Archetype detection fallback (deprecated, priority 3)
    
    Provider abstraction (V2):
    - Supports both filesystem and GitHub API access
    - Auto-detects source type from repo_root or repo_source string
    """
    if not state.repo_root:
        state.completed_steps.append("attach_repo_context")
        return state

    try:
        # Check if a provider source is configured via options
        repo_source = None
        github_token = None
        github_ref = None
        use_llm_inference = True  # ADR-0002: default to using LLM fallback
        
        if state.options:
            repo_source = getattr(state.options, 'repo_source', None)
            github_token = getattr(state.options, 'github_token', None)
            github_ref = getattr(state.options, 'github_ref', None)
            # Allow disabling LLM inference via options
            use_llm_inference = getattr(state.options, 'use_llm_inference', True)

        # Determine source - prefer explicit repo_source, fallback to repo_root
        source = repo_source if repo_source else state.repo_root
        
        # Use the unified context provider
        snapshot = repo_context_from_source(
            source,
            github_token=github_token,
            github_ref=github_ref,
        )
        state.repo_snapshot = snapshot
        state.repo_markdown_context = snapshot.full_markdown if snapshot else None

        # Infer repo_profile if not explicitly provided
        if state.repo_profile is None:
            # Check if this is a remote source (GitHub)
            is_remote = repo_source and (
                repo_source.startswith("https://github.com/") or 
                ("/" in repo_source and not repo_source.startswith("/"))
            )
            
            if is_remote:
                # For GitHub provider, we can't do local config file detection
                # Use a basic profile based on snapshot metadata
                from integration_coworker.repo.models import RepoProfile
                state.repo_profile = RepoProfile(
                    name=snapshot.repo_name if snapshot else "unknown",
                    language="python",  # Default, could be improved with API metadata
                    integrations_root="integrations",
                    tests_root="tests",
                    profile_source="github_metadata",
                )
            else:
                # Local detection using config-first approach (ADR-0002)
                state.repo_profile = _get_profile_config_first(
                    state.repo_root,
                    use_llm_fallback=use_llm_inference,
                )
                
        logger.info(
            f"Repo profile resolved: {state.repo_profile.name} "
            f"(source: {getattr(state.repo_profile, 'profile_source', 'unknown')})"
        )

    except Exception as e:
        state.errors.append(f"Failed to attach repo context: {str(e)}")
        logger.exception("Failed to attach repo context")

    state.completed_steps.append("attach_repo_context")
    return state


def _get_profile_config_first(
    repo_root: str,
    use_llm_fallback: bool = True,
):
    """
    Get repository profile using config-first approach per ADR-0002.
    
    Resolution order:
    1. .integration-coworker.yaml config file (instant, accurate)
    2. LLM inference (generates and caches config)
    3. Archetype detection fallback (deprecated, for backward compat)
    
    Returns:
        RepoProfile instance
    """
    from integration_coworker.repo.config_schema import load_config, config_to_profile
    from integration_coworker.repo.models import RepoProfile
    from integration_coworker.repo.detection import detect_repo_profile, build_effective_repo_profile
    
    repo_path = Path(repo_root)
    
    # -------------------------------------------------------------------------
    # Priority 1: Config file (ADR-0002 Milestone 1)
    # -------------------------------------------------------------------------
    for config_name in [".integration-coworker.yaml", ".integration-coworker.yml"]:
        config_file = repo_path / config_name
        if config_file.exists():
            config = load_config(config_file)
            if config:
                logger.info(f"Using config file: {config_file}")
                profile = config_to_profile(config)
                profile.profile_source = "config_file"
                return profile
    
    # -------------------------------------------------------------------------
    # Priority 2: LLM inference (ADR-0002 Milestone 2)
    # -------------------------------------------------------------------------
    if use_llm_fallback:
        try:
            from integration_coworker.repo.llm_inference import infer_repo_config
            
            config = infer_repo_config(repo_path, save_to_file=True)
            if config:
                logger.info("Using LLM-inferred config (saved to .integration-coworker.yaml)")
                profile = config_to_profile(config)
                profile.profile_source = "llm_inference"
                return profile
        except Exception as e:
            logger.warning(f"LLM inference failed, falling back to archetype: {e}")
    
    # -------------------------------------------------------------------------
    # Priority 3: Archetype detection fallback (ADR-0002 Milestone 3: deprecated)
    # -------------------------------------------------------------------------
    import warnings
    warnings.warn(
        "Archetype-based detection is deprecated per ADR-0002. "
        "Create a .integration-coworker.yaml config file or enable LLM inference.",
        DeprecationWarning,
        stacklevel=3,
    )
    
    logger.info("Falling back to archetype detection (deprecated)")
    detected = detect_repo_profile(repo_root)
    profile = build_effective_repo_profile(detected, repo_root)
    profile.profile_source = "archetype_deprecated"
    return profile
