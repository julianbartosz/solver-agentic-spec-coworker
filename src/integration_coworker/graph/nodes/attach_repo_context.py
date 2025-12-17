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
    
    Bug #3 Fix (V2.3): Two-Phase Profile Loading
    - plan_run does fast config+convention detection (no LLM)
    - This node refines with LLM if profile source is low-confidence
    
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

        # Bug #3 Fix: Check if profile needs LLM refinement
        # plan_run may have set a low-confidence profile (convention_inference or default)
        # that should be refined with LLM for better accuracy
        needs_llm_refinement = _should_refine_profile_with_llm(state.repo_profile)
        
        # Infer repo_profile if not explicitly provided OR needs refinement
        if state.repo_profile is None or (needs_llm_refinement and use_llm_inference):
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
                # Bug #3 Fix: Now includes LLM inference for refinement
                if needs_llm_refinement:
                    logger.info(
                        f"Profile source '{getattr(state.repo_profile, 'profile_source', 'unknown')}' "
                        f"is low-confidence, attempting LLM refinement..."
                    )
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


def _should_refine_profile_with_llm(repo_profile) -> bool:
    """
    Check if a profile should be refined with LLM inference.
    
    Bug #3 Fix: plan_run eagerly loads profiles with use_llm_fallback=False,
    which means it only uses config file or convention inference. Profiles
    from convention_inference or default sources are low-confidence and
    should be refined with LLM when this node runs.
    
    Args:
        repo_profile: The current RepoProfile (may be None)
    
    Returns:
        True if profile should be refined with LLM, False otherwise
    """
    if repo_profile is None:
        return False  # Will do full detection anyway
    
    profile_source = getattr(repo_profile, 'profile_source', None)
    
    # High-confidence sources that don't need refinement
    high_confidence_sources = {
        'config_file',      # User explicitly provided config
        'llm_inference',    # Already used LLM
        'string_parameter', # User explicitly specified
    }
    
    if profile_source in high_confidence_sources:
        return False
    
    # Low-confidence sources that benefit from LLM refinement
    low_confidence_sources = {
        'convention_inference',  # Basic file pattern matching
        'default',               # No detection at all
        'generic_fallback',      # Last resort fallback
        'heuristic_fallback',    # Deprecated heuristic detection
    }
    
    return profile_source in low_confidence_sources


def _detect_python_framework(repo_path: Path) -> str:
    """
    Detect Python framework from dependency files.
    
    Scans pyproject.toml and requirements.txt for known framework dependencies.
    Returns framework name or 'generic' if none detected.
    """
    framework_indicators = {
        "fastapi": ["fastapi"],
        "flask": ["flask"],
        "django": ["django"],
        "starlette": ["starlette"],
        "aiohttp": ["aiohttp"],
        "tornado": ["tornado"],
    }
    
    # Collect dependency text from common files
    dep_text = ""
    
    pyproject_file = repo_path / "pyproject.toml"
    if pyproject_file.exists():
        try:
            dep_text += pyproject_file.read_text().lower()
        except Exception:
            pass
    
    requirements_file = repo_path / "requirements.txt"
    if requirements_file.exists():
        try:
            dep_text += requirements_file.read_text().lower()
        except Exception:
            pass
    
    setup_py = repo_path / "setup.py"
    if setup_py.exists():
        try:
            dep_text += setup_py.read_text().lower()
        except Exception:
            pass
    
    # Check for each framework
    for framework, indicators in framework_indicators.items():
        for indicator in indicators:
            if indicator in dep_text:
                logger.debug(f"Detected framework '{framework}' from dependency files")
                return framework
    
    return "generic"


def _infer_profile_from_conventions(repo_path: Path):
    """
    Infer repository profile from project conventions (Bug #39 fix).
    
    This is a fast, no-LLM fallback that uses common project structure
    patterns to generate a sensible profile. Preferred over deprecated
    archetype detection because it's:
    - Faster (no file content scanning beyond dependency files)
    - More predictable (explicit convention rules)
    - Easier to debug
    
    Supported Languages (Bug #89 fix - production audit):
    - Python: pyproject.toml, requirements.txt, setup.py
    - TypeScript/JavaScript: package.json, tsconfig.json
    - Go: go.mod
    - Java: pom.xml, build.gradle, build.gradle.kts
    - Ruby: Gemfile
    - C#: *.csproj, *.sln
    
    Args:
        repo_path: Path to repository root
    
    Returns:
        RepoProfile if conventions detected, None otherwise
    """
    from integration_coworker.repo.models import RepoProfile
    from integration_coworker.repo.profiles import has_typescript_config
    
    # Check for Python project indicators
    has_pyproject = (repo_path / "pyproject.toml").exists()
    has_requirements = (repo_path / "requirements.txt").exists()
    has_setup_py = (repo_path / "setup.py").exists()
    has_src_dir = (repo_path / "src").is_dir()
    
    # Check for Node.js/TypeScript indicators (Bug #2 fix: use utility)
    has_package_json = (repo_path / "package.json").exists()
    has_tsconfig = has_typescript_config(repo_path)  # Handles tsconfig.base.json, etc.
    
    # Bug #89 Fix: Check for Go project indicators
    has_go_mod = (repo_path / "go.mod").exists()
    
    # Bug #89 Fix: Check for Java project indicators
    has_pom_xml = (repo_path / "pom.xml").exists()
    has_gradle = (repo_path / "build.gradle").exists()
    has_gradle_kts = (repo_path / "build.gradle.kts").exists()
    
    # Bug #89 Fix: Check for Ruby project indicators
    has_gemfile = (repo_path / "Gemfile").exists()
    
    # Bug #89 Fix: Check for C# project indicators
    has_csproj = any(repo_path.glob("*.csproj"))
    has_sln = any(repo_path.glob("*.sln"))
    
    # -------------------------------------------------------------------------
    # Language Detection Priority Order (most specific first)
    # -------------------------------------------------------------------------
    
    # 1. Go project (go.mod is definitive)
    if has_go_mod:
        profile = RepoProfile(
            name=repo_path.name,
            language="go",
            framework="generic",
            integrations_root="internal/integrations",
            tests_root="internal/integrations",  # Go tests are colocated
            conventions={
                "client_module_pattern": "clients/{provider}.go",
                "flow_module_pattern": "flows/{provider}_{task}.go",
                "test_module_pattern": "{provider}_{task}_test.go",  # Go test convention
            },
        )
        profile.profile_source = "convention_inference"
        logger.info("Convention inference: Go project (detected go.mod)")
        return profile
    
    # 2. Java project (Maven or Gradle)
    if has_pom_xml or has_gradle or has_gradle_kts:
        build_tool = "maven" if has_pom_xml else "gradle"
        profile = RepoProfile(
            name=repo_path.name,
            language="java",
            framework=build_tool,
            integrations_root="src/main/java/integrations",
            tests_root="src/test/java/integrations",
            conventions={
                "client_module_pattern": "clients/{Provider}Client.java",
                "flow_module_pattern": "flows/{Provider}{Task}Flow.java",
                "test_module_pattern": "{Provider}{Task}Test.java",
            },
        )
        profile.profile_source = "convention_inference"
        logger.info(f"Convention inference: Java/{build_tool} project")
        return profile
    
    # 3. Ruby project (Gemfile)
    if has_gemfile:
        profile = RepoProfile(
            name=repo_path.name,
            language="ruby",
            framework="generic",
            integrations_root="lib/integrations",
            tests_root="spec/integrations",  # RSpec convention
            conventions={
                "client_module_pattern": "clients/{provider}_client.rb",
                "flow_module_pattern": "flows/{provider}_{task}_flow.rb",
                "test_module_pattern": "{provider}_{task}_spec.rb",
            },
        )
        profile.profile_source = "convention_inference"
        logger.info("Convention inference: Ruby project (detected Gemfile)")
        return profile
    
    # 4. C# project (.csproj or .sln)
    if has_csproj or has_sln:
        profile = RepoProfile(
            name=repo_path.name,
            language="csharp",
            framework="dotnet",
            integrations_root="src/Integrations",
            tests_root="tests/Integrations",
            conventions={
                "client_module_pattern": "Clients/{Provider}Client.cs",
                "flow_module_pattern": "Flows/{Provider}{Task}Flow.cs",
                "test_module_pattern": "{Provider}{Task}Tests.cs",
            },
        )
        profile.profile_source = "convention_inference"
        logger.info("Convention inference: C#/.NET project")
        return profile
    
    # 5. Python project
    if has_pyproject or has_requirements or has_setup_py:
        # Python project - detect framework from dependencies
        framework = _detect_python_framework(repo_path)
        
        if has_src_dir:
            # src-layout Python project
            integrations_root = "src/integrations"
            tests_root = "tests/integrations"
        else:
            # flat-layout Python project
            integrations_root = "integrations"
            tests_root = "tests/integrations"
        
        profile = RepoProfile(
            name=repo_path.name,
            language="python",
            framework=framework,
            integrations_root=integrations_root,
            tests_root=tests_root,
            conventions={
                "client_module_pattern": "clients/{provider}.py",
                "flow_module_pattern": "flows/{provider}_{task}.py",
                "test_module_pattern": "test_{provider}_{task}.py",
            },
        )
        profile.profile_source = "convention_inference"
        logger.info(f"Convention inference: Python/{framework} project with {'src-layout' if has_src_dir else 'flat-layout'}")
        return profile
    
    # 6. Node.js/TypeScript project (check after language-specific build files)
    if has_package_json:
        # Node.js/TypeScript project
        language = "typescript" if has_tsconfig else "javascript"
        ext = "ts" if has_tsconfig else "js"
        
        profile = RepoProfile(
            name=repo_path.name,
            language=language,
            framework="generic",
            integrations_root="src/integrations",
            tests_root="tests/integrations",
            conventions={
                "client_module_pattern": f"clients/{{provider}}.{ext}",
                "flow_module_pattern": f"flows/{{provider}}/{{task}}.{ext}",
                "test_module_pattern": f"{{provider}}/{{task}}.test.{ext}",
            },
        )
        profile.profile_source = "convention_inference"
        logger.info(f"Convention inference: {language.capitalize()} project")
        return profile
    
    # No clear conventions detected - return None to fall through
    logger.debug("No project conventions detected, falling back to archetype detection")
    return None


def _get_profile_config_first(
    repo_root: str,
    use_llm_fallback: bool = True,
):
    """
    Get repository profile using config-first approach per ADR-0002.
    
    V2.2 (Dynamic Capability Fix #5): Simplified to 2-tier system.
    
    Resolution order:
    1. .integration-coworker.yaml config file (instant, accurate)
    2. LLM inference (generates and caches config)
    3. Sensible defaults (no archetype detection - removed per Fix #5)
    
    The archetype detection system has been removed because:
    - It was slow (scanned many files)
    - It was unreliable (heuristics often wrong)
    - It was deprecated per ADR-0002
    
    Returns:
        RepoProfile instance
    """
    from integration_coworker.repo.config_schema import load_config, config_to_profile
    from integration_coworker.repo.models import RepoProfile
    
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
            
            logger.info("No config file found, attempting LLM inference for repo layout...")
            config = infer_repo_config(repo_path, save_to_file=True)
            if config:
                logger.info("Using LLM-inferred config (saved to .integration-coworker.yaml)")
                profile = config_to_profile(config)
                profile.profile_source = "llm_inference"
                return profile
            else:
                logger.warning("LLM inference returned None - using convention fallback")
        except ImportError as e:
            logger.warning(f"LLM inference not available (missing dependency): {e}")
        except Exception as e:
            logger.warning(
                f"LLM inference failed, using convention fallback. "
                f"Error: {type(e).__name__}: {e}"
            )
            logger.debug(f"Full LLM inference exception:", exc_info=True)
    else:
        logger.info("LLM inference disabled (use_llm_fallback=False), using convention fallback")
    
    # -------------------------------------------------------------------------
    # Priority 3: Convention-based inference (Fix #5 - replaces archetype detection)
    # -------------------------------------------------------------------------
    # This is a fast, deterministic fallback that uses project structure
    # conventions instead of the deprecated archetype detection system.
    convention_profile = _infer_profile_from_conventions(repo_path)
    if convention_profile:
        logger.info(f"Using convention-inferred profile: {convention_profile.name}")
        return convention_profile
    
    # -------------------------------------------------------------------------
    # Priority 4: Sensible defaults (V2.2 Fix #5 - replaces archetype detection)
    # -------------------------------------------------------------------------
    # When no conventions are detected, use sensible defaults rather than
    # the unreliable archetype detection system.
    logger.info("No conventions detected, using sensible default profile")
    default_profile = RepoProfile(
        name=repo_path.name,
        language="python",  # Default to Python as most common
        framework="generic",
        integrations_root="integrations",
        tests_root="tests/integrations",
        conventions={
            "client_module_pattern": "clients/{provider}.py",
            "flow_module_pattern": "flows/{provider}_{task}.py",
            "test_module_pattern": "test_{provider}_{task}.py",
        },
    )
    default_profile.profile_source = "default"
    return default_profile
