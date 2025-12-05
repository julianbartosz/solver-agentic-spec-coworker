"""
P1.4: Generated code execution test.

Proves end-to-end functionality by:
1. Generating client + flow code into a temp repo
2. Importing the generated modules
3. Executing the flow with mocked HTTP
4. Verifying correct behavior
"""
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions

# Tests that require real LLM output (mock LLM produces placeholder code with issues)
# Skip if USE_MOCK_LLM is set OR if OPENAI_API_KEY appears invalid
_openai_key = os.environ.get("OPENAI_API_KEY", "")
_is_mock_mode = os.environ.get("USE_MOCK_LLM", "").lower() in ("true", "1", "yes")
_has_valid_key = _openai_key and not _openai_key.startswith("sk-eS9")
requires_real_llm = pytest.mark.skipif(
    _is_mock_mode or not _has_valid_key,
    reason="Test requires real LLM output (valid OPENAI_API_KEY not set or mock mode enabled)"
)


@requires_real_llm
def test_generated_mock_payments_flow_executes(tmp_path, monkeypatch):
    """
    Test that generated code can be imported and executed successfully.
    
    This proves the entire M4 pipeline works end-to-end:
    - Spec parsing
    - Silver/Gold model building
    - Code generation
    - Repo file writes
    - Generated code execution
    """
    # Set API_KEY env var for generated client (it validates this on init)
    monkeypatch.setenv("API_KEY", "test-key-12345")
    
    # 1. Generate integration code into tmp_path
    fixture_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
    
    result = design_and_generate_integration(
        spec_refs=[str(fixture_path)],
        task_description="Create checkout session",
        provider_code="mock_payments",
        repo_root=str(tmp_path),
        repo_profile=None,
        options=IntegrationOptions(
            repo_integration_enabled=True,
            dry_run=False,  # Actually write files
        ),
    )
    
    # Verify code was generated
    assert result.code_artifacts is not None
    assert len(result.code_artifacts) >= 2, "Should generate at least client and flow"
    
    # Verify repo changes include created files
    assert result.repo_changes is not None
    created_files = result.repo_changes.files_created()
    assert len(created_files) > 0, "Should create files in repo"
    
    # Find client and flow files
    client_files = [c for c in created_files if "client" in c.rel_path.lower()]
    flow_files = [c for c in created_files if "flow" in c.rel_path.lower()]
    
    assert len(client_files) >= 1, "Should create client file"
    assert len(flow_files) >= 1, "Should create flow file"
    
    # Verify files exist on disk
    client_path = tmp_path / client_files[0].rel_path
    flow_path = tmp_path / flow_files[0].rel_path
    
    assert client_path.exists(), f"Client file should exist at {client_path}"
    assert flow_path.exists(), f"Flow file should exist at {flow_path}"
    
    # 2. Add tmp_path/src to import path (generated code is under src/)
    src_path = tmp_path / "src"
    sys.path.insert(0, str(src_path))
    
    # Get actual flow module name from path
    flow_rel_path = flow_files[0].rel_path  # e.g. "src/integrations/flows/mock_payments_create_checkout_session.py"
    flow_module_name = Path(flow_rel_path).stem  # e.g. "mock_payments_create_checkout_session"
    
    # M5: Import path fix is no longer needed - codegen now uses absolute imports
    # The flow imports client using integrations.clients.<provider> which works with PYTHONPATH=src
    
    try:
        # 3. Import generated modules dynamically
        import importlib
        from integrations.clients.mock_payments import MockPaymentsClient
        
        # Import flow module dynamically based on actual path
        flow_module = importlib.import_module(f"integrations.flows.{flow_module_name}")
        
        # Find the flow function (any function ending with _flow)
        flow_func = None
        for name in dir(flow_module):
            if name.endswith("_flow") and callable(getattr(flow_module, name)):
                flow_func = getattr(flow_module, name)
                break
        
        assert flow_func is not None, f"Should find a flow function in {flow_module_name}"
        
        # 4. Mock HTTP layer
        fake_response_body = {
            "id": "sess_123abc",
            "object": "checkout_session",
            "amount": 1000,
            "currency": "usd",
            "status": "open",
            "checkout_url": "https://checkout.example.com/sess_123abc",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        }
        
        # Create a fake response object that mimics httpx.Response
        class FakeResponse:
            def __init__(self, body, status=200):
                self._body = body
                self.status_code = status
            
            def json(self):
                return self._body
            
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise Exception(f"HTTP {self.status_code}")
        
        def fake_request(self, method, path, *, params=None, json=None, headers=None):
            """Mock HTTP request that returns fake checkout session."""
            # Verify correct method and path
            assert method.upper() == "POST"
            assert "/v1/checkout/sessions" in path
            
            return FakeResponse(fake_response_body)
        
        # 5. Execute generated flow with mocked HTTP
        # New flow signature uses payload dict instead of individual params
        with patch("integration_coworker.runtime.http_client.IntegrationHttpClient.request", new=fake_request):
            result = flow_func(
                api_key="test-key-12345",
                payload={
                    "amount": 1000,
                    "currency": "usd",
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            )
            
            # 6. Verify result
            assert result is not None, "Flow should return a result"
            assert isinstance(result, dict), "Result should be a dictionary"
            
            # Check response contains data (structure may vary)
            # New flows return {"success": True, "data": <response>}
            if "data" in result:
                data = result["data"]
                assert data.get("id") == "sess_123abc" or data.get("session_id") == "sess_123abc"
            else:
                # Legacy format
                assert result.get("session_id") == "sess_123abc" or result.get("id") == "sess_123abc"
    
    finally:
        # Clean up import path
        src_path_str = str(tmp_path / "src")
        if src_path_str in sys.path:
            sys.path.remove(src_path_str)


def test_generated_code_has_correct_structure(tmp_path):
    """
    Verify that generated code files have expected structure without executing.
    
    This is a lighter test that checks code structure but doesn't require
    full execution.
    """
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
    
    # Find generated client file
    created_files = result.repo_changes.files_created()
    client_files = [c for c in created_files if "client" in c.rel_path.lower()]
    
    assert len(client_files) >= 1
    client_path = tmp_path / client_files[0].rel_path
    client_content = client_path.read_text()
    
    # Verify client structure (supports both inline and runtime modes)
    assert "class " in client_content, "Should define a client class"
    # Inline mode uses httpx.Client directly, runtime mode uses IntegrationHttpClient
    has_http_client = (
        "IntegrationHttpClient" in client_content or 
        "httpx.Client" in client_content or
        "_client = httpx.Client" in client_content
    )
    assert has_http_client, "Should use HTTP client (inline httpx.Client or shared IntegrationHttpClient)"
    assert "def " in client_content, "Should define methods"
    assert "checkout" in client_content.lower(), "Should reference checkout operations"
    
    # Find flow file
    flow_files = [c for c in created_files if "flow" in c.rel_path.lower()]
    assert len(flow_files) >= 1
    flow_path = tmp_path / flow_files[0].rel_path
    flow_content = flow_path.read_text()
    
    # Verify flow structure
    assert "def " in flow_content, "Should define flow function"
    # Check for client usage (more flexible than exact param names)
    client_used = (
        "client" in flow_content.lower() or 
        "Client" in flow_content
    )
    assert client_used, "Should use client"
    # Check for API key or payload parameter (new signature uses payload)
    has_api_params = (
        "api_key" in flow_content.lower() or 
        "payload" in flow_content.lower()
    )
    assert has_api_params, "Should accept api_key or payload parameter"
