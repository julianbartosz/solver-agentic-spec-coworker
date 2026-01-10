"""
Tests for import safety and optional dependencies.

Ensures that modules can be imported without requiring optional dependencies
at import time (lazy imports).

PR #4: Review dialog should be import-safe without streamlit.
"""
import importlib
import sys
from unittest import mock

import pytest


class TestReviewDialogImportSafety:
    """Test that review_dialog.py can be imported without streamlit."""

    def test_import_without_streamlit(self):
        """
        review_dialog should import successfully even if streamlit is not installed.
        
        This tests the lazy import pattern where streamlit is only imported
        when functions are actually called, not at module import time.
        """
        # Temporarily remove streamlit from sys.modules to simulate it not being installed
        streamlit_modules = {
            name: mod for name, mod in sys.modules.items()
            if name == 'streamlit' or name.startswith('streamlit.')
        }
        
        # Remove streamlit from modules
        for name in streamlit_modules:
            del sys.modules[name]
        
        # Mock the import to raise ImportError
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
        
        def mock_import(name, *args, **kwargs):
            if name == 'streamlit' or name.startswith('streamlit.'):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)
        
        # Remove the module from cache if it exists
        if 'integration_coworker.ui.review_dialog' in sys.modules:
            del sys.modules['integration_coworker.ui.review_dialog']
        
        try:
            # Patch import and try to import review_dialog
            with mock.patch('builtins.__import__', side_effect=mock_import):
                # This should NOT raise ImportError at module level
                from integration_coworker.ui import review_dialog
                
                # Verify the module loaded
                assert hasattr(review_dialog, 'check_pending_review')
                assert hasattr(review_dialog, 'render_review_dialog')
                assert hasattr(review_dialog, 'show_review_dialog')
                assert hasattr(review_dialog, '_get_streamlit')
                
        finally:
            # Restore streamlit modules
            sys.modules.update(streamlit_modules)

    def test_check_pending_review_no_streamlit(self):
        """check_pending_review should work without streamlit (it's pure Python)."""
        from integration_coworker.ui.review_dialog import check_pending_review
        
        # Test with None - no streamlit needed
        result = check_pending_review(None)
        assert result is None
        
        # Test with empty dict - no streamlit needed  
        result = check_pending_review({})
        assert result is None
        
        # Test with dict missing pending_review_kind - no streamlit needed
        result = check_pending_review({"last_result_dict": {}})
        assert result is None

    def test_check_pending_review_returns_info(self):
        """check_pending_review should return review info when pending."""
        from integration_coworker.ui.review_dialog import check_pending_review
        
        # Simulate session state object with last_result_dict attribute
        class MockSessionState:
            last_result_dict = {
                "run_id": "test-run-123",
                "pending_review_kind": "code",
                "review_artifact_refs": {
                    "code": {"code_snapshot": {"type": "file", "path": "/tmp/test.json"}},
                },
            }
            current_run_id = "test-run-123"
        
        result = check_pending_review(MockSessionState())
        
        assert result is not None
        assert result["run_id"] == "test-run-123"
        assert result["kind"] == "code"
        assert "code_snapshot" in result["artifact_refs"]


class TestPyprojectExtras:
    """Test that pyproject.toml has correct extras configuration."""

    def test_ui_extra_exists(self):
        """The [ui] extra should exist in pyproject.toml."""
        from pathlib import Path
        import tomllib
        
        pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
        
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
        
        optional_deps = pyproject.get("project", {}).get("optional-dependencies", {})
        assert "ui" in optional_deps, "pyproject.toml should have [ui] extra"
        
        # Verify streamlit is in ui extra
        ui_deps = optional_deps["ui"]
        streamlit_found = any("streamlit" in dep for dep in ui_deps)
        assert streamlit_found, "streamlit should be in [ui] extra"

    def test_streamlit_not_in_required_deps(self):
        """streamlit should NOT be in required dependencies."""
        from pathlib import Path
        import tomllib
        
        pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
        
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
        
        required_deps = pyproject.get("project", {}).get("dependencies", [])
        
        # streamlit should not be in required deps
        for dep in required_deps:
            assert "streamlit" not in dep.lower(), \
                f"streamlit should not be in required dependencies: {dep}"


class TestAPITypesHITLFields:
    """Test that IntegrationResult has HITL review fields."""

    def test_integration_result_has_review_fields(self):
        """IntegrationResult should have pending_review_kind and related fields."""
        from integration_coworker.api.types import IntegrationResult
        
        # Create a result instance with required fields
        result = IntegrationResult(run_id="test-123", task=None)
        
        # Check for HITL review fields
        assert hasattr(result, 'pending_review_kind'), \
            "IntegrationResult should have pending_review_kind field"
        assert hasattr(result, 'review_artifact_refs'), \
            "IntegrationResult should have review_artifact_refs field"
        assert hasattr(result, 'review_decisions'), \
            "IntegrationResult should have review_decisions field"
        
        # Default values should be None
        assert result.pending_review_kind is None
        assert result.review_artifact_refs is None
        assert result.review_decisions is None

    def test_integration_result_accepts_review_values(self):
        """IntegrationResult should accept HITL review values."""
        from integration_coworker.api.types import IntegrationResult
        
        result = IntegrationResult(
            run_id="test-123",
            task=None,
            pending_review_kind="code",
            review_artifact_refs={"code_snapshot": {"type": "file"}},
            review_decisions={"code": {"approved": True}},
        )
        
        assert result.pending_review_kind == "code"
        assert result.review_artifact_refs == {"code_snapshot": {"type": "file"}}
        assert result.review_decisions == {"code": {"approved": True}}


class TestQualityModulesImportSafety:
    """
    PR #7: Test that quality modules are import-safe.
    
    Ensures quality_models.py and quality_artifacts.py:
    - Do NOT import streamlit
    - Do NOT import graph.nodes (no runtime wiring)
    - Import without errors in minimal environment
    """

    def test_quality_models_import_clean(self):
        """quality_models.py should import without side effects."""
        import sys
        
        # Track modules before import
        modules_before = set(sys.modules.keys())
        
        # Import the module
        from integration_coworker.graph import quality_models
        
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        
        # Should NOT have imported streamlit
        streamlit_modules = [m for m in new_modules if 'streamlit' in m.lower()]
        assert not streamlit_modules, f"quality_models imported streamlit: {streamlit_modules}"
        
        # Should NOT have imported graph.nodes
        node_modules = [m for m in new_modules if 'integration_coworker.graph.nodes' in m]
        assert not node_modules, f"quality_models imported graph.nodes: {node_modules}"

    def test_quality_models_has_expected_exports(self):
        """quality_models.py should export expected domain classes."""
        from integration_coworker.graph import quality_models
        
        # Core domain models
        assert hasattr(quality_models, 'StaticIssue')
        assert hasattr(quality_models, 'StaticAnalysisResult')
        assert hasattr(quality_models, 'FailureAttribution')
        assert hasattr(quality_models, 'QualityScoreBreakdown')
        assert hasattr(quality_models, 'RegenerationTarget')
        # NOTE: HumanEditPatch moved to human_edit_models.py as single source of truth
        assert hasattr(quality_models, 'IterationState')
        
        # Bounded constants
        assert hasattr(quality_models, 'MAX_ISSUES_IN_SUMMARY')
        assert hasattr(quality_models, 'MAX_FIX_HINTS')
        assert hasattr(quality_models, 'MAX_ERROR_MESSAGE_LENGTH')
    
    def test_human_edit_models_is_source_of_truth(self):
        """human_edit_models.py is the canonical source for HumanEditPatch."""
        from integration_coworker.graph import human_edit_models
        from integration_coworker.graph import quality_artifacts
        
        # The canonical location has the full implementation
        assert hasattr(human_edit_models, 'HumanEditPatch')
        assert hasattr(human_edit_models, 'EditValidationResult')
        assert hasattr(human_edit_models, 'EditAuditEntry')
        assert hasattr(human_edit_models, 'PatchHunk')
        
        # quality_artifacts imports from the canonical source
        # Both should reference the SAME class object (not duplicate)
        assert quality_artifacts.HumanEditPatch is human_edit_models.HumanEditPatch

    def test_quality_artifacts_import_clean(self):
        """quality_artifacts.py should import without side effects."""
        import sys
        
        modules_before = set(sys.modules.keys())
        
        from integration_coworker.graph import quality_artifacts
        
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        
        # Should NOT have imported streamlit
        streamlit_modules = [m for m in new_modules if 'streamlit' in m.lower()]
        assert not streamlit_modules, f"quality_artifacts imported streamlit: {streamlit_modules}"
        
        # Should NOT have imported graph.nodes (no runtime wiring)
        node_modules = [m for m in new_modules if 'integration_coworker.graph.nodes' in m]
        assert not node_modules, f"quality_artifacts imported graph.nodes: {node_modules}"

    def test_quality_artifacts_has_expected_exports(self):
        """quality_artifacts.py should export expected helpers."""
        from integration_coworker.graph import quality_artifacts
        
        # Codec constant
        assert hasattr(quality_artifacts, 'QUALITY_ARTIFACT_CODEC')
        assert hasattr(quality_artifacts, 'QUALITY_SCHEMA_VERSION')
        
        # Storage functions
        assert hasattr(quality_artifacts, 'store_static_analysis_artifact')
        assert hasattr(quality_artifacts, 'store_failure_attributions_artifact')
        
        # Summary builders
        assert hasattr(quality_artifacts, 'build_static_analysis_summary')
        assert hasattr(quality_artifacts, 'build_attribution_summary')
        
        # Guards
        assert hasattr(quality_artifacts, 'check_no_blobs_in_quality_refs')
        assert hasattr(quality_artifacts, 'validate_quality_refs_size')

    def test_quality_artifact_codec_is_json(self):
        """QUALITY_ARTIFACT_CODEC must be JSON (not pickle)."""
        from integration_coworker.graph.quality_artifacts import QUALITY_ARTIFACT_CODEC
        from integration_coworker.persistence.artifacts.base import ArtifactCodec
        
        assert QUALITY_ARTIFACT_CODEC == ArtifactCodec.JSON, \
            "Quality artifacts must use JSON codec for safety"


class TestStaticAnalysisGateImportSafety:
    """
    PR #8: Test that static analysis modules are import-safe.
    
    Ensures static_checks.py and static_analysis_gate.py:
    - Do NOT import streamlit
    - Do NOT hardcode tool paths
    - Import without errors in minimal environment
    """

    def test_static_checks_import_clean(self):
        """static_checks.py should import without side effects."""
        import sys
        
        modules_before = set(sys.modules.keys())
        
        from integration_coworker.graph import static_checks
        
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        
        # Should NOT have imported streamlit
        streamlit_modules = [m for m in new_modules if 'streamlit' in m.lower()]
        assert not streamlit_modules, f"static_checks imported streamlit: {streamlit_modules}"

    def test_static_checks_has_registry(self):
        """static_checks.py should export CHECK_REGISTRY."""
        from integration_coworker.graph import static_checks
        
        assert hasattr(static_checks, 'CHECK_REGISTRY')
        assert 'syntax' in static_checks.CHECK_REGISTRY
        assert 'imports' in static_checks.CHECK_REGISTRY

    def test_static_analysis_gate_import_clean(self):
        """static_analysis_gate.py should import without streamlit."""
        import sys
        
        modules_before = set(sys.modules.keys())
        
        from integration_coworker.graph.nodes import static_analysis_gate
        
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        
        streamlit_modules = [m for m in new_modules if 'streamlit' in m.lower()]
        assert not streamlit_modules, f"static_analysis_gate imported streamlit: {streamlit_modules}"

    def test_builtin_checks_are_stdlib_only(self):
        """Built-in checks should only use stdlib modules."""
        from integration_coworker.graph.static_checks import CHECK_REGISTRY
        
        # Verify built-in checks don't require external tools
        assert CHECK_REGISTRY['syntax'].builtin is True
        assert CHECK_REGISTRY['syntax'].tool_name is None
        assert CHECK_REGISTRY['imports'].builtin is True
        assert CHECK_REGISTRY['imports'].tool_name is None