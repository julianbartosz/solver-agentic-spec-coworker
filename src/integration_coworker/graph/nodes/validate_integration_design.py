import ast
import logging
from typing import Optional
from integration_coworker.graph.state import WorkflowState
from integration_coworker.feedback.hooks import (
    safe_record_validation_result,
    safe_record_syntax_check,
    are_hooks_enabled,
)
from integration_coworker.codegen.syntax_validator import (
    validate_syntax as tree_sitter_validate_syntax,
    is_tree_sitter_available,
)
from integration_coworker.runtime.test_execution import (
    is_test_execution_enabled,
    execute_tests,
    generate_vscode_test_task,
    TestStatus,
)

logger = logging.getLogger(__name__)


def _validate_python_syntax(code: str, filepath: str) -> list[str]:
    """
    Validate Python code syntax using AST.
    
    Deprecated: Use _validate_syntax(code, filepath, "python") for multi-language support.
    Kept for backward compatibility.
    
    Returns list of error messages (empty if valid).
    """
    return _validate_syntax(code, filepath, "python")


def _validate_syntax(code: str, filepath: str, language: str = "python") -> list[str]:
    """
    Validate code syntax for the specified language.
    
    Bug #88 Fix: Uses tree-sitter for accurate syntax validation across all
    supported languages when available. Falls back to Python AST for Python
    code, or skips validation with warning for other languages.
    
    Supported languages (with tree-sitter):
    - Python, TypeScript, JavaScript, Go, Java, Ruby, C#
    
    Args:
        code: Source code to validate
        filepath: File path for error messages
        language: Programming language (default: "python")
        
    Returns:
        List of error messages (empty if valid)
    """
    errors = []
    lang_lower = language.lower()
    
    # Use tree-sitter if available (preferred for all languages)
    if is_tree_sitter_available():
        result = tree_sitter_validate_syntax(code, lang_lower)
        if not result.is_valid:
            error_msg = f"Syntax error in {filepath}"
            if result.error_line:
                error_msg += f" at line {result.error_line}"
            if result.error_message:
                error_msg += f": {result.error_message}"
            errors.append(error_msg)
        return errors
    
    # Fallback: Python AST for Python code
    if lang_lower in ("python", "py"):
        try:
            ast.parse(code)
        except SyntaxError as e:
            errors.append(
                f"Syntax error in {filepath} at line {e.lineno}: {e.msg}"
            )
        except Exception as e:
            errors.append(
                f"Failed to parse {filepath}: {str(e)}"
            )
        return errors
    
    # Fallback: No validation for non-Python without tree-sitter
    # Log debug message but don't treat as error
    logger.debug(
        f"Skipping syntax validation for {filepath} ({language}) - "
        f"tree-sitter not installed. Install with: pip install 'solver-agentic-spec-coworker[validation]'"
    )
    return errors


def _get_template_key(state: WorkflowState) -> str:
    """Get the template key from state for feedback recording."""
    # Bug #59 fix: WorkflowTemplate uses 'code' not 'key'
    if state.workflow_template and state.workflow_template.code:
        return state.workflow_template.code
    # Fallback to constructed key from provider + task
    provider = state.provider_code or "unknown"
    task_slug = state.integration_task.task_slug if state.integration_task else "task"
    return f"workflow.{provider}.{task_slug}"


def _validate_code_artifacts(state: WorkflowState) -> list[str]:
    """
    Validate generated code artifacts for syntax errors.
    
    M5 WS3-T2: AST parse generated code before returning.
    Bug #88: Validates all supported languages using tree-sitter when available,
    not just Python.
    
    Also records feedback for each artifact's syntax check result
    to update KG confidence scores.
    """
    errors = []
    run_id = state.run_id or "unknown"
    template_key = _get_template_key(state)
    
    # Bug #88: Supported languages for validation
    supported_languages = {"python", "py", "typescript", "ts", "javascript", "js", 
                          "go", "java", "ruby", "csharp", "c#", "cs"}

    for artifact in state.code_artifacts:
        lang = (artifact.language or "").lower()
        
        # Skip validation for unsupported languages
        if lang not in supported_languages:
            logger.debug(f"Skipping syntax validation for {artifact.rel_path} (unsupported language: {lang or 'unknown'})")
            continue
        
        # Bug #88: Use language-aware validation
        syntax_errors = _validate_syntax(
            artifact.content,
            artifact.rel_path,
            lang
        )
        errors.extend(syntax_errors)
        
        # Record syntax check result for feedback learning (Python only for now)
        if are_hooks_enabled() and lang in ("python", "py"):
            if syntax_errors:
                safe_record_syntax_check(
                    run_id=run_id,
                    template_key=template_key,
                    artifact_type=artifact.artifact_type,
                    success=False,
                    error_message="; ".join(syntax_errors),
                )
            else:
                safe_record_syntax_check(
                    run_id=run_id,
                    template_key=template_key,
                    artifact_type=artifact.artifact_type,
                    success=True,
                )

    if errors:
        logger.warning(f"Found {len(errors)} syntax error(s) in generated code")
    else:
        logger.debug(f"All {len(state.code_artifacts)} code artifacts passed syntax validation")

    return errors


def _execute_generated_tests(state: WorkflowState):
    """
    Execute generated tests in a sandboxed environment.
    
    This is opt-in functionality controlled by ENABLE_TEST_EXECUTION env var.
    
    Args:
        state: WorkflowState with code_artifacts
        
    Returns:
        TestExecutionResult or None if no tests to run
    """
    if not state.code_artifacts:
        return None
    
    # Determine language from artifacts
    language = "python"  # Default
    for artifact in state.code_artifacts:
        if artifact.language:
            language = artifact.language.lower()
            break
    
    # Execute tests
    from integration_coworker.runtime.test_execution import (
        execute_tests,
        TestExecutionResult,
    )
    
    result = execute_tests(
        artifacts=state.code_artifacts,
        language=language,
    )
    
    # Log result
    if result.status == TestStatus.PASSED:
        logger.info(f"Tests passed: {result.passed}/{result.total_tests}")
    elif result.status == TestStatus.FAILED:
        logger.warning(f"Tests failed: {result.failed}/{result.total_tests}")
    elif result.status == TestStatus.NOT_EXECUTED:
        logger.debug("Test execution disabled")
    elif result.status == TestStatus.SKIPPED:
        logger.debug("No test artifacts found")
    else:
        logger.warning(f"Test execution error: {result.error_message}")
    
    return result


def validate_integration_design(state: WorkflowState) -> WorkflowState:
    """
    Reads: integration_task, workflow_nodes, workflow_edges, endpoint_bindings, code_artifacts, errors
    Writes: validation results (adds to errors if issues found)
    
    Contract per Appendix C.3.14:
    - Enforces flow structure semantics (start/end, connectivity, positions)
    - Validates endpoint binding consistency
    - Raises on critical failures
    """
    validation_errors = []

    # Check that we have core components
    if not state.integration_task:
        validation_errors.append("Validation failed: No integration_task defined")

    if not state.workflow_nodes:
        validation_errors.append("Validation failed: No workflow_nodes defined")

    if not state.code_artifacts:
        # Warning only for M3
        validation_errors.append("Validation warning: No code_artifacts generated")

    # Check that endpoints were found
    if not state.endpoints:
        validation_errors.append("Validation warning: No endpoints extracted from spec")

    # 1. Validate flow structure per Appendix H.5
    if state.workflow_nodes:
        start_nodes = [n for n in state.workflow_nodes if n.node_type == "start"]
        end_nodes = [n for n in state.workflow_nodes if n.node_type == "end"]

        if len(start_nodes) != 1:
            validation_errors.append(f"Flow must have exactly one start node, found {len(start_nodes)}")

        if len(end_nodes) < 1:
            validation_errors.append("Flow must have at least one end node, found 0")

        # Check connectivity
        edge_map = {}
        reverse_edge_map = {}

        for edge in state.workflow_edges:
            edge_map.setdefault(edge.from_node_key, []).append(edge.to_node_key)
            reverse_edge_map.setdefault(edge.to_node_key, []).append(edge.from_node_key)

        # All non-start nodes must have incoming edges
        for node in state.workflow_nodes:
            if node.node_type != "start" and node.node_key not in reverse_edge_map:
                validation_errors.append(f"Node '{node.node_key}' has no incoming edges")

        # All non-end nodes must have outgoing edges
        for node in state.workflow_nodes:
            if node.node_type != "end" and node.node_key not in edge_map:
                validation_errors.append(f"Node '{node.node_key}' has no outgoing edges")

        # Check position strictly increases
        node_positions = {n.node_key: n.position for n in state.workflow_nodes}
        for edge in state.workflow_edges:
            from_pos = node_positions.get(edge.from_node_key, -1)
            to_pos = node_positions.get(edge.to_node_key, -1)
            if from_pos >= to_pos:
                validation_errors.append(
                    f"Position must increase along edges: {edge.from_node_key}(pos={from_pos}) -> {edge.to_node_key}(pos={to_pos})"
                )

    # 2. Validate endpoint bindings
    api_call_nodes = [n for n in state.workflow_nodes if n.node_type == "api_call"]
    binding_keys = {b.flow_node_key for b in state.endpoint_bindings}

    for api_node in api_call_nodes:
        if api_node.node_key not in binding_keys:
            validation_errors.append(
                f"API call node '{api_node.node_key}' has no matching EndpointBinding"
            )
        else:
            # Check for endpoint_id
            matching_bindings = [b for b in state.endpoint_bindings if b.flow_node_key == api_node.node_key]
            for binding in matching_bindings:
                if binding.endpoint_id is None:
                    # Warning only for M3
                    validation_errors.append(
                        f"Warning: EndpointBinding for node '{api_node.node_key}' has endpoint_id=None"
                    )

    # Add all validation errors to state
    state.errors.extend(validation_errors)

    # 3. Validate generated code syntax (M5 WS3-T2)
    if state.code_artifacts:
        code_errors = _validate_code_artifacts(state)
        state.errors.extend(code_errors)

    # 4. Execute tests (opt-in, M5+)
    if state.code_artifacts and is_test_execution_enabled():
        test_result = _execute_generated_tests(state)
        if test_result:
            # Add test execution info to plan for reporting
            if state.plan is not None:
                state.plan["test_execution_result"] = {
                    "status": test_result.status.value,
                    "passed": test_result.passed,
                    "failed": test_result.failed,
                    "errors": test_result.errors,
                    "output": test_result.output[:1000] if test_result.output else "",
                }
            
            if test_result.status == TestStatus.FAILED:
                state.errors.append(
                    f"Test execution failed: {test_result.error_message or 'See test output'}"
                )
            elif test_result.status == TestStatus.ERROR:
                # Log but don't fail validation on test execution errors
                logger.warning(f"Test execution error: {test_result.error_message}")

    # For M3: don't raise on warnings, only on critical structural failures
    critical_failures = [e for e in validation_errors if "Warning" not in e and "warning" not in e]
    
    # Record validation result for feedback learning
    if are_hooks_enabled():
        run_id = state.run_id or "unknown"
        template_key = _get_template_key(state)
        safe_record_validation_result(
            run_id=run_id,
            template_key=template_key,
            success=len(critical_failures) == 0,
            validation_errors=critical_failures if critical_failures else None,
        )
    
    if critical_failures:
        error_msg = f"Validation failed with {len(critical_failures)} critical error(s)"
        raise ValueError(error_msg)

    state.completed_steps.append("validate_integration_design")
    return state
