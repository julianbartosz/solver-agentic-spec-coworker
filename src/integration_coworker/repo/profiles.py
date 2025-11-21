"""
Predefined repository profiles for common frameworks.

Profiles define where to place generated code in different project types.
"""
from integration_coworker.repo.models import RepoProfile


# Mock profile for testing
SUBATOMIC_MOCK_PROFILE = RepoProfile(
    name="subatomic-mock",
    framework="mock",
    language="python",
    integrations_root="integrations",
    tests_root="tests",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "flows/{provider}_{task}.py",
        "test_module_pattern": "test_{provider}_{task}.py",
    }
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


# Registry of all profiles
REPO_PROFILES = {
    "subatomic-mock": SUBATOMIC_MOCK_PROFILE,
    "next-js-app-router": NEXTJS_APP_ROUTER_PROFILE,
    "django-rest": DJANGO_REST_PROFILE,
    "fastapi": FASTAPI_PROFILE,
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
    
    Args:
        repo_root: Path to repository root
    
    Returns:
        Detected RepoProfile, or SUBATOMIC_MOCK_PROFILE as default
    """
    # TODO: Implement actual detection logic
    # For now, return the mock profile
    return SUBATOMIC_MOCK_PROFILE
