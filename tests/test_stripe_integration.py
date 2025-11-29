"""
End-to-end integration test for Stripe Payment Intents.

Proves multi-provider support by running the same workflow with Stripe spec
instead of mock_payments. This demonstrates the system is general, not tied
to a single toy provider.
"""
import pytest
from pathlib import Path
import tempfile
import shutil

from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions


@pytest.fixture
def stripe_spec():
    """Path to Stripe Payment Intents OpenAPI spec fixture."""
    return str(Path(__file__).parent / "fixtures" / "stripe_payment_intents_openapi.yaml")


@pytest.fixture
def temp_repo():
    """Create a temporary repo directory."""
    temp_dir = tempfile.mkdtemp(prefix="test_stripe_repo_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestStripePaymentIntents:
    """Tests for Stripe Payment Intents provider support."""

    def test_stripe_create_payment_intent_dry_run(self, stripe_spec):
        """Test end-to-end integration with Stripe spec in dry-run mode."""
        result = design_and_generate_integration(
            spec_refs=[stripe_spec],
            task_description="Create a payment intent for processing card payments",
            provider_code="stripe",
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
        assert result.task.provider_code == "stripe"
        # Task slug should be normalized
        assert "payment" in result.task.task_slug.lower()

        # Verify code artifacts were generated
        assert len(result.code_artifacts) == 3  # client, flow, test
        artifact_types = {a.artifact_type for a in result.code_artifacts}
        assert artifact_types == {"client", "flow", "test"}

        # Verify report contains Stripe-specific content
        assert result.report_markdown is not None
        assert "stripe" in result.report_markdown.lower()
        assert "payment" in result.report_markdown.lower()

    def test_stripe_extracts_payment_intents_endpoints(self, stripe_spec):
        """Test that Stripe spec extraction finds payment_intents endpoints."""
        result = design_and_generate_integration(
            spec_refs=[stripe_spec],
            task_description="Create payment intent",
            provider_code="stripe",
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
        )

        # Verify endpoints are in report
        assert "Endpoints:" in result.report_markdown
        assert "/v1/payment_intents" in result.report_markdown

    def test_stripe_generated_code_has_correct_names(self, stripe_spec, temp_repo):
        """Test that generated code uses Stripe-specific naming."""
        result = design_and_generate_integration(
            spec_refs=[stripe_spec],
            task_description="Create payment intent",
            provider_code="stripe",
            repo_root=str(temp_repo),
            options=IntegrationOptions(
                dry_run=False,
                repo_integration_enabled=True,
            ),
        )

        # Verify result
        assert result.run_id is not None
        assert result.repo_changes is not None

        # Check files were created
        created_files = result.repo_changes.files_created()
        assert len(created_files) > 0, "Should create files in repo"

        # Find client file and verify naming
        client_files = [c for c in created_files if "client" in c.rel_path.lower()]
        assert len(client_files) >= 1, "Should create client file"

        client_path = temp_repo / client_files[0].rel_path
        assert client_path.exists(), f"Client file should exist at {client_path}"

        client_content = client_path.read_text()
        # Verify Stripe-specific naming
        assert "StripeClient" in client_content or "Stripe" in client_content
        assert "IntegrationHttpClient" in client_content

    def test_stripe_workflow_has_standard_nodes(self, stripe_spec):
        """Test that Stripe workflow has the expected node structure."""
        result = design_and_generate_integration(
            spec_refs=[stripe_spec],
            task_description="Create payment intent",
            provider_code="stripe",
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
        )

        # Check report for workflow nodes
        assert "Workflow Steps:" in result.report_markdown
        # Should have validation and API call steps
        report_lower = result.report_markdown.lower()
        assert "validate" in report_lower or "validation" in report_lower
        assert "api" in report_lower or "call" in report_lower

    def test_stripe_policies_attached(self, stripe_spec):
        """Test that policies are attached to Stripe workflow."""
        result = design_and_generate_integration(
            spec_refs=[stripe_spec],
            task_description="Create payment intent",
            provider_code="stripe",
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
        )

        # Check report for policies
        assert "Policies" in result.report_markdown
        report_lower = result.report_markdown.lower()
        assert "auth" in report_lower
        assert "retry" in report_lower

    def test_stripe_provider_code_inference(self, stripe_spec):
        """Test that provider_code is inferred from Stripe spec filename."""
        result = design_and_generate_integration(
            spec_refs=[stripe_spec],
            task_description="Create payment intent",
            provider_code=None,  # Not specified - should infer from filename
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
        )

        # Should infer "stripe" from the spec filename
        assert result.task is not None
        # Provider code should contain "stripe" (could be "stripe_payment_intents")
        assert "stripe" in result.task.provider_code.lower()


class TestMultiProviderSupport:
    """Tests proving the system works with multiple providers."""

    def test_different_providers_generate_different_code(
        self, stripe_spec
    ):
        """Test that different providers produce different artifacts."""
        mock_spec = str(Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml")

        # Generate for mock_payments
        mock_result = design_and_generate_integration(
            spec_refs=[mock_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
        )

        # Generate for Stripe
        stripe_result = design_and_generate_integration(
            spec_refs=[stripe_spec],
            task_description="Create payment intent",
            provider_code="stripe",
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
        )

        # Both should succeed
        assert mock_result.run_id is not None
        assert stripe_result.run_id is not None

        # Provider codes should be different
        assert mock_result.task.provider_code == "mock_payments"
        assert stripe_result.task.provider_code == "stripe"

        # Client module names should be different
        mock_clients = [a for a in mock_result.code_artifacts if a.artifact_type == "client"]
        stripe_clients = [a for a in stripe_result.code_artifacts if a.artifact_type == "client"]

        assert len(mock_clients) == 1
        assert len(stripe_clients) == 1
        assert "mock_payments" in mock_clients[0].module_name.lower()
        assert "stripe" in stripe_clients[0].module_name.lower()

    def test_both_providers_extract_endpoints(self, stripe_spec):
        """Test that both providers correctly extract their endpoints."""
        mock_spec = str(Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml")

        mock_result = design_and_generate_integration(
            spec_refs=[mock_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
        )

        stripe_result = design_and_generate_integration(
            spec_refs=[stripe_spec],
            task_description="Create payment intent",
            provider_code="stripe",
            options=IntegrationOptions(dry_run=True, repo_integration_enabled=False),
        )

        # Mock payments should have checkout endpoints
        assert "/v1/checkout/sessions" in mock_result.report_markdown

        # Stripe should have payment_intents endpoints
        assert "/v1/payment_intents" in stripe_result.report_markdown
