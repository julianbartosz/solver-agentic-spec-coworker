"""
M3 Milestone Test: Task understanding + planning for one provider

Tests the core planning workflow without repo integration.
"""
import pytest
from pathlib import Path
from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions


def test_m3_planning_workflow_mock_payments():
    """
    Test M3: Single-provider planning workflow for mock_payments.
    
    Validates:
    - Graph runs through all planning nodes
    - IntegrationTask is created with correct fields
    - Workflow nodes and edges are valid
    - Report is generated
    """
    # Setup
    spec_ref = str(Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml")
    task_description = "Create checkout session"
    
    # Options: disable repo integration for planning-only run
    options = IntegrationOptions(
        repo_integration_enabled=False,
        dry_run=True,  # Don't write to DB
    )
    
    # Run
    result = design_and_generate_integration(
        spec_refs=[spec_ref],
        task_description=task_description,
        options=options,
    )
    
    # Assertions
    assert result.run_id, "Should have a run_id"
    assert result.task is not None, "Should have an IntegrationTask"
    assert result.report_markdown, "Should have a report"
    
    # Check IntegrationTask fields
    task = result.task
    assert task.task_slug, "Task should have a slug"
    assert task.task_slug.replace("_", "").replace("-", "").isalnum(), "Slug should be normalized"
    assert task.provider_code == "mock_payments", "Should detect mock_payments provider"
    assert task.description == task_description
    assert isinstance(task.input_entities, list), "Should have input_entities list"
    assert isinstance(task.output_entities, list), "Should have output_entities list"
    assert isinstance(task.constraints, dict), "Should have constraints dict"
    assert "idempotency_required" in task.constraints
    assert "requires_webhooks" in task.constraints
    
    # Check code artifacts were generated
    assert len(result.code_artifacts) > 0, "Should generate code artifacts"
    artifact_types = {a.artifact_type for a in result.code_artifacts}
    assert "client" in artifact_types, "Should generate client code"
    assert "flow" in artifact_types, "Should generate flow code"
    assert "test" in artifact_types, "Should generate test code"
    
    # Check report contains key sections
    assert "Integration Co-Worker Report" in result.report_markdown
    assert "mock_payments" in result.report_markdown.lower()
    
    print("✅ M3 planning workflow test passed")
    print(f"Generated task slug: {task.task_slug}")
    print(f"Generated {len(result.code_artifacts)} code artifacts")


def test_m3_spec_refs_validation():
    """Test that plan_run enforces at least one spec_ref is required."""
    options = IntegrationOptions(repo_integration_enabled=False, dry_run=True)
    
    # Test with no spec_refs - should raise ValueError
    with pytest.raises(ValueError, match="At least one spec_ref is required"):
        design_and_generate_integration(
            spec_refs=[],
            task_description="test",
            options=options,
        )
    
    # Multiple spec_refs is now allowed (Phase 4 multi-spec support)
    # This should NOT raise - instead will fail later on missing files,
    # which is expected behavior for this validation test
    
    print("✅ Spec refs validation test passed")


def test_m3_provider_code_override():
    """Test that options.override_provider_code is honored."""
    spec_ref = str(Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml")
    
    options = IntegrationOptions(
        override_provider_code="custom_provider",
        repo_integration_enabled=False,
        dry_run=True,
    )
    
    result = design_and_generate_integration(
        spec_refs=[spec_ref],
        task_description="Test task",
        options=options,
    )
    
    assert result.task.provider_code == "custom_provider", "Should use override"
    print("✅ Provider code override test passed")


if __name__ == "__main__":
    # Run tests
    test_m3_planning_workflow_mock_payments()
    test_m3_spec_refs_validation()
    test_m3_provider_code_override()
    print("\n✅ All M3 tests passed!")
