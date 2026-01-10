"""
Unit tests for node contract enforcement.

These tests lock down the LangGraph node contract to prevent drift:
1. normalize_node_result rejects unknown state keys (typo detection)
2. normalize_node_result produces correct merged_state
3. Collection auto-marks testcontainers when postgres_env fixture is used

MERGE-BLOCKING: These tests must pass to ensure the runtime contract is correct.
"""

import pytest
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List
from unittest.mock import MagicMock

from integration_coworker.graph.node_contract import (
    normalize_node_result,
    validate_node_result,
    NodeResultType,
)
from integration_coworker.graph.state import WorkflowState


def _make_minimal_state(**overrides) -> WorkflowState:
    """Create a minimal WorkflowState with required fields for testing."""
    defaults = {
        "source_refs": [],
        "spec_refs": [],
        "task_description": "Test task",
        "provider_code": "test-provider",
        "run_id": "test-run-001",
        "completed_steps": [],
    }
    defaults.update(overrides)
    return WorkflowState(**defaults)


class TestNormalizeNodeResult:
    """Test normalize_node_result produces correct (merged_state, update_dict)."""

    @pytest.fixture
    def minimal_state(self) -> WorkflowState:
        """Create a minimal WorkflowState for testing."""
        return _make_minimal_state()

    def test_none_result_returns_unchanged_state(self, minimal_state: WorkflowState):
        """None result means no change - should return original state and empty dict."""
        merged, update = normalize_node_result(minimal_state, None, "test_node")
        
        assert merged is minimal_state, "None result should return same state object"
        assert update == {}, "None result should return empty update dict"

    def test_full_workflowstate_returns_itself(self, minimal_state: WorkflowState):
        """Full WorkflowState return should be used directly."""
        new_state = _make_minimal_state(
            run_id="new-run-002",
            task_description="New task",
            provider_code="new-provider",
            completed_steps=["step1"],
        )
        
        merged, update = normalize_node_result(minimal_state, new_state, "test_node")
        
        assert merged is new_state, "Full state should be returned as merged_state"
        assert update == {}, "Full state return should have empty update dict"

    def test_dict_update_produces_merged_state(self, minimal_state: WorkflowState):
        """Dict update should produce merged_state with changes applied."""
        update_dict = {
            "completed_steps": ["node1", "node2"],
            "task_description": "Updated task",
        }
        
        merged, update = normalize_node_result(minimal_state, update_dict, "test_node")
        
        # Merged state should have updates applied
        assert merged.completed_steps == ["node1", "node2"]
        assert merged.task_description == "Updated task"
        # Unchanged fields should be preserved
        assert merged.run_id == "test-run-001"
        assert merged.provider_code == "test-provider"
        # Update dict should be returned for LangGraph
        assert update == update_dict

    def test_unknown_keys_rejected_with_typo_detection(self, minimal_state: WorkflowState):
        """
        CONTRACT: Unknown keys in update dict are rejected.
        
        This catches typos like "compelted_steps" instead of "completed_steps".
        """
        bad_update = {
            "completed_steps": ["node1"],
            "compelted_steps": ["typo"],  # Typo!
        }
        
        with pytest.raises(TypeError) as exc_info:
            normalize_node_result(minimal_state, bad_update, "test_node")
        
        error_msg = str(exc_info.value)
        assert "unknown state keys" in error_msg.lower()
        assert "compelted_steps" in error_msg
        assert "test_node" in error_msg  # Node name for debugging

    def test_empty_dict_returns_unchanged_state(self, minimal_state: WorkflowState):
        """Empty dict update should return unchanged state."""
        merged, update = normalize_node_result(minimal_state, {}, "test_node")
        
        # State should be unchanged (but may be a new object due to replace())
        assert merged.run_id == minimal_state.run_id
        assert merged.completed_steps == minimal_state.completed_steps
        assert update == {}

    def test_partial_update_preserves_other_fields(self):
        """Partial update should preserve all non-updated fields."""
        # Set some additional fields
        minimal_state = _make_minimal_state(
            run_id="test-run",
            task_description="Original task",
            provider_code="original-provider",
            completed_steps=["step0"],
            chunk_count=42,  # Use a real field instead of 'status'
        )
        
        # Update only one field
        update_dict = {"chunk_count": 100}
        merged, _ = normalize_node_result(minimal_state, update_dict, "test_node")
        
        # Updated field changed
        assert merged.chunk_count == 100
        # All other fields preserved
        assert merged.run_id == "test-run"
        assert merged.task_description == "Original task"
        assert merged.provider_code == "original-provider"
        assert merged.completed_steps == ["step0"]


class TestValidateNodeResult:
    """Test validate_node_result type checking."""

    def test_accepts_none(self):
        """None is a valid return type (no change)."""
        result = validate_node_result(None, "test_node")
        assert result is None

    def test_accepts_dict(self):
        """Dict is a valid return type (partial update)."""
        update = {"key": "value"}
        result = validate_node_result(update, "test_node")
        assert result == {"key": "value"}

    def test_accepts_workflowstate(self):
        """WorkflowState is a valid return type (full state)."""
        state = _make_minimal_state()
        result = validate_node_result(state, "test_node")
        assert result is state

    def test_rejects_invalid_types(self):
        """Invalid types should be rejected with clear error message."""
        invalid_values = [
            "string",
            123,
            ["list"],
            object(),
        ]
        
        for invalid in invalid_values:
            with pytest.raises(TypeError) as exc_info:
                validate_node_result(invalid, "test_node")
            assert "test_node" in str(exc_info.value)
            assert "invalid type" in str(exc_info.value).lower()


class TestCheckpointUsesMergedState:
    """
    Regression test: checkpoint must use merged state, not input state.
    
    This test verifies the contract that checkpointing uses the POST-MERGE
    state so that resume starts from the correct point, not stale state.
    """

    def test_merged_state_differs_from_input_on_dict_update(self):
        """
        When node returns dict update, merged_state MUST differ from input state.
        
        This ensures checkpointing the merged state captures the update.
        """
        input_state = _make_minimal_state(
            run_id="test-run",
            completed_steps=[],
        )
        
        # Node returns partial update
        node_result = {"completed_steps": ["node1"]}
        
        merged_state, update_dict = normalize_node_result(
            input_state, node_result, "test_node"
        )
        
        # CRITICAL: merged_state must have the update applied
        assert merged_state.completed_steps == ["node1"], \
            "Merged state must reflect the node's update"
        
        # Input state should NOT be mutated (dataclass immutability)
        assert input_state.completed_steps == [], \
            "Input state must not be mutated"
        
        # Update dict should match the node result for LangGraph
        assert update_dict == node_result, \
            "Update dict should be returned for LangGraph merge"


class TestTestcontainersMarkerAutoInjection:
    """
    Test that testcontainers marker is auto-injected for fixture usage.
    
    This prevents the bug where tests using postgres_env fixture
    were missing @pytest.mark.testcontainers and got selected
    incorrectly by CI `-m "postgres and not testcontainers"`.
    """

    def test_fixture_names_detected(self, request):
        """Verify this test can access its own fixture names."""
        # This test uses 'request' fixture
        fixture_names = set(request.fixturenames)
        assert "request" in fixture_names

    def test_marker_injection_logic_is_correct(self):
        """
        Verify the marker injection set includes expected fixtures.
        
        This is a static check that the conftest.py has the right fixtures.
        """
        # Import the fixture set from conftest
        import tests.conftest as conftest_module
        
        expected_fixtures = {"postgres_env", "postgres_container", "postgres_dsn"}
        actual_fixtures = conftest_module._TESTCONTAINER_FIXTURES
        
        assert expected_fixtures == actual_fixtures, \
            f"Fixture set mismatch: expected {expected_fixtures}, got {actual_fixtures}"


# Verify that conftest has only one hook definition (guard against duplicate hooks)
class TestConfTestHookIntegrity:
    """
    Guard test: ensure conftest.py doesn't have duplicate hook definitions.
    
    Python silently overwrites duplicate function defs, causing hard-to-debug issues.
    """

    def test_no_duplicate_pytest_hooks(self):
        """conftest.py should have at most one definition of each pytest hook."""
        import inspect
        import tests.conftest as conftest_module
        
        # Get all pytest hook names we care about
        hook_names = [
            "pytest_configure",
            "pytest_collection_modifyitems", 
            "pytest_runtest_setup",
        ]
        
        # Read conftest source to count definitions
        source = inspect.getsource(conftest_module)
        
        for hook_name in hook_names:
            # Count "def <hook_name>" occurrences
            pattern = f"def {hook_name}"
            count = source.count(pattern)
            
            assert count <= 1, \
                f"Duplicate hook definition detected: {hook_name} appears {count} times. " \
                f"Python silently overwrites duplicates - merge into one function."
