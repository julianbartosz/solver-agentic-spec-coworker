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
        "tsconfig.base.json",  # Bug #2 fix: Nx monorepos use this
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
2. Where to place workflow/service code (use "flows" directory, NOT "workflows")
3. Where to place tests
4. Naming conventions based on existing patterns

IMPORTANT: For flows_dir, always use "flows/" as the directory name, NOT "workflows/".
The Integration Coworker always creates files in a "flows/" directory.

Output a valid YAML configuration following this schema:

```yaml
version: "1.0"
profile:
  name: "<descriptive-name>"
  framework: "<detected-framework-or-custom>"
  language: "<detected-language>"
layout:
  integrations_root: "<path-to-integrations-directory>"
  tests_root: "<path-to-test-directory>"
  clients_dir: "<optional-subdirectory-for-clients>"
  flows_dir: "flows/"  # MUST be "flows/", not "workflows/"
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
    import time
    from integration_coworker.llm.client import call_llm_for_node
    
    logger.info(f"Inferring repo config via LLM for: {repo_root}")
    
    # Gather context
    tree = _sample_repo_structure(repo_root)
    config_files = _get_config_files_content(repo_root)
    existing_integrations = _find_existing_integrations(repo_root)
    
    # Build prompt
    prompt = _build_inference_prompt(
        repo_root, tree, config_files, existing_integrations
    )
    
    # Bug #47 fix: Add retry with exponential backoff for rate limits
    MAX_RETRIES = 3
    response = None
    
    for attempt in range(MAX_RETRIES):
        try:
            # Call LLM with verbose logging for debugging (Bug #39 fix)
            logger.info(f"Calling LLM for repo config inference (attempt {attempt + 1}/{MAX_RETRIES}, prompt length: {len(prompt)} chars)")
            logger.debug(f"Repo structure sample:\n{tree[:500]}..." if len(tree) > 500 else f"Repo structure:\n{tree}")
            
            # Use archetype-based LLM call (system_prompt is in archetype YAML)
            response = call_llm_for_node("repo_config_inference", prompt)
            
            if response:
                break  # Success, exit retry loop
            
            logger.warning(f"LLM returned empty response (attempt {attempt + 1})")
            
        except Exception as e:
            error_str = str(e).lower()
            # Check for rate limit errors (429, rate limit, too many requests)
            is_rate_limit = any(term in error_str for term in ['429', 'rate limit', 'too many requests', 'ratelimit'])
            
            if is_rate_limit and attempt < MAX_RETRIES - 1:
                wait_time = 2 ** (attempt + 1)  # 2, 4, 8 seconds
                logger.warning(f"Rate limited on repo config inference, waiting {wait_time}s before retry...")
                time.sleep(wait_time)
                continue
            else:
                logger.warning(f"LLM error during repo config inference: {e}")
                if attempt == MAX_RETRIES - 1:
                    logger.warning("Exhausted retries for LLM repo config inference, falling back to convention detection")
                    return None
    
    # Log response for debugging
    if not response:
        logger.warning("LLM returned empty response for repo config inference after all retries")
        return None
    
    try:
        logger.info(f"LLM response received ({len(response)} chars)")
        logger.debug(f"LLM response preview: {response[:500]}..." if len(response) > 500 else f"LLM response: {response}")
        
        # Parse YAML from response
        import yaml
        
        # Extract YAML block if wrapped in markdown code fence
        yaml_content = response
        if "```yaml" in response:
            start = response.find("```yaml") + 7
            end = response.find("```", start)
            if end > start:
                yaml_content = response[start:end].strip()
            else:
                logger.warning("Found ```yaml but no closing ```, using full response")
        elif "```" in response:
            start = response.find("```") + 3
            end = response.find("```", start)
            if end > start:
                yaml_content = response[start:end].strip()
            else:
                logger.warning("Found ``` but no closing ```, using full response")
        
        if not yaml_content.strip():
            logger.warning("Extracted YAML content is empty after parsing code fences")
            return None
        
        logger.debug(f"Extracted YAML content ({len(yaml_content)} chars): {yaml_content[:300]}...")
        
        try:
            config_dict = yaml.safe_load(yaml_content)
        except yaml.YAMLError as yaml_err:
            logger.warning(f"Failed to parse LLM response as YAML: {yaml_err}")
            logger.debug(f"Raw YAML content that failed:\n{yaml_content[:500]}")
            return None
        
        if not config_dict:
            logger.warning("LLM returned empty config after YAML parsing")
            return None
        
        # Bug #90 Fix: Add default values for required fields if LLM didn't provide them
        # This prevents validation failures when LLM returns partial config
        if "profile" not in config_dict:
            config_dict["profile"] = {}
        
        profile = config_dict["profile"]
        if "name" not in profile:
            # Generate name from framework or language
            framework = profile.get("framework", "generic")
            language = profile.get("language", "python")
            profile["name"] = f"{framework}_{language}_profile"
            logger.info(f"Added default profile.name: {profile['name']}")
        
        if "layout" not in config_dict:
            config_dict["layout"] = {}
        
        layout = config_dict["layout"]
        language = config_dict.get("profile", {}).get("language", "python")
        
        if "integrations_root" not in layout:
            # Default based on language conventions
            if language in ("python",):
                layout["integrations_root"] = "src/integrations"
            elif language in ("typescript", "javascript"):
                layout["integrations_root"] = "src/integrations"
            else:
                layout["integrations_root"] = "integrations"
            logger.info(f"Added default layout.integrations_root: {layout['integrations_root']}")
        
        if "tests_root" not in layout:
            # Default based on language conventions
            if language in ("python",):
                layout["tests_root"] = "tests/integrations"
            elif language in ("typescript", "javascript"):
                layout["tests_root"] = "tests"
            else:
                layout["tests_root"] = "tests"
            logger.info(f"Added default layout.tests_root: {layout['tests_root']}")
        
        # V41-004 Fix: Normalize flows_dir to always use 'flows', not 'workflows'
        # The LLM sometimes generates 'workflows' but the coworker creates files in 'flows/'
        # This causes import path mismatches (Bug report from NoteDiscovery integration)
        if "flows_dir" in layout:
            flows_dir = layout["flows_dir"]
            if "workflow" in flows_dir.lower():
                original = flows_dir
                # Normalize: workflows/ -> flows/, workflow/ -> flows/
                flows_dir = flows_dir.replace("workflows", "flows").replace("workflow", "flows")
                layout["flows_dir"] = flows_dir
                logger.info(f"[V41-004] Normalized flows_dir: '{original}' -> '{flows_dir}'")
        
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
        
        logger.info(f"LLM repo config inference successful: {config.profile.name if config.profile else 'unnamed'}")
        return config
        
    except Exception as e:
        logger.error(f"Failed to infer repo config via LLM: {type(e).__name__}: {e}")
        logger.debug("Full LLM inference exception:", exc_info=True)
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
            logger.warning(f"LLM inference failed, falling back to heuristics: {e}")
    
    # -------------------------------------------------------------------------
    # Priority 3: Heuristic detection fallback
    # -------------------------------------------------------------------------
    from integration_coworker.repo.detection import detect_repo_profile, build_effective_repo_profile
    
    logger.info("Falling back to heuristic detection")
    detected = detect_repo_profile(repo_path)
    profile = build_effective_repo_profile(detected, str(repo_path))
    profile.profile_source = "heuristic_fallback"
    return profile
