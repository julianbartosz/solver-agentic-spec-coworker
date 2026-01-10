"""
Targeted Regeneration Node (PR #10).

This node processes targeted regeneration decisions from sandbox review,
builds regeneration constraints, and prepares state for code regeneration.

Per PR #10 Plan:
- Pure transformation: decision + attribution → constraints artifact
- Deterministic: same inputs → same constraints fingerprint
- Idempotent: duplicate fingerprint check prevents infinite loops
- Refs-not-blobs: only artifact ref stored in state, not full constraints

Flow:
    sandbox_review_gate (decision=regenerate_targeted)
        → targeted_regeneration
        → generate_code_and_tests (reads constraints from artifact)

NON-NEGOTIABLE INVARIANTS:
1. Iteration budget is enforced (max 3 iterations)
2. Same fingerprint twice = escalate to human (stuck loop detection)
3. All paths validated at parse time (security boundary)
4. Constraints artifact is append-only (history preserved)
"""

import logging
import time
from typing import Any, Dict, Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.regeneration_models import (
    RegenerateTargetedDecision,
    RegenerationConstraints,
    IterationState,
    parse_review_decision,
    PathValidationError,
    MAX_REGENERATION_ITERATIONS,
)
from integration_coworker.persistence.artifacts.base import ArtifactRef, ArtifactCodec
from integration_coworker.persistence.artifacts import get_artifact_store

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Key used for constraints artifact
CONSTRAINTS_ARTIFACT_KEY = "regeneration_constraints"


# =============================================================================
# Main Node
# =============================================================================

def targeted_regeneration(state: WorkflowState) -> WorkflowState:
    """
    Process targeted regeneration decision and build constraints artifact.
    
    This node is invoked when sandbox_review_gate returns action="regenerate_targeted".
    It:
    1. Parses and validates the decision (paths, bounds)
    2. Builds RegenerationConstraints from decision + attribution summary
    3. Checks for stuck loops (same fingerprint repeated)
    4. Stores constraints as artifact (not inline in state)
    5. Updates iteration state with budget tracking
    
    Returns:
        Updated state with regeneration_constraints_ref set
        
    Raises:
        PathValidationError: If targets contain invalid paths (security)
        ValueError: If iteration budget exceeded
    """
    node_name = "targeted_regeneration"
    start_time = time.time()
    
    # =================================================================
    # 1. Extract and validate decision
    # =================================================================
    
    decision_dict = _extract_decision_from_state(state)
    if not decision_dict:
        logger.warning(f"{node_name}: No regeneration decision found, skipping")
        _mark_completed(state, node_name)
        return state
    
    # Parse with validation (rejects invalid paths)
    try:
        decision = RegenerateTargetedDecision.from_dict(decision_dict)
    except (PathValidationError, ValueError) as e:
        logger.error(f"{node_name}: Invalid decision - {e}")
        state.errors.append(f"Invalid regeneration decision: {e}")
        _mark_completed(state, node_name)
        return state
    
    logger.info(
        f"{node_name}: Processing regeneration - "
        f"{len(decision.targets)} targets, "
        f"fingerprint={decision.fingerprint()[:12]}..."
    )
    
    # =================================================================
    # 2. Initialize/load iteration state
    # =================================================================
    
    iteration_state = _get_or_create_iteration_state(state)
    
    # Budget check BEFORE processing
    if not iteration_state.can_iterate():
        logger.warning(
            f"{node_name}: Iteration budget exhausted "
            f"({iteration_state.current_iteration}/{iteration_state.max_iterations})"
        )
        state.errors.append("Regeneration iteration budget exhausted")
        _escalate_to_human(state, "budget_exhausted")
        _mark_completed(state, node_name)
        return state
    
    # =================================================================
    # 3. Build constraints from decision + attribution
    # =================================================================
    
    constraints = _build_constraints(state, decision)
    
    # =================================================================
    # 4. Idempotency guard: check for stuck loop
    # =================================================================
    
    constraints_fingerprint = constraints.fingerprint()
    
    # Check if we've seen this exact constraints fingerprint before
    if _is_duplicate_constraints(state, constraints_fingerprint):
        logger.warning(
            f"{node_name}: Duplicate constraints fingerprint detected - "
            f"likely stuck loop, escalating"
        )
        _escalate_to_human(state, "stuck_loop", constraints_fingerprint)
        _mark_completed(state, node_name)
        return state
    
    # =================================================================
    # 5. Store constraints as artifact
    # =================================================================
    
    run_id = state.run_id or f"regen-{int(time.time())}"
    
    ref = _store_constraints_artifact(run_id, constraints)
    
    # Store ref + summary in state (refs-not-blobs pattern)
    state.regeneration_constraints_ref = {
        "ref": ref.to_dict(),
        "summary": {
            "target_count": len(constraints.target_paths),
            "targets_preview": constraints.target_paths[:3],  # First 3 for logging
            "has_global_guidance": bool(constraints.global_guidance),
            "fingerprint": constraints_fingerprint,
        },
    }
    
    logger.info(
        f"{node_name}: Stored constraints artifact - "
        f"{ref.size_bytes} bytes, fingerprint={constraints_fingerprint[:12]}..."
    )
    
    # =================================================================
    # 6. Update iteration state
    # =================================================================
    
    # Get blocking count from attribution summary (or default)
    blocking_before = _get_blocking_count(state, "before")
    
    # Record this iteration
    iteration_state.record_iteration(
        targets=decision.targets,
        outcome="pending",  # Will be updated after regeneration completes
        blocking_before=blocking_before,
        blocking_after=0,  # Updated after regeneration
        fingerprint=constraints_fingerprint,
    )
    
    # Check if we should escalate (stuck loop via history analysis)
    if iteration_state.should_escalate():
        logger.warning(
            f"{node_name}: Escalation triggered by iteration history analysis"
        )
        _escalate_to_human(state, "no_improvement")
        _mark_completed(state, node_name)
        return state
    
    # Persist iteration state back to workflow state
    state.iteration_state = iteration_state.to_dict()
    
    # =================================================================
    # 7. Mark ready for regeneration
    # =================================================================
    
    # Set flag that codegen should use constraints
    if not state.plan:
        state.plan = {}
    state.plan["regeneration_mode"] = True
    state.plan["regeneration_fingerprint"] = constraints_fingerprint
    
    # Clear any previous rejection state
    state.plan.pop("sandbox_rejected", None)
    state.plan.pop("sandbox_rejection_feedback", None)
    
    elapsed = time.time() - start_time
    logger.info(f"{node_name}: Completed in {elapsed:.2f}s")
    
    _mark_completed(state, node_name)
    return state


# =============================================================================
# Helper Functions
# =============================================================================

def _extract_decision_from_state(state: WorkflowState) -> Optional[Dict[str, Any]]:
    """
    Extract regeneration decision from state.
    
    The decision is stored by sandbox_review_gate in review_decisions["sandbox"].
    We look for action="regenerate_targeted".
    """
    decisions = getattr(state, 'review_decisions', None) or {}
    
    # Check sandbox decision
    sandbox_decision = decisions.get("sandbox", {})
    if sandbox_decision.get("action") == "regenerate_targeted":
        return sandbox_decision
    
    # Also check if there's a raw decision in plan (for testing)
    if state.plan and state.plan.get("regeneration_decision"):
        return state.plan["regeneration_decision"]
    
    return None


def _get_or_create_iteration_state(state: WorkflowState) -> IterationState:
    """Get existing iteration state or create new one."""
    if state.iteration_state:
        return IterationState.from_dict(state.iteration_state)
    return IterationState(max_iterations=MAX_REGENERATION_ITERATIONS)


def _build_constraints(
    state: WorkflowState,
    decision: RegenerateTargetedDecision,
) -> RegenerationConstraints:
    """
    Build RegenerationConstraints from decision + attribution summary.
    
    Combines:
    - Targets and feedback from human decision
    - Attribution hints from sandbox analysis
    - Any existing context from previous iterations
    """
    # Get attribution summary if available
    attribution = getattr(state, 'sandbox_attribution_summary', None) or {}
    
    # Build target reasons from attribution and decision feedback
    target_reasons: Dict[str, list] = {}
    
    for target in decision.targets:
        reasons = []
        
        # Add feedback from decision
        if target in decision.target_feedback:
            reasons.append(f"Human feedback: {decision.target_feedback[target]}")
        
        # Add attribution hints if available
        if attribution.get("hints"):
            for hint in attribution["hints"]:
                # Match hints to targets by file path
                if hint.get("file") == target:
                    reasons.append(f"Attribution: {hint.get('message', 'Unknown issue')}")
        
        if reasons:
            target_reasons[target] = reasons
    
    # Build constraints
    return RegenerationConstraints(
        target_paths=decision.targets,
        target_reasons=target_reasons,
        preserve_non_targets=True,  # Always preserve files not being regenerated
        global_guidance=decision.global_feedback,
        decision_fingerprint=decision.fingerprint(),
        attribution_fingerprint=attribution.get("fingerprint"),
    )


def _is_duplicate_constraints(state: WorkflowState, fingerprint: str) -> bool:
    """
    Check if we've already processed constraints with this fingerprint.
    
    This detects stuck loops where the same decision is processed multiple times.
    """
    # Check iteration history for same fingerprint
    if state.iteration_state:
        iteration = IterationState.from_dict(state.iteration_state)
        for entry in iteration.history:
            if entry.fingerprint == fingerprint:
                return True
    
    return False


def _store_constraints_artifact(run_id: str, constraints: RegenerationConstraints) -> ArtifactRef:
    """
    Store constraints as artifact and return ref.
    
    Uses JSON codec for human-readable storage.
    """
    store = get_artifact_store()
    
    ref = store.put(
        run_id=run_id,
        key=CONSTRAINTS_ARTIFACT_KEY,
        value=constraints.to_dict(),
        codec=ArtifactCodec.JSON,
    )
    
    return ref


def _get_blocking_count(state: WorkflowState, when: str) -> int:
    """Get blocking issue count from quality refs."""
    quality_refs = getattr(state, 'quality_refs', None) or {}
    
    if when == "before":
        # Static analysis blocking count
        static = quality_refs.get("static", {})
        summary = static.get("summary", {})
        return summary.get("blocking_count", 0)
    
    return 0


def _escalate_to_human(
    state: WorkflowState,
    reason: str,
    fingerprint: Optional[str] = None,
) -> None:
    """
    Mark state for human escalation.
    
    This sets flags that will cause routing to interrupt for human review
    rather than attempting another regeneration.
    """
    if not state.plan:
        state.plan = {}
    
    state.plan["regeneration_escalated"] = True
    state.plan["escalation_reason"] = reason
    
    if fingerprint:
        state.plan["escalation_fingerprint"] = fingerprint
    
    # Add warning for visibility
    state.warnings.append(
        f"Regeneration escalated to human review: {reason}"
    )


def _mark_completed(state: WorkflowState, node_name: str) -> None:
    """Mark node as completed in state."""
    if node_name not in state.completed_steps:
        state.completed_steps.append(node_name)
