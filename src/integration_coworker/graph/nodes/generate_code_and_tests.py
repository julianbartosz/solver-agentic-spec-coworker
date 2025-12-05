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
    fix_code_style,
    check_syntax,
)
from integration_coworker.llm.content_policy import (
    validate_generated_code as validate_content_policy,
    format_violations as format_policy_violations,
)
from integration_coworker.llm.utils import strip_code_fences
from integration_coworker.feedback.hooks import (
    safe_record_syntax_check,
    safe_record_security_check,
    are_hooks_enabled,
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

        # P1: Inject policy code into client (skip for inline mode - policies embedded in template)
        policy_mode = state.options.policy_mode if state.options else "inline"
        if state.policies and policy_mode == "runtime":
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


def _get_template_key(state: WorkflowState) -> Optional[str]:
    """Get the template key from state for feedback recording."""
    if state.workflow_template and state.workflow_template.key:
        return state.workflow_template.key
    # Fallback to constructed key from provider + task
    provider = state.provider_code or "unknown"
    task_slug = state.integration_task.task_slug if state.integration_task else "task"
    return f"workflow.{provider}.{task_slug}"


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
    
    V1.1 FT-008 Strict Codegen Mode:
    - When state.options.strict_codegen=True:
      - Applies automatic style fixes via ruff
      - Performs stricter security validation
      - Fails on any validation issues instead of falling back to template
    
    Feedback Learning Hooks:
    - Records syntax check results (pass/fail) to update KG confidence
    - Records security validation results to update KG confidence
    - Hooks are isolated and never break the main workflow
    
    Args:
        template_code: The template/skeleton code
        state: WorkflowState for context
        artifact_type: "client", "flow", or "test"
        expected_class: Class name that must be present (for client)
        expected_function: Function name that must be present (for flow/test)
        endpoint: The endpoint being implemented
    
    Returns:
        Refined code, or template code if LLM fails validation (unless strict mode)
    
    Raises:
        ValueError: In strict mode, if code fails validation
    """
    module_name = expected_class or expected_function or artifact_type
    
    # V1.1: Check for strict codegen mode
    strict_mode = False
    if state.options and hasattr(state.options, 'strict_codegen'):
        strict_mode = state.options.strict_codegen
    
    # Feedback hooks context - get run_id and template_key for recording
    run_id = state.run_id or "unknown"
    template_key = _get_template_key(state) or "unknown"

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
            msg = f"LLM returned empty response for {artifact_type} '{module_name}'"
            if strict_mode:
                raise ValueError(f"Strict codegen failed: {msg}")
            logger.info(f"Falling back to template skeleton for {artifact_type} '{module_name}' because: {msg}")
            return template_code

        # Clean up markdown code blocks if present using utility
        clean_refined = strip_code_fences(refined)

        # REMOVED: Mock detection heuristic (LLM-007)
        # v2: We now trust the LLM with proper prompting instead of fragile
        # string-matching heuristics. Security validation below catches real issues.

        # V1.1 FT-008: Apply automatic style fixes in strict mode
        if strict_mode:
            logger.debug(f"Strict mode: applying ruff fixes to {artifact_type} '{module_name}'")
            clean_refined = fix_code_style(clean_refined)

        # Validate syntax with AST
        if not _validate_python_syntax(clean_refined):
            msg = f"LLM output failed AST syntax validation"
            # Record syntax failure for feedback learning (isolated, won't break flow)
            if are_hooks_enabled():
                is_valid, syntax_error = check_syntax(clean_refined)
                safe_record_syntax_check(
                    run_id=run_id,
                    template_key=template_key,
                    artifact_type=artifact_type,
                    success=False,
                    error_message=syntax_error,
                )
            if strict_mode:
                # In strict mode, also get detailed error from check_syntax
                is_valid, syntax_error = check_syntax(clean_refined)
                raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {syntax_error}")
            logger.warning(f"Falling back to template skeleton for {artifact_type} '{module_name}' because: {msg}")
            return template_code
        
        # Record syntax success for feedback learning
        if are_hooks_enabled():
            safe_record_syntax_check(
                run_id=run_id,
                template_key=template_key,
                artifact_type=artifact_type,
                success=True,
            )

        # v2: Security validation (SEC-003)
        is_secure, violations = validate_code_security(
            clean_refined,
            allow_subprocess=False,  # Default: no subprocess
            allow_file_io=True,       # Allow open() for config reading
        )
        
        if not is_secure:
            msg = f"security violations detected:\n{format_violations(violations)}"
            # Record security failure for feedback learning
            if are_hooks_enabled():
                safe_record_security_check(
                    run_id=run_id,
                    template_key=template_key,
                    success=False,
                    violations=[format_violations(violations)],
                )
            if strict_mode:
                raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {msg}")
            logger.warning(
                f"Falling back to template skeleton for {artifact_type} '{module_name}' "
                f"because: {msg}"
            )
            return template_code
        
        # Record security pass for feedback learning
        if are_hooks_enabled():
            safe_record_security_check(
                run_id=run_id,
                template_key=template_key,
                success=True,
            )

        # SEC-004: Content policy validation (semantic checks)
        is_policy_valid, policy_violations = validate_content_policy(
            clean_refined,
            endpoints=state.endpoints if state.endpoints else None,
        )
        
        if not is_policy_valid:
            msg = f"content policy violations detected:\n{format_policy_violations(policy_violations)}"
            if strict_mode:
                raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {msg}")
            logger.warning(
                f"Falling back to template skeleton for {artifact_type} '{module_name}' "
                f"because: {msg}"
            )
            return template_code

        # Validate expected symbols are present
        if expected_class and not _has_class(clean_refined, expected_class):
            msg = f"LLM output missing expected class '{expected_class}'"
            if strict_mode:
                raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {msg}")
            logger.warning(f"Falling back to template skeleton for {artifact_type} '{module_name}' because: {msg}")
            return template_code

        if expected_function and not _has_function(clean_refined, expected_function):
            msg = f"LLM output missing expected function '{expected_function}'"
            if strict_mode:
                raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {msg}")
            logger.warning(f"Falling back to template skeleton for {artifact_type} '{module_name}' because: {msg}")
            return template_code

        logger.info(f"Using LLM-generated body for {artifact_type} '{module_name}'" + (" [strict mode]" if strict_mode else ""))
        return clean_refined

    except ValueError:
        # Re-raise strict mode errors
        raise
    except Exception as e:
        msg = f"LLM refinement failed with exception: {e}"
        if strict_mode:
            raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {msg}")
        logger.warning(f"Falling back to template skeleton for {artifact_type} '{module_name}' because: {msg}")
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
    
    V2.1 (GAP-02): Supports policy_mode to switch between inline and runtime styles.
    
    Args:
        state: WorkflowState for context
        provider_code: Provider identifier (e.g., "stripe")
        client_class: Class name (e.g., "StripeClient")
        method_name: Main method name (e.g., "create_checkout_session")
        endpoint: Primary endpoint with path, method info
        base_url: API base URL
    """
    # V2.1 (GAP-02): Check policy_mode to determine generation style
    policy_mode = "inline"  # Default
    if state.options:
        policy_mode = getattr(state.options, 'policy_mode', 'inline')
    
    if policy_mode == "runtime":
        return _generate_runtime_client_code(
            state=state,
            provider_code=provider_code,
            client_class=client_class,
            method_name=method_name,
            endpoint=endpoint,
            base_url=base_url,
        )
    
    # Original inline code generation
    return _generate_inline_client_code(
        state=state,
        provider_code=provider_code,
        client_class=client_class,
        method_name=method_name,
        endpoint=endpoint,
        base_url=base_url,
    )


def _generate_runtime_client_code(
    state: WorkflowState,
    provider_code: str,
    client_class: str,
    method_name: str,
    endpoint: Optional[Endpoint],
    base_url: str,
) -> str:
    """
    Generate thin client code using the IntegrationClient runtime library.
    
    V2.1 (GAP-02): Produces ~30 LOC instead of ~300 LOC inline clients.
    Uses pluggable auth, retry, and rate limiting from runtime.
    
    Args:
        state: WorkflowState for context (includes policies)
        provider_code: Provider identifier
        client_class: Class name
        method_name: Main method name
        endpoint: Primary endpoint with path, method info
        base_url: API base URL
    """
    if not endpoint:
        return f'''"""
{provider_code.title()} API Client (Runtime-based)

Auto-generated by Integration Co-Worker v2.1
"""
# No endpoint found for binding - please configure endpoint bindings
'''

    endpoint_path = endpoint.path
    http_method = endpoint.method.lower() if endpoint.method else "post"
    summary = endpoint.summary or f"{http_method.upper()} {endpoint_path}"
    env_var = f"{provider_code.upper()}_API_KEY"
    
    # Determine auth class from policies
    auth_import = "NoAuth"
    auth_init = "NoAuth()"
    
    # Determine retry class from policies
    retry_import = "NoRetry"
    retry_init = "NoRetry()"
    
    # Determine rate limiter from policies
    rate_limit_import = "NoRateLimiter"
    rate_limit_init = "NoRateLimiter()"
    
    for policy in (state.policies or []):
        policy_type = policy.policy_type.value if hasattr(policy.policy_type, 'value') else str(policy.policy_type)
        
        if policy_type == "auth":
            hint = policy.implementation_hint or ""
            if "bearer" in hint.lower() or "token" in hint.lower():
                auth_import = "BearerAuth"
                auth_init = f'BearerAuth(env_var="{env_var}")'
            elif "api_key" in hint.lower():
                auth_import = "ApiKeyAuth"
                header = policy.config.get("header", "X-API-Key") if policy.config else "X-API-Key"
                auth_init = f'ApiKeyAuth(env_var="{env_var}", header="{header}")'
            else:
                auth_import = "BearerAuth"  # Default to bearer
                auth_init = f'BearerAuth(env_var="{env_var}")'
                
        elif policy_type == "retry":
            retry_import = "ExponentialRetry"
            max_attempts = 3
            if policy.config:
                max_attempts = policy.config.get("max_attempts", 3)
            retry_init = f"ExponentialRetry(max_attempts={max_attempts})"
            
        elif policy_type == "rate_limit":
            rate_limit_import = "TokenBucketRateLimiter"
            rps = 10
            if policy.config:
                rps = policy.config.get("rps", 10)
            rate_limit_init = f"TokenBucketRateLimiter(rps={rps})"
    
    # Build imports (deduplicate)
    imports = {"IntegrationClient"}
    imports.add(auth_import)
    imports.add(retry_import)
    imports.add(rate_limit_import)
    
    auth_imports = ", ".join(sorted([i for i in imports if i != "IntegrationClient" and i.endswith("Auth")]))
    retry_imports = ", ".join(sorted([i for i in imports if "Retry" in i]))
    rate_imports = ", ".join(sorted([i for i in imports if "RateLimiter" in i]))
    
    # Build request body handling
    body_arg = ""
    body_param = ""
    if http_method in ("post", "put", "patch"):
        body_arg = ", json=data"
        body_param = ", data: Dict[str, Any]"
    
    code = f'''"""
{provider_code.title()} API Client (Runtime-based)

Auto-generated by Integration Co-Worker v2.1
Uses integration_coworker_runtime library for auth, retry, and rate limiting.

Install: pip install integration-coworker-runtime
"""
import os
from typing import Dict, Any

from integration_coworker_runtime import IntegrationClient
from integration_coworker_runtime.auth import {auth_imports or "NoAuth"}
from integration_coworker_runtime.retry import {retry_imports or "NoRetry"}
from integration_coworker_runtime.rate_limit import {rate_imports or "NoRateLimiter"}


class {client_class}(IntegrationClient):
    """
    Client for {provider_code.title()} API.
    
    {summary}
    """
    
    def __init__(self):
        """Initialize with policies from spec analysis."""
        super().__init__(
            base_url="{base_url}",
            auth={auth_init},
            retry={retry_init},
            rate_limiter={rate_limit_init},
        )
    
    def {method_name}(self{body_param}) -> Dict[str, Any]:
        """
        {summary}
        
        Returns:
            API response as dictionary
        """
        return self.{http_method}("{endpoint_path}"{body_arg})
'''
    return code


def _generate_inline_client_code(
    state: WorkflowState,
    provider_code: str,
    client_class: str,
    method_name: str,
    endpoint: Optional[Endpoint],
    base_url: str,
) -> str:
    """
    Generate FULLY self-contained client code with inline auth, retry, rate-limiting.
    
    V2.1 FIX (ADR-0005): This is the "inline" mode that generates ~300 LOC clients
    with NO external dependencies beyond stdlib + httpx. Everything is embedded.
    
    Generated code uses ONLY:
    - Python stdlib (typing, time, random, logging, os)
    - httpx (must be pip installed)
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
    env_var = f"{provider_code.upper()}_API_KEY"

    # Determine body handling
    body_handling = ""
    body_param = ","  # Always include comma after self
    if http_method in ("POST", "PUT", "PATCH"):
        body_param = ",\n        payload: Dict[str, Any],"
        body_handling = """
            json=payload,"""
    
    # Build inline retry, rate-limiting code based on policies
    has_retry = any(
        (getattr(p.policy_type, 'value', str(p.policy_type)) == "retry")
        for p in (state.policies or [])
    )
    has_rate_limit = any(
        (getattr(p.policy_type, 'value', str(p.policy_type)) == "rate_limit")
        for p in (state.policies or [])
    )
    
    # Extra imports for retry/rate-limit
    extra_imports = ""
    if has_retry or has_rate_limit:
        extra_imports = """import time
import random
import threading"""
    
    # Inline retry mixin
    retry_mixin = ""
    retry_wrapper_start = ""
    retry_wrapper_end = ""
    request_indent = "        "  # Normal indentation (8 spaces)
    if has_retry:
        retry_mixin = '''
    # Retry configuration
    _max_retries: int = 3
    _base_delay: float = 1.0
    _max_delay: float = 60.0
    _retryable_status_codes: set = {429, 500, 502, 503, 504}
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter."""
        delay = self._base_delay * (2 ** attempt)
        delay = min(delay, self._max_delay)
        # Add jitter (up to 25%)
        jitter = delay * 0.25 * random.uniform(-1, 1)
        return max(0, delay + jitter)
    
    def _should_retry(self, status_code: int) -> bool:
        """Check if status code is retryable."""
        return status_code in self._retryable_status_codes
'''
        retry_wrapper_start = '''
        # Retry loop with exponential backoff
        last_exception = None
        for attempt in range(self._max_retries):
            try:
'''
        request_indent = "                "  # Inside try block (16 spaces)
        retry_wrapper_end = '''
                # Check for retryable status codes
                if response.status_code in self._retryable_status_codes:
                    if attempt < self._max_retries - 1:
                        delay = self._calculate_delay(attempt)
                        self._logger.info(f"Retrying after {delay:.2f}s (status {response.status_code})")
                        time.sleep(delay)
                        continue
                break
            except httpx.TransportError as e:
                last_exception = e
                if attempt < self._max_retries - 1:
                    delay = self._calculate_delay(attempt)
                    self._logger.info(f"Retrying after {delay:.2f}s (transport error)")
                    time.sleep(delay)
                else:
                    raise IntegrationError(f"Request failed after {self._max_retries} attempts: {last_exception}")'''

    # Inline rate limiter
    rate_limit_mixin = ""
    rate_limit_call = ""
    if has_rate_limit:
        rate_limit_mixin = '''
    # Rate limiting (token bucket)
    _rate_limit_rps: float = 10.0
    _rate_limit_burst: int = 20
    _rate_limit_tokens: float = 20.0
    _rate_limit_last_refill: float = 0.0
    _rate_limit_lock: threading.Lock = None
    
    def _init_rate_limiter(self):
        """Initialize rate limiter state."""
        if self._rate_limit_lock is None:
            self._rate_limit_lock = threading.Lock()
            self._rate_limit_last_refill = time.monotonic()
            self._rate_limit_tokens = float(self._rate_limit_burst)
    
    def _acquire_rate_limit_token(self):
        """Block until rate limit allows request."""
        self._init_rate_limiter()
        while True:
            with self._rate_limit_lock:
                now = time.monotonic()
                elapsed = now - self._rate_limit_last_refill
                new_tokens = elapsed * self._rate_limit_rps
                self._rate_limit_tokens = min(self._rate_limit_burst, self._rate_limit_tokens + new_tokens)
                self._rate_limit_last_refill = now
                
                if self._rate_limit_tokens >= 1.0:
                    self._rate_limit_tokens -= 1.0
                    return
                
                wait_time = (1.0 - self._rate_limit_tokens) / self._rate_limit_rps
            time.sleep(wait_time)
'''
        rate_limit_call = '''
        # Apply rate limiting
        self._acquire_rate_limit_token()
'''

    # Build the complete code
    code = f'''"""
{provider_code.title()} API Client (Standalone)

Auto-generated by Integration Co-Worker (inline mode)
Provider: {provider_code}
Endpoint: {http_method} {endpoint_path}

This is a FULLY SELF-CONTAINED client with no external dependencies
beyond Python stdlib and httpx.
"""
from typing import Dict, Any, Optional
import os
import logging
{extra_imports}

import httpx


class IntegrationError(Exception):
    """Base exception for integration errors."""
    pass


class {client_class}:
    """
    Client for {provider_code.title()} API.
    
    {summary}
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "{base_url}",
        timeout: float = 30.0,
    ):
        """
        Initialize the {client_class}.
        
        Args:
            api_key: API key for authentication (or set {env_var} env var)
            base_url: Base URL for the API
            timeout: Request timeout in seconds
        """
        self.api_key = api_key or os.environ.get("{env_var}", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)
        self._logger = logging.getLogger(self.__class__.__name__)
    {retry_mixin}{rate_limit_mixin}
    def close(self):
        """Close the HTTP client."""
        self._client.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def {method_name}(
        self{body_param}
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        {summary}
        
        Args:
            payload: Request payload (for POST/PUT/PATCH)
            idempotency_key: Optional idempotency key for safe retries
        
        Returns:
            API response as dictionary
        
        Raises:
            IntegrationError: If the API request fails
        """
        url = f"{{self.base_url}}{endpoint_path}"
        
        headers = {{}}
        if self.api_key:
            headers["Authorization"] = f"Bearer {{self.api_key}}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
{rate_limit_call}{retry_wrapper_start}{request_indent}response = self._client.request(
{request_indent}    "{http_method}",
{request_indent}    url,
{request_indent}    headers=headers,{body_handling}
{request_indent})
{retry_wrapper_end}
        if response.status_code not in (200, 201, 204):
            raise IntegrationError(
                f"API request failed: {{response.status_code}} - {{response.text[:200]}}"
            )
        
        if response.status_code == 204 or not response.content:
            return {{}}
        
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
    
    V2.1: Generates self-contained flow code. The IntegrationError is imported
    from the client module (which defines it inline in inline mode).
    
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
    
    # Determine exception import based on policy_mode
    policy_mode = "inline"
    if state.options:
        policy_mode = getattr(state.options, 'policy_mode', 'inline')
    
    if policy_mode == "runtime":
        exception_import = "from integration_coworker_runtime import IntegrationError"
    else:
        # Inline mode: import from client module which defines IntegrationError
        exception_import = f"from {client_import_module} import IntegrationError"

    code = f'''"""
{provider_code.title()} {task_slug.replace('_', ' ').title()} Flow

Auto-generated by Integration Co-Worker
Task: {task_desc}
"""
from typing import Dict, Any, Optional
from {client_import_module} import {client_class}
{exception_import}


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
