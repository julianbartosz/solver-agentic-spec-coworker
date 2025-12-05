"""
Phase 5: Codegen validation tests.

Validates that generated code artifacts:
1. Parse without syntax errors (AST validation)
2. Contain expected class/function names
3. Can be imported without errors
4. Follow spec-driven naming conventions
"""
import ast
import os
import sys
import importlib.util
import pytest
from pathlib import Path
from typing import List, Set

from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions
from integration_coworker.domain.models import CodeArtifact

# Tests that require real LLM output - skip if:
# 1. USE_MOCK_LLM is explicitly enabled, OR
# 2. OPENAI_API_KEY is not set or appears invalid (starts with sk-eS9 which is our invalid test key)
_openai_key = os.environ.get("OPENAI_API_KEY", "")
_is_mock_mode = os.environ.get("USE_MOCK_LLM", "").lower() in ("true", "1", "yes")
_has_valid_key = _openai_key and not _openai_key.startswith("sk-eS9")
requires_real_llm = pytest.mark.skipif(
    _is_mock_mode or not _has_valid_key,
    reason="Test requires real LLM output (valid OPENAI_API_KEY not set or mock mode enabled)"
)


class TestASTValidation:
    """Test that all generated code parses correctly."""
    
    @pytest.fixture
    def generated_artifacts(self, tmp_path) -> List[CodeArtifact]:
        """Generate code artifacts for testing."""
        fixture_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
        
        result = design_and_generate_integration(
            spec_refs=[str(fixture_path)],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        return result.code_artifacts
    
    def test_all_artifacts_parse_without_syntax_errors(self, generated_artifacts):
        """Every generated artifact should be valid Python syntax."""
        for artifact in generated_artifacts:
            try:
                ast.parse(artifact.content)
            except SyntaxError as e:
                pytest.fail(
                    f"Artifact {artifact.artifact_type} ({artifact.module_name}) has syntax error:\n"
                    f"  Line {e.lineno}: {e.msg}\n"
                    f"  Code:\n{artifact.content[:500]}..."
                )
    
    def test_client_artifact_has_class_definition(self, generated_artifacts):
        """Client artifacts should define a class."""
        client_artifacts = [a for a in generated_artifacts if a.artifact_type == "client"]
        
        assert len(client_artifacts) >= 1, "Should generate at least one client artifact"
        
        for artifact in client_artifacts:
            tree = ast.parse(artifact.content)
            class_names = [
                node.name for node in ast.walk(tree) 
                if isinstance(node, ast.ClassDef)
            ]
            
            assert len(class_names) >= 1, (
                f"Client artifact {artifact.module_name} should define at least one class, "
                f"found: {class_names}"
            )
            
            # Class name should match expected pattern (e.g., MockPaymentsClient)
            assert any("Client" in name for name in class_names), (
                f"Client class name should contain 'Client', found: {class_names}"
            )
    
    def test_flow_artifact_has_function_definition(self, generated_artifacts):
        """Flow artifacts should define a function ending in _flow."""
        flow_artifacts = [a for a in generated_artifacts if a.artifact_type == "flow"]
        
        assert len(flow_artifacts) >= 1, "Should generate at least one flow artifact"
        
        for artifact in flow_artifacts:
            tree = ast.parse(artifact.content)
            function_names = [
                node.name for node in ast.walk(tree) 
                if isinstance(node, ast.FunctionDef)
            ]
            
            assert len(function_names) >= 1, (
                f"Flow artifact {artifact.module_name} should define at least one function, "
                f"found: {function_names}"
            )
            
            # Should have a function ending in _flow
            flow_functions = [n for n in function_names if n.endswith("_flow")]
            assert len(flow_functions) >= 1, (
                f"Flow artifact should have a function ending in '_flow', found: {function_names}"
            )
    
    @requires_real_llm
    def test_test_artifact_has_test_function(self, generated_artifacts):
        """Test artifacts should define test functions."""
        test_artifacts = [a for a in generated_artifacts if a.artifact_type == "test"]
        
        assert len(test_artifacts) >= 1, "Should generate at least one test artifact"
        
        for artifact in test_artifacts:
            tree = ast.parse(artifact.content)
            function_names = [
                node.name for node in ast.walk(tree) 
                if isinstance(node, ast.FunctionDef)
            ]
            
            assert len(function_names) >= 1, (
                f"Test artifact {artifact.module_name} should define at least one function, "
                f"found: {function_names}"
            )
            
            # Should have a function starting with test_
            test_functions = [n for n in function_names if n.startswith("test_")]
            assert len(test_functions) >= 1, (
                f"Test artifact should have a function starting with 'test_', found: {function_names}"
            )


class TestImportValidation:
    """Test that generated code can be imported."""
    
    def test_client_can_be_imported(self, tmp_path):
        """Generated client module should be importable."""
        fixture_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
        
        result = design_and_generate_integration(
            spec_refs=[str(fixture_path)],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        # Find client file
        created_files = result.repo_changes.files_created()
        client_files = [c for c in created_files if "client" in c.rel_path.lower()]
        assert len(client_files) >= 1
        
        client_path = tmp_path / client_files[0].rel_path
        
        # Add src to path
        src_path = tmp_path / "src"
        sys.path.insert(0, str(src_path))
        
        try:
            # Try importing the module
            spec = importlib.util.spec_from_file_location(
                "test_client_module",
                str(client_path)
            )
            module = importlib.util.module_from_spec(spec)
            
            # This should not raise
            try:
                spec.loader.exec_module(module)
                
                # Verify class exists
                client_classes = [
                    name for name in dir(module)
                    if "Client" in name and not name.startswith("_")
                ]
                assert len(client_classes) >= 1, f"Should export a Client class, found: {dir(module)}"
            except ImportError as e:
                # Import errors are acceptable if they're about missing dependencies
                # (e.g., the runtime HTTP client)
                if "integration_coworker" not in str(e):
                    pytest.fail(f"Unexpected import error: {e}")
        finally:
            if str(src_path) in sys.path:
                sys.path.remove(str(src_path))
    
    def test_flow_can_be_imported(self, tmp_path):
        """Generated flow module should be importable."""
        fixture_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
        
        result = design_and_generate_integration(
            spec_refs=[str(fixture_path)],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        # Find flow file
        created_files = result.repo_changes.files_created()
        flow_files = [c for c in created_files if "flow" in c.rel_path.lower()]
        assert len(flow_files) >= 1
        
        flow_path = tmp_path / flow_files[0].rel_path
        
        # Add src to path
        src_path = tmp_path / "src"
        sys.path.insert(0, str(src_path))
        
        try:
            spec = importlib.util.spec_from_file_location(
                "test_flow_module",
                str(flow_path)
            )
            module = importlib.util.module_from_spec(spec)
            
            try:
                spec.loader.exec_module(module)
                
                # Verify flow function exists
                flow_functions = [
                    name for name in dir(module)
                    if name.endswith("_flow") and callable(getattr(module, name))
                ]
                assert len(flow_functions) >= 1, f"Should export a _flow function, found: {dir(module)}"
            except ImportError as e:
                # Import errors are acceptable if about missing internal dependencies
                if "integration_coworker" not in str(e) and "integrations" not in str(e):
                    pytest.fail(f"Unexpected import error: {e}")
        finally:
            if str(src_path) in sys.path:
                sys.path.remove(str(src_path))


class TestNamingConventions:
    """Test that generated code follows spec-driven naming conventions."""
    
    def test_client_class_matches_provider(self, tmp_path):
        """Client class name should be derived from provider code."""
        fixture_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
        
        result = design_and_generate_integration(
            spec_refs=[str(fixture_path)],
            task_description="Create checkout session",
            provider_code="mock_payments",  # Provider code
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        client_artifacts = [a for a in result.code_artifacts if a.artifact_type == "client"]
        assert len(client_artifacts) >= 1
        
        client_content = client_artifacts[0].content
        
        # Should have MockPaymentsClient (PascalCase provider + "Client")
        assert "MockPaymentsClient" in client_content or "Mock_PaymentsClient" in client_content, (
            "Client class should be named based on provider_code"
        )
    
    def test_flow_function_matches_task(self, tmp_path):
        """Flow function name should be derived from task description."""
        fixture_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
        
        result = design_and_generate_integration(
            spec_refs=[str(fixture_path)],
            task_description="Create checkout session",  # Task description
            provider_code="mock_payments",
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        flow_artifacts = [a for a in result.code_artifacts if a.artifact_type == "flow"]
        assert len(flow_artifacts) >= 1
        
        flow_content = flow_artifacts[0].content
        
        # Should have create_checkout_session_flow (snake_case task + "_flow")
        assert "create_checkout_session_flow" in flow_content, (
            "Flow function should be named based on task_description"
        )
    
    def test_module_names_match_provider(self, tmp_path):
        """Module names should be derived from provider code."""
        fixture_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
        
        result = design_and_generate_integration(
            spec_refs=[str(fixture_path)],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        # Check module names
        client_artifacts = [a for a in result.code_artifacts if a.artifact_type == "client"]
        flow_artifacts = [a for a in result.code_artifacts if a.artifact_type == "flow"]
        
        assert len(client_artifacts) >= 1
        assert len(flow_artifacts) >= 1
        
        # Client module should be provider_code (e.g., mock_payments)
        client_module = client_artifacts[0].module_name
        assert "mock_payments" in client_module, f"Client module should contain provider code: {client_module}"
        
        # Flow module should contain provider and task
        flow_module = flow_artifacts[0].module_name
        assert "mock_payments" in flow_module, f"Flow module should contain provider code: {flow_module}"


class TestSpecDrivenContent:
    """Test that generated code content is derived from spec."""
    
    def test_client_method_matches_endpoint(self, tmp_path):
        """Client method name should be derived from endpoint operationId or path."""
        fixture_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
        
        result = design_and_generate_integration(
            spec_refs=[str(fixture_path)],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        client_artifacts = [a for a in result.code_artifacts if a.artifact_type == "client"]
        assert len(client_artifacts) >= 1
        
        client_content = client_artifacts[0].content
        
        # The mock_payments spec has operationId: createCheckoutSession
        # So method should be create_checkout_session (snake_case)
        assert "create_checkout_session" in client_content or "createCheckoutSession" in client_content, (
            "Client method should be derived from endpoint operationId"
        )
    
    def test_client_uses_correct_endpoint_path(self, tmp_path):
        """Client should use the correct API endpoint path."""
        fixture_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
        
        result = design_and_generate_integration(
            spec_refs=[str(fixture_path)],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        client_artifacts = [a for a in result.code_artifacts if a.artifact_type == "client"]
        assert len(client_artifacts) >= 1
        
        client_content = client_artifacts[0].content
        
        # The mock_payments spec has path: /v1/checkout/sessions
        assert "/v1/checkout/sessions" in client_content or "checkout/sessions" in client_content, (
            "Client should use the correct endpoint path from spec"
        )
    
    def test_client_uses_correct_http_method(self, tmp_path):
        """Client should use the correct HTTP method."""
        fixture_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
        
        result = design_and_generate_integration(
            spec_refs=[str(fixture_path)],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(tmp_path),
            options=IntegrationOptions(
                repo_integration_enabled=True,
                dry_run=False,
            ),
        )
        
        client_artifacts = [a for a in result.code_artifacts if a.artifact_type == "client"]
        assert len(client_artifacts) >= 1
        
        client_content = client_artifacts[0].content.lower()
        
        # The mock_payments spec has POST for checkout sessions
        assert "post" in client_content, (
            "Client should use POST method for checkout session endpoint"
        )
