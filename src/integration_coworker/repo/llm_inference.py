"""
LLM-based repository config inference.

Per ADR-0002 Phase 2: When no .integration-coworker.yaml exists,
use LLM to analyze repository structure and generate config.
"""
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from integration_coworker.repo.config_schema import (
    IntegrationCoworkerConfig,
    ProfileConfig,
    LayoutConfig,
    ConventionsConfig,
    HooksConfig,
    MetadataConfig,
    save_config,
    validate_config,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Repo Sampling Utilities
# =============================================================================


def _sample_repo_structure(repo_root: Path, max_files: int = 50) -> str:
    """
    Create a compact representation of repo structure for LLM analysis.
    
    Args:
        repo_root: Path to repository root
        max_files: Maximum files to include in sample
    
    Returns:
        String representation of directory tree
    """
    lines = []
    file_count = 0
    
    # Common directories to skip
    skip_dirs = {
        ".git", ".venv", "venv", "node_modules", "__pycache__", 
        ".pytest_cache", "dist", "build", ".next", ".nuxt",
        "coverage", ".nyc_output", ".idea", ".vscode"
    }
    
    def walk_tree(path: Path, prefix: str = "", depth: int = 0):
        nonlocal file_count
        
        if depth > 4:  # Limit depth
            return
        if file_count > max_files:
            return
            
        try:
            entries = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        except PermissionError:
            return
            
        for i, entry in enumerate(entries):
            if entry.name.startswith(".") and entry.name not in {".gitignore"}:
                continue
            if entry.name in skip_dirs:
                continue
                
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                extension = "    " if is_last else "│   "
                walk_tree(entry, prefix + extension, depth + 1)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")
                file_count += 1
                
                if file_count > max_files:
                    lines.append(f"{prefix}    ... (truncated)")
                    return
    
    walk_tree(repo_root)
    return "\n".join(lines)


def _get_config_files_content(repo_root: Path) -> Dict[str, str]:
    """
    Read key config files that help determine project structure.
    
    Returns dict of filename -> content (truncated).
    """
    config_files = [
        "package.json",
        "pyproject.toml",
        "tsconfig.json",
        "requirements.txt",
        "setup.py",
        "Cargo.toml",
        "go.mod",
    ]
    
    result = {}
    for filename in config_files:
        path = repo_root / filename
        if path.exists():
            try:
                content = path.read_text()[:2000]  # Truncate
                result[filename] = content
            except Exception:
                pass
    
    return result


def _find_existing_integrations(repo_root: Path) -> List[Dict[str, str]]:
    """
    Find existing integration files to learn from their placement.
    
    Returns list of dicts with path and content preview.
    """
    # Common patterns for integration/client code
    patterns = [
        "**/client*.py",
        "**/clients/*.py",
        "**/integrations/**/*.py",
        "**/external/**/*.py",
        "**/services/**/*.py",
        "**/api/**/*.ts",
        "**/lib/**/*.ts",
    ]
    
    integrations = []
    seen_dirs = set()
    
    for pattern in patterns:
        for path in repo_root.glob(pattern):
            if "__pycache__" in str(path) or "node_modules" in str(path):
                continue
            if path.parent in seen_dirs:
                continue
                
            try:
                content = path.read_text()[:500]
                if "import" in content or "from" in content or "require" in content:
                    integrations.append({
                        "path": str(path.relative_to(repo_root)),
                        "preview": content,
                    })
                    seen_dirs.add(path.parent)
                    
                    if len(integrations) >= 3:  # Limit to 3 examples
                        break
            except Exception:
                pass
                
        if len(integrations) >= 3:
            break
    
    return integrations


# =============================================================================
# LLM Inference
# =============================================================================


def _build_inference_prompt(
    repo_root: Path,
    tree: str,
    config_files: Dict[str, str],
    existing_integrations: List[Dict[str, str]],
) -> str:
    """
    Build the prompt for LLM config inference.
    """
    prompt = f"""Analyze this repository structure and generate an integration configuration.

## Repository Structure
```
{tree}
```

## Configuration Files
"""
    
    for filename, content in config_files.items():
        prompt += f"\n### {filename}\n```\n{content}\n```\n"
    
    if existing_integrations:
        prompt += "\n## Existing Integration Examples\n"
        for integration in existing_integrations:
            prompt += f"\n### {integration['path']}\n```\n{integration['preview']}\n```\n"
    
    prompt += """

## Task
Generate a YAML configuration for the Integration Coworker tool that specifies:
1. Where to place new API client code
2. Where to place workflow/service code  
3. Where to place tests
4. Naming conventions based on existing patterns

Output a valid YAML configuration following this schema:

```yaml
version: "1.0"
profile:
  name: "<descriptive-name>"
  framework: "<detected-framework-or-custom>"
  language: "<python|typescript|javascript>"
layout:
  integrations_root: "<path-to-integrations-directory>"
  tests_root: "<path-to-test-directory>"
  clients_dir: "<optional-subdirectory-for-clients>"
  flows_dir: "<optional-subdirectory-for-flows>"
conventions:
  client_module: "<pattern-with-{provider}-placeholder>"
  flow_module: "<pattern-with-{provider}-and-{task}-placeholders>"
  test_module: "<pattern-with-{provider}-and-{task}-placeholders>"
hooks:
  router_file: "<optional-file-for-route-registration>"
  router_marker: "<optional-marker-comment>"
```

Respond with ONLY the YAML configuration, no explanations."""

    return prompt


def infer_repo_config(
    repo_root: Path,
    save_to_file: bool = True,
) -> Optional[IntegrationCoworkerConfig]:
    """
    Use LLM to infer repository configuration.
    
    Per ADR-0002 Phase 2:
    1. Sample repository structure
    2. Read config files (package.json, pyproject.toml, etc.)
    3. Find existing integration patterns
    4. Call LLM to generate config
    5. Validate and optionally save to .integration-coworker.yaml
    
    Args:
        repo_root: Path to repository root
        save_to_file: Whether to save generated config to file
    
    Returns:
        IntegrationCoworkerConfig if successful, None otherwise
    """
    from integration_coworker.llm.client import call_llm
    
    logger.info(f"Inferring repo config via LLM for: {repo_root}")
    
    # Gather context
    tree = _sample_repo_structure(repo_root)
    config_files = _get_config_files_content(repo_root)
    existing_integrations = _find_existing_integrations(repo_root)
    
    # Build prompt
    prompt = _build_inference_prompt(
        repo_root, tree, config_files, existing_integrations
    )
    
    try:
        # Call LLM
        response = call_llm(
            prompt,
            task_type="repo_config_inference",
            system_prompt=(
                "You are an expert at analyzing code repositories and determining "
                "appropriate file structures. Generate precise, valid YAML configurations."
            ),
        )
        
        # Parse YAML from response
        import yaml
        
        # Extract YAML block if wrapped in markdown code fence
        yaml_content = response
        if "```yaml" in response:
            start = response.find("```yaml") + 7
            end = response.find("```", start)
            yaml_content = response[start:end].strip()
        elif "```" in response:
            start = response.find("```") + 3
            end = response.find("```", start)
            yaml_content = response[start:end].strip()
        
        config_dict = yaml.safe_load(yaml_content)
        
        if not config_dict:
            logger.warning("LLM returned empty config")
            return None
        
        # Add metadata
        if "metadata" not in config_dict:
            config_dict["metadata"] = {}
        config_dict["metadata"]["detection_method"] = "llm_inference"
        
        # Parse and validate
        config = IntegrationCoworkerConfig.model_validate(config_dict)
        
        # Validate against actual repo structure
        validation_result = validate_config(config, repo_root, check_paths_exist=True)
        
        if not validation_result.is_valid:
            logger.warning(f"Generated config has validation errors: {validation_result.errors}")
            # Still return config but with warnings logged
        
        for warning in validation_result.warnings:
            logger.info(f"Config validation warning: {warning}")
        
        # Save to file if requested
        if save_to_file:
            config_path = repo_root / ".integration-coworker.yaml"
            if save_config(config, config_path):
                logger.info(f"Saved inferred config to {config_path}")
        
        return config
        
    except Exception as e:
        logger.error(f"Failed to infer repo config via LLM: {e}")
        return None


# =============================================================================
# Config-First Profile Resolution (ADR-0002)
# =============================================================================


def get_repo_profile_config_first(
    repo_root: Path,
    use_llm_fallback: bool = True,
    save_llm_config: bool = True,
):
    """
    Get repository profile using config-first approach per ADR-0002.
    
    Resolution order:
    1. .integration-coworker.yaml config file (instant, accurate)
    2. LLM inference (generates and caches config)
    3. Archetype detection fallback (deprecated, for backward compat)
    
    Args:
        repo_root: Path to repository root
        use_llm_fallback: Whether to use LLM when no config exists
        save_llm_config: Whether to save LLM-generated config to file
    
    Returns:
        RepoProfile instance
    """
    from integration_coworker.repo.config_schema import load_config, config_to_profile
    from integration_coworker.repo.profiles import detect_profile_from_repo
    
    repo_path = Path(repo_root)
    
    # -------------------------------------------------------------------------
    # Priority 1: Config file (ADR-0002 Phase 1)
    # -------------------------------------------------------------------------
    config_file = repo_path / ".integration-coworker.yaml"
    if config_file.exists():
        config = load_config(config_file)
        if config:
            logger.info(f"Using config file: {config_file}")
            profile = config_to_profile(config)
            profile.profile_source = "config_file"
            return profile
    
    # Also check .yml extension
    config_file_yml = repo_path / ".integration-coworker.yml"
    if config_file_yml.exists():
        config = load_config(config_file_yml)
        if config:
            logger.info(f"Using config file: {config_file_yml}")
            profile = config_to_profile(config)
            profile.profile_source = "config_file"
            return profile
    
    # -------------------------------------------------------------------------
    # Priority 2: LLM inference (ADR-0002 Phase 2)
    # -------------------------------------------------------------------------
    if use_llm_fallback:
        try:
            config = infer_repo_config(repo_path, save_to_file=save_llm_config)
            if config:
                logger.info("Using LLM-inferred config")
                profile = config_to_profile(config)
                profile.profile_source = "llm_inference"
                return profile
        except Exception as e:
            logger.warning(f"LLM inference failed, falling back to archetype: {e}")
    
    # -------------------------------------------------------------------------
    # Priority 3: Archetype detection fallback (deprecated)
    # -------------------------------------------------------------------------
    import warnings
    warnings.warn(
        "Archetype-based detection is deprecated. "
        "Create a .integration-coworker.yaml config file or enable LLM inference. "
        "See ADR-0002 for migration guidance.",
        DeprecationWarning,
        stacklevel=2,
    )
    
    logger.info("Falling back to archetype detection (deprecated)")
    profile = detect_profile_from_repo(repo_path)
    profile.profile_source = "archetype_deprecated"
    return profile
