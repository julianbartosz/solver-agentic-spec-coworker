"""
Tests for the two-layer RepoProfile detection pipeline.

Tests both Layer 1 (detect_repo_profile) and Layer 2 (build_effective_repo_profile).
"""
import pytest
from pathlib import Path
import tempfile
import json

from integration_coworker.repo.detection import (
    detect_repo_profile,
    build_effective_repo_profile,
)
from integration_coworker.repo.models import DetectedProfile, RepoProfile


# Mark all tests in this file as not needing DB
pytestmark = pytest.mark.no_db


class TestDetectRepoProfileFastAPI:
    """Tests for FastAPI detection."""
    
    def test_detects_fastapi_from_main_py_and_deps(self, tmp_path):
        """High confidence detection with main.py and dependencies."""
        # Setup FastAPI repo structure
        (tmp_path / "pyproject.toml").write_text(
            'dependencies = ["fastapi", "uvicorn"]'
        )
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()"
        )
        
        result = detect_repo_profile(tmp_path)
        
        assert result.archetype_name == "fastapi"
        assert result.language == "python"
        assert result.confidence >= 0.8  # High confidence
        assert result.is_high_confidence
        assert "fastapi" in str(result.evidence).lower()
    
    def test_detects_fastapi_from_requirements_txt(self, tmp_path):
        """Detection with requirements.txt dependencies."""
        (tmp_path / "requirements.txt").write_text("fastapi>=0.100.0\nuvicorn>=0.22.0\n")
        
        result = detect_repo_profile(tmp_path)
        
        assert result.archetype_name == "fastapi"
        assert "fastapi" in str(result.evidence).lower()
    
    def test_detects_fastapi_with_src_layout(self, tmp_path):
        """Detection with src/app layout."""
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi"]')
        (tmp_path / "src" / "app").mkdir(parents=True)
        (tmp_path / "src" / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()"
        )
        
        result = detect_repo_profile(tmp_path)
        
        # Should detect fastapi from deps, even if main.py is in non-standard location
        assert result.archetype_name == "fastapi"


class TestDetectRepoProfileDjango:
    """Tests for Django detection."""
    
    def test_detects_django_with_manage_py_and_settings(self, tmp_path):
        """High confidence detection with manage.py and settings."""
        (tmp_path / "manage.py").write_text("#!/usr/bin/env python\nimport django")
        (tmp_path / "myproject").mkdir()
        (tmp_path / "myproject" / "settings.py").write_text("DEBUG = True")
        
        result = detect_repo_profile(tmp_path)
        
        assert result.archetype_name == "django"
        assert result.language == "python"
        assert "manage.py" in str(result.evidence).lower()
    
    def test_detects_django_with_nested_settings(self, tmp_path):
        """Detection with nested project settings."""
        (tmp_path / "manage.py").write_text("#!/usr/bin/env python\nimport django")
        (tmp_path / "config" / "settings").mkdir(parents=True)
        (tmp_path / "config" / "settings" / "base.py").write_text("DEBUG = True")
        
        result = detect_repo_profile(tmp_path)
        
        # Should detect django from manage.py
        assert result.archetype_name == "django"


class TestDetectRepoProfileNextJS:
    """Tests for Next.js detection."""
    
    def test_detects_nextjs_with_config_file(self, tmp_path):
        """Detection with next.config.js."""
        (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18.0.0"}}')
        (tmp_path / "next.config.js").write_text("module.exports = {}")
        
        result = detect_repo_profile(tmp_path)
        
        assert result.archetype_name == "nextjs"
        assert result.language == "typescript"
    
    def test_detects_nextjs_from_dependency(self, tmp_path):
        """Detection from package.json dependency."""
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"next": "^14.0.0", "react": "^18.0.0"}})
        )
        
        result = detect_repo_profile(tmp_path)
        
        assert result.archetype_name == "nextjs"


class TestDetectRepoProfileNestJS:
    """Tests for NestJS detection."""
    
    def test_detects_nestjs_with_core_package(self, tmp_path):
        """Detection from @nestjs/core dependency."""
        (tmp_path / "package.json").write_text(
            json.dumps({
                "dependencies": {
                    "@nestjs/core": "^10.0.0",
                    "@nestjs/common": "^10.0.0",
                }
            })
        )
        
        result = detect_repo_profile(tmp_path)
        
        assert result.archetype_name == "nestjs"
        assert result.language == "typescript"


class TestDetectRepoProfileExpress:
    """Tests for Express.js detection."""
    
    def test_detects_express_from_dependency(self, tmp_path):
        """Detection from express dependency."""
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"express": "^4.18.0"}})
        )
        
        result = detect_repo_profile(tmp_path)
        
        assert result.archetype_name == "express"
        assert result.language == "typescript"


class TestDetectRepoProfileFlask:
    """Tests for Flask detection."""
    
    def test_detects_flask_from_pyproject(self, tmp_path):
        """Detection from pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text('dependencies = ["flask"]')
        
        result = detect_repo_profile(tmp_path)
        
        assert result.archetype_name == "flask"
        assert result.language == "python"
    
    def test_detects_flask_from_app_py(self, tmp_path):
        """Detection from app.py with Flask import."""
        (tmp_path / "app.py").write_text(
            "from flask import Flask\napp = Flask(__name__)"
        )
        
        result = detect_repo_profile(tmp_path)
        
        assert result.archetype_name == "flask"


class TestDetectRepoProfileUnknown:
    """Tests for unknown/generic repos."""
    
    def test_empty_directory_returns_unknown(self, tmp_path):
        """Empty directory should return unknown archetype."""
        result = detect_repo_profile(tmp_path)
        
        assert result.archetype_name == "unknown"
        assert result.confidence < 0.5
        assert not result.is_high_confidence
    
    def test_nonexistent_path_returns_unknown(self):
        """Non-existent path should return unknown."""
        result = detect_repo_profile("/nonexistent/path/12345")
        
        assert result.archetype_name == "unknown"
        assert result.confidence == 0.0
    
    def test_none_repo_root_returns_unknown(self):
        """None repo_root should return unknown."""
        result = detect_repo_profile(None)
        
        assert result.archetype_name == "unknown"
        assert result.confidence == 0.0
    
    def test_python_project_without_framework(self, tmp_path):
        """Generic Python project without recognized framework."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "mypackage"')
        (tmp_path / "src" / "mypackage").mkdir(parents=True)
        (tmp_path / "src" / "mypackage" / "__init__.py").write_text("# Package")
        
        result = detect_repo_profile(tmp_path)
        
        # Should detect Python language but unknown archetype
        assert result.language == "python"
        assert result.archetype_name == "unknown" or "generic" in result.archetype_name


class TestDetectedProfile:
    """Tests for DetectedProfile helper methods."""
    
    def test_is_high_confidence_threshold(self):
        """Test confidence threshold check."""
        high = DetectedProfile(archetype_name="fastapi", language="python", confidence=0.8)
        medium = DetectedProfile(archetype_name="fastapi", language="python", confidence=0.7)
        low = DetectedProfile(archetype_name="unknown", language="python", confidence=0.3)
        
        assert high.is_high_confidence is True
        assert medium.is_high_confidence is False
        assert low.is_high_confidence is False
    
    def test_is_known_archetype(self):
        """Test known archetype check."""
        fastapi = DetectedProfile(archetype_name="fastapi", language="python", confidence=0.9)
        unknown = DetectedProfile(archetype_name="unknown", language="python", confidence=0.3)
        generic = DetectedProfile(archetype_name="generic_python", language="python", confidence=0.6)
        
        assert fastapi.is_known_archetype is True
        assert unknown.is_known_archetype is False
        assert generic.is_known_archetype is False
    
    def test_should_use_archetype_defaults(self):
        """Test decision to use archetype defaults."""
        high_known = DetectedProfile(archetype_name="fastapi", language="python", confidence=0.9)
        low_known = DetectedProfile(archetype_name="fastapi", language="python", confidence=0.5)
        high_unknown = DetectedProfile(archetype_name="unknown", language="python", confidence=0.9)
        
        assert high_known.should_use_archetype_defaults() is True
        assert low_known.should_use_archetype_defaults() is False
        assert high_unknown.should_use_archetype_defaults() is False


class TestBuildEffectiveRepoProfile:
    """Tests for Layer 2: build_effective_repo_profile."""
    
    def test_uses_archetype_defaults_for_high_confidence(self, tmp_path):
        """High confidence known archetype uses defaults."""
        # Setup FastAPI repo
        (tmp_path / "pyproject.toml").write_text('dependencies = ["fastapi", "uvicorn"]')
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text("from fastapi import FastAPI")
        
        detected = detect_repo_profile(tmp_path)
        profile = build_effective_repo_profile(detected, tmp_path)
        
        assert profile.name == "fastapi"
        assert profile.framework == "fastapi"
        assert profile.profile_source == "archetype"
        assert profile.detection_confidence >= 0.8
    
    def test_uses_heuristics_for_low_confidence(self, tmp_path):
        """Low confidence uses heuristic inference."""
        # Empty or ambiguous repo
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        
        detected = detect_repo_profile(tmp_path)
        profile = build_effective_repo_profile(detected, tmp_path)
        
        assert "heuristic" in profile.profile_source
        assert profile.detection_confidence < 0.8
    
    def test_profile_includes_detection_evidence(self, tmp_path):
        """Profile should include detection evidence."""
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"next": "^14.0.0"}})
        )
        
        detected = detect_repo_profile(tmp_path)
        profile = build_effective_repo_profile(detected, tmp_path)
        
        assert profile.detection_evidence is not None
        assert len(profile.detection_evidence) > 0
    
    def test_profile_infers_src_root_from_structure(self, tmp_path):
        """Profile should infer src root from directory structure."""
        (tmp_path / "src" / "app").mkdir(parents=True)
        (tmp_path / "src" / "app" / "__init__.py").write_text("")
        
        detected = detect_repo_profile(tmp_path)
        profile = build_effective_repo_profile(detected, tmp_path)
        
        # Should detect src-based layout
        assert "src" in profile.integrations_root or "src" in str(profile.layout_hints)


class TestPriorityDetection:
    """Tests for framework priority ordering."""
    
    def test_nestjs_priority_over_express(self, tmp_path):
        """NestJS should take priority when both are present."""
        (tmp_path / "package.json").write_text(
            json.dumps({
                "dependencies": {
                    "express": "^4.18.0",
                    "@nestjs/core": "^10.0.0",
                    "@nestjs/common": "^10.0.0",
                }
            })
        )
        
        result = detect_repo_profile(tmp_path)
        
        # NestJS should have higher score due to more specific markers
        assert result.archetype_name == "nestjs"
    
    def test_nextjs_with_config_beats_just_next_dep(self, tmp_path):
        """Next.js with config file has higher confidence."""
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"next": "^14.0.0"}})
        )
        
        result_without_config = detect_repo_profile(tmp_path)
        
        # Add config file
        (tmp_path / "next.config.mjs").write_text("export default {}")
        
        result_with_config = detect_repo_profile(tmp_path)
        
        assert result_with_config.confidence > result_without_config.confidence
