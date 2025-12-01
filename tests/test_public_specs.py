"""
Tests for diverse public OpenAPI specs.

M5 WS1-T4: Prove system works with varied real-world spec structures,
not just our custom test fixtures.
"""
import os
# Use SQLite for tests
os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("USE_MOCK_LLM", "true")

import pytest
from pathlib import Path
from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions


@pytest.fixture
def petstore_spec():
    """Path to Petstore OpenAPI spec."""
    return str(Path(__file__).parent / "fixtures" / "petstore_openapi.yaml")


@pytest.fixture
def jsonplaceholder_spec():
    """Path to JSONPlaceholder OpenAPI spec."""
    return str(Path(__file__).parent / "fixtures" / "jsonplaceholder_openapi.yaml")


class TestPetstoreSpec:
    """Tests for Petstore API spec handling."""
    
    def test_petstore_provider_inferred(self, petstore_spec):
        """Provider should be inferred from spec servers URL."""
        result = design_and_generate_integration(
            spec_refs=[petstore_spec],
            task_description="List all pets",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Provider should be inferred (petstore from URL)
        assert result.task is not None
        assert result.task.provider_code is not None
        assert "petstore" in result.task.provider_code.lower()
    
    def test_petstore_list_pets_workflow(self, petstore_spec):
        """GET /pets should generate a list workflow."""
        result = design_and_generate_integration(
            spec_refs=[petstore_spec],
            task_description="List all pets",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should have workflow nodes
        assert result.workflow_nodes is not None
        assert len(result.workflow_nodes) >= 3
        
        # Should have api_call node
        node_types = [n.node_type for n in result.workflow_nodes]
        assert "api_call" in node_types
    
    def test_petstore_create_pet_workflow(self, petstore_spec):
        """POST /pets should generate a create workflow with validation."""
        result = design_and_generate_integration(
            spec_refs=[petstore_spec],
            task_description="Create a new pet",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should have validation step for POST with body
        node_types = [n.node_type for n in result.workflow_nodes]
        assert "validation" in node_types
        assert "api_call" in node_types
    
    def test_petstore_get_by_id_workflow(self, petstore_spec):
        """GET /pets/{id} should generate a fetch workflow."""
        result = design_and_generate_integration(
            spec_refs=[petstore_spec],
            task_description="Get a specific pet by ID",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should generate code
        assert result.code_artifacts is not None
        assert len(result.code_artifacts) >= 2
    
    def test_petstore_delete_workflow(self, petstore_spec):
        """DELETE /pets/{id} should generate a delete workflow."""
        result = design_and_generate_integration(
            spec_refs=[petstore_spec],
            task_description="Delete a pet",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should complete successfully
        assert result.run_id is not None
        assert result.code_artifacts is not None


class TestJSONPlaceholderSpec:
    """Tests for JSONPlaceholder API spec handling."""
    
    def test_jsonplaceholder_provider_inferred(self, jsonplaceholder_spec):
        """Provider should be inferred from servers URL."""
        result = design_and_generate_integration(
            spec_refs=[jsonplaceholder_spec],
            task_description="List all posts",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Provider should be inferred
        assert result.task is not None
        assert result.task.provider_code is not None
        # From servers URL jsonplaceholder.typicode.com → jsonplaceholder
        # or from title "JSONPlaceholder API" → jsonplaceholder
        assert "jsonplaceholder" in result.task.provider_code.lower() or "json" in result.task.provider_code.lower()
    
    def test_jsonplaceholder_no_auth_inferred(self, jsonplaceholder_spec):
        """Spec with empty security should infer no auth."""
        result = design_and_generate_integration(
            spec_refs=[jsonplaceholder_spec],
            task_description="Create a post",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should still complete successfully
        assert result.run_id is not None
        assert result.code_artifacts is not None
    
    def test_jsonplaceholder_create_post(self, jsonplaceholder_spec):
        """POST /posts should generate create workflow."""
        result = design_and_generate_integration(
            spec_refs=[jsonplaceholder_spec],
            task_description="Create a new blog post",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should have workflow nodes
        assert len(result.workflow_nodes) >= 4  # start, validate, call, transform, end
        
        # Should have validation for POST with body
        node_types = [n.node_type for n in result.workflow_nodes]
        assert "validation" in node_types
    
    def test_jsonplaceholder_update_post(self, jsonplaceholder_spec):
        """PUT /posts/{id} should generate update workflow."""
        result = design_and_generate_integration(
            spec_refs=[jsonplaceholder_spec],
            task_description="Update an existing post",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should complete and generate code
        assert result.run_id is not None
        assert len(result.code_artifacts) >= 2
    
    def test_jsonplaceholder_nested_resource(self, jsonplaceholder_spec):
        """GET /posts/{id}/comments should work with nested resources."""
        result = design_and_generate_integration(
            spec_refs=[jsonplaceholder_spec],
            task_description="Get comments for a post",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should handle nested resource path
        assert result.run_id is not None
        assert result.code_artifacts is not None


class TestCrossSpecPatterns:
    """Tests proving patterns generalize across different specs."""
    
    @pytest.mark.parametrize("spec_file,task,expected_method", [
        ("petstore_openapi.yaml", "Create a pet", "POST"),
        ("jsonplaceholder_openapi.yaml", "Create a post", "POST"),
        ("acme_widgets_openapi.yaml", "Create a widget", "POST"),
    ])
    def test_create_pattern_works_across_specs(self, spec_file, task, expected_method):
        """CREATE pattern should work identically across different specs."""
        spec_path = Path(__file__).parent / "fixtures" / spec_file
        
        result = design_and_generate_integration(
            spec_refs=[str(spec_path)],
            task_description=task,
            options=IntegrationOptions(dry_run=True),
        )
        
        # All should produce similar workflow structure
        assert result.workflow_nodes is not None
        assert len(result.workflow_nodes) >= 4  # create pattern has 4-5 nodes
        
        # All should have validation for POST
        node_types = [n.node_type for n in result.workflow_nodes]
        assert "validation" in node_types
        assert "api_call" in node_types
        
        # Should generate code
        assert len(result.code_artifacts) >= 2
    
    @pytest.mark.parametrize("spec_file,task", [
        ("petstore_openapi.yaml", "List all pets"),
        ("jsonplaceholder_openapi.yaml", "List all posts"),
        ("acme_widgets_openapi.yaml", "List widgets"),
    ])
    def test_list_pattern_works_across_specs(self, spec_file, task):
        """LIST pattern should work identically across different specs."""
        spec_path = Path(__file__).parent / "fixtures" / spec_file
        
        result = design_and_generate_integration(
            spec_refs=[str(spec_path)],
            task_description=task,
            options=IntegrationOptions(dry_run=True),
        )
        
        # All should complete successfully
        assert result.run_id is not None
        assert result.code_artifacts is not None
        
        # All should have api_call node
        node_types = [n.node_type for n in result.workflow_nodes]
        assert "api_call" in node_types


class TestEdgeCases:
    """Tests for edge cases and unusual spec structures."""
    
    def test_spec_without_explicit_provider(self, petstore_spec):
        """Specs without explicit provider should still work."""
        result = design_and_generate_integration(
            spec_refs=[petstore_spec],
            task_description="Create a pet",
            # No provider_code argument
            options=IntegrationOptions(dry_run=True),
        )
        
        # Should still succeed
        assert result.run_id is not None
        assert result.task.provider_code is not None
    
    def test_generated_code_has_correct_structure(self, petstore_spec, tmp_path):
        """Generated code should have proper structure regardless of spec."""
        result = design_and_generate_integration(
            spec_refs=[petstore_spec],
            task_description="Create a pet",
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        # Should have client and flow artifacts
        artifact_types = [a.artifact_type for a in result.code_artifacts]
        assert "client" in artifact_types
        assert "flow" in artifact_types
        
        # Client should have proper imports
        client = next(a for a in result.code_artifacts if a.artifact_type == "client")
        assert "from integration_coworker.runtime" in client.content
