"""
Naming utilities for spec-driven code generation.

Derives module names, class names, function names, etc. from spec metadata
rather than hardcoding assumptions like "checkout".
"""
from __future__ import annotations

import re
from typing import Optional, TYPE_CHECKING

from integration_coworker.domain.models import Endpoint

if TYPE_CHECKING:
    from integration_coworker.graph.state import WorkflowState
    from integration_coworker.codegen.context import CodegenContext


def to_snake_case(text: str) -> str:
    """
    Convert text to snake_case.
    
    Examples:
        "createCheckoutSession" -> "create_checkout_session"
        "PaymentIntent" -> "payment_intent"
        "POST /v1/charges" -> "post_v1_charges"
    """
    if not text:
        return "unknown"

    # Handle camelCase and PascalCase
    text = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', text)
    text = re.sub(r'([a-z\d])([A-Z])', r'\1_\2', text)

    # Replace non-alphanumeric with underscores
    text = re.sub(r'[^a-zA-Z0-9]+', '_', text)

    # Remove leading/trailing underscores and collapse multiple
    text = re.sub(r'_+', '_', text).strip('_')

    return text.lower()


def to_pascal_case(text: str) -> str:
    """
    Convert text to PascalCase.
    
    Bug #85 fix: Properly sanitizes invalid identifier characters.
    
    Examples:
        "mock_payments" -> "MockPayments"
        "stripe" -> "Stripe"
        "create_checkout_session" -> "CreateCheckoutSession"
        "resume-test-123" -> "ResumeTest123"
        "123test" -> "Test123"
    """
    if not text:
        return "Unknown"

    # Bug #85: Replace all non-alphanumeric with underscores first
    sanitized = re.sub(r'[^a-zA-Z0-9]+', '_', text)

    # Split by underscores or spaces
    parts = re.split(r'[_\s]+', sanitized)
    
    # Filter empty parts and capitalize each part
    result = ''.join(word.capitalize() for word in parts if word)
    
    # Bug #85: Ensure result doesn't start with a digit (invalid identifier)
    if result and result[0].isdigit():
        result = 'X' + result
    
    return result if result else "Unknown"


def derive_method_name(endpoint: Endpoint) -> str:
    """
    Derive a method name from an endpoint.
    
    Strategy:
    1. If endpoint.operation_id exists, use it (snake_cased)
    2. Otherwise, derive from HTTP verb + path
    
    Examples:
        POST /v1/checkout/sessions with operationId="createCheckoutSession"
            -> "create_checkout_session"
        POST /v1/charges (no operationId)
            -> "create_charges"
        GET /v1/customers/{id}
            -> "get_customer"
    """
    if endpoint.operation_id:
        return to_snake_case(endpoint.operation_id)

    # Derive from method + path
    method = endpoint.method.lower()
    path = endpoint.path

    # Normalize path: remove version prefix, params, trailing slashes
    path = re.sub(r'^/v\d+/?', '/', path)  # Remove version prefix
    path = re.sub(r'/\{[^}]+\}', '', path)  # Remove path params like {id}
    path = path.strip('/')

    # Get last meaningful segment(s)
    segments = [s for s in path.split('/') if s]
    if not segments:
        return f"{method}_resource"

    # Use last segment, singularize for specific operations
    resource = segments[-1]

    # Map HTTP verbs to action words
    verb_map = {
        'get': 'get',
        'post': 'create',
        'put': 'update',
        'patch': 'update',
        'delete': 'delete',
    }
    action = verb_map.get(method, method)

    # Singularize if it looks like a collection
    if resource.endswith('s') and action in ('create', 'get') and '{' not in endpoint.path:
        # Keep plural for list operations, singularize for create
        if action == 'create':
            resource = resource.rstrip('s')

    return to_snake_case(f"{action}_{resource}")


def derive_client_class_name(provider_code: str, task_slug: Optional[str] = None) -> str:
    """
    Derive the client class name from provider code.
    
    Examples:
        "mock_payments" -> "MockPaymentsClient"
        "stripe" -> "StripeClient"
    """
    base = to_pascal_case(provider_code)
    return f"{base}Client"


def derive_client_module_name(provider_code: str) -> str:
    """
    Derive the client module filename (without .py).
    
    Examples:
        "mock_payments" -> "mock_payments"
        "Stripe" -> "stripe"
    """
    return to_snake_case(provider_code)


def derive_flow_function_name(task_slug: str, provider_code: Optional[str] = None) -> str:
    """
    Derive the flow function name from task slug.
    
    Examples:
        "create_checkout_session" -> "create_checkout_session_flow"
        "process_payment" -> "process_payment_flow"
    """
    base = to_snake_case(task_slug)
    if not base.endswith('_flow'):
        base = f"{base}_flow"
    return base


def derive_flow_module_name(provider_code: str, task_slug: str) -> str:
    """
    Derive the flow module filename (without .py).
    
    Examples:
        ("mock_payments", "create_checkout_session") -> "mock_payments_create_checkout_session"
        ("stripe", "process_payment") -> "stripe_process_payment"
    """
    provider = to_snake_case(provider_code)
    task = to_snake_case(task_slug)
    return f"{provider}_{task}"


def derive_test_module_name(provider_code: str, task_slug: str) -> str:
    """
    Derive the test module filename (without .py).
    
    Examples:
        ("mock_payments", "create_checkout_session") -> "test_mock_payments_create_checkout_session"
    """
    provider = to_snake_case(provider_code)
    task = to_snake_case(task_slug)
    return f"test_{provider}_{task}"


def derive_test_class_name(provider_code: str) -> str:
    """
    Derive the test class name from provider code.
    
    Examples:
        "mock_payments" -> "TestMockPaymentsFlow"
        "stripe" -> "TestStripeFlow"
    """
    base = to_pascal_case(provider_code)
    return f"Test{base}Flow"


def build_codegen_context(state: "WorkflowState") -> "CodegenContext":
    """
    Build a CodegenContext from WorkflowState.
    
    This is the SINGLE point where all code generation names are derived.
    All artifact generators should use this context to ensure consistency.
    
    Args:
        state: WorkflowState containing spec, task, and repo information
    
    Returns:
        Frozen CodegenContext with all derived names
    
    Example:
        ctx = build_codegen_context(state)
        # Now use ctx.method_name, ctx.client_class, etc. everywhere
    """
    # Avoid circular imports
    from integration_coworker.codegen.context import CodegenContext, get_file_extension
    from integration_coworker.codegen.paths import (
        get_layout_dirs,
        derive_base_url,
        path_to_module,
        strip_src_prefix,
    )
    
    # Extract base identifiers
    provider_code = state.provider_code or "unknown"
    task_slug = (
        state.integration_task.task_slug 
        if state.integration_task 
        else "integration"
    )
    
    # Get primary endpoint
    primary_endpoint = _get_primary_endpoint_for_context(state)
    
    # Get layout directories from RepoProfile
    clients_dir, flows_dir, tests_dir = get_layout_dirs(state.repo_profile)
    
    # Get language from RepoProfile (Bug #1 fix: language-aware file extensions)
    language = state.repo_profile.language if state.repo_profile else "python"
    ext = get_file_extension(language)
    
    # Derive all names using existing functions
    client_module = derive_client_module_name(provider_code)
    client_class = derive_client_class_name(provider_code)
    
    # CRITICAL: method_name comes from endpoint, NOT task_slug
    method_name = (
        derive_method_name(primary_endpoint) 
        if primary_endpoint 
        else to_snake_case(task_slug)
    )
    
    flow_module = derive_flow_module_name(provider_code, task_slug)
    flow_function = derive_flow_function_name(task_slug)
    test_module = derive_test_module_name(provider_code, task_slug)
    test_class = derive_test_class_name(provider_code)
    
    # Compute import paths (use language-aware extension)
    client_rel_path = f"{clients_dir}/{client_module}{ext}"
    flow_rel_path = f"{flows_dir}/{flow_module}{ext}"
    
    client_import_path = path_to_module(strip_src_prefix(client_rel_path))
    flow_import_path = path_to_module(strip_src_prefix(flow_rel_path))
    
    # Derive base URL
    base_url = derive_base_url(state)
    
    return CodegenContext(
        provider_code=provider_code,
        task_slug=task_slug,
        client_module=client_module,
        client_class=client_class,
        method_name=method_name,
        client_import_path=client_import_path,
        flow_module=flow_module,
        flow_function=flow_function,
        flow_import_path=flow_import_path,
        test_module=test_module,
        test_class=test_class,
        clients_dir=clients_dir,
        flows_dir=flows_dir,
        tests_dir=tests_dir,
        language=language,
        base_url=base_url,
        endpoint=primary_endpoint,
    )


def _get_primary_endpoint_for_context(state: "WorkflowState") -> Optional[Endpoint]:
    """
    Get the primary endpoint for code generation context.
    
    This is a copy of the logic from generate_code_and_tests to avoid
    circular dependencies.
    """
    if not state.endpoint_bindings:
        return None
    
    binding = state.endpoint_bindings[0]
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
