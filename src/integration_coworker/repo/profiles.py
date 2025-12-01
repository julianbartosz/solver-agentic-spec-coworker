"""
Predefined repository profiles for common frameworks.

Profiles define where to place generated code in different project types.
"""
from integration_coworker.repo.models import RepoProfile


# Mock profile for testing (per Appendix D spec)
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


def detect_profile_from_repo(repo_root) -> RepoProfile:
    """
    Detect the appropriate profile from a repository's structure.
    
    Uses marker file heuristics to identify framework:
    - Next.js: package.json + next.config.* (js/mjs/ts)
    - Django: manage.py + settings.py (or settings/ directory)
    - FastAPI: pyproject.toml or requirements.txt with "fastapi" dependency
    - Default: SUBATOMIC_MOCK_PROFILE
    
    Args:
        repo_root: Path to repository root (can be None)
    
    Returns:
        Detected RepoProfile, or SUBATOMIC_MOCK_PROFILE as default
    """
    from pathlib import Path

    if repo_root is None:
        return SUBATOMIC_MOCK_PROFILE

    repo_path = Path(repo_root)

    if not repo_path.exists():
        return SUBATOMIC_MOCK_PROFILE

    # -------------------------------------------------------------------------
    # Next.js detection: package.json + next.config.* file
    # -------------------------------------------------------------------------
    package_json = repo_path / "package.json"
    next_config_patterns = [
        repo_path / "next.config.js",
        repo_path / "next.config.mjs",
        repo_path / "next.config.ts",
    ]

    if package_json.exists() and any(p.exists() for p in next_config_patterns):
        return NEXTJS_APP_ROUTER_PROFILE

    # Check package.json for Node.js frameworks
    if package_json.exists():
        try:
            import json
            pkg_data = json.loads(package_json.read_text())
            deps = pkg_data.get("dependencies", {})
            dev_deps = pkg_data.get("devDependencies", {})
            all_deps = {**deps, **dev_deps}

            # Next.js
            if "next" in all_deps:
                return NEXTJS_APP_ROUTER_PROFILE

            # NestJS (M5 WS3-T3)
            if "@nestjs/core" in all_deps or "@nestjs/common" in all_deps:
                return NESTJS_PROFILE

            # Express.js (M5 WS3-T3)
            if "express" in all_deps:
                return EXPRESS_PROFILE

        except (json.JSONDecodeError, IOError):
            pass

    # -------------------------------------------------------------------------
    # Django detection: manage.py + settings.py or settings/ directory
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
        return DJANGO_REST_PROFILE

    # -------------------------------------------------------------------------
    # FastAPI detection: pyproject.toml or requirements.txt with "fastapi"
    # -------------------------------------------------------------------------
    pyproject_toml = repo_path / "pyproject.toml"
    requirements_txt = repo_path / "requirements.txt"

    # Check pyproject.toml
    if pyproject_toml.exists():
        try:
            content = pyproject_toml.read_text().lower()
            if "fastapi" in content:
                return FASTAPI_PROFILE
            # Flask detection (M5 WS3-T3)
            if "flask" in content:
                return FLASK_PROFILE
        except IOError:
            pass

    # Check requirements.txt
    if requirements_txt.exists():
        try:
            content = requirements_txt.read_text().lower()
            # Look for "fastapi" as a line or with version specifier
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("fastapi") or line.startswith("fastapi["):
                    return FASTAPI_PROFILE
                # Flask detection (M5 WS3-T3)
                if line.startswith("flask") or line.startswith("flask["):
                    return FLASK_PROFILE
        except IOError:
            pass

    # -------------------------------------------------------------------------
    # Flask detection via app.py (M5 WS3-T3)
    # -------------------------------------------------------------------------
    app_py = repo_path / "app.py"
    wsgi_py = repo_path / "wsgi.py"

    if app_py.exists() or wsgi_py.exists():
        # Check if it contains Flask imports
        check_file = app_py if app_py.exists() else wsgi_py
        try:
            content = check_file.read_text()
            if "from flask import" in content or "import flask" in content.lower():
                return FLASK_PROFILE
        except IOError:
            pass

    # -------------------------------------------------------------------------
    # Default fallback
    # -------------------------------------------------------------------------
    return SUBATOMIC_MOCK_PROFILE
