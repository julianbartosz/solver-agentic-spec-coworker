"""
generate_code_and_tests node - Generate client, flow, and test code.

Uses templates for structure, with optional LLM refinement for function bodies.
"""
import logging
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import CodeArtifact
from integration_coworker.llm import call_llm

logger = logging.getLogger(__name__)


def _build_code_refinement_prompt(template_code: str, state: WorkflowState, artifact_type: str) -> str:
    """Build prompt for LLM code refinement."""
    task_desc = state.task_description or "API integration"
    provider = state.provider_code or "unknown"
    
    return f"""Refine this {artifact_type} code template for a {provider} API integration.

TASK: {task_desc}

TEMPLATE CODE:
```python
{template_code}
```

Improve the code by:
1. Adding better docstrings and comments
2. Improving error handling
3. Adding type hints where missing
4. Making the code more idiomatic

Return ONLY the refined Python code, no explanations.
"""


def generate_code_and_tests(state: WorkflowState) -> WorkflowState:
    """
    Reads: endpoints, endpoint_bindings, policies, integration_task, workflow_nodes, repo_profile
    Writes: code_artifacts
    
    Generates code using templates, optionally refined by LLM.
    """
    if not state.endpoint_bindings:
        state.errors.append("No endpoint_bindings to generate code from")
        state.completed_steps.append("generate_code_and_tests")
        return state
    
    provider_code = state.provider_code or "unknown"
    task_slug = state.integration_task.task_slug if state.integration_task else "integration"
    
    try:
        # Generate CLIENT code
        client_template = _generate_client_code(state, provider_code)
        client_code = _refine_with_llm(client_template, state, "client")
        
        client_artifact = CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="client",
            language="python",
            module_name=f"{provider_code}_client",
            rel_path=f"integrations/clients/{provider_code}.py",
            content=client_code,
        )
        state.code_artifacts.append(client_artifact)
        
        # Generate FLOW code
        flow_template = _generate_flow_code(state, provider_code, task_slug)
        flow_code = _refine_with_llm(flow_template, state, "flow")
        
        flow_artifact = CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="flow",
            language="python",
            module_name=f"{provider_code}_checkout_flow",
            rel_path=f"integrations/flows/{provider_code}_checkout.py",
            content=flow_code,
        )
        state.code_artifacts.append(flow_artifact)
        
        # Generate TEST code
        test_template = _generate_test_code(state, provider_code)
        test_code = _refine_with_llm(test_template, state, "test")
        
        test_artifact = CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="test",
            language="python",
            module_name=f"test_{provider_code}_checkout",
            rel_path=f"integrations/test_{provider_code}_checkout.py",
            content=test_code,
        )
        state.code_artifacts.append(test_artifact)
        
    except Exception as e:
        state.errors.append(f"Failed to generate code: {str(e)}")
    
    state.completed_steps.append("generate_code_and_tests")
    return state


def _refine_with_llm(template_code: str, state: WorkflowState, artifact_type: str) -> str:
    """Optionally refine template code with LLM."""
    try:
        prompt = _build_code_refinement_prompt(template_code, state, artifact_type)
        refined = call_llm(prompt, task_type="codegen")
        
        # Validate refined code is genuinely improved (not just a mock response)
        # Mock responses are short generic functions - template should be used instead
        if refined:
            # Remove markdown code blocks if present
            clean_refined = refined
            if clean_refined.startswith("```"):
                lines = clean_refined.split("\n")
                clean_refined = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            
            # Check if it's a mock/placeholder response
            is_mock = (
                "mock_function" in clean_refined or 
                "Mock response" in refined or
                len(clean_refined) < 100  # Too short to be useful refinement
            )
            
            if not is_mock and ("def " in clean_refined or "class " in clean_refined):
                logger.info(f"LLM refined {artifact_type} code")
                return clean_refined
        
        # LLM returned mock or invalid, use template
        logger.info(f"Using template code for {artifact_type} (LLM returned mock/placeholder)")
        return template_code
            
    except Exception as e:
        logger.warning(f"LLM refinement failed for {artifact_type}, using template: {e}")
        return template_code


def _generate_client_code(state: WorkflowState, provider_code: str) -> str:
    """Generate client module code."""
    # Find the main endpoint
    binding = state.endpoint_bindings[0] if state.endpoint_bindings else None
    if not binding:
        return "# No endpoint binding found"
    
    # Find endpoint - first try by ID, then by looking for POST endpoints
    endpoint = None
    
    # Try by ID if available
    if binding.endpoint_id is not None:
        for ep in state.endpoints:
            if ep.id == binding.endpoint_id:
                endpoint = ep
                break
    
    # Fallback: look for stored endpoint reference (set by plan_integration_flow)
    if not endpoint and hasattr(binding, '_matched_endpoint'):
        endpoint = binding._matched_endpoint
    
    # Fallback: find a suitable POST endpoint (typically the main API endpoint)
    if not endpoint and state.endpoints:
        for ep in state.endpoints:
            if ep.method == "POST":
                endpoint = ep
                break
        # If no POST, use first endpoint
        if not endpoint:
            endpoint = state.endpoints[0]
    
    if not endpoint:
        return "# No endpoint found for binding"
    
    endpoint_path = endpoint.path
    base_url = "https://api.mockpayments.example"  # Default, could extract from openapi_spec
    
    code = f'''"""
{provider_code.title()} API Client

Auto-generated by Integration Co-Worker
"""
from typing import Dict, Any, Optional
from integration_coworker.runtime.http_client import IntegrationHttpClient
from integration_coworker.runtime.exceptions import IntegrationError


class {provider_code.title().replace("_", "")}Client:
    """Client for {provider_code} API."""
    
    def __init__(self, api_key: str, base_url: str = "{base_url}"):
        self.client = IntegrationHttpClient(
            base_url=base_url,
            api_key=api_key,
            timeout_s=30,
            retries=3,
        )
    
    def create_checkout_session(
        self,
        amount: int,
        currency: str,
        success_url: str,
        cancel_url: str,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a checkout session.
        
        Args:
            amount: Amount in smallest currency unit (e.g. cents)
            currency: Three-letter ISO currency code
            success_url: URL to redirect on successful payment
            cancel_url: URL to redirect on cancelled payment
            metadata: Optional metadata
            idempotency_key: Optional idempotency key
        
        Returns:
            Checkout session data
        """
        payload = {{
            "amount": amount,
            "currency": currency,
            "success_url": success_url,
            "cancel_url": cancel_url,
        }}
        
        if metadata:
            payload["metadata"] = metadata
        
        headers = {{}}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        
        # Add auth header
        if self.client.api_key:
            headers["Authorization"] = f"Bearer {{self.client.api_key}}"
        
        response = self.client.request(
            "POST",
            "{endpoint_path}",
            json=payload,
            headers=headers,
        )
        
        if response.status_code != 200:
            raise IntegrationError(f"Failed to create checkout session: {{response.status_code}}")
        
        return response.json()
'''
    return code


def _generate_flow_code(state: WorkflowState, provider_code: str, task_slug: str) -> str:
    """Generate flow/workflow code."""
    code = f'''"""
{provider_code.title()} Checkout Flow

Auto-generated by Integration Co-Worker
"""
from typing import Dict, Any
from .clients.{provider_code} import {provider_code.title().replace("_", "")}Client


def create_checkout_session_flow(
    api_key: str,
    amount: int,
    currency: str,
    success_url: str,
    cancel_url: str,
    metadata: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Create a checkout session workflow.
    
    Implements: validate_input -> call_create_session -> transform_response -> return_result
    
    Args:
        api_key: API authentication key
        amount: Payment amount in smallest unit
        currency: ISO currency code
        success_url: Success redirect URL
        cancel_url: Cancel redirect URL
        metadata: Optional metadata
    
    Returns:
        Dict with session_id and checkout_url
    """
    # Step 1: Validate input
    if amount <= 0:
        raise ValueError("Amount must be positive")
    if len(currency) != 3:
        raise ValueError("Currency must be 3-letter ISO code")
    if not success_url or not cancel_url:
        raise ValueError("Success and cancel URLs are required")
    
    # Step 2: Call create session
    client = {provider_code.title().replace("_", "")}Client(api_key=api_key)
    response = client.create_checkout_session(
        amount=amount,
        currency=currency,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
    )
    
    # Step 3: Transform response
    session_id = response.get("id")
    checkout_url = response.get("checkout_url")
    
    if not session_id or not checkout_url:
        raise ValueError("Invalid response from API")
    
    # Step 4: Return result
    return {{
        "session_id": session_id,
        "checkout_url": checkout_url,
        "status": response.get("status", "open"),
        "amount": response.get("amount"),
        "currency": response.get("currency"),
    }}
'''
    return code


def _generate_test_code(state: WorkflowState, provider_code: str) -> str:
    """Generate test code."""
    code = f'''"""
Tests for {provider_code.title()} Checkout Flow

Auto-generated by Integration Co-Worker
"""
import pytest
from unittest.mock import Mock, patch
from integrations.flows.{provider_code}_checkout import create_checkout_session_flow


def test_create_checkout_session_flow_success():
    """Test successful checkout session creation."""
    # Mock the client
    with patch('integrations.flows.{provider_code}_checkout.{provider_code.title().replace("_", "")}Client') as MockClient:
        mock_client = MockClient.return_value
        mock_client.create_checkout_session.return_value = {{
            "id": "cs_test_123abc",
            "object": "checkout_session",
            "checkout_url": "https://checkout.mockpayments.example/cs_test_123abc",
            "amount": 2000,
            "currency": "usd",
            "status": "open",
        }}
        
        result = create_checkout_session_flow(
            api_key="test_key",
            amount=2000,
            currency="usd",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        
        assert result["session_id"] == "cs_test_123abc"
        assert result["checkout_url"] == "https://checkout.mockpayments.example/cs_test_123abc"
        assert result["status"] == "open"
        assert result["amount"] == 2000
        assert result["currency"] == "usd"


def test_create_checkout_session_flow_validation_error():
    """Test input validation."""
    with pytest.raises(ValueError, match="Amount must be positive"):
        create_checkout_session_flow(
            api_key="test_key",
            amount=-100,
            currency="usd",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
    
    with pytest.raises(ValueError, match="Currency must be 3-letter ISO code"):
        create_checkout_session_flow(
            api_key="test_key",
            amount=2000,
            currency="us",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
'''
    return code
