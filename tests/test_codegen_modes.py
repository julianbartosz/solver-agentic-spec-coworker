"""
Tests for codegen mode functionality (inline vs runtime).

V2.1: Verifies that:
- inline mode produces fully self-contained code (no external imports)
- runtime mode uses integration_coworker_runtime package
"""
import ast
import pytest
from unittest.mock import MagicMock, patch

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import Endpoint, CodeArtifact
from integration_coworker.api.types import IntegrationOptions


@pytest.fixture
def mock_state_inline():
    """Create a WorkflowState configured for inline mode."""
    state = WorkflowState(
        source_refs=["https://api.example.com/v1/openapi.json"],
        spec_refs=["https://api.example.com/v1/openapi.json"],
        task_description="Get user by ID",
        provider_code="example_api",
        options=IntegrationOptions(policy_mode="inline"),
    )
    state.openapi_spec = {
        "servers": [{"url": "https://api.example.com/v1"}],
        "paths": {
            "/users/{id}": {
                "get": {
                    "operationId": "getUser",
                    "summary": "Get a user by ID",
                }
            }
        }
    }
    state.endpoint_bindings = [
        {"endpoint_id": 1, "node_id": 1}
    ]
    state.endpoints = [
        Endpoint(
            id=1,
            source_system_id=None,
            spec_document_id=1,
            path="/users/{id}",
            method="GET",
            operation_id="getUser",
            summary="Get a user by ID",
            description=None,
            request_schema_id=None,
            response_schema_id=None,
            auth_required=True,
        )
    ]
    return state


@pytest.fixture
def mock_state_runtime():
    """Create a WorkflowState configured for runtime mode."""
    state = WorkflowState(
        source_refs=["https://api.stripe.com/v1/openapi.json"],
        spec_refs=["https://api.stripe.com/v1/openapi.json"],
        task_description="Create a payment",
        provider_code="stripe",
        options=IntegrationOptions(policy_mode="runtime"),
    )
    state.openapi_spec = {
        "servers": [{"url": "https://api.stripe.com/v1"}],
        "paths": {
            "/charges": {
                "post": {
                    "operationId": "createCharge",
                    "summary": "Create a charge",
                }
            }
        }
    }
    state.endpoint_bindings = [
        {"endpoint_id": 1, "node_id": 1}
    ]
    state.endpoints = [
        Endpoint(
            id=1,
            source_system_id=None,
            spec_document_id=1,
            path="/charges",
            method="POST",
            operation_id="createCharge",
            summary="Create a charge",
            description=None,
            request_schema_id=None,
            response_schema_id=None,
            auth_required=True,
        )
    ]
    return state


class TestInlineCodeGeneration:
    """Tests for inline (standalone) code generation mode."""
    
    def test_inline_client_has_no_integration_coworker_imports(self, mock_state_inline):
        """Verify inline mode generates code without integration_coworker imports."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_inline_client_code,
        )
        
        endpoint = mock_state_inline.endpoints[0]
        code = _generate_inline_client_code(
            state=mock_state_inline,
            provider_code="example_api",
            client_class="ExampleApiClient",
            method_name="get_user",
            endpoint=endpoint,
            base_url="https://api.example.com/v1",
        )
        
        # Should NOT contain integration_coworker imports
        assert "from integration_coworker" not in code
        assert "import integration_coworker" not in code
        
        # Should contain httpx import
        assert "import httpx" in code
        
        # Should define IntegrationError locally
        assert "class IntegrationError(Exception):" in code
    
    def test_inline_client_is_valid_python(self, mock_state_inline):
        """Verify inline mode generates valid Python syntax."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_inline_client_code,
        )
        
        endpoint = mock_state_inline.endpoints[0]
        code = _generate_inline_client_code(
            state=mock_state_inline,
            provider_code="example_api",
            client_class="ExampleApiClient",
            method_name="get_user",
            endpoint=endpoint,
            base_url="https://api.example.com/v1",
        )
        
        # Should parse without syntax errors
        try:
            ast.parse(code)
        except SyntaxError as e:
            pytest.fail(f"Generated code has syntax error: {e}\n\nCode:\n{code}")
    
    def test_inline_client_is_self_contained(self, mock_state_inline):
        """Verify inline mode includes all necessary components."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_inline_client_code,
        )
        
        endpoint = mock_state_inline.endpoints[0]
        code = _generate_inline_client_code(
            state=mock_state_inline,
            provider_code="example_api",
            client_class="ExampleApiClient",
            method_name="get_user",
            endpoint=endpoint,
            base_url="https://api.example.com/v1",
        )
        
        # Should have class definition
        assert "class ExampleApiClient:" in code
        
        # Should have __init__ method
        assert "def __init__(" in code
        
        # Should have the main method
        assert "def get_user(" in code
        
        # Should have close/context manager support
        assert "def close(" in code
        assert "def __enter__(" in code
        assert "def __exit__(" in code


class TestRuntimeCodeGeneration:
    """Tests for runtime mode code generation."""
    
    def test_runtime_client_uses_runtime_package(self, mock_state_runtime):
        """Verify runtime mode uses integration_coworker_runtime package."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_runtime_client_code,
        )
        
        endpoint = mock_state_runtime.endpoints[0]
        code = _generate_runtime_client_code(
            state=mock_state_runtime,
            provider_code="stripe",
            client_class="StripeClient",
            method_name="create_charge",
            endpoint=endpoint,
            base_url="https://api.stripe.com/v1",
        )
        
        # Should import from integration_coworker_runtime
        assert "from integration_coworker_runtime" in code
        
        # Should NOT use old integration_coworker.runtime path
        assert "from integration_coworker.runtime" not in code
    
    def test_runtime_client_is_valid_python(self, mock_state_runtime):
        """Verify runtime mode generates valid Python syntax."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_runtime_client_code,
        )
        
        endpoint = mock_state_runtime.endpoints[0]
        code = _generate_runtime_client_code(
            state=mock_state_runtime,
            provider_code="stripe",
            client_class="StripeClient",
            method_name="create_charge",
            endpoint=endpoint,
            base_url="https://api.stripe.com/v1",
        )
        
        # Should parse without syntax errors
        try:
            ast.parse(code)
        except SyntaxError as e:
            pytest.fail(f"Generated code has syntax error: {e}\n\nCode:\n{code}")
    
    def test_runtime_client_extends_integration_client(self, mock_state_runtime):
        """Verify runtime clients extend IntegrationClient."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_runtime_client_code,
        )
        
        endpoint = mock_state_runtime.endpoints[0]
        code = _generate_runtime_client_code(
            state=mock_state_runtime,
            provider_code="stripe",
            client_class="StripeClient",
            method_name="create_charge",
            endpoint=endpoint,
            base_url="https://api.stripe.com/v1",
        )
        
        # Should extend IntegrationClient
        assert "class StripeClient(IntegrationClient):" in code


class TestFlowCodeGeneration:
    """Tests for flow code generation in both modes."""
    
    def test_inline_flow_imports_from_client(self, mock_state_inline):
        """Verify inline flow imports IntegrationError from client module."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_flow_code,
        )
        
        code = _generate_flow_code(
            state=mock_state_inline,
            provider_code="example_api",
            task_slug="get_user",
            client_class="ExampleApiClient",
            client_import_module="integrations.clients.example_api",
            method_name="get_user",
            flow_function="get_user_flow",
        )
        
        # Should import IntegrationError from client module (not runtime)
        assert "from integrations.clients.example_api import IntegrationError" in code
        
        # Should NOT import from integration_coworker.runtime
        assert "from integration_coworker.runtime" not in code
    
    def test_runtime_flow_imports_from_runtime_package(self, mock_state_runtime):
        """Verify runtime flow imports from integration_coworker_runtime."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_flow_code,
        )
        
        code = _generate_flow_code(
            state=mock_state_runtime,
            provider_code="stripe",
            task_slug="create_charge",
            client_class="StripeClient",
            client_import_module="integrations.clients.stripe",
            method_name="create_charge",
            flow_function="create_charge_flow",
        )
        
        # Should import IntegrationError from runtime package
        assert "from integration_coworker_runtime import IntegrationError" in code
    
    def test_flow_is_valid_python_inline(self, mock_state_inline):
        """Verify inline flow generates valid Python."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_flow_code,
        )
        
        code = _generate_flow_code(
            state=mock_state_inline,
            provider_code="example_api",
            task_slug="get_user",
            client_class="ExampleApiClient",
            client_import_module="integrations.clients.example_api",
            method_name="get_user",
            flow_function="get_user_flow",
        )
        
        try:
            ast.parse(code)
        except SyntaxError as e:
            pytest.fail(f"Generated flow code has syntax error: {e}\n\nCode:\n{code}")
    
    def test_flow_is_valid_python_runtime(self, mock_state_runtime):
        """Verify runtime flow generates valid Python."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_flow_code,
        )
        
        code = _generate_flow_code(
            state=mock_state_runtime,
            provider_code="stripe",
            task_slug="create_charge",
            client_class="StripeClient",
            client_import_module="integrations.clients.stripe",
            method_name="create_charge",
            flow_function="create_charge_flow",
        )
        
        try:
            ast.parse(code)
        except SyntaxError as e:
            pytest.fail(f"Generated flow code has syntax error: {e}\n\nCode:\n{code}")


class TestPolicyModeSelection:
    """Tests for policy mode selection logic."""
    
    @pytest.mark.asyncio
    async def test_client_code_uses_correct_generator(self, mock_state_inline, mock_state_runtime):
        """Verify _generate_client_code delegates to correct generator."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_client_code,
        )
        
        # Inline mode
        endpoint = mock_state_inline.endpoints[0]
        inline_code = await _generate_client_code(
            state=mock_state_inline,
            provider_code="example_api",
            client_class="ExampleApiClient",
            method_name="get_user",
            endpoint=endpoint,
            base_url="https://api.example.com/v1",
        )
        
        # Should be standalone (no integration_coworker imports)
        assert "from integration_coworker" not in inline_code
        
        # Runtime mode
        endpoint = mock_state_runtime.endpoints[0]
        runtime_code = await _generate_client_code(
            state=mock_state_runtime,
            provider_code="stripe",
            client_class="StripeClient",
            method_name="create_charge",
            endpoint=endpoint,
            base_url="https://api.stripe.com/v1",
        )
        
        # Should use runtime package
        assert "from integration_coworker_runtime" in runtime_code
