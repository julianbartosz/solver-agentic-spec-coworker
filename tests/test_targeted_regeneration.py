"""
Tests for Targeted Regeneration Node (PR #10).

Tests cover:
1. Decision extraction and validation
2. Constraints building from decision + attribution
3. Iteration budget enforcement
4. Stuck loop detection (fingerprint deduplication)
5. Artifact storage (refs-not-blobs)
6. Escalation triggers
"""
import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MockWorkflowState:
    """Minimal mock state for testing."""
    run_id: str = "test-run"
    review_decisions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    iteration_state: Optional[Dict[str, Any]] = None
    regeneration_constraints_ref: Optional[Dict[str, Any]] = None
    sandbox_attribution_summary: Optional[Dict[str, Any]] = None
    quality_refs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    plan: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    completed_steps: List[str] = field(default_factory=list)
    options: Optional[Any] = None


class TestDecisionExtraction:
    """Tests for extracting regeneration decision from state."""
    
    def test_extracts_from_sandbox_decision(self):
        """Decision is extracted from review_decisions['sandbox']."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _extract_decision_from_state
        )
        
        state = MockWorkflowState(
            review_decisions={
                "sandbox": {
                    "action": "regenerate_targeted",
                    "targets": ["src/client.py"],
                    "global_feedback": "Fix auth",
                }
            }
        )
        
        decision = _extract_decision_from_state(state)
        
        assert decision is not None
        assert decision["action"] == "regenerate_targeted"
        assert decision["targets"] == ["src/client.py"]
    
    def test_returns_none_for_continue_decision(self):
        """Returns None when decision is 'continue'."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _extract_decision_from_state
        )
        
        state = MockWorkflowState(
            review_decisions={
                "sandbox": {
                    "action": "continue",
                    "feedback": "Looks good",
                }
            }
        )
        
        decision = _extract_decision_from_state(state)
        
        assert decision is None
    
    def test_returns_none_for_missing_decision(self):
        """Returns None when no sandbox decision exists."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _extract_decision_from_state
        )
        
        state = MockWorkflowState()
        
        decision = _extract_decision_from_state(state)
        
        assert decision is None


class TestConstraintsBuilding:
    """Tests for building constraints from decision + attribution."""
    
    def test_builds_basic_constraints(self):
        """Basic constraints are built from decision."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _build_constraints
        )
        from integration_coworker.graph.regeneration_models import (
            RegenerateTargetedDecision
        )
        
        state = MockWorkflowState()
        decision = RegenerateTargetedDecision(
            targets=["src/client.py", "src/server.py"],
            global_feedback="Fix all errors",
        )
        
        constraints = _build_constraints(state, decision)
        
        assert constraints.target_paths == ["src/client.py", "src/server.py"]
        assert constraints.global_guidance == "Fix all errors"
        assert constraints.preserve_non_targets is True
    
    def test_incorporates_target_feedback(self):
        """Target-specific feedback is added to reasons."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _build_constraints
        )
        from integration_coworker.graph.regeneration_models import (
            RegenerateTargetedDecision
        )
        
        state = MockWorkflowState()
        decision = RegenerateTargetedDecision(
            targets=["src/client.py"],
            target_feedback={"src/client.py": "Fix auth token refresh"},
        )
        
        constraints = _build_constraints(state, decision)
        
        assert "src/client.py" in constraints.target_reasons
        assert any(
            "Fix auth token refresh" in reason
            for reason in constraints.target_reasons["src/client.py"]
        )
    
    def test_incorporates_attribution_hints(self):
        """Attribution hints are added to target reasons."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _build_constraints
        )
        from integration_coworker.graph.regeneration_models import (
            RegenerateTargetedDecision
        )
        
        state = MockWorkflowState(
            sandbox_attribution_summary={
                "hints": [
                    {"file": "src/client.py", "message": "TypeError on line 42"},
                ]
            }
        )
        decision = RegenerateTargetedDecision(targets=["src/client.py"])
        
        constraints = _build_constraints(state, decision)
        
        assert "src/client.py" in constraints.target_reasons
        assert any(
            "TypeError on line 42" in reason
            for reason in constraints.target_reasons["src/client.py"]
        )
    
    def test_stores_decision_fingerprint(self):
        """Decision fingerprint is stored in constraints."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _build_constraints
        )
        from integration_coworker.graph.regeneration_models import (
            RegenerateTargetedDecision
        )
        
        state = MockWorkflowState()
        decision = RegenerateTargetedDecision(targets=["src/client.py"])
        
        constraints = _build_constraints(state, decision)
        
        assert constraints.decision_fingerprint == decision.fingerprint()


class TestIterationBudget:
    """Tests for iteration budget enforcement."""
    
    def test_allows_iteration_when_under_budget(self):
        """Iteration proceeds when under budget."""
        from integration_coworker.graph.regeneration_models import IterationState
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _get_or_create_iteration_state
        )
        
        state = MockWorkflowState(
            iteration_state=IterationState(
                current_iteration=1,
                max_iterations=3,
            ).to_dict()
        )
        
        iteration = _get_or_create_iteration_state(state)
        
        assert iteration.can_iterate() is True
    
    def test_blocks_iteration_at_budget(self):
        """Iteration is blocked at max budget."""
        from integration_coworker.graph.regeneration_models import IterationState
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _get_or_create_iteration_state
        )
        
        state = MockWorkflowState(
            iteration_state=IterationState(
                current_iteration=3,
                max_iterations=3,
            ).to_dict()
        )
        
        iteration = _get_or_create_iteration_state(state)
        
        assert iteration.can_iterate() is False
    
    def test_creates_new_state_when_missing(self):
        """New iteration state is created when missing."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _get_or_create_iteration_state,
            MAX_REGENERATION_ITERATIONS,
        )
        from integration_coworker.graph.regeneration_models import MAX_REGENERATION_ITERATIONS
        
        state = MockWorkflowState()
        
        iteration = _get_or_create_iteration_state(state)
        
        assert iteration.current_iteration == 0
        assert iteration.max_iterations == MAX_REGENERATION_ITERATIONS


class TestStuckLoopDetection:
    """Tests for stuck loop detection via fingerprint matching."""
    
    def test_detects_duplicate_fingerprint(self):
        """Duplicate fingerprint is detected as stuck loop."""
        from integration_coworker.graph.regeneration_models import (
            IterationState, IterationHistoryEntry
        )
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _is_duplicate_constraints
        )
        
        state = MockWorkflowState(
            iteration_state=IterationState(
                history=[
                    IterationHistoryEntry(
                        iteration=1,
                        targets=["src/client.py"],
                        outcome="no_change",
                        blocking_before=5,
                        blocking_after=5,
                        fingerprint="abc123",
                    )
                ]
            ).to_dict()
        )
        
        is_dup = _is_duplicate_constraints(state, "abc123")
        
        assert is_dup is True
    
    def test_allows_new_fingerprint(self):
        """New fingerprint is allowed."""
        from integration_coworker.graph.regeneration_models import (
            IterationState, IterationHistoryEntry
        )
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _is_duplicate_constraints
        )
        
        state = MockWorkflowState(
            iteration_state=IterationState(
                history=[
                    IterationHistoryEntry(
                        iteration=1,
                        targets=["src/client.py"],
                        outcome="improved",
                        blocking_before=5,
                        blocking_after=3,
                        fingerprint="abc123",
                    )
                ]
            ).to_dict()
        )
        
        is_dup = _is_duplicate_constraints(state, "xyz789")
        
        assert is_dup is False
    
    def test_returns_false_when_no_history(self):
        """Returns False when no iteration history."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _is_duplicate_constraints
        )
        
        state = MockWorkflowState()
        
        is_dup = _is_duplicate_constraints(state, "abc123")
        
        assert is_dup is False


class TestEscalation:
    """Tests for escalation to human review."""
    
    def test_escalation_sets_plan_flags(self):
        """Escalation sets appropriate plan flags."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _escalate_to_human
        )
        
        state = MockWorkflowState()
        
        _escalate_to_human(state, "budget_exhausted")
        
        assert state.plan["regeneration_escalated"] is True
        assert state.plan["escalation_reason"] == "budget_exhausted"
    
    def test_escalation_stores_fingerprint(self):
        """Escalation stores fingerprint when provided."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _escalate_to_human
        )
        
        state = MockWorkflowState()
        
        _escalate_to_human(state, "stuck_loop", "abc123")
        
        assert state.plan["escalation_fingerprint"] == "abc123"
    
    def test_escalation_adds_warning(self):
        """Escalation adds user-visible warning."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _escalate_to_human
        )
        
        state = MockWorkflowState()
        
        _escalate_to_human(state, "no_improvement")
        
        assert any("escalated" in w.lower() for w in state.warnings)


class TestNodeIntegration:
    """Integration tests for the full node."""
    
    @patch('integration_coworker.graph.nodes.targeted_regeneration.get_artifact_store')
    def test_full_flow_success(self, mock_get_store):
        """Full node execution succeeds with valid decision."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            targeted_regeneration
        )
        from integration_coworker.persistence.artifacts.base import ArtifactRef
        
        # Mock artifact store
        mock_store = MagicMock()
        mock_store.put.return_value = ArtifactRef(
            run_id="test-run",
            key="regeneration_constraints",
            uri="file:///tmp/constraints.json",
            sha256="abc123",
            size_bytes=1024,
        )
        mock_get_store.return_value = mock_store
        
        state = MockWorkflowState(
            review_decisions={
                "sandbox": {
                    "action": "regenerate_targeted",
                    "targets": ["src/client.py"],
                    "global_feedback": "Fix errors",
                }
            }
        )
        
        result = targeted_regeneration(state)
        
        # Check state updates
        assert result.regeneration_constraints_ref is not None
        assert "ref" in result.regeneration_constraints_ref
        assert result.plan.get("regeneration_mode") is True
        assert "targeted_regeneration" in result.completed_steps
    
    def test_skips_when_no_decision(self):
        """Node skips gracefully when no decision."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            targeted_regeneration
        )
        
        state = MockWorkflowState()
        
        result = targeted_regeneration(state)
        
        assert result.regeneration_constraints_ref is None
        assert "targeted_regeneration" in result.completed_steps
    
    def test_rejects_invalid_paths(self):
        """Node rejects decision with invalid paths."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            targeted_regeneration
        )
        
        state = MockWorkflowState(
            review_decisions={
                "sandbox": {
                    "action": "regenerate_targeted",
                    "targets": ["/etc/passwd"],  # Invalid!
                }
            }
        )
        
        result = targeted_regeneration(state)
        
        assert any("Invalid" in e for e in result.errors)
        assert result.regeneration_constraints_ref is None
    
    @patch('integration_coworker.graph.nodes.targeted_regeneration.get_artifact_store')
    def test_escalates_at_budget_limit(self, mock_get_store):
        """Node escalates when iteration budget exhausted."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            targeted_regeneration
        )
        from integration_coworker.graph.regeneration_models import IterationState
        
        state = MockWorkflowState(
            review_decisions={
                "sandbox": {
                    "action": "regenerate_targeted",
                    "targets": ["src/client.py"],
                }
            },
            iteration_state=IterationState(
                current_iteration=3,
                max_iterations=3,
            ).to_dict()
        )
        
        result = targeted_regeneration(state)
        
        assert result.plan.get("regeneration_escalated") is True
        assert result.plan.get("escalation_reason") == "budget_exhausted"
    
    @patch('integration_coworker.graph.nodes.targeted_regeneration.get_artifact_store')
    def test_escalates_on_stuck_loop(self, mock_get_store):
        """Node escalates when duplicate fingerprint detected."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            targeted_regeneration, _build_constraints
        )
        from integration_coworker.graph.regeneration_models import (
            IterationState, IterationHistoryEntry, RegenerateTargetedDecision
        )
        
        # Pre-compute fingerprint for the CONSTRAINTS (not just decision)
        # Build a mock state to get the actual constraints fingerprint
        mock_state = MockWorkflowState()
        decision = RegenerateTargetedDecision(targets=["src/client.py"])
        constraints = _build_constraints(mock_state, decision)
        constraints_fingerprint = constraints.fingerprint()
        
        state = MockWorkflowState(
            review_decisions={
                "sandbox": {
                    "action": "regenerate_targeted",
                    "targets": ["src/client.py"],
                }
            },
            iteration_state=IterationState(
                current_iteration=1,
                max_iterations=3,
                history=[
                    IterationHistoryEntry(
                        iteration=1,
                        targets=["src/client.py"],
                        outcome="no_change",
                        blocking_before=5,
                        blocking_after=5,
                        fingerprint=constraints_fingerprint,  # Same fingerprint!
                    )
                ]
            ).to_dict()
        )
        
        result = targeted_regeneration(state)
        
        assert result.plan.get("regeneration_escalated") is True
        assert result.plan.get("escalation_reason") == "stuck_loop"


class TestIdempotency:
    """Tests for idempotent behavior."""
    
    @patch('integration_coworker.graph.nodes.targeted_regeneration.get_artifact_store')
    def test_constraints_fingerprint_is_stable(self, mock_get_store):
        """Same inputs produce same constraints fingerprint."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _build_constraints
        )
        from integration_coworker.graph.regeneration_models import (
            RegenerateTargetedDecision
        )
        
        state = MockWorkflowState()
        decision = RegenerateTargetedDecision(
            targets=["src/client.py", "src/server.py"],
            global_feedback="Fix errors",
        )
        
        c1 = _build_constraints(state, decision)
        c2 = _build_constraints(state, decision)
        
        assert c1.fingerprint() == c2.fingerprint()
    
    @patch('integration_coworker.graph.nodes.targeted_regeneration.get_artifact_store')
    def test_constraints_fingerprint_differs_for_different_targets(self, mock_get_store):
        """Different targets produce different fingerprints."""
        from integration_coworker.graph.nodes.targeted_regeneration import (
            _build_constraints
        )
        from integration_coworker.graph.regeneration_models import (
            RegenerateTargetedDecision
        )
        
        state = MockWorkflowState()
        d1 = RegenerateTargetedDecision(targets=["src/a.py"])
        d2 = RegenerateTargetedDecision(targets=["src/b.py"])
        
        c1 = _build_constraints(state, d1)
        c2 = _build_constraints(state, d2)
        
        assert c1.fingerprint() != c2.fingerprint()
