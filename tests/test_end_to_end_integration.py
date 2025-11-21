"""
End-to-end integration test for Phase 2 vertical slice.

Tests the complete workflow from spec ingestion through code generation.
"""
import pytest
from pathlib import Path
import tempfile
import shutil

from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions


@pytest.fixture
def mock_payments_spec():
    """Path to mock_payments OpenAPI spec fixture."""
    return str(Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml")


@pytest.fixture
def temp_repo():
    """Create a temporary repo directory."""
    temp_dir = tempfile.mkdtemp(prefix="test_repo_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_end_to_end_dry_run(mock_payments_spec):
    """Test end-to-end integration with dry_run=True."""
    result = design_and_generate_integration(
        spec_refs=[mock_payments_spec],
        task_description="Create checkout session",
        provider_code="mock_payments",
        repo_root=None,
        repo_profile=None,
        options=IntegrationOptions(
            dry_run=True,
            repo_integration_enabled=False,
        ),
    )
    
    # Verify result structure
    assert result.run_id is not None
    assert result.task is not None
    assert result.task.task_slug == "mock_payments_create_checkout_session"
    assert result.task.provider_code == "mock_payments"
    
    # Verify code artifacts were generated
    assert len(result.code_artifacts) == 3  # client, flow, test
    artifact_types = {a.artifact_type for a in result.code_artifacts}
    assert artifact_types == {"client", "flow", "test"}
    
    # Verify report was generated
    assert result.report_markdown is not None
    assert "mock_payments" in result.report_markdown
    assert "checkout" in result.report_markdown.lower()


def test_end_to_end_with_repo_integration(mock_payments_spec, temp_repo):
    """Test end-to-end integration with repo file writes."""
    result = design_and_generate_integration(
        spec_refs=[mock_payments_spec],
        task_description="Create checkout session",
        provider_code="mock_payments",
        repo_root=str(temp_repo),
        repo_profile=None,  # Will default to SUBATOMIC_MOCK_PROFILE
        options=IntegrationOptions(
            dry_run=False,
            repo_integration_enabled=True,
        ),
    )
    
    # Verify result
    assert result.run_id is not None
    assert result.task is not None
    
    # Verify repo_changes
    assert result.repo_changes is not None
    assert len(result.repo_changes.changes) == 3
    
    # Verify files were actually created
    created_files = result.repo_changes.files_created()
    assert len(created_files) > 0
    
    for change in created_files:
        file_path = temp_repo / change.rel_path
        assert file_path.exists(), f"File not created: {change.rel_path}"
        content = file_path.read_text()
        assert len(content) > 0, f"File is empty: {change.rel_path}"
    
    # Verify client file contains expected content
    client_files = [c for c in created_files if "client" in c.rel_path]
    assert len(client_files) == 1
    client_path = temp_repo / client_files[0].rel_path
    client_content = client_path.read_text()
    assert "MockPaymentsClient" in client_content or "MockpaymentsClient" in client_content or "Mock_PaymentsClient" in client_content
    assert "create_checkout_session" in client_content
    assert "IntegrationHttpClient" in client_content


def test_provider_code_inference(mock_payments_spec):
    """Test that provider_code is inferred from spec_ref."""
    result = design_and_generate_integration(
        spec_refs=[mock_payments_spec],
        task_description="Create checkout session",
        provider_code=None,  # Not specified
        repo_root=None,
        options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
    )
    
    # Should infer "mock_payments" from the spec filename
    assert result.task is not None
    assert result.task.provider_code == "mock_payments"


def test_silver_model_extraction(mock_payments_spec):
    """Test that Silver API model is correctly extracted."""
    result = design_and_generate_integration(
        spec_refs=[mock_payments_spec],
        task_description="Create checkout session",
        options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
    )
    
    # Should have extracted endpoints from OpenAPI spec
    # mock_payments spec has 2 endpoints: POST /v1/checkout/sessions and GET /v1/checkout/sessions/{id}
    assert len(result.code_artifacts) > 0
    
    # Check report for evidence of Silver extraction
    assert "Endpoints:" in result.report_markdown
    assert "/v1/checkout/sessions" in result.report_markdown


def test_workflow_creation(mock_payments_spec):
    """Test that workflow nodes and edges are created."""
    result = design_and_generate_integration(
        spec_refs=[mock_payments_spec],
        task_description="Create checkout session",
        options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
    )
    
    # Check report for workflow information
    assert "Workflow Steps:" in result.report_markdown
    assert "validate" in result.report_markdown.lower() or "Validate" in result.report_markdown
    assert "call" in result.report_markdown.lower() or "Call" in result.report_markdown


def test_policy_attachment(mock_payments_spec):
    """Test that policies are attached to the workflow."""
    result = design_and_generate_integration(
        spec_refs=[mock_payments_spec],
        task_description="Create checkout session",
        options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
    )
    
    # Check report for policies
    assert "Policies" in result.report_markdown
    assert "AUTH" in result.report_markdown or "auth" in result.report_markdown
    assert "RETRY" in result.report_markdown or "retry" in result.report_markdown


def test_error_handling_missing_spec():
    """Test error handling when spec file doesn't exist."""
    result = design_and_generate_integration(
        spec_refs=["/nonexistent/spec.yaml"],
        task_description="Create something",
        options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
    )
    
    # Should complete but with errors
    assert result.run_id is not None
    # Errors should be present in the workflow (check via report or result inspection)
    assert "not found" in result.report_markdown.lower() or "error" in result.report_markdown.lower()
