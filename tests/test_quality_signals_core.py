"""
Tests for Quality Signals Core (PR #7).

These tests validate:
1. Model JSON compatibility (all dataclasses serialize/deserialize)
2. Artifact roundtrip (store large results, load them back)
3. Bounded summary enforcement (truncation works correctly)
4. No blobs in state (quality_refs stays small)

Per ADR-HITL-ENHANCEMENT-v2:
- All tests must be deterministic and offline
- No Streamlit, LangGraph internals, or real DB connections needed
- Focus on contract validation, not integration
"""

import json
import pytest
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, Any, List
from unittest.mock import MagicMock, patch


# =============================================================================
# Test: Module Import Safety
# =============================================================================

class TestImportSafety:
    """Verify quality modules don't pull in heavy dependencies."""
    
    def test_quality_models_import_no_streamlit(self):
        """quality_models should import without streamlit."""
        import sys
        # Clear any cached imports
        modules_before = set(sys.modules.keys())
        
        # Import the module
        from integration_coworker.graph import quality_models
        
        # Check no streamlit was pulled in
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        
        streamlit_modules = [m for m in new_modules if 'streamlit' in m.lower()]
        assert not streamlit_modules, f"Streamlit modules imported: {streamlit_modules}"
    
    def test_quality_models_import_no_langgraph(self):
        """quality_models should not import langgraph internals."""
        import sys
        modules_before = set(sys.modules.keys())
        
        from integration_coworker.graph import quality_models
        
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        
        # Allow langgraph.types but not langgraph.graph, langgraph.checkpoint, etc.
        forbidden = [m for m in new_modules if 'langgraph.graph' in m or 'langgraph.checkpoint' in m]
        assert not forbidden, f"LangGraph internals imported: {forbidden}"


# =============================================================================
# Test: Model JSON Compatibility
# =============================================================================

class TestModelJsonCompatibility:
    """Verify all models can serialize to JSON and back."""
    
    def test_static_issue_roundtrip(self):
        """StaticIssue should roundtrip through JSON."""
        from integration_coworker.graph.quality_models import StaticIssue
        
        issue = StaticIssue(
            severity="error",
            category="syntax",
            file_path="src/client.py",
            line_number=42,
            column=10,
            message="SyntaxError: invalid syntax",
            rule_id="E999",
            suggested_fix="Add missing colon",
            auto_fixable=False,
        )
        
        # Serialize to dict
        d = issue.to_dict()
        
        # Must be JSON-serializable
        json_str = json.dumps(d)
        
        # Roundtrip
        loaded = json.loads(json_str)
        restored = StaticIssue.from_dict(loaded)
        
        assert restored.severity == issue.severity
        assert restored.category == issue.category
        assert restored.file_path == issue.file_path
        assert restored.line_number == issue.line_number
        assert restored.message == issue.message
        assert restored.rule_id == issue.rule_id
        assert restored.auto_fixable == issue.auto_fixable
    
    def test_static_analysis_result_roundtrip(self):
        """StaticAnalysisResult with issues should roundtrip."""
        from integration_coworker.graph.quality_models import StaticAnalysisResult, StaticIssue
        
        issues = [
            StaticIssue(
                severity="error",
                category="syntax",
                file_path="src/a.py",
                line_number=1,
                column=0,
                message="Error A",
            ),
            StaticIssue(
                severity="warning",
                category="lint",
                file_path="src/b.py",
                line_number=10,
                column=5,
                message="Warning B",
                rule_id="W001",
            ),
        ]
        
        result = StaticAnalysisResult(
            passed=False,
            issues=issues,
            blocking_count=1,
            warning_count=1,
            tool_versions={"ruff": "0.1.0", "ast": "3.11"},
        )
        
        d = result.to_dict()
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        restored = StaticAnalysisResult.from_dict(loaded)
        
        assert restored.passed == result.passed
        assert restored.blocking_count == result.blocking_count
        assert restored.warning_count == result.warning_count
        assert len(restored.issues) == 2
        assert restored.tool_versions == result.tool_versions
    
    def test_failure_attribution_roundtrip(self):
        """FailureAttribution with all fields should roundtrip."""
        from integration_coworker.graph.quality_models import FailureAttribution
        
        attr = FailureAttribution(
            test_name="test_create_pet",
            test_file="tests/test_client.py",
            error_type="TypeError",
            error_message="Expected str, got int",
            likely_cause_file="src/client.py",
            likely_cause_lines=(10, 15),
            confidence=0.85,
            fix_hints=["Check return type", "Add type annotation"],
        )
        
        d = attr.to_dict()
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        restored = FailureAttribution.from_dict(loaded)
        
        assert restored.test_name == attr.test_name
        assert restored.confidence == attr.confidence
        assert restored.likely_cause_lines == attr.likely_cause_lines
        assert restored.fix_hints == attr.fix_hints
    
    def test_failure_attribution_null_fields(self):
        """FailureAttribution with null attribution should roundtrip."""
        from integration_coworker.graph.quality_models import FailureAttribution
        
        attr = FailureAttribution(
            test_name="test_unknown",
            test_file="tests/test.py",
            error_type="AssertionError",
            error_message="assert False",
            # No attribution found
            likely_cause_file=None,
            likely_cause_lines=None,
            confidence=0.0,
        )
        
        d = attr.to_dict()
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        restored = FailureAttribution.from_dict(loaded)
        
        assert restored.likely_cause_file is None
        assert restored.likely_cause_lines is None
        assert restored.confidence == 0.0
    
    def test_quality_score_breakdown_roundtrip(self):
        """QualityScoreBreakdown should roundtrip."""
        from integration_coworker.graph.quality_models import QualityScoreBreakdown
        
        breakdown = QualityScoreBreakdown(
            overall=75.5,
            static_analysis=80.0,
            test_coverage=70.0,
            complexity=None,  # Not measured
        )
        
        d = breakdown.to_dict()
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        restored = QualityScoreBreakdown.from_dict(loaded)
        
        assert restored.overall == breakdown.overall
        assert restored.static_analysis == breakdown.static_analysis
        assert restored.test_coverage == breakdown.test_coverage
        assert restored.complexity is None
    
    def test_regeneration_target_roundtrip(self):
        """RegenerationTarget should roundtrip."""
        from integration_coworker.graph.quality_models import RegenerationTarget
        
        target = RegenerationTarget(
            file_path="src/api/client.py",
            reason="Multiple test failures",
            failures_linked=["test_a", "test_b"],
            priority=2,
            constraints=["Keep existing method signatures"],
        )
        
        d = target.to_dict()
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        restored = RegenerationTarget.from_dict(loaded)
        
        assert restored.file_path == target.file_path
        assert restored.priority == target.priority
        assert restored.failures_linked == target.failures_linked
    
    def test_human_edit_patch_roundtrip(self):
        """HumanEditPatch should roundtrip."""
        # Single source of truth: human_edit_models.py (PR #11)
        from integration_coworker.graph.human_edit_models import HumanEditPatch
        
        patch = HumanEditPatch(
            file_path="src/client.py",
            patch_text="--- a/src/client.py\n+++ b/src/client.py\n@@ -10 +10 @@\n-old\n+new",
            reason="Fix typo in method name",
            editor_id="user123",
        )
        
        d = patch.to_dict()
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        restored = HumanEditPatch.from_dict(loaded)
        
        assert restored.file_path == patch.file_path
        assert restored.patch_text == patch.patch_text
        assert restored.reason == patch.reason
        # Timestamp is datetime now, compare ISO strings
        assert restored.timestamp.isoformat() == patch.timestamp.isoformat()

    
    def test_iteration_state_roundtrip(self):
        """IterationState should roundtrip."""
        from integration_coworker.graph.quality_models import IterationState
        
        state = IterationState(
            iteration_count=2,
            max_iterations=3,
            quality_history=[60.0, 75.0],
            escalation_reason=None,
        )
        
        d = state.to_dict()
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        restored = IterationState.from_dict(loaded)
        
        assert restored.iteration_count == state.iteration_count
        assert restored.max_iterations == state.max_iterations
        assert restored.quality_history == state.quality_history
    
    def test_iteration_state_from_empty_dict(self):
        """IterationState.from_dict({}) should return defaults."""
        from integration_coworker.graph.quality_models import IterationState
        
        state = IterationState.from_dict({})
        
        assert state.iteration_count == 0
        assert state.max_iterations == 3
        assert state.quality_history == []


# =============================================================================
# Test: Iteration State Logic
# =============================================================================

class TestIterationStateLogic:
    """Test iteration budget enforcement."""
    
    def test_should_continue_under_budget(self):
        """Should continue if under max iterations."""
        from integration_coworker.graph.quality_models import IterationState
        
        state = IterationState(iteration_count=1, max_iterations=3)
        should_continue, reason = state.should_continue()
        
        assert should_continue is True
        assert reason == "continue"
    
    def test_should_stop_at_max(self):
        """Should stop at max iterations."""
        from integration_coworker.graph.quality_models import IterationState
        
        state = IterationState(iteration_count=3, max_iterations=3)
        should_continue, reason = state.should_continue()
        
        assert should_continue is False
        assert reason == "max_iterations_reached"
    
    def test_should_stop_quality_not_improving(self):
        """Should stop if quality not improving."""
        from integration_coworker.graph.quality_models import IterationState
        
        state = IterationState(
            iteration_count=2,
            max_iterations=5,
            quality_history=[70.0, 65.0],  # Declining
        )
        should_continue, reason = state.should_continue()
        
        assert should_continue is False
        assert reason == "quality_not_improving"
    
    def test_record_iteration(self):
        """record_iteration should increment count and add score."""
        from integration_coworker.graph.quality_models import IterationState
        
        state = IterationState(iteration_count=1, quality_history=[60.0])
        new_state = state.record_iteration(75.0)
        
        assert new_state.iteration_count == 2
        assert new_state.quality_history == [60.0, 75.0]
        # Original should be unchanged (immutable pattern)
        assert state.iteration_count == 1


# =============================================================================
# Test: Bounded Summary Builders
# =============================================================================

class TestBoundedSummaryBuilders:
    """Test that summary builders enforce size limits."""
    
    def test_static_analysis_summary_truncates_issues(self):
        """Summary should cap issues at MAX_ISSUES_IN_SUMMARY."""
        from integration_coworker.graph.quality_models import (
            StaticAnalysisResult, StaticIssue, MAX_ISSUES_IN_SUMMARY
        )
        from integration_coworker.graph.quality_artifacts import build_static_analysis_summary
        
        # Create more issues than the limit
        issues = [
            StaticIssue(
                severity="warning",
                category="lint",
                file_path=f"src/file_{i}.py",
                line_number=i,
                column=0,
                message=f"Warning {i}",
            )
            for i in range(50)  # Way over MAX_ISSUES_IN_SUMMARY
        ]
        
        result = StaticAnalysisResult(
            passed=True,
            issues=issues,
            blocking_count=0,
            warning_count=50,
        )
        
        summary = build_static_analysis_summary(result)
        
        assert summary["total_issues"] == 50
        assert summary["issues_truncated"] is True
        assert len(summary["issues_preview"]) == MAX_ISSUES_IN_SUMMARY
    
    def test_static_analysis_summary_not_truncated_small(self):
        """Summary should not set truncated flag for small lists."""
        from integration_coworker.graph.quality_models import (
            StaticAnalysisResult, StaticIssue, MAX_ISSUES_IN_SUMMARY
        )
        from integration_coworker.graph.quality_artifacts import build_static_analysis_summary
        
        issues = [
            StaticIssue(
                severity="error",
                category="syntax",
                file_path="src/a.py",
                line_number=1,
                column=0,
                message="Error",
            )
        ]
        
        result = StaticAnalysisResult(passed=False, issues=issues, blocking_count=1)
        summary = build_static_analysis_summary(result)
        
        assert summary["total_issues"] == 1
        assert summary["issues_truncated"] is False
        assert len(summary["issues_preview"]) == 1
    
    def test_attribution_summary_truncates(self):
        """Attribution summary should cap at limit."""
        from integration_coworker.graph.quality_models import (
            FailureAttribution, MAX_ISSUES_IN_SUMMARY
        )
        from integration_coworker.graph.quality_artifacts import build_attribution_summary
        
        attributions = [
            FailureAttribution(
                test_name=f"test_{i}",
                test_file="tests/test.py",
                error_type="Error",
                error_message="msg",
                confidence=0.5,
            )
            for i in range(30)
        ]
        
        summary = build_attribution_summary(attributions)
        
        assert summary["count"] == 30
        assert summary["attributions_truncated"] is True
        assert len(summary["attributions_preview"]) == MAX_ISSUES_IN_SUMMARY
    
    def test_attribution_summary_high_confidence_first(self):
        """High confidence attributions should appear first in preview."""
        from integration_coworker.graph.quality_models import FailureAttribution
        from integration_coworker.graph.quality_artifacts import build_attribution_summary
        
        attributions = [
            FailureAttribution(
                test_name="low_conf",
                test_file="t.py",
                error_type="E",
                error_message="m",
                confidence=0.3,
            ),
            FailureAttribution(
                test_name="high_conf",
                test_file="t.py",
                error_type="E",
                error_message="m",
                confidence=0.9,
            ),
        ]
        
        summary = build_attribution_summary(attributions)
        
        # High confidence should be first
        assert summary["attributions_preview"][0]["test"] == "high_conf"
        assert summary["high_confidence_count"] == 1
    
    def test_targets_summary_sorted_by_priority(self):
        """Targets summary should sort by priority (highest first)."""
        from integration_coworker.graph.quality_models import RegenerationTarget
        from integration_coworker.graph.quality_artifacts import build_targets_summary
        
        targets = [
            RegenerationTarget(file_path="low.py", reason="r", priority=1),
            RegenerationTarget(file_path="high.py", reason="r", priority=10),
            RegenerationTarget(file_path="mid.py", reason="r", priority=5),
        ]
        
        summary = build_targets_summary(targets)
        
        # Should be sorted: high (10), mid (5), low (1)
        files = [t["file"] for t in summary["targets_preview"]]
        assert files == ["high.py", "mid.py", "low.py"]
    
    def test_summary_truncates_long_messages(self):
        """Summary should truncate long error messages."""
        from integration_coworker.graph.quality_models import (
            StaticAnalysisResult, StaticIssue, MAX_ERROR_MESSAGE_LENGTH
        )
        from integration_coworker.graph.quality_artifacts import build_static_analysis_summary
        
        long_message = "X" * 1000  # Way over MAX_ERROR_MESSAGE_LENGTH
        
        issues = [
            StaticIssue(
                severity="error",
                category="syntax",
                file_path="a.py",
                line_number=1,
                column=0,
                message=long_message,
            )
        ]
        
        result = StaticAnalysisResult(passed=False, issues=issues, blocking_count=1)
        summary = build_static_analysis_summary(result)
        
        # Message in preview should be truncated
        preview_msg = summary["issues_preview"][0]["message"]
        assert len(preview_msg) <= MAX_ERROR_MESSAGE_LENGTH
        assert preview_msg.endswith("...")


# =============================================================================
# Test: No Blobs in State Guard
# =============================================================================

class TestNoBlobsGuard:
    """Test the no-blobs-in-state validation."""
    
    def test_clean_quality_refs_passes(self):
        """Clean quality_refs should pass validation."""
        from integration_coworker.graph.quality_artifacts import check_no_blobs_in_quality_refs
        
        quality_refs = {
            "static": {
                "result": {
                    "__artifact_ref__": True,
                    "run_id": "test",
                    "key": "static_analysis_result",
                    "uri": "file:///tmp/test.json",
                    "sha256": "abc123",
                    "size_bytes": 1000,
                },
                "summary": {
                    "passed": True,
                    "blocking_count": 0,
                    "total_issues": 5,
                    "issues_preview": [],
                },
                "schema_version": "1.0",
            }
        }
        
        violations = check_no_blobs_in_quality_refs(quality_refs)
        assert violations == []
    
    def test_large_list_detected(self):
        """Large lists in quality_refs should be detected."""
        from integration_coworker.graph.quality_artifacts import check_no_blobs_in_quality_refs
        
        quality_refs = {
            "static": {
                "summary": {
                    # This would be a violation - issues not bounded
                    "issues": [{"msg": f"issue {i}"} for i in range(200)],
                }
            }
        }
        
        violations = check_no_blobs_in_quality_refs(quality_refs)
        assert len(violations) > 0
        assert any("issues" in v for v in violations)
    
    def test_content_blob_detected(self):
        """Content blobs should be detected."""
        from integration_coworker.graph.quality_artifacts import check_no_blobs_in_quality_refs
        
        quality_refs = {
            "static": {
                "summary": {
                    # Embedding file content is a violation
                    "content": "x" * 5000,
                }
            }
        }
        
        violations = check_no_blobs_in_quality_refs(quality_refs)
        assert len(violations) > 0
        assert any("content" in v for v in violations)
    
    def test_size_validation(self):
        """Total size should be under threshold."""
        from integration_coworker.graph.quality_artifacts import validate_quality_refs_size
        
        # Small refs should pass
        small_refs = {"static": {"summary": {"passed": True}}}
        assert validate_quality_refs_size(small_refs, max_bytes=65536) is True
        
        # Artificially large refs should fail
        large_refs = {"data": "x" * 100000}
        assert validate_quality_refs_size(large_refs, max_bytes=65536) is False


# =============================================================================
# Test: Artifact Roundtrip (Mocked Store)
# =============================================================================

class TestArtifactRoundtrip:
    """Test artifact storage and loading roundtrips."""
    
    def test_static_analysis_artifact_roundtrip(self):
        """Store and load static analysis result."""
        from integration_coworker.graph.quality_models import StaticAnalysisResult, StaticIssue
        from integration_coworker.graph.quality_artifacts import (
            store_static_analysis_artifact,
            load_static_analysis_result,
            QUALITY_ARTIFACT_CODEC,
        )
        from integration_coworker.persistence.artifacts.base import ArtifactRef, ArtifactCodec
        
        # Create test data
        issues = [
            StaticIssue(
                severity="error",
                category="syntax",
                file_path="src/a.py",
                line_number=10,
                column=5,
                message="Syntax error",
            )
            for _ in range(200)  # Large number of issues
        ]
        
        result = StaticAnalysisResult(
            passed=False,
            issues=issues,
            blocking_count=200,
            warning_count=0,
            tool_versions={"ast": "3.11"},
        )
        
        # Mock the artifact store
        stored_data = {}
        
        def mock_put(run_id, key, value, codec=None):
            stored_data[key] = value
            return ArtifactRef(
                run_id=run_id,
                key=key,
                uri=f"file:///tmp/{key}.json",
                sha256="abc123",
                size_bytes=len(json.dumps(value)),
                codec=codec or ArtifactCodec.JSON,
            )
        
        def mock_get(ref):
            return stored_data.get(ref.key)
        
        mock_store = MagicMock()
        mock_store.put = mock_put
        mock_store.get = mock_get
        
        with patch('integration_coworker.graph.quality_artifacts.get_artifact_store', return_value=mock_store):
            # Store
            refs = store_static_analysis_artifact("run-123", result)
            
            # Verify structure
            assert "result" in refs
            assert "summary" in refs
            assert "schema_version" in refs
            assert refs["schema_version"] == "1.0"
            
            # Verify summary is bounded
            assert refs["summary"]["total_issues"] == 200
            assert len(refs["summary"]["issues_preview"]) <= 10
            
            # Load back
            quality_refs = {"static": refs}
            loaded = load_static_analysis_result(quality_refs)
            
            assert loaded is not None
            assert loaded.passed == result.passed
            assert loaded.blocking_count == result.blocking_count
            assert len(loaded.issues) == 200
    
    def test_failure_attributions_artifact_roundtrip(self):
        """Store and load failure attributions."""
        from integration_coworker.graph.quality_models import FailureAttribution
        from integration_coworker.graph.quality_artifacts import (
            store_failure_attributions_artifact,
            load_failure_attributions,
        )
        from integration_coworker.persistence.artifacts.base import ArtifactRef, ArtifactCodec
        
        attributions = [
            FailureAttribution(
                test_name=f"test_{i}",
                test_file="tests/test.py",
                error_type="TypeError",
                error_message="Error message",
                likely_cause_file="src/client.py",
                likely_cause_lines=(10, 20),
                confidence=0.8,
                fix_hints=["Fix hint 1"],
            )
            for i in range(50)
        ]
        
        stored_data = {}
        
        def mock_put(run_id, key, value, codec=None):
            stored_data[key] = value
            return ArtifactRef(
                run_id=run_id,
                key=key,
                uri=f"file:///tmp/{key}.json",
                sha256="def456",
                size_bytes=len(json.dumps(value)),
                codec=codec or ArtifactCodec.JSON,
            )
        
        def mock_get(ref):
            return stored_data.get(ref.key)
        
        mock_store = MagicMock()
        mock_store.put = mock_put
        mock_store.get = mock_get
        
        with patch('integration_coworker.graph.quality_artifacts.get_artifact_store', return_value=mock_store):
            refs = store_failure_attributions_artifact("run-123", attributions)
            
            assert refs["summary"]["count"] == 50
            assert refs["summary"]["high_confidence_count"] == 50  # All have 0.8
            
            quality_refs = {"sandbox_attribution": refs}
            loaded = load_failure_attributions(quality_refs)
            
            assert loaded is not None
            assert len(loaded) == 50
            assert loaded[0].test_name == "test_0"


# =============================================================================
# Test: ArtifactRef Contract
# =============================================================================

class TestArtifactRefContract:
    """Test ArtifactRef serialization contract."""
    
    def test_artifact_ref_to_dict_has_marker(self):
        """ArtifactRef.to_dict() must include __artifact_ref__ marker."""
        from integration_coworker.persistence.artifacts.base import ArtifactRef, ArtifactCodec
        
        ref = ArtifactRef(
            run_id="test",
            key="quality",
            uri="file:///tmp/test.json",
            sha256="abc123",
            size_bytes=100,
            codec=ArtifactCodec.JSON,
        )
        
        d = ref.to_dict()
        
        assert d.get("__artifact_ref__") is True
    
    def test_artifact_ref_roundtrip(self):
        """ArtifactRef.from_dict(ref.to_dict()) should equal original."""
        from integration_coworker.persistence.artifacts.base import ArtifactRef, ArtifactCodec
        from datetime import datetime, timezone
        
        ref = ArtifactRef(
            run_id="test-run",
            key="quality_result",
            uri="file:///tmp/quality.json",
            sha256="sha256hash",
            size_bytes=12345,
            content_type="application/json",
            codec=ArtifactCodec.JSON,
            created_at=datetime(2026, 1, 3, 12, 0, 0, tzinfo=timezone.utc),
            metadata={"test": True},
        )
        
        d = ref.to_dict()
        
        # Must be JSON-serializable
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        
        restored = ArtifactRef.from_dict(loaded)
        
        assert restored.run_id == ref.run_id
        assert restored.key == ref.key
        assert restored.uri == ref.uri
        assert restored.sha256 == ref.sha256
        assert restored.size_bytes == ref.size_bytes
        assert restored.codec == ref.codec
        assert restored.metadata == ref.metadata
    
    def test_is_artifact_ref_detection(self):
        """ArtifactRef.is_artifact_ref() should detect ref dicts."""
        from integration_coworker.persistence.artifacts.base import ArtifactRef
        
        ref_dict = {"__artifact_ref__": True, "run_id": "x", "key": "y"}
        assert ArtifactRef.is_artifact_ref(ref_dict) is True
        
        not_ref = {"run_id": "x", "key": "y"}  # Missing marker
        assert ArtifactRef.is_artifact_ref(not_ref) is False
        
        assert ArtifactRef.is_artifact_ref("string") is False
        assert ArtifactRef.is_artifact_ref(None) is False
        assert ArtifactRef.is_artifact_ref([]) is False


# =============================================================================
# Test: State Integration
# =============================================================================

class TestStateIntegration:
    """Test quality fields in WorkflowState."""
    
    def test_state_has_quality_refs_field(self):
        """WorkflowState should have quality_refs field."""
        from integration_coworker.graph.state import WorkflowState
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
        )
        
        assert hasattr(state, 'quality_refs')
        assert isinstance(state.quality_refs, dict)
    
    def test_state_has_iteration_state_field(self):
        """WorkflowState should have iteration_state field."""
        from integration_coworker.graph.state import WorkflowState
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
        )
        
        assert hasattr(state, 'iteration_state')
        assert state.iteration_state is None  # Default is None
    
    def test_state_has_static_analysis_fields(self):
        """WorkflowState should have static analysis tracking fields."""
        from integration_coworker.graph.state import WorkflowState
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
        )
        
        assert hasattr(state, 'static_analysis_retries')
        assert hasattr(state, 'static_analysis_escalated')
        assert state.static_analysis_retries == 0
        assert state.static_analysis_escalated is False
    
    def test_quality_refs_json_serializable(self):
        """quality_refs with populated data should be JSON-serializable."""
        from integration_coworker.graph.state import WorkflowState
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
        )
        
        # Populate quality_refs as it would be in real usage
        state.quality_refs = {
            "static": {
                "result": {
                    "__artifact_ref__": True,
                    "run_id": "test",
                    "key": "static",
                    "uri": "file:///tmp/test.json",
                    "sha256": "abc",
                    "size_bytes": 100,
                },
                "summary": {
                    "passed": True,
                    "blocking_count": 0,
                    "total_issues": 5,
                },
                "schema_version": "1.0",
            }
        }
        
        # Should serialize without error
        json_str = json.dumps(state.quality_refs)
        assert len(json_str) > 0
        
        # Should roundtrip
        loaded = json.loads(json_str)
        assert loaded == state.quality_refs


# =============================================================================
# Test: Codec Enforcement
# =============================================================================

class TestCodecEnforcement:
    """Test that JSON codec is enforced for quality artifacts."""
    
    def test_quality_artifact_codec_is_json(self):
        """QUALITY_ARTIFACT_CODEC must be JSON, not pickle."""
        from integration_coworker.graph.quality_artifacts import QUALITY_ARTIFACT_CODEC
        from integration_coworker.persistence.artifacts.base import ArtifactCodec
        
        assert QUALITY_ARTIFACT_CODEC == ArtifactCodec.JSON
        assert QUALITY_ARTIFACT_CODEC != ArtifactCodec.PICKLE


# =============================================================================
# Test: Bounds Enforcement Regression Tests (PR #8 Hardening)
# =============================================================================

# Hard ceiling for summaries - enforced in tests to prevent regression
SUMMARY_MAX_BYTES = 4096  # 4KB


class TestBoundsEnforcementRegression:
    """
    Regression tests for summary bounds enforcement.
    
    These tests ensure that summary truncation cannot regress,
    keeping checkpoint payloads bounded.
    """
    
    def test_huge_issue_list_truncated_in_summary(self):
        """Summary should truncate large issue lists to MAX_ISSUES_IN_SUMMARY."""
        from integration_coworker.graph.quality_models import (
            StaticIssue,
            MAX_ISSUES_IN_SUMMARY,
        )
        from integration_coworker.graph.static_checks import StaticAnalysisResult
        from integration_coworker.graph.quality_artifacts import build_static_analysis_summary
        
        # Create 500 issues
        issues = [
            StaticIssue(
                severity="warning",
                category="lint",
                file_path=f"file_{i}.py",
                line_number=i,
                column=0,
                message=f"Issue {i}: This is a moderately long warning message",
            )
            for i in range(500)
        ]
        
        result = StaticAnalysisResult(
            passed=True,
            issues=issues,
            blocking_count=0,
            warning_count=500,
        )
        
        summary = build_static_analysis_summary(result)
        
        # Verify truncation
        assert len(summary["issues_preview"]) == MAX_ISSUES_IN_SUMMARY
        assert summary["issues_truncated"] is True
        assert summary["total_issues"] == 500
        
        # Verify size is bounded
        summary_json = json.dumps(summary)
        assert len(summary_json) < SUMMARY_MAX_BYTES, (
            f"Summary {len(summary_json)} bytes exceeds {SUMMARY_MAX_BYTES} ceiling"
        )
    
    def test_huge_error_messages_truncated_in_summary(self):
        """Summary should truncate long error messages."""
        from integration_coworker.graph.quality_models import (
            StaticIssue,
            MAX_ERROR_MESSAGE_LENGTH,
        )
        from integration_coworker.graph.static_checks import StaticAnalysisResult
        from integration_coworker.graph.quality_artifacts import build_static_analysis_summary
        
        # Create issue with 10KB message
        long_message = "x" * 10000
        issues = [
            StaticIssue(
                severity="error",
                category="syntax",
                file_path="test.py",
                line_number=1,
                column=0,
                message=long_message,
            )
        ]
        
        result = StaticAnalysisResult(
            passed=False,
            issues=issues,
            blocking_count=1,
            warning_count=0,
        )
        
        summary = build_static_analysis_summary(result)
        
        # Verify message was truncated
        preview_msg = summary["issues_preview"][0]["message"]
        assert len(preview_msg) <= MAX_ERROR_MESSAGE_LENGTH + 10  # Small buffer for "..."
        
        # Verify size is bounded
        summary_json = json.dumps(summary)
        assert len(summary_json) < SUMMARY_MAX_BYTES
    
    def test_many_files_truncated_in_summary(self):
        """Summary should handle many files gracefully."""
        from integration_coworker.graph.quality_models import (
            StaticIssue,
            MAX_ISSUES_IN_SUMMARY,
        )
        from integration_coworker.graph.static_checks import StaticAnalysisResult
        from integration_coworker.graph.quality_artifacts import build_static_analysis_summary
        
        # Create 1 issue per file, 200 files
        issues = [
            StaticIssue(
                severity="warning",
                category="import",
                file_path=f"path/to/deeply/nested/directory/file_{i}.py",
                line_number=1,
                column=0,
                message=f"Import issue in file {i}",
            )
            for i in range(200)
        ]
        
        result = StaticAnalysisResult(
            passed=True,
            issues=issues,
            blocking_count=0,
            warning_count=200,
        )
        
        summary = build_static_analysis_summary(result)
        
        # Only MAX_ISSUES_IN_SUMMARY files shown
        assert len(summary["issues_preview"]) == MAX_ISSUES_IN_SUMMARY
        assert summary["issues_truncated"] is True
        
        # Verify size is bounded
        summary_json = json.dumps(summary)
        assert len(summary_json) < SUMMARY_MAX_BYTES
    
    def test_empty_result_produces_minimal_summary(self):
        """Empty results should produce minimal summary."""
        from integration_coworker.graph.static_checks import StaticAnalysisResult
        from integration_coworker.graph.quality_artifacts import build_static_analysis_summary
        
        result = StaticAnalysisResult(
            passed=True,
            issues=[],
            blocking_count=0,
            warning_count=0,
        )
        
        summary = build_static_analysis_summary(result)
        summary_json = json.dumps(summary)
        
        # Minimal summary should be very small
        assert len(summary_json) < 500
        assert summary["total_issues"] == 0
        assert summary["issues_preview"] == []
        assert summary["issues_truncated"] is False
    
    def test_validate_quality_refs_size_catches_oversized(self):
        """validate_quality_refs_size should catch oversized refs."""
        from integration_coworker.graph.quality_artifacts import validate_quality_refs_size
        
        # Small refs should pass
        small_refs = {"static": {"summary": {"passed": True}}}
        assert validate_quality_refs_size(small_refs, max_bytes=8192) is True
        
        # Large refs should fail
        large_refs = {"data": "x" * 20000}
        assert validate_quality_refs_size(large_refs, max_bytes=8192) is False
    
    def test_check_no_blobs_catches_violations(self):
        """check_no_blobs_in_quality_refs should detect violations."""
        from integration_coworker.graph.quality_artifacts import check_no_blobs_in_quality_refs
        
        # Clean refs should pass
        clean_refs = {
            "static": {
                "result": {"__artifact_ref__": True, "uri": "file://test"},
                "summary": {"passed": True},
            }
        }
        violations = check_no_blobs_in_quality_refs(clean_refs)
        assert len(violations) == 0
        
        # Refs with embedded blob should fail
        blob_refs = {
            "static": {
                "summary": {
                    "issues": [{"msg": f"issue {i}"} for i in range(200)],
                }
            }
        }
        violations = check_no_blobs_in_quality_refs(blob_refs)
        assert len(violations) > 0
        assert any("issues list" in v for v in violations)


class TestBoundedConstantsAreSensible:
    """Test that bounded constants have reasonable values."""
    
    def test_max_issues_in_summary_is_reasonable(self):
        """MAX_ISSUES_IN_SUMMARY should be reasonable for UI display."""
        from integration_coworker.graph.quality_models import MAX_ISSUES_IN_SUMMARY
        
        assert MAX_ISSUES_IN_SUMMARY >= 5, "Should show at least 5 issues"
        assert MAX_ISSUES_IN_SUMMARY <= 50, "Should not show more than 50 issues"
    
    def test_max_error_message_length_is_reasonable(self):
        """MAX_ERROR_MESSAGE_LENGTH should fit in UI without scrolling."""
        from integration_coworker.graph.quality_models import MAX_ERROR_MESSAGE_LENGTH
        
        assert MAX_ERROR_MESSAGE_LENGTH >= 100, "Should allow at least 100 chars"
        assert MAX_ERROR_MESSAGE_LENGTH <= 2000, "Should not exceed 2000 chars"
    
    def test_max_file_path_length_is_reasonable(self):
        """MAX_FILE_PATH_LENGTH should handle nested paths."""
        from integration_coworker.graph.quality_models import MAX_FILE_PATH_LENGTH
        
        assert MAX_FILE_PATH_LENGTH >= 100, "Should handle moderately nested paths"
        assert MAX_FILE_PATH_LENGTH <= 500, "Should not be excessive"

