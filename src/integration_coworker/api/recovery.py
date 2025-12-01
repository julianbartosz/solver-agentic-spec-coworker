"""
Recovery helpers for integration workflow failures.

Provides thin wrappers around re-running the workflow with different
strategies:
- retry: Re-run with the same inputs
- skip: (Future) Skip the failing step and continue
- restart: Clear state and start fresh

Per UX plan Step 5.3.1, these are simple helpers that wrap
design_and_generate_integration with appropriate options.
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

from integration_coworker.api.types import IntegrationOptions, IntegrationResult


@dataclass
class RecoveryContext:
    """
    Captured context from a failed run for recovery attempts.
    
    Stores the original inputs so they can be reused in retry/restart.
    """
    spec_refs: List[str]
    task_description: str
    provider_code: Optional[str] = None
    repo_root: Optional[str] = None
    dry_run: bool = True
    last_error: Optional[str] = None
    failed_step: Optional[str] = None


def retry_from_last_failure(context: RecoveryContext) -> IntegrationResult:
    """
    Retry an integration run with the same inputs as the failed run.
    
    This is the simplest recovery strategy - just run again with
    identical parameters. Useful for transient failures (network,
    rate limits, etc.).
    
    Args:
        context: Recovery context from the failed run
        
    Returns:
        IntegrationResult from the retry attempt
        
    Raises:
        Exception: If the retry also fails
    """
    from integration_coworker.api.entrypoint import design_and_generate_integration
    
    options = IntegrationOptions(
        dry_run=context.dry_run,
        repo_integration_enabled=context.repo_root is not None,
        override_provider_code=context.provider_code,
    )
    
    return design_and_generate_integration(
        spec_refs=context.spec_refs,
        task_description=context.task_description,
        provider_code=context.provider_code,
        repo_root=context.repo_root,
        options=options,
    )


def skip_failing_step(context: RecoveryContext) -> IntegrationResult:
    """
    Skip the failing step and continue with remaining workflow.
    
    Note: This is a placeholder for future implementation.
    Full skip support requires:
    - Workflow checkpointing (save state at each node)
    - Step-level error boundaries
    - Conditional edge routing based on skip markers
    
    For V1, this falls back to a simple retry.
    
    Args:
        context: Recovery context with failed_step information
        
    Returns:
        IntegrationResult (currently same as retry)
    """
    # TODO: Implement proper skip logic with workflow checkpointing
    # For now, this is equivalent to retry
    return retry_from_last_failure(context)


def restart_fresh() -> None:
    """
    Clear all state and prepare for a fresh run.
    
    This is a no-op for the backend - the caller (UI) is responsible
    for clearing session state. This function is provided for API
    consistency and future state cleanup needs.
    """
    # No backend state to clear in V1
    # UI handles session state clearing
    pass


def create_recovery_context(
    inputs: Dict[str, Any],
    error: Optional[str] = None,
    failed_step: Optional[str] = None,
) -> RecoveryContext:
    """
    Create a RecoveryContext from captured run inputs.
    
    Args:
        inputs: Dictionary of original run inputs
        error: The error message from the failure
        failed_step: The step/node that failed (if known)
        
    Returns:
        RecoveryContext ready for recovery attempts
    """
    return RecoveryContext(
        spec_refs=inputs.get("spec_refs", []),
        task_description=inputs.get("task_description", ""),
        provider_code=inputs.get("provider_code"),
        repo_root=inputs.get("repo_root"),
        dry_run=inputs.get("dry_run", True),
        last_error=error,
        failed_step=failed_step,
    )
