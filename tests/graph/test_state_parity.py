"""
Tests for state schema generation and parity enforcement.

These tests ensure:
1. WorkflowState and WorkflowStateDict have identical fields
2. No reducer uses operator.add (causes checkpoint bloat)
3. Roundtrip conversion preserves data
4. Reducers behave correctly
"""

import operator
import pytest
from dataclasses import fields as dataclass_fields
from typing import get_origin, get_args, Annotated

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.state_v2 import (
    WorkflowStateDict,
    dataclass_to_dict,
    dict_to_dataclass,
    create_initial_state_dict,
)
from integration_coworker.graph.state_schema import (
    merge_dicts,
    last_non_none,
    unique_list,
    sum_token_usage,
    verify_field_parity,
    verify_no_operator_add,
    get_reducer_mapping,
)


class TestFieldParity:
    """Test that WorkflowState and WorkflowStateDict have identical fields."""
    
    def test_all_dataclass_fields_in_typeddict(self):
        """Every field in WorkflowState must exist in WorkflowStateDict."""
        dataclass_field_names = {f.name for f in dataclass_fields(WorkflowState)}
        typeddict_field_names = set(WorkflowStateDict.__annotations__.keys())
        
        missing = dataclass_field_names - typeddict_field_names
        assert not missing, f"Fields missing from WorkflowStateDict: {missing}"
    
    def test_no_extra_fields_in_typeddict(self):
        """WorkflowStateDict should not have fields not in WorkflowState."""
        dataclass_field_names = {f.name for f in dataclass_fields(WorkflowState)}
        typeddict_field_names = set(WorkflowStateDict.__annotations__.keys())
        
        extra = typeddict_field_names - dataclass_field_names
        assert not extra, f"Extra fields in WorkflowStateDict: {extra}"
    
    def test_verify_field_parity_function(self):
        """The verify_field_parity function should pass."""
        is_valid, issues = verify_field_parity(WorkflowStateDict)
        assert is_valid, f"Field parity issues: {issues}"
    
    def test_field_count_matches(self):
        """Both definitions should have the same number of fields."""
        dataclass_count = len([f for f in dataclass_fields(WorkflowState)])
        typeddict_count = len(WorkflowStateDict.__annotations__)
        assert dataclass_count == typeddict_count, (
            f"Field count mismatch: WorkflowState={dataclass_count}, "
            f"WorkflowStateDict={typeddict_count}"
        )


class TestNoOperatorAdd:
    """Test that no reducer uses operator.add (causes checkpoint bloat)."""
    
    def test_no_operator_add_in_typeddict(self):
        """No field should use operator.add reducer."""
        is_valid, issues = verify_no_operator_add(WorkflowStateDict)
        assert is_valid, f"operator.add found: {issues}"
    
    def test_spec_chunk_ids_not_operator_add(self):
        """spec_chunk_ids specifically should NOT use operator.add."""
        annotation = WorkflowStateDict.__annotations__.get('spec_chunk_ids')
        assert annotation is not None
        
        if get_origin(annotation) is Annotated:
            args = get_args(annotation)
            if len(args) >= 2:
                reducer = args[1]
                assert reducer is not operator.add, (
                    "spec_chunk_ids uses operator.add - this causes checkpoint bloat!"
                )
    
    def test_spec_chunk_embeddings_not_operator_add(self):
        """spec_chunk_embeddings specifically should NOT use operator.add."""
        annotation = WorkflowStateDict.__annotations__.get('spec_chunk_embeddings')
        assert annotation is not None
        
        if get_origin(annotation) is Annotated:
            args = get_args(annotation)
            if len(args) >= 2:
                reducer = args[1]
                assert reducer is not operator.add, (
                    "spec_chunk_embeddings uses operator.add - this causes checkpoint bloat!"
                )
    
    def test_llm_fallbacks_not_operator_add(self):
        """llm_fallbacks specifically should NOT use operator.add."""
        annotation = WorkflowStateDict.__annotations__.get('llm_fallbacks')
        assert annotation is not None
        
        if get_origin(annotation) is Annotated:
            args = get_args(annotation)
            if len(args) >= 2:
                reducer = args[1]
                assert reducer is not operator.add, (
                    "llm_fallbacks uses operator.add - this causes checkpoint bloat!"
                )


class TestReducerSemantics:
    """Test that reducers behave correctly."""
    
    def test_last_non_none_picks_right(self):
        """last_non_none should take the right value if non-None."""
        assert last_non_none("left", "right") == "right"
        assert last_non_none("left", None) == "left"
        assert last_non_none(None, "right") == "right"
        assert last_non_none(None, None) is None
    
    def test_unique_list_dedupes(self):
        """unique_list should combine and dedupe."""
        result = unique_list(["a", "b"], ["b", "c"])
        assert result == ["a", "b", "c"]
    
    def test_unique_list_preserves_order(self):
        """unique_list should preserve order."""
        result = unique_list(["c", "a"], ["a", "b"])
        assert result == ["c", "a", "b"]
    
    def test_unique_list_handles_none(self):
        """unique_list should handle None inputs."""
        assert unique_list(None, ["a"]) == ["a"]
        assert unique_list(["a"], None) == ["a"]
        assert unique_list(None, None) == []
    
    def test_merge_dicts_right_precedence(self):
        """merge_dicts should give right precedence."""
        result = merge_dicts({"a": 1, "b": 2}, {"b": 3, "c": 4})
        assert result == {"a": 1, "b": 3, "c": 4}
    
    def test_merge_dicts_handles_none(self):
        """merge_dicts should handle None inputs."""
        assert merge_dicts(None, {"a": 1}) == {"a": 1}
        assert merge_dicts({"a": 1}, None) == {"a": 1}
        assert merge_dicts(None, None) == {}
    
    def test_sum_token_usage_adds(self):
        """sum_token_usage should sum all token counts."""
        left = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        right = {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}
        result = sum_token_usage(left, right)
        assert result == {
            "prompt_tokens": 300,
            "completion_tokens": 150,
            "total_tokens": 450,
        }
    
    def test_sum_token_usage_handles_none(self):
        """sum_token_usage should handle None inputs."""
        default = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        assert sum_token_usage(None, None) == default


class TestReducerMapping:
    """Test that fields are mapped to correct reducers."""
    
    def test_completed_steps_uses_unique_list(self):
        """completed_steps should use unique_list."""
        annotation = WorkflowStateDict.__annotations__['completed_steps']
        args = get_args(annotation)
        assert args[1] is unique_list
    
    def test_errors_uses_unique_list(self):
        """errors should use unique_list."""
        annotation = WorkflowStateDict.__annotations__['errors']
        args = get_args(annotation)
        assert args[1] is unique_list
    
    def test_warnings_uses_unique_list(self):
        """warnings should use unique_list."""
        annotation = WorkflowStateDict.__annotations__['warnings']
        args = get_args(annotation)
        assert args[1] is unique_list
    
    def test_plan_uses_merge_dicts(self):
        """plan should use merge_dicts."""
        annotation = WorkflowStateDict.__annotations__['plan']
        args = get_args(annotation)
        assert args[1] is merge_dicts
    
    def test_node_timings_uses_merge_dicts(self):
        """node_timings should use merge_dicts."""
        annotation = WorkflowStateDict.__annotations__['node_timings']
        args = get_args(annotation)
        assert args[1] is merge_dicts
    
    def test_llm_token_usage_uses_sum(self):
        """llm_token_usage should use sum_token_usage."""
        annotation = WorkflowStateDict.__annotations__['llm_token_usage']
        args = get_args(annotation)
        assert args[1] is sum_token_usage
    
    def test_get_reducer_mapping_returns_all_fields(self):
        """get_reducer_mapping should return a mapping for every field."""
        mapping = get_reducer_mapping()
        dataclass_field_names = {f.name for f in dataclass_fields(WorkflowState)}
        
        for field_name in dataclass_field_names:
            assert field_name in mapping, f"Missing reducer for {field_name}"


class TestRoundtripConversion:
    """Test that dataclass <-> dict conversion preserves data."""
    
    def test_basic_roundtrip(self):
        """Basic fields should survive roundtrip."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=["test.yaml"],
            task_description="Test task",
        )
        state.completed_steps = ["step1", "step2"]
        
        state_dict = dataclass_to_dict(state, exclude_large_fields=False)
        state_back = dict_to_dataclass(state_dict)
        
        assert state_back.task_description == "Test task"
        assert state_back.spec_refs == ["test.yaml"]
        assert state_back.completed_steps == ["step1", "step2"]
    
    def test_operations_survives_roundtrip(self):
        """operations field should survive roundtrip (was missing before)."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
        )
        state.operations = [{"name": "test_op", "method": "GET"}]
        
        state_dict = dataclass_to_dict(state, exclude_large_fields=False)
        state_back = dict_to_dataclass(state_dict)
        
        assert state_back.operations == [{"name": "test_op", "method": "GET"}]
    
    def test_sandbox_result_survives_roundtrip(self):
        """sandbox_result field should survive roundtrip (was missing before)."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
        )
        state.sandbox_result = {"passed": True, "tests_run": 5}
        
        state_dict = dataclass_to_dict(state, exclude_large_fields=False)
        state_back = dict_to_dataclass(state_dict)
        
        assert state_back.sandbox_result == {"passed": True, "tests_run": 5}
    
    def test_primary_spec_document_id_survives_roundtrip(self):
        """primary_spec_document_id should survive roundtrip (V24-007 fix)."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
        )
        state.primary_spec_document_id = 42
        
        state_dict = dataclass_to_dict(state, exclude_large_fields=False)
        state_back = dict_to_dataclass(state_dict)
        
        assert state_back.primary_spec_document_id == 42


class TestInitialStateDict:
    """Test create_initial_state_dict."""
    
    def test_creates_all_fields(self):
        """Initial state should have all expected fields."""
        state = create_initial_state_dict(
            source_refs=[],
            spec_refs=["test.yaml"],
            task_description="Test",
        )
        
        # Check key fields exist
        assert "source_refs" in state
        assert "spec_refs" in state
        assert "task_description" in state
        assert "operations" in state
        assert "sandbox_result" in state
        assert "primary_spec_document_id" in state
    
    def test_has_correct_defaults(self):
        """Initial state should have correct default values."""
        state = create_initial_state_dict(
            source_refs=[],
            spec_refs=["test.yaml"],
            task_description="Test",
        )
        
        assert state["completed_steps"] == []
        assert state["errors"] == []
        assert state["plan"] == {}
        assert state["degraded_mode"] is False
        assert state["cache_hit"] is False
        assert state["llm_token_usage"] == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }


class TestImportTimeVerification:
    """Test that import-time verification catches issues."""
    
    def test_import_succeeds_with_correct_parity(self):
        """Importing state_v2 should succeed when parity is correct."""
        # This test passes if we got this far - import worked
        assert WorkflowStateDict is not None
    
    def test_verify_field_parity_is_called(self):
        """verify_field_parity should be called at import time."""
        # The fact that import succeeded means verification passed
        is_valid, issues = verify_field_parity(WorkflowStateDict)
        assert is_valid
    
    def test_verify_no_operator_add_is_called(self):
        """verify_no_operator_add should be called at import time."""
        # The fact that import succeeded means verification passed
        is_valid, issues = verify_no_operator_add(WorkflowStateDict)
        assert is_valid
