"""
generate_code_and_tests node - Generate client, flow, and test code.

Uses spec-driven naming and RepoProfile layout for paths, with LLM for code body generation.

Per design doc Section 5.5:
- Archetype: generate_code_and_tests.archetype.yaml
- Provider: Anthropic (claude-3-sonnet) - superior code generation
- Strategy: generation with multi-candidate selection
"""
import ast
import logging
from typing import Optional, Literal

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import CodeArtifact, Endpoint
from integration_coworker.llm import get_llm_client_for_node, is_mock_llm_mode
from integration_coworker.config import get_archetype_prompt_config
from integration_coworker.codegen.naming import (
    derive_method_name,
    derive_client_class_name,
    derive_client_module_name,
    derive_flow_function_name,
    derive_flow_module_name,
    derive_test_module_name,
    to_snake_case,
)
from integration_coworker.codegen.paths import (
    derive_base_url,
    get_layout_dirs,
    path_to_module,
    strip_src_prefix,
)
from integration_coworker.codegen.prompts import build_codegen_prompt
from integration_coworker.codegen.policy_templates import (
    inject_policies_into_client_code,
)
from integration_coworker.codegen.security import (
    validate_code_security,
    format_violations,
)
from integration_coworker.llm.content_policy import (
    validate_generated_code as validate_content_policy,
    format_violations as format_policy_violations,
)

logger = logging.getLogger(__name__)

# Node name for archetype loading
NODE_NAME = "generate_code_and_tests"


def _build_code_generation_prompt(
    skeleton_code: str,
    state: WorkflowState,
    artifact_type: Literal["client", "flow", "test"],
    endpoint: Optional[Endpoint] = None,
    client_class: Optional[str] = None,
    method_name: Optional[str] = None,
    flow_function: Optional[str] = None,
) -> str:
    """Build a rich prompt for LLM code generation."""
    return build_codegen_prompt(
        state=state,
        endpoint=endpoint,
        task=state.integration_task,
        repo_profile=state.repo_profile,
        artifact_kind=artifact_type,
        skeleton_code=skeleton_code,
        client_class=client_class,
        method_name=method_name,
        flow_function=flow_function,
    )


def generate_code_and_tests(state: WorkflowState) -> WorkflowState:
    """
    Reads: endpoints, endpoint_bindings, policies, integration_task, workflow_nodes, repo_profile
    Writes: code_artifacts
    
    Generates code using spec-driven naming and RepoProfile layout.
    """
    # Runtime guard: warn if using mock LLM in non-dry-run mode
    is_dry_run = state.options.dry_run if state.options else False
    if is_mock_llm_mode() and not is_dry_run:
        logger.warning(
            "Codegen is running with mock LLM; generated code is for testing only "
            "and not production-grade. Set OPENAI_API_KEY for real LLM codegen."
        )

    if not state.endpoint_bindings:
        state.errors.append("No endpoint_bindings to generate code from")
        state.completed_steps.append("generate_code_and_tests")
        return state

    provider_code = state.provider_code or "unknown"
    task_slug = state.integration_task.task_slug if state.integration_task else "integration"

    # Get the primary endpoint for this task
    primary_endpoint = _get_primary_endpoint(state)

    # Get layout directories from RepoProfile
    clients_dir, flows_dir, tests_dir = get_layout_dirs(state.repo_profile)

    # Derive spec-driven names
    client_module = derive_client_module_name(provider_code)
    client_class = derive_client_class_name(provider_code)
    method_name = derive_method_name(primary_endpoint) if primary_endpoint else to_snake_case(task_slug)
    flow_module = derive_flow_module_name(provider_code, task_slug)
    flow_function = derive_flow_function_name(task_slug)
    test_module = derive_test_module_name(provider_code, task_slug)

    # Derive base URL from spec
    base_url = derive_base_url(state)

    try:
        # Generate CLIENT code
        client_code = _generate_client_code(
            state=state,
            provider_code=provider_code,
            client_class=client_class,
            method_name=method_name,
            endpoint=primary_endpoint,
            base_url=base_url,
        )
        client_code = _refine_with_llm(
            client_code, state, "client", client_class, method_name, primary_endpoint
        )

        # P1: Inject policy code into client
        if state.policies:
            policies_for_injection = [
                {"policy_type": p.policy_type.value if hasattr(p.policy_type, 'value') else str(p.policy_type), "config": p.config}
                for p in state.policies
            ]
            client_code = inject_policies_into_client_code(client_code, policies_for_injection)
            logger.info(f"Injected {len(state.policies)} policies into client code")

        client_artifact = CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="client",
            language="python",
            module_name=client_module,
            rel_path=f"{clients_dir}/{client_module}.py",
            content=client_code,
        )
        state.code_artifacts.append(client_artifact)

        # Compute import path for flow to import client
        client_import_module = path_to_module(strip_src_prefix(f"{clients_dir}/{client_module}.py"))

        # Generate FLOW code
        flow_code = _generate_flow_code(
            state=state,
            provider_code=provider_code,
            task_slug=task_slug,
            client_class=client_class,
            client_import_module=client_import_module,
            method_name=method_name,
            flow_function=flow_function,
        )
        flow_code = _refine_with_llm(
            flow_code, state, "flow", None, flow_function, primary_endpoint
        )

        flow_artifact = CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="flow",
            language="python",
            module_name=flow_module,
            rel_path=f"{flows_dir}/{flow_module}.py",
            content=flow_code,
        )
        state.code_artifacts.append(flow_artifact)

        # Compute import path for test to import flow
        flow_import_module = path_to_module(strip_src_prefix(f"{flows_dir}/{flow_module}.py"))

        # Generate TEST code
        test_code = _generate_test_code(
            state=state,
            provider_code=provider_code,
            task_slug=task_slug,
            client_class=client_class,
            flow_import_module=flow_import_module,
            flow_function=flow_function,
        )
        test_code = _refine_with_llm(
            test_code, state, "test", None, None, primary_endpoint
        )

        test_artifact = CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="test",
            language="python",
            module_name=test_module,
            rel_path=f"{tests_dir}/{test_module}.py",
            content=test_code,
        )
        state.code_artifacts.append(test_artifact)

    except Exception as e:
        state.errors.append(f"Failed to generate code: {str(e)}")
        logger.exception("Code generation failed")

    state.completed_steps.append("generate_code_and_tests")
    return state


def _get_primary_endpoint(state: WorkflowState) -> Optional[Endpoint]:
    """Get the primary endpoint for code generation."""
    binding = state.endpoint_bindings[0] if state.endpoint_bindings else None
    if not binding:
        return None

    endpoint = None

    # Try by ID if available
    if binding.endpoint_id is not None:
        for ep in state.endpoints:
            if ep.id == binding.endpoint_id:
                endpoint = ep
                break

    # Fallback: look for stored endpoint reference
    if not endpoint and hasattr(binding, '_matched_endpoint'):
        endpoint = binding._matched_endpoint

    # Fallback: find a suitable POST endpoint
    if not endpoint and state.endpoints:
        for ep in state.endpoints:
            if ep.method == "POST":
                endpoint = ep
                break
        if not endpoint:
            endpoint = state.endpoints[0]

    return endpoint


def _refine_with_llm(
    template_code: str,
    state: WorkflowState,
    artifact_type: Literal["client", "flow", "test"],
    expected_class: Optional[str] = None,
    expected_function: Optional[str] = None,
    endpoint: Optional[Endpoint] = None,
) -> str:
    """
    Refine template code with LLM, with validation.
    
    Args:
        template_code: The template/skeleton code
        state: WorkflowState for context
        artifact_type: "client", "flow", or "test"
        expected_class: Class name that must be present (for client)
        expected_function: Function name that must be present (for flow/test)
        endpoint: The endpoint being implemented
    
    Returns:
        Refined code, or template code if LLM fails validation
    """
    module_name = expected_class or expected_function or artifact_type

    try:
        # Get LLM client configured for this node's archetype
        client = get_llm_client_for_node(NODE_NAME)

        # Get system prompt from archetype if available
        prompt_config = get_archetype_prompt_config(NODE_NAME)
        system_prompt = prompt_config.get("system_template")

        prompt = _build_code_generation_prompt(
            skeleton_code=template_code,
            state=state,
            artifact_type=artifact_type,
            endpoint=endpoint,
            client_class=expected_class,
            method_name=expected_function if artifact_type == "client" else None,
            flow_function=expected_function if artifact_type in ("flow", "test") else None,
        )
        refined = client.complete(prompt, system_prompt=system_prompt)

        if not refined:
            logger.info(f"Falling back to template skeleton for {artifact_type} '{module_name}' because: LLM returned empty response")
            return template_code

        # Clean up markdown code blocks if present
        clean_refined = refined
        if clean_refined.startswith("```"):
            lines = clean_refined.split("\n")
            # Remove first line (```python) and last line if it's ```)
            if lines[-1].strip() == "```":
                clean_refined = "\n".join(lines[1:-1])
            else:
                clean_refined = "\n".join(lines[1:])

        # REMOVED: Mock detection heuristic (LLM-007)
        # v2: We now trust the LLM with proper prompting instead of fragile
        # string-matching heuristics. Security validation below catches real issues.

        # Validate syntax with AST
        if not _validate_python_syntax(clean_refined):
            logger.warning(f"Falling back to template skeleton for {artifact_type} '{module_name}' because: LLM output failed AST syntax validation")
            return template_code

        # v2: Security validation (SEC-003)
        is_secure, violations = validate_code_security(
            clean_refined,
            allow_subprocess=False,  # Default: no subprocess
            allow_file_io=True,       # Allow open() for config reading
        )
        
        if not is_secure:
            logger.warning(
                f"Falling back to template skeleton for {artifact_type} '{module_name}' "
                f"because: security violations detected:\n{format_violations(violations)}"
            )
            return template_code

        # SEC-004: Content policy validation (semantic checks)
        is_policy_valid, policy_violations = validate_content_policy(
            clean_refined,
            endpoints=state.endpoints if state.endpoints else None,
        )
        
        if not is_policy_valid:
            logger.warning(
                f"Falling back to template skeleton for {artifact_type} '{module_name}' "
                f"because: content policy violations detected:\n{format_policy_violations(policy_violations)}"
            )
            return template_code

        # Validate expected symbols are present
        if expected_class and not _has_class(clean_refined, expected_class):
            logger.warning(f"Falling back to template skeleton for {artifact_type} '{module_name}' because: LLM output missing expected class '{expected_class}'")
            return template_code

        if expected_function and not _has_function(clean_refined, expected_function):
            logger.warning(f"Falling back to template skeleton for {artifact_type} '{module_name}' because: LLM output missing expected function '{expected_function}'")
            return template_code

        logger.info(f"Using LLM-generated body for {artifact_type} '{module_name}'")
        return clean_refined

    except Exception as e:
        logger.warning(f"Falling back to template skeleton for {artifact_type} '{module_name}' because: LLM refinement failed with exception: {e}")
        return template_code


def _validate_python_syntax(code: str) -> bool:
    """Check if code is syntactically valid Python."""
    try:
        ast.parse(code)
        return True
    except SyntaxError as e:
        logger.debug(f"Syntax error in generated code: {e}")
        return False


def _has_class(code: str, class_name: str) -> bool:
    """Check if code defines a class with the given name."""
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return True
        return False
    except:
        return False


def _has_function(code: str, func_name: str) -> bool:
    """Check if code defines a function with the given name."""
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return True
        return False
    except:
        return False


def _generate_client_code(
    state: WorkflowState,
    provider_code: str,
    client_class: str,
    method_name: str,
    endpoint: Optional[Endpoint],
    base_url: str,
) -> str:
    """
    Generate client module code using spec-driven naming.
    
    Args:
        state: WorkflowState for context
        provider_code: Provider identifier (e.g., "stripe")
        client_class: Class name (e.g., "StripeClient")
        method_name: Main method name (e.g., "create_checkout_session")
        endpoint: Primary endpoint with path, method info
        base_url: API base URL
    """
    if not endpoint:
        return f'''"""
{provider_code.title()} API Client

Auto-generated by Integration Co-Worker
"""
# No endpoint found for binding - please configure endpoint bindings
'''

    endpoint_path = endpoint.path
    http_method = endpoint.method.upper()
    summary = endpoint.summary or f"{http_method} {endpoint_path}"

    # Build the template code
    code = f'''"""
{provider_code.title()} API Client

Auto-generated by Integration Co-Worker
Provider: {provider_code}
Endpoint: {http_method} {endpoint_path}
"""
from typing import Dict, Any, Optional
from integration_coworker.runtime.http_client import IntegrationHttpClient
from integration_coworker.runtime.exceptions import IntegrationError


class {client_class}:
    """Client for {provider_code} API."""
    
    def __init__(self, api_key: str, base_url: str = "{base_url}"):
        """
        Initialize the {client_class}.
        
        Args:
            api_key: API key for authentication
            base_url: Base URL for the API (default: {base_url})
        """
        self.client = IntegrationHttpClient(
            base_url=base_url,
            api_key=api_key,
            timeout_s=30,
            retries=3,
        )
    
    def {method_name}(
        self,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        {summary}
        
        Args:
            payload: Request payload
            idempotency_key: Optional idempotency key for safe retries
        
        Returns:
            API response as dictionary
        
        Raises:
            IntegrationError: If the API request fails
        """
        headers = {{}}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        
        # Add auth header
        if self.client.api_key:
            headers["Authorization"] = f"Bearer {{self.client.api_key}}"
        
        response = self.client.request(
            "{http_method}",
            "{endpoint_path}",
            json=payload,
            headers=headers,
        )
        
        if response.status_code not in (200, 201):
            raise IntegrationError(
                f"API request failed: {{response.status_code}} - {{response.text[:200]}}"
            )
        
        return response.json()
'''
    return code


def _generate_flow_code(
    state: WorkflowState,
    provider_code: str,
    task_slug: str,
    client_class: str,
    client_import_module: str,
    method_name: str,
    flow_function: str,
) -> str:
    """
    Generate flow/workflow code using spec-driven naming.
    
    Args:
        state: WorkflowState for context
        provider_code: Provider identifier
        task_slug: Task slug (e.g., "create_checkout_session")
        client_class: Client class name to import
        client_import_module: Module path to import client from
        method_name: Client method name to call
        flow_function: Flow function name
    """
    task_desc = state.task_description or f"{provider_code} integration"

    code = f'''"""
{provider_code.title()} {task_slug.replace('_', ' ').title()} Flow

Auto-generated by Integration Co-Worker
Task: {task_desc}
"""
from typing import Dict, Any, Optional
from {client_import_module} import {client_class}
from integration_coworker.runtime.exceptions import IntegrationError


def {flow_function}(
    api_key: str,
    payload: Dict[str, Any],
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Execute the {task_slug.replace('_', ' ')} workflow.
    
    This flow:
    1. Validates input parameters
    2. Calls the {provider_code} API
    3. Transforms and returns the response
    
    Args:
        api_key: API authentication key
        payload: Request payload for the API
        **kwargs: Additional options (e.g., idempotency_key)
    
    Returns:
        Dict containing the API response with relevant fields
    
    Raises:
        ValueError: If required input is missing or invalid
        IntegrationError: If the API call fails
    """
    # Step 1: Validate input
    if not api_key:
        raise ValueError("api_key is required")
    if not payload:
        raise ValueError("payload is required")
    
    # Step 2: Call the API
    client = {client_class}(api_key=api_key)
    idempotency_key = kwargs.get("idempotency_key")
    
    try:
        response = client.{method_name}(
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except Exception as e:
        raise IntegrationError(f"API call failed: {{str(e)}}") from e
    
    # Step 3: Transform and return response
    return {{
        "success": True,
        "data": response,
    }}
'''
    return code


def _generate_test_code(
    state: WorkflowState,
    provider_code: str,
    task_slug: str,
    client_class: str,
    flow_import_module: str,
    flow_function: str,
) -> str:
    """
    Generate test code using spec-driven naming.
    
    Args:
        state: WorkflowState for context
        provider_code: Provider identifier
        task_slug: Task slug
        client_class: Client class name (for mocking)
        flow_import_module: Module path to import flow from
        flow_function: Flow function name to test
    """
    code = f'''"""
Tests for {provider_code.title()} {task_slug.replace('_', ' ').title()} Flow

Auto-generated by Integration Co-Worker
"""
import pytest
from unittest.mock import Mock, patch
from {flow_import_module} import {flow_function}


class Test{client_class.replace("Client", "")}Flow:
    """Tests for the {task_slug.replace('_', ' ')} flow."""
    
    def test_{flow_function}_success(self):
        """Test successful flow execution."""
        with patch('{flow_import_module}.{client_class}') as MockClient:
            # Setup mock
            mock_client = MockClient.return_value
            mock_client.{to_snake_case(task_slug)}.return_value = {{
                "id": "test_123",
                "status": "success",
            }}
            
            # Execute flow
            result = {flow_function}(
                api_key="test_api_key",
                payload={{"key": "value"}},
            )
            
            # Verify
            assert result["success"] is True
            assert "data" in result
    
    def test_{flow_function}_missing_api_key(self):
        """Test flow raises error when api_key is missing."""
        with pytest.raises(ValueError, match="api_key is required"):
            {flow_function}(
                api_key="",
                payload={{"key": "value"}},
            )
    
    def test_{flow_function}_missing_payload(self):
        """Test flow raises error when payload is missing."""
        with pytest.raises(ValueError, match="payload is required"):
            {flow_function}(
                api_key="test_api_key",
                payload={{}},
            )
'''
    return code
