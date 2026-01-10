"""
Targeted Regeneration Models Tests (PR #10)

Tests for decision schema and validation:
1. Path validation (security boundaries)
2. Decision parsing (bounded, deterministic)
3. Iteration state (budget enforcement)
4. Idempotency (fingerprint stability)

These tests are merge-blocking for PR #10.
"""
import time
import pytest


class TestPathValidation:
    """Test path validation security boundaries."""
    
    def test_rejects_absolute_unix_path(self):
        """Absolute Unix paths are rejected."""
        from integration_coworker.graph.regeneration_models import (
            validate_rel_path, PathValidationError
        )
        
        with pytest.raises(PathValidationError, match="absolute paths not allowed"):
            validate_rel_path("/etc/passwd")
    
    def test_rejects_absolute_windows_path(self):
        """Absolute Windows paths are rejected."""
        from integration_coworker.graph.regeneration_models import (
            validate_rel_path, PathValidationError
        )
        
        with pytest.raises(PathValidationError, match="absolute paths not allowed"):
            validate_rel_path("C:\\Users\\test\\file.py")
        
        with pytest.raises(PathValidationError, match="absolute paths not allowed"):
            validate_rel_path("D:/data/file.txt")
    
    def test_rejects_parent_traversal(self):
        """Parent traversal (..) is rejected."""
        from integration_coworker.graph.regeneration_models import (
            validate_rel_path, PathValidationError
        )
        
        test_cases = [
            "../secret.txt",
            "foo/../../../etc/passwd",
            "src/../../outside.py",
            "foo/bar/..",
        ]
        
        for path in test_cases:
            with pytest.raises(PathValidationError, match="parent traversal"):
                validate_rel_path(path)
    
    def test_rejects_empty_path(self):
        """Empty paths are rejected."""
        from integration_coworker.graph.regeneration_models import (
            validate_rel_path, PathValidationError
        )
        
        with pytest.raises(PathValidationError, match="cannot be empty"):
            validate_rel_path("")
        
        with pytest.raises(PathValidationError, match="cannot be empty"):
            validate_rel_path("   ")
    
    def test_rejects_too_long_path(self):
        """Paths exceeding MAX_FILE_PATH_LENGTH are rejected."""
        from integration_coworker.graph.regeneration_models import (
            validate_rel_path, PathValidationError
        )
        from integration_coworker.graph.production_guardrails import MAX_FILE_PATH_LENGTH
        
        long_path = "a" * (MAX_FILE_PATH_LENGTH + 1)
        with pytest.raises(PathValidationError, match="path too long"):
            validate_rel_path(long_path)
    
    def test_normalizes_backslashes(self):
        """Windows backslashes are normalized to forward slashes."""
        from integration_coworker.graph.regeneration_models import validate_rel_path
        
        assert validate_rel_path("src\\client\\api.py") == "src/client/api.py"
    
    def test_normalizes_multiple_slashes(self):
        """Multiple consecutive slashes are normalized."""
        from integration_coworker.graph.regeneration_models import validate_rel_path
        
        assert validate_rel_path("src//client///api.py") == "src/client/api.py"
    
    def test_removes_leading_dot_slash(self):
        """Leading ./ is removed."""
        from integration_coworker.graph.regeneration_models import validate_rel_path
        
        assert validate_rel_path("./src/client.py") == "src/client.py"
        assert validate_rel_path("././src/client.py") == "src/client.py"
    
    def test_valid_relative_paths(self):
        """Valid relative paths pass validation."""
        from integration_coworker.graph.regeneration_models import validate_rel_path
        
        valid_paths = [
            "src/client.py",
            "tests/test_client.py",
            "README.md",
            "src/integration_coworker/graph/nodes/codegen.py",
        ]
        
        for path in valid_paths:
            result = validate_rel_path(path)
            assert result == path


class TestValidatePaths:
    """Test bulk path validation."""
    
    def test_deduplicates_paths(self):
        """Duplicate paths are removed."""
        from integration_coworker.graph.regeneration_models import validate_paths
        
        paths = ["src/a.py", "src/b.py", "src/a.py", "src/b.py"]
        result = validate_paths(paths)
        
        assert len(result) == 2
        assert set(result) == {"src/a.py", "src/b.py"}
    
    def test_sorts_paths(self):
        """Paths are sorted for determinism."""
        from integration_coworker.graph.regeneration_models import validate_paths
        
        paths = ["z.py", "a.py", "m.py"]
        result = validate_paths(paths)
        
        assert result == ["a.py", "m.py", "z.py"]
    
    def test_rejects_too_many_targets(self):
        """Exceeding MAX_TARGETS is rejected."""
        from integration_coworker.graph.regeneration_models import (
            validate_paths, PathValidationError
        )
        from integration_coworker.graph.production_guardrails import MAX_TARGETS
        
        paths = [f"file{i}.py" for i in range(MAX_TARGETS + 1)]
        
        with pytest.raises(PathValidationError, match="too many targets"):
            validate_paths(paths)
    
    def test_empty_list_returns_empty(self):
        """Empty input returns empty list."""
        from integration_coworker.graph.regeneration_models import validate_paths
        
        assert validate_paths([]) == []


class TestRegenerateTargetedDecision:
    """Test the decision schema."""
    
    def test_creates_with_valid_targets(self):
        """Valid targets are accepted."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        
        decision = RegenerateTargetedDecision(
            targets=["src/client.py", "src/server.py"],
            global_feedback="Fix authentication",
        )
        
        assert decision.action == "regenerate_targeted"
        assert decision.targets == ["src/client.py", "src/server.py"]
        assert decision.global_feedback == "Fix authentication"
    
    def test_sorts_targets(self):
        """Targets are sorted at creation."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        
        decision = RegenerateTargetedDecision(
            targets=["z.py", "a.py", "m.py"]
        )
        
        assert decision.targets == ["a.py", "m.py", "z.py"]
    
    def test_deduplicates_targets(self):
        """Duplicate targets are removed."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        
        decision = RegenerateTargetedDecision(
            targets=["a.py", "b.py", "a.py"]
        )
        
        assert decision.targets == ["a.py", "b.py"]
    
    def test_rejects_absolute_path_in_targets(self):
        """Absolute paths in targets are rejected."""
        from integration_coworker.graph.regeneration_models import (
            RegenerateTargetedDecision, PathValidationError
        )
        
        with pytest.raises(PathValidationError, match="absolute paths"):
            RegenerateTargetedDecision(targets=["/etc/passwd"])
    
    def test_rejects_traversal_in_targets(self):
        """Traversal in targets is rejected."""
        from integration_coworker.graph.regeneration_models import (
            RegenerateTargetedDecision, PathValidationError
        )
        
        with pytest.raises(PathValidationError, match="parent traversal"):
            RegenerateTargetedDecision(targets=["../secret.py"])
    
    def test_target_feedback_keys_must_be_in_targets(self):
        """target_feedback keys must be subset of targets."""
        from integration_coworker.graph.regeneration_models import (
            RegenerateTargetedDecision, PathValidationError
        )
        
        with pytest.raises(PathValidationError, match="not in targets"):
            RegenerateTargetedDecision(
                targets=["a.py"],
                target_feedback={"b.py": "Fix this"},
            )
    
    def test_bounds_global_feedback(self):
        """Global feedback is bounded to MAX_FEEDBACK_LENGTH."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        from integration_coworker.graph.production_guardrails import MAX_FEEDBACK_LENGTH
        
        long_feedback = "x" * (MAX_FEEDBACK_LENGTH + 1000)
        decision = RegenerateTargetedDecision(
            targets=["a.py"],
            global_feedback=long_feedback,
        )
        
        assert len(decision.global_feedback) == MAX_FEEDBACK_LENGTH
    
    def test_bounds_target_feedback(self):
        """Target feedback values are bounded to MAX_TARGET_FEEDBACK_LENGTH."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        from integration_coworker.graph.production_guardrails import MAX_TARGET_FEEDBACK_LENGTH
        
        long_feedback = "x" * (MAX_TARGET_FEEDBACK_LENGTH + 1000)
        decision = RegenerateTargetedDecision(
            targets=["a.py"],
            target_feedback={"a.py": long_feedback},
        )
        
        assert len(decision.target_feedback["a.py"]) == MAX_TARGET_FEEDBACK_LENGTH
    
    def test_to_dict_has_sorted_keys(self):
        """to_dict produces sorted keys for stable JSON."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        
        decision = RegenerateTargetedDecision(
            targets=["a.py"],
            global_feedback="test",
        )
        
        d = decision.to_dict()
        keys = list(d.keys())
        
        assert keys == sorted(keys)
    
    def test_from_dict_roundtrip(self):
        """from_dict(to_dict()) roundtrip preserves data."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        
        original = RegenerateTargetedDecision(
            targets=["src/a.py", "src/b.py"],
            target_feedback={"src/a.py": "Fix auth"},
            global_feedback="General improvements",
            decided_at=1234567890.0,
        )
        
        roundtrip = RegenerateTargetedDecision.from_dict(original.to_dict())
        
        assert roundtrip.targets == original.targets
        assert roundtrip.target_feedback == original.target_feedback
        assert roundtrip.global_feedback == original.global_feedback
        assert roundtrip.decided_at == original.decided_at
    
    def test_from_dict_rejects_wrong_action(self):
        """from_dict rejects wrong action."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        
        with pytest.raises(ValueError, match="Invalid action"):
            RegenerateTargetedDecision.from_dict({"action": "continue"})
    
    def test_fingerprint_is_stable(self):
        """Same decision produces same fingerprint."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        
        d1 = RegenerateTargetedDecision(
            targets=["a.py", "b.py"],
            global_feedback="test",
        )
        d2 = RegenerateTargetedDecision(
            targets=["b.py", "a.py"],  # Different order
            global_feedback="test",
        )
        
        assert d1.fingerprint() == d2.fingerprint()
    
    def test_fingerprint_differs_for_different_targets(self):
        """Different targets produce different fingerprints."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        
        d1 = RegenerateTargetedDecision(targets=["a.py"])
        d2 = RegenerateTargetedDecision(targets=["b.py"])
        
        assert d1.fingerprint() != d2.fingerprint()
    
    def test_fingerprint_ignores_decided_at(self):
        """Fingerprint does not include decided_at (for idempotency)."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        
        d1 = RegenerateTargetedDecision(targets=["a.py"], decided_at=1.0)
        d2 = RegenerateTargetedDecision(targets=["a.py"], decided_at=2.0)
        
        assert d1.fingerprint() == d2.fingerprint()


class TestIterationState:
    """Test iteration budget enforcement."""
    
    def test_can_iterate_initially(self):
        """New state allows iteration."""
        from integration_coworker.graph.regeneration_models import IterationState
        
        state = IterationState()
        
        assert state.can_iterate() is True
        assert state.current_iteration == 0
    
    def test_cannot_iterate_at_max(self):
        """Cannot iterate when at max."""
        from integration_coworker.graph.regeneration_models import IterationState
        
        state = IterationState(current_iteration=3, max_iterations=3)
        
        assert state.can_iterate() is False
    
    def test_record_iteration_increments_count(self):
        """Recording an iteration increments the count."""
        from integration_coworker.graph.regeneration_models import IterationState
        
        state = IterationState()
        state.record_iteration(
            targets=["a.py"],
            outcome="improved",
            blocking_before=5,
            blocking_after=3,
            fingerprint="abc123",
        )
        
        assert state.current_iteration == 1
        assert len(state.history) == 1
    
    def test_should_escalate_at_max_iterations(self):
        """Escalate when max iterations reached."""
        from integration_coworker.graph.regeneration_models import IterationState
        
        state = IterationState(current_iteration=3, max_iterations=3)
        
        assert state.should_escalate() is True
    
    def test_should_escalate_on_stuck_loop(self):
        """Escalate when same fingerprint repeated."""
        from integration_coworker.graph.regeneration_models import (
            IterationState, IterationHistoryEntry
        )
        
        state = IterationState(
            current_iteration=2,
            max_iterations=5,
            history=[
                IterationHistoryEntry(
                    iteration=1, targets=["a.py"], outcome="no_change",
                    blocking_before=5, blocking_after=5, fingerprint="same",
                ),
                IterationHistoryEntry(
                    iteration=2, targets=["a.py"], outcome="no_change",
                    blocking_before=5, blocking_after=5, fingerprint="same",
                ),
            ]
        )
        
        assert state.should_escalate() is True
    
    def test_should_escalate_on_no_improvement(self):
        """Escalate when last 2 iterations showed no improvement."""
        from integration_coworker.graph.regeneration_models import (
            IterationState, IterationHistoryEntry
        )
        
        state = IterationState(
            current_iteration=2,
            max_iterations=5,
            history=[
                IterationHistoryEntry(
                    iteration=1, targets=["a.py"], outcome="regressed",
                    blocking_before=3, blocking_after=5, fingerprint="fp1",
                ),
                IterationHistoryEntry(
                    iteration=2, targets=["b.py"], outcome="no_change",
                    blocking_before=5, blocking_after=5, fingerprint="fp2",
                ),
            ]
        )
        
        assert state.should_escalate() is True
    
    def test_should_not_escalate_on_improvement(self):
        """Don't escalate if improving."""
        from integration_coworker.graph.regeneration_models import (
            IterationState, IterationHistoryEntry
        )
        
        state = IterationState(
            current_iteration=1,
            max_iterations=5,
            history=[
                IterationHistoryEntry(
                    iteration=1, targets=["a.py"], outcome="improved",
                    blocking_before=5, blocking_after=3, fingerprint="fp1",
                ),
            ]
        )
        
        assert state.should_escalate() is False
    
    def test_history_is_bounded(self):
        """History is bounded to MAX_ITERATION_HISTORY_ENTRIES."""
        from integration_coworker.graph.regeneration_models import (
            IterationState, MAX_ITERATION_HISTORY_ENTRIES
        )
        
        state = IterationState()
        
        for i in range(MAX_ITERATION_HISTORY_ENTRIES + 5):
            state.record_iteration(
                targets=["a.py"],
                outcome="improved",
                blocking_before=5,
                blocking_after=5 - i % 5,
                fingerprint=f"fp{i}",
            )
        
        assert len(state.history) == MAX_ITERATION_HISTORY_ENTRIES
    
    def test_to_dict_from_dict_roundtrip(self):
        """to_dict/from_dict roundtrip preserves state."""
        from integration_coworker.graph.regeneration_models import IterationState
        
        state = IterationState()
        state.record_iteration(
            targets=["a.py"],
            outcome="improved",
            blocking_before=5,
            blocking_after=3,
            fingerprint="abc123",
        )
        
        roundtrip = IterationState.from_dict(state.to_dict())
        
        assert roundtrip.current_iteration == state.current_iteration
        assert roundtrip.max_iterations == state.max_iterations
        assert len(roundtrip.history) == len(state.history)


class TestRegenerationConstraints:
    """Test constraint schema."""
    
    def test_sorts_target_paths(self):
        """Target paths are sorted."""
        from integration_coworker.graph.regeneration_models import RegenerationConstraints
        
        constraints = RegenerationConstraints(
            target_paths=["z.py", "a.py", "m.py"]
        )
        
        assert constraints.target_paths == ["a.py", "m.py", "z.py"]
    
    def test_deduplicates_target_paths(self):
        """Target paths are deduplicated."""
        from integration_coworker.graph.regeneration_models import RegenerationConstraints
        
        constraints = RegenerationConstraints(
            target_paths=["a.py", "b.py", "a.py"]
        )
        
        assert constraints.target_paths == ["a.py", "b.py"]
    
    def test_filters_reasons_to_target_paths(self):
        """Only reasons for target_paths are kept."""
        from integration_coworker.graph.regeneration_models import RegenerationConstraints
        
        constraints = RegenerationConstraints(
            target_paths=["a.py"],
            target_reasons={
                "a.py": ["reason1"],
                "b.py": ["should be removed"],
            }
        )
        
        assert "b.py" not in constraints.target_reasons
        assert "a.py" in constraints.target_reasons
    
    def test_fingerprint_is_stable(self):
        """Same constraints produce same fingerprint."""
        from integration_coworker.graph.regeneration_models import RegenerationConstraints
        
        c1 = RegenerationConstraints(
            target_paths=["a.py", "b.py"],
            global_guidance="test",
        )
        c2 = RegenerationConstraints(
            target_paths=["b.py", "a.py"],  # Different order
            global_guidance="test",
        )
        
        assert c1.fingerprint() == c2.fingerprint()
    
    def test_to_dict_from_dict_roundtrip(self):
        """Roundtrip preserves data."""
        from integration_coworker.graph.regeneration_models import RegenerationConstraints
        
        original = RegenerationConstraints(
            target_paths=["a.py", "b.py"],
            target_reasons={"a.py": ["fix auth"]},
            preserve_non_targets=True,
            global_guidance="Be careful",
            decision_fingerprint="dec123",
            attribution_fingerprint="attr456",
        )
        
        roundtrip = RegenerationConstraints.from_dict(original.to_dict())
        
        assert roundtrip.target_paths == original.target_paths
        assert roundtrip.target_reasons == original.target_reasons
        assert roundtrip.preserve_non_targets == original.preserve_non_targets
        assert roundtrip.global_guidance == original.global_guidance


class TestParseReviewDecision:
    """Test the decision parsing utility."""
    
    def test_parses_continue_action(self):
        """Continue action passes through."""
        from integration_coworker.graph.regeneration_models import parse_review_decision
        
        result = parse_review_decision({
            "action": "continue",
            "feedback": "Looks good",
        })
        
        assert result["action"] == "continue"
        assert result["feedback"] == "Looks good"
    
    def test_parses_regenerate_targeted(self):
        """Regenerate targeted is validated."""
        from integration_coworker.graph.regeneration_models import parse_review_decision
        
        result = parse_review_decision({
            "action": "regenerate_targeted",
            "targets": ["src/a.py", "src/b.py"],
            "global_feedback": "Fix errors",
        })
        
        assert result["action"] == "regenerate_targeted"
        assert result["targets"] == ["src/a.py", "src/b.py"]
    
    def test_rejects_invalid_paths_in_regenerate(self):
        """Invalid paths in regenerate_targeted are rejected."""
        from integration_coworker.graph.regeneration_models import (
            parse_review_decision, PathValidationError
        )
        
        with pytest.raises(PathValidationError):
            parse_review_decision({
                "action": "regenerate_targeted",
                "targets": ["/etc/passwd"],
            })
    
    def test_rejects_unknown_action(self):
        """Unknown actions are rejected."""
        from integration_coworker.graph.regeneration_models import parse_review_decision
        
        with pytest.raises(ValueError, match="Unknown review action"):
            parse_review_decision({"action": "invalid"})
    
    def test_bounds_feedback_in_continue(self):
        """Feedback is bounded in continue action."""
        from integration_coworker.graph.regeneration_models import parse_review_decision
        from integration_coworker.graph.production_guardrails import MAX_FEEDBACK_LENGTH
        
        long_feedback = "x" * (MAX_FEEDBACK_LENGTH + 1000)
        result = parse_review_decision({
            "action": "continue",
            "feedback": long_feedback,
        })
        
        assert len(result["feedback"]) == MAX_FEEDBACK_LENGTH


class TestDeterministicOrdering:
    """Test that all outputs are deterministically ordered."""
    
    def test_decision_to_dict_is_deterministic(self):
        """Decision.to_dict() produces deterministic output."""
        from integration_coworker.graph.regeneration_models import RegenerateTargetedDecision
        import json
        
        d1 = RegenerateTargetedDecision(
            targets=["z.py", "a.py"],
            target_feedback={"z.py": "fix z", "a.py": "fix a"},
            global_feedback="test",
        )
        
        # Serialize multiple times
        json_outputs = [json.dumps(d1.to_dict()) for _ in range(5)]
        
        # All should be identical
        assert len(set(json_outputs)) == 1
    
    def test_constraints_to_dict_is_deterministic(self):
        """Constraints.to_dict() produces deterministic output."""
        from integration_coworker.graph.regeneration_models import RegenerationConstraints
        import json
        
        c = RegenerationConstraints(
            target_paths=["z.py", "a.py"],
            target_reasons={"z.py": ["r1"], "a.py": ["r2"]},
            global_guidance="test",
        )
        
        json_outputs = [json.dumps(c.to_dict()) for _ in range(5)]
        
        assert len(set(json_outputs)) == 1
    
    def test_iteration_state_to_dict_is_deterministic(self):
        """IterationState.to_dict() produces deterministic output."""
        from integration_coworker.graph.regeneration_models import IterationState
        import json
        
        state = IterationState()
        state.record_iteration(
            targets=["z.py", "a.py"],
            outcome="improved",
            blocking_before=5,
            blocking_after=3,
            fingerprint="abc",
        )
        
        json_outputs = [json.dumps(state.to_dict()) for _ in range(5)]
        
        assert len(set(json_outputs)) == 1
