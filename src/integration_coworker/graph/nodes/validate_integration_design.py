import ast
import logging
from typing import Optional
from integration_coworker.graph.state import WorkflowState
from integration_coworker.feedback.hooks import (
    safe_record_validation_result,
    safe_record_syntax_check,
    are_hooks_enabled,
)

logger = logging.getLogger(__name__)


def _validate_python_syntax(code: str, filepath: str) -> list[str]:
    """
    Validate Python code syntax using AST.
    
    Returns list of error messages (empty if valid).
    """
    errors = []
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


def _get_template_key(state: WorkflowState) -> str:
    """Get the template key from state for feedback recording."""
    if state.workflow_template and state.workflow_template.key:
        return state.workflow_template.key
    # Fallback to constructed key from provider + task
    provider = state.provider_code or "unknown"
    task_slug = state.integration_task.task_slug if state.integration_task else "task"
    return f"workflow.{provider}.{task_slug}"


def _validate_code_artifacts(state: WorkflowState) -> list[str]:
    """
    Validate generated code artifacts for syntax errors.
    
    M5 WS3-T2: AST parse generated code before returning.
    
    Also records feedback for each artifact's syntax check result
    to update KG confidence scores.
    """
    errors = []
    run_id = state.run_id or "unknown"
    template_key = _get_template_key(state)

    for artifact in state.code_artifacts:
        if artifact.language == "python":
            syntax_errors = _validate_python_syntax(
                artifact.content,
                artifact.rel_path
            )
            errors.extend(syntax_errors)
            
            # Record syntax check result for feedback learning
            if are_hooks_enabled():
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
