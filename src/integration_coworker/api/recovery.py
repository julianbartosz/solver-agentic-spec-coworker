"""
Recovery helpers for integration workflow failures.

V2 Implementation (per V2 Implementation Plan Section 3.5):
- Checkpoint-based recovery with database persistence
- Resume capability from any checkpointed node
- True skip with dependency analysis

Provides recovery strategies:
- retry: Re-run with the same inputs
- resume: Resume from last checkpoint
- skip: Skip the failing step and continue
- restart: Clear state and start fresh
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
import logging

from integration_coworker.api.types import IntegrationOptions, IntegrationResult

logger = logging.getLogger(__name__)


@dataclass
class RecoveryContext:
    """
    Captured context from a failed run for recovery attempts.
    
    V2: Added run_id for checkpoint-based recovery.
    """
    run_id: str
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


def resume_run(run_id: str) -> IntegrationResult:
    """
    Resume a run from its last checkpoint.
    
    V2 Implementation:
    1. Load the last checkpoint state
    2. Determine which nodes are remaining
    3. Re-execute from that point
    
    Args:
        run_id: The run_id to resume
        
    Returns:
        IntegrationResult from the resumed run
        
    Raises:
        ValueError: If no checkpoint found for run_id
    """
    from integration_coworker.graph.runtime import get_node_names, run_from_node
    from integration_coworker.persistence.checkpoints import (
        load_checkpoint,
        get_completed_nodes,
        delete_checkpoints,
    )
    
    # Load last checkpoint
    state = load_checkpoint(run_id)
    if not state:
        raise ValueError(f"No checkpoint found for run_id: {run_id}")
    
    # Get completed nodes
    completed = set(get_completed_nodes(run_id))
    
    # Determine next node
    all_nodes = get_node_names()
    
    next_node = None
    for node in all_nodes:
        if node not in completed:
            next_node = node
            break
    
    if not next_node:
        # All nodes completed, just return final state
        return _state_to_result(state)
    
    # Resume from next node
    logger.info(f"Resuming run {run_id} from node {next_node}")
    final_state = run_from_node(state, next_node)
    
    # Clean up checkpoints on success
    if not final_state.errors:
        delete_checkpoints(run_id)
    
    return _state_to_result(final_state)


def skip_failing_step(context: RecoveryContext) -> IntegrationResult:
    """
    Skip the failing step and all dependent steps, then continue.
    
    V2.1 Implementation (Section 13.2 per ADR-0009):
    1. Load checkpoint at failed node
    2. Compute skip cascade (failed node + all dependents)
    3. Mark all skipped nodes in state.skipped_nodes
    4. Continue from first non-skipped node
    
    This ensures that if we skip a node, all nodes that depend on its
    output are also skipped (avoiding failures from missing data).
    
    Requires: failed_step to be set in context
    
    Args:
        context: Recovery context with failed_step information
        
    Returns:
        IntegrationResult from the resumed run
    """
    if not context.failed_step:
        raise ValueError("failed_step required for skip recovery")
    
    from integration_coworker.graph.runtime import (
        get_node_names,
        run_from_node,
        get_skip_cascade,
    )
    from integration_coworker.persistence.checkpoints import load_checkpoint
    
    # Load checkpoint before failed step
    state = load_checkpoint(context.run_id)
    if not state:
        # No checkpoint, fall back to retry
        logger.warning("No checkpoint for skip; falling back to retry")
        return retry_from_last_failure(context)
    
    # V2.1: Compute the full skip cascade (failed node + all dependents)
    skip_cascade = get_skip_cascade(context.failed_step)
    logger.info(f"Skip cascade for {context.failed_step}: {skip_cascade}")
    
    # Mark all nodes in cascade as skipped
    if not hasattr(state, 'skipped_nodes'):
        state.skipped_nodes = []
    state.skipped_nodes.extend(skip_cascade)
    
    # Also mark as completed (so workflow doesn't try to run them)
    for node in skip_cascade:
        if node not in state.completed_steps:
            state.completed_steps.append(node)
            state.warnings.append(f"Skipped step (dependency cascade): {node}")
    
    # Determine next node after skip cascade
    all_nodes = get_node_names()
    skip_set = set(skip_cascade)
    completed_set = set(state.completed_steps)
    
    next_node = None
    for node in all_nodes:
        if node not in skip_set and node not in completed_set:
            next_node = node
            break
    
    if not next_node:
        # All remaining nodes are skipped or completed
        logger.info("No more nodes to run after skip cascade")
        return _state_to_result(state)
    
    # Continue from next node
    logger.info(f"Skipped {len(skip_cascade)} nodes, resuming from {next_node}")
    final_state = run_from_node(state, next_node)
    return _state_to_result(final_state)


def _state_to_result(state) -> IntegrationResult:
    """Convert final WorkflowState to IntegrationResult."""
    return IntegrationResult(
        run_id=state.run_id or "",
        task=state.integration_task,
        code_artifacts=state.code_artifacts or [],
        repo_changes=state.repo_changes,
        report_markdown=state.report_markdown or "",
        persisted_ids=state.persisted_ids,
        endpoints=state.endpoints or [],
        schemas=state.schemas or [],
        entities=state.entities or [],
        workflow_nodes=state.workflow_nodes or [],
        workflow_edges=state.workflow_edges or [],
        endpoint_bindings=state.endpoint_bindings or [],
        policies=state.policies or [],  # V2.1: Expose inferred policies
        spec_documents=state.spec_documents or [],
        doc_chunks=state.doc_chunks or [],
        plan=state.plan or {},
        errors=state.errors or [],
        completed_steps=state.completed_steps or [],
        provider_code=state.provider_code,
    )


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
    run_id: Optional[str] = None,
) -> RecoveryContext:
    """
    Create a RecoveryContext from captured run inputs.
    
    V2: Added run_id parameter for checkpoint-based recovery.
    
    Args:
        inputs: Dictionary of original run inputs
        error: The error message from the failure
        failed_step: The step/node that failed (if known)
        run_id: The run_id for checkpoint lookup
        
    Returns:
        RecoveryContext ready for recovery attempts
    """
    return RecoveryContext(
        run_id=run_id or inputs.get("run_id", ""),
        spec_refs=inputs.get("spec_refs", []),
        task_description=inputs.get("task_description", ""),
        provider_code=inputs.get("provider_code"),
        repo_root=inputs.get("repo_root"),
        dry_run=inputs.get("dry_run", True),
        last_error=error,
        failed_step=failed_step,
    )

