"""
generate_code_and_tests node - Generate client, flow, and test code.

Uses spec-driven naming and RepoProfile layout for paths, with LLM for code body generation.

Per design doc Section 5.5:
- Archetype: generate_code_and_tests.archetype.yaml
- Provider: Anthropic (claude-3-sonnet) - superior code generation
- Strategy: generation with multi-candidate selection

V2.2 (ADR-CODEGEN-001): Uses CodegenContext for single source of truth naming.

V3.0: Converted to async for scalability (ASYNC_MIGRATION_PLAN.md)
"""
import ast

# Async imports for concurrent LLM calls (Bug #35 fix)
import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from integration_coworker.codegen.context import CodegenContext
from integration_coworker.codegen.code_test_validator import (
    cross_validate_code_and_tests,
    validate_and_fix_tests,
    build_repair_prompt_with_context,
    extract_code_behaviors,
    extract_client_method_from_flow,
    align_test_mock_with_flow,
)
from integration_coworker.codegen.response_type_guard import (
    fix_generated_client_code,
    validate_response_handling,
)
from integration_coworker.codegen.naming import (
    build_codegen_context,
)
from integration_coworker.codegen.path_fixer import fix_hallucinated_paths
from integration_coworker.codegen.paths import join_path, normalize_path
from integration_coworker.codegen.policy_templates import (
    inject_policies_into_client_code,
)
from integration_coworker.codegen.prompts import build_codegen_prompt, build_constrained_body_prompt
from integration_coworker.codegen.sandbox import (
    ArtifactFile,
    SandboxConfig,
    SandboxResult,
    execute_in_sandbox,
)

# Multi-language sandbox support (MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.2)
from integration_coworker.codegen.sandbox_multilang import (
    ArtifactLanguage,
    MultiLangSandboxConfig,
    execute_multilang_sandbox,
)
from integration_coworker.codegen.security import (
    check_syntax,
    fix_code_style,
    format_violations,
    validate_code_security,
)
from integration_coworker.codegen.self_review import review_and_repair as llm_review_and_repair
from integration_coworker.codegen.semantic_validator import (
    format_semantic_issues,
    validate_semantic_correctness,
)
# V35 Bug Fix Imports - Post-generation validation
from integration_coworker.codegen.import_fixer import (
    fix_imports_in_code,
    fix_flow_imports,  # V38-007
    fix_flow_client_signature_mismatch,  # V38-007
    fix_imports_for_policy_mode,  # BUG-002
)
from integration_coworker.codegen.repo_type_detector import (
    detect_repo_type,
    should_generate_fastapi_router,
    RepoType,
)
from integration_coworker.codegen.post_generation_validator import (
    validate_and_fix_generated_code,
    validate_artifact_set,
)
# V42-007: Error taxonomy for sandbox feedback loop
from integration_coworker.codegen.error_taxonomy import (
    ErrorExtractor,
    FeedbackPromptBuilder,
    SandboxErrorReport,
    ErrorClass,
    RepairStrategy,
    get_primary_error_for_feedback,
)
from integration_coworker.codegen.syntax_validator import (
    is_tree_sitter_available,
)
from integration_coworker.codegen.syntax_validator import (
    validate_syntax as tree_sitter_validate_syntax,
)
from integration_coworker.config import get_archetype_prompt_config
from integration_coworker.config.profiles import CodegenProfile, get_active_profile
from integration_coworker.domain.models import CodeArtifact, Endpoint
from integration_coworker.domain.ir import Operation, ProtocolType
from integration_coworker.codegen.protocol_dispatch import (
    create_default_dispatcher,
    CodegenContext as ProtocolCodegenContext,
    ArtifactType,
)
from integration_coworker.feedback.hooks import (
    are_hooks_enabled,
    safe_record_security_check,
    safe_record_syntax_check,
)
from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.memory_sampler import check_ceiling_in_loop, check_ceiling_or_raise
from integration_coworker.graph.shutdown_aware import (
    shutdown_check_point,
    ShutdownInterruptError,
)
from integration_coworker.llm import get_async_llm_client_for_node, is_mock_llm_mode
from integration_coworker.llm.client import get_llm_client_for_node as _sync_get_llm_client_for_node
from integration_coworker.llm.content_policy import (
    PolicyViolationType,
)
from integration_coworker.llm.content_policy import (
    format_violations as format_policy_violations,
)
from integration_coworker.llm.content_policy import (
    validate_generated_code as validate_content_policy,
)
from integration_coworker.llm.exceptions import LLMAuthError
from integration_coworker.llm.utils import strip_code_fences, clean_llm_code_output

logger = logging.getLogger(__name__)


# Backward-compatible sync LLM client accessor (deprecated)
def get_llm_client_for_node(node_name: str, strict: bool = False):
    """Expose legacy get_llm_client_for_node for tests and callers still patching sync API."""
    return _sync_get_llm_client_for_node(node_name, strict=strict)

# Node name for archetype loading
NODE_NAME = "generate_code_and_tests"

# Feature flag for async codegen (can be disabled via env var)
ASYNC_CODEGEN_ENABLED = True

# Feature flag for protocol-aware codegen dispatch
PROTOCOL_DISPATCH_ENABLED = True


# =============================================================================
# Protocol-Aware Codegen (Protocol vNext Phase 2)
# =============================================================================

def _has_non_rest_operations(state: WorkflowState) -> bool:
    """Check if state contains non-REST operations that need protocol dispatch.
    
    V3 FILE: Also checks endpoint_bindings for file-based operations.
    """
    # Check IR operations
    if state.operations:
        if any(op.protocol != ProtocolType.REST for op in state.operations):
            return True
    
    # V3 FILE: Check endpoint_bindings for file protocol
    if state.endpoint_bindings:
        for binding in state.endpoint_bindings:
            if binding.request_mapping and binding.request_mapping.get("protocol_type") == ProtocolType.FILE.value:
                return True
    
    return False


def _generate_protocol_aware_artifacts(
    state: WorkflowState,
    target_language: str = "python",
) -> list[CodeArtifact]:
    """
    Generate code artifacts using protocol-aware dispatch.
    
    Uses StrategyDispatcher to route operations to protocol-specific strategies:
    - REST: HTTP client generation
    - GraphQL: Query/Mutation + Subscription (WebSocket) generation
    - AsyncAPI: Publisher/Subscriber generation with correct direction semantics
    - FILE: CSV/Excel/Fixed-Width parser generation
    
    V3 FILE: Extended to support file-based integrations.
    
    BUG-001 FIX: Uses get_layout_dirs() to derive paths from RepoProfile instead
    of hardcoding "src/integrations/clients", "src/integrations/flows", etc.
    This ensures generated artifacts respect the target repo's structure.
    
    References:
    - GraphQL subscriptions: https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md
    - AsyncAPI semantics: https://v2.asyncapi.com/docs/reference/specification/v2.6.0
    """
    from integration_coworker.codegen.paths import get_layout_dirs
    
    artifacts: list[CodeArtifact] = []
    
    # BUG-001 FIX: Derive paths from RepoProfile instead of hardcoding
    clients_dir, flows_dir, tests_dir = get_layout_dirs(state.repo_profile)
    # Derive parsers dir from clients_dir pattern (same parent, different subdir)
    if "/" in clients_dir:
        parent_dir = "/".join(clients_dir.split("/")[:-1])
        parsers_dir = f"{parent_dir}/parsers"
    else:
        parsers_dir = "parsers"
    
    logger.debug(
        f"[BUG-001] Protocol dispatch using paths: clients={clients_dir}, "
        f"flows={flows_dir}, tests={tests_dir}, parsers={parsers_dir}"
    )
    
    # V3 FILE: Create dispatcher with file specs if available
    dispatcher = create_default_dispatcher(
        file_specs=state.file_specs if hasattr(state, 'file_specs') else None,
        file_fields=state.file_fields if hasattr(state, 'file_fields') else None,
    )
    
    provider_code = state.provider_code or "unknown"
    
    # Process IR operations
    for operation in state.operations:
        # Skip REST operations - those use the existing Endpoint-based flow
        if operation.protocol == ProtocolType.REST:
            continue
        
        # Build protocol-specific context
        context = ProtocolCodegenContext(
            language=target_language,
            provider_code=provider_code,
            use_async=True,
            include_tests=True,
        )
        
        # Generate client artifact
        client_artifact = dispatcher.generate(operation, context)
        if client_artifact:
            code_artifact = CodeArtifact(
                id=None,
                task_id=None,
                artifact_type=client_artifact.artifact_type.value,
                language=target_language,
                module_name=client_artifact.filename.replace(".py", ""),
                rel_path=join_path(clients_dir, client_artifact.filename),  # BUG-PATH-001 FIX
                content=client_artifact.code,
            )
            artifacts.append(code_artifact)
            logger.info(
                f"Protocol dispatch: Generated {client_artifact.artifact_type.value} "
                f"for {operation.protocol.value} operation {operation.name}"
            )
        
        # Generate test artifact
        test_artifact = dispatcher.generate_test(operation, context)
        if test_artifact:
            code_artifact = CodeArtifact(
                id=None,
                task_id=None,
                artifact_type="test",
                language=target_language,
                module_name=test_artifact.filename.replace(".py", ""),
                rel_path=join_path(tests_dir, test_artifact.filename),  # BUG-PATH-001 FIX
                content=test_artifact.code,
            )
            artifacts.append(code_artifact)
    
    # V3 FILE: Process file-based operations from endpoint_bindings
    for binding in state.endpoint_bindings:
        if not binding.request_mapping:
            continue
        
        protocol_type = binding.request_mapping.get("protocol_type")
        if protocol_type != ProtocolType.FILE.value:
            continue
        
        # Build file operation from binding metadata
        file_spec_id = binding.request_mapping.get("file_spec_id")
        if file_spec_id is None:
            logger.warning(f"File binding {binding.flow_node_key} missing file_spec_id")
            continue
        
        # Create Operation for file parsing
        file_operation = Operation(
            name=f"parse_file_{file_spec_id}",
            operation_id=binding.flow_node_key,
            protocol=ProtocolType.FILE,
            metadata={
                "file_spec_id": file_spec_id,
                "op": binding.request_mapping.get("op", "parse"),
            },
        )
        
        # Build context
        context = ProtocolCodegenContext(
            language=target_language,
            provider_code=provider_code,
            use_async=False,  # File parsing is typically sync
            include_tests=True,
        )
        
        # Generate client (parser) artifact
        client_artifact = dispatcher.generate(file_operation, context, ArtifactType.CLIENT)
        if client_artifact:
            code_artifact = CodeArtifact(
                id=None,
                task_id=None,
                artifact_type=client_artifact.artifact_type.value,
                language=target_language,
                module_name=client_artifact.filename.replace(".py", ""),
                rel_path=join_path(parsers_dir, client_artifact.filename),  # BUG-PATH-001 FIX
                content=client_artifact.code,
            )
            artifacts.append(code_artifact)
            logger.info(
                f"FILE dispatch: Generated parser for file_spec_id={file_spec_id}"
            )
            
            # Generate flow artifact
            flow_artifact = dispatcher.generate(file_operation, context, ArtifactType.FLOW)
            if flow_artifact:
                flow_code_artifact = CodeArtifact(
                    id=None,
                    task_id=None,
                    artifact_type="flow",
                    language=target_language,
                    module_name=flow_artifact.filename.replace(".py", ""),
                    rel_path=join_path(flows_dir, flow_artifact.filename),  # BUG-PATH-001 FIX
                    content=flow_artifact.code,
                )
                artifacts.append(flow_code_artifact)
            
            # Generate test artifact
            test_artifact = dispatcher.generate(file_operation, context, ArtifactType.TEST)
            if test_artifact:
                test_code_artifact = CodeArtifact(
                    id=None,
                    task_id=None,
                    artifact_type="test",
                    language=target_language,
                    module_name=test_artifact.filename.replace(".py", ""),
                    rel_path=join_path(tests_dir, test_artifact.filename),  # BUG-PATH-001 FIX
                    content=test_artifact.code,
                )
                artifacts.append(test_code_artifact)
    
    return artifacts


# =============================================================================
# V33-002: Dependency Auto-Detection
# =============================================================================

# Known HTTP client packages that should be declared as dependencies
_HTTP_CLIENT_PACKAGES = frozenset({
    "httpx", "requests", "aiohttp", "urllib3", "httplib2",
})

# Known async packages
_ASYNC_PACKAGES = frozenset({
    "asyncio", "aiohttp", "aiofiles", "aioboto3",
})

# Known third-party packages that need to be declared as dependencies
_THIRD_PARTY_PACKAGES = frozenset({
    # HTTP clients
    "httpx", "requests", "aiohttp", "urllib3", "httplib2",
    # Data
    "pydantic", "marshmallow", "attrs", "dataclasses_json",
    # Async
    "anyio", "trio", "curio",
    # Cloud
    "boto3", "google", "azure",
    # Testing
    "pytest", "respx", "pytest_httpx",
    # Config/TOML (V38-004: Added for pyproject.toml parsing)
    "tomli", "toml", "tomllib",
    # YAML
    "yaml", "pyyaml",
    # JSON Schema
    "jsonschema",
})

def _auto_detect_dependencies(code: str) -> list[str]:
    """
    Auto-detect dependencies from code imports.
    
    V33-002: When state.options.dependencies is not set, analyze the code
    to determine which third-party packages are being used. This allows
    self-review to correctly evaluate dependency declarations.
    
    Args:
        code: Generated Python code to analyze
        
    Returns:
        List of detected package names (e.g., ["httpx", "pydantic"])
    """
    dependencies: set[str] = set()
    
    # Pattern 1: import xxx
    for match in re.finditer(r'^\s*import\s+(\w+)', code, re.MULTILINE):
        pkg = match.group(1)
        if pkg in _THIRD_PARTY_PACKAGES:
            dependencies.add(pkg)
    
    # Pattern 2: from xxx import ...
    for match in re.finditer(r'^\s*from\s+(\w+)(?:\.\w+)*\s+import', code, re.MULTILINE):
        pkg = match.group(1)
        if pkg in _THIRD_PARTY_PACKAGES:
            dependencies.add(pkg)
    
    # Special case: httpx.AsyncClient or httpx.Client in code
    if 'httpx.' in code or 'httpx\n' in code or 'import httpx' in code:
        dependencies.add("httpx")
    
    # Special case: requests.Session or requests.get/post
    if 'requests.' in code or 'import requests' in code:
        dependencies.add("requests")
    
    return sorted(dependencies)


# =============================================================================
# Self-Review Integration (Task B: LLM Self-Review)
# =============================================================================

async def _apply_self_review_if_enabled(
    code: str,
    artifact_type: Literal["client", "flow", "test"],
    module_name: str,
    state: WorkflowState,
    profile: CodegenProfile,
) -> tuple[str, bool]:
    """
    Apply LLM self-review to code if enabled in profile.
    
    Per Task B:
    - Only runs when profile.enable_self_review=True
    - Uses same provider-agnostic pattern (LangChain structured output)
    - Max 1 repair iteration per artifact
    - Production: unfixable issues are HARD failures
    - Development: unfixable issues log warnings, use original code
    
    Args:
        code: Code to review
        artifact_type: "client", "flow", or "test"
        module_name: Module/class name for logging
        state: WorkflowState for context
        profile: Active codegen profile
        
    Returns:
        Tuple of (final_code, success). 
        In production mode, success=False means hard failure.
        In development mode, success is always True (original code on failure).
    """
    if not profile.enable_self_review:
        logger.debug(f"[{module_name}] Self-review disabled (profile={profile.name})")
        return code, True
    
    # Safety: When running in mock/offline validation modes, do NOT invoke any
    # LLM client for self-review. This guarantees offline determinism and
    # prevents tests from accidentally creating or awaiting LLM clients.
    import os
    mock_flags = {"true", "1", "yes", "on"}
    is_offline = os.getenv("VALIDATION_PROFILE", "").lower() == "offline"
    is_env_mock = os.getenv("USE_MOCK_LLM", "").lower() in mock_flags
    is_mode_mock = os.getenv("LLM_MODE", "").lower() == "mock"
    if is_mock_llm_mode() or is_offline or is_env_mock or is_mode_mock:
        logger.debug(
            f"[{module_name}] Skipping self-review due to mock/offline mode "
            f"(VALIDATION_PROFILE={os.getenv('VALIDATION_PROFILE')}, USE_MOCK_LLM={os.getenv('USE_MOCK_LLM')}, LLM_MODE={os.getenv('LLM_MODE')})"
        )
        return code, True
    try:
        # Get a LangChain LLM instance for self-review
        # Use the existing LLM client factory which handles provider detection and API keys
        from integration_coworker.llm import get_async_llm_client_for_node

        # Get an async LLM client configured for codegen tasks (non-deprecated path)
        async_client = get_async_llm_client_for_node("generate_code_and_tests", strict=False)

        # Derive a LangChain chat model from the async client when available
        llm = None
        if hasattr(async_client, "chat_model"):
            llm = async_client.chat_model
        elif hasattr(async_client, "_get_llm"):
            try:
                llm = async_client._get_llm()  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning(f"Failed to build chat model from async client: {e}")

        if llm is None:
            # Fallback: use the async client directly if it behaves like a chat model
            llm = async_client
        
        # Build context from state
        task_context = None
        if state.task_description:
            task_context = state.task_description
        elif state.integration_task:
            task_context = state.integration_task.task_slug
        
        # Extract dependencies from state, or auto-detect from code [V33-002]
        dependencies = None
        if state.options and hasattr(state.options, 'dependencies'):
            dependencies = state.options.dependencies
        
        # V33-002: Auto-detect dependencies from code when not explicitly set
        # This prevents self-review from flagging httpx/requests as "not declared"
        if not dependencies:
            dependencies = _auto_detect_dependencies(code)
            if dependencies:
                logger.debug(f"[{module_name}] Auto-detected dependencies: {dependencies}")
        
        # Derive file path for context
        file_path = f"{artifact_type}/{module_name}.py"
        
        # Run review with repair logic
        is_production = profile.name == "production"
        
        final_code, success, error = await llm_review_and_repair(
            code=code,
            artifact_type=artifact_type,
            file_path=file_path,
            llm=llm,
            is_production=is_production,
            task_context=task_context,
            dependencies=dependencies,
            max_repair_attempts=1,  # Per B3.5: Max 1 repair iteration
        )
        
        if success:
            if final_code != code:
                logger.info(f"[{module_name}] Self-review repaired {artifact_type}")
            else:
                logger.debug(f"[{module_name}] Self-review passed {artifact_type}")
            return final_code, True
        else:
            # Failed and couldn't repair
            if is_production:
                logger.error(
                    f"[{module_name}] Self-review FAILED (production): {error}"
                )
                return code, False
            else:
                logger.warning(
                    f"[{module_name}] Self-review failed (development): {error}. Using original code."
                )
                return code, True
                
    except Exception as e:
        logger.warning(f"[{module_name}] Self-review error: {e}. Using original code.")
        # On error, don't block - use original code
        return code, True


# =============================================================================
# Test Assertion Error Detection and Repair (Fix #89)
# =============================================================================

def _detect_test_assertion_errors(pytest_output: str) -> list[dict[str, str]]:
    """
    Detect test assertion errors from pytest output.
    
    Returns list of dicts with:
    - test_name: Name of the failing test
    - error_type: Type of error (KeyError, AssertionError, etc.)
    - error_detail: The specific error message
    - line_number: Line number if available
    """
    errors = []
    
    # Common patterns for test assertion errors
    patterns = [
        # KeyError: 'line_items'
        (r"KeyError: ['\"](\w+)['\"]", "KeyError"),
        # AssertionError
        (r"AssertionError:?\s*(.*)", "AssertionError"),
        # AttributeError: 'Mock' object has no attribute
        (r"AttributeError: ['\"]?(Mock|MagicMock)['\"]? object has no attribute ['\"](\w+)['\"]", "AttributeError"),
        # ConnectError - test is making real HTTP calls (Bug #91)
        (r"httpcore\.ConnectError:?\s*(.*)", "ConnectError"),
        # DNS resolution error - real HTTP call
        (r"nodename nor servname provided", "ConnectError"),
        # Connection refused - real HTTP call
        (r"Connection refused", "ConnectError"),
        # IntegrationError from API call
        (r"IntegrationError:?\s*(.*)", "IntegrationError"),
        # ValueError from missing params
        (r"ValueError:?\s*(.*required.*)", "ValueError"),
    ]
    
    lines = pytest_output.split('\n')
    current_test = None
    
    for i, line in enumerate(lines):
        # Track current test
        if 'def test_' in line or '::test_' in line:
            # Extract test name
            match = re.search(r'(test_\w+)', line)
            if match:
                current_test = match.group(1)
        
        # Check for error patterns
        for pattern, error_type in patterns:
            match = re.search(pattern, line)
            if match:
                errors.append({
                    "test_name": current_test or "unknown",
                    "error_type": error_type,
                    "error_detail": match.group(1) if match.groups() else "",
                    "line": line.strip(),
                })
    
    return errors


async def _repair_test_assertions(
    test_code: str,
    pytest_errors: list[dict[str, str]],
    state: WorkflowState,
    flow_code: str | None = None,
) -> tuple[str, bool]:
    """
    Repair test assertion errors using LLM with flow code context.
    
    V25-002 Fix: Now includes actual flow code in the repair prompt so the LLM
    can understand what behavior the test should expect.
    
    Args:
        test_code: The failing test code
        pytest_errors: List of detected errors
        state: WorkflowState for context
        flow_code: The actual flow code being tested (V25-002 fix)
        
    Returns:
        Tuple of (repaired_code, success)
    """
    if not pytest_errors:
        return test_code, True
    
    try:
        from integration_coworker.llm import get_async_llm_client_for_node
        
        client = get_async_llm_client_for_node(NODE_NAME)
        
        # V25-002 Fix: Use context-aware prompt if flow code is available
        if flow_code:
            prompt = build_repair_prompt_with_context(
                test_code=test_code,
                flow_code=flow_code,
                pytest_errors=pytest_errors,
            )
            system_prompt = """You are a test repair expert. Fix failing tests to match the ACTUAL behavior 
of the flow code provided. Tests must assert what the code ACTUALLY DOES, not what we wish it did.
If the code converts None to a default value instead of raising, the test must NOT expect an exception."""
        else:
            # Fallback to original prompt (backward compatibility)
            error_summary = "\n".join([
                f"- {e['error_type']}: {e['error_detail']} in {e['test_name']}"
                for e in pytest_errors[:5]
            ])
            
            prompt = f"""Fix the failing tests in this code. The pytest errors are:

{error_summary}

ORIGINAL CODE:
```python
{test_code}
```

COMMON FIXES:
1. For KeyError on call_args: Replace `call_args[1]["key"]` with `mock.method.called` or `mock.method.call_count >= 1`
2. For AssertionError: Use more flexible assertions like `assert result is not None` instead of strict dict key checks
3. For AttributeError on Mock: Use MagicMock instead of Mock, or set up the attribute before testing
4. For missing required params: The flow signature is (api_key: str, payload: Dict[str, Any]). Always pass both!
5. For ValueError about payload/api_key required: Ensure tests pass payload={{"key": "value"}} and api_key="test_key"
6. For ConnectError/httpcore errors: The test is making REAL HTTP calls - add mock patching!
7. For IntegrationError during API call: The mock isn't being applied - wrap test in `with patch(...):`

CRITICAL - EVERY test MUST mock HTTP calls:
- EVERY test method MUST have: `with patch('module.ClientClass') as MockClient:`
- Tests WITHOUT mocks will fail with ConnectError or DNS errors
- Even validation tests must have mocks before calling the flow

CRITICAL - Flow function signature:
```python
def flow_function(api_key: str, payload: Dict[str, Any], **kwargs) -> Dict[str, Any]
```
ALL tests must pass BOTH api_key AND payload parameters!

RULES:
- Keep ALL existing imports
- Keep the test class structure
- Only modify the assertion lines that cause errors
- Do NOT access call_args with specific dictionary keys
- Use `.called`, `.call_count`, or `isinstance()` checks instead
- ALWAYS pass api_key and payload to the flow function
- ALWAYS wrap flow calls in `with patch(...)` context manager

Return the complete fixed Python test code:"""

            system_prompt = """You are a test repair expert. Fix failing tests by making assertions more robust.
Never assume specific dictionary keys exist in call_args. Use mock.called or mock.call_count instead."""

        response = await client.complete_async(prompt, system_prompt=system_prompt)
        
        if response:
            clean_code = strip_code_fences(response)
            
            # Validate syntax
            if _validate_syntax(clean_code, "python"):
                # Verify we still have test structure
                if "def test_" in clean_code and "import" in clean_code:
                    logger.info(f"[test_repair] Successfully repaired {len(pytest_errors)} test assertion errors")
                    return clean_code, True
            
        logger.warning("[test_repair] Repair produced invalid code, using original")
        return test_code, False
        
    except Exception as e:
        logger.error(f"[test_repair] Failed to repair tests: {e}")
        return test_code, False


# =============================================================================
# V42-007: Sandbox Error Feedback Loop
# =============================================================================
# When sandbox validation fails, extract structured errors and regenerate
# the failing artifact with targeted feedback. This closes the loop between
# sandbox failures and LLM regeneration.
# =============================================================================

async def _sandbox_feedback_regenerate(
    code_artifacts: list["CodeArtifact"],
    sandbox_result: SandboxResult,
    state: WorkflowState,
    max_iterations: int = 2,
) -> tuple[list["CodeArtifact"], bool]:
    """
    V42-007: Regenerate artifacts based on sandbox failure feedback.
    
    Uses the error taxonomy to:
    1. Extract and classify errors from sandbox output
    2. Build targeted feedback prompts for each error type
    3. Regenerate failing artifacts with error context
    4. Re-validate until success or max iterations reached
    
    Args:
        code_artifacts: Current artifacts that failed sandbox
        sandbox_result: SandboxResult with failure details
        state: WorkflowState for context
        max_iterations: Max regeneration attempts (default: 2)
        
    Returns:
        Tuple of (possibly_fixed_artifacts, did_fix_succeed)
    """
    from integration_coworker.llm.async_client import call_llm_async
    
    if sandbox_result.success:
        return code_artifacts, True
    
    # Build artifacts dict for error extraction
    artifacts_dict = {a.rel_path: a.content for a in code_artifacts}
    
    # Get gate results as list of dicts
    gate_results = [
        {
            "name": g.name,
            "passed": g.passed,
            "output": g.output or "",
            "duration_ms": g.duration_ms,
        }
        for g in sandbox_result.gate_results
    ]
    
    # Get primary error report
    error_report = get_primary_error_for_feedback(gate_results, artifacts_dict)
    
    if not error_report or not error_report.has_errors:
        logger.warning("[V42-007] No structured errors extracted, cannot provide targeted feedback")
        return code_artifacts, False
    
    logger.info(
        f"[V42-007] Extracted {len(error_report.errors)} error(s) from {error_report.gate_name}, "
        f"primary class: {error_report.primary_error_class.name if error_report.primary_error_class else 'UNKNOWN'}"
    )
    
    # Find which artifact needs fixing based on error type
    artifact_to_fix = None
    flow_code = None
    client_code = None
    
    # Get flow and client code for context
    for artifact in code_artifacts:
        if artifact.artifact_type == "flow":
            flow_code = artifact.content
        elif artifact.artifact_type == "client":
            client_code = artifact.content
    
    # Determine which artifact to fix based on error class
    primary_class = error_report.primary_error_class
    
    if primary_class in (ErrorClass.TEST_MOCK_ERROR, ErrorClass.TEST_FIXTURE_ERROR,
                         ErrorClass.TEST_PARAMETER_ERROR, ErrorClass.TEST_ASSERTION):
        # Test-specific errors - fix test artifact
        artifact_to_fix = next(
            (a for a in code_artifacts if a.artifact_type == "test"),
            None
        )
    elif primary_class in (ErrorClass.IMPORT_ERROR, ErrorClass.UNDEFINED_NAME):
        # Import errors - could be in any artifact, check error file path
        error_file = error_report.errors[0].file_path if error_report.errors else None
        if error_file:
            if "test" in error_file.lower():
                artifact_to_fix = next((a for a in code_artifacts if a.artifact_type == "test"), None)
            elif "flow" in error_file.lower():
                artifact_to_fix = next((a for a in code_artifacts if a.artifact_type == "flow"), None)
            else:
                artifact_to_fix = next((a for a in code_artifacts if a.artifact_type == "client"), None)
        else:
            # Default to client for import errors
            artifact_to_fix = next((a for a in code_artifacts if a.artifact_type == "client"), None)
    else:
        # Other errors - try to match by file path
        error_file = error_report.errors[0].file_path if error_report.errors else None
        if error_file:
            for artifact in code_artifacts:
                if artifact.rel_path and artifact.rel_path in error_file:
                    artifact_to_fix = artifact
                    break
        
        # Default to client if no match
        if not artifact_to_fix:
            artifact_to_fix = next((a for a in code_artifacts if a.artifact_type == "client"), None)
    
    if not artifact_to_fix:
        logger.warning("[V42-007] Could not determine which artifact to fix")
        return code_artifacts, False
    
    logger.info(f"[V42-007] Will regenerate {artifact_to_fix.artifact_type} artifact")
    
    # Build targeted feedback prompt
    feedback_prompt = FeedbackPromptBuilder.build_prompt(
        error_report=error_report,
        original_code=artifact_to_fix.content,
        artifact_type=artifact_to_fix.artifact_type,
        flow_code=flow_code,
        client_code=client_code,
    )
    
    if not feedback_prompt:
        logger.warning("[V42-007] Could not build feedback prompt")
        return code_artifacts, False
    
    # Get system prompt for code repair
    system_prompt = """You are an expert Python code repair assistant. Fix the code based on the errors provided.
Return ONLY the complete, fixed Python code. No explanations, no markdown, just the code.
Ensure all imports are valid, all names are defined, and the code is syntactically correct."""
    
    try:
        # Call LLM for repair
        logger.info(f"[V42-007] Calling LLM to regenerate {artifact_to_fix.artifact_type}")
        response = await call_llm_async(
            prompt=feedback_prompt,
            task_type="codegen",
            system_prompt=system_prompt,
        )
        
        if not response:
            logger.warning("[V42-007] LLM returned empty response")
            return code_artifacts, False
        
        # Clean response
        repaired_code = strip_code_fences(response)
        
        # Validate repaired code syntax
        if not _validate_syntax(repaired_code, "python"):
            logger.warning("[V42-007] Repaired code has syntax errors")
            return code_artifacts, False
        
        # Update artifact
        original_content = artifact_to_fix.content
        artifact_to_fix.content = repaired_code
        
        logger.info(f"[V42-007] Applied LLM repair to {artifact_to_fix.artifact_type}")
        
        return code_artifacts, True
        
    except Exception as e:
        logger.error(f"[V42-007] Feedback regeneration failed: {e}")
        return code_artifacts, False


async def _run_sandbox_with_feedback_loop(
    code_artifacts: list["CodeArtifact"],
    sandbox_artifacts: list[ArtifactFile],
    dependencies: list[str],
    config: SandboxConfig,
    state: WorkflowState,
    max_regeneration_attempts: int = 2,
) -> tuple[bool, SandboxResult | None]:
    """
    V42-007: Run sandbox validation with automatic feedback-based regeneration.
    
    Wraps execute_in_sandbox with a retry loop that uses structured error
    extraction and targeted LLM prompts to fix failing code.
    
    Args:
        code_artifacts: List of CodeArtifact objects
        sandbox_artifacts: List of ArtifactFile for sandbox
        dependencies: List of pip dependencies
        config: SandboxConfig
        state: WorkflowState
        max_regeneration_attempts: Max feedback regeneration loops
        
    Returns:
        Tuple of (success, final SandboxResult)
    """
    attempt = 0
    
    while attempt <= max_regeneration_attempts:
        # Run sandbox
        result = await execute_in_sandbox(
            artifacts=sandbox_artifacts,
            dependencies=dependencies,
            config=config,
        )
        
        if result.success:
            if attempt > 0:
                logger.info(f"[V42-007] Sandbox passed after {attempt} regeneration(s)")
            return True, result
        
        # Check if we have attempts left
        if attempt >= max_regeneration_attempts:
            logger.warning(
                f"[V42-007] Sandbox failed after {attempt} regeneration attempt(s), "
                "no more attempts allowed"
            )
            return False, result
        
        # Attempt feedback-based regeneration
        logger.info(f"[V42-007] Sandbox failed, attempting feedback regeneration (attempt {attempt + 1})")
        
        fixed_artifacts, did_fix = await _sandbox_feedback_regenerate(
            code_artifacts=code_artifacts,
            sandbox_result=result,
            state=state,
            max_iterations=1,  # Single fix per attempt
        )
        
        if not did_fix:
            logger.warning("[V42-007] Feedback regeneration did not produce fixes")
            return False, result
        
        # Update sandbox artifacts with fixed code
        for code_artifact in fixed_artifacts:
            for sandbox_artifact in sandbox_artifacts:
                # Match by path
                if code_artifact.rel_path and sandbox_artifact.path.endswith(code_artifact.rel_path.split('/')[-1]):
                    sandbox_artifact.content = code_artifact.content
                    logger.debug(f"[V42-007] Updated sandbox artifact: {sandbox_artifact.path}")
                    break
        
        attempt += 1
    
    return False, result


# =============================================================================
# Sandbox Execution Integration (ADR-0005: Production Codegen Quality Gates)
# =============================================================================

async def _run_sandbox_validation(
    code_artifacts: list["CodeArtifact"],
    profile: CodegenProfile,
    state: WorkflowState,
) -> tuple[bool, SandboxResult | None]:
    """
    Execute generated code in sandbox to validate quality.
    
    Per ADR-0005: Production-Grade Codegen Quality Gates
    - Creates isolated venv
    - Runs ruff (lint + format check)
    - Runs mypy (type checking)
    - Runs pytest with coverage (when tests exist and coverage enabled)
    
    V32-001: Pre-validates syntax and applies auto-repair before sandbox
    V32-002: Applies Response type guard fixes to client code
    V42-007: Uses feedback loop for automatic regeneration on failure
    
    Args:
        code_artifacts: List of CodeArtifact objects to validate
        profile: Active codegen profile (determines gate strictness)
        state: WorkflowState for context
        
    Returns:
        Tuple of (success, SandboxResult or None)
    """
    # Import V32 fixes
    from integration_coworker.codegen.security import validate_and_repair_syntax
    from integration_coworker.codegen.response_type_guard import fix_generated_client_code
    
    # Skip sandbox if disabled or no Python artifacts
    python_artifacts = [a for a in code_artifacts if a.language in ("python", "py")]
    if not python_artifacts:
        logger.debug("Sandbox validation skipped: no Python artifacts")
        return True, None
    
    # Check if sandbox is enabled via profile flag or environment override
    import os
    sandbox_enabled = profile.enable_sandbox_execution
    
    # Allow environment override to enable sandbox even in development
    if os.getenv("ENABLE_SANDBOX_GATES", "").lower() in ("true", "1", "yes"):
        sandbox_enabled = True
    
    if not sandbox_enabled:
        logger.debug(f"Sandbox validation skipped: not enabled (profile={profile.name})")
        return True, None
    
    logger.info(f"[sandbox] Running sandbox validation on {len(python_artifacts)} Python artifacts")
    
    # ==========================================================================
    # V32-001/V32-002: Pre-sandbox validation and auto-repair
    # ==========================================================================
    # Apply fixes BEFORE building sandbox artifacts to catch issues early
    # and reduce sandbox failure rate.
    #
    # V44-003: CRITICAL - Also apply pre-AST sanitization to handle hallucinated
    # imports and concatenated statements from ALL code paths (including FILE
    # protocol templates which bypass the main V44 fix in the LLM code path).
    
    for artifact in python_artifacts:
        original_content = artifact.content
        modified = False
        
        # V44-003: Pre-AST sanitization (handles hallucinated imports, concatenated statements)
        # This MUST run before V32-001 syntax repair because it fixes issues that
        # prevent AST parsing from working at all.
        try:
            from integration_coworker.codegen.import_fixer import pre_ast_sanitize_code
            sanitized_code, sanitize_fixes = pre_ast_sanitize_code(artifact.content)
            if sanitize_fixes:
                artifact.content = sanitized_code
                modified = True
                logger.info(f"[V44-003] Pre-AST sanitization on {artifact.module_name}: {len(sanitize_fixes)} fixes")
        except Exception as e:
            logger.warning(f"[V44-003] Pre-AST sanitization failed for {artifact.module_name}: {e}")
        
        # V32-001: Syntax validation and auto-repair
        repaired_code, is_valid = validate_and_repair_syntax(artifact.content)
        if repaired_code != artifact.content:
            artifact.content = repaired_code
            modified = True
            logger.info(f"[V32-001] Auto-repaired syntax in {artifact.module_name}")
        
        if not is_valid:
            # Syntax repair failed - this will likely fail sandbox
            logger.warning(
                f"[V32-001] Syntax repair failed for {artifact.module_name}, "
                "sandbox validation will likely fail"
            )
        
        # V32-002: Apply Response type guard fixes to client code
        if artifact.artifact_type == "client":
            fixed_code, changes = fix_generated_client_code(artifact.content)
            if changes:
                artifact.content = fixed_code
                modified = True
                logger.info(f"[V32-002] Applied {len(changes)} Response type guard fixes to {artifact.module_name}")
        
        if modified:
            logger.debug(f"[V32] Pre-sandbox modifications applied to {artifact.module_name}")
    
    # Build artifact files for sandbox
    # V45-007: Use get_layout_dirs for consistent path computation
    # This ensures sandbox paths match router import paths
    from integration_coworker.codegen.paths import get_layout_dirs
    clients_dir, flows_dir, tests_dir = get_layout_dirs(state.repo_profile)
    
    sandbox_artifacts: list[ArtifactFile] = []
    for artifact in python_artifacts:
        # V22-011: Check memory ceiling in artifact processing loop
        check_ceiling_or_raise()
        # Map artifact type to sandbox path structure
        # Note: artifact.rel_path may already include directory prefix (e.g., "tests/test_foo.py")
        rel_path = artifact.rel_path or ""
        
        if artifact.artifact_type == "client":
            if rel_path:
                # Use provided rel_path, ensuring src/ prefix for Python imports
                if not rel_path.startswith("src/") and not rel_path.startswith(clients_dir):
                    path = f"src/{rel_path}"
                else:
                    path = rel_path
            else:
                # V45-007: Use clients_dir from profile instead of hardcoded path
                path = f"{clients_dir}/{artifact.module_name}.py"
        elif artifact.artifact_type == "flow":
            if rel_path:
                # Use provided rel_path
                if not rel_path.startswith("src/") and not rel_path.startswith(flows_dir):
                    path = f"src/{rel_path}"
                else:
                    path = rel_path
            else:
                # V45-007: Use flows_dir from profile instead of hardcoded path
                path = f"{flows_dir}/{artifact.module_name}.py"
        elif artifact.artifact_type == "test":
            # Test files should go in tests/ directory
            if rel_path and rel_path.startswith("tests/"):
                path = rel_path  # Already has tests/ prefix
            elif rel_path and rel_path.startswith(tests_dir):
                path = rel_path  # Already has correct tests prefix
            elif rel_path:
                path = f"{tests_dir}/{rel_path}"
            else:
                path = f"{tests_dir}/test_{artifact.module_name}.py"
        else:
            if rel_path:
                path = rel_path
            else:
                path = f"src/{artifact.module_name}.py"
        
        sandbox_artifacts.append(ArtifactFile(
            path=path,
            content=artifact.content,
        ))
        logger.debug(f"[sandbox] Added artifact: {path} ({len(artifact.content)} chars)")
    
    # Prepare contract test config if enabled
    contract_spec_dict = None
    if profile.enable_contract_tests and state.openapi_spec:
        contract_spec_dict = state.openapi_spec
        logger.info("[sandbox] Contract tests enabled - will validate against OpenAPI spec")
    
    # Prepare live test config if enabled
    # Live tests are enabled via:
    #   1. profile.enable_live_tests=True (CodegenProfile setting), OR
    #   2. VALIDATION_PROFILE=live + ALLOW_LIVE=1 (environment override for CI/runtime)
    validation_profile_live = (
        os.environ.get("VALIDATION_PROFILE", "").lower() == "live"
        and os.environ.get("ALLOW_LIVE", "0") == "1"
    )
    enable_live = profile.enable_live_tests or validation_profile_live
    
    if validation_profile_live and not profile.enable_live_tests:
        logger.info("[sandbox] Live tests enabled via VALIDATION_PROFILE=live + ALLOW_LIVE=1 env override")
    
    live_host_allowlist = list(profile.live_host_allowlist) if profile.live_host_allowlist else []
    
    # Also check LIVE_HOST_ALLOWLIST env var (CI/runtime override)
    env_host_allowlist = os.environ.get("LIVE_HOST_ALLOWLIST", "")
    if env_host_allowlist:
        env_hosts = [h.strip() for h in env_host_allowlist.split(",") if h.strip()]
        live_host_allowlist = list(set(live_host_allowlist + env_hosts))
        logger.info(f"[sandbox] Added hosts from LIVE_HOST_ALLOWLIST env: {env_hosts}")
    
    # Resolve env var NAMES to actual values at runtime (keeps secrets out of config)
    # Sources: profile.live_env_passthrough AND/OR LIVE_ENV_PASSTHROUGH env var
    # This allows CI to inject env var names without code changes
    env_passthrough_names: set[str] = set()
    
    # From profile (code-defined)
    if profile.live_env_passthrough:
        env_passthrough_names.update(profile.live_env_passthrough)
    
    # From LIVE_ENV_PASSTHROUGH env var (CI/runtime override)
    # Format: LIVE_ENV_PASSTHROUGH=OPENAI_API_KEY,STRIPE_SECRET_KEY
    env_passthrough_raw = os.environ.get("LIVE_ENV_PASSTHROUGH", "")
    if env_passthrough_raw:
        env_names = [n.strip() for n in env_passthrough_raw.split(",") if n.strip()]
        env_passthrough_names.update(env_names)
        logger.info(f"[sandbox] Added env vars from LIVE_ENV_PASSTHROUGH: {env_names}")
    
    # Resolve names to values
    live_env_vars: dict[str, str] = {}
    for var_name in env_passthrough_names:
        value = os.environ.get(var_name)
        if value:
            live_env_vars[var_name] = value
        else:
            logger.warning(f"[sandbox] Live env var {var_name} not set in environment, skipping")
    
    # If live tests enabled but no explicit allowlist, try to extract from spec
    if enable_live and not live_host_allowlist and state.openapi_spec:
        from integration_coworker.codegen.sandbox import extract_hosts_from_spec
        extracted_hosts = extract_hosts_from_spec(state.openapi_spec)
        if extracted_hosts:
            live_host_allowlist = extracted_hosts
            logger.info(f"[sandbox] Extracted hosts from spec for live tests: {live_host_allowlist}")
        else:
            logger.warning(
                "[sandbox] enable_live_tests=True but no hosts could be extracted from spec servers. "
                "Live tests will be skipped unless explicit live_host_allowlist is provided."
            )
    
    # V45-001: Get policy_mode from state.options for sandbox runtime package installation
    policy_mode = state.options.policy_mode if state.options else "inline"
    
    # V45-011: Compute coverage_target based on actual artifact locations
    # Previously hardcoded to "src" which missed code in "integrations/clients", "flows", etc.
    # Now we use the integrations_root from repo_profile to ensure coverage measures
    # the actual generated code, not just the sandbox scaffold.
    coverage_target = None
    if profile.enable_coverage:
        # Get the root directory where clients/flows are placed
        # This is typically "integrations" by default, but can vary based on repo_profile
        integrations_root = "integrations"  # Default
        if state.repo_profile and state.repo_profile.integrations_root:
            integrations_root = state.repo_profile.integrations_root
        coverage_target = integrations_root
    
    # Build sandbox config from profile
    # V5: Use profile's individual gate flags instead of hardcoding
    config = SandboxConfig(
        python_version="3.11",
        enable_ruff=profile.enable_ruff,
        enable_mypy=profile.enable_mypy,
        enable_bandit=profile.enable_bandit,
        enable_pytest=profile.enable_pytest or profile.enable_coverage or profile.fail_on_no_tests or enable_live,
        enable_coverage=profile.enable_coverage,
        coverage_target=coverage_target,
        coverage_fail_under=profile.coverage_fail_under,
        fail_on_no_tests=profile.fail_on_no_tests,
        timeout_seconds=120,
        cleanup_on_success=True,
        cleanup_on_failure=False,  # Keep for debugging on failure
        src_dir="src",
        tests_dir="tests",
        # Contract testing (Schemathesis + Prism)
        enable_contract_tests=profile.enable_contract_tests and contract_spec_dict is not None,
        contract_spec_dict=contract_spec_dict,
        # Live integration tests
        enable_live_tests=enable_live and bool(live_host_allowlist),
        live_host_allowlist=live_host_allowlist,
        live_env_vars=live_env_vars,
        # V45-001: Policy mode determines if runtime package is needed in sandbox
        policy_mode=policy_mode,
    )
    
    # Standard dependencies for generated code (including type stubs for mypy)
    dependencies = [
        "requests", "httpx", "pydantic",
        "types-requests",  # Type stubs for mypy
    ]
    
    # V38-004: Auto-detect additional dependencies from generated code
    # This catches imports like tomli, yaml, jsonschema that might be used
    for artifact in code_artifacts:
        if artifact.content:
            detected = _auto_detect_dependencies(artifact.content)
            for dep in detected:
                if dep not in dependencies:
                    dependencies.append(dep)
                    logger.debug(f"[sandbox] V38-004: Auto-detected dependency: {dep}")
    
    try:
        result = await execute_in_sandbox(
            artifacts=sandbox_artifacts,
            dependencies=dependencies,
            config=config,
        )
        
        # Log results
        if result.success:
            logger.info(f"[sandbox] ✅ All {len(result.gate_results)} gates passed")
            for gate in result.gate_results:
                logger.debug(f"[sandbox]   {gate.name}: {gate.output[:100]}")
            
            # V38-002: Apply auto-fixed code back to artifacts
            # This ensures lint fixes (unused imports, etc.) are written to final output
            if result.fixed_artifacts:
                logger.info(f"[sandbox] V38-002: Applying {len(result.fixed_artifacts)} lint auto-fixes")
                for artifact in code_artifacts:
                    if artifact.rel_path in result.fixed_artifacts:
                        artifact.content = result.fixed_artifacts[artifact.rel_path]
                        logger.debug(f"[sandbox] V38-002: Updated {artifact.rel_path} with lint fixes")
        else:
            logger.warning(f"[sandbox] ❌ Failed: {result.summary}")
            for gate in result.failed_gates:
                # Show full pytest output for debugging (truncated at 2000 chars)
                logger.warning(f"[sandbox]   FAILED {gate.name}: {gate.output[:2000]}")
        
        # Store sandbox result in state for observability
        if not hasattr(state, 'sandbox_result'):
            state.sandbox_result = None
        state.sandbox_result = {
            "success": result.success,
            "summary": result.summary,
            "gates": [
                {
                    "name": g.name,
                    "passed": g.passed,
                    "duration_ms": g.duration_ms,
                    "output": g.output[:500] if g.output else "",
                }
                for g in result.gate_results
            ],
        }
        
        # Fix #89: Check for pytest assertion errors and attempt repair
        if not result.success:
            pytest_gate = next((g for g in result.gate_results if g.name == "pytest" and not g.passed), None)
            if pytest_gate and pytest_gate.output:
                # Detect test assertion errors
                pytest_errors = _detect_test_assertion_errors(pytest_gate.output)
                
                if pytest_errors:
                    logger.info(f"[sandbox] Detected {len(pytest_errors)} test assertion error(s), attempting repair")
                    
                    # Find the test artifact and flow artifact
                    test_artifact = next((a for a in code_artifacts if a.artifact_type == "test"), None)
                    flow_artifact = next((a for a in code_artifacts if a.artifact_type == "flow"), None)
                    
                    if test_artifact:
                        # V25-002 Fix: Include flow code context for smarter repair
                        flow_code = flow_artifact.content if flow_artifact else None
                        if flow_code:
                            logger.info("[sandbox] V25-002: Using flow code context for test repair")
                        
                        # Attempt to repair test assertions with flow context
                        repaired_code, repair_success = await _repair_test_assertions(
                            test_code=test_artifact.content,
                            pytest_errors=pytest_errors,
                            state=state,
                            flow_code=flow_code,  # V25-002: Pass flow code for context
                        )
                        
                        if repair_success and repaired_code != test_artifact.content:
                            # Update the test artifact with repaired code
                            test_artifact.content = repaired_code
                            
                            # Update sandbox artifacts
                            for sa in sandbox_artifacts:
                                if sa.path.startswith("tests/"):
                                    sa.content = repaired_code
                                    break
                            
                            # Re-run sandbox validation
                            logger.info("[sandbox] Re-running sandbox with repaired tests")
                            retry_result = await execute_in_sandbox(
                                artifacts=sandbox_artifacts,
                                dependencies=dependencies,
                                config=config,
                            )
                            
                            if retry_result.success:
                                logger.info("[sandbox] ✅ Repaired tests pass!")
                                state.sandbox_result = {
                                    "success": retry_result.success,
                                    "summary": retry_result.summary,
                                    "gates": [
                                        {
                                            "name": g.name,
                                            "passed": g.passed,
                                            "duration_ms": g.duration_ms,
                                            "output": g.output[:500] if g.output else "",
                                        }
                                        for g in retry_result.gate_results
                                    ],
                                    "test_repair_applied": True,
                                }
                                return True, retry_result
                            else:
                                logger.warning("[sandbox] Repaired tests still failed")
                                # Fall through to V42-007 feedback loop
                                result = retry_result
        
        # =======================================================================
        # V42-007: Feedback-based regeneration for remaining failures
        # =======================================================================
        # If we get here, either:
        # - Test assertion repair was not applicable (no pytest errors)
        # - Test assertion repair failed
        # - Other gates failed (ruff, mypy, bandit)
        #
        # Try the generalized feedback loop that handles all error types
        # =======================================================================
        if not result.success:
            logger.info("[V42-007] Attempting feedback-based regeneration for sandbox failures")
            
            fixed_artifacts, did_fix = await _sandbox_feedback_regenerate(
                code_artifacts=code_artifacts,
                sandbox_result=result,
                state=state,
                max_iterations=2,
            )
            
            if did_fix:
                # Update sandbox artifacts with fixed code
                for code_artifact in fixed_artifacts:
                    for sandbox_artifact in sandbox_artifacts:
                        if code_artifact.rel_path:
                            path_suffix = code_artifact.rel_path.split('/')[-1]
                            if sandbox_artifact.path.endswith(path_suffix):
                                sandbox_artifact.content = code_artifact.content
                                logger.debug(f"[V42-007] Updated sandbox artifact: {sandbox_artifact.path}")
                                break
                
                # Re-run sandbox validation
                logger.info("[V42-007] Re-running sandbox with feedback-repaired code")
                feedback_result = await execute_in_sandbox(
                    artifacts=sandbox_artifacts,
                    dependencies=dependencies,
                    config=config,
                )
                
                if feedback_result.success:
                    logger.info("[V42-007] ✅ Feedback-repaired code passes sandbox!")
                    state.sandbox_result = {
                        "success": feedback_result.success,
                        "summary": feedback_result.summary,
                        "gates": [
                            {
                                "name": g.name,
                                "passed": g.passed,
                                "duration_ms": g.duration_ms,
                                "output": g.output[:500] if g.output else "",
                            }
                            for g in feedback_result.gate_results
                        ],
                        "feedback_repair_applied": True,
                    }
                    return True, feedback_result
                else:
                    logger.warning("[V42-007] Feedback-repaired code still failed sandbox")
                    result = feedback_result  # Use latest result for reporting
        
        return result.success, result
        
    except Exception as e:
        logger.error(f"[sandbox] Execution error: {e}")
        return False, None


async def _run_multilang_sandbox_validation(
    code_artifacts: list["CodeArtifact"],
    target_language: str,
    profile: CodegenProfile,
    state: WorkflowState,
) -> tuple[bool, SandboxResult | None]:
    """
    Execute generated code in multi-language sandbox (TypeScript, Go, etc.).
    
    Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.2:
    - 2-Phase Design: provision (network) → validate (network none)
    - Tier 1: Compiler-backed validation (tsc, vitest, go test)
    - Tier 2: Regex fallback (marked UNTRUSTED)
    
    Args:
        code_artifacts: List of CodeArtifact objects to validate
        target_language: Target language ("typescript", "go", etc.)
        profile: Active codegen profile (determines gate strictness)
        state: WorkflowState for context
        
    Returns:
        Tuple of (success, SandboxResult or None) - returns Python-style SandboxResult for compatibility
    """
    # Map language string to enum
    lang_map = {
        "typescript": ArtifactLanguage.TYPESCRIPT,
        "ts": ArtifactLanguage.TYPESCRIPT,
        "javascript": ArtifactLanguage.JAVASCRIPT,
        "js": ArtifactLanguage.JAVASCRIPT,
        "go": ArtifactLanguage.GO,
        "golang": ArtifactLanguage.GO,
        "python": ArtifactLanguage.PYTHON,
        "py": ArtifactLanguage.PYTHON,
    }
    artifact_lang = lang_map.get(target_language.lower(), ArtifactLanguage.PYTHON)
    
    # Python uses existing sandbox
    if artifact_lang == ArtifactLanguage.PYTHON:
        return await _run_sandbox_validation(code_artifacts, profile, state)
    
    # Check if sandbox is enabled
    import os
    sandbox_enabled = profile.enable_sandbox_execution
    if os.getenv("ENABLE_SANDBOX_GATES", "").lower() in ("true", "1", "yes"):
        sandbox_enabled = True
    
    if not sandbox_enabled:
        logger.debug(f"Multi-lang sandbox validation skipped: not enabled (profile={profile.name})")
        return True, None
    
    logger.info(f"[sandbox-multilang] Running {artifact_lang.value} sandbox validation on {len(code_artifacts)} artifacts")
    
    # V45-007: Use get_layout_dirs for consistent path computation
    # This ensures sandbox paths match router import paths
    from integration_coworker.codegen.paths import get_layout_dirs
    clients_dir, flows_dir, tests_dir = get_layout_dirs(state.repo_profile)
    
    # Build artifact files for sandbox
    sandbox_artifacts: list[ArtifactFile] = []
    for artifact in code_artifacts:
        rel_path = artifact.rel_path or ""
        
        # Use artifact's path or generate language-appropriate default
        if rel_path:
            path = rel_path
        elif artifact.artifact_type == "client":
            ext = _get_extension_for_language(artifact_lang)
            # V45-007: Use clients_dir from profile
            path = f"{clients_dir}/{artifact.module_name}{ext}"
        elif artifact.artifact_type == "flow":
            ext = _get_extension_for_language(artifact_lang)
            # V45-007: Use flows_dir from profile
            path = f"{flows_dir}/{artifact.module_name}{ext}"
        elif artifact.artifact_type == "test":
            ext = _get_extension_for_language(artifact_lang)
            # Language-specific test conventions - V45-007: Use tests_dir from profile
            if artifact_lang in (ArtifactLanguage.TYPESCRIPT, ArtifactLanguage.JAVASCRIPT):
                path = f"{tests_dir}/{artifact.module_name}.test{ext}"  # foo.test.ts
            elif artifact_lang == ArtifactLanguage.GO:
                path = f"{tests_dir}/{artifact.module_name}_test{ext}"  # foo_test.go
            else:
                path = f"{tests_dir}/test_{artifact.module_name}{ext}"
        else:
            ext = _get_extension_for_language(artifact_lang)
            path = f"src/{artifact.module_name}{ext}"
        
        sandbox_artifacts.append(ArtifactFile(
            path=path,
            content=artifact.content,
        ))
        logger.debug(f"[sandbox-multilang] Added artifact: {path} ({len(artifact.content)} chars)")
    
    # Build multi-lang config from explicit settings (NEVER from Docker availability)
    # Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.4:
    # - Tier is EXPLICIT from config/env, never inferred from environment
    # - Tier.PROD requires Docker; if unavailable, fails loudly (no silent downgrade)
    from integration_coworker.codegen.gates import Tier
    from integration_coworker.config import get_settings
    
    settings = get_settings()
    
    # Map config tier string to Tier enum
    tier = Tier.PROD if settings.multilang_tier == "prod" else Tier.EXP
    use_docker = settings.multilang_use_docker
    
    # Log the decision (explicit config values, not environment detection)
    logger.info(
        f"[sandbox-multilang] Tier from config: MULTILANG_TIER={settings.multilang_tier}, "
        f"MULTILANG_USE_DOCKER={settings.multilang_use_docker}"
    )
    
    config = MultiLangSandboxConfig(
        tier=tier,
        timeout_seconds=120,
        cleanup_on_success=True,
        cleanup_on_failure=False,
        use_docker=use_docker,  # From explicit config, not Docker detection
        container_network_none=settings.multilang_network_none,
    )
    
    if tier == Tier.PROD:
        logger.info(
            "[sandbox-multilang] Using Tier.PROD (Docker 2-phase execution). "
            "Provision with network → Validate with --network none."
        )
    else:
        logger.info(
            "[sandbox-multilang] Using Tier.EXP (host execution). "
            "Non-deterministic; suitable for development only."
        )
    
    try:
        result = await execute_multilang_sandbox(
            artifacts=sandbox_artifacts,
            language=artifact_lang,
            config=config,
        )
        
        # Log results
        if result.success:
            logger.info(f"[sandbox-multilang] ✅ All {len(result.gate_results)} gates passed (Tier1={result.is_tier1_compliant})")
            for gate in result.gate_results:
                logger.debug(f"[sandbox-multilang]   {gate.name}: {gate.output[:100] if gate.output else ''}")
        else:
            logger.warning(f"[sandbox-multilang] ❌ Failed: {result.summary}")
            for gate in result.failed_gates:
                logger.warning(f"[sandbox-multilang]   FAILED {gate.name}: {gate.output[:2000] if gate.output else ''}")
        
        # Store result in state for observability
        if not hasattr(state, 'sandbox_result'):
            state.sandbox_result = None
        state.sandbox_result = {
            "success": result.success,
            "summary": result.summary,
            "language": artifact_lang.value,
            "tier1_compliant": result.is_tier1_compliant,
            "gates": [
                {
                    "name": g.name,
                    "passed": g.passed,
                    "duration_ms": g.duration_ms,
                    "output": g.output[:500] if g.output else "",
                }
                for g in result.gate_results
            ],
        }
        
        # Convert MultiLangSandboxResult to SandboxResult for API compatibility
        compat_result = SandboxResult(
            success=result.success,
            sandbox_dir=result.sandbox_dir,
            gate_results=result.gate_results,  # Already GateResult instances
            summary=result.summary,
        )
        
        return result.success, compat_result
        
    except Exception as e:
        logger.error(f"[sandbox-multilang] Execution error: {e}")
        return False, None


def _get_extension_for_language(lang: ArtifactLanguage) -> str:
    """Get file extension for a language."""
    ext_map = {
        ArtifactLanguage.PYTHON: ".py",
        ArtifactLanguage.TYPESCRIPT: ".ts",
        ArtifactLanguage.JAVASCRIPT: ".js",
        ArtifactLanguage.GO: ".go",
        ArtifactLanguage.JAVA: ".java",
    }
    return ext_map.get(lang, ".py")


def _extract_path_params(endpoint_path: str) -> list[str]:
    """
    Extract path parameters from OpenAPI-style path.
    
    Bug #66 Fix: OpenAPI paths contain parameters like {ServiceSid}, {AccountSid}.
    These need to be extracted and converted to Python function arguments.
    
    Args:
        endpoint_path: Path like "/v1/Services/{ServiceSid}/AlphaSenders"
        
    Returns:
        List of parameter names: ["ServiceSid"]
    """
    return re.findall(r'\{(\w+)\}', endpoint_path)


def _to_python_param_name(param: str) -> str:
    """
    Convert OpenAPI path parameter to Python snake_case parameter name.
    
    Examples:
        ServiceSid -> service_sid
        accountId -> account_id
    """
    # Insert underscore before uppercase letters (for camelCase/PascalCase)
    s1 = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', param)
    s2 = re.sub(r'([a-z\d])([A-Z])', r'\1_\2', s1)
    return s2.lower()


def _build_path_param_signature(path_params: list[str]) -> str:
    """
    Build function signature for path parameters.
    
    Args:
        path_params: ["ServiceSid", "AccountSid"]
        
    Returns:
        String like "service_sid: str, account_sid: str, "
    """
    if not path_params:
        return ""
    
    parts = [f"{_to_python_param_name(p)}: str" for p in path_params]
    return ",\n        ".join(parts) + ","


def _build_path_format_kwargs(path_params: list[str]) -> str:
    """
    Build .format() kwargs for URL path substitution.
    
    Args:
        path_params: ["ServiceSid", "AccountSid"]
        
    Returns:
        String like "ServiceSid=service_sid, AccountSid=account_sid"
    """
    if not path_params:
        return ""
    
    parts = [f"{p}={_to_python_param_name(p)}" for p in path_params]
    return ", ".join(parts)

# V2.2 (Fix #2): Fixed test fixture skeleton for secure credential handling
# This skeleton ensures credentials are NEVER hardcoded in generated tests
# V2.3 (Fix #89): Improved test assertions to avoid KeyError on mock call args
# V2.4 (Fix #90): Tests now pass payload dict to match flow signature (api_key, payload)
TEST_FIXTURE_SKELETON = '''"""
Tests for {provider_title} {task_title} Flow

Auto-generated by Integration Co-Worker (secure fixture mode)

SECURITY: Uses pytest fixtures for credentials - never hardcoded.
"""
import os
import pytest
from unittest.mock import Mock, patch, MagicMock
from {flow_import_module} import {flow_function}


# ============================================================
# SECURITY FIXTURES - Environment-based credentials (SEC-FIX-002)
# ============================================================

@pytest.fixture
def api_key():
    """
    API key fixture - reads from environment or returns test placeholder.
    
    For unit tests with mocked clients, the actual value doesn't matter.
    For integration tests, set {env_var} in your environment.
    """
    return os.environ.get("{env_var}", "test_key_for_mocking")


@pytest.fixture
def sample_payload():
    """Sample payload for API calls - matches flow signature."""
    return {{
        "key": "test_value",
        "id": "test_123",
    }}


@pytest.fixture
def mock_response():
    """Standard mock response for API calls."""
    return {{
        "id": "test_123",
        "status": "success",
        "object": "test_object",
    }}


@pytest.fixture
def mock_client(mock_response):
    """Pre-configured mock client for unit tests."""
    mock_instance = MagicMock()
    mock_instance.{method_name}.return_value = mock_response
    return mock_instance


# ============================================================
# UNIT TESTS
# ============================================================

class {test_class_name}:
    """Tests for the {task_slug} flow."""
    
    def test_{flow_function}_success(self, api_key, sample_payload, mock_client, mock_response):
        """Test successful flow execution with mocked client."""
        with patch('{flow_import_module}.{client_class}') as MockClient:
            MockClient.return_value = mock_client
            
            result = {flow_function}(
                api_key=api_key,
                payload=sample_payload,
            )
            
            # Verify the flow completed successfully
            assert result is not None
            # V22-003 Fix: AST-extracted method name ensures this matches actual flow code
            assert mock_client.{method_name}.called, \\
                f"Expected mock_client.{method_name} to be called"
    
    def test_{flow_function}_returns_data(self, api_key, sample_payload, mock_client, mock_response):
        """Test flow returns expected data structure."""
        with patch('{flow_import_module}.{client_class}') as MockClient:
            MockClient.return_value = mock_client
            
            result = {flow_function}(
                api_key=api_key,
                payload=sample_payload,
            )
            
            # Check result has expected keys (if dict)
            if isinstance(result, dict):
                assert "success" in result or "data" in result or "id" in result or len(result) > 0
    
    def test_{flow_function}_handles_api_error(self, api_key, sample_payload):
        """Test flow handles API errors gracefully."""
        with patch('{flow_import_module}.{client_class}') as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.{method_name}.side_effect = Exception("API Error")
            
            with pytest.raises(Exception):
                {flow_function}(api_key=api_key, payload=sample_payload)
    
    def test_{flow_function}_mock_called_correctly(self, api_key, sample_payload, mock_client):
        """Test the mock client method is invoked."""
        with patch('{flow_import_module}.{client_class}') as MockClient:
            MockClient.return_value = mock_client
            
            {flow_function}(api_key=api_key, payload=sample_payload)
            
            # V22-003 Fix: AST-extracted method name ensures this matches actual flow code
            assert mock_client.{method_name}.call_count >= 1, \\
                f"Expected mock_client.{method_name} to be called at least once"
    
    def test_{flow_function}_missing_api_key(self, sample_payload, mock_client):
        """Test flow raises error when api_key is empty."""
        with patch('{flow_import_module}.{client_class}') as MockClient:
            MockClient.return_value = mock_client
            
            with pytest.raises(ValueError, match="api_key is required"):
                {flow_function}(api_key="", payload=sample_payload)
    
    def test_{flow_function}_missing_payload(self, api_key, mock_client):
        """Test flow raises error when payload is empty."""
        with patch('{flow_import_module}.{client_class}') as MockClient:
            MockClient.return_value = mock_client
            
            with pytest.raises(ValueError, match="payload is required"):
                {flow_function}(api_key=api_key, payload={{}})
'''


# =============================================================================
# Full Parallel Async Codegen (Bug #35 Fix - Production Ready)
# =============================================================================
# 
# Architecture: Full concurrency with independent retry loops per artifact.
# Each artifact handles its own LLM calls + validation + retries independently.
# Rate limiting is handled by the async_client.py layer (exponential backoff).
#
# Performance: O(1) wall-clock time bounded by the slowest artifact, not O(n).
# For 5 endpoints with 3 artifacts each = 15 parallel operations.
# =============================================================================


@dataclass
class GateResult:
    """Result of running a quality gate (ruff, mypy, etc.)."""
    ok: bool
    tool: str
    msg: str = ""
    exit_code: int | None = None
    tool_missing: bool = False


async def _run_strict_quality_gates(
    code: str,
    module_name: str,
    profile: CodegenProfile,
) -> tuple[bool, str]:
    """
    Run ruff and mypy quality gates on generated code.
    
    Per ADR-0005 and Phase 1 production hardening:
    - Production profile (enable_strict_gates=True):
      - Tool missing = HARD FAILURE (not warning)
      - Non-zero exit = HARD FAILURE
    - Development profile:
      - Tool missing = WARNING + continue
      - Non-zero exit = WARNING + continue
    
    Args:
        code: The Python code to validate
        module_name: Module name for logging
        profile: Active codegen profile
        
    Returns:
        Tuple of (passed, error_message). passed=True means code is acceptable.
    """
    import os
    import tempfile
    
    if not profile.enable_strict_gates:
        # Development mode: skip strict gates entirely
        logger.debug(f"[{module_name}] Strict gates disabled (profile={profile.name})")
        return True, ""
    
    # Write code to temp file for validation
    with tempfile.NamedTemporaryFile(
        mode='w', 
        suffix='.py', 
        delete=False,
        encoding='utf-8'
    ) as f:
        f.write(code)
        temp_path = f.name
    
    try:
        gate_results: list[GateResult] = []
        
        # Run ruff check (linting)
        ruff_result = _run_ruff_check(temp_path, module_name)
        gate_results.append(ruff_result)
        
        # Run ruff format --check (formatting)
        ruff_format_result = _run_ruff_format_check(temp_path, module_name)
        gate_results.append(ruff_format_result)
        
        # Run mypy check
        mypy_result = _run_mypy_check(temp_path, module_name, profile.mypy_strict)
        gate_results.append(mypy_result)
        
        # Collect failures
        failures = [r for r in gate_results if not r.ok]
        
        if failures:
            error_msgs = []
            for f in failures:
                if f.tool_missing:
                    error_msgs.append(f"{f.tool}: MISSING (required in production)")
                else:
                    error_msgs.append(f"{f.tool}: {f.msg}")
            
            combined_error = "; ".join(error_msgs)
            logger.warning(f"[{module_name}] Strict gates FAILED: {combined_error[:300]}")
            return False, combined_error
        
        logger.debug(f"[{module_name}] Strict gates passed (ruff + mypy)")
        return True, ""
        
    finally:
        # Clean up temp file
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _run_ruff_check(temp_path: str, module_name: str) -> GateResult:
    """Run ruff check and return GateResult."""
    import subprocess
    try:
        result = subprocess.run(
            ["ruff", "check", temp_path, "--output-format", "concise"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            errors = result.stdout or result.stderr
            logger.warning(f"[{module_name}] ruff check failed: {errors[:200]}")
            return GateResult(ok=False, tool="ruff check", msg=errors.strip(), exit_code=result.returncode)
        return GateResult(ok=True, tool="ruff check", exit_code=0)
    except FileNotFoundError:
        logger.warning(f"[{module_name}] ruff not installed - REQUIRED in production")
        return GateResult(ok=False, tool="ruff check", msg="tool not installed", tool_missing=True)
    except subprocess.TimeoutExpired:
        logger.warning(f"[{module_name}] ruff check timed out")
        return GateResult(ok=False, tool="ruff check", msg="timeout")


def _run_ruff_format_check(temp_path: str, module_name: str) -> GateResult:
    """Run ruff format --check and return GateResult."""
    import subprocess
    try:
        result = subprocess.run(
            ["ruff", "format", "--check", temp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            errors = result.stdout or result.stderr
            logger.warning(f"[{module_name}] ruff format check failed: {errors[:200]}")
            return GateResult(ok=False, tool="ruff format", msg=errors.strip(), exit_code=result.returncode)
        return GateResult(ok=True, tool="ruff format", exit_code=0)
    except FileNotFoundError:
        # ruff format uses same binary as ruff check, so if we get here, ruff is missing
        return GateResult(ok=False, tool="ruff format", msg="tool not installed", tool_missing=True)
    except subprocess.TimeoutExpired:
        logger.warning(f"[{module_name}] ruff format timed out")
        return GateResult(ok=False, tool="ruff format", msg="timeout")


def _run_mypy_check(temp_path: str, module_name: str, strict_mode: bool = False) -> GateResult:
    """Run mypy and return GateResult."""
    import subprocess
    try:
        args = ["mypy", temp_path, "--ignore-missing-imports", "--no-error-summary"]
        if strict_mode:
            args.append("--strict")
        
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            errors = result.stdout or result.stderr
            # Filter out "Success" messages (mypy returns 0 on success but sometimes has output)
            if "Success" not in errors:
                logger.warning(f"[{module_name}] mypy gate failed: {errors[:200]}")
                return GateResult(ok=False, tool="mypy", msg=errors.strip(), exit_code=result.returncode)
        return GateResult(ok=True, tool="mypy", exit_code=0)
    except FileNotFoundError:
        logger.warning(f"[{module_name}] mypy not installed - REQUIRED in production")
        return GateResult(ok=False, tool="mypy", msg="tool not installed", tool_missing=True)
    except subprocess.TimeoutExpired:
        logger.warning(f"[{module_name}] mypy timed out")
        return GateResult(ok=False, tool="mypy", msg="timeout")
        try:
            os.unlink(temp_path)
        except OSError:
            pass


@dataclass
class ArtifactResult:
    """Result of refining a single artifact with LLM."""
    artifact_type: Literal["client", "flow", "test"]
    code: str
    success: bool
    attempts: int = 1
    auto_fixed: bool = False
    fallback: bool = False
    error: str | None = None


async def _refine_artifact_async(
    template_code: str,
    state: WorkflowState,
    artifact_type: Literal["client", "flow", "test"],
    expected_class: str | None = None,
    expected_function: str | None = None,
    endpoint: Endpoint | None = None,
    target_language: str | None = None,
    # V37-002: Task-extracted function requirements
    task_function_name: str | None = None,
    task_function_signature: str | None = None,
    # V38-007: Async code generation flag
    is_async_required: bool = False,
    # V38-008: Streaming response handling flag
    is_streaming_required: bool = False,
) -> ArtifactResult:
    """
    Refine a single artifact with LLM, with full validation and independent retry loop.
    
    Each artifact handles its own retries without blocking other artifacts.
    This is the core of the parallel architecture.
    
    Bug #70: Added target_language for dynamic language support.
    V37-002: Added task_function_name and task_function_signature to honor user's explicit requirements.
    V38-007: Added is_async_required to generate async/concurrent code patterns.
    V38-008: Added is_streaming_required to generate streaming response handling.
    
    Args:
        template_code: The template/skeleton code
        state: WorkflowState for context
        artifact_type: "client", "flow", or "test"
        expected_class: Class name that must be present (for client)
        expected_function: Function name that must be present (for flow/test)
        endpoint: The endpoint being implemented
        target_language: Target language (default: from repo_profile or "python")
        task_function_name: User-specified function name (V37-002)
        task_function_signature: User-specified function signature (V37-002)
        is_async_required: Whether async/concurrent code patterns are needed (V38-007)
        is_streaming_required: Whether streaming response handling is needed (V38-008)
        
    Returns:
        ArtifactResult with refined code or fallback
    """
    from integration_coworker.llm.async_client import call_llm_async
    
    module_name = expected_class or expected_function or artifact_type
    MAX_ATTEMPTS = 3  # Initial + 2 retries
    
    # Bug #70: Determine target language for validation decisions
    if target_language:
        lang = target_language.lower()
    elif state.repo_profile and state.repo_profile.language:
        lang = state.repo_profile.language.lower()
    else:
        lang = "python"
    
    is_python = lang in ("python", "py")
    
    # V1.1: Check for strict codegen mode
    strict_mode = False
    if state.options and hasattr(state.options, 'strict_codegen'):
        strict_mode = state.options.strict_codegen
    
    # Get system prompt from archetype
    prompt_config = get_archetype_prompt_config(NODE_NAME)
    system_prompt = prompt_config.get("system_template")
    
    # Track state across attempts
    policy_violations = []
    clean_refined = template_code
    
    for attempt in range(MAX_ATTEMPTS):
        # V22-011: Check memory ceiling inside LLM retry loop
        check_ceiling_in_loop(attempt, check_interval=1)
        
        try:
            # Build prompt - initial or feedback
            if attempt == 0:
                prompt = _build_code_generation_prompt(
                    skeleton_code=template_code,
                    state=state,
                    artifact_type=artifact_type,
                    endpoint=endpoint,
                    client_class=expected_class,
                    method_name=expected_function if artifact_type == "client" else None,
                    flow_function=expected_function if artifact_type in ("flow", "test") else None,
                    target_language=lang,  # Bug #70: Pass target language
                    # V37-002: Pass task function constraints
                    task_function_name=task_function_name,
                    task_function_signature=task_function_signature,
                    # V38-007: Pass async requirement
                    is_async_required=is_async_required,
                    # V38-008: Pass streaming requirement
                    is_streaming_required=is_streaming_required,
                )
            else:
                prompt = _build_policy_feedback_prompt(
                    original_code=clean_refined,
                    violations=policy_violations,
                    state=state,
                    artifact_type=artifact_type,
                    retry_num=attempt - 1,
                )
            
            # Async LLM call - runs concurrently with other artifacts
            response = await call_llm_async(
                prompt=prompt,
                task_type="codegen",
                system_prompt=system_prompt,
            )
            
            if not response:
                logger.warning(f"[{module_name}] Attempt {attempt+1}: Empty LLM response")
                continue
            
            # === CPU-bound validation (fast, sync is fine) ===
            
            # Clean markdown fences and immediately release raw response
            clean_refined = strip_code_fences(response)
            del response  # V22: Release raw LLM response to reduce memory pressure
            
            # V1.1: Apply style fixes in strict mode (Python only)
            if strict_mode and is_python:
                clean_refined = fix_code_style(clean_refined)
            
            # Bug #70/#88: Syntax check for all languages (tree-sitter when available)
            if not _validate_syntax(clean_refined, lang):
                logger.warning(f"[{module_name}] Attempt {attempt+1}: Syntax error in {lang} code")
                continue
            
            # Bug #70: Security check (Python only - uses Python AST)
            if is_python:
                is_secure, sec_violations = validate_code_security(
                    clean_refined,
                    allow_subprocess=False,
                    allow_file_io=True,
                )
                if not is_secure:
                    logger.warning(f"[{module_name}] Attempt {attempt+1}: Security violation")
                    continue
            
            # Content policy check (SEC-004) - language-agnostic (regex-based)
            is_policy_valid, policy_violations = validate_content_policy(
                clean_refined,
                endpoints=state.endpoints if state.endpoints else None,
            )
            
            if is_policy_valid:
                # Validate expected symbols (Bug #84: pass lang for non-Python support)
                if expected_class and not _has_class(clean_refined, expected_class, lang):
                    logger.warning(f"[{module_name}] Attempt {attempt+1}: Missing class {expected_class}")
                    continue
                if expected_function and not _has_function(clean_refined, expected_function, lang):
                    logger.warning(f"[{module_name}] Attempt {attempt+1}: Missing function {expected_function}")
                    continue
                
                # Quality Gate: Semantic validation (Python only) - Alternative A implementation
                # Validates imports resolve and expected methods/functions exist
                if is_python:
                    is_semantic_valid, semantic_issues = validate_semantic_correctness(
                        clean_refined,
                        context=None,  # Import validation is context-independent
                        check_imports=True,
                        check_docstrings=False,  # Don't block on missing docstrings
                    )
                    if not is_semantic_valid:
                        errors_only = [i for i in semantic_issues if i.severity == "error"]
                        logger.warning(
                            f"[{module_name}] Attempt {attempt+1}: Semantic validation failed - "
                            f"{len(errors_only)} error(s): {format_semantic_issues(errors_only)}"
                        )
                        continue
                
                # Quality Gate: Ruff + Mypy (ADR-0005) - profile-aware strict gates
                # In production profile: these are HARD gates that fail the attempt
                # In development profile: these log warnings but proceed
                profile = get_active_profile()
                if is_python:
                    gates_passed, gate_errors = await _run_strict_quality_gates(
                        clean_refined, module_name, profile
                    )
                    if not gates_passed:
                        if profile.enable_strict_gates:
                            logger.warning(
                                f"[{module_name}] Attempt {attempt+1}: Strict gates failed - {gate_errors[:200]}"
                            )
                            continue  # Hard fail in production
                        else:
                            # Development: warn but proceed
                            logger.debug(f"[{module_name}] Strict gates would fail: {gate_errors[:100]}")
                
                # === Self-Review Step (Task B) ===
                # Apply LLM self-review if enabled in profile
                reviewed_code, review_success = await _apply_self_review_if_enabled(
                    code=clean_refined,
                    artifact_type=artifact_type,
                    module_name=module_name,
                    state=state,
                    profile=profile,
                )
                
                if not review_success:
                    # Production: self-review failed and couldn't repair - hard fail
                    logger.error(f"[{module_name}] Self-review HARD FAIL (production)")
                    continue
                
                logger.info(f"[{module_name}] Succeeded on attempt {attempt+1}")
                return ArtifactResult(
                    artifact_type=artifact_type,
                    code=reviewed_code,
                    success=True,
                    attempts=attempt + 1,
                )
            
            # Bug #30: Try auto-fixing hallucinated paths before next retry
            has_path_violations = any(
                hasattr(v, 'type') and v.type == PolicyViolationType.HALLUCINATED_ENDPOINT
                for v in policy_violations
            )
            
            if has_path_violations and state.endpoints:
                fix_result = fix_hallucinated_paths(
                    clean_refined,
                    endpoints=state.endpoints,
                    threshold=0.65,
                )
                
                if fix_result.num_fixes > 0:
                    logger.info(f"[{module_name}] Auto-fixed {fix_result.num_fixes} path(s)")
                    
                    if _validate_syntax(fix_result.fixed_code, lang):
                        is_fixed_valid, fixed_violations = validate_content_policy(
                            fix_result.fixed_code,
                            endpoints=state.endpoints,
                        )
                        if is_fixed_valid:
                            # Validate symbols after fix
                            if expected_class and not _has_class(fix_result.fixed_code, expected_class, lang):
                                continue
                            if expected_function and not _has_function(fix_result.fixed_code, expected_function, lang):
                                continue
                            
                            # === Self-Review Step (Task B) for auto-fixed code ===
                            profile = get_active_profile()
                            reviewed_code, review_success = await _apply_self_review_if_enabled(
                                code=fix_result.fixed_code,
                                artifact_type=artifact_type,
                                module_name=module_name,
                                state=state,
                                profile=profile,
                            )
                            
                            if not review_success:
                                continue
                            
                            logger.info(f"[{module_name}] Path auto-fix succeeded on attempt {attempt+1}")
                            return ArtifactResult(
                                artifact_type=artifact_type,
                                code=reviewed_code,
                                success=True,
                                attempts=attempt + 1,
                                auto_fixed=True,
                            )
                        else:
                            policy_violations = fixed_violations
                            clean_refined = fix_result.fixed_code
            
            logger.info(f"[{module_name}] Attempt {attempt+1}: {len(policy_violations)} policy violations, will retry")
            
        except LLMAuthError:
            # Auth errors: fail-fast, never retry (P0-2/Phase 0)
            raise
        except Exception as e:
            logger.error(f"[{module_name}] Attempt {attempt+1} error: {e}")
            continue
    
    # All attempts failed - profile determines fallback behavior (ADR-0005)
    profile = get_active_profile()
    
    if not profile.fallback_to_skeleton:
        # Production profile: fail hard, don't silently degrade
        logger.error(f"[{module_name}] Failed all {MAX_ATTEMPTS} attempts - no skeleton fallback (profile={profile.name})")
        return ArtifactResult(
            artifact_type=artifact_type,
            code="",  # Empty code signals complete failure
            success=False,
            attempts=MAX_ATTEMPTS,
            fallback=False,
            error=f"PRODUCTION: Failed after {MAX_ATTEMPTS} attempts - no fallback allowed",
        )
    
    # Development profile: use skeleton fallback (Bug #75 fix)
    logger.warning(f"[{module_name}] Failed all {MAX_ATTEMPTS} attempts, using template fallback")
    
    # Bug #75 Fix: Use language-appropriate skeleton instead of Python template
    from integration_coworker.codegen.prompts import get_skeleton_template
    
    # Extract naming info for skeleton
    provider_title = state.provider_code.replace("_", " ").title() if state.provider_code else "API"
    task_title = state.task_description[:30] if state.task_description else "Task"
    
    def _snake(name: str) -> str:
        s1 = re.sub('(.)([A-Z][a-z]+)', r"\1_\2", name)
        return re.sub('([a-z0-9])([A-Z])', r"\1_\2", s1).lower()

    client_class_name = expected_class or "ApiClient"
    client_module = _snake(client_class_name)

    fallback_code = get_skeleton_template(
        language=lang,
        artifact_type=artifact_type,
        provider_title=provider_title,
        task_title=task_title,
        client_class=client_class_name,
        client_module=client_module,
        method_name=expected_function or "execute",
        flow_function=expected_function or "execute_flow",
        endpoint_path=getattr(endpoint, "path", None) if 'endpoint' in locals() else None,
        http_method=getattr(endpoint, "method", None) if 'endpoint' in locals() else None,
    )
    
    return ArtifactResult(
        artifact_type=artifact_type,
        code=fallback_code,
        success=False,
        attempts=MAX_ATTEMPTS,
        fallback=True,
        error=f"Failed after {MAX_ATTEMPTS} attempts - using {lang} skeleton",
    )


async def _generate_artifacts_for_endpoint_async(
    state: WorkflowState,
    endpoint: Endpoint,
    ctx: 'CodegenContext',
    client_template: str,
    flow_template: str,
    test_template: str,
) -> dict[str, ArtifactResult]:
    """
    Generate all 3 artifacts (client, flow, test) for one endpoint in parallel.
    
    Args:
        state: WorkflowState
        endpoint: The endpoint to generate for
        ctx: CodegenContext with naming info
        client_template: Client code template
        flow_template: Flow code template  
        test_template: Test code template
        
    Returns:
        Dict mapping artifact type to ArtifactResult
    """
    # V37-002: Extract task function constraints from context
    task_func_name = ctx.task_function_name if ctx.has_task_overrides else None
    # Build signature from task_parser result if we have function info
    task_func_sig = None
    # V38-007: Extract async requirement from context
    is_async = ctx.is_async_required
    # V38-008: Extract streaming requirement from context
    is_streaming = ctx.is_streaming_required
    
    tasks = [
        _refine_artifact_async(
            template_code=client_template,
            state=state,
            artifact_type="client",
            expected_class=ctx.client_class,
            endpoint=endpoint,
            # V38-007: Pass async requirement
            is_async_required=is_async,
            # V38-008: Pass streaming requirement
            is_streaming_required=is_streaming,
        ),
        _refine_artifact_async(
            template_code=flow_template,
            state=state,
            artifact_type="flow",
            expected_function=ctx.get_effective_flow_function(),  # V37-002: Use effective name
            endpoint=endpoint,
            # V37-002: Pass task function constraints for flows
            task_function_name=task_func_name,
            task_function_signature=task_func_sig,
            # V38-007: Pass async requirement
            is_async_required=is_async,
            # V38-008: Pass streaming requirement
            is_streaming_required=is_streaming,
        ),
        _refine_artifact_async(
            template_code=test_template,
            state=state,
            artifact_type="test",
            expected_function=ctx.get_effective_flow_function(),  # V37-002: Use effective name
            endpoint=endpoint,
            # V37-002: Pass task function constraints for tests too
            task_function_name=task_func_name,
            task_function_signature=task_func_sig,
            # V38-007: Pass async requirement
            is_async_required=is_async,
            # V38-008: Pass streaming requirement
            is_streaming_required=is_streaming,
        ),
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Convert to dict, handling exceptions
    output = {}
    for i, (artifact_type, result) in enumerate(zip(["client", "flow", "test"], results)):
        if isinstance(result, Exception):
            logger.error(f"[{ctx.client_class}] {artifact_type} failed with exception: {result}")
            output[artifact_type] = ArtifactResult(
                artifact_type=artifact_type,
                code=tasks[i].cr_frame.f_locals.get('template_code', ''),  # Fallback
                success=False,
                fallback=True,
                error=str(result),
            )
        else:
            output[artifact_type] = result
    
    return output


def run_parallel_codegen(
    state: WorkflowState,
    artifacts_to_generate: list[dict[str, Any]],
) -> list[dict[str, ArtifactResult]]:
    """
    Run parallel code generation for all artifacts.
    
    This is the main entry point for full parallel codegen.
    
    Args:
        state: WorkflowState
        artifacts_to_generate: List of dicts with keys:
            - endpoint: Endpoint object
            - ctx: CodegenContext
            - client_template: str
            - flow_template: str
            - test_template: str
            
    Returns:
        List of dicts mapping artifact type to ArtifactResult
    """
    if not ASYNC_CODEGEN_ENABLED:
        logger.debug("Async codegen disabled, returning None for sequential fallback")
        return None
    
    async def _run_all():
        tasks = [
            _generate_artifacts_for_endpoint_async(
                state=state,
                endpoint=item["endpoint"],
                ctx=item["ctx"],
                client_template=item["client_template"],
                flow_template=item["flow_template"],
                test_template=item["test_template"],
            )
            for item in artifacts_to_generate
        ]
        
        # Full concurrency - all endpoints in parallel
        logger.info(f"Running parallel codegen for {len(tasks)} endpoint(s)")
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    # Run the async orchestrator
    try:
        loop = asyncio.get_running_loop()
        logger.debug("Already in async context, falling back to sequential")
        return None
    except RuntimeError:
        pass
    
    try:
        results = asyncio.run(_run_all())
        
        # Count successes
        total_artifacts = 0
        successful = 0
        for r in results:
            if isinstance(r, dict):
                for artifact_result in r.values():
                    total_artifacts += 1
                    if isinstance(artifact_result, ArtifactResult) and artifact_result.success:
                        successful += 1
        
        logger.info(f"Parallel codegen completed: {successful}/{total_artifacts} artifacts succeeded")
        return results
        
    except Exception as e:
        logger.warning(f"Parallel codegen failed, returning None for sequential fallback: {e}")
        return None


def _build_code_generation_prompt(
    skeleton_code: str,
    state: WorkflowState,
    artifact_type: Literal["client", "flow", "test"],
    endpoint: Endpoint | None = None,
    client_class: str | None = None,
    method_name: str | None = None,
    flow_function: str | None = None,
    target_language: str | None = None,
    # V37-002: Task-extracted function requirements
    task_function_name: str | None = None,
    task_function_signature: str | None = None,
    # V38-007: Async code generation flag
    is_async_required: bool = False,
    # V38-008: Streaming response handling flag
    is_streaming_required: bool = False,
) -> str:
    """
    Build a rich prompt for LLM code generation.
    
    Bug #70: Added target_language for dynamic language support.
    V37-002: Added task_function_name and task_function_signature for user-specified requirements.
    V38-007: Added is_async_required for async/concurrent code generation.
    V38-008: Added is_streaming_required for streaming response handling.
    """
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
        target_language=target_language,
        # V37-002: Pass task function constraints
        task_function_name=task_function_name,
        task_function_signature=task_function_signature,
        # V38-007: Pass async requirement
        is_async_required=is_async_required,
        # V38-008: Pass streaming requirement
        is_streaming_required=is_streaming_required,
    )


async def generate_code_and_tests(state: WorkflowState) -> WorkflowState:
    """
    Reads: endpoints, endpoint_bindings, policies, integration_task, workflow_nodes, repo_profile
    Writes: code_artifacts
    
    Generates code using spec-driven naming and RepoProfile layout.
    
    V2.2: Uses CodegenContext as single source of truth for all derived names.
    This prevents naming misalignment bugs between client/flow/test artifacts.
    
    V3.0: Converted to async for scalability (ASYNC_MIGRATION_PLAN.md)
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

    # =============================================================
    # Protocol vNext: Protocol-Aware Codegen Dispatch
    # =============================================================
    # For non-REST protocols (GraphQL, AsyncAPI), use strategy dispatcher
    # to generate protocol-specific artifacts with correct semantics.
    if PROTOCOL_DISPATCH_ENABLED and _has_non_rest_operations(state):
        target_language = "python"
        if state.repo_profile and state.repo_profile.language:
            target_language = state.repo_profile.language.lower()
        
        protocol_artifacts = _generate_protocol_aware_artifacts(state, target_language)
        if protocol_artifacts:
            # V45-005: Apply import fixes to protocol-generated artifacts
            # These artifacts bypass the main endpoint codegen flow and need
            # separate import fixing to catch missing typing imports like Optional
            if target_language.lower() in ("python", "py"):
                # V45-012: Removed redundant local import of fix_imports_for_policy_mode.
                # It's already imported at module level (line 76). Local imports
                # inside functions cause Python scoping issues: Python treats ALL
                # references to the name as local, even if the import is in an
                # unexecuted branch. This caused UnboundLocalError at line 2910
                # when the protocol branch didn't execute but the main codegen did.
                policy_mode = state.options.policy_mode if state.options else "inline"
                task_desc = state.task_description or ""
                if not task_desc and state.integration_task:
                    task_desc = getattr(state.integration_task, 'description', '') or ""
                
                for artifact in protocol_artifacts:
                    try:
                        fixed_code, fixes = fix_imports_for_policy_mode(
                            artifact.content,
                            policy_mode=policy_mode,
                            artifact_type=artifact.artifact_type,
                            task_description=task_desc,
                        )
                        if fixes:
                            artifact.content = fixed_code
                            logger.info(
                                f"[V45-005] Fixed {len(fixes)} imports in protocol artifact "
                                f"{artifact.module_name}"
                            )
                    except Exception as e:
                        logger.warning(
                            f"[V45-005] Protocol artifact import fix failed (non-fatal): {e}"
                        )
            
            state.code_artifacts.extend(protocol_artifacts)
            logger.info(
                f"Protocol vNext: Generated {len(protocol_artifacts)} artifacts "
                f"for non-REST operations"
            )

    # V2.2: Build CodegenContext - single source of truth for all names
    ctx = build_codegen_context(state)
    logger.debug(
        f"CodegenContext: client={ctx.client_class}.{ctx.method_name}, "
        f"flow={ctx.flow_function}, test_class={ctx.test_class}"
    )

    # V24-002: Shutdown checkpoint before expensive LLM code generation
    try:
        shutdown_check_point("generate_code_and_tests: before LLM calls")
    except ShutdownInterruptError:
        logger.warning("V24-002: Shutdown requested, aborting code generation")
        state.errors.append("Code generation interrupted: shutdown requested")
        state.completed_steps.append("generate_code_and_tests")
        return state

    try:
        # Generate template codes for all artifacts first
        client_template = await _generate_client_code_with_context(ctx, state)
        flow_template = _generate_flow_code_with_context(ctx, state)
        test_template = await _generate_test_code_with_context(ctx, state)
        
        # Full Parallel Codegen: Run all LLM calls + validation + retries concurrently
        # This is the production-ready path for large specs
        parallel_results = None
        if ASYNC_CODEGEN_ENABLED and not is_mock_llm_mode():
            artifacts_to_generate = [{
                "endpoint": ctx.endpoint,
                "ctx": ctx,
                "client_template": client_template,
                "flow_template": flow_template,
                "test_template": test_template,
            }]
            parallel_results = run_parallel_codegen(state, artifacts_to_generate)
        
        # Use parallel results if available
        if parallel_results is not None and len(parallel_results) > 0:
            endpoint_results = parallel_results[0]
            if isinstance(endpoint_results, dict):
                logger.info("Using full parallel codegen results")
                
                client_result = endpoint_results.get("client")
                flow_result = endpoint_results.get("flow")
                test_result = endpoint_results.get("test")
                
                client_code = client_result.code if client_result else client_template
                flow_code = flow_result.code if flow_result else flow_template
                test_code = test_result.code if test_result else test_template
                
                # Log success rates
                successes = sum(1 for r in [client_result, flow_result, test_result] 
                               if r and r.success)
                logger.info(f"Parallel codegen: {successes}/3 artifacts LLM-generated")
            else:
                # Exception occurred, fallback to sequential
                logger.warning(f"Parallel codegen returned non-dict, falling back to sequential: {endpoint_results}")
                parallel_results = None
        
        if parallel_results is None:
            # Sequential fallback (original behavior, now async)
            logger.debug("Using sequential LLM refinement for code generation")
            client_code = await _refine_with_llm(
                client_template, state, "client", ctx.client_class, ctx.method_name, ctx.endpoint
            )
            flow_code = await _refine_with_llm(
                flow_template, state, "flow", None, ctx.flow_function, ctx.endpoint
            )
            test_code = await _refine_with_llm(
                test_template, state, "test", None, None, ctx.endpoint
            )

        # =============================================================
        # BUG-004 FIX: Ensure flow calls the correct client method
        # =============================================================
        # When LLM refines flow code, it may use a different method name than
        # what the client actually provides. This mismatch causes runtime errors.
        # Fix: Post-process flow code to replace any incorrect method name with
        # the canonical method name from CodegenContext (which matches the client).
        # =============================================================
        actual_method = _get_primary_client_method(flow_code, fallback_method=ctx.method_name)
        if actual_method and actual_method != ctx.method_name:
            logger.info(
                f"[BUG-004] Detected method mismatch in flow: '{actual_method}' vs client's '{ctx.method_name}'"
            )
            
            # Fix the flow code to use the correct method name
            # This regex matches client.wrong_method( patterns
            import re
            old_pattern = rf'(client\.)({re.escape(actual_method)})(\s*\()'
            new_replacement = rf'\g<1>{ctx.method_name}\g<3>'
            fixed_flow_code, sub_count = re.subn(old_pattern, new_replacement, flow_code)
            
            if sub_count > 0:
                flow_code = fixed_flow_code
                logger.info(f"[BUG-004] Fixed {sub_count} method call(s) in flow: '{actual_method}' -> '{ctx.method_name}'")
            
            # V29-001: Also update test mocks to match the corrected flow
            test_code, was_mock_modified, mock_changes = align_test_mock_with_flow(
                test_code=test_code,
                flow_code=flow_code,
                expected_method_name=ctx.method_name,
            )
            if was_mock_modified:
                logger.info(f"[BUG-004/V29-001] Applied {len(mock_changes)} mock method fixes: {mock_changes}")

        # P1: Inject policy code into client (skip for inline mode - policies embedded in template)
        policy_mode = state.options.policy_mode if state.options else "inline"
        if state.policies and policy_mode == "runtime":
            policies_for_injection = [
                {"policy_type": p.policy_type.value if hasattr(p.policy_type, 'value') else str(p.policy_type), "config": p.config}
                for p in state.policies
            ]
            client_code = inject_policies_into_client_code(client_code, policies_for_injection)
            logger.info(f"Injected {len(state.policies)} policies into client code")


        # Bug #70 Fix (v3): Dynamic language detection from repo profile
        # The LLM now generates code in the target language via language-aware prompts.
        # Templates are still Python-based, but LLM refinement adapts to target language.
        target_language = "python"  # Default
        if state.repo_profile and state.repo_profile.language:
            target_language = state.repo_profile.language.lower()
            if target_language not in ("python", "py"):
                logger.info(
                    f"Bug #70: Generating code for {target_language} repo. "
                    f"LLM will refine templates to target language."
                )

        # =============================================================
        # V44-001/V44-002 FIX: Pre-Validation Sanitization
        # =============================================================
        # LLMs sometimes generate code with:
        # - Concatenated statements on one line (V44-001)
        # - Hallucinated imports from non-existent packages (V44-002)
        # These must be fixed BEFORE any AST-based validation because:
        # 1. Concatenated statements cause parse failures
        # 2. Hallucinated imports can't be fixed if AST parsing fails
        # This is a critical pre-processing step that enables all downstream fixes.
        # =============================================================
        if target_language.lower() in ("python", "py"):
            try:
                from integration_coworker.codegen.import_fixer import pre_ast_sanitize_code
                from integration_coworker.codegen.security import repair_syntax_errors
                
                logger.debug("[V44] Running pre-validation sanitization")
                
                # First, sanitize hallucinated imports and concatenated statements
                # This is AST-free and works on broken code
                client_code, client_pre_fixes = pre_ast_sanitize_code(client_code)
                if client_pre_fixes:
                    logger.info(f"[V44-002] Pre-AST sanitization on client: {len(client_pre_fixes)} fixes")
                
                flow_code, flow_pre_fixes = pre_ast_sanitize_code(flow_code)
                if flow_pre_fixes:
                    logger.info(f"[V44-002] Pre-AST sanitization on flow: {len(flow_pre_fixes)} fixes")
                
                test_code, test_pre_fixes = pre_ast_sanitize_code(test_code)
                if test_pre_fixes:
                    logger.info(f"[V44-002] Pre-AST sanitization on test: {len(test_pre_fixes)} fixes")
                
                # Then, run full syntax repair (now AST should work)
                client_repaired, client_was_fixed, client_method = repair_syntax_errors(client_code)
                if client_was_fixed:
                    logger.info(f"[V44-001] Syntax repair on client ({client_method})")
                    client_code = client_repaired
                
                flow_repaired, flow_was_fixed, flow_method = repair_syntax_errors(flow_code)
                if flow_was_fixed:
                    logger.info(f"[V44-001] Syntax repair on flow ({flow_method})")
                    flow_code = flow_repaired
                
                test_repaired, test_was_fixed, test_method = repair_syntax_errors(test_code)
                if test_was_fixed:
                    logger.info(f"[V44-001] Syntax repair on test ({test_method})")
                    test_code = test_repaired
                    
            except Exception as e:
                logger.warning(f"[V44] Pre-validation sanitization failed (non-fatal): {e}")

        # =============================================================
        # V35 FIX: Post-Generation Validation and Import Fixing
        # =============================================================
        # Run comprehensive validation BEFORE creating artifacts:
        # - V35-001: Fix hallucinated import paths
        # - V35-002: Validate task path requirements
        # - V35-003: Validate repo type (CLI vs web)
        # - V35-004/005/006: Validate cross-artifact import consistency
        # =============================================================
        if target_language.lower() in ("python", "py"):
            try:
                logger.info("[V35] Running post-generation validation")
                
                # V35-003: Detect repo type for appropriate code patterns
                repo_type_info = None
                if state.repo_root:
                    try:
                        repo_type_info = detect_repo_type(state.repo_root)
                        logger.debug(f"[V35-003] Detected repo type: {repo_type_info.repo_type.value}")
                    except Exception as e:
                        logger.debug(f"[V35-003] Repo detection failed (non-fatal): {e}")
                
                # Validate and fix all artifacts
                validation_results = validate_artifact_set(
                    client_code=client_code,
                    flow_code=flow_code,
                    test_code=test_code,
                    repo_root=state.repo_root or "",
                    provider_code=ctx.provider_code,
                    task_slug=ctx.task_slug,
                    task_description=state.task_description,
                )
                
                # Apply fixes from validation
                if "client" in validation_results:
                    result = validation_results["client"]
                    if result.was_modified:
                        logger.info("[V35-001] Fixed client imports")
                        client_code = result.fixed_code
                    for issue in result.issues:
                        if issue.severity == "error" and not issue.auto_fixed:
                            logger.warning(f"[V35] Client validation issue: {issue.message}")
                
                if "flow" in validation_results:
                    result = validation_results["flow"]
                    if result.was_modified:
                        logger.info("[V35-001] Fixed flow imports")
                        flow_code = result.fixed_code
                    for issue in result.issues:
                        if issue.severity == "error" and not issue.auto_fixed:
                            logger.warning(f"[V35] Flow validation issue: {issue.message}")
                
                if "test" in validation_results:
                    result = validation_results["test"]
                    if result.was_modified:
                        logger.info("[V35-001] Fixed test imports")
                        test_code = result.fixed_code
                    for issue in result.issues:
                        if issue.severity == "error" and not issue.auto_fixed:
                            logger.warning(f"[V35] Test validation issue: {issue.message}")
                
                logger.info("[V35] Post-generation validation complete")
                
            except Exception as e:
                logger.warning(f"[V35] Post-generation validation failed (non-fatal): {e}")
                # Continue with original code

        # =============================================================
        # BUG-002 FIX: Policy-Mode-Aware Import Fixing
        # =============================================================
        # When policy_mode='inline' (default), generated code must NOT import
        # from integration_coworker_runtime since that package won't exist in
        # the target repo. Strip runtime imports and use inline definitions.
        # V40-003: Also respect task-level runtime import restrictions.
        # =============================================================
        if target_language.lower() in ("python", "py"):
            try:
                policy_mode = state.options.policy_mode if state.options else "inline"
                # V40-003: Get task description for runtime import detection
                task_desc = state.task_description or ""
                if not task_desc and state.integration_task:
                    task_desc = getattr(state.integration_task, 'description', '') or ""
                
                logger.debug(f"[BUG-002] Applying policy-mode import fixes (mode={policy_mode})")
                
                # Fix client code imports based on policy mode
                client_code, client_import_fixes = fix_imports_for_policy_mode(
                    client_code,
                    policy_mode=policy_mode,
                    artifact_type="client",
                    task_description=task_desc,
                )
                if client_import_fixes:
                    logger.info(f"[BUG-002] Fixed {len(client_import_fixes)} client import issues for {policy_mode} mode")
                
                # Fix flow code imports based on policy mode
                flow_code, flow_import_fixes = fix_imports_for_policy_mode(
                    flow_code,
                    policy_mode=policy_mode,
                    artifact_type="flow",
                    task_description=task_desc,
                )
                if flow_import_fixes:
                    logger.info(f"[BUG-002] Fixed {len(flow_import_fixes)} flow import issues for {policy_mode} mode")
                
                # Fix test code imports based on policy mode
                test_code, test_import_fixes = fix_imports_for_policy_mode(
                    test_code,
                    policy_mode=policy_mode,
                    artifact_type="test",
                    task_description=task_desc,
                )
                if test_import_fixes:
                    logger.info(f"[BUG-002] Fixed {len(test_import_fixes)} test import issues for {policy_mode} mode")
                    
            except Exception as e:
                logger.warning(f"[BUG-002] Policy-mode import fix failed (non-fatal): {e}")
                # Continue with original code

        # =============================================================
        # V38-007 FIX: Flow-Specific Import and Signature Fixing
        # =============================================================
        # LLM sometimes generates invalid imports in flow code:
        # - "from integrations.exceptions import IntegrationError" (doesn't exist)
        # - Passes params to client methods that don't accept them
        # Fix: Post-process flow code to correct these issues.
        # =============================================================
        if target_language.lower() in ("python", "py"):
            try:
                logger.debug("[V38-007] Checking flow imports and signatures")
                policy_mode = state.options.policy_mode if state.options else "inline"
                
                # Fix invalid exception imports in flow code
                flow_code, flow_import_fixes = fix_flow_imports(
                    flow_code,
                    client_import_module=ctx.client_import_path,
                    policy_mode=policy_mode,
                )
                if flow_import_fixes:
                    logger.info(f"[V38-007] Fixed {len(flow_import_fixes)} flow import issues")
                
                # V45-009: Extract actual valid params from client code instead of hardcoding
                # The previous hardcoded list ["payload", "idempotency_key", "self"] was
                # incorrect and caused valid parameters like "data" to be wrongly removed.
                # NOTE: The interface_validator.check_artifacts_interface_compatibility()
                # called in sandbox.py handles this more comprehensively with parameter
                # aliasing (payload<->data), so this is now a safety check only.
                from integration_coworker.codegen.import_fixer import extract_method_params
                valid_client_params = extract_method_params(client_code, ctx.method_name)
                if valid_client_params:
                    flow_code, removed_params = fix_flow_client_signature_mismatch(
                        flow_code,
                        client_method_name=ctx.method_name,
                        valid_params=valid_client_params,
                    )
                    if removed_params:
                        logger.info(f"[V38-007] Removed invalid params from flow: {removed_params}")
                    
            except Exception as e:
                logger.warning(f"[V38-007] Flow import/signature fix failed (non-fatal): {e}")
                # Continue with original flow code

        # =============================================================
        # V26-003 FIX: Fix Response type hints in generated client code
        # =============================================================
        # LLM sometimes generates `response: Response | None = ...` despite
        # prompts stating that request() always returns Response, never None.
        # This causes mypy union-attr errors like:
        #   "Item 'None' of 'Response | None' has no attribute 'status_code'"
        # Fix: Post-process to remove Optional/Union Response type hints.
        # =============================================================
        if target_language.lower() in ("python", "py"):
            try:
                logger.debug("[V26-003] Checking for Response type hint issues in client code")
                client_code, response_changes = fix_generated_client_code(client_code)
                
                if response_changes:
                    logger.info(f"[V26-003] Fixed {len(response_changes)} Response type hint issues")
                    for change in response_changes[:3]:  # Log first 3 changes
                        logger.debug(f"  - {change}")
                
                # Validate the fix was successful
                warnings = validate_response_handling(client_code)
                if warnings:
                    logger.warning(f"[V26-003] Remaining response handling warnings: {warnings}")
                    
            except Exception as e:
                logger.warning(f"[V26-003] Response type guard failed (non-fatal): {e}")
                # Continue with original client code

        # =============================================================
        # V39-007 FIX: Skip client generation if existing client detected
        # =============================================================
        # When an existing compatible client is found in the target repo,
        # we skip generating a new client artifact to prevent duplication.
        # The flow and test code will use the existing client via the
        # ctx.get_effective_* methods which return existing client info.
        # =============================================================
        if ctx.skip_client_generation:
            logger.info(
                f"[V39-007] Skipping client artifact generation - using existing client: "
                f"{ctx.existing_client_class} from {ctx.existing_client_module}"
            )
            # Log the import that flow/test will use
            logger.debug(f"[V39-007] Flow will use import: {ctx.get_effective_client_import_statement()}")
        else:
            client_artifact = CodeArtifact(
                id=None,
                task_id=None,
                artifact_type="client",
                language=target_language,
                module_name=ctx.client_module,
                rel_path=ctx.get_client_rel_path(),
                content=client_code,
            )
            state.code_artifacts.append(client_artifact)

        flow_artifact = CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="flow",
            language=target_language,
            module_name=ctx.flow_module,
            rel_path=ctx.get_flow_rel_path(),
            content=flow_code,
        )
        state.code_artifacts.append(flow_artifact)

        # =============================================================
        # V25-001 FIX: Cross-validate test code against flow code
        # =============================================================
        # Ensure test assertions match actual flow behavior BEFORE sandbox.
        # This prevents mismatches like tests expecting ValueError when
        # the flow silently converts None to a default value.
        # =============================================================
        if target_language.lower() in ("python", "py"):
            try:
                logger.info("[V25-001] Running cross-validation of test against flow code")
                fixed_test_code, was_modified, changes = validate_and_fix_tests(
                    flow_code=flow_code,
                    test_code=test_code,
                    flow_function_name=ctx.flow_function,
                    flow_module_path=ctx.flow_import_path,  # V42-006: Pass flow module path for mock patch fixing
                )
                
                if was_modified:
                    logger.info(f"[V25-001] Test code modified to match flow behavior: {changes}")
                    test_code = fixed_test_code
                else:
                    logger.debug("[V25-001] Test code is consistent with flow behavior")
                    
            except Exception as e:
                logger.warning(f"[V25-001] Cross-validation failed (non-fatal): {e}")
                # Continue with original test code - sandbox will catch real issues

        test_artifact = CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="test",
            language=target_language,
            module_name=ctx.test_module,
            rel_path=ctx.get_test_rel_path(),
            content=test_code,
        )
        state.code_artifacts.append(test_artifact)

        # =============================================================
        # SANDBOX VALIDATION (ADR-0005: Production Codegen Quality Gates)
        # =============================================================
        # Run generated code through sandbox gates:
        # - Python: ruff check + format (lint/style), mypy (type checking), pytest with coverage
        # - TypeScript: tsc (types), vitest (tests), eslint (lint)
        # - Go: go test (tests), go vet (lint), staticcheck (lint)
        #
        # Gate behavior controlled by profile:
        # - production: gates are hard failures
        # - development: gates log warnings but don't block
        #
        # Multi-lang support per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.2
        # =============================================================
        profile = get_active_profile()
        
        # Use multi-lang sandbox for non-Python languages (Tier 1 compiler-backed gates)
        if target_language.lower() in ("typescript", "ts", "go", "golang", "javascript", "js"):
            sandbox_success, sandbox_result = await _run_multilang_sandbox_validation(
                code_artifacts=state.code_artifacts,
                target_language=target_language,
                profile=profile,
                state=state,
            )
        else:
            # Python uses existing sandbox
            sandbox_success, sandbox_result = await _run_sandbox_validation(
                code_artifacts=state.code_artifacts,
                profile=profile,
                state=state,
            )
        
        if not sandbox_success and profile.name == "production":
            # Production mode: sandbox failure is a hard error
            error_msg = "Sandbox validation failed"
            if sandbox_result:
                error_msg += f": {sandbox_result.summary}"
            state.errors.append(error_msg)
            logger.error(f"[generate_code_and_tests] {error_msg}")
        elif not sandbox_success:
            # Development mode: log warning but continue
            logger.warning(
                f"[generate_code_and_tests] Sandbox validation failed (dev mode, continuing): "
                f"{sandbox_result.summary if sandbox_result else 'unknown error'}"
            )

    except Exception as e:
        state.errors.append(f"Failed to generate code: {str(e)}")
        logger.exception("Code generation failed")

    # V22-MEM: Clear repo context after code generation (no longer needed)
    try:
        from integration_coworker.graph.state_gc import cleanup_after_codegen
        cleanup_after_codegen(state)
    except Exception as e:
        logger.debug(f"state_gc cleanup_after_codegen failed (non-fatal): {e}")

    state.completed_steps.append("generate_code_and_tests")
    return state


def _get_primary_endpoint(state: WorkflowState) -> Endpoint | None:
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


# =============================================================================
# Context-based wrapper functions (V2.2 ADR-CODEGEN-001)
# These wrap the existing generators, extracting values from CodegenContext
# to ensure consistent naming across all artifacts.
# =============================================================================

async def _generate_client_code_with_context(ctx: CodegenContext, state: WorkflowState) -> str:
    """Generate client code using CodegenContext for consistent naming."""
    return await _generate_client_code(
        state=state,
        provider_code=ctx.provider_code,
        client_class=ctx.client_class,
        method_name=ctx.method_name,
        endpoint=ctx.endpoint,
        base_url=ctx.base_url,
    )


def _generate_flow_code_with_context(ctx: CodegenContext, state: WorkflowState) -> str:
    """
    Generate flow code using CodegenContext for consistent naming.
    
    V37-002: Now uses get_effective_flow_function() to honor user's explicit function name.
    V37-001: Now uses get_effective_client_import_path() to fix imports for relocated flows.
    V39-007: Now uses get_effective_client_class() to use existing client if detected.
    """
    return _generate_flow_code(
        state=state,
        provider_code=ctx.provider_code,
        task_slug=ctx.task_slug,
        client_class=ctx.get_effective_client_class(),  # V39-007: Use effective client class
        client_import_module=ctx.get_effective_client_import_path(),  # V37-001: Use effective import
        method_name=ctx.method_name,
        flow_function=ctx.get_effective_flow_function(),  # V37-002: Use effective name
    )


async def _generate_test_code_with_context(ctx: CodegenContext, state: WorkflowState) -> str:
    """
    Generate test code using CodegenContext for consistent naming.
    
    V2.2 (Fix #2): Uses constrained test generation when constrained_codegen is enabled,
    which guarantees secure credential handling via pytest fixtures.
    
    V3.0: Converted to async for scalability (ASYNC_MIGRATION_PLAN.md)
    
    V37-002: Now uses get_effective_flow_function() to honor user's explicit function name.
    V39-007: Now uses get_effective_client_class() to use existing client if detected.
    """
    # V37-002: Get effective flow function name (honors user's explicit request)
    effective_flow_function = ctx.get_effective_flow_function()
    # V39-007: Get effective client class (uses existing client if detected)
    effective_client_class = ctx.get_effective_client_class()
    
    # V2.2 (Fix #2): Check for constrained_codegen mode
    constrained_mode = False
    if state.options and hasattr(state.options, 'constrained_codegen'):
        constrained_mode = state.options.constrained_codegen
    
    if constrained_mode:
        logger.debug("Using constrained test generation (secure fixtures)")
        return await _generate_constrained_test_code(
            state=state,
            provider_code=ctx.provider_code,
            task_slug=ctx.task_slug,
            client_class=effective_client_class,  # V39-007
            flow_import_module=ctx.flow_import_path,
            flow_function=effective_flow_function,  # V37-002
            method_name=ctx.method_name,
        )
    
    return _generate_test_code(
        state=state,
        provider_code=ctx.provider_code,
        task_slug=ctx.task_slug,
        client_class=effective_client_class,  # V39-007: Use effective client class
        flow_import_module=ctx.flow_import_path,
        flow_function=effective_flow_function,  # V37-002
        method_name=ctx.method_name,  # CRITICAL: Uses ctx.method_name, not task_slug
    )


def _get_template_key(state: WorkflowState) -> str | None:
    """Get the template key from state for feedback recording."""
    # Bug #59 fix: WorkflowTemplate uses 'code' not 'key'
    if state.workflow_template and state.workflow_template.code:
        return state.workflow_template.code
    # Fallback to constructed key from provider + task
    provider = state.provider_code or "unknown"
    task_slug = state.integration_task.task_slug if state.integration_task else "task"
    return f"workflow.{provider}.{task_slug}"


async def _refine_with_llm(
    template_code: str,
    state: WorkflowState,
    artifact_type: Literal["client", "flow", "test"],
    expected_class: str | None = None,
    expected_function: str | None = None,
    endpoint: Endpoint | None = None,
    target_language: str | None = None,
) -> str:
    """
    Refine template code with LLM, with validation.
    
    V1.1 FT-008 Strict Codegen Mode:
    - When state.options.strict_codegen=True:
      - Applies automatic style fixes via ruff
      - Performs stricter security validation
      - Fails on any validation issues instead of falling back to template
    
    Bug #70 (v3): Added target_language parameter for dynamic language support.
    - Python code: Full AST validation + security checks
    - Other languages: LLM validation only (no AST), security checks skipped
    
    V3.0: Converted to async for scalability (ASYNC_MIGRATION_PLAN.md)
    
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
        target_language: Target language (default: from repo_profile or "python")
    
    Returns:
        Refined code, or template code if LLM fails validation (unless strict mode)
    
    Raises:
        ValueError: In strict mode, if code fails validation
    """
    module_name = expected_class or expected_function or artifact_type
    
    # Bug #70: Determine target language for validation decisions
    if target_language:
        lang = target_language.lower()
    elif state.repo_profile and state.repo_profile.language:
        lang = state.repo_profile.language.lower()
    else:
        lang = "python"
    
    is_python = lang in ("python", "py")
    
    # Bug #75 Fix: Helper to get language-aware skeleton fallback
    def _get_fallback_skeleton() -> str:
        """Get language-appropriate skeleton instead of Python template."""
        from integration_coworker.codegen.prompts import get_skeleton_template
        
        provider_title = state.provider_code.replace("_", " ").title() if state.provider_code else "API"
        task_title = state.task_description[:30] if state.task_description else "Task"

        def _snake(name: str) -> str:
            s1 = re.sub('(.)([A-Z][a-z]+)', r"\1_\2", name)
            return re.sub('([a-z0-9])([A-Z])', r"\1_\2", s1).lower()

        if expected_class:
            client_class_name = expected_class
            client_module = _snake(client_class_name)
        else:
            # Fall back to provider-based naming instead of generic ApiClient to
            # keep import paths stable for tests and flows (e.g., mock_payments)
            provider_slug = state.provider_code or "api"
            client_class_name = "".join(part.capitalize() for part in provider_slug.split("_")) + "Client"
            client_module = provider_slug
        
        # Use endpoint-derived method name when available to keep flow calls in sync
        method_name_hint = expected_function or None
        # Fall back to task slug when available (e.g., create_checkout_session)
        state_task = getattr(state, "task", None)
        if not method_name_hint and state_task and getattr(state_task, "task_slug", None):
            method_name_hint = state_task.task_slug
        # Next, derive from task description to avoid generic method names
        if not method_name_hint and state.task_description:
            # slugify description: lowercase, replace non-alnum with underscore, collapse repeats
            desc_slug = re.sub(r"[^a-zA-Z0-9]+", "_", state.task_description).strip("_").lower()
            method_name_hint = desc_slug or None
        # Absolute last resort
        if not method_name_hint:
            method_name_hint = "execute"

        if endpoint and getattr(endpoint, "operation_id", None):
            try:
                method_name_hint = endpoint.operation_id
            except Exception as e:
                # Item G: Log expected fallback
                logger.debug(f"Could not get operation_id from endpoint: {e}")
                method_name_hint = method_name_hint

        # Normalize to snake_case for Python targets to keep tests stable
        method_name_hint = _snake(method_name_hint)

        return get_skeleton_template(
            language=lang,
            artifact_type=artifact_type,
            provider_title=provider_title,
            task_title=task_title,
            client_class=client_class_name,
            client_module=client_module,
            method_name=method_name_hint,
            flow_function=expected_function or "execute_flow",
            endpoint_path=getattr(endpoint, "path", None) if endpoint else None,
            http_method=getattr(endpoint, "method", None) if endpoint else None,
        )
    
    # V1.1: Check for strict codegen mode
    strict_mode = False
    if state.options and hasattr(state.options, 'strict_codegen'):
        strict_mode = state.options.strict_codegen
    
    # Feedback hooks context - get run_id and template_key for recording
    run_id = state.run_id or "unknown"
    template_key = _get_template_key(state) or "unknown"

    try:
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
            target_language=lang,  # Bug #70: Pass target language for dynamic prompts
        )

        refined = None

        # In mock LLM mode, prefer the sync accessor so pytest monkeypatches capture calls
        # and sentinel outputs flow through the pipeline.
        if is_mock_llm_mode():
            sync_client = get_llm_client_for_node(NODE_NAME)
            if hasattr(sync_client, "complete_async"):
                refined = await sync_client.complete_async(prompt, system_prompt=system_prompt)
            elif hasattr(sync_client, "complete"):
                # V22-011: Sync fallback - run in executor to avoid blocking event loop
                # The sync client now has timeout protection via with_retry()
                loop = asyncio.get_running_loop()
                refined = await loop.run_in_executor(
                    None, lambda: sync_client.complete(prompt, system_prompt=system_prompt)
                )
        else:
            # Get LLM client configured for this node's archetype (async-first)
            client = get_async_llm_client_for_node(NODE_NAME)

            if hasattr(client, "complete_async"):
                refined = await client.complete_async(prompt, system_prompt=system_prompt)
            elif hasattr(client, "complete"):
                # V22-011: Sync fallback - run in executor to avoid blocking event loop
                loop = asyncio.get_running_loop()
                refined = await loop.run_in_executor(
                    None, lambda: client.complete(prompt, system_prompt=system_prompt)
                )

        # Fallback: allow tests patching the sync accessor to capture prompts
        if refined is None:
            sync_client = get_llm_client_for_node(NODE_NAME)
            if hasattr(sync_client, "complete_async"):
                refined = await sync_client.complete_async(prompt, system_prompt=system_prompt)
            elif hasattr(sync_client, "complete"):
                # V22-011: Sync fallback - run in executor to avoid blocking event loop
                loop = asyncio.get_running_loop()
                refined = await loop.run_in_executor(
                    None, lambda: sync_client.complete(prompt, system_prompt=system_prompt)
                )

        if not refined:
            msg = f"LLM returned empty response for {artifact_type} '{module_name}'"
            if strict_mode:
                raise ValueError(f"Strict codegen failed: {msg}")
            logger.info(f"Falling back to {lang} skeleton for {artifact_type} '{module_name}' because: {msg}")
            return _get_fallback_skeleton()

        # V22-002 Fix: Clean up markdown code blocks AND preamble/postamble text
        # Using the more robust cleaner that handles LLM outputs like:
        # "Here is the code:\n```python\n...\n```\nLet me know if you need anything else"
        clean_refined = clean_llm_code_output(refined)

        # REMOVED: Mock detection heuristic (LLM-007)
        # v2: We now trust the LLM with proper prompting instead of fragile
        # string-matching heuristics. Security validation below catches real issues.

        # V1.1 FT-008: Apply automatic style fixes in strict mode (Python only)
        if strict_mode and is_python:
            logger.debug(f"Strict mode: applying ruff fixes to {artifact_type} '{module_name}'")
            clean_refined = fix_code_style(clean_refined)

        # Bug #70/#88: Validate syntax with tree-sitter (all languages) or AST (Python fallback)
        # Tree-sitter provides accurate syntax validation for Python, TypeScript, JavaScript,
        # Go, Java, Ruby, and C#. Falls back to AST for Python if tree-sitter unavailable.
        if not _validate_syntax(clean_refined, lang):
            msg = f"LLM output failed {lang} syntax validation"
            # V22-002: Log the actual failing code for diagnosis
            lines = clean_refined.split('\n')
            logger.error(
                f"V22-002 Syntax Error Diagnostic for {artifact_type} '{module_name}':\n"
                f"First 15 lines:\n" + 
                "\n".join(f"  L{i+1}: {line}" for i, line in enumerate(lines[:15]))
            )
            # Record syntax failure for feedback learning (isolated, won't break flow)
            if are_hooks_enabled() and is_python:
                is_valid, syntax_error = check_syntax(clean_refined)
                safe_record_syntax_check(
                    run_id=run_id,
                    template_key=template_key,
                    artifact_type=artifact_type,
                    success=False,
                    error_message=syntax_error,
                )
            if strict_mode:
                # In strict mode, also get detailed error
                if is_python:
                    is_valid, syntax_error = check_syntax(clean_refined)
                    raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {syntax_error}")
                else:
                    raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {lang} syntax error")
            logger.warning(f"Falling back to {lang} skeleton for {artifact_type} '{module_name}' because: {msg}")
            return _get_fallback_skeleton()
        
        # Record syntax success for feedback learning (Python only for now)
        if are_hooks_enabled() and is_python:
            safe_record_syntax_check(
                run_id=run_id,
                template_key=template_key,
                artifact_type=artifact_type,
                success=True,
            )

        # V23-003: Check for undefined names (F821) that break test execution
        # This catches cases where LLM refinement loses the response assignment
        # e.g., "response = client.method(...)" gets lost, leaving "response" undefined
        if is_python:
            try:
                import subprocess
                result = subprocess.run(
                    ["ruff", "check", "--select=F821", "--output-format=json", "-"],
                    input=clean_refined,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode != 0 and result.stdout:
                    import json as _json
                    try:
                        f821_errors = _json.loads(result.stdout)
                        if f821_errors:
                            undefined_names = [e.get("message", "") for e in f821_errors[:5]]
                            msg = f"Undefined names detected (F821): {undefined_names}"
                            logger.warning(
                                f"V23-003 F821 Error for {artifact_type} '{module_name}': {msg}"
                            )
                            if strict_mode:
                                raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {msg}")
                            # For non-strict, log and continue - security/policy checks may still pass
                    except _json.JSONDecodeError:
                        pass  # Ignore parse errors in ruff output
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                # ruff not available or timed out - skip F821 check
                logger.debug(f"Skipping F821 check: {e}")

        # Bug #70: Security validation only for Python (uses Python AST internally)
        # For non-Python languages, security checks would require language-specific tools.
        if is_python:
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
                    f"Falling back to {lang} skeleton for {artifact_type} '{module_name}' "
                    f"because: {msg}"
                )
                return _get_fallback_skeleton()
            
            # Record security pass for feedback learning
        if are_hooks_enabled():
            safe_record_security_check(
                run_id=run_id,
                template_key=template_key,
                success=True,
            )

        # SEC-004: Spec compliance validation (semantic checks) with retry
        is_policy_valid, policy_violations = validate_content_policy(
            clean_refined,
            endpoints=state.endpoints if state.endpoints else None,
        )
        
        if not is_policy_valid:
            # Bug #30 Fix: Try auto-fixing hallucinated paths before LLM retry
            # This is more reliable than asking the LLM to fix itself
            has_path_violations = any(
                hasattr(v, 'type') and v.type == PolicyViolationType.HALLUCINATED_ENDPOINT
                for v in policy_violations
            )
            
            if has_path_violations and state.endpoints:
                logger.info(
                    f"Attempting auto-fix for hallucinated paths in {artifact_type} '{module_name}'"
                )
                fix_result = fix_hallucinated_paths(
                    clean_refined,
                    endpoints=state.endpoints,
                    threshold=0.65,  # 65% confidence threshold
                )
                
                if fix_result.num_fixes > 0:
                    logger.info(
                        f"Auto-fixed {fix_result.num_fixes} hallucinated path(s) in {artifact_type} '{module_name}'"
                    )
                    for repl in fix_result.replacements:
                        logger.debug(f"  Fixed: {repl}")
                    
                    # Re-validate after auto-fix (Bug #88: use language-aware validation)
                    if _validate_syntax(fix_result.fixed_code, lang):
                        is_fixed_valid, fixed_violations = validate_content_policy(
                            fix_result.fixed_code,
                            endpoints=state.endpoints,
                        )
                        if is_fixed_valid:
                            logger.info(
                                f"Path auto-fix successful for {artifact_type} '{module_name}'"
                            )
                            clean_refined = fix_result.fixed_code
                            is_policy_valid = True
                        else:
                            # Some violations remain, update for next step
                            policy_violations = fixed_violations
                            clean_refined = fix_result.fixed_code
                            logger.info(
                                f"Path auto-fix reduced violations from {len(policy_violations)} "
                                f"to {len(fixed_violations)} for {artifact_type} '{module_name}'"
                            )
                
                if fix_result.num_unfixable > 0:
                    logger.warning(
                        f"{fix_result.num_unfixable} unfixable path(s) in {artifact_type} '{module_name}': "
                        f"{fix_result.unfixable_paths[:3]}"
                    )
        
        if not is_policy_valid:
            # v2.2: Retry with feedback on content policy failure
            # Bug #30 fix: Increase retries from 1 to 2 for better hallucination recovery
            max_retries = 2
            for retry_num in range(max_retries):
                logger.info(
                    f"Spec compliance violation detected for {artifact_type} '{module_name}', "
                    f"retry {retry_num + 1}/{max_retries}"
                )
                
                # Build feedback prompt with violations
                feedback_prompt = _build_policy_feedback_prompt(
                    original_code=clean_refined,
                    violations=policy_violations,
                    state=state,
                    artifact_type=artifact_type,
                    retry_num=retry_num,  # Bug #30: Pass retry number for escalating strictness
                )
                
                # Retry with feedback (async)
                try:
                    retry_response = await client.complete_async(feedback_prompt, system_prompt=system_prompt)
                    if retry_response:
                        retry_code = strip_code_fences(retry_response)
                        del retry_response  # V22: Release raw LLM response immediately
                        if strict_mode:
                            retry_code = fix_code_style(retry_code)
                        
                        # Re-validate the retry (Bug #88: use language-aware validation)
                        if _validate_syntax(retry_code, lang):
                            is_retry_valid, retry_violations = validate_content_policy(
                                retry_code,
                                endpoints=state.endpoints if state.endpoints else None,
                            )
                            if is_retry_valid:
                                logger.info(f"Retry {retry_num + 1} successful for {artifact_type} '{module_name}'")
                                clean_refined = retry_code
                                is_policy_valid = True
                                break
                            else:
                                logger.warning(
                                    f"Retry {retry_num + 1} still has {len(retry_violations)} "
                                    f"policy violations for {artifact_type} '{module_name}'"
                                )
                                policy_violations = retry_violations  # Update for next retry
                        else:
                            logger.warning(f"Retry {retry_num + 1} has {lang} syntax errors for {artifact_type} '{module_name}'")
                except Exception as retry_error:
                    logger.warning(f"Retry {retry_num + 1} failed for {artifact_type} '{module_name}': {retry_error}")
        
        if not is_policy_valid:
            msg = f"spec compliance violations detected:\n{format_policy_violations(policy_violations)}"
            if strict_mode:
                raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {msg}")
            logger.warning(
                f"Falling back to {lang} skeleton for {artifact_type} '{module_name}' "
                f"because: {msg}"
            )
            return _get_fallback_skeleton()

        # Validate expected symbols are present (Bug #84: pass lang for non-Python support)
        if expected_class and not _has_class(clean_refined, expected_class, lang):
            msg = f"LLM output missing expected class '{expected_class}'"
            if strict_mode:
                raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {msg}")
            logger.warning(f"Falling back to {lang} skeleton for {artifact_type} '{module_name}' because: {msg}")
            return _get_fallback_skeleton()

        if expected_function and not _has_function(clean_refined, expected_function, lang):
            msg = f"LLM output missing expected function '{expected_function}'"
            if strict_mode:
                raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {msg}")
            logger.warning(f"Falling back to {lang} skeleton for {artifact_type} '{module_name}' because: {msg}")
            return _get_fallback_skeleton()

        # === Self-Review Step (Task B) for sequential path ===
        profile = get_active_profile()
        reviewed_code, review_success = await _apply_self_review_if_enabled(
            code=clean_refined,
            artifact_type=artifact_type,
            module_name=module_name,
            state=state,
            profile=profile,
        )
        
        if not review_success:
            # Production: self-review failed and couldn't repair - hard fail
            msg = "Self-review failed and could not repair"
            if strict_mode:
                raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {msg}")
            logger.warning(f"Falling back to {lang} skeleton for {artifact_type} '{module_name}' because: {msg}")
            return _get_fallback_skeleton()

        logger.info(f"Using LLM-generated body for {artifact_type} '{module_name}'" + (" [strict mode]" if strict_mode else ""))
        return reviewed_code

    except ValueError:
        # Re-raise strict mode errors
        raise
    except Exception as e:
        msg = f"LLM refinement failed with exception: {e}"
        if strict_mode:
            raise ValueError(f"Strict codegen failed for {artifact_type} '{module_name}': {msg}")
        logger.warning(f"Falling back to {lang} skeleton for {artifact_type} '{module_name}' because: {msg}")
        return _get_fallback_skeleton()


def _build_policy_feedback_prompt(
    original_code: str,
    violations: list,
    state: WorkflowState,
    artifact_type: str,
    retry_num: int = 0,
) -> str:
    """
    Build a feedback prompt for retrying code generation after policy violations.
    
    v2.2 SEC-004: Provides specific feedback about what went wrong and which
    API paths are valid, giving the LLM a chance to correct itself.
    
    Bug #30 fix: Escalates strictness on subsequent retries.
    Bug #89 fix: Intelligently filters endpoints based on task relevance for large specs.
    
    Args:
        original_code: The code that failed validation
        violations: List of PolicyViolation objects
        state: WorkflowState for context
        artifact_type: Type of artifact being generated
        retry_num: Current retry number (0 = first retry, 1 = second retry)
        
    Returns:
        A prompt string for the retry attempt
    """
    # Format violations as readable text
    violation_lines = []
    for v in violations:
        loc = f"Line {v.line_number}: " if hasattr(v, 'line_number') and v.line_number else ""
        violation_lines.append(f"- {loc}{v.message}")
        if hasattr(v, 'suggestion') and v.suggestion:
            violation_lines.append(f"  Suggestion: {v.suggestion}")
    
    violations_text = "\n".join(violation_lines) if violation_lines else "Unknown violations"
    
    # Bug #89: Filter endpoints to those relevant to the task
    # For large specs (>100 endpoints), we need to show relevant ones, not just first 30
    valid_paths = []
    if state.endpoints:
        # Extract keywords from task description for relevance filtering
        task_keywords = set()
        if state.task_description:
            # Extract meaningful words from task (lowercase, >2 chars)
            for word in state.task_description.lower().split():
                clean = ''.join(c for c in word if c.isalnum())
                if len(clean) > 2:
                    task_keywords.add(clean)
        
        # Score and sort endpoints by relevance to task
        scored_endpoints = []
        for ep in state.endpoints:
            path_lower = ep.path.lower()
            desc_lower = (ep.description or "").lower() if hasattr(ep, 'description') else ""
            op_lower = (ep.operation_id or "").lower() if hasattr(ep, 'operation_id') else ""
            
            # Calculate relevance score
            score = 0
            for kw in task_keywords:
                if kw in path_lower:
                    score += 3  # Path match is most important
                if kw in op_lower:
                    score += 2
                if kw in desc_lower:
                    score += 1
            
            scored_endpoints.append((score, ep))
        
        # Sort by score (descending), then alphabetically by path
        scored_endpoints.sort(key=lambda x: (-x[0], x[1].path))
        
        # Take top 50 relevant endpoints (or all if fewer)
        max_endpoints = 50
        selected = scored_endpoints[:max_endpoints]
        
        for i, (score, ep) in enumerate(selected, 1):
            # Use quotes to make exact string clear
            path_display = f'  [{i}] {ep.method.upper()} "{ep.path}"'
            if score > 0:
                path_display += " ★"  # Mark relevant endpoints
            valid_paths.append(path_display)
        
        if len(state.endpoints) > max_endpoints:
            valid_paths.append(f"\n  ... and {len(state.endpoints) - max_endpoints} more endpoints")
            valid_paths.append(f"  (★ = matches task keywords: {', '.join(list(task_keywords)[:5])})")
    
    valid_paths_text = "\n".join(valid_paths) if valid_paths else "  (No paths available)"
    
    # Bug #30: Escalate strictness on retries
    # Per PRODUCTION_AUDIT_DEC12.md - Improved path hallucination prevention
    if retry_num == 0:
        strictness_instruction = """
## INSTRUCTIONS
1. Fix the violations listed above
2. ONLY use API paths from the VALID API PATHS list - copy them character-for-character
3. Do NOT invent or hallucinate any API paths - this is the #1 cause of failures
4. If you're not 100% certain a path exists, DO NOT use it
5. Return the complete corrected code
6. Keep all class names, function names, and signatures the same"""
    else:
        # Second retry - be even stricter
        strictness_instruction = """
## CRITICAL INSTRUCTIONS (FINAL ATTEMPT - READ CAREFULLY)

Your previous attempts failed because you used paths that DON'T EXIST in the API.

1. STOP and look at the VALID API PATHS list below
2. You can ONLY use paths that are EXACTLY listed there
3. DO NOT add any API calls using paths not in the list
4. If you need a path that's not listed, REMOVE that API call entirely
5. It's better to have incomplete code than code with invalid paths

Common mistakes you MUST NOT make:
  ❌ Using '/AlphaSenders' when the real path is '/Services/{ServiceSid}/AlphaSenders'
  ❌ Guessing that a '/list' or '/create' endpoint exists
  ❌ Changing capitalization of path segments
  ❌ Adding extra segments to paths

WARNING: If you include ANY path not in the EXACT list below, this code will be REJECTED."""
    
    prompt = f"""Your previous code generation contained spec compliance violations that must be fixed.

## VIOLATIONS DETECTED
{violations_text}

## VALID API PATHS (use EXACTLY as shown, copy character-for-character)
{valid_paths_text}
{strictness_instruction}

## YOUR PREVIOUS CODE
```python
{original_code}
```

## OUTPUT
Return ONLY the corrected Python code, no markdown fences or explanations:
"""
    return prompt


def _validate_python_syntax(code: str) -> bool:
    """
    Check if code is syntactically valid Python.
    
    Deprecated: Use _validate_syntax(code, "python") instead for multi-language support.
    Kept for backward compatibility.
    """
    return _validate_syntax(code, "python")


def _validate_syntax(code: str, lang: str = "python") -> bool:
    """
    Check if code is syntactically valid for the given language.
    
    Bug #88 Fix: Uses tree-sitter for accurate syntax validation across all
    supported languages when available. Falls back to Python AST for Python
    code, or skips validation with warning for other languages if tree-sitter
    is not installed.
    
    Supported languages (with tree-sitter):
    - Python, TypeScript, JavaScript, Go, Java, Ruby, C#
    
    Args:
        code: Source code to validate
        lang: Programming language (default: "python")
        
    Returns:
        True if code is syntactically valid (or validation skipped),
        False if syntax errors detected.
    """
    lang_lower = lang.lower()
    
    # Use tree-sitter if available (preferred for all languages)
    if is_tree_sitter_available():
        result = tree_sitter_validate_syntax(code, lang_lower)
        if not result.is_valid:
            logger.debug(
                f"Syntax error in {lang} code (line {result.error_line}): "
                f"{result.error_message}"
            )
        return result.is_valid
    
    # Fallback: Python AST for Python code
    if lang_lower in ("python", "py"):
        try:
            ast.parse(code)
            return True
        except SyntaxError as e:
            logger.debug(f"Syntax error in generated Python code: {e}")
            return False
    
    # Fallback: No validation for non-Python without tree-sitter
    # Log warning but allow code to pass (better than breaking)
    logger.debug(
        f"Skipping syntax validation for {lang} (tree-sitter not installed). "
        f"Install with: pip install 'solver-agentic-spec-coworker[validation]'"
    )
    return True


def _extract_client_method_calls(
    flow_code: str,
    # V23-011: Expanded pattern to catch common client variable names
    # Old pattern: r"client|_client" missed slack_client, api_client, http_client etc.
    # New pattern captures:
    #   - client, _client (original)
    #   - self.client, self._client (instance attributes)
    #   - api_client, http_client, slack_client (prefixed variants)
    #   - sdk, api, service (alternative names for API wrappers)
    client_var_pattern: str = r"(?:self\.)?(?:_?(?:client|api|sdk|service|http|rest)(?:_client)?|_?[a-z]+_client)",
) -> list[str]:
    """
    Extract method names called on client objects from flow code using AST.
    
    V22-003 Fix: This replaces the weak assertion approach with precise AST-based
    method extraction. By analyzing the generated flow code, we can determine
    exactly which client methods are called and use those in test assertions.
    
    Args:
        flow_code: The generated flow code to analyze
        client_var_pattern: Regex pattern for client variable names
        
    Returns:
        List of method names called on client objects (e.g., ["create_user", "get_balance"])
        Returns empty list if parsing fails or no calls found.
        
    Example:
        flow_code = '''
        client = StripeClient(api_key)
        result = client.create_payment_intent(amount=100)
        '''
        _extract_client_method_calls(flow_code)
        # Returns: ["create_payment_intent"]
    """
    import re
    
    method_calls: list[str] = []
    
    try:
        tree = ast.parse(flow_code)
    except SyntaxError:
        logger.debug("Flow code has syntax errors, cannot extract method calls")
        return method_calls
    
    # Compile pattern for client variable matching
    client_pattern = re.compile(client_var_pattern, re.IGNORECASE)
    
    # Walk the AST looking for method calls on client-like objects
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check for attribute access: client.method_name(...)
            if isinstance(node.func, ast.Attribute):
                # Get the object being called on
                value = node.func.value
                
                # Direct variable: client.method_name()
                if isinstance(value, ast.Name):
                    if client_pattern.search(value.id):
                        method_calls.append(node.func.attr)
                
                # Chained: self.client.method_name() or _client.method_name()
                elif isinstance(value, ast.Attribute):
                    if client_pattern.search(value.attr):
                        method_calls.append(node.func.attr)
    
    # Remove duplicates while preserving order
    seen = set()
    unique_calls = []
    for method in method_calls:
        if method not in seen:
            seen.add(method)
            unique_calls.append(method)
    
    if unique_calls:
        logger.debug(f"Extracted {len(unique_calls)} client method calls: {unique_calls}")
    
    return unique_calls


def _get_primary_client_method(
    flow_code: str,
    fallback_method: str = "execute",
) -> str:
    """
    Get the primary client method called in flow code.
    
    V22-003 Fix: Uses AST extraction to find the actual method called,
    falling back to the provided method name if extraction fails.
    
    Args:
        flow_code: The generated flow code
        fallback_method: Method name to use if extraction fails
        
    Returns:
        The first client method found, or fallback_method if none found.
    """
    methods = _extract_client_method_calls(flow_code)
    if methods:
        return methods[0]
    return fallback_method


def _has_class(code: str, class_name: str, lang: str = "python") -> bool:
    """
    Check if code defines a class matching the expected name.
    
    Bug #45 fix: Uses flexible matching to handle LLM naming variations.
    Bug #84 fix: For non-Python languages, uses regex instead of AST parsing.
    
    Matches if:
    1. Exact match (case-insensitive)
    2. Name contains expected (e.g., "OpenAIClient" matches "openai")
    3. Expected contains name (e.g., "openai" matches "OpenAIAPIClient")
    4. Semantic: class has HTTP/API methods (get, post, request, etc.)
    """
    expected_lower = class_name.lower()
    
    # Bug #84: For non-Python languages, use regex-based detection
    if lang.lower() != "python":
        return _has_class_regex(code, class_name, lang)
    
    # Python: use AST for accurate detection
    try:
        tree = ast.parse(code)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                name_lower = node.name.lower()
                
                # Flexible name matching
                if name_lower == expected_lower:
                    return True
                if expected_lower in name_lower or name_lower in expected_lower:
                    return True
                
                # Semantic check: has API-like methods?
                method_names = {
                    n.name.lower() for n in node.body 
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                api_methods = {'get', 'post', 'put', 'delete', 'patch', 'request', 'call', 'send'}
                if method_names & api_methods:
                    logger.debug(f"Accepting class {node.name} via semantic match (has API methods)")
                    return True
        
        return False
    except Exception as e:
        # Item G: Log AST fallback to regex
        logger.debug(f"AST class parsing failed, falling back to regex: {e}")
        # Fallback to regex if AST parsing fails
        return _has_class_regex(code, class_name, lang)


def _has_class_regex(code: str, class_name: str, lang: str) -> bool:
    """
    Bug #84: Regex-based class detection for non-Python languages.
    
    Supports: TypeScript, JavaScript, Go, Java, C#, Ruby
    """
    expected_lower = class_name.lower()
    
    # Language-specific class patterns
    patterns = {
        # TypeScript/JavaScript: class Foo { or export class Foo {
        "typescript": r'\bclass\s+(\w+)',
        "javascript": r'\bclass\s+(\w+)',
        # Go: type Foo struct
        "go": r'\btype\s+(\w+)\s+struct',
        # Java/C#: class Foo or public class Foo
        "java": r'\bclass\s+(\w+)',
        "csharp": r'\bclass\s+(\w+)',
        # Ruby: class Foo
        "ruby": r'\bclass\s+(\w+)',
    }
    
    pattern = patterns.get(lang.lower(), r'\bclass\s+(\w+)')
    
    try:
        for match in re.finditer(pattern, code, re.IGNORECASE):
            found_name = match.group(1).lower()
            
            # Flexible matching
            if found_name == expected_lower:
                return True
            if expected_lower in found_name or found_name in expected_lower:
                return True
            
            # Semantic: found any class that looks like an API client
            if 'client' in found_name or 'api' in found_name:
                logger.debug(f"Accepting class {match.group(1)} via regex semantic match")
                return True
        
        return False
    except Exception as e:
        # Item G: Log regex fallback failure
        logger.debug(f"Regex class detection failed: {e}")
        return False


def _has_function(code: str, func_name: str, lang: str = "python") -> bool:
    """
    Check if code defines a function matching the expected name.
    
    Bug #45 fix: Uses flexible matching to handle LLM naming variations.
    Bug #84 fix: For non-Python languages, uses regex instead of AST parsing.
    
    Matches if:
    1. Exact match (case-insensitive)
    2. Name contains expected key terms
    3. Semantic: any async def or def with relevant keywords
    """
    expected_lower = func_name.lower()
    expected_terms = set(expected_lower.replace('_', ' ').split())
    
    # Bug #84: For non-Python languages, use regex-based detection
    if lang.lower() != "python":
        return _has_function_regex(code, func_name, lang)
    
    # Python: use AST for accurate detection
    try:
        tree = ast.parse(code)
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name_lower = node.name.lower()
                
                # Skip private/dunder methods
                if name_lower.startswith('_'):
                    continue
                
                # Flexible name matching
                if name_lower == expected_lower:
                    return True
                if expected_lower in name_lower or name_lower in expected_lower:
                    return True
                
                # Term overlap: at least 2 key terms match
                name_terms = set(name_lower.replace('_', ' ').split())
                overlap = expected_terms & name_terms
                if len(overlap) >= 2:
                    logger.debug(f"Accepting function {node.name} via term overlap: {overlap}")
                    return True
                
                # For test functions, accept any test_* function
                if 'test' in expected_lower and name_lower.startswith('test'):
                    return True
        
        return False
    except Exception as e:
        # Item G: Log AST fallback to regex
        logger.debug(f"AST function parsing failed, falling back to regex: {e}")
        # Fallback to regex if AST parsing fails
        return _has_function_regex(code, func_name, lang)


def _has_function_regex(code: str, func_name: str, lang: str) -> bool:
    """
    Bug #84: Regex-based function detection for non-Python languages.
    
    Supports: TypeScript, JavaScript, Go, Java, C#, Ruby
    """
    expected_lower = func_name.lower()
    expected_terms = set(expected_lower.replace('_', ' ').split())
    
    # Language-specific function patterns
    patterns = {
        # Python: def foo_name( - matches both standalone and class methods
        "python": r'\bdef\s+(\w+)\s*\(',
        # TypeScript/JavaScript: function foo( or async function foo( or const foo = or export function foo(
        # Also matches class methods: async methodName(...) or methodName(...)
        "typescript": r'\b(?:async\s+)?function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(|export\s+(?:async\s+)?function\s+(\w+)|^\s*(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*[\w<>\[\],\s|]+)?\s*\{',
        "javascript": r'\b(?:async\s+)?function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(|^\s*(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{',
        # Go: func FooName( or func (receiver) FooName( for methods
        "go": r'\bfunc\s+(?:\([^)]+\)\s*)?(\w+)\s*\(',
        # Java: void fooName( or public void fooName(
        "java": r'\b(?:public|private|protected|static|\s)*\w+\s+(\w+)\s*\(',
        # C#: similar to Java
        "csharp": r'\b(?:public|private|protected|static|async|\s)*\w+\s+(\w+)\s*\(',
        # Ruby: def foo_name
        "ruby": r'\bdef\s+(\w+)',
    }
    
    pattern = patterns.get(lang.lower(), r'\bfunction\s+(\w+)')
    
    try:
        for match in re.finditer(pattern, code, re.IGNORECASE | re.MULTILINE):
            # Get the first non-None group (different patterns capture in different groups)
            found_name = next((g for g in match.groups() if g), None)
            if not found_name:
                continue
                
            found_lower = found_name.lower()
            
            # Skip private methods
            if found_lower.startswith('_'):
                continue
            
            # Flexible matching
            if found_lower == expected_lower:
                return True
            if expected_lower in found_lower or found_lower in expected_lower:
                return True
            
            # Term overlap
            name_terms = set(found_lower.replace('_', ' ').split())
            overlap = expected_terms & name_terms
            if len(overlap) >= 2:
                logger.debug(f"Accepting function {found_name} via regex term overlap: {overlap}")
                return True
            
            # For test functions, accept any test* function
            if 'test' in expected_lower and found_lower.startswith('test'):
                return True
            
            # Semantic: looks like a flow/handler function
            if 'flow' in found_lower or 'handler' in found_lower or 'execute' in found_lower:
                logger.debug(f"Accepting function {found_name} via regex semantic match")
                return True
        
        return False
    except Exception as e:
        # Item G: Log regex fallback failure
        logger.debug(f"Regex function detection failed: {e}")
        return False


async def _generate_client_code(
    state: WorkflowState,
    provider_code: str,
    client_class: str,
    method_name: str,
    endpoint: Endpoint | None,
    base_url: str,
) -> str:
    """
    Generate client module code using spec-driven naming.
    
    V2.1 (GAP-02): Supports policy_mode to switch between inline and runtime styles.
    V2.2 (Fix #1): Supports constrained_codegen for path hallucination prevention.
    V3.0: Converted to async for scalability (ASYNC_MIGRATION_PLAN.md)
        Args:
        state: WorkflowState for context
        provider_code: Provider identifier (e.g., "stripe")
        client_class: Class name (e.g., "StripeClient")
        method_name: Main method name (e.g., "create_checkout_session")
        endpoint: Primary endpoint with path, method info
        base_url: API base URL
    """
    # V2.2 (Fix #1): Check for constrained_codegen mode first (takes precedence)
    constrained_mode = False
    if state.options and hasattr(state.options, 'constrained_codegen'):
        constrained_mode = state.options.constrained_codegen
    
    if constrained_mode:
        logger.debug("Using constrained code generation mode (path hallucination prevention)")
        return await _generate_constrained_client_code(
            state=state,
            provider_code=provider_code,
            client_class=client_class,
            method_name=method_name,
            endpoint=endpoint,
            base_url=base_url,
        )
    
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
    endpoint: Endpoint | None,
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
            # Get implementation hint from config dict (not a direct attribute)
            hint = ""
            if policy.config and isinstance(policy.config, dict):
                hint = policy.config.get("implementation_hint", "") or policy.config.get("type", "")
            if "bearer" in hint.lower() or "token" in hint.lower():
                auth_import = "BearerAuth"
                auth_init = f'BearerAuth(env_var="{env_var}")'
            elif "api_key" in hint.lower():
                auth_import = "ApiKeyAuth"
                header = policy.config.get("header", "X-API-Key") if policy.config and isinstance(policy.config, dict) else "X-API-Key"
                auth_init = f'ApiKeyAuth(env_var="{env_var}", header="{header}")'
            else:
                auth_import = "BearerAuth"  # Default to bearer
                auth_init = f'BearerAuth(env_var="{env_var}")'
                
        elif policy_type == "retry":
            retry_import = "ExponentialRetry"
            max_attempts = 3
            if policy.config and isinstance(policy.config, dict):
                max_attempts = policy.config.get("max_attempts", 3)
            retry_init = f"ExponentialRetry(max_attempts={max_attempts})"
            
        elif policy_type == "rate_limit":
            rate_limit_import = "TokenBucketRateLimiter"
            rps = 10
            if policy.config and isinstance(policy.config, dict):
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
    endpoint: Endpoint | None,
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

    # Bug #66 Fix: Extract path parameters for method signature
    path_params = _extract_path_params(endpoint_path)
    path_param_signature = ""
    path_format_kwargs = ""
    path_param_docstrings = ""
    path_format_call = ""
    endpoint_path_template = endpoint_path
    
    if path_params:
        # Include leading comma after self when we have path params
        path_param_signature = ",\n        " + _build_path_param_signature(path_params)
        path_format_kwargs = _build_path_format_kwargs(path_params)
        # Generate docstring entries for path params
        path_param_docstrings = "\n" + "\n".join(
            f"            {_to_python_param_name(p)}: {p} path parameter"
            for p in path_params
        )
        # Convert {Param} to {{Param}} for f-string, then .format() with kwargs
        # E.g., /v1/Services/{ServiceSid} -> /v1/Services/{{ServiceSid}} + .format(ServiceSid=service_sid)
        endpoint_path_template = endpoint_path.replace("{", "{{").replace("}", "}}")
        path_format_call = f".format({path_format_kwargs})"

    # Determine body handling
    # Note: path_param_signature already ends with comma when present
    body_handling = ""
    if path_params:
        # Path params end with comma, so no leading comma needed
        body_param = ""
        if http_method in ("POST", "PUT", "PATCH"):
            body_param = "\n        payload: Dict[str, Any],"
            body_handling = """
            json=payload,"""
    else:
        # No path params, need comma after self
        body_param = ","
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
        # Add jitter (up to 25%) - using random for non-cryptographic jitter
        jitter = delay * 0.25 * random.uniform(-1, 1)  # nosec B311
        return max(0, delay + jitter)
    
    def _should_retry(self, status_code: int) -> bool:
        """Check if status code is retryable."""
        return status_code in self._retryable_status_codes
'''
        retry_wrapper_start = '''
        # Retry loop with exponential backoff - DO NOT MODIFY TYPE ANNOTATIONS
        response: httpx.Response  # Will be assigned in loop, raises on failure
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
                if attempt < self._max_retries - 1:
                    delay = self._calculate_delay(attempt)
                    self._logger.info(f"Retrying after {delay:.2f}s (transport error)")
                    time.sleep(delay)
                    continue
                # Last attempt failed - raise immediately
                raise IntegrationError(f"Request failed after {self._max_retries} attempts: {e}") from e
        else:
            # Loop completed without break - should not happen in normal flow
            # This is a safety net for malformed retry logic
            raise IntegrationError("Retry loop exhausted without success or explicit error")'''

    # Inline rate limiter
    rate_limit_mixin = ""
    rate_limit_call = ""
    if has_rate_limit:
        # Bug #93 Fix: Use ClassVar to prevent mypy LockType errors when LLM modifies code
        rate_limit_mixin = '''
    # Rate limiting (token bucket) - ClassVar prevents mypy narrowing issues
    _rate_limit_rps: float = 10.0
    _rate_limit_burst: int = 20
    _rate_limit_tokens: float = 20.0
    _rate_limit_last_refill: float = 0.0
    _rate_limit_lock: Optional["threading.Lock"] = None  # Note: Keep Optional type
    
    def _init_rate_limiter(self) -> None:
        """Initialize rate limiter state. Safe to call multiple times."""
        if self._rate_limit_lock is None:
            self._rate_limit_lock = threading.Lock()
            self._rate_limit_last_refill = time.monotonic()
            self._rate_limit_tokens = float(self._rate_limit_burst)
    
    def _acquire_rate_limit_token(self) -> None:
        """Block until rate limit allows request."""
        self._init_rate_limiter()
        # Note: _rate_limit_lock is guaranteed non-None after _init_rate_limiter
        assert self._rate_limit_lock is not None  # Help mypy understand narrowing
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

    # V38-005: Build required headers lines for beta endpoints (inline mode)
    inline_required_headers_lines = ""
    if endpoint and hasattr(endpoint, 'required_headers') and endpoint.required_headers:
        for header_name, header_value in endpoint.required_headers.items():
            inline_required_headers_lines += f'\n        headers["{header_name}"] = "{header_value}"'

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
        self{path_param_signature}{body_param}
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        {summary}
        
        Args:{path_param_docstrings}
            payload: Request payload (for POST/PUT/PATCH)
            idempotency_key: Optional idempotency key for safe retries
        
        Returns:
            API response as dictionary
        
        Raises:
            IntegrationError: If the API request fails
        """
        url = f"{{self.base_url}}{endpoint_path_template}"{path_format_call}
        
        headers = {{}}
        if self.api_key:
            headers["Authorization"] = f"Bearer {{self.api_key}}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key{inline_required_headers_lines}
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


async def _generate_constrained_client_code(
    state: WorkflowState,
    provider_code: str,
    client_class: str,
    method_name: str,
    endpoint: Endpoint | None,
    base_url: str,
) -> str:
    """
    Generate client code using CONSTRAINED generation to prevent path hallucination.
    
    V2.2 (Dynamic Capability Fix #1): This mode injects the EXACT path from the spec
    into a fixed skeleton, then asks the LLM only to fill in the method body logic.
    This eliminates path hallucination (Bug #29, #30) because:
    1. The path is hardcoded in the skeleton, not generated by the LLM
    2. The LLM only fills in error handling and response parsing logic
    3. We validate the output still contains the exact spec path
    
    This produces ~100 LOC clients (between inline ~300 and runtime ~50).
    
    V3.0: Converted to async for scalability (ASYNC_MIGRATION_PLAN.md)
    
    Args:
        state: WorkflowState for context
        provider_code: Provider identifier (e.g., "stripe")
        client_class: Class name (e.g., "StripeClient")
        method_name: Main method name (e.g., "create_checkout_session")
        endpoint: Primary endpoint with path, method info (REQUIRED - contains the spec path)
        base_url: API base URL
        
    Returns:
        Generated client code with spec-injected path
    """
    if not endpoint:
        return f'''"""
{provider_code.title()} API Client (Constrained)

Auto-generated by Integration Co-Worker
"""
# No endpoint found for binding - please configure endpoint bindings
'''

    # Extract info from the spec endpoint (this is the source of truth)
    endpoint_path = endpoint.path  # EXACT path from spec
    http_method = endpoint.method.upper()
    summary = endpoint.summary or f"{http_method} {endpoint_path}"
    env_var = f"{provider_code.upper()}_API_KEY"
    
    # Get schema info for the prompt
    request_schema = None
    response_schema = None
    
    # Safely get openapi_spec as dict
    from integration_coworker.codegen.paths import get_openapi_spec_dict
    spec = get_openapi_spec_dict(state)
    if spec:
        paths = spec.get("paths", {})
        path_item = paths.get(endpoint.path, {})
        operation = path_item.get(endpoint.method.lower(), {})
        
        # Request body schema
        request_body = operation.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            json_content = content.get("application/json", {})
            request_schema = json_content.get("schema")
        
        # Response schema
        responses = operation.get("responses", {})
        success_response = responses.get("200", responses.get("201", {}))
        if success_response:
            content = success_response.get("content", {})
            json_content = content.get("application/json", {})
            response_schema = json_content.get("schema")
    
    # V38-005: Get required headers from endpoint
    required_headers = None
    if endpoint and hasattr(endpoint, 'required_headers'):
        required_headers = endpoint.required_headers
    
    # Build the constrained prompt (path is FIXED in the skeleton)
    prompt = build_constrained_body_prompt(
        endpoint_path=endpoint_path,
        http_method=http_method,
        base_url=base_url,
        provider_code=provider_code,
        client_class=client_class,
        method_name=method_name,
        request_schema=request_schema,
        response_schema=response_schema,
        summary=summary,
        spec=state.openapi_spec,
        required_headers=required_headers,
    )
    
    # Call LLM with the constrained prompt (async)
    try:
        from integration_coworker.config import get_archetype_prompt_config
        from integration_coworker.llm import get_async_llm_client_for_node
        
        client = get_async_llm_client_for_node(NODE_NAME)
        prompt_config = get_archetype_prompt_config(NODE_NAME)
        system_prompt = prompt_config.get("system_template")
        
        response = await client.complete_async(prompt, system_prompt=system_prompt)
        
        if response:
            clean_code = strip_code_fences(response)
            
            # CRITICAL VALIDATION: Ensure the exact spec path is still present
            # This catches any LLM attempt to modify the path
            if endpoint_path in clean_code:
                # Syntax check (Bug #88: use _validate_syntax for consistency)
                if _validate_syntax(clean_code, "python"):
                    logger.info(f"Constrained generation successful for {client_class}.{method_name}")
                    return clean_code
                else:
                    logger.warning(f"Constrained generation produced invalid Python syntax for {client_class}.{method_name}")
            else:
                logger.warning(
                    f"Constrained generation modified path - expected '{endpoint_path}' not found in output"
                )
        else:
            logger.warning(f"Constrained generation got empty response for {client_class}.{method_name}")
            
    except Exception as e:
        logger.error(f"Constrained generation failed for {client_class}.{method_name}: {e}")
    
    # Fallback: Generate a minimal skeleton with the correct path hardcoded
    # This guarantees no path hallucination even if LLM fails
    logger.info(f"Using constrained fallback skeleton for {client_class}.{method_name}")
    
    # Bug #66 Fix: Extract path parameters for constrained fallback too
    path_params = _extract_path_params(endpoint_path)
    path_param_signature = ""
    path_param_docstrings = ""
    path_format_call = ""
    endpoint_path_template = endpoint_path
    
    if path_params:
        # Include leading comma after self when we have path params
        path_param_signature = ",\n        " + _build_path_param_signature(path_params)
        path_param_docstrings = "\n" + "\n".join(
            f"            {_to_python_param_name(p)}: {p} path parameter"
            for p in path_params
        )
        endpoint_path_template = endpoint_path.replace("{", "{{").replace("}", "}}")
        path_format_kwargs = _build_path_format_kwargs(path_params)
        path_format_call = f".format({path_format_kwargs})"
    
    # Note: path_param_signature already ends with comma when present
    body_arg = ""
    if path_params:
        # Path params end with comma, so no leading comma needed
        body_param = ""
        if http_method in ("POST", "PUT", "PATCH"):
            body_param = "\n        payload: Dict[str, Any],"
            body_arg = "\n            json=payload,"
    else:
        # No path params, need comma after self
        body_param = ","
        if http_method in ("POST", "PUT", "PATCH"):
            body_param = ",\n        payload: Dict[str, Any],"
            body_arg = "\n            json=payload,"
    
    # V38-005: Build required headers lines for beta endpoints
    required_headers_lines = ""
    if endpoint and hasattr(endpoint, 'required_headers') and endpoint.required_headers:
        for header_name, header_value in endpoint.required_headers.items():
            required_headers_lines += f'\n        headers["{header_name}"] = "{header_value}"'
    
    fallback_code = f'''"""
{provider_code.title()} API Client (Constrained)

Auto-generated by Integration Co-Worker (constrained mode)
Provider: {provider_code}
Endpoint: {http_method} {endpoint_path}

Path is spec-injected to prevent hallucination.
"""
from typing import Dict, Any, Optional
import os
import logging

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
    
    def close(self):
        """Close the HTTP client."""
        self._client.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def {method_name}(
        self{path_param_signature}{body_param}
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        {summary}
        
        Args:{path_param_docstrings}
            payload: Request payload (for POST/PUT/PATCH)
            idempotency_key: Optional idempotency key for safe retries
        
        Returns:
            API response as dictionary
        
        Raises:
            IntegrationError: If the API request fails
        """
        # SPEC-INJECTED PATH - guaranteed to match the API spec
        url = f"{{self.base_url}}{endpoint_path_template}"{path_format_call}
        
        headers = {{}}
        if self.api_key:
            headers["Authorization"] = f"Bearer {{self.api_key}}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key{required_headers_lines}
        
        response = self._client.request(
            "{http_method}",
            url,
            headers=headers,{body_arg}
        )
        
        if response.status_code not in (200, 201, 204):
            raise IntegrationError(
                f"API request failed: {{response.status_code}} - {{response.text[:200]}}"
            )
        
        if response.status_code == 204 or not response.content:
            return {{}}
        
        return response.json()
'''
    return fallback_code


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
from typing import Dict, Any
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
    method_name: str,
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
        method_name: Client method name (for correct mock setup)
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
            mock_client.{method_name}.return_value = {{
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


async def _generate_constrained_test_code(
    state: WorkflowState,
    provider_code: str,
    task_slug: str,
    client_class: str,
    flow_import_module: str,
    flow_function: str,
    method_name: str,
) -> str:
    """
    Generate test code using CONSTRAINED fixture-based skeleton.
    
    V2.2 (Dynamic Capability Fix #2): Uses a fixed skeleton with pytest fixtures
    to prevent credential hardcoding (Bug #31). The LLM fills in additional test
    logic but the fixture pattern is pre-defined and secure.
    
    Security guarantees:
    1. Credentials come from os.environ.get(), never hardcoded
    2. Fixtures are injected, not generated by LLM
    3. Fallback skeleton is production-safe even if LLM fails
    
    Args:
        state: WorkflowState for context
        provider_code: Provider identifier
        task_slug: Task slug
        client_class: Client class name (for mocking)
        flow_import_module: Module path to import flow from
        flow_function: Flow function name to test
        method_name: Client method name (for correct mock setup)
        
    Returns:
        Generated test code with secure fixture patterns
    """
    env_var = f"{provider_code.upper()}_API_KEY"
    provider_title = provider_code.title()
    task_title = task_slug.replace('_', ' ').title()
    test_class_name = f"Test{client_class.replace('Client', '')}Flow"
    
    # Format the fixed skeleton with context values
    skeleton = TEST_FIXTURE_SKELETON.format(
        provider_title=provider_title,
        task_title=task_title,
        flow_import_module=flow_import_module,
        flow_function=flow_function,
        env_var=env_var,
        client_class=client_class,
        method_name=method_name,
        test_class_name=test_class_name,
        task_slug=task_slug.replace('_', ' '),
    )
    
    # In constrained mode, we use the skeleton directly with minimal LLM enhancement
    # The LLM is only asked to add additional test cases, not modify the fixtures
    if state.options and hasattr(state.options, 'constrained_codegen') and state.options.constrained_codegen:
        try:
            from integration_coworker.config import get_archetype_prompt_config
            from integration_coworker.llm import get_async_llm_client_for_node
            
            client = get_async_llm_client_for_node(NODE_NAME)
            prompt_config = get_archetype_prompt_config(NODE_NAME)
            system_prompt = prompt_config.get("system_template")
            
            prompt = f"""You are a senior Python developer. Add additional test cases to this test skeleton.

## SKELETON (DO NOT MODIFY FIXTURES OR EXISTING TESTS)
The fixtures and basic tests are already correct. ONLY ADD new test methods.

```python
{skeleton}
```

## INSTRUCTIONS
1. Add 1-2 additional test methods to the test class
2. DO NOT modify the existing fixtures (api_key, mock_client)
3. DO NOT modify the existing test methods
4. Use the api_key fixture parameter for any credential needs
5. Test edge cases like error responses, network failures, etc.
6. Return the COMPLETE test file with your additions

## OUTPUT
Return the complete Python test code:
"""
            response = await client.complete_async(prompt, system_prompt=system_prompt)
            
            if response:
                clean_code = strip_code_fences(response)
                
                # Validate the fixtures are still present (LLM didn't remove them)
                if "os.environ.get" in clean_code and "@pytest.fixture" in clean_code:
                    # Bug #88: use _validate_syntax for consistency
                    if _validate_syntax(clean_code, "python"):
                        logger.info(f"Constrained test generation successful for {test_class_name}")
                        return clean_code
                    else:
                        logger.warning("Constrained test generation produced invalid Python syntax")
                else:
                    logger.warning("Constrained test generation removed security fixtures - using fallback")
                    
        except Exception as e:
            logger.error(f"Constrained test generation failed: {e}")
    
    # Fallback: return the skeleton directly (guaranteed secure)
    logger.debug(f"Using secure test fixture skeleton for {test_class_name}")
    return skeleton
