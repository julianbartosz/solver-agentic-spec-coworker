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
    
    V36-003: Now integrates task_parser to extract explicit paths and function
    signatures from task descriptions. When users specify explicit paths like
    "create file at src/myapp/ai_enhancer.py", those paths take precedence.
    
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
        join_path,
    )
    # V36-003: Import task parser for explicit path/function extraction
    from integration_coworker.codegen.task_parser import (
        extract_task_requirements,
        should_override_template_path,
        TaskPathExtractionResult,
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
    # BUG-PATH-001 FIX: Use join_path to prevent double-slashes
    client_rel_path = join_path(clients_dir, f"{client_module}{ext}")
    flow_rel_path = join_path(flows_dir, f"{flow_module}{ext}")
    
    client_import_path = path_to_module(strip_src_prefix(client_rel_path))
    flow_import_path = path_to_module(strip_src_prefix(flow_rel_path))
    
    # Derive base URL
    base_url = derive_base_url(state)
    
    # =========================================================================
    # V36-003: Extract task requirements for path/function overrides
    # =========================================================================
    task_explicit_path = None
    task_function_name = None
    task_class_name = None
    has_task_overrides = False
    
    # Get task description from multiple sources
    task_description = state.task_description or ""
    if not task_description and state.integration_task:
        task_description = getattr(state.integration_task, 'description', '') or ""
        if not task_description:
            task_description = getattr(state.integration_task, 'task_slug', '') or ""
    
    # V38-007: Detect async requirements from task description
    is_async_required = False
    # V38-008: Detect streaming requirements from task description
    is_streaming_required = False
    
    # V39-005: Enhanced logging for path extraction debugging
    import logging
    logger = logging.getLogger(__name__)
    
    if task_description:
        logger.debug(f"[V39-005] Task description for path extraction: {task_description[:200]}...")
        task_result = extract_task_requirements(task_description)
        
        # V38-007: Check if async code is needed
        is_async_required = task_result.is_async_required
        # V38-008: Check if streaming is needed
        is_streaming_required = task_result.is_streaming_required
        
        # V39-005: Always log extraction results for debugging
        logger.debug(
            f"[V39-005] Task extraction: paths={task_result.explicit_file_paths}, "
            f"primary={task_result.primary_target_path}, "
            f"confidence={task_result.extraction_confidence:.2f}, "
            f"override={should_override_template_path(task_result)}"
        )
        
        if should_override_template_path(task_result):
            has_task_overrides = True
            task_explicit_path = task_result.primary_target_path
            
            # Extract function name if provided
            if task_result.requested_functions:
                task_function_name = task_result.requested_functions[0].name
                # Don't add _flow suffix if user specified exact name
                
            # Extract class name if provided
            if task_result.requested_classes:
                task_class_name = task_result.requested_classes[0].name
            
            logger.info(
                f"[V36-003] Task parser found explicit requirements: "
                f"path={task_explicit_path}, function={task_function_name}, "
                f"class={task_class_name}, async={is_async_required}, "
                f"streaming={is_streaming_required}, "
                f"confidence={task_result.extraction_confidence:.2f}"
            )
            
            # V36-003: Update flow import path if explicit path is used
            if task_explicit_path:
                flow_import_path = path_to_module(strip_src_prefix(task_explicit_path))
        else:
            # V39-005: Log why we're not overriding
            logger.debug(
                f"[V39-005] Not overriding template path: "
                f"has_explicit={task_result.has_explicit_structure}, "
                f"primary_path={task_result.primary_target_path}, "
                f"confidence={task_result.extraction_confidence:.2f} (need >= 0.3)"
            )
    else:
        logger.warning("[V39-005] No task description available for path extraction")
    
    # =========================================================================
    # V39-007: Detect existing API clients in target repository
    # =========================================================================
    # Before generating a new client, check if the target repo already has
    # a compatible client for the API. This prevents duplicate code and
    # allows reuse of existing, potentially customized clients.
    # =========================================================================
    existing_client_detected = False
    existing_client_class = None
    existing_client_import = None
    existing_client_module = None
    existing_client_methods: tuple = ()
    skip_client_generation = False
    
    if state.repo_root:
        try:
            from integration_coworker.codegen.client_detector import decide_client_strategy
            
            repo_root_str = str(state.repo_root) if state.repo_root else ""
            
            client_decision = decide_client_strategy(
                repo_root=repo_root_str,
                provider_code=provider_code,
                base_url=base_url,
                force_generate=False,  # Respect existing clients
            )
            
            if client_decision.use_existing:
                existing_client_detected = True
                existing_client_class = client_decision.client_class_name
                existing_client_import = client_decision.import_statement
                existing_client_module = client_decision.client_module_path
                skip_client_generation = True
                
                # Extract available methods if we have the client info
                if client_decision.existing_client:
                    existing_client_methods = tuple(
                        client_decision.existing_client.methods +
                        client_decision.existing_client.async_methods
                    )
                
                logger.info(
                    f"[V39-007] Found existing {provider_code} client in target repo: "
                    f"{existing_client_class} at {existing_client_module}. "
                    f"Skipping client generation and using existing client."
                )
                for reason in client_decision.reasons[:3]:  # Log top 3 reasons
                    logger.debug(f"[V39-007] Detection reason: {reason}")
            else:
                logger.debug(
                    f"[V39-007] No existing {provider_code} client found in target repo. "
                    f"Will generate new client. Reasons: {client_decision.reasons[:2]}"
                )
                
        except Exception as e:
            logger.debug(
                f"[V39-007] Client detection failed (non-fatal, will generate new client): {e}"
            )
            # Continue with normal client generation
    
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
        # V36-003: Task-extracted overrides
        task_explicit_path=task_explicit_path,
        task_function_name=task_function_name,
        task_class_name=task_class_name,
        has_task_overrides=has_task_overrides,
        # V38-007: Async code generation flag
        is_async_required=is_async_required,
        # V38-008: Streaming response handling flag
        is_streaming_required=is_streaming_required,
        # V39-007: Existing client detection
        existing_client_detected=existing_client_detected,
        existing_client_class=existing_client_class,
        existing_client_import=existing_client_import,
        existing_client_module=existing_client_module,
        existing_client_methods=existing_client_methods,
        skip_client_generation=skip_client_generation,
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
