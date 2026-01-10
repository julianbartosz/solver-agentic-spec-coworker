"""
Feedback hooks for automatic learning during workflow execution.

These hooks wrap the implicit signal recording functions with error isolation
to ensure feedback recording failures never break the main workflow.

Usage:
    from integration_coworker.feedback.hooks import (
        safe_record_compile_result,
        safe_record_syntax_check,
        safe_record_validation_result,
    )
    
    # In workflow nodes - these never raise, always return success/failure
    safe_record_compile_result(run_id, template_key, success=True)
"""
import logging
import threading
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from integration_coworker.feedback.implicit_signals import (
    record_compile_result,
    record_test_result,
    record_lint_result,
    record_validation_result,
)

logger = logging.getLogger(__name__)

# Type variable for generic wrapper
F = TypeVar('F', bound=Callable[..., Any])


# =============================================================================
# Metrics: Feedback Recording Failure Counter (B-008)
# =============================================================================
# Tracks when feedback recording fails. These failures are non-fatal but
# indicate issues with the feedback pipeline that should be investigated.
# =============================================================================

_feedback_failure_count = 0
_feedback_failure_lock = threading.Lock()


def _increment_feedback_failure_count() -> None:
    """Increment the feedback failure counter (thread-safe)."""
    global _feedback_failure_count
    with _feedback_failure_lock:
        _feedback_failure_count += 1


def get_feedback_failure_count() -> int:
    """Get the current feedback failure count."""
    with _feedback_failure_lock:
        return _feedback_failure_count


def reset_feedback_failure_count() -> None:
    """Reset the feedback failure count (for testing)."""
    global _feedback_failure_count
    with _feedback_failure_lock:
        _feedback_failure_count = 0


def _safe_feedback_wrapper(func: F) -> F:
    """
    Decorator that wraps feedback recording functions with error isolation.
    
    Ensures that:
    1. Exceptions in feedback recording never propagate to caller
    2. Errors are logged but don't break workflow execution
    3. Returns True on success, False on failure
    4. B-008: Increments failure counter for observability
    """
    @wraps(func)
    def wrapper(*args, **kwargs) -> bool:
        try:
            func(*args, **kwargs)
            return True
        except Exception as e:
            # B-008: Track feedback failures for metrics
            _increment_feedback_failure_count()
            
            # Log but don't propagate - feedback failures should never break workflows
            logger.warning(
                f"Feedback recording failed (non-fatal): {func.__name__}: {e}",
                exc_info=False,  # Don't log full traceback for expected failures
            )
            return False
    return wrapper  # type: ignore


# =============================================================================
# Safe wrappers for implicit signal recording
# =============================================================================

@_safe_feedback_wrapper
def safe_record_compile_result(
    run_id: str,
    template_key: str,
    success: bool,
    error_message: Optional[str] = None,
) -> None:
    """
    Safely record a compile/syntax check result.
    
    This is a wrapper around record_compile_result that:
    - Never raises exceptions
    - Logs any errors but continues execution
    - Returns True if recording succeeded, False otherwise
    
    Args:
        run_id: The workflow run ID
        template_key: The template/workflow key being executed
        success: Whether compilation succeeded
        error_message: Error message if compilation failed (logged but not persisted)
    """
    # Note: error_message is accepted for logging purposes but not passed to
    # record_compile_result which only stores success as a boolean score
    if error_message:
        logger.debug(f"Compile error for {template_key}: {error_message}")
    record_compile_result(
        run_id=run_id,
        template_key=template_key,
        success=success,
    )


@_safe_feedback_wrapper
def safe_record_syntax_check(
    run_id: str,
    template_key: str,
    artifact_type: str,
    success: bool,
    error_message: Optional[str] = None,
) -> None:
    """
    Safely record a syntax validation result for a specific artifact.
    
    This records the result of AST parsing for generated code artifacts.
    Uses compile_result feedback type internally.
    
    Args:
        run_id: The workflow run ID
        template_key: The template/workflow key
        artifact_type: Type of artifact ("client", "flow", "test")
        success: Whether syntax check passed
        error_message: Syntax error message if failed (logged but not persisted)
    """
    # Use a qualified template key for artifact-level granularity
    qualified_key = f"{template_key}:{artifact_type}"
    # Note: error_message is accepted for logging purposes but not passed to
    # record_compile_result which only stores success as a boolean score
    if error_message:
        logger.debug(f"Syntax error for {qualified_key}: {error_message}")
    record_compile_result(
        run_id=run_id,
        template_key=qualified_key,
        success=success,
    )


@_safe_feedback_wrapper
def safe_record_test_result(
    run_id: str,
    template_key: str,
    passed: int,
    total: int,
    failures: Optional[list[str]] = None,
) -> None:
    """
    Safely record test execution results.
    
    Args:
        run_id: The workflow run ID
        template_key: The template/workflow key
        passed: Number of tests that passed
        total: Total number of tests
        failures: List of failure messages (logged but not persisted)
    """
    # Note: failures list is accepted for logging but not passed to underlying func
    if failures:
        logger.debug(f"Test failures for {template_key}: {failures}")
    failed = total - passed
    record_test_result(
        run_id=run_id,
        passed=passed,
        failed=failed,
        template_key=template_key,
    )


@_safe_feedback_wrapper
def safe_record_lint_result(
    run_id: str,
    template_key: str,
    success: bool,
    warnings: Optional[list[str]] = None,
    errors: Optional[list[str]] = None,
) -> None:
    """
    Safely record linting/style check results.
    
    Args:
        run_id: The workflow run ID
        template_key: The template/workflow key
        success: Whether lint check passed (no errors)
        warnings: List of warning messages
        errors: List of error messages
    """
    # Convert success/errors/warnings to violations count
    # Assume a default of 100 lines if we don't know actual lines
    violations = len(errors or []) + len(warnings or []) if not success else 0
    total_lines = 100  # Default estimate for scoring purposes
    record_lint_result(
        run_id=run_id,
        violations=violations,
        total_lines=total_lines,
        template_key=template_key,
    )


@_safe_feedback_wrapper
def safe_record_validation_result(
    run_id: str,
    template_key: str,
    success: bool,
    validation_errors: Optional[list[str]] = None,
) -> None:
    """
    Safely record workflow validation results.
    
    Records the outcome of validate_integration_design node.
    
    Args:
        run_id: The workflow run ID
        template_key: The template/workflow key
        success: Whether validation passed
        validation_errors: List of validation error messages
    """
    # Convert success/errors to has_errors/error_count format
    error_count = len(validation_errors) if validation_errors else 0
    has_errors = not success
    record_validation_result(
        run_id=run_id,
        has_errors=has_errors,
        error_count=error_count,
        warning_count=0,
        template_key=template_key,
    )


@_safe_feedback_wrapper
def safe_record_security_check(
    run_id: str,
    template_key: str,
    success: bool,
    violations: Optional[list[str]] = None,
) -> None:
    """
    Safely record security validation results.
    
    Records the outcome of code security checks (AST-based validation).
    Uses validation result internally since it's a type of validation.
    
    Args:
        run_id: The workflow run ID
        template_key: The template/workflow key
        success: Whether security check passed
        violations: List of security violation descriptions
    """
    # Convert success/violations to has_errors/error_count format
    error_count = len(violations) if violations else 0
    has_errors = not success
    record_validation_result(
        run_id=run_id,
        has_errors=has_errors,
        error_count=error_count,
        warning_count=0,
        template_key=template_key,
    )


# =============================================================================
# Hook registry for enabling/disabling feedback collection
# =============================================================================

_HOOKS_ENABLED = True


def enable_feedback_hooks():
    """Enable automatic feedback recording hooks."""
    global _HOOKS_ENABLED
    _HOOKS_ENABLED = True
    logger.info("Feedback hooks enabled")


def disable_feedback_hooks():
    """Disable automatic feedback recording hooks (useful for testing)."""
    global _HOOKS_ENABLED
    _HOOKS_ENABLED = False
    logger.info("Feedback hooks disabled")


def are_hooks_enabled() -> bool:
    """Check if feedback hooks are currently enabled."""
    return _HOOKS_ENABLED


def conditional_record(record_func: Callable[..., bool], *args, **kwargs) -> bool:
    """
    Conditionally execute a feedback recording function based on hook state.
    
    This allows nodes to call feedback functions that only execute if hooks
    are enabled, without checking the flag everywhere.
    
    Args:
        record_func: The safe_record_* function to call
        *args, **kwargs: Arguments to pass to the function
        
    Returns:
        True if recording was attempted and succeeded (or hooks disabled)
        False if recording was attempted and failed
    """
    if not _HOOKS_ENABLED:
        return True  # Skip silently when disabled
    return record_func(*args, **kwargs)
