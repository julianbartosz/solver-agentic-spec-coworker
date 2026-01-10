"""
Tests for the Streamlit review dialog component.

Tests UI rendering logic and decision building without requiring Streamlit runtime.
"""
import pytest
from datetime import datetime
from typing import Any, Dict
from unittest.mock import MagicMock, patch

# Skip all tests if streamlit not installed
pytest.importorskip("streamlit")


# =============================================================================
# Test fixtures
# =============================================================================


@pytest.fixture
def sample_code_artifact_refs() -> Dict[str, Any]:
    """Sample code review artifact refs."""
    return {
        "code_snapshot": {
            "run_id": "test-run-123",
            "artifact_name": "review_pre_write_snapshot",
            "sha256": "abc123",
            "size": 1024,
            "codec": "json",
        },
        "diff_patches": {
            "run_id": "test-run-123",
            "artifact_name": "review_pre_write_diff_patches",
            "sha256": "def456",
            "size": 512,
            "codec": "json",
        },
    }


@pytest.fixture
def sample_sandbox_artifact_refs() -> Dict[str, Any]:
    """Sample sandbox review artifact refs."""
    return {
        "sandbox_result": {
            "run_id": "test-run-123",
            "artifact_name": "review_post_sandbox_sandbox",
            "sha256": "ghi789",
            "size": 2048,
            "codec": "json",
        },
    }


@pytest.fixture
def sample_code_snapshot() -> list:
    """Sample code snapshot data."""
    return [
        {"path": "src/client.py", "size": 1500, "sha256": "abc123", "type": "code"},
        {"path": "tests/test_client.py", "size": 800, "sha256": "def456", "type": "test"},
    ]


@pytest.fixture
def sample_sandbox_result() -> Dict[str, Any]:
    """Sample sandbox result data."""
    return {
        "passed": False,
        "summary": "Quality checks failed (mypy errors)",
        "gates": [
            {"name": "bandit", "passed": True},
            {"name": "mypy", "passed": False, "error": "Type error in client.py:42"},
            {"name": "pytest", "passed": True},
        ],
        "errors": ["src/client.py:42: error: Incompatible types"],
        "warnings": ["Consider adding type hints to helper functions"],
    }


# =============================================================================
# Test check_pending_review
# =============================================================================


class TestCheckPendingReview:
    """Test the check_pending_review function."""
    
    def test_returns_none_when_no_result(self):
        """Returns None when session state has no result."""
        from integration_coworker.ui.review_dialog import check_pending_review
        
        mock_state = MagicMock()
        mock_state.last_result_dict = None
        
        result = check_pending_review(mock_state)
        assert result is None
    
    def test_returns_none_when_no_pending_kind(self):
        """Returns None when result has no pending_review_kind."""
        from integration_coworker.ui.review_dialog import check_pending_review
        
        mock_state = MagicMock()
        mock_state.last_result_dict = {
            "run_id": "test-123",
            "status": "completed",
        }
        
        result = check_pending_review(mock_state)
        assert result is None
    
    def test_returns_review_info_when_pending(self, sample_code_artifact_refs):
        """Returns review info when pending_review_kind is set."""
        from integration_coworker.ui.review_dialog import check_pending_review
        
        mock_state = MagicMock()
        mock_state.last_result_dict = {
            "run_id": "test-123",
            "pending_review_kind": "code",
            "review_artifact_refs": {
                "code": sample_code_artifact_refs,
            },
        }
        
        result = check_pending_review(mock_state)
        assert result is not None
        assert result["run_id"] == "test-123"
        assert result["kind"] == "code"
        assert result["artifact_refs"] == sample_code_artifact_refs


# =============================================================================
# Test decision building
# =============================================================================


class TestBuildDecision:
    """Test decision building logic."""
    
    def test_build_approval_decision(self):
        """Build a simple approval decision."""
        from integration_coworker.ui.review_dialog import _build_decision
        
        decision = _build_decision(approved=True)
        
        assert decision["approved"] is True
        assert "decided_at" in decision
        assert "feedback" not in decision
    
    def test_build_rejection_with_feedback(self):
        """Build rejection with feedback."""
        from integration_coworker.ui.review_dialog import _build_decision
        
        decision = _build_decision(
            approved=False,
            feedback="Needs more error handling",
        )
        
        assert decision["approved"] is False
        assert decision["feedback"] == "Needs more error handling"
    
    def test_build_approval_with_exclusions(self):
        """Build approval with file exclusions."""
        from integration_coworker.ui.review_dialog import _build_decision
        
        decision = _build_decision(
            approved=True,
            exclude_files=["src/temp.py", "tests/scratch.py"],
        )
        
        assert decision["approved"] is True
        assert decision["overrides"]["exclude_files"] == ["src/temp.py", "tests/scratch.py"]
    
    def test_build_rejection_with_regenerate(self):
        """Build rejection with regeneration request."""
        from integration_coworker.ui.review_dialog import _build_decision
        
        decision = _build_decision(
            approved=False,
            feedback="Use async pattern instead",
            regenerate=True,
        )
        
        assert decision["approved"] is False
        assert decision["regenerate"] is True
        assert decision["feedback"] == "Use async pattern instead"
    
    def test_decision_has_timestamp(self):
        """Decision includes decided_at timestamp."""
        from integration_coworker.ui.review_dialog import _build_decision
        from datetime import datetime, timezone
        
        before = datetime.now(timezone.utc).isoformat()
        decision = _build_decision(approved=True)
        after = datetime.now(timezone.utc).isoformat()
        
        assert "decided_at" in decision
        # Timestamp should be between before and after
        assert before <= decision["decided_at"] <= after


# =============================================================================
# Test resume_with_decision
# =============================================================================


class TestResumeWithDecision:
    """Test the resume_with_decision wrapper."""
    
    def test_successful_resume(self):
        """Test successful resume returns success."""
        from integration_coworker.ui.review_dialog import resume_with_decision
        
        mock_result = MagicMock()
        mock_result.completed_steps = ["code_review_gate", "persist_gold_checkpoint"]
        mock_result.errors = []
        mock_result.warnings = []
        
        # Patch at the source module since it's lazily imported
        with patch("integration_coworker.graph.runtime.resume_with_approval", return_value=mock_result):
            result = resume_with_decision("test-123", {"approved": True})
        
        assert result["success"] is True
        assert result["run_id"] == "test-123"
        assert "code_review_gate" in result["completed_steps"]
    
    def test_resume_value_error(self):
        """Test resume handles ValueError."""
        from integration_coworker.ui.review_dialog import resume_with_decision
        
        # Patch at the source module since it's lazily imported
        with patch("integration_coworker.graph.runtime.resume_with_approval", side_effect=ValueError("Not paused")):
            result = resume_with_decision("test-123", {"approved": True})
        
        assert result["success"] is False
        assert "Invalid state" in result["error"]
    
    def test_resume_generic_error(self):
        """Test resume handles generic exceptions."""
        from integration_coworker.ui.review_dialog import resume_with_decision
        
        # Patch at the source module since it's lazily imported
        with patch("integration_coworker.graph.runtime.resume_with_approval", side_effect=RuntimeError("Network error")):
            result = resume_with_decision("test-123", {"approved": True})
        
        assert result["success"] is False
        assert "Network error" in result["error"]


# =============================================================================
# Test utility functions
# =============================================================================


class TestUtilities:
    """Test utility functions."""
    
    def test_format_bytes_bytes(self):
        """Format small sizes in bytes."""
        from integration_coworker.ui.review_dialog import _format_bytes
        
        assert _format_bytes(100) == "100.0 B"
        assert _format_bytes(0) == "0.0 B"
    
    def test_format_bytes_kilobytes(self):
        """Format KB sizes."""
        from integration_coworker.ui.review_dialog import _format_bytes
        
        assert _format_bytes(1024) == "1.0 KB"
        assert _format_bytes(2048) == "2.0 KB"
    
    def test_format_bytes_megabytes(self):
        """Format MB sizes."""
        from integration_coworker.ui.review_dialog import _format_bytes
        
        assert _format_bytes(1024 * 1024) == "1.0 MB"
        assert _format_bytes(5 * 1024 * 1024) == "5.0 MB"


# =============================================================================
# Test artifact loading
# =============================================================================


class TestArtifactLoading:
    """Test artifact loading utilities."""
    
    def test_load_artifact_safe_returns_none_on_missing_ref(self):
        """Returns None when ref is None."""
        from integration_coworker.ui.review_dialog import _load_artifact_safe
        
        result = _load_artifact_safe(None)
        assert result is None
    
    def test_load_artifact_safe_returns_none_on_error(self):
        """Returns None when loading fails."""
        from integration_coworker.ui.review_dialog import _load_artifact_safe
        
        # Patch at the source module since it's lazily imported
        with patch("integration_coworker.graph.review_artifacts.load_review_artifact_from_dict", side_effect=RuntimeError("Not found")):
            result = _load_artifact_safe({"run_id": "test", "artifact_name": "missing"})
        
        assert result is None
    
    def test_load_artifact_safe_returns_data_on_success(self, sample_code_snapshot):
        """Returns artifact data on success."""
        from integration_coworker.ui.review_dialog import _load_artifact_safe
        
        # Patch at the source module since it's lazily imported
        with patch("integration_coworker.graph.review_artifacts.load_review_artifact_from_dict", return_value=sample_code_snapshot):
            result = _load_artifact_safe({"run_id": "test", "artifact_name": "snapshot"})
        
        assert result == sample_code_snapshot


# =============================================================================
# Test REVIEW_KIND_LABELS
# =============================================================================


class TestConstants:
    """Test module constants."""
    
    def test_review_kind_labels(self):
        """Review kinds have display labels."""
        from integration_coworker.ui.review_dialog import REVIEW_KIND_LABELS
        
        assert "code" in REVIEW_KIND_LABELS
        assert "sandbox" in REVIEW_KIND_LABELS
        assert REVIEW_KIND_LABELS["code"] == "Code Review"
    
    def test_status_icons(self):
        """Status icons are defined."""
        from integration_coworker.ui.review_dialog import STATUS_ICONS
        
        assert "passed" in STATUS_ICONS
        assert "failed" in STATUS_ICONS
        assert STATUS_ICONS["passed"] == "✅"
