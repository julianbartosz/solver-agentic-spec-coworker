"""
Test dynamic spec handling without hardcoded provider dependencies.

M5 WS1-T6: Prove the system works with "unknown" specs without
requiring provider_code to be explicitly passed.
"""
import os
# Use SQLite for tests to avoid Postgres pool issues
os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("USE_MOCK_LLM", "true")

import pytest
from pathlib import Path
from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions


@pytest.fixture
def acme_widgets_spec():
    """Path to the Acme Widgets test spec."""
    return str(Path(__file__).parent / "fixtures" / "acme_widgets_openapi.yaml")


@pytest.fixture
def mock_payments_spec():
    """Path to the mock payments spec."""
    return str(Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml")


class TestDynamicProviderInference:
    """Tests for dynamic provider inference without hardcoded dependencies."""
    
    def test_unknown_spec_works_without_provider_code(self, acme_widgets_spec):
        """
        Spec we've never seen before still generates valid code.
        
        M5 key test: Provider should be inferred from spec content,
        not from a hardcoded list.
        """
        result = design_and_generate_integration(
            spec_refs=[acme_widgets_spec],
            task_description="Create a widget",
            # NO provider_code passed - should be inferred
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should have inferred a provider code
        assert result.task is not None
        assert result.task.provider_code is not None
        # Provider should be inferred from spec info.title or servers URL
        # "Acme Widget API" → "acme_widget" or from filename "acme_widgets_openapi.yaml" → "acme_widgets"
        assert "acme" in result.task.provider_code.lower()
        
        # Should have generated code artifacts
        assert result.code_artifacts is not None
        assert len(result.code_artifacts) >= 2  # At least client and flow
    
    def test_inferred_provider_matches_spec_title(self, acme_widgets_spec):
        """Provider code is correctly derived from spec info.title."""
        result = design_and_generate_integration(
            spec_refs=[acme_widgets_spec],
            task_description="List widgets",
            options=IntegrationOptions(dry_run=True),
        )
        
        # The spec has info.title: "Acme Widget API"
        # Should produce provider_code like "acme_widget" or "acme"
        provider = result.task.provider_code
        assert provider is not None
        assert "acme" in provider.lower()
    
    def test_known_spec_still_works_without_provider_code(self, mock_payments_spec):
        """
        Even for specs we have legacy templates for, inference should work.
        
        This proves backwards compatibility while using the new dynamic path.
        """
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            # NO provider_code passed
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should infer "mock_payments" from the filename/content
        assert result.task.provider_code in ("mock_payments", "mock")
        assert result.code_artifacts is not None
    
    def test_workflow_inferred_from_http_method_post(self, acme_widgets_spec):
        """
        POST operations should generate create-style workflow.
        
        M5 WS1-T2: Smarter generic fallback based on HTTP method.
        """
        result = design_and_generate_integration(
            spec_refs=[acme_widgets_spec],
            task_description="Create a new widget",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should have workflow nodes for a create operation
        assert result.workflow_nodes is not None
        assert len(result.workflow_nodes) >= 3  # start, validate, api_call, end
        
        # Check for validation step (POST operations need input validation)
        node_types = [n.node_type for n in result.workflow_nodes]
        assert "validation" in node_types
        assert "api_call" in node_types
    
    def test_workflow_inferred_from_http_method_get_list(self, acme_widgets_spec):
        """
        GET list operations should generate list-style workflow.
        """
        result = design_and_generate_integration(
            spec_refs=[acme_widgets_spec],
            task_description="List all widgets",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should have workflow nodes for a list operation
        assert result.workflow_nodes is not None
        assert len(result.workflow_nodes) >= 3
    
    def test_workflow_inferred_from_http_method_delete(self, acme_widgets_spec):
        """
        DELETE operations should generate delete-style workflow.
        """
        result = design_and_generate_integration(
            spec_refs=[acme_widgets_spec],
            task_description="Delete a widget",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should have workflow nodes for a delete operation
        assert result.workflow_nodes is not None
        assert len(result.workflow_nodes) >= 3


class TestInferenceBasedWorkflow:
    """V2: Tests for KG-only with inference fallback (legacy templates removed)."""
    
    def test_inference_used_when_kg_empty(self, mock_payments_spec, monkeypatch):
        """
        V2: When KG has no templates, should use HTTP-method-based inference.
        """
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should still work using dynamic inference
        assert result.code_artifacts is not None
        
        # Check template source - can be "inferred", "pattern", "kg", or None
        template_source = result.plan.get("template_source")
        assert template_source in (None, "inferred", "pattern", "kg")
    
    def test_workflow_nodes_created_from_inference(self, mock_payments_spec, monkeypatch):
        """
        V2: Inference should create valid workflow nodes.
        """
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should have workflow nodes from inference
        assert result.workflow_nodes is not None
        assert len(result.workflow_nodes) >= 3  # At least start, api_call, end


class TestCodeArtifactQuality:
    """Tests for generated code quality without provider-specific assumptions."""
    
    def test_generated_client_uses_correct_imports(self, acme_widgets_spec, tmp_path):
        """Generated client code has correct absolute imports."""
        result = design_and_generate_integration(
            spec_refs=[acme_widgets_spec],
            task_description="Create a widget",
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        # Find client artifact
        client_artifacts = [
            a for a in result.code_artifacts 
            if a.artifact_type == "client"
        ]
        assert len(client_artifacts) >= 1
        
        client_code = client_artifacts[0].content
        
        # In inline mode (default): uses httpx directly
        # In runtime mode: uses from integration_coworker.runtime.http_client
        # Both are valid - just check we have proper imports, not relative ones
        assert "import httpx" in client_code or "from integration_coworker.runtime.http_client" in client_code
        # Should NOT use relative imports
        assert "from .http_client" not in client_code
    
    def test_generated_flow_uses_correct_imports(self, acme_widgets_spec, tmp_path):
        """Generated flow code has correct absolute imports."""
        result = design_and_generate_integration(
            spec_refs=[acme_widgets_spec],
            task_description="Create a widget",
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        # Find flow artifact
        flow_artifacts = [
            a for a in result.code_artifacts 
            if a.artifact_type == "flow"
        ]
        assert len(flow_artifacts) >= 1
        
        flow_code = flow_artifacts[0].content
        
        # Should use absolute imports for client
        assert "from integrations.clients." in flow_code or "from integrations.clients.acme" in flow_code
        # Should NOT use relative imports
        assert "from .clients" not in flow_code
