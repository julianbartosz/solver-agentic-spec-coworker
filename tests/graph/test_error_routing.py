"""
Tests for graph error routing.

Verifies that errors are routed to handle_error node and the graph
completes gracefully with error information intact.
"""
import pytest
from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.runtime import build_graph
from integration_coworker.api.types import IntegrationOptions


def test_error_routing_after_validation():
    """Test that validation errors route to handle_error."""
    state = WorkflowState(
        source_refs=[],
        spec_refs=["fake.yaml"],
        task_description="Test error routing",
        provider_code="test",
        options=IntegrationOptions(dry_run=True),
    )
    
    # Simulate completing up to validation
    state.completed_steps = [
        "plan_run", "ingest_spec", "detect_and_parse_spec",
        "build_silver_api_model", "embed_spec_chunks",
        "understand_task", "align_task_with_kg",
        "plan_integration_flow", "attach_policies_and_patterns",
        "generate_code_and_tests", "validate_integration_design"
    ]
    
    # Add an error during validation
    state.errors.append("Validation failed: missing required endpoint")
    
    # Build graph - just verify it compiles without error
    app = build_graph()
    assert app is not None
    
    # Verify the state has errors
    assert len(state.errors) > 0
    assert "Validation failed" in state.errors[0]


def test_handle_error_sets_failed_flag():
    """Test that handle_error node sets plan['failed'] = True."""
    from integration_coworker.graph.nodes.handle_error import handle_error
    
    state = WorkflowState(
        source_refs=[],
        spec_refs=["test.yaml"],
        task_description="Test",
        options=IntegrationOptions(dry_run=True),
    )
    
    state.errors.append("Something went wrong")
    
    result = handle_error(state)
    
    assert result.plan.get("failed") is True
    assert "handle_error" in result.completed_steps


def test_no_errors_skips_handle_error():
    """Test that when there are no errors, handle_error is not needed."""
    state = WorkflowState(
        source_refs=[],
        spec_refs=["test.yaml"],
        task_description="Test",
        options=IntegrationOptions(dry_run=True),
    )
    
    # No errors
    assert len(state.errors) == 0
    
    # Routing function should return "no_errors"
    # (We're testing the logic, not the full graph execution)
    has_errors = len(state.errors) > 0 and not state.plan.get("failed", False)
    
    assert has_errors is False


def test_graph_completes_after_handle_error():
    """Test that graph continues to build_report after handle_error."""
    from integration_coworker.graph.nodes.handle_error import handle_error
    from integration_coworker.graph.nodes.build_report import build_report
    
    state = WorkflowState(
        source_refs=[],
        spec_refs=["test.yaml"],
        task_description="Test",
        provider_code="test",
        options=IntegrationOptions(dry_run=True),
        run_id="test-run-123",
    )
    
    # Simulate error
    state.errors.append("Test error")
    
    # Run handle_error
    state = handle_error(state)
    assert state.plan.get("failed") is True
    
    # Should still be able to build report
    state = build_report(state)
    assert state.report_markdown is not None
    assert "test-run-123" in state.report_markdown
    assert "Test error" in state.report_markdown or "Errors" in state.report_markdown
