"""
Tests for repository profiles.

Verifies that concrete RepoProfile instances like SUBATOMIC_MOCK_PROFILE
have the expected structure and fields.
"""
import pytest
from integration_coworker.repo.profiles import (
    SUBATOMIC_MOCK_PROFILE,
    FASTAPI_PROFILE,
    detect_profile_from_repo,
)


def test_subatomic_mock_profile_exists():
    """Test that SUBATOMIC_MOCK_PROFILE is defined."""
    assert SUBATOMIC_MOCK_PROFILE is not None
    assert SUBATOMIC_MOCK_PROFILE.name == "subatomic_mock_service"


def test_subatomic_profile_has_archetype():
    """Test that SUBATOMIC_MOCK_PROFILE has fastapi_service archetype."""
    assert SUBATOMIC_MOCK_PROFILE.archetype == "fastapi_service"
    assert SUBATOMIC_MOCK_PROFILE.framework == "fastapi"


def test_subatomic_profile_has_layout_hints():
    """Test that SUBATOMIC_MOCK_PROFILE has all required layout_hints."""
    hints = SUBATOMIC_MOCK_PROFILE.layout_hints
    
    assert hints is not None
    assert "clients_dir" in hints
    assert "workflows_dir" in hints
    assert "tests_dir" in hints
    
    # Verify expected paths
    assert hints["clients_dir"] == "src/integrations/clients"
    assert hints["workflows_dir"] == "src/integrations/flows"
    assert hints["tests_dir"] == "tests/integrations"


def test_subatomic_profile_has_integration_hooks():
    """Test that SUBATOMIC_MOCK_PROFILE has integration_hooks for router/settings."""
    hooks = SUBATOMIC_MOCK_PROFILE.integration_hooks
    
    assert hooks is not None
    assert "router_file" in hooks
    assert "router_registration_marker" in hooks
    assert "settings_file" in hooks
    assert "settings_marker" in hooks
    
    # Verify expected values
    assert hooks["router_file"] == "src/app/router.py"
    assert hooks["router_registration_marker"] == "# <AUTO_INTEGRATION_MARKER>"
    assert hooks["settings_file"] == "src/app/settings.py"
    assert hooks["settings_marker"] == "# <AUTO_INTEGRATION_SETTINGS_MARKER>"


def test_fastapi_profile_exists():
    """Test that FASTAPI_PROFILE is also defined."""
    assert FASTAPI_PROFILE is not None
    assert FASTAPI_PROFILE.framework == "fastapi"


def test_detect_profile_returns_valid_profile():
    """Test that detect_profile_from_repo returns a valid profile."""
    # For now it returns SUBATOMIC_MOCK_PROFILE
    profile = detect_profile_from_repo(None)
    
    assert profile is not None
    assert hasattr(profile, "archetype")
    assert hasattr(profile, "language")
