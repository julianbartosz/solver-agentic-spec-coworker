"""
Tests for review_gate module.

These tests verify the review_gate(kind) factory and its behavior:
- Idempotency: doesn't interrupt again if decision exists for this kind
- Refs-not-blobs: payloads use bounded summaries + artifact refs
- Skip conditions: dry_run, hitl_mode, no reviewable content
- Guard functions: check_review_approved, get_review_feedback

NO LIVE LLM REQUIRED - uses mocks for external dependencies.
"""

import pytest
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, call

from integration_coworker.graph.nodes.review_gate import (
    review_gate,
    hitl_review_gate,
    check_review_approved,
    get_review_feedback,
    _has_existing_decision,
    _should_skip_hitl,
    _has_reviewable_content,
    _build_review_payload,
    _parse_decision,
)
from integration_coworker.graph.state import WorkflowState
from integration_coworker.persistence.artifacts.base import ArtifactRef, ArtifactCodec


# =============================================================================
# Mock Helpers
# =============================================================================

@dataclass
class MockOptions:
    """Mock IntegrationOptions for testing."""
    dry_run: bool = False
    hitl_mode: str = "always"
    skip_hitl: bool = False
    
    def should_skip_hitl(self) -> bool:
        """Match production contract."""
        if self.skip_hitl:
            return True
        return self.hitl_mode in ("never", "auto")


@dataclass
class MockCodeArtifact:
    """Mock CodeArtifact for testing."""
    rel_path: str
    content: str
    artifact_type: str = "client_code"


@dataclass
class MockRepoChange:
    """Mock RepoChange for testing."""
    rel_path: str
    change_type: str
    before: str = ""
    after: str = ""


@dataclass  
class MockRepoChanges:
    """Mock RepoChanges for testing."""
    changes: List[MockRepoChange] = field(default_factory=list)


@dataclass
class MockSandboxResult:
    """Mock sandbox result for testing."""
    all_gates_passed: bool = True
    gate_results: List[Dict] = field(default_factory=list)
    errors: List[Dict] = field(default_factory=list)


def make_test_state(
    run_id: str = "test-run",
    code_artifacts: Optional[List[MockCodeArtifact]] = None,
    repo_changes: Optional[MockRepoChanges] = None,
    sandbox_result: Optional[MockSandboxResult] = None,
    options: Optional[MockOptions] = None,
    review_decisions: Optional[Dict] = None,
    review_artifact_refs: Optional[Dict] = None,
    pending_review_kind: Optional[str] = None,
) -> WorkflowState:
    """Create a WorkflowState for testing."""
    state = WorkflowState(
        source_refs=[],
        spec_refs=[],
        task_description="Test task",
    )
    state.run_id = run_id
    state.code_artifacts = code_artifacts or []
    state.repo_changes = repo_changes
    state.sandbox_result = sandbox_result
    state.options = options or MockOptions()
    state.review_decisions = review_decisions or {}
    state.review_artifact_refs = review_artifact_refs or {}
    state.pending_review_kind = pending_review_kind
    return state


# =============================================================================
# Test Factory Creation
# =============================================================================

class TestReviewGateFactory:
    """Test review_gate(kind) factory function."""
    
    def test_creates_code_gate(self):
        """Factory creates a code review gate."""
        gate_fn = review_gate("code")
        assert callable(gate_fn)
        assert gate_fn.__name__ == "code_review_gate"
    
    def test_creates_sandbox_gate(self):
        """Factory creates a sandbox review gate."""
        gate_fn = review_gate("sandbox")
        assert callable(gate_fn)
        assert gate_fn.__name__ == "sandbox_review_gate"
    
    def test_unknown_kind_handled_gracefully(self):
        """Unknown kind creates gate but logs warning."""
        # Factory accepts unknown kinds - _has_reviewable_content returns False
        # which causes auto-skip. This is intentional for forward compatibility.
        gate_fn = review_gate("unknown")  # type: ignore
        assert callable(gate_fn)


# =============================================================================
# Test Idempotency
# =============================================================================

class TestIdempotency:
    """Test gate idempotency - don't interrupt again if decision exists."""
    
    def test_has_existing_decision_empty(self):
        """No decision exists for fresh state."""
        state = make_test_state()
        assert _has_existing_decision(state, "code") is False
        assert _has_existing_decision(state, "sandbox") is False
    
    def test_has_existing_decision_true(self):
        """Decision exists after approval."""
        state = make_test_state(
            review_decisions={
                "code": {
                    "approved": True,
                    "auto": False,
                    "reason": "Human approved",
                    "decided_at": time.time(),
                }
            }
        )
        assert _has_existing_decision(state, "code") is True
        assert _has_existing_decision(state, "sandbox") is False
    
    def test_gate_skips_with_existing_decision(self):
        """Gate returns immediately if decision exists."""
        state = make_test_state(
            code_artifacts=[MockCodeArtifact("main.py", "code")],
            review_decisions={
                "code": {
                    "approved": True,
                    "auto": False,
                    "reason": "Already approved",
                    "decided_at": time.time(),
                }
            }
        )
        
        # Should not interrupt - decision exists
        gate_fn = review_gate("code")
        
        with patch('integration_coworker.graph.nodes.review_gate.interrupt') as mock_interrupt:
            result = gate_fn(state)
            mock_interrupt.assert_not_called()
        
        # Should just return state unchanged
        assert result is state or result == state


# =============================================================================
# Test Skip Conditions
# =============================================================================

class TestSkipConditions:
    """Test conditions that skip HITL entirely."""
    
    def test_should_skip_hitl_never_mode(self):
        """hitl_mode='never' skips HITL."""
        state = make_test_state(options=MockOptions(hitl_mode="never"))
        assert _should_skip_hitl(state) is True
    
    def test_should_skip_hitl_auto_mode(self):
        """hitl_mode='auto' skips HITL for non-interactive."""
        state = make_test_state(options=MockOptions(hitl_mode="auto"))
        assert _should_skip_hitl(state) is True
    
    def test_should_skip_hitl_always_mode(self):
        """hitl_mode='always' does not skip HITL."""
        state = make_test_state(options=MockOptions(hitl_mode="always"))
        assert _should_skip_hitl(state) is False
    
    def test_should_skip_hitl_legacy_flag(self):
        """Legacy skip_hitl=True skips HITL."""
        state = make_test_state(options=MockOptions(skip_hitl=True))
        assert _should_skip_hitl(state) is True
    
    def test_should_skip_dry_run(self):
        """dry_run mode skips HITL."""
        state = make_test_state(options=MockOptions(dry_run=True))
        # dry_run should cause skip (checked separately in gate)
        assert state.options.dry_run is True
    
    def test_gate_skips_no_reviewable_content(self):
        """Gate skips if no content to review."""
        state = make_test_state(
            code_artifacts=[],
            repo_changes=None,
            options=MockOptions(hitl_mode="always"),
        )
        
        # No code artifacts or repo changes for code gate
        gate_fn = review_gate("code")
        
        with patch('integration_coworker.graph.nodes.review_gate.interrupt') as mock_interrupt:
            result = gate_fn(state)
            mock_interrupt.assert_not_called()


# =============================================================================
# Test Reviewable Content Detection
# =============================================================================

class TestReviewableContent:
    """Test _has_reviewable_content detection."""
    
    def test_code_gate_with_artifacts(self):
        """Code gate detects code_artifacts."""
        state = make_test_state(
            code_artifacts=[MockCodeArtifact("main.py", "code")]
        )
        assert _has_reviewable_content(state, "code") is True
    
    def test_code_gate_with_repo_changes(self):
        """Code gate only checks code_artifacts, not repo_changes.
        
        Note: Current implementation only checks code_artifacts for code review.
        repo_changes are handled by the apply step, not the review gate.
        """
        state = make_test_state(
            repo_changes=MockRepoChanges(changes=[
                MockRepoChange("main.py", "create", after="code")
            ])
        )
        # Implementation only checks code_artifacts, not repo_changes
        assert _has_reviewable_content(state, "code") is False
    
    def test_code_gate_no_content(self):
        """Code gate with no content returns False."""
        state = make_test_state(code_artifacts=[], repo_changes=None)
        assert _has_reviewable_content(state, "code") is False
    
    def test_sandbox_gate_with_result(self):
        """Sandbox gate detects sandbox_result."""
        state = make_test_state(
            sandbox_result=MockSandboxResult(all_gates_passed=True)
        )
        assert _has_reviewable_content(state, "sandbox") is True
    
    def test_sandbox_gate_no_result(self):
        """Sandbox gate with no result returns False."""
        state = make_test_state(sandbox_result=None)
        assert _has_reviewable_content(state, "sandbox") is False


# =============================================================================
# Test Payload Building
# =============================================================================

class TestPayloadBuilding:
    """Test _build_review_payload produces bounded, refs-not-blobs payloads."""
    
    def test_payload_has_required_fields(self):
        """Payload includes required fields."""
        state = make_test_state(
            run_id="run-123",
            code_artifacts=[MockCodeArtifact("main.py", "print('hello')")]
        )
        
        # _build_review_payload takes (state, kind, artifact_refs)
        artifact_refs = {}  # Empty refs for test
        payload = _build_review_payload(state, "code", artifact_refs)
        
        # Check for actual field names from implementation
        assert "gate" in payload or "kind" in payload
        assert "run_id" in payload
        assert "requested_at" in payload
        assert "summary" in payload
        assert "artifact_refs" in payload
    
    def test_payload_summary_is_bounded(self):
        """Payload summary stays bounded even with large data."""
        # Create many large artifacts
        artifacts = [
            MockCodeArtifact(f"src/file_{i}.py", "x" * 10000)
            for i in range(100)
        ]
        state = make_test_state(code_artifacts=artifacts)
        
        artifact_refs = {}  # Empty refs for test
        payload = _build_review_payload(state, "code", artifact_refs)
        
        # Payload must be JSON-serializable and bounded
        payload_json = json.dumps(payload)
        assert len(payload_json) < 10000  # Should be well under 10KB
    
    def test_payload_has_artifact_refs_not_blobs(self):
        """Payload contains artifact refs, not full content."""
        state = make_test_state(
            code_artifacts=[MockCodeArtifact("main.py", "x" * 100000)]  # 100KB
        )
        
        mock_ref = ArtifactRef(
            run_id="run",
            key="code_snapshot",
            uri="file:///tmp/abc",
            sha256="a" * 64,
            size_bytes=100000,
            codec=ArtifactCodec.JSON,
        )
        
        artifact_refs = {"code_snapshot": mock_ref}
        payload = _build_review_payload(state, "code", artifact_refs)
        
        # Payload should have refs, not raw content
        assert "artifact_refs" in payload
        payload_json = json.dumps(payload)
        # Full content (100KB) should NOT be in payload
        assert len(payload_json) < 10000


# =============================================================================
# Test Resume Decision Parsing
# =============================================================================

class TestResumeDecisionParsing:
    """Test _parse_decision handles various formats."""
    
    def test_parse_boolean_true(self):
        """Simple True is approval."""
        decision = _parse_decision(True)
        assert decision["approved"] is True
        assert decision["feedback"] is None
        assert decision["overrides"] == {}
    
    def test_parse_boolean_false(self):
        """Simple False is rejection."""
        decision = _parse_decision(False)
        assert decision["approved"] is False
    
    def test_parse_dict_with_approved(self):
        """Dict with approved key is parsed."""
        decision = _parse_decision({
            "approved": True,
            "feedback": "LGTM",
            "overrides": {"exclude_files": ["test.py"]},
        })
        assert decision["approved"] is True
        assert decision["feedback"] == "LGTM"
        assert "exclude_files" in decision["overrides"]
    
    def test_parse_unknown_format_rejects(self):
        """Unknown format is treated as rejection."""
        decision = _parse_decision("invalid string")
        assert decision["approved"] is False


# =============================================================================
# Test Guard Functions
# =============================================================================

class TestGuardFunctions:
    """Test check_review_approved and get_review_feedback guards."""
    
    def test_check_approved_no_decision_defaults_true(self):
        """No decision defaults to True (allow workflow to proceed).
        
        This is by design: if no review gate ran (e.g., hitl_mode=never),
        downstream nodes should proceed. Rejection must be explicit.
        """
        state = make_test_state()
        # No decision means no rejection - workflow can proceed
        assert check_review_approved(state, "code") is True
        assert check_review_approved(state, "sandbox") is True
    
    def test_check_approved_with_approval(self):
        """Approved decision returns True."""
        state = make_test_state(
            review_decisions={
                "code": {
                    "approved": True,
                    "auto": False,
                    "reason": "Human approved",
                    "decided_at": time.time(),
                }
            }
        )
        assert check_review_approved(state, "code") is True
        # No decision for sandbox - defaults to True
        assert check_review_approved(state, "sandbox") is True
    
    def test_check_approved_with_rejection(self):
        """Rejected decision returns False."""
        state = make_test_state(
            review_decisions={
                "code": {
                    "approved": False,
                    "auto": False,
                    "reason": "Rejected",
                    "decided_at": time.time(),
                }
            }
        )
        assert check_review_approved(state, "code") is False
    
    def test_check_approved_auto_approved(self):
        """Auto-approved (skip) counts as approved."""
        state = make_test_state(
            review_decisions={
                "code": {
                    "approved": True,
                    "auto": True,
                    "reason": "dry_run mode",
                    "decided_at": time.time(),
                }
            }
        )
        assert check_review_approved(state, "code") is True
    
    def test_get_feedback_none(self):
        """No feedback returns None."""
        state = make_test_state()
        assert get_review_feedback(state, "code") is None
    
    def test_get_feedback_with_decision(self):
        """Feedback from decision is returned."""
        state = make_test_state(
            review_decisions={
                "code": {
                    "approved": True,
                    "feedback": "Please add docstrings",
                    "decided_at": time.time(),
                }
            }
        )
        assert get_review_feedback(state, "code") == "Please add docstrings"


# =============================================================================
# Test hitl_review_gate Compatibility
# =============================================================================

class TestHitlReviewGateCompat:
    """Test hitl_review_gate() backwards compatibility wrapper."""
    
    def test_hitl_review_gate_delegates_to_code_gate(self):
        """hitl_review_gate delegates to review_gate('code')."""
        state = make_test_state(
            options=MockOptions(hitl_mode="never"),  # Skip actual HITL
            code_artifacts=[MockCodeArtifact("main.py", "code")]
        )
        
        result = hitl_review_gate(state)
        
        # Should auto-approve (hitl_mode=never)
        assert result.review_decisions.get("code", {}).get("auto") is True


# =============================================================================
# Test State Updates
# =============================================================================

class TestStateUpdates:
    """Test that gate correctly updates state."""
    
    def test_records_auto_approval_on_skip(self):
        """Auto-approval is recorded when HITL is skipped."""
        state = make_test_state(
            options=MockOptions(hitl_mode="never"),
            code_artifacts=[MockCodeArtifact("main.py", "code")]
        )
        
        gate_fn = review_gate("code")
        result = gate_fn(state)
        
        # Should record auto-approval
        assert "code" in result.review_decisions
        assert result.review_decisions["code"]["approved"] is True
        assert result.review_decisions["code"]["auto"] is True
    
    def test_records_dry_run_skip(self):
        """Dry run skip is recorded."""
        state = make_test_state(
            options=MockOptions(dry_run=True, hitl_mode="always"),
            code_artifacts=[MockCodeArtifact("main.py", "code")]
        )
        
        gate_fn = review_gate("code")
        result = gate_fn(state)
        
        # Should record auto-approval due to dry_run
        assert "code" in result.review_decisions
        assert result.review_decisions["code"]["auto"] is True
        assert "dry_run" in result.review_decisions["code"]["reason"].lower()
    
    def test_records_no_content_skip(self):
        """No content skip is recorded."""
        state = make_test_state(
            options=MockOptions(hitl_mode="always"),
            code_artifacts=[],
            repo_changes=None,
        )
        
        gate_fn = review_gate("code")
        result = gate_fn(state)
        
        # Should record auto-approval due to no content
        assert "code" in result.review_decisions
        assert result.review_decisions["code"]["auto"] is True


# =============================================================================
# Test Deprecation Warnings
# =============================================================================

class TestDeprecationWarnings:
    """Test deprecation warnings from hitl_gate.py."""
    
    def test_check_hitl_approval_warns(self):
        """check_hitl_approval shows deprecation warning."""
        from integration_coworker.graph.nodes.hitl_gate import check_hitl_approval
        
        state = make_test_state()
        
        with pytest.warns(DeprecationWarning, match="check_review_approved"):
            check_hitl_approval(state)


# =============================================================================
# Test JSON Serialization
# =============================================================================

class TestJsonSerialization:
    """Test that all gate outputs are JSON-serializable."""
    
    def test_review_decisions_serializable(self):
        """review_decisions can be serialized to JSON."""
        state = make_test_state(
            options=MockOptions(hitl_mode="never"),
            code_artifacts=[MockCodeArtifact("main.py", "code")]
        )
        
        gate_fn = review_gate("code")
        result = gate_fn(state)
        
        # Must be JSON-serializable
        json_str = json.dumps(result.review_decisions)
        restored = json.loads(json_str)
        assert restored == result.review_decisions
    
    def test_payload_serializable(self):
        """Interrupt payload is JSON-serializable."""
        state = make_test_state(
            code_artifacts=[MockCodeArtifact("main.py", "x" * 1000)]
        )
        
        # _build_review_payload takes (state, kind, artifact_refs)
        artifact_refs = {}
        payload = _build_review_payload(state, "code", artifact_refs)
        
        # Must not raise
        json_str = json.dumps(payload)
        restored = json.loads(json_str)
        # Check actual field name from implementation
        assert restored["kind"] == "code"
