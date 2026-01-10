"""
End-to-end tests for RepoProfile detection using golden repo fixtures.

These tests validate that the two-layer detection pipeline correctly identifies
framework archetypes and produces appropriate profiles for realistic repo structures.
"""
import pytest
from pathlib import Path

from integration_coworker.repo.detection import (
    detect_repo_profile,
    build_effective_repo_profile,
)
from integration_coworker.repo.models import DetectedProfile, RepoProfile


# Mark all tests as not needing DB
pytestmark = pytest.mark.no_db


# Path to golden repo fixtures
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "repos"


class TestFastAPIServiceDetection:
    """Tests for FastAPI service detection."""
    
    @pytest.fixture
    def repo_path(self):
        return FIXTURES_DIR / "fastapi_service"
    
    def test_detects_fastapi_archetype(self, repo_path):
        """Should detect FastAPI with high confidence."""
        detected = detect_repo_profile(repo_path)
        
        assert detected.archetype_name == "fastapi"
        assert detected.language == "python"
        assert detected.confidence >= 0.8, f"Expected high confidence, got {detected.confidence}"
        assert detected.is_high_confidence
    
    def test_fastapi_evidence_includes_markers(self, repo_path):
        """Should include relevant evidence."""
        detected = detect_repo_profile(repo_path)
        
        evidence_str = " ".join(detected.evidence).lower()
        assert "fastapi" in evidence_str
        # Should find either the dependency or the import
        assert "dependency" in evidence_str or "import" in evidence_str or "main.py" in evidence_str
    
    def test_builds_correct_profile(self, repo_path):
        """Should build profile with FastAPI defaults."""
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        assert profile.framework == "fastapi"
        assert profile.language == "python"
        assert profile.profile_source == "archetype"
        assert profile.detection_confidence >= 0.8
        
        # Should use FastAPI archetype layout
        assert "integrations" in profile.integrations_root
        assert "tests" in profile.tests_root


class TestDjangoServiceDetection:
    """Tests for Django service detection."""
    
    @pytest.fixture
    def repo_path(self):
        return FIXTURES_DIR / "django_service"
    
    def test_detects_django_archetype(self, repo_path):
        """Should detect Django with high confidence."""
        detected = detect_repo_profile(repo_path)
        
        assert detected.archetype_name == "django"
        assert detected.language == "python"
        assert detected.confidence >= 0.8
    
    def test_django_evidence_includes_manage_py(self, repo_path):
        """Should include manage.py in evidence."""
        detected = detect_repo_profile(repo_path)
        
        evidence_str = " ".join(detected.evidence).lower()
        assert "manage.py" in evidence_str or "django" in evidence_str
    
    def test_builds_correct_profile(self, repo_path):
        """Should build profile with Django defaults."""
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        assert profile.framework == "django"
        assert profile.language == "python"
        assert profile.profile_source == "archetype"


class TestFlaskServiceDetection:
    """Tests for Flask service detection."""
    
    @pytest.fixture
    def repo_path(self):
        return FIXTURES_DIR / "flask_service"
    
    def test_detects_flask_archetype(self, repo_path):
        """Should detect Flask with high confidence."""
        detected = detect_repo_profile(repo_path)
        
        assert detected.archetype_name == "flask"
        assert detected.language == "python"
        assert detected.confidence >= 0.7  # Flask detection may be slightly lower
    
    def test_flask_evidence_includes_markers(self, repo_path):
        """Should include Flask markers in evidence."""
        detected = detect_repo_profile(repo_path)
        
        evidence_str = " ".join(detected.evidence).lower()
        assert "flask" in evidence_str
    
    def test_builds_correct_profile(self, repo_path):
        """Should build profile with Flask defaults."""
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        assert profile.framework == "flask"
        assert profile.language == "python"


class TestNextJSAppDetection:
    """Tests for Next.js app detection."""
    
    @pytest.fixture
    def repo_path(self):
        return FIXTURES_DIR / "nextjs_app"
    
    def test_detects_nextjs_archetype(self, repo_path):
        """Should detect Next.js with high confidence."""
        detected = detect_repo_profile(repo_path)
        
        assert detected.archetype_name == "nextjs"
        assert detected.language == "typescript"
        assert detected.confidence >= 0.8
    
    def test_nextjs_evidence_includes_markers(self, repo_path):
        """Should include Next.js markers in evidence."""
        detected = detect_repo_profile(repo_path)
        
        evidence_str = " ".join(detected.evidence).lower()
        assert "next" in evidence_str or "next.config" in evidence_str
    
    def test_builds_correct_profile(self, repo_path):
        """Should build profile with Next.js defaults."""
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        assert profile.framework == "nextjs"
        assert profile.language == "typescript"
        # Next.js uses lib/integrations by default
        assert "lib" in profile.integrations_root or "integrations" in profile.integrations_root


class TestNestJSAppDetection:
    """Tests for NestJS app detection."""
    
    @pytest.fixture
    def repo_path(self):
        return FIXTURES_DIR / "nestjs_app"
    
    def test_detects_nestjs_archetype(self, repo_path):
        """Should detect NestJS with high confidence."""
        detected = detect_repo_profile(repo_path)
        
        assert detected.archetype_name == "nestjs"
        assert detected.language == "typescript"
        assert detected.confidence >= 0.8
    
    def test_nestjs_evidence_includes_markers(self, repo_path):
        """Should include NestJS markers in evidence."""
        detected = detect_repo_profile(repo_path)
        
        evidence_str = " ".join(detected.evidence).lower()
        assert "nestjs" in evidence_str or "nest" in evidence_str
    
    def test_builds_correct_profile(self, repo_path):
        """Should build profile with NestJS defaults."""
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        assert profile.framework == "nestjs"
        assert profile.language == "typescript"
        # NestJS uses src/integrations by default
        assert "src" in profile.integrations_root or "integrations" in profile.integrations_root


class TestExpressAppDetection:
    """Tests for Express.js app detection."""
    
    @pytest.fixture
    def repo_path(self):
        return FIXTURES_DIR / "express_app"
    
    def test_detects_express_archetype(self, repo_path):
        """Should detect Express with reasonable confidence."""
        detected = detect_repo_profile(repo_path)
        
        assert detected.archetype_name == "express"
        assert detected.language == "typescript"
        assert detected.confidence >= 0.5
    
    def test_express_evidence_includes_markers(self, repo_path):
        """Should include Express markers in evidence."""
        detected = detect_repo_profile(repo_path)
        
        evidence_str = " ".join(detected.evidence).lower()
        assert "express" in evidence_str
    
    def test_builds_correct_profile(self, repo_path):
        """Should build profile with Express defaults."""
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        assert profile.framework == "express"
        assert profile.language == "typescript"


class TestGenericPythonDetection:
    """Tests for generic Python project detection."""
    
    @pytest.fixture
    def repo_path(self):
        return FIXTURES_DIR / "generic_python"
    
    def test_detects_unknown_or_generic_archetype(self, repo_path):
        """Should detect as unknown/generic Python with low confidence."""
        detected = detect_repo_profile(repo_path)
        
        # Should not match a known framework
        assert detected.archetype_name in ("unknown", "generic_python")
        assert detected.language == "python"
        # Low confidence since no framework detected
        assert detected.confidence < 0.8
        assert not detected.is_high_confidence
    
    def test_builds_inferred_profile(self, repo_path):
        """Should build profile using heuristic inference."""
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        assert profile.language == "python"
        # Should use heuristic or inferred source
        assert "heuristic" in profile.profile_source or "inferred" in profile.profile_source
        
        # Should find src-based layout
        assert "src" in profile.integrations_root or "integrations" in profile.integrations_root
        assert "tests" in profile.tests_root


class TestGenericJSDetection:
    """Tests for generic JavaScript project detection."""
    
    @pytest.fixture
    def repo_path(self):
        return FIXTURES_DIR / "generic_js"
    
    def test_detects_unknown_or_generic_archetype(self, repo_path):
        """Should detect as unknown/generic JS with low confidence."""
        detected = detect_repo_profile(repo_path)
        
        # Should not match a known framework
        assert detected.archetype_name in ("unknown", "generic_typescript", "generic_javascript")
        # Should detect JavaScript/TypeScript
        assert detected.language in ("javascript", "typescript")
        # Low confidence since no framework detected
        assert detected.confidence < 0.8
    
    def test_builds_inferred_profile(self, repo_path):
        """Should build profile using heuristic inference."""
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        assert profile.language in ("javascript", "typescript")
        # Should use heuristic or inferred source
        assert "heuristic" in profile.profile_source or "inferred" in profile.profile_source


class TestDetectionConfidenceThresholds:
    """Tests for confidence threshold behavior."""
    
    def test_high_confidence_uses_archetype_defaults(self):
        """High confidence detection should use archetype defaults."""
        repo_path = FIXTURES_DIR / "fastapi_service"
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        assert detected.is_high_confidence
        assert detected.should_use_archetype_defaults()
        assert profile.profile_source == "archetype"
    
    def test_low_confidence_uses_heuristics(self):
        """Low confidence detection should use heuristic inference."""
        repo_path = FIXTURES_DIR / "generic_python"
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        assert not detected.is_high_confidence
        assert not detected.should_use_archetype_defaults()
        assert "heuristic" in profile.profile_source or "inferred" in profile.profile_source


class TestLowConfidenceGuardrails:
    """Tests for low confidence detection guardrails."""
    
    def test_very_low_confidence_sets_fallback_source(self, caplog):
        """Very low confidence detection should set heuristic_fallback source."""
        # Create a minimal detected profile with low confidence
        detected = DetectedProfile(
            archetype_name="unknown",
            language="python",
            confidence=0.2,  # Very low confidence
            evidence=["No known framework patterns detected"],
        )
        
        import logging
        with caplog.at_level(logging.WARNING):
            profile = build_effective_repo_profile(detected, repo_root=None)
        
        # Should set fallback source
        assert profile.profile_source == "heuristic_fallback"
        # Should use generic project name
        assert profile.name == "generic_python_project"
        
        # Should log a warning
        assert any("low confidence" in record.message.lower() for record in caplog.records)
    
    def test_generic_js_uses_generic_project_name(self):
        """Generic JS detection should use generic_js_project name."""
        detected = DetectedProfile(
            archetype_name="unknown",
            language="javascript",
            confidence=0.3,
            evidence=["No known framework patterns detected"],
        )
        
        profile = build_effective_repo_profile(detected, repo_root=None)
        
        assert profile.name == "generic_js_project"
        assert profile.language == "javascript"
        assert profile.profile_source == "heuristic_fallback"
    
    def test_low_but_not_very_low_uses_heuristic(self):
        """Confidence >= 0.4 should use regular heuristic, not fallback."""
        detected = DetectedProfile(
            archetype_name="unknown",
            language="python",
            confidence=0.45,  # Above LOW_CONFIDENCE_THRESHOLD
            evidence=["No known framework patterns detected"],
        )
        
        profile = build_effective_repo_profile(detected, repo_root=None)
        
        # Should use regular heuristic, not fallback
        assert profile.profile_source == "heuristic"
        assert "fallback" not in profile.profile_source


class TestProfileLayoutHints:
    """Tests for profile layout_hints correctness."""
    
    def test_fastapi_layout_hints(self):
        """FastAPI profile should have correct layout hints."""
        repo_path = FIXTURES_DIR / "fastapi_service"
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        assert profile.layout_hints is not None
        # Should have clients_dir and flows_dir hints
        assert "clients_dir" in profile.layout_hints or "clients" in str(profile.layout_hints)
    
    def test_generic_python_layout_hints(self):
        """Generic Python profile should infer sensible layout hints."""
        repo_path = FIXTURES_DIR / "generic_python"
        detected = detect_repo_profile(repo_path)
        profile = build_effective_repo_profile(detected, repo_path)
        
        # Should have detected src layout
        if profile.layout_hints:
            hints_str = str(profile.layout_hints).lower()
            # Should reference either src or integrations
            assert "src" in hints_str or "integrations" in hints_str or "clients" in hints_str
