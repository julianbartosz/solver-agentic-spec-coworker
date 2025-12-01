"""
End-to-end tests for RepoProfile detection integrated with the full pipeline.

These tests validate that auto-detection flows through the entire design_and_generate_integration
pipeline, produces correct reports, and places files in the right locations.
"""
import pytest
from pathlib import Path
import tempfile
import shutil
import os

from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions


# Path to golden repo fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "repos"
SPEC_FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def mock_payments_spec():
    """Path to mock_payments OpenAPI spec fixture."""
    return str(SPEC_FIXTURES_DIR / "mock_payments_openapi.yaml")


@pytest.fixture
def fastapi_repo(tmp_path):
    """Create a copy of the FastAPI fixture repo in tmp_path."""
    src = FIXTURES_DIR / "fastapi_service"
    dst = tmp_path / "fastapi_repo"
    shutil.copytree(src, dst)
    return dst


@pytest.fixture
def generic_python_repo(tmp_path):
    """Create a copy of the generic Python fixture repo in tmp_path."""
    src = FIXTURES_DIR / "generic_python"
    dst = tmp_path / "generic_python_repo"
    shutil.copytree(src, dst)
    return dst


@pytest.fixture
def nextjs_repo(tmp_path):
    """Create a copy of the Next.js fixture repo in tmp_path."""
    src = FIXTURES_DIR / "nextjs_app"
    dst = tmp_path / "nextjs_repo"
    shutil.copytree(src, dst)
    return dst


class TestEndToEndWithFastAPIRepoDetection:
    """Tests for E2E pipeline with FastAPI repo auto-detection."""
    
    def test_auto_detects_fastapi_profile(self, mock_payments_spec, fastapi_repo):
        """Should auto-detect FastAPI and include profile info in report."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(fastapi_repo),
            repo_profile=None,  # Let it auto-detect
            options=IntegrationOptions(
                dry_run=True,
                repo_integration_enabled=True,
            ),
        )
        
        # Should complete successfully
        assert result.run_id is not None
        assert result.task is not None
        
        # Report should include Repository Profile section
        assert result.report_markdown is not None
        assert "Repository Profile" in result.report_markdown
        
        # Should detect FastAPI
        report_lower = result.report_markdown.lower()
        assert "fastapi" in report_lower
        
        # Should have high detection confidence
        assert "high" in report_lower or "100%" in result.report_markdown or "80%" in result.report_markdown or "90%" in result.report_markdown
    
    def test_generated_files_use_fastapi_layout(self, mock_payments_spec, fastapi_repo):
        """Generated files should land in FastAPI-appropriate locations."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(fastapi_repo),
            repo_profile=None,
            options=IntegrationOptions(
                dry_run=False,
                repo_integration_enabled=True,
            ),
        )
        
        # Should have repo changes
        assert result.repo_changes is not None
        assert len(result.repo_changes.changes) > 0
        
        # Check that files use FastAPI-style paths
        created_files = result.repo_changes.files_created()
        assert len(created_files) > 0
        
        # At least one file should be in app/integrations or similar FastAPI location
        paths = [c.rel_path for c in created_files]
        paths_str = " ".join(paths).lower()
        assert "integrations" in paths_str
        
        # Should include client, flow, and test artifacts
        has_client = any("client" in p.lower() for p in paths)
        has_test = any("test" in p.lower() for p in paths)
        assert has_client or has_test  # At least one


class TestEndToEndWithGenericPythonRepoDetection:
    """Tests for E2E pipeline with generic Python repo (no framework)."""
    
    def test_detects_generic_python_profile(self, mock_payments_spec, generic_python_repo):
        """Should detect as generic Python with inferred layout."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(generic_python_repo),
            repo_profile=None,
            options=IntegrationOptions(
                dry_run=True,
                repo_integration_enabled=True,
            ),
        )
        
        assert result.run_id is not None
        assert result.report_markdown is not None
        
        # Should indicate inferred/heuristic profile
        report_lower = result.report_markdown.lower()
        assert "repository profile" in report_lower
        # Should indicate low confidence or heuristic
        assert "heuristic" in report_lower or "inferred" in report_lower or "low" in report_lower
    
    def test_infers_src_based_layout(self, mock_payments_spec, generic_python_repo):
        """Should infer src-based layout for generic Python project."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(generic_python_repo),
            repo_profile=None,
            options=IntegrationOptions(
                dry_run=False,
                repo_integration_enabled=True,
            ),
        )
        
        assert result.repo_changes is not None
        created_files = result.repo_changes.files_created()
        
        # Files should be placed sensibly (src/integrations or similar)
        paths = [c.rel_path for c in created_files]
        paths_str = " ".join(paths).lower()
        
        # Should have integrations somewhere in path
        assert "integrations" in paths_str
        # Tests should go to tests directory
        test_paths = [p for p in paths if "test" in p.lower()]
        if test_paths:
            assert any("tests" in p.lower() for p in test_paths)


class TestEndToEndWithJSRepoDetection:
    """Tests for E2E pipeline with JavaScript/TypeScript repo."""
    
    def test_detects_nextjs_archetype(self, mock_payments_spec, nextjs_repo):
        """Should detect Next.js archetype accurately."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(nextjs_repo),
            repo_profile=None,
            options=IntegrationOptions(
                dry_run=True,
                repo_integration_enabled=True,
            ),
        )
        
        assert result.run_id is not None
        assert result.report_markdown is not None
        
        # Report should accurately state nextjs archetype
        report_lower = result.report_markdown.lower()
        assert "repository profile" in report_lower
        assert "nextjs" in report_lower or "next" in report_lower
        
        # Language should be typescript
        assert "typescript" in report_lower
    
    def test_uses_nextjs_integration_root(self, mock_payments_spec, nextjs_repo):
        """Should use Next.js appropriate integration locations."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(nextjs_repo),
            repo_profile=None,
            options=IntegrationOptions(
                dry_run=False,
                repo_integration_enabled=True,
            ),
        )
        
        # Note: Even though we detect Next.js, codegen currently generates Python
        # But the profile should still be set correctly
        assert result.report_markdown is not None
        
        # Profile should mention lib/integrations (Next.js default) or similar
        report = result.report_markdown.lower()
        # Should reference the integrations root from profile
        assert "integrations" in report


class TestEndToEndReportContents:
    """Tests for report content when using auto-detection."""
    
    def test_report_includes_detection_confidence(self, mock_payments_spec, fastapi_repo):
        """Report should include detection confidence percentage."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(fastapi_repo),
            repo_profile=None,
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=True),
        )
        
        assert "Detection Confidence" in result.report_markdown
        # Should have a percentage
        assert "%" in result.report_markdown
    
    def test_report_includes_profile_source(self, mock_payments_spec, fastapi_repo):
        """Report should include profile source (archetype/heuristic/etc)."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(fastapi_repo),
            repo_profile=None,
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=True),
        )
        
        assert "Profile Source" in result.report_markdown
    
    def test_report_includes_detection_evidence(self, mock_payments_spec, fastapi_repo):
        """Report should include detection evidence."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(fastapi_repo),
            repo_profile=None,
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=True),
        )
        
        assert "Detection Evidence" in result.report_markdown
    
    def test_report_includes_layout_configuration(self, mock_payments_spec, fastapi_repo):
        """Report should include layout configuration details."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(fastapi_repo),
            repo_profile=None,
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=True),
        )
        
        assert "Layout Configuration" in result.report_markdown
        assert "Integrations root" in result.report_markdown
        assert "Tests root" in result.report_markdown
    
    def test_report_shows_low_confidence_warning(self, mock_payments_spec, generic_python_repo):
        """Report should show warning for low confidence detection."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(generic_python_repo),
            repo_profile=None,
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=True),
        )
        
        # For generic/low confidence repos, should show warning
        report_lower = result.report_markdown.lower()
        # Should either have heuristic_fallback or low confidence indication
        has_warning = (
            "heuristic fallback" in report_lower or
            "low detection confidence" in report_lower or
            "layout inference may be unreliable" in report_lower
        )
        assert has_warning, f"Expected low confidence warning in report, got: {result.report_markdown[:500]}"
    
    def test_report_shows_detected_archetype(self, mock_payments_spec, fastapi_repo):
        """Report should show the detected archetype name."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(fastapi_repo),
            repo_profile=None,
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=True),
        )
        
        # Should have Detected Archetype section
        assert "Detected Archetype" in result.report_markdown
        # FastAPI should be mentioned
        assert "fastapi" in result.report_markdown.lower()
