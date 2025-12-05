"""
Two-Layer RepoProfile Detection Pipeline.

Layer 1 (Detection): detect_repo_profile() -> DetectedProfile
    - Identifies framework archetype with confidence scoring
    - Collects evidence (files/patterns matched)
    
Layer 2 (Inference): build_effective_repo_profile() -> RepoProfile
    - High confidence + known archetype → use archetype defaults
    - Low confidence or unknown → run heuristics to infer layout
    - Very uncertain → use LLM-assisted refinement

This design allows:
- Efficiency for common stacks (skip re-inference)
- Flexibility for real-world messiness
- Learning for subsequent runs
"""
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from integration_coworker.repo.models import DetectedProfile, RepoProfile

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIDENCE THRESHOLDS
# =============================================================================

# Threshold for using archetype defaults without refinement
HIGH_CONFIDENCE_THRESHOLD = 0.8

# Threshold below which we warn about unreliable detection
LOW_CONFIDENCE_THRESHOLD = 0.4

# Threshold below which we try LLM refinement if enabled
VERY_LOW_CONFIDENCE_THRESHOLD = 0.3


# =============================================================================
# KNOWN ARCHETYPES - Framework-specific layouts and hooks
# =============================================================================
#
# .. deprecated:: 2.1
#     KNOWN_ARCHETYPES and FrameworkArchetypeConfig are deprecated per ADR-0002.
#     Prefer using .integration-coworker.yaml config files for repo-aware integration.
#     These archetypes will be removed in v3.0.
#

@dataclass
class FrameworkArchetypeConfig:
    """
    Configuration for a known framework archetype.
    
    Defines:
    - Standard directory layouts
    - Integration hooks (files to update)
    - Naming conventions
    - Framework-specific patterns
    
    .. deprecated:: 2.1
        This class is deprecated per ADR-0002. Prefer using 
        IntegrationCoworkerConfig from config_schema.py instead.
        Will be removed in v3.0.
    """
    name: str
    framework: str
    language: str

    # Standard layout
    default_integrations_root: str
    default_tests_root: str

    # Directory patterns
    clients_dir_pattern: str = "clients"
    flows_dir_pattern: str = "flows"  # or "services" for some frameworks

    # File naming conventions
    client_module_pattern: str = "clients/{provider}.py"
    flow_module_pattern: str = "flows/{provider}_{task}.py"
    test_module_pattern: str = "test_{provider}_{task}.py"

    # Integration hooks - files to update when wiring integrations
    router_file: Optional[str] = None
    router_marker: Optional[str] = None
    settings_file: Optional[str] = None
    settings_marker: Optional[str] = None
    module_file: Optional[str] = None  # For NestJS modules, etc.

    # Detection patterns
    detection_files: List[str] = field(default_factory=list)  # Files that indicate this framework
    detection_deps: List[str] = field(default_factory=list)   # Dependencies in package.json/pyproject.toml


# Known framework archetypes with their configurations
# DEPRECATED: See ADR-0002 for config-first approach. Will be removed in v3.0.
KNOWN_ARCHETYPES: Dict[str, FrameworkArchetypeConfig] = {
    "fastapi": FrameworkArchetypeConfig(
        name="fastapi",
        framework="fastapi",
        language="python",
        default_integrations_root="app/integrations",
        default_tests_root="tests/integrations",
        clients_dir_pattern="clients",
        flows_dir_pattern="services",
        client_module_pattern="clients/{provider}.py",
        flow_module_pattern="services/{provider}_{task}.py",
        test_module_pattern="test_{provider}_{task}.py",
        router_file="app/api/router.py",
        router_marker="# <AUTO_INTEGRATION_ROUTER>",
        settings_file="app/core/settings.py",
        settings_marker="# <AUTO_INTEGRATION_SETTINGS>",
        detection_files=["main.py", "app/main.py"],
        detection_deps=["fastapi", "uvicorn"],
    ),

    "django": FrameworkArchetypeConfig(
        name="django",
        framework="django",
        language="python",
        default_integrations_root="integrations",
        default_tests_root="tests/integrations",
        clients_dir_pattern="clients",
        flows_dir_pattern="services",
        client_module_pattern="clients/{provider}.py",
        flow_module_pattern="services/{provider}_{task}.py",
        test_module_pattern="test_{provider}_{task}.py",
        router_file="urls.py",
        router_marker="# <AUTO_INTEGRATION_URLS>",
        settings_file="settings.py",
        settings_marker="# <AUTO_INTEGRATION_SETTINGS>",
        detection_files=["manage.py", "wsgi.py", "asgi.py"],
        detection_deps=["django", "djangorestframework"],
    ),

    "flask": FrameworkArchetypeConfig(
        name="flask",
        framework="flask",
        language="python",
        default_integrations_root="app/integrations",
        default_tests_root="tests/integrations",
        clients_dir_pattern="clients",
        flows_dir_pattern="services",
        client_module_pattern="clients/{provider}.py",
        flow_module_pattern="services/{provider}_{task}.py",
        test_module_pattern="test_{provider}_{task}.py",
        router_file="app/routes.py",
        router_marker="# <AUTO_INTEGRATION_ROUTES>",
        settings_file="app/config.py",
        settings_marker="# <AUTO_INTEGRATION_CONFIG>",
        detection_files=["app.py", "wsgi.py", "app/__init__.py"],
        detection_deps=["flask"],
    ),

    "nextjs": FrameworkArchetypeConfig(
        name="nextjs",
        framework="nextjs",
        language="typescript",
        default_integrations_root="lib/integrations",
        default_tests_root="__tests__/integrations",
        clients_dir_pattern="clients",
        flows_dir_pattern="services",
        client_module_pattern="clients/{provider}.ts",
        flow_module_pattern="services/{provider}/{task}.ts",
        test_module_pattern="{provider}/{task}.test.ts",
        detection_files=["next.config.js", "next.config.mjs", "next.config.ts"],
        detection_deps=["next", "react"],
    ),

    "express": FrameworkArchetypeConfig(
        name="express",
        framework="express",
        language="typescript",
        default_integrations_root="src/integrations",
        default_tests_root="tests/integrations",
        clients_dir_pattern="clients",
        flows_dir_pattern="services",
        client_module_pattern="clients/{provider}.ts",
        flow_module_pattern="services/{provider}/{task}.ts",
        test_module_pattern="{provider}/{task}.test.ts",
        router_file="src/routes/index.ts",
        router_marker="// <AUTO_INTEGRATION_ROUTES>",
        detection_files=["app.ts", "app.js", "server.ts", "server.js"],
        detection_deps=["express"],
    ),

    "nestjs": FrameworkArchetypeConfig(
        name="nestjs",
        framework="nestjs",
        language="typescript",
        default_integrations_root="src/integrations",
        default_tests_root="test/integrations",
        clients_dir_pattern="clients",
        flows_dir_pattern="services",
        client_module_pattern="clients/{provider}.client.ts",
        flow_module_pattern="services/{provider}-{task}.service.ts",
        test_module_pattern="{provider}-{task}.service.spec.ts",
        module_file="src/integrations/integrations.module.ts",
        detection_files=["nest-cli.json", "src/main.ts"],
        detection_deps=["@nestjs/core", "@nestjs/common"],
    ),
}


# =============================================================================
# LAYER 1: DETECTION
# =============================================================================

def detect_repo_profile(repo_root: Optional[str]) -> DetectedProfile:
    """
    Layer 1: Detect repository archetype with confidence scoring.
    
    Analyzes repository structure to identify:
    - Framework (FastAPI, Django, Next.js, etc.)
    - Primary language
    - Evidence files/patterns
    
    Args:
        repo_root: Path to repository root (can be None)
        
    Returns:
        DetectedProfile with archetype, language, confidence, and evidence
    """
    if repo_root is None:
        return DetectedProfile(
            archetype_name="unknown",
            language="python",
            confidence=0.0,
            evidence=["No repo_root provided"],
        )

    repo_path = Path(repo_root)

    if not repo_path.exists():
        return DetectedProfile(
            archetype_name="unknown",
            language="python",
            confidence=0.0,
            evidence=[f"Repo root does not exist: {repo_root}"],
        )

    # Collect evidence and scores for each archetype
    archetype_scores: Dict[str, Tuple[float, List[str]]] = {}
    detected_paths: Dict[str, str] = {}

    # Check package.json for Node.js projects
    package_json = repo_path / "package.json"
    pkg_deps: Dict[str, str] = {}

    if package_json.exists():
        try:
            pkg_data = json.loads(package_json.read_text())
            deps = pkg_data.get("dependencies", {})
            dev_deps = pkg_data.get("devDependencies", {})
            pkg_deps = {**deps, **dev_deps}
            detected_paths["package_json"] = str(package_json)
        except (json.JSONDecodeError, IOError):
            pass

    # Check Python dependency files
    pyproject_toml = repo_path / "pyproject.toml"
    requirements_txt = repo_path / "requirements.txt"
    python_deps: List[str] = []

    if pyproject_toml.exists():
        try:
            content = pyproject_toml.read_text().lower()
            python_deps = _extract_python_deps_from_pyproject(content)
            detected_paths["pyproject_toml"] = str(pyproject_toml)
        except IOError:
            pass

    if requirements_txt.exists():
        try:
            content = requirements_txt.read_text()
            python_deps.extend(_extract_python_deps_from_requirements(content))
            detected_paths["requirements_txt"] = str(requirements_txt)
        except IOError:
            pass

    # Score each archetype
    for arch_name, arch_config in KNOWN_ARCHETYPES.items():
        score = 0.0
        evidence: List[str] = []

        # Check detection files
        for det_file in arch_config.detection_files:
            file_path = repo_path / det_file
            if file_path.exists():
                score += 0.3
                evidence.append(f"Found {det_file}")
                detected_paths[f"{arch_name}_marker"] = str(file_path)

        # Check dependencies
        if arch_config.language in ("typescript", "javascript"):
            for dep in arch_config.detection_deps:
                if dep in pkg_deps:
                    score += 0.25
                    evidence.append(f"Dependency: {dep}")
        else:  # Python
            for dep in arch_config.detection_deps:
                if any(dep in pd.lower() for pd in python_deps):
                    score += 0.25
                    evidence.append(f"Dependency: {dep}")

        # Check for framework-specific content in main files
        score_boost, extra_evidence = _check_framework_content(repo_path, arch_config)
        score += score_boost
        evidence.extend(extra_evidence)

        # Cap at 1.0
        score = min(score, 1.0)

        if score > 0:
            archetype_scores[arch_name] = (score, evidence)

    # Find best match
    if not archetype_scores:
        # No framework detected - determine language
        language = _detect_primary_language(repo_path)
        return DetectedProfile(
            archetype_name="unknown",
            language=language,
            confidence=0.3,  # Low confidence unknown
            evidence=["No known framework patterns detected"],
            detected_paths=detected_paths,
        )

    # Sort by score descending
    sorted_archetypes = sorted(archetype_scores.items(), key=lambda x: x[1][0], reverse=True)
    best_archetype, (best_score, best_evidence) = sorted_archetypes[0]

    return DetectedProfile(
        archetype_name=best_archetype,
        language=KNOWN_ARCHETYPES[best_archetype].language,
        confidence=best_score,
        evidence=best_evidence,
        detected_paths=detected_paths,
        metadata={
            "all_scores": {k: v[0] for k, v in archetype_scores.items()},
        },
    )


def _extract_python_deps_from_pyproject(content: str) -> List[str]:
    """
    Extract dependency names from pyproject.toml content.
    
    Handles multiple formats:
    - dependencies = ["fastapi", "uvicorn"]  (inline)
    - dependencies = [
          "fastapi",
          "uvicorn",
      ]  (multi-line)
    - Version specifiers (>=, ==, <, ~=)
    - Extras like package[extra]
    - Comments on same line as deps
    - Just searching for known dep names in content
    """
    deps = []

    # First try to parse structured format
    in_deps_section = False

    for line in content.splitlines():
        line = line.strip()
        if "dependencies" in line and "=" in line:
            in_deps_section = True
            # Handle inline list like: dependencies = ["fastapi", "uvicorn"]
            if "[" in line:
                # Extract deps from inline format
                inline_deps = _extract_inline_deps(line)
                deps.extend(inline_deps)
                if "]" in line:
                    in_deps_section = False
            continue
        if in_deps_section:
            if line.startswith("]") or ("]" in line and not line.startswith('"') and not line.startswith("'")):
                in_deps_section = False
            elif line.startswith('"') or line.startswith("'"):
                # Remove comment if present
                if "#" in line:
                    comment_start = line.index("#")
                    # Only strip comment if it's outside quotes
                    # Simple heuristic: find last quote before #
                    line = line[:comment_start]
                # Extract the quoted string
                # Find matching end quote
                quote_char = line[0]
                end_quote = line.find(quote_char, 1)
                if end_quote > 0:
                    dep = line[1:end_quote]
                else:
                    dep = line.strip('",\' ').strip()
                if dep:
                    # Extract just the package name (remove version specifiers and extras)
                    pkg_name = _extract_pkg_name(dep)
                    if pkg_name:
                        deps.append(pkg_name)

    # Fallback: simple keyword search for common packages
    if not deps:
        common_deps = ["fastapi", "django", "flask", "uvicorn", "starlette", "pydantic"]
        for dep in common_deps:
            if dep in content.lower():
                deps.append(dep)

    return deps


def _extract_pkg_name(dep_spec: str) -> str:
    """
    Extract package name from a dependency specification.
    
    Handles:
    - fastapi>=0.100.0 -> fastapi
    - uvicorn[standard] -> uvicorn
    - pydantic<2.0 -> pydantic
    - requests~=2.28.0 -> requests
    """
    # Remove any quotes
    dep_spec = dep_spec.strip('"\'')

    # Find the package name (before any specifier)
    # Order matters: check for extras bracket first
    if "[" in dep_spec:
        dep_spec = dep_spec[:dep_spec.index("[")]

    # Then check for version specifiers
    for specifier in [">=", "<=", "==", "~=", "!=", "<", ">"]:
        if specifier in dep_spec:
            dep_spec = dep_spec[:dep_spec.index(specifier)]
            break

    return dep_spec.strip()


def _extract_inline_deps(line: str) -> List[str]:
    """Extract dependencies from an inline format like: dependencies = ["pkg1", "pkg2"]"""
    deps = []
    # Find the list portion
    if "[" not in line:
        return deps

    start = line.index("[")
    end = line.rfind("]")
    if end == -1:
        end = len(line)

    list_content = line[start+1:end]

    # Split by comma and extract package names
    for item in list_content.split(","):
        item = item.strip().strip('"\'')
        if item:
            # Extract just the package name using the helper
            pkg_name = _extract_pkg_name(item)
            if pkg_name:
                deps.append(pkg_name)

    return deps


def _extract_python_deps_from_requirements(content: str) -> List[str]:
    """Extract dependency names from requirements.txt content."""
    deps = []
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("-"):
            # Extract package name using the helper
            pkg_name = _extract_pkg_name(line)
            if pkg_name:
                deps.append(pkg_name.lower())
    return deps


def _check_framework_content(repo_path: Path, arch_config: FrameworkArchetypeConfig) -> Tuple[float, List[str]]:
    """Check for framework-specific content in files."""
    score = 0.0
    evidence = []

    # Check for FastAPI app
    if arch_config.framework == "fastapi":
        for main_file in ["main.py", "app/main.py", "src/main.py"]:
            file_path = repo_path / main_file
            if file_path.exists():
                try:
                    content = file_path.read_text()
                    if "FastAPI(" in content or "from fastapi import" in content:
                        score += 0.2
                        evidence.append(f"FastAPI import in {main_file}")
                except IOError:
                    pass

    # Check for Django
    elif arch_config.framework == "django":
        manage_py = repo_path / "manage.py"
        if manage_py.exists():
            try:
                content = manage_py.read_text()
                if "django" in content.lower():
                    score += 0.2
                    evidence.append("Django in manage.py")
            except IOError:
                pass

    # Check for Flask
    elif arch_config.framework == "flask":
        for app_file in ["app.py", "app/__init__.py", "wsgi.py"]:
            file_path = repo_path / app_file
            if file_path.exists():
                try:
                    content = file_path.read_text()
                    if "Flask(" in content or "from flask import" in content:
                        score += 0.2
                        evidence.append(f"Flask import in {app_file}")
                except IOError:
                    pass

    # Check for Express
    elif arch_config.framework == "express":
        for app_file in ["app.ts", "app.js", "server.ts", "server.js", "src/app.ts", "src/index.ts", "src/server.ts"]:
            file_path = repo_path / app_file
            if file_path.exists():
                try:
                    content = file_path.read_text()
                    if "express(" in content.lower() or "from 'express'" in content or 'from "express"' in content or "require('express')" in content or 'require("express")' in content:
                        score += 0.3
                        evidence.append(f"Express import in {app_file}")
                except IOError:
                    pass

    return score, evidence


def _detect_primary_language(repo_path: Path) -> str:
    """Detect primary language when no framework is identified."""
    py_count = 0
    ts_count = 0
    js_count = 0

    for root, dirs, files in os.walk(repo_path):
        # Skip common non-source directories
        dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', '.venv', 'venv', 'node_modules'}]

        for f in files:
            if f.endswith(".py"):
                py_count += 1
            elif f.endswith(".ts") or f.endswith(".tsx"):
                ts_count += 1
            elif f.endswith(".js") or f.endswith(".jsx"):
                js_count += 1

    if ts_count > py_count and ts_count > js_count:
        return "typescript"
    elif js_count > py_count:
        return "javascript"
    return "python"


# =============================================================================
# LAYER 2: INFERENCE / EFFECTIVE PROFILE
# =============================================================================

def build_effective_repo_profile(
    detected: DetectedProfile,
    repo_root: Optional[str],
    use_llm_refinement: bool = False,
) -> RepoProfile:
    """
    Layer 2: Build effective RepoProfile from detection result.
    
    Decision logic:
    1. High confidence (>=0.8) + known archetype → use archetype defaults
    2. Medium confidence (0.5-0.8) → archetype defaults + heuristic refinement
    3. Low confidence (<0.5) or unknown → full heuristic inference
    4. Very uncertain + use_llm_refinement → call LLM for assistance
    
    Guardrails:
    - Logs warnings for low confidence detections
    - Sets profile_source to "heuristic_fallback" for unreliable detections
    - Uses generic project names when archetype is unknown
    
    Args:
        detected: Detection result from detect_repo_profile()
        repo_root: Path to repository root
        use_llm_refinement: Whether to use LLM for uncertain cases
        
    Returns:
        RepoProfile with effective layout configuration
    """
    logger.info(
        f"Building effective profile: archetype={detected.archetype_name}, "
        f"confidence={detected.confidence:.2f}, evidence={detected.evidence[:3]}"
    )

    # Guardrail: Warn about low confidence detection
    if detected.confidence < LOW_CONFIDENCE_THRESHOLD:
        logger.warning(
            f"Low confidence detection ({detected.confidence:.2f} < {LOW_CONFIDENCE_THRESHOLD}): "
            f"archetype={detected.archetype_name}, evidence={detected.evidence}. "
            f"Layout inference may be unreliable."
        )

    # Case 1: High confidence with known archetype
    if detected.should_use_archetype_defaults():
        logger.info(f"Using archetype defaults for {detected.archetype_name}")
        return _build_profile_from_archetype(detected)

    # Case 2: Known archetype but lower confidence - use archetype with refinement
    if detected.is_known_archetype and detected.confidence >= 0.5:
        logger.info(f"Using archetype with heuristic refinement for {detected.archetype_name}")
        base_profile = _build_profile_from_archetype(detected)
        return _refine_profile_with_heuristics(base_profile, repo_root, detected)

    # Case 3: Low confidence or unknown - full heuristic inference
    logger.info("Using full heuristic inference (low confidence or unknown archetype)")

    # Guardrail: Determine profile source based on confidence
    if detected.confidence < LOW_CONFIDENCE_THRESHOLD:
        profile_source = "heuristic_fallback"
        logger.warning(
            f"Using heuristic fallback for {detected.archetype_name} "
            f"(confidence {detected.confidence:.2f}). Consider providing explicit repo_profile."
        )
    else:
        profile_source = "heuristic"

    # Try heuristic inference
    inferred_profile = _infer_profile_heuristically(repo_root, detected, profile_source)

    # Case 4: Very uncertain and LLM refinement enabled
    if use_llm_refinement and detected.confidence < VERY_LOW_CONFIDENCE_THRESHOLD:
        logger.info("Confidence very low, attempting LLM-assisted refinement")
        refined_profile = _refine_profile_with_llm(inferred_profile, repo_root, detected)
        
        # ADR-0002: Persist LLM-refined profile as config file for future runs
        if refined_profile.profile_source == "llm" and repo_root:
            _persist_profile_as_config(refined_profile, Path(repo_root))
        
        return refined_profile

    return inferred_profile


def _build_profile_from_archetype(detected: DetectedProfile) -> RepoProfile:
    """
    Build RepoProfile from archetype defaults.
    
    .. deprecated:: 2.1
        Archetype-based detection is deprecated per ADR-0002.
        Prefer using .integration-coworker.yaml config files.
        Archetypes will be removed in v3.0.
    """
    import warnings
    warnings.warn(
        "Archetype-based detection is deprecated per ADR-0002. "
        "Consider creating a .integration-coworker.yaml config file. "
        "Archetypes will be removed in v3.0.",
        DeprecationWarning,
        stacklevel=2,
    )
    
    arch_config = KNOWN_ARCHETYPES.get(detected.archetype_name)

    if not arch_config:
        # Fallback to generic
        return RepoProfile(
            name=f"{detected.language}_project",
            archetype=detected.archetype_name,
            framework=None,
            language=detected.language,
            integrations_root="integrations",
            tests_root="tests",
            detection_confidence=detected.confidence,
            detection_evidence=detected.evidence,
            profile_source="archetype",
        )

    # Build hooks dict if configured
    integration_hooks = {}
    if arch_config.router_file:
        integration_hooks["router_file"] = arch_config.router_file
        integration_hooks["router_marker"] = arch_config.router_marker or "# <AUTO_INTEGRATION>"
    if arch_config.settings_file:
        integration_hooks["settings_file"] = arch_config.settings_file
        integration_hooks["settings_marker"] = arch_config.settings_marker or "# <AUTO_SETTINGS>"
    if arch_config.module_file:
        integration_hooks["module_file"] = arch_config.module_file

    return RepoProfile(
        name=arch_config.name,
        archetype=arch_config.name,
        framework=arch_config.framework,
        language=arch_config.language,
        integrations_root=arch_config.default_integrations_root,
        tests_root=arch_config.default_tests_root,
        conventions={
            "client_module_pattern": arch_config.client_module_pattern,
            "flow_module_pattern": arch_config.flow_module_pattern,
            "test_module_pattern": arch_config.test_module_pattern,
        },
        layout_hints={
            "clients_dir": f"{arch_config.default_integrations_root}/{arch_config.clients_dir_pattern}",
            "flows_dir": f"{arch_config.default_integrations_root}/{arch_config.flows_dir_pattern}",
            "tests_dir": arch_config.default_tests_root,
        },
        integration_hooks=integration_hooks if integration_hooks else None,
        detection_confidence=detected.confidence,
        detection_evidence=detected.evidence,
        profile_source="archetype",
    )


def _refine_profile_with_heuristics(
    base_profile: RepoProfile,
    repo_root: Optional[str],
    detected: DetectedProfile,
) -> RepoProfile:
    """Refine archetype profile with heuristic path detection."""
    if not repo_root:
        return base_profile

    repo_path = Path(repo_root)

    # Try to find actual source roots
    source_root = _find_source_root(repo_path)
    tests_root = _find_tests_root(repo_path)

    # Update paths if we found better ones
    refined_integrations_root = base_profile.integrations_root
    refined_tests_root = base_profile.tests_root

    if source_root:
        # Check if integrations already exists under source root
        existing_integrations = _find_existing_integrations_dir(repo_path, source_root)
        if existing_integrations:
            refined_integrations_root = existing_integrations
        else:
            refined_integrations_root = f"{source_root}/integrations"

    if tests_root:
        refined_tests_root = f"{tests_root}/integrations"

    # Build refined layout hints
    layout_hints = base_profile.layout_hints.copy() if base_profile.layout_hints else {}
    layout_hints["detected_source_root"] = source_root
    layout_hints["detected_tests_root"] = tests_root

    return RepoProfile(
        name=base_profile.name,
        archetype=base_profile.archetype,
        framework=base_profile.framework,
        language=base_profile.language,
        integrations_root=refined_integrations_root,
        tests_root=refined_tests_root,
        conventions=base_profile.conventions,
        layout_hints=layout_hints,
        integration_hooks=base_profile.integration_hooks,
        detection_confidence=detected.confidence,
        detection_evidence=detected.evidence + ["Refined with heuristics"],
        profile_source="archetype+heuristic",
    )


def _infer_profile_heuristically(
    repo_root: Optional[str],
    detected: DetectedProfile,
    profile_source: str = "heuristic",
) -> RepoProfile:
    """Infer profile entirely from heuristics (no archetype).
    
    Args:
        repo_root: Path to repository root
        detected: Detection result with language/evidence
        profile_source: Source tag for profile (heuristic, heuristic_fallback)
    """
    # Use language-specific generic project name
    if detected.archetype_name is None or detected.archetype_name == "unknown":
        if detected.language in ("typescript", "javascript"):
            project_name = "generic_js_project"
        else:
            project_name = "generic_python_project"
    else:
        project_name = f"inferred_{detected.language}_project"

    if not repo_root:
        return RepoProfile(
            name=project_name,
            language=detected.language,
            integrations_root="integrations",
            tests_root="tests",
            detection_confidence=detected.confidence,
            detection_evidence=detected.evidence,
            profile_source=profile_source,
        )

    repo_path = Path(repo_root)

    # Find source root
    source_root = _find_source_root(repo_path) or ""

    # Find tests root
    tests_root = _find_tests_root(repo_path) or "tests"

    # Infer where services/routes might live
    services_dir = _find_services_directory(repo_path, source_root)

    # Determine integrations placement
    if services_dir:
        integrations_root = f"{services_dir}/integrations"
    elif source_root:
        integrations_root = f"{source_root}/integrations"
    else:
        integrations_root = "integrations"

    # Build conventions based on language
    if detected.language in ("typescript", "javascript"):
        conventions = {
            "client_module_pattern": "clients/{provider}.ts",
            "flow_module_pattern": "services/{provider}/{task}.ts",
            "test_module_pattern": "{provider}/{task}.test.ts",
        }
    else:
        conventions = {
            "client_module_pattern": "clients/{provider}.py",
            "flow_module_pattern": "flows/{provider}_{task}.py",
            "test_module_pattern": "test_{provider}_{task}.py",
        }

    return RepoProfile(
        name=project_name,
        archetype=None,
        framework=None,
        language=detected.language,
        integrations_root=integrations_root,
        tests_root=f"{tests_root}/integrations",
        conventions=conventions,
        layout_hints={
            "detected_source_root": source_root,
            "detected_tests_root": tests_root,
            "detected_services_dir": services_dir,
            "clients_dir": f"{integrations_root}/clients",
            "flows_dir": f"{integrations_root}/flows",
        },
        detection_confidence=detected.confidence,
        detection_evidence=detected.evidence + ["Inferred via heuristics"],
        profile_source=profile_source,
    )


def _refine_profile_with_llm(
    base_profile: RepoProfile,
    repo_root: Optional[str],
    detected: DetectedProfile,
) -> RepoProfile:
    """Use LLM to refine profile when heuristics are uncertain."""
    if not repo_root:
        return base_profile

    try:
        from integration_coworker.llm import get_llm_client_for_node
        from integration_coworker.repo.context import filesystem_repo_context_provider

        # Get repo context
        repo_snapshot = filesystem_repo_context_provider(repo_root)

        # Build prompt with directory tree
        prompt = f"""Analyze this repository structure and suggest where to place API integration code.

## Repository Structure
{repo_snapshot.tree_markdown[:3000]}

## Current Detection
- Detected archetype: {detected.archetype_name}
- Language: {detected.language}
- Confidence: {detected.confidence:.2f}
- Evidence: {', '.join(detected.evidence[:5])}

## Task
Suggest the best locations for:
1. Integration client code (API clients)
2. Integration flow/service code (business logic)
3. Integration tests

Respond in this exact format:
integrations_root=<path>
clients_dir=<path>
flows_dir=<path>
tests_root=<path>
reasoning=<one line explanation>
"""

        # Get LLM client (uses archetype config)
        client = get_llm_client_for_node("plan_run")
        response = client.complete(prompt)

        # Parse response
        if response and not response.startswith("Mock"):
            parsed = _parse_llm_layout_response(response)
            if parsed:
                return RepoProfile(
                    name=f"llm_refined_{detected.language}_project",
                    archetype=detected.archetype_name if detected.is_known_archetype else None,
                    framework=detected.archetype_name if detected.is_known_archetype else None,
                    language=detected.language,
                    integrations_root=parsed.get("integrations_root", base_profile.integrations_root),
                    tests_root=parsed.get("tests_root", base_profile.tests_root),
                    conventions=base_profile.conventions,
                    layout_hints={
                        "clients_dir": parsed.get("clients_dir", f"{parsed.get('integrations_root', 'integrations')}/clients"),
                        "flows_dir": parsed.get("flows_dir", f"{parsed.get('integrations_root', 'integrations')}/flows"),
                        "llm_reasoning": parsed.get("reasoning", ""),
                    },
                    detection_confidence=detected.confidence,
                    detection_evidence=detected.evidence + ["Refined with LLM"],
                    profile_source="llm",
                )

    except Exception as e:
        logger.warning(f"LLM refinement failed: {e}")

    return base_profile


def _parse_llm_layout_response(response: str) -> Optional[Dict[str, str]]:
    """Parse LLM layout suggestion response."""
    result = {}
    for line in response.strip().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()

    return result if result else None


def _persist_profile_as_config(profile: RepoProfile, repo_root: Path) -> bool:
    """
    Persist a RepoProfile as .integration-coworker.yaml config file.
    
    Per ADR-0002: After LLM inference, save the config so future runs
    can skip LLM inference and use the cached config.
    
    Args:
        profile: The RepoProfile to persist
        repo_root: Path to the repository root
        
    Returns:
        True if saved successfully, False otherwise
    """
    try:
        from integration_coworker.repo.config_schema import (
            profile_to_config,
            save_config,
            validate_config,
        )
        
        # Convert profile to config
        config = profile_to_config(profile, detection_method=profile.profile_source)
        
        # Validate before saving
        validation = validate_config(config, repo_root, check_paths_exist=True)
        if not validation.is_valid:
            logger.warning(
                f"Config validation failed, not persisting: {validation.errors}"
            )
            return False
        
        if validation.warnings:
            logger.debug(f"Config validation warnings: {validation.warnings}")
        
        # Save to .integration-coworker.yaml
        config_path = repo_root / ".integration-coworker.yaml"
        success = save_config(config, config_path)
        
        if success:
            logger.info(
                f"Persisted LLM-refined profile as config: {config_path}"
            )
        
        return success
        
    except ImportError as e:
        logger.warning(f"Cannot persist profile (missing dependency): {e}")
        return False
    except Exception as e:
        logger.warning(f"Failed to persist profile as config: {e}")
        return False


# =============================================================================
# HEURISTIC HELPERS
# =============================================================================

def _find_source_root(repo_path: Path) -> Optional[str]:
    """Find the primary source root directory."""
    # Common source directory patterns (in priority order)
    source_patterns = [
        "src",
        "app",
        "lib",
        "source",
        "pkg",
        "packages",  # Monorepo
    ]

    for pattern in source_patterns:
        candidate = repo_path / pattern
        if candidate.is_dir():
            return pattern

    # Check for Python package structure (directory with __init__.py)
    for child in repo_path.iterdir():
        if child.is_dir() and (child / "__init__.py").exists():
            if child.name not in {"tests", "test", "docs", "scripts"}:
                return child.name

    return None


def _find_tests_root(repo_path: Path) -> Optional[str]:
    """Find the tests directory."""
    test_patterns = [
        "tests",
        "test",
        "__tests__",
        "spec",
        "specs",
    ]

    for pattern in test_patterns:
        candidate = repo_path / pattern
        if candidate.is_dir():
            return pattern

    return "tests"  # Default


def _find_services_directory(repo_path: Path, source_root: str) -> Optional[str]:
    """Find existing services/routes directory."""
    if not source_root:
        return None

    service_patterns = [
        "services",
        "service",
        "routes",
        "api",
        "handlers",
        "controllers",
    ]

    base = repo_path / source_root
    if not base.exists():
        return None

    for pattern in service_patterns:
        candidate = base / pattern
        if candidate.is_dir():
            return f"{source_root}/{pattern}"

    return source_root


def _find_existing_integrations_dir(repo_path: Path, source_root: str) -> Optional[str]:
    """Check if an integrations directory already exists."""
    integration_patterns = [
        "integrations",
        "integration",
        "external",
        "vendors",
        "third_party",
    ]

    # Check under source root
    base = repo_path / source_root
    if base.exists():
        for pattern in integration_patterns:
            candidate = base / pattern
            if candidate.is_dir():
                return f"{source_root}/{pattern}"

    # Check at repo root
    for pattern in integration_patterns:
        candidate = repo_path / pattern
        if candidate.is_dir():
            return pattern

    return None


# =============================================================================
# PUBLIC API
# =============================================================================

def get_repo_profile(repo_root: Optional[str], use_llm: bool = False) -> RepoProfile:
    """
    Main entry point: Detect and build effective RepoProfile.
    
    Combines both layers into a single call:
    1. detect_repo_profile() - Detection layer
    2. build_effective_repo_profile() - Inference layer
    
    Args:
        repo_root: Path to repository root
        use_llm: Whether to use LLM for uncertain cases
        
    Returns:
        Effective RepoProfile for the repository
    """
    detected = detect_repo_profile(repo_root)
    return build_effective_repo_profile(detected, repo_root, use_llm_refinement=use_llm)
