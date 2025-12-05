"""
Predefined repository profiles for common frameworks.

Profiles define where to place generated code in different project types.

DEPRECATION NOTICE (ADR-0002):
===============================
Archetype-based profiles are deprecated as of ADR-0002.
The preferred approach is config-first with LLM fallback:

1. Check for .integration-coworker.yaml config file
2. If not found, use LLM to infer and cache config
3. Fall back to these profiles only as last resort

To migrate:
- Create a .integration-coworker.yaml in your repo root
- Or let the system auto-generate one via LLM inference

See: docs/decisions/adr-0002-repo-aware-integration-config-first.md
"""
import logging
import warnings
from pathlib import Path
from typing import Optional

from integration_coworker.repo.models import RepoProfile

logger = logging.getLogger(__name__)

# Flag to suppress deprecation warnings during tests
_SUPPRESS_DEPRECATION_WARNINGS = False


def _mark_deprecated(profile: RepoProfile, reason: str = "archetype") -> RepoProfile:
    """Mark a profile as deprecated for tracking."""
    profile.profile_source = f"{reason}_deprecated"
    return profile


# =============================================================================
# Generic Fallback Profiles (V2 - replaces SUBATOMIC_MOCK_PROFILE fallbacks)
# =============================================================================

GENERIC_PYTHON_PROFILE = RepoProfile(
    name="generic-python",
    framework="generic",
    language="python",
    integrations_root="src/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "flows/{provider}_{task}.py",
        "test_module_pattern": "test_{provider}_{task}.py",
    },
)

GENERIC_TYPESCRIPT_PROFILE = RepoProfile(
    name="generic-typescript",
    framework="generic",
    language="typescript",
    integrations_root="src/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.ts",
        "flow_module_pattern": "flows/{provider}/{task}.ts",
        "test_module_pattern": "{provider}/{task}.test.ts",
    },
)


# =============================================================================
# Framework-Specific Profiles (DEPRECATED per ADR-0002)
# =============================================================================

# Mock profile for testing (per Appendix D spec) - legacy, prefer GENERIC_PYTHON_PROFILE
SUBATOMIC_MOCK_PROFILE = RepoProfile(
    name="subatomic_mock_service",
    archetype="fastapi_service",  # P2.1: High-level classification
    framework="fastapi",
    language="python",
    integrations_root="src/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "flows/{provider}_{task}.py",
        "test_module_pattern": "test_{provider}_{task}.py",
    },
    layout_hints={
        "clients_dir": "src/integrations/clients",
        "workflows_dir": "src/integrations/flows",
        "tests_dir": "tests/integrations",
    },
    integration_hooks={
        "router_file": "src/app/router.py",
        "router_registration_marker": "# <AUTO_INTEGRATION_MARKER>",
        "settings_file": "src/app/settings.py",
        "settings_marker": "# <AUTO_INTEGRATION_SETTINGS_MARKER>",
    },
)


# Next.js App Router profile
NEXTJS_APP_ROUTER_PROFILE = RepoProfile(
    name="next-js-app-router",
    framework="nextjs",
    language="typescript",
    integrations_root="lib/integrations",
    tests_root="__tests__/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.ts",
        "flow_module_pattern": "flows/{provider}/{task}.ts",
        "test_module_pattern": "{provider}/{task}.test.ts",
    }
)


# Django REST Framework profile
DJANGO_REST_PROFILE = RepoProfile(
    name="django-rest",
    framework="django",
    language="python",
    integrations_root="integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "services/{provider}_{task}.py",
        "test_module_pattern": "test_{provider}_{task}.py",
    }
)


# FastAPI profile
FASTAPI_PROFILE = RepoProfile(
    name="fastapi",
    framework="fastapi",
    language="python",
    integrations_root="app/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "services/{provider}_{task}.py",
        "test_module_pattern": "test_{provider}_{task}.py",
    }
)


# Flask profile (M5 WS3-T3)
FLASK_PROFILE = RepoProfile(
    name="flask",
    framework="flask",
    language="python",
    integrations_root="app/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "services/{provider}_{task}.py",
        "test_module_pattern": "test_{provider}_{task}.py",
    },
    layout_hints={
        "clients_dir": "app/integrations/clients",
        "services_dir": "app/integrations/services",
        "tests_dir": "tests/integrations",
    },
)


# Express.js profile (M5 WS3-T3)
EXPRESS_PROFILE = RepoProfile(
    name="express",
    framework="express",
    language="typescript",
    integrations_root="src/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.ts",
        "flow_module_pattern": "services/{provider}/{task}.ts",
        "test_module_pattern": "{provider}/{task}.test.ts",
    },
    layout_hints={
        "clients_dir": "src/integrations/clients",
        "services_dir": "src/integrations/services",
        "tests_dir": "tests/integrations",
    },
)


# NestJS profile (M5 WS3-T3)
NESTJS_PROFILE = RepoProfile(
    name="nestjs",
    framework="nestjs",
    language="typescript",
    integrations_root="src/integrations",
    tests_root="test/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.client.ts",
        "flow_module_pattern": "services/{provider}-{task}.service.ts",
        "test_module_pattern": "{provider}-{task}.service.spec.ts",
    },
    layout_hints={
        "clients_dir": "src/integrations/clients",
        "services_dir": "src/integrations/services",
        "tests_dir": "test/integrations",
        "module_file": "src/integrations/integrations.module.ts",
    },
)


# Registry of all profiles
REPO_PROFILES = {
    "subatomic-mock": SUBATOMIC_MOCK_PROFILE,
    "next-js-app-router": NEXTJS_APP_ROUTER_PROFILE,
    "django-rest": DJANGO_REST_PROFILE,
    "fastapi": FASTAPI_PROFILE,
    "flask": FLASK_PROFILE,
    "express": EXPRESS_PROFILE,
    "nestjs": NESTJS_PROFILE,
    "generic-python": GENERIC_PYTHON_PROFILE,
    "generic-typescript": GENERIC_TYPESCRIPT_PROFILE,
}


def get_profile_by_name(name: str) -> RepoProfile:
    """
    Get a repo profile by name.
    
    Args:
        name: Profile name (e.g., "next-js-app-router")
    
    Returns:
        RepoProfile instance
    
    Raises:
        KeyError: If profile not found
    """
    return REPO_PROFILES[name]


def _load_profile_from_config(config_path: Path) -> Optional[RepoProfile]:
    """
    Load profile from .integration-coworker.yaml config file.
    
    Per ADR-0002 Phase 2: Explicit config files take precedence.
    Uses the IntegrationCoworkerConfig schema for structured parsing.
    
    Args:
        config_path: Path to the config file
    
    Returns:
        RepoProfile if successfully loaded, None if parsing fails
    """
    from integration_coworker.repo.config_schema import (
        load_config,
        config_to_profile,
    )
    
    config = load_config(config_path)
    if config is None:
        return None
    
    profile = config_to_profile(config)
    profile.profile_source = "config_file"
    return profile


def detect_profile_from_repo(repo_root) -> RepoProfile:
    """
    Detect the appropriate profile from a repository's structure.
    
    DEPRECATED (ADR-0002): This function uses archetype detection which is deprecated.
    Use get_repo_profile_config_first() from repo/llm_inference.py instead, which:
    1. Checks for .integration-coworker.yaml config file first
    2. Uses LLM inference as fallback
    3. Falls back to this function only as last resort
    
    Detection hierarchy:
    1. Explicit config file (.integration-coworker.yaml)
    2. Framework-specific markers (package.json deps, pyproject.toml, etc.)
    3. Language detection (fallback to generic profiles)
    4. Generic Python profile (ultimate fallback)
    
    Args:
        repo_root: Path to repository root (can be None)
    
    Returns:
        Detected RepoProfile
    """
    import json
    
    if repo_root is None:
        return GENERIC_PYTHON_PROFILE

    repo_path = Path(repo_root)

    if not repo_path.exists():
        return GENERIC_PYTHON_PROFILE

    # -------------------------------------------------------------------------
    # Priority 1: Explicit config file (ADR-0002 Phase 1)
    # -------------------------------------------------------------------------
    config_file = repo_path / ".integration-coworker.yaml"
    if config_file.exists():
        profile = _load_profile_from_config(config_file)
        if profile:
            logger.info(f"Loaded profile from config file: {config_file}")
            return profile
    
    # Also check for .yml extension
    config_file_yml = repo_path / ".integration-coworker.yml"
    if config_file_yml.exists():
        profile = _load_profile_from_config(config_file_yml)
        if profile:
            logger.info(f"Loaded profile from config file: {config_file_yml}")
            return profile

    # -------------------------------------------------------------------------
    # Emit deprecation warning for archetype-based detection
    # -------------------------------------------------------------------------
    if not _SUPPRESS_DEPRECATION_WARNINGS:
        warnings.warn(
            "Archetype-based profile detection is deprecated per ADR-0002. "
            "Create a .integration-coworker.yaml config file for better accuracy. "
            "See: docs/decisions/adr-0002-repo-aware-integration-config-first.md",
            DeprecationWarning,
            stacklevel=2,
        )

    # -------------------------------------------------------------------------
    # Priority 2: Next.js detection: package.json + next.config.* file
    # -------------------------------------------------------------------------
    package_json = repo_path / "package.json"
    next_config_patterns = [
        repo_path / "next.config.js",
        repo_path / "next.config.mjs",
        repo_path / "next.config.ts",
    ]

    if package_json.exists() and any(p.exists() for p in next_config_patterns):
        profile = NEXTJS_APP_ROUTER_PROFILE
        profile.profile_source = "archetype_deprecated"
        return profile

    # Check package.json for Node.js frameworks
    if package_json.exists():
        try:
            pkg_data = json.loads(package_json.read_text())
            deps = pkg_data.get("dependencies", {})
            dev_deps = pkg_data.get("devDependencies", {})
            all_deps = {**deps, **dev_deps}

            # Next.js
            if "next" in all_deps:
                profile = NEXTJS_APP_ROUTER_PROFILE
                profile.profile_source = "archetype_deprecated"
                return profile

            # NestJS (M5 WS3-T3)
            if "@nestjs/core" in all_deps or "@nestjs/common" in all_deps:
                profile = NESTJS_PROFILE
                profile.profile_source = "archetype_deprecated"
                return profile

            # Express.js (M5 WS3-T3)
            if "express" in all_deps:
                profile = EXPRESS_PROFILE
                profile.profile_source = "archetype_deprecated"
                return profile

            # If package.json exists but no framework detected, use TypeScript generic
            profile = GENERIC_TYPESCRIPT_PROFILE
            profile.profile_source = "generic_fallback"
            return profile

        except (json.JSONDecodeError, IOError):
            pass

    # -------------------------------------------------------------------------
    # Priority 3: Django detection: manage.py + settings.py or settings/ directory
    # -------------------------------------------------------------------------
    manage_py = repo_path / "manage.py"
    settings_py = repo_path / "settings.py"
    settings_dir = repo_path / "settings"

    # Also check for <project_name>/settings.py pattern
    has_nested_settings = False
    for child in repo_path.iterdir():
        if child.is_dir() and (child / "settings.py").exists():
            has_nested_settings = True
            break

    if manage_py.exists() and (settings_py.exists() or settings_dir.exists() or has_nested_settings):
        profile = DJANGO_REST_PROFILE
        profile.profile_source = "archetype_deprecated"
        return profile

    # -------------------------------------------------------------------------
    # Priority 4: FastAPI/Flask detection via pyproject.toml or requirements.txt
    # -------------------------------------------------------------------------
    pyproject_toml = repo_path / "pyproject.toml"
    requirements_txt = repo_path / "requirements.txt"

    # Check pyproject.toml
    if pyproject_toml.exists():
        try:
            content = pyproject_toml.read_text().lower()
            if "fastapi" in content:
                profile = FASTAPI_PROFILE
                profile.profile_source = "archetype_deprecated"
                return profile
            if "flask" in content:
                profile = FLASK_PROFILE
                profile.profile_source = "archetype_deprecated"
                return profile
            # Has pyproject.toml -> Python project
            profile = GENERIC_PYTHON_PROFILE
            profile.profile_source = "generic_fallback"
            return profile
        except IOError:
            pass

    # Check requirements.txt
    if requirements_txt.exists():
        try:
            content = requirements_txt.read_text().lower()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("fastapi") or line.startswith("fastapi["):
                    profile = FASTAPI_PROFILE
                    profile.profile_source = "archetype_deprecated"
                    return profile
                if line.startswith("flask") or line.startswith("flask["):
                    profile = FLASK_PROFILE
                    profile.profile_source = "archetype_deprecated"
                    return profile
            # Has requirements.txt -> Python project
            profile = GENERIC_PYTHON_PROFILE
            profile.profile_source = "generic_fallback"
            return profile
        except IOError:
            pass

    # -------------------------------------------------------------------------
    # Priority 5: Flask detection via app.py
    # -------------------------------------------------------------------------
    app_py = repo_path / "app.py"
    wsgi_py = repo_path / "wsgi.py"

    if app_py.exists() or wsgi_py.exists():
        check_file = app_py if app_py.exists() else wsgi_py
        try:
            content = check_file.read_text()
            if "from flask import" in content or "import flask" in content.lower():
                profile = FLASK_PROFILE
                profile.profile_source = "archetype_deprecated"
                return profile
        except IOError:
            pass

    # -------------------------------------------------------------------------
    # Priority 6: Language detection via common files
    # -------------------------------------------------------------------------
    setup_py = repo_path / "setup.py"
    
    if setup_py.exists():
        profile = GENERIC_PYTHON_PROFILE
        profile.profile_source = "generic_fallback"
        return profile
    
    # Check for any .py files at root level
    if any(repo_path.glob("*.py")):
        profile = GENERIC_PYTHON_PROFILE
        profile.profile_source = "generic_fallback"
        return profile
    
    # Check for any .ts files at root or src level
    if any(repo_path.glob("*.ts")) or (repo_path / "src").exists() and any((repo_path / "src").glob("*.ts")):
        profile = GENERIC_TYPESCRIPT_PROFILE
        profile.profile_source = "generic_fallback"
        return profile

    # -------------------------------------------------------------------------
    # Ultimate fallback: Generic Python profile
    # -------------------------------------------------------------------------
    profile = GENERIC_PYTHON_PROFILE
    profile.profile_source = "generic_fallback"
    return profile
