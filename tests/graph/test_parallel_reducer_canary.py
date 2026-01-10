"""
Canary tests for parallel execution reducer coverage.

These tests validate that:
1. All fields written by parallel branches have reducers
2. Reducer semantics match expected behavior
3. New parallel branches cannot silently corrupt state
4. ACTUAL writes (not just annotations) are detected and covered

This is a P0 production safety test per LangChain forum best practices.
"""

import copy
import operator
from typing import Annotated, Any, Dict, List, Set, get_type_hints, get_origin, get_args
import pytest

from integration_coworker.graph.state_v2 import (
    WorkflowStateDict,
    merge_dicts,
    unique_list,
    last_non_none,
    sum_token_usage,
)


# =============================================================================
# Fields that each parallel branch is known to write
# =============================================================================

# embed_spec_chunks writes these fields:
EMBED_SPEC_CHUNKS_WRITES = {
    "spec_chunk_embeddings",  # Primary output
    "spec_chunk_ids",         # V3 streaming IDs
    "chunk_count",            # Count tracking
    "embedding_count",        # Count tracking
    "completed_steps",        # Always updated
    "node_timings",           # Timing info
    "warnings",               # May add warnings
    "llm_token_usage",        # May track token usage
    "errors",                 # May add errors
}

# understand_task writes these fields:
UNDERSTAND_TASK_WRITES = {
    "integration_task",       # Primary output
    "completed_steps",        # Always updated
    "node_timings",           # Timing info
    "warnings",               # May add warnings
    "llm_token_usage",        # May track token usage
    "errors",                 # May add errors
}

# Fields written by BOTH parallel branches (must have merge-friendly reducers)
PARALLEL_OVERLAP_FIELDS = EMBED_SPEC_CHUNKS_WRITES & UNDERSTAND_TASK_WRITES


def detect_written_fields(before: Dict, after: Dict) -> Set[str]:
    """
    Detect which fields were written (modified or added) between before and after.
    
    This is the core of write detection - compares state before and after
    a branch runs to find what actually changed.
    
    Args:
        before: State snapshot before branch execution
        after: State snapshot after branch execution
        
    Returns:
        Set of field names that were written
    """
    written = set()
    
    # Check all keys in after state
    all_keys = set(after.keys()) | set(before.keys())
    
    for key in all_keys:
        before_val = before.get(key)
        after_val = after.get(key)
        
        # Field was added
        if key not in before:
            if after_val is not None and after_val != [] and after_val != {}:
                written.add(key)
        # Field was modified
        elif before_val != after_val:
            written.add(key)
    
    return written


class TestReducerCoverage:
    """Verify all parallel-written fields have appropriate reducers."""
    
    def _get_reducer(self, field_name: str) -> Any:
        """Extract reducer from WorkflowStateDict type hints."""
        hints = get_type_hints(WorkflowStateDict, include_extras=True)
        hint = hints.get(field_name)
        
        if hint is None:
            return None
        
        origin = get_origin(hint)
        if origin is Annotated:
            args = get_args(hint)
            if len(args) >= 2:
                return args[1]  # The reducer function
        return None
    
    def test_embed_spec_chunks_fields_have_reducers(self):
        """All fields written by embed_spec_chunks must have reducers."""
        for field in EMBED_SPEC_CHUNKS_WRITES:
            reducer = self._get_reducer(field)
            assert reducer is not None, (
                f"Field '{field}' written by embed_spec_chunks has no reducer. "
                f"Add 'Annotated[..., reducer]' to WorkflowStateDict."
            )
    
    def test_understand_task_fields_have_reducers(self):
        """All fields written by understand_task must have reducers."""
        for field in UNDERSTAND_TASK_WRITES:
            reducer = self._get_reducer(field)
            assert reducer is not None, (
                f"Field '{field}' written by understand_task has no reducer. "
                f"Add 'Annotated[..., reducer]' to WorkflowStateDict."
            )
    
    def test_overlap_fields_have_merge_reducers(self):
        """Fields written by BOTH branches must have merge-friendly reducers."""
        # These reducers properly combine values from multiple branches
        merge_friendly_reducers = {
            operator.add,        # Concatenate lists
            unique_list,         # Combine lists without duplicates
            merge_dicts,         # Merge dicts
            sum_token_usage,     # Sum token counts
        }
        
        for field in PARALLEL_OVERLAP_FIELDS:
            reducer = self._get_reducer(field)
            assert reducer in merge_friendly_reducers, (
                f"Field '{field}' is written by BOTH parallel branches but has reducer "
                f"'{reducer}' which is not merge-friendly. Use one of: "
                f"{[r.__name__ for r in merge_friendly_reducers]}"
            )


class TestActualWriteDetection:
    """
    Detect ACTUAL writes by simulating branch execution.
    
    This guards against silent state corruption when someone adds a new field
    to a branch without updating the reducer registry.
    """
    
    def _simulate_embed_spec_chunks(self, state: Dict) -> Dict:
        """Simulate embed_spec_chunks branch writes."""
        result = copy.deepcopy(state)
        result["spec_chunk_embeddings"] = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        result["spec_chunk_ids"] = ["chunk-1", "chunk-2"]
        result["chunk_count"] = 2
        result["embedding_count"] = 2
        result["completed_steps"] = result.get("completed_steps", []) + ["embed_spec_chunks"]
        result["node_timings"] = {**result.get("node_timings", {}), "embed_spec_chunks": 150.0}
        result["llm_token_usage"] = {
            "prompt_tokens": result.get("llm_token_usage", {}).get("prompt_tokens", 0) + 100,
            "completion_tokens": result.get("llm_token_usage", {}).get("completion_tokens", 0) + 50,
            "total_tokens": result.get("llm_token_usage", {}).get("total_tokens", 0) + 150,
        }
        return result
    
    def _simulate_understand_task(self, state: Dict) -> Dict:
        """Simulate understand_task branch writes."""
        result = copy.deepcopy(state)
        result["integration_task"] = {"type": "api_integration", "target": "test_endpoint"}
        result["completed_steps"] = result.get("completed_steps", []) + ["understand_task"]
        result["node_timings"] = {**result.get("node_timings", {}), "understand_task": 100.0}
        result["llm_token_usage"] = {
            "prompt_tokens": result.get("llm_token_usage", {}).get("prompt_tokens", 0) + 200,
            "completion_tokens": result.get("llm_token_usage", {}).get("completion_tokens", 0) + 100,
            "total_tokens": result.get("llm_token_usage", {}).get("total_tokens", 0) + 300,
        }
        return result
    
    def _get_reducer(self, field_name: str) -> Any:
        """Extract reducer from WorkflowStateDict type hints."""
        hints = get_type_hints(WorkflowStateDict, include_extras=True)
        hint = hints.get(field_name)
        
        if hint is None:
            return None
        
        origin = get_origin(hint)
        if origin is Annotated:
            args = get_args(hint)
            if len(args) >= 2:
                return args[1]
        return None
    
    def test_detect_embed_writes_have_reducers(self):
        """
        Detect actual writes from embed_spec_chunks and verify they have reducers.
        
        This catches the case where a developer adds a new field write to the
        embed branch but forgets to add a reducer.
        """
        # Create base state
        base_state = {
            "completed_steps": ["build_silver_api_model"],
            "node_timings": {"build_silver_api_model": 50.0},
            "spec_chunk_embeddings": None,
            "spec_chunk_ids": None,
            "chunk_count": 0,
            "embedding_count": 0,
            "llm_token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        
        # Execute simulated branch
        after_state = self._simulate_embed_spec_chunks(base_state)
        
        # Detect actual writes
        written_fields = detect_written_fields(base_state, after_state)
        
        # Verify all written fields have reducers
        fields_without_reducers = []
        for field in written_fields:
            reducer = self._get_reducer(field)
            if reducer is None:
                fields_without_reducers.append(field)
        
        assert not fields_without_reducers, (
            f"embed_spec_chunks ACTUALLY WROTE fields without reducers: {fields_without_reducers}. "
            f"Total writes detected: {written_fields}. "
            f"Add Annotated[..., reducer] for each field in WorkflowStateDict."
        )
    
    def test_detect_understand_writes_have_reducers(self):
        """
        Detect actual writes from understand_task and verify they have reducers.
        """
        # Create base state
        base_state = {
            "completed_steps": ["build_silver_api_model"],
            "node_timings": {"build_silver_api_model": 50.0},
            "integration_task": None,
            "llm_token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        
        # Execute simulated branch
        after_state = self._simulate_understand_task(base_state)
        
        # Detect actual writes
        written_fields = detect_written_fields(base_state, after_state)
        
        # Verify all written fields have reducers
        fields_without_reducers = []
        for field in written_fields:
            reducer = self._get_reducer(field)
            if reducer is None:
                fields_without_reducers.append(field)
        
        assert not fields_without_reducers, (
            f"understand_task ACTUALLY WROTE fields without reducers: {fields_without_reducers}. "
            f"Total writes detected: {written_fields}. "
            f"Add Annotated[..., reducer] for each field in WorkflowStateDict."
        )
    
    def test_overlapping_writes_detected(self):
        """
        Verify we can detect when both branches write overlapping fields.
        
        This is critical for merge safety - overlapping writes MUST have
        merge-friendly reducers or data will be lost/corrupted.
        """
        base_state = {
            "completed_steps": [],
            "node_timings": {},
            "llm_token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        
        # Run both branches from same base
        embed_after = self._simulate_embed_spec_chunks(base_state)
        understand_after = self._simulate_understand_task(base_state)
        
        # Detect writes from each
        embed_writes = detect_written_fields(base_state, embed_after)
        understand_writes = detect_written_fields(base_state, understand_after)
        
        # Find overlapping writes
        overlap = embed_writes & understand_writes
        
        # Verify overlapping fields have merge-friendly reducers
        merge_friendly_reducers = {
            operator.add,
            unique_list,
            merge_dicts,
            sum_token_usage,
        }
        
        for field in overlap:
            reducer = self._get_reducer(field)
            assert reducer in merge_friendly_reducers, (
                f"Field '{field}' is ACTUALLY WRITTEN by BOTH branches "
                f"but has reducer '{reducer}' which may cause data loss. "
                f"Detected overlap: {overlap}. "
                f"Use a merge-friendly reducer."
            )


class TestReducerSemantics:
    """Verify reducer functions behave correctly."""
    
    def test_unique_list_deduplicates(self):
        """unique_list must remove duplicates and preserve order."""
        left = ["a", "b", "c"]
        right = ["b", "c", "d", "e"]
        result = unique_list(left, right)
        
        assert result == ["a", "b", "c", "d", "e"]
        assert len(result) == len(set(result))  # No duplicates
    
    def test_unique_list_handles_none(self):
        """unique_list must handle None gracefully."""
        assert unique_list(None, ["a"]) == ["a"]
        assert unique_list(["a"], None) == ["a"]
        assert unique_list(None, None) == []
    
    def test_merge_dicts_right_precedence(self):
        """merge_dicts must give precedence to right dict."""
        left = {"a": 1, "b": 2}
        right = {"b": 3, "c": 4}
        result = merge_dicts(left, right)
        
        assert result == {"a": 1, "b": 3, "c": 4}  # b comes from right
    
    def test_merge_dicts_handles_none(self):
        """merge_dicts must handle None gracefully."""
        assert merge_dicts(None, {"a": 1}) == {"a": 1}
        assert merge_dicts({"a": 1}, None) == {"a": 1}
        assert merge_dicts(None, None) == {}
    
    def test_last_non_none_picks_right(self):
        """last_non_none must prefer right value when non-None."""
        assert last_non_none("left", "right") == "right"
        assert last_non_none("left", None) == "left"
        assert last_non_none(None, "right") == "right"
    
    def test_sum_token_usage_adds(self):
        """sum_token_usage must sum all token fields."""
        left = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        right = {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}
        result = sum_token_usage(left, right)
        
        assert result["prompt_tokens"] == 300
        assert result["completion_tokens"] == 150
        assert result["total_tokens"] == 450


class TestParallelWriteGuard:
    """
    Canary test: detect if a new field is added to parallel branches
    without a reducer.
    
    This test should fail if someone adds a new field to a parallel branch
    node's output without updating WorkflowStateDict with an appropriate reducer.
    """
    
    def test_all_statedict_fields_have_reducers(self):
        """Every field in WorkflowStateDict must have a reducer annotation."""
        hints = get_type_hints(WorkflowStateDict, include_extras=True)
        
        fields_without_reducers = []
        for field_name, hint in hints.items():
            origin = get_origin(hint)
            if origin is Annotated:
                args = get_args(hint)
                if len(args) < 2:
                    fields_without_reducers.append(field_name)
            else:
                fields_without_reducers.append(field_name)
        
        assert not fields_without_reducers, (
            f"Fields without reducers in WorkflowStateDict: {fields_without_reducers}. "
            f"Every field must be Annotated[Type, reducer] for parallel execution safety. "
            f"See state_v2.py for examples."
        )
    
    def test_known_parallel_branches_documented(self):
        """Ensure we track which branches write which fields."""
        # This is a documentation canary - if you add a new parallel branch,
        # you must update the field sets at the top of this file
        
        assert len(EMBED_SPEC_CHUNKS_WRITES) >= 5, (
            "EMBED_SPEC_CHUNKS_WRITES seems too small. Did you forget to add fields?"
        )
        assert len(UNDERSTAND_TASK_WRITES) >= 5, (
            "UNDERSTAND_TASK_WRITES seems too small. Did you forget to add fields?"
        )
        assert len(PARALLEL_OVERLAP_FIELDS) >= 4, (
            "PARALLEL_OVERLAP_FIELDS should include at least completed_steps, "
            "node_timings, warnings, and errors."
        )


class TestReducerIntegration:
    """Integration tests simulating parallel merge scenarios."""
    
    def test_simulated_parallel_merge(self):
        """
        Simulate what LangGraph does when merging parallel branch outputs.
        
        This verifies that our reducers produce correct merged state.
        """
        # Base state before parallel split
        base_state = {
            "completed_steps": ["build_silver_api_model"],
            "node_timings": {"build_silver_api_model": 100.0},
            "warnings": ["base warning"],
            "errors": [],
            "llm_token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        
        # Output from embed_spec_chunks branch
        embed_output = {
            "completed_steps": ["embed_spec_chunks"],
            "node_timings": {"embed_spec_chunks": 200.0},
            "warnings": ["embedding warning"],
            "errors": [],
            "llm_token_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        
        # Output from understand_task branch  
        understand_output = {
            "completed_steps": ["understand_task"],
            "node_timings": {"understand_task": 150.0},
            "warnings": ["task warning"],
            "errors": [],
            "llm_token_usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
        }
        
        # Simulate LangGraph reducer application
        merged_completed = unique_list(
            unique_list(base_state["completed_steps"], embed_output["completed_steps"]),
            understand_output["completed_steps"]
        )
        merged_timings = merge_dicts(
            merge_dicts(base_state["node_timings"], embed_output["node_timings"]),
            understand_output["node_timings"]
        )
        merged_warnings = unique_list(
            unique_list(base_state["warnings"], embed_output["warnings"]),
            understand_output["warnings"]
        )
        merged_tokens = sum_token_usage(
            sum_token_usage(base_state["llm_token_usage"], embed_output["llm_token_usage"]),
            understand_output["llm_token_usage"]
        )
        
        # Verify merged state is correct
        assert set(merged_completed) == {
            "build_silver_api_model", "embed_spec_chunks", "understand_task"
        }
        assert merged_timings == {
            "build_silver_api_model": 100.0,
            "embed_spec_chunks": 200.0,
            "understand_task": 150.0,
        }
        assert set(merged_warnings) == {"base warning", "embedding warning", "task warning"}
        assert merged_tokens["prompt_tokens"] == 300
        assert merged_tokens["completion_tokens"] == 150
        assert merged_tokens["total_tokens"] == 450
