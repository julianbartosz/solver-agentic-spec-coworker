"""
Tests for HITL (Human-in-the-Loop) gate node.

Tests interrupt/resume behavior without a full graph run.

NOTE: The original internal helpers (_build_interrupt_payload, _compute_file_summary,
_parse_approval_decision) were refactored into review_gate.py. Tests now focus on
the public API (hitl_review_gate, check_review_approved) rather than internal helpers.
"""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.nodes.hitl_gate import (
    hitl_review_gate,
    check_hitl_approval,
    check_review_approved,
)
# Import internal helpers from review_gate for testing
from integration_coworker.graph.nodes.review_gate import (
    _build_review_payload,
    _parse_decision,
    _has_reviewable_content,
)
from integration_coworker.domain.models import CodeArtifact
from integration_coworker.repo.models import RepoChangeSet, FileChange
from integration_coworker.api.types import IntegrationOptions


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_state() -> WorkflowState:
    """Create a minimal WorkflowState for testing."""
    state = WorkflowState(
        source_refs=[],  # Required field
        spec_refs=[],    # Required field
        run_id="test-run-123",
        provider_code="test_api",
        task_description="Implement payment processing",
        completed_steps=[],
        warnings=[],
        errors=[],
        plan={},
    )
    # Set options with hitl_mode="auto" (default, respects API context)
    state.options = IntegrationOptions(hitl_mode="auto", dry_run=False)
    return state


@pytest.fixture
def state_with_code_artifacts(mock_state: WorkflowState) -> WorkflowState:
    """State with code artifacts to write."""
    # Use hitl_mode="always" to ensure HITL gate is NOT skipped (tests interrupt flow)
    mock_state.options = IntegrationOptions(hitl_mode="always", dry_run=False)
    mock_state.code_artifacts = [
        CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="client_code",
            language="python",
            module_name="api_client",
            rel_path="src/api_client.py",
            content="# Generated client code\nclass APIClient:\n    pass",
        ),
        CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="test_code",
            language="python",
            module_name="test_api_client",
            rel_path="tests/test_api_client.py",
            content="# Generated tests\nimport pytest\n",
        ),
    ]
    return mock_state


@pytest.fixture
def state_with_repo_changes(mock_state: WorkflowState) -> WorkflowState:
    """State with repo changes to apply."""
    from pathlib import Path
    mock_state.repo_changes = RepoChangeSet(
        repo_root=Path("/tmp/test_repo"),
        changes=[
            FileChange(
                rel_path="src/main.py",
                change_type="update",
                original_content="# existing",
                content="# existing\nfrom api_client import APIClient",
                before="# existing",
                after="# existing\nfrom api_client import APIClient",
            ),
            FileChange(
                rel_path="src/new_file.py",
                change_type="create",
                content="# new content",
                after="# new content",
            ),
        ]
    )
    return mock_state


# =============================================================================
# Test Reviewable Content Detection
# =============================================================================

class TestHasReviewableContent:
    """Tests for detecting reviewable content."""

    def test_empty_state_has_no_content(self, mock_state: WorkflowState):
        """Empty state should have no reviewable content."""
        assert _has_reviewable_content(mock_state, "code") is False
        assert _has_reviewable_content(mock_state, "sandbox") is False

    def test_code_artifacts_are_reviewable(self, state_with_code_artifacts: WorkflowState):
        """Code artifacts should be detected as reviewable."""
        assert _has_reviewable_content(state_with_code_artifacts, "code") is True

    def test_unknown_kind_returns_false(self, mock_state: WorkflowState):
        """Unknown review kind should return False."""
        assert _has_reviewable_content(mock_state, "unknown") is False


# =============================================================================
# Test Review Payload Building
# =============================================================================

class TestBuildReviewPayload:
    """Tests for review payload construction."""

    def test_payload_contains_required_fields(self, state_with_code_artifacts: WorkflowState):
        """Payload should contain all required fields."""
        payload = _build_review_payload(state_with_code_artifacts, "code", {})
        
        assert payload["gate"] == "code_review"
        assert payload["kind"] == "code"
        assert payload["run_id"] == "test-run-123"
        assert payload["provider_code"] == "test_api"
        assert "summary" in payload
        assert "requested_at" in payload

    def test_payload_summary_has_file_count(self, state_with_code_artifacts: WorkflowState):
        """Payload summary should include file count."""
        payload = _build_review_payload(state_with_code_artifacts, "code", {})
        
        assert payload["summary"]["file_count"] == 2


# =============================================================================
# Test Decision Parsing
# =============================================================================

class TestParseDecision:
    """Tests for decision parsing (refactored from _parse_approval_decision)."""

    def test_bool_true_approved(self):
        """Boolean True should be parsed as approved."""
        result = _parse_decision(True)
        
        assert result["approved"] is True
        assert result["feedback"] is None
        assert result["overrides"] == {}

    def test_bool_false_rejected(self):
        """Boolean False should be parsed as rejected."""
        result = _parse_decision(False)
        
        assert result["approved"] is False

    def test_dict_with_approved_true(self):
        """Dict with approved=True should be parsed correctly."""
        result = _parse_decision({
            "approved": True,
            "feedback": "LGTM",
        })
        
        assert result["approved"] is True
        assert result["feedback"] == "LGTM"

    def test_dict_with_comment_field(self):
        """Dict with comment field should be normalized to feedback."""
        result = _parse_decision({
            "approved": True,
            "comment": "LGTM",
        })
        
        assert result["approved"] is True
        assert result["feedback"] == "LGTM"

    def test_dict_with_overrides(self):
        """Dict with overrides should preserve them."""
        result = _parse_decision({
            "approved": True,
            "overrides": {"exclude_files": ["src/skip.py"]},
        })
        
        assert result["overrides"] == {"exclude_files": ["src/skip.py"]}

    def test_action_format_approve(self):
        """Action-based format with approve should work."""
        result = _parse_decision({
            "action": "approve",
            "feedback": "Looks good",
        })
        
        assert result["approved"] is True
        assert result["feedback"] == "Looks good"
        assert result["action"] == "approve"

    def test_action_format_reject(self):
        """Action-based format with reject should work."""
        result = _parse_decision({
            "action": "reject",
            "feedback": "Needs work",
        })
        
        assert result["approved"] is False
        assert result["feedback"] == "Needs work"

    def test_unknown_format_treated_as_rejection(self):
        """Unknown formats should be treated as rejection."""
        result = _parse_decision("yes")
        
        assert result["approved"] is False


# =============================================================================
# Test check_hitl_approval (legacy compatibility)
# =============================================================================

class TestCheckHitlApproval:
    """Tests for HITL approval check (deprecated, wraps check_review_approved)."""

    def test_returns_true_when_no_rejection(self, mock_state: WorkflowState):
        """Should return True when not rejected."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert check_hitl_approval(mock_state) is True

    def test_returns_false_when_rejected(self, mock_state: WorkflowState):
        """Should return False when code_rejected is True (new flag name)."""
        mock_state.plan["code_rejected"] = True
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert check_hitl_approval(mock_state) is False

    def test_check_review_approved_works_directly(self, mock_state: WorkflowState):
        """check_review_approved should work without deprecation warning."""
        # Not rejected - should return True
        assert check_review_approved(mock_state, "code") is True
        
        # With rejection
        mock_state.plan["code_rejected"] = True
        assert check_review_approved(mock_state, "code") is False


# =============================================================================
# Test hitl_review_gate (with mocked interrupt)
# =============================================================================

class TestHitlReviewGate:
    """Tests for the HITL gate node function."""

    def test_skips_in_dry_run_mode(self, state_with_code_artifacts: WorkflowState):
        """Should skip approval in dry_run mode."""
        state_with_code_artifacts.options = IntegrationOptions(dry_run=True)
        
        result = hitl_review_gate(state_with_code_artifacts)
        
        # Node completes as code_review_gate (delegated)
        assert "code_review_gate" in result.completed_steps
        # Should not have called interrupt

    def test_skips_when_hitl_mode_never(self, state_with_code_artifacts: WorkflowState):
        """Should skip approval when hitl_mode='never' (CI/test automation)."""
        state_with_code_artifacts.options = IntegrationOptions(hitl_mode="never")
        
        result = hitl_review_gate(state_with_code_artifacts)
        
        assert "code_review_gate" in result.completed_steps
        # Should not have called interrupt (no mock needed since it returns early)
        # Artifacts should be preserved (not rejected)
        assert result.plan.get("code_rejected") is not True
        assert len(result.code_artifacts) > 0

    def test_skips_when_skip_hitl_enabled_legacy(self, state_with_code_artifacts: WorkflowState):
        """Should skip approval when skip_hitl=True (legacy backwards compat)."""
        state_with_code_artifacts.options = IntegrationOptions(skip_hitl=True)
        
        result = hitl_review_gate(state_with_code_artifacts)
        
        assert "code_review_gate" in result.completed_steps
        # Should not have called interrupt (no mock needed since it returns early)
        # Artifacts should be preserved (not rejected)
        assert result.plan.get("code_rejected") is not True
        assert len(result.code_artifacts) > 0

    def test_skips_when_no_changes(self, mock_state: WorkflowState):
        """Should skip approval when no changes to write."""
        result = hitl_review_gate(mock_state)
        
        assert "code_review_gate" in result.completed_steps

    @patch("integration_coworker.graph.nodes.review_gate.interrupt")
    def test_interrupts_with_payload(
        self,
        mock_interrupt: MagicMock,
        state_with_code_artifacts: WorkflowState,
    ):
        """Should call interrupt with correct payload."""
        # Simulate approval on resume
        mock_interrupt.return_value = True
        
        result = hitl_review_gate(state_with_code_artifacts)
        
        # Verify interrupt was called
        mock_interrupt.assert_called_once()
        
        # Verify payload structure (new format uses code_review gate)
        payload = mock_interrupt.call_args[0][0]
        assert payload["gate"] == "code_review"
        assert payload["run_id"] == "test-run-123"
        
        # Should complete without rejection
        assert "code_review_gate" in result.completed_steps
        assert result.plan.get("code_rejected") is not True

    @patch("integration_coworker.graph.nodes.review_gate.interrupt")
    def test_approved_returns_unchanged_state(
        self,
        mock_interrupt: MagicMock,
        state_with_code_artifacts: WorkflowState,
    ):
        """Approved decision should return state unchanged."""
        mock_interrupt.return_value = {"approved": True, "feedback": "LGTM"}
        
        result = hitl_review_gate(state_with_code_artifacts)
        
        # State should be unchanged (except completed_steps)
        assert result.code_artifacts == state_with_code_artifacts.code_artifacts
        assert result.plan.get("code_rejected") is not True

    @patch("integration_coworker.graph.nodes.review_gate.interrupt")
    def test_rejected_sets_skip_flags(
        self,
        mock_interrupt: MagicMock,
        state_with_code_artifacts: WorkflowState,
    ):
        """Rejected decision should set skip flags."""
        mock_interrupt.return_value = {"approved": False, "feedback": "Not ready"}
        
        result = hitl_review_gate(state_with_code_artifacts)
        
        # Uses new flag names
        assert result.plan.get("code_rejected") is True
        assert result.plan.get("code_rejection_feedback") == "Not ready"
        assert len(result.warnings) > 0

    @patch("integration_coworker.graph.nodes.review_gate.interrupt")
    def test_overrides_exclude_files(
        self,
        mock_interrupt: MagicMock,
        state_with_code_artifacts: WorkflowState,
    ):
        """Approval with exclude_files override should filter artifacts."""
        mock_interrupt.return_value = {
            "approved": True,
            "overrides": {"exclude_files": ["src/api_client.py"]},
        }
        
        result = hitl_review_gate(state_with_code_artifacts)
        
        # Should have filtered out the excluded file
        remaining_paths = [a.rel_path for a in result.code_artifacts]
        assert "src/api_client.py" not in remaining_paths
        assert "tests/test_api_client.py" in remaining_paths


# =============================================================================
# Test graph wiring (smoke test)
# =============================================================================

class TestGraphWiring:
    """Smoke tests for HITL gate in the graph."""

    def test_code_review_gate_in_graph_nodes(self):
        """Code review gate should be present in built graph."""
        from integration_coworker.graph.runtime import build_graph
        from integration_coworker.graph.node_names import CODE_REVIEW_GATE
        
        graph = build_graph()
        nodes = list(graph.get_graph().nodes.keys())
        
        # The built graph uses code_review_gate (from review_gate("code"))
        assert CODE_REVIEW_GATE in nodes

    def test_code_review_gate_in_workflow_order(self):
        """code_review_gate should be between analyze and apply in WORKFLOW_NODE_ORDER.
        
        The canonical name is 'code_review_gate' in all constants.
        """
        from integration_coworker.graph.node_names import (
            WORKFLOW_NODE_ORDER,
            CODE_REVIEW_GATE,
            ANALYZE_REPO_LAYOUT,
            APPLY_REPO_INTEGRATION_CHANGES,
        )
        
        order = list(WORKFLOW_NODE_ORDER)
        gate_idx = order.index(CODE_REVIEW_GATE)
        analyze_idx = order.index(ANALYZE_REPO_LAYOUT)
        apply_idx = order.index(APPLY_REPO_INTEGRATION_CHANGES)
        
        assert analyze_idx < gate_idx < apply_idx

    def test_code_review_gate_in_dependencies(self):
        """code_review_gate should be in NODE_DEPENDENCIES.
        
        The canonical name is 'code_review_gate' in all constants.
        """
        from integration_coworker.graph.node_names import (
            NODE_DEPENDENCIES,
            CODE_REVIEW_GATE,
            ANALYZE_REPO_LAYOUT,
            APPLY_REPO_INTEGRATION_CHANGES,
        )
        
        assert CODE_REVIEW_GATE in NODE_DEPENDENCIES
        assert ANALYZE_REPO_LAYOUT in NODE_DEPENDENCIES[CODE_REVIEW_GATE]
        assert CODE_REVIEW_GATE in NODE_DEPENDENCIES[APPLY_REPO_INTEGRATION_CHANGES]


# =============================================================================
# Test resume functions
# =============================================================================

class TestResumeFunctions:
    """Tests for HITL resume API."""

    def test_resume_with_approval_import(self):
        """Resume function should be importable."""
        from integration_coworker.graph.runtime import (
            resume_with_approval,
            get_interrupt_payload,
            is_workflow_paused,
        )
        
        assert callable(resume_with_approval)
        assert callable(get_interrupt_payload)
        assert callable(is_workflow_paused)

    def test_is_workflow_paused_nonexistent_thread(self):
        """Should return False for nonexistent thread."""
        from integration_coworker.graph.runtime import is_workflow_paused
        
        # This should return False, not raise
        result = is_workflow_paused("nonexistent-thread-id-12345")
        assert result is False

    @pytest.mark.asyncio
    async def test_get_interrupt_payload_nonexistent_thread(self):
        """Should return None for nonexistent thread."""
        from integration_coworker.graph.runtime import get_interrupt_payload
        
        result = await get_interrupt_payload("nonexistent-thread-id-12345")
        assert result is None
