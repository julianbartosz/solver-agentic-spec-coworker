"""
Naming utilities for spec-driven code generation.

Derives module names, class names, function names, etc. from spec metadata
rather than hardcoding assumptions like "checkout".
"""
import re
from typing import Optional

from integration_coworker.domain.models import Endpoint, IntegrationTask


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
    
    Examples:
        "mock_payments" -> "MockPayments"
        "stripe" -> "Stripe"
        "create_checkout_session" -> "CreateCheckoutSession"
    """
    if not text:
        return "Unknown"
    
    # Split by underscores or spaces
    parts = re.split(r'[_\s]+', text)
    
    # Capitalize each part
    return ''.join(word.capitalize() for word in parts if word)


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
