"""
Tests for repository profiles.

Verifies that concrete RepoProfile instances like SUBATOMIC_MOCK_PROFILE
have the expected structure and fields.
"""
import json
import pytest
from pathlib import Path
from integration_coworker.repo.profiles import (
    SUBATOMIC_MOCK_PROFILE,
    FASTAPI_PROFILE,
    NEXTJS_APP_ROUTER_PROFILE,
    DJANGO_REST_PROFILE,
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


# ---------------------------------------------------------------------------
# Profile Detection Heuristics Tests
# ---------------------------------------------------------------------------

class TestDetectProfileNextJS:
    """Tests for Next.js profile detection."""
    
    def test_detects_nextjs_with_config_js(self, tmp_path):
        """Detect Next.js via package.json + next.config.js."""
        (tmp_path / "package.json").write_text('{"dependencies": {}}')
        (tmp_path / "next.config.js").touch()
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile.framework == "nextjs"
        assert profile.language == "typescript"
    
    def test_detects_nextjs_with_config_mjs(self, tmp_path):
        """Detect Next.js via package.json + next.config.mjs."""
        (tmp_path / "package.json").write_text('{"dependencies": {}}')
        (tmp_path / "next.config.mjs").touch()
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile.framework == "nextjs"
    
    def test_detects_nextjs_with_config_ts(self, tmp_path):
        """Detect Next.js via package.json + next.config.ts."""
        (tmp_path / "package.json").write_text('{"dependencies": {}}')
        (tmp_path / "next.config.ts").touch()
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile.framework == "nextjs"
    
    def test_detects_nextjs_from_dependency(self, tmp_path):
        """Detect Next.js via 'next' in package.json dependencies."""
        pkg_content = json.dumps({
            "dependencies": {
                "next": "^14.0.0",
                "react": "^18.0.0"
            }
        })
        (tmp_path / "package.json").write_text(pkg_content)
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile.framework == "nextjs"
    
    def test_detects_nextjs_from_dev_dependency(self, tmp_path):
        """Detect Next.js via 'next' in devDependencies."""
        pkg_content = json.dumps({
            "dependencies": {},
            "devDependencies": {
                "next": "^14.0.0"
            }
        })
        (tmp_path / "package.json").write_text(pkg_content)
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile.framework == "nextjs"


class TestDetectProfileDjango:
    """Tests for Django profile detection."""
    
    def test_detects_django_with_manage_and_settings(self, tmp_path):
        """Detect Django via manage.py + settings.py."""
        (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n")
        (tmp_path / "settings.py").write_text("INSTALLED_APPS = []\n")
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile.framework == "django"
        assert profile.language == "python"
    
    def test_detects_django_with_settings_directory(self, tmp_path):
        """Detect Django via manage.py + settings/ directory."""
        (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n")
        (tmp_path / "settings").mkdir()
        (tmp_path / "settings" / "__init__.py").touch()
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile.framework == "django"
    
    def test_detects_django_with_nested_settings(self, tmp_path):
        """Detect Django via manage.py + myproject/settings.py pattern."""
        (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n")
        (tmp_path / "myproject").mkdir()
        (tmp_path / "myproject" / "settings.py").write_text("DEBUG = True\n")
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile.framework == "django"


class TestDetectProfileFastAPI:
    """Tests for FastAPI profile detection."""
    
    def test_detects_fastapi_from_pyproject(self, tmp_path):
        """Detect FastAPI via pyproject.toml dependencies."""
        pyproject_content = '''
[project]
dependencies = [
    "fastapi>=0.100.0",
    "uvicorn[standard]>=0.22.0",
]
'''
        (tmp_path / "pyproject.toml").write_text(pyproject_content)
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile.framework == "fastapi"
        assert profile.language == "python"
    
    def test_detects_fastapi_from_requirements(self, tmp_path):
        """Detect FastAPI via requirements.txt."""
        requirements_content = '''
fastapi>=0.100.0
uvicorn[standard]
pydantic>=2.0
'''
        (tmp_path / "requirements.txt").write_text(requirements_content)
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile.framework == "fastapi"
    
    def test_detects_fastapi_with_extras(self, tmp_path):
        """Detect FastAPI via requirements.txt with extras like fastapi[all]."""
        requirements_content = '''
fastapi[all]>=0.100.0
'''
        (tmp_path / "requirements.txt").write_text(requirements_content)
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile.framework == "fastapi"


class TestDetectProfileFallback:
    """Tests for fallback behavior when no framework is detected."""
    
    def test_empty_directory_returns_default(self, tmp_path):
        """Empty directory returns SUBATOMIC_MOCK_PROFILE."""
        profile = detect_profile_from_repo(tmp_path)
        assert profile == SUBATOMIC_MOCK_PROFILE
    
    def test_nonexistent_path_returns_default(self, tmp_path):
        """Non-existent path returns SUBATOMIC_MOCK_PROFILE."""
        nonexistent = tmp_path / "does_not_exist"
        profile = detect_profile_from_repo(nonexistent)
        assert profile == SUBATOMIC_MOCK_PROFILE
    
    def test_generic_python_project_returns_default(self, tmp_path):
        """Generic Python project without framework markers returns default."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "myproject"\n')
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")
        
        profile = detect_profile_from_repo(tmp_path)
        assert profile == SUBATOMIC_MOCK_PROFILE
    
    def test_node_project_without_next_returns_default(self, tmp_path):
        """Node project without Next.js returns default (for now)."""
        pkg_content = json.dumps({
            "dependencies": {
                "express": "^4.18.0"
            }
        })
        (tmp_path / "package.json").write_text(pkg_content)
        
        profile = detect_profile_from_repo(tmp_path)
        # Express is not explicitly handled, so falls back to default
        assert profile == SUBATOMIC_MOCK_PROFILE
