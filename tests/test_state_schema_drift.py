"""
Tests for state schema consistency and drift detection.

PR #7/8 Hardening: These tests ensure WorkflowState and WorkflowStateDict
stay in sync, preventing runtime import failures.

CRITICAL: This test file must be import-safe:
- No imports from graph.nodes (may have heavy deps)
- No imports from ui (streamlit)
- No imports from graph.runtime (pulls in too much)
- Only imports: state.py, state_v2.py, state_schema.py

This allows the test to run in core CI without optional deps.
"""

import pytest
import sys
from dataclasses import fields as dataclass_fields
from typing import get_type_hints, get_origin, get_args, Annotated


# =============================================================================
# Import Safety Guard
# =============================================================================

class TestImportSafety:
    """Verify this test file doesn't accidentally import heavy deps."""
    
    def test_no_streamlit_imported(self):
        """Test file must not import streamlit."""
        # Record modules before importing state modules
        modules_before = set(sys.modules.keys())
        
        # Import only the state modules we need
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.graph.state_v2 import WorkflowStateDict
        from integration_coworker.graph.state_schema import (
            verify_field_parity,
            verify_no_operator_add,
        )
        
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        
        streamlit_mods = [m for m in new_modules if 'streamlit' in m.lower()]
        assert not streamlit_mods, f"Streamlit imported unexpectedly: {streamlit_mods}"
    
    def test_no_runtime_imported(self):
        """Test file must not import graph.runtime."""
        modules_before = set(sys.modules.keys())
        
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.graph.state_v2 import WorkflowStateDict
        
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        
        # runtime.py is a heavy import - should not be pulled in
        runtime_mods = [m for m in new_modules if m.endswith('.runtime')]
        assert not runtime_mods, f"runtime imported unexpectedly: {runtime_mods}"


# =============================================================================
# Schema Drift Detection
# =============================================================================

class TestStateSchemaDrift:
    """
    Detect drift between WorkflowState (dataclass) and WorkflowStateDict (TypedDict).
    
    These tests fail fast if fields are added to one but not the other.
    """
    
    def test_field_parity_via_schema_module(self):
        """Use the official parity checker from state_schema.py."""
        from integration_coworker.graph.state_v2 import WorkflowStateDict
        from integration_coworker.graph.state_schema import verify_field_parity
        
        is_valid, issues = verify_field_parity(WorkflowStateDict)
        
        assert is_valid, (
            f"WorkflowState and WorkflowStateDict have drifted!\n"
            f"Issues: {issues}\n"
            f"Fix: Add missing fields to WorkflowStateDict in state_v2.py"
        )
    
    def test_no_operator_add_reducers(self):
        """Ensure no field uses operator.add (causes checkpoint bloat)."""
        from integration_coworker.graph.state_v2 import WorkflowStateDict
        from integration_coworker.graph.state_schema import verify_no_operator_add
        
        is_valid, issues = verify_no_operator_add(WorkflowStateDict)
        
        assert is_valid, (
            f"WorkflowStateDict uses operator.add which causes checkpoint bloat!\n"
            f"Issues: {issues}\n"
            f"Fix: Change to last_non_none, unique_list, or merge_dicts"
        )
    
    def test_field_count_matches(self):
        """Simple sanity check: field counts should match."""
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.graph.state_v2 import WorkflowStateDict
        
        dataclass_fields_count = len(list(dataclass_fields(WorkflowState)))
        typed_dict_fields_count = len(WorkflowStateDict.__annotations__)
        
        assert dataclass_fields_count == typed_dict_fields_count, (
            f"Field count mismatch: WorkflowState has {dataclass_fields_count} fields, "
            f"WorkflowStateDict has {typed_dict_fields_count} fields"
        )
    
    def test_quality_fields_in_both(self):
        """Verify PR #7/8 quality fields exist in both state types."""
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.graph.state_v2 import WorkflowStateDict
        
        # PR #7/8 quality pipeline fields
        QUALITY_FIELDS = {
            "quality_refs",              # PR #7: refs-not-blobs storage
            "iteration_state",           # PR #7: regeneration budget
            "static_analysis_retries",   # PR #8: retry tracking
            "static_analysis_escalated", # PR #8: escalation flag
            "needs_static_recheck",      # PR #8: recheck flag
        }
        
        dataclass_field_names = {f.name for f in dataclass_fields(WorkflowState)}
        typed_dict_field_names = set(WorkflowStateDict.__annotations__.keys())
        
        missing_from_dataclass = QUALITY_FIELDS - dataclass_field_names
        missing_from_typed_dict = QUALITY_FIELDS - typed_dict_field_names
        
        assert not missing_from_dataclass, (
            f"Quality fields missing from WorkflowState: {missing_from_dataclass}"
        )
        assert not missing_from_typed_dict, (
            f"Quality fields missing from WorkflowStateDict: {missing_from_typed_dict}"
        )


# =============================================================================
# Quality Fields Source of Truth
# =============================================================================

# This is the SINGLE SOURCE OF TRUTH for quality-related state keys.
# If you add a new quality field, add it here first, then to state.py and state_v2.py.

QUALITY_STATE_FIELDS = {
    # PR #7: Quality pipeline core
    "quality_refs": {
        "description": "ArtifactRefs + bounded summaries for quality signals",
        "type": "Dict[str, Dict[str, Any]]",
        "reducer": "merge_dicts",
        "introduced_in": "PR #7",
    },
    "iteration_state": {
        "description": "Regeneration budget tracking (JSON-friendly dict)",
        "type": "Optional[Dict[str, Any]]",
        "reducer": "last_non_none",
        "introduced_in": "PR #7",
    },
    # PR #8: Static analysis tracking
    "static_analysis_retries": {
        "description": "Count of static analysis retry attempts",
        "type": "int",
        "reducer": "last_non_none",
        "introduced_in": "PR #8",
    },
    "static_analysis_escalated": {
        "description": "Whether static analysis was escalated to human review",
        "type": "bool",
        "reducer": "last_non_none",
        "introduced_in": "PR #8",
    },
    "needs_static_recheck": {
        "description": "Flag set when human edits require re-validation",
        "type": "bool",
        "reducer": "last_non_none",
        "introduced_in": "PR #8",
    },
    # PR #9: Sandbox failure attribution
    "sandbox_attribution_summary": {
        "description": "Bounded summary of sandbox failure attributions (categories, hints, actionability)",
        "type": "Optional[Dict[str, Any]]",
        "reducer": "last_non_none",
        "introduced_in": "PR #9",
    },
}


class TestQualityFieldsSourceOfTruth:
    """Verify quality fields are registered correctly in both state types."""
    
    def test_all_quality_fields_documented(self):
        """All quality fields in the source of truth should have docs."""
        for field_name, field_info in QUALITY_STATE_FIELDS.items():
            assert "description" in field_info, f"{field_name} missing description"
            assert "type" in field_info, f"{field_name} missing type"
            assert "reducer" in field_info, f"{field_name} missing reducer"
            assert "introduced_in" in field_info, f"{field_name} missing introduced_in"
    
    def test_quality_fields_in_workflow_state(self):
        """All quality fields should exist in WorkflowState dataclass."""
        from integration_coworker.graph.state import WorkflowState
        
        dataclass_field_names = {f.name for f in dataclass_fields(WorkflowState)}
        
        for field_name in QUALITY_STATE_FIELDS.keys():
            assert field_name in dataclass_field_names, (
                f"Quality field '{field_name}' not in WorkflowState. "
                f"Add it to state.py"
            )
    
    def test_quality_fields_in_workflow_state_dict(self):
        """All quality fields should exist in WorkflowStateDict."""
        from integration_coworker.graph.state_v2 import WorkflowStateDict
        
        typed_dict_field_names = set(WorkflowStateDict.__annotations__.keys())
        
        for field_name in QUALITY_STATE_FIELDS.keys():
            assert field_name in typed_dict_field_names, (
                f"Quality field '{field_name}' not in WorkflowStateDict. "
                f"Add it to state_v2.py"
            )
    
    def test_quality_fields_have_correct_reducers(self):
        """Quality fields should use correct reducers in WorkflowStateDict."""
        from integration_coworker.graph.state_v2 import WorkflowStateDict
        from integration_coworker.graph.state_schema import (
            merge_dicts,
            last_non_none,
            unique_list,
        )
        
        # Map reducer names to functions
        reducer_map = {
            "merge_dicts": merge_dicts,
            "last_non_none": last_non_none,
            "unique_list": unique_list,
        }
        
        annotations = WorkflowStateDict.__annotations__
        
        for field_name, field_info in QUALITY_STATE_FIELDS.items():
            expected_reducer_name = field_info["reducer"]
            expected_reducer = reducer_map[expected_reducer_name]
            
            # Get the Annotated type from WorkflowStateDict
            field_annotation = annotations.get(field_name)
            if field_annotation is None:
                pytest.fail(f"Field {field_name} not in WorkflowStateDict")
            
            # Extract reducer from Annotated[type, reducer]
            if get_origin(field_annotation) is Annotated:
                args = get_args(field_annotation)
                if len(args) >= 2:
                    actual_reducer = args[1]
                    assert actual_reducer == expected_reducer, (
                        f"Field '{field_name}' has reducer {actual_reducer.__name__} "
                        f"but expected {expected_reducer_name}"
                    )


# =============================================================================
# Intentional Drift Detection (Canary Test)
# =============================================================================

class TestDriftDetectionCanary:
    """
    Canary tests that SHOULD pass normally but would fail if drift occurs.
    
    These tests document the expected behavior and serve as early warning.
    """
    
    def test_import_time_parity_check_ran(self):
        """
        Verify that import-time parity check in state_v2.py executed.
        
        If WorkflowStateDict was importable, the parity check passed.
        """
        # This import triggers _verify_at_import() in state_v2.py
        from integration_coworker.graph.state_v2 import WorkflowStateDict
        
        # If we got here, parity check passed
        assert WorkflowStateDict is not None
    
    def test_detect_missing_field_in_typed_dict(self):
        """
        Demonstrate that verify_field_parity catches missing fields.
        
        This is a meta-test: it creates a fake TypedDict with missing fields
        and verifies the checker catches it.
        """
        from typing import TypedDict
        from integration_coworker.graph.state_schema import verify_field_parity
        
        # Create a minimal TypedDict missing most fields
        class IncompleteStateDict(TypedDict):
            source_refs: list
            spec_refs: list
            task_description: str
        
        is_valid, issues = verify_field_parity(IncompleteStateDict)
        
        # Should fail - missing many fields
        assert not is_valid, "verify_field_parity should detect missing fields"
        assert len(issues) > 0, "Should report missing field issues"
        assert any("Missing from TypedDict" in issue for issue in issues)
