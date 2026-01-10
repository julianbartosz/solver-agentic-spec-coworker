"""
Review gate factory for HITL approval points.

Creates parameterized gate nodes that interrupt workflow for human review.
Each gate instance handles a specific review kind (e.g., "code", "sandbox").

Per ADR-HITL-ENHANCEMENT-v2:
- Uses interrupt() with checkpointer for pause/resume
- Resume via Command(resume=...) with same thread_id
- Idempotent - don't interrupt again if decision already exists
- Refs-not-blobs - payload uses bounded summaries + artifact refs

CRITICAL: interrupt() must NOT be in try/except - breaks pause behavior.

Usage:
    workflow.add_node("code_review_gate", review_gate("code"))
    workflow.add_node("sandbox_review_gate", review_gate("sandbox"))
"""

import logging
import time
from typing import Any, Callable, Dict, Literal, Optional

from langgraph.types import interrupt

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.review_artifacts import (
    store_review_artifacts,
    compute_unified_diff_patches,
    build_code_summary,
    build_sandbox_summary,
    refs_to_dicts,
    REVIEW_ARTIFACT_CODEC,
    MAX_FILES_IN_SUMMARY,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Supported review kinds
ReviewKind = Literal["code", "sandbox"]

# Maximum payload size for interrupt (refs + summaries only)
MAX_PAYLOAD_SIZE_BYTES = 2048


# =============================================================================
# Review Gate Factory
# =============================================================================

def review_gate(kind: ReviewKind) -> Callable[[WorkflowState], WorkflowState]:
    """
    Factory that creates a review gate node for the specified kind.
    
    Each gate instance:
    1. Checks if review is needed (idempotent - skips if decision exists)
    2. Stores large artifacts via ArtifactStore
    3. Builds bounded payload with refs + summaries
    4. Calls interrupt() to pause workflow
    5. On resume, parses decision and updates state
    
    Args:
        kind: The review kind - "code" (pre-sandbox) or "sandbox" (post-sandbox)
        
    Returns:
        A node function bound to the specified kind
        
    Usage:
        workflow.add_node("code_review_gate", review_gate("code"))
    """
    
    def gate_node(state: WorkflowState) -> WorkflowState:
        """
        Review gate node that interrupts for human approval.
        
        CRITICAL: interrupt() must NOT be wrapped in try/except.
        On resume, the entire node re-executes from the beginning.
        All pre-interrupt computation must be idempotent.
        """
        node_name = f"{kind}_review_gate"
        
        # =================================================================
        # 1. Check if review should be skipped
        # =================================================================
        
        # Already decided - idempotent guard
        if _has_existing_decision(state, kind):
            logger.info(f"{node_name}: Skipping - decision already exists for '{kind}'")
            _mark_completed(state, node_name)
            return state
        
        # HITL mode check
        if _should_skip_hitl(state):
            logger.info(f"{node_name}: Skipping - HITL disabled")
            # Auto-approve when HITL disabled
            _record_decision(state, kind, approved=True, auto=True)
            _mark_completed(state, node_name)
            return state
        
        # Dry run - no actual changes
        if state.options and state.options.dry_run:
            logger.info(f"{node_name}: Skipping - dry_run mode")
            _record_decision(state, kind, approved=True, auto=True, reason="dry_run")
            _mark_completed(state, node_name)
            return state
        
        # No changes to review
        if not _has_reviewable_content(state, kind):
            logger.info(f"{node_name}: Skipping - no content to review")
            _record_decision(state, kind, approved=True, auto=True, reason="no_content")
            _mark_completed(state, node_name)
            return state
        
        # =================================================================
        # 2. Store artifacts and build payload (idempotent)
        # =================================================================
        
        run_id = state.run_id or f"unknown-{int(time.time())}"
        
        # Store large artifacts, get refs
        artifact_refs = _store_artifacts_for_kind(state, run_id, kind)
        
        # Store refs in state for later retrieval
        if "review_artifact_refs" not in state.__dict__ or state.review_artifact_refs is None:
            state.review_artifact_refs = {}
        state.review_artifact_refs[kind] = refs_to_dicts(artifact_refs)
        
        # Set pending review kind
        state.pending_review_kind = kind
        
        # Build bounded payload
        payload = _build_review_payload(state, kind, artifact_refs)
        
        logger.info(
            f"{node_name}: Requesting review - "
            f"{payload['summary'].get('file_count', 0)} files, "
            f"payload size: {len(str(payload))} bytes"
        )
        
        # =================================================================
        # 3. INTERRUPT - DO NOT WRAP IN try/except
        # =================================================================
        # This call will:
        # - On first execution: Pause graph, return __interrupt__ to caller
        # - On resume: Return value from Command(resume=...)
        # =================================================================
        decision = interrupt(payload)
        # =================================================================
        
        # =================================================================
        # 4. Process decision (runs on resume)
        # =================================================================
        
        parsed = _parse_decision(decision)
        
        # Record the decision
        _record_decision(
            state, 
            kind, 
            approved=parsed["approved"],
            feedback=parsed.get("feedback"),
            overrides=parsed.get("overrides"),
        )
        
        # Store human feedback in state
        if parsed.get("feedback"):
            state.human_feedback = parsed["feedback"]
        
        # Clear pending review
        state.pending_review_kind = None
        
        if parsed["approved"]:
            logger.info(f"{node_name}: Approved. Feedback: {parsed.get('feedback', 'none')}")
            
            # Apply any file exclusions
            if parsed.get("overrides", {}).get("exclude_files"):
                _apply_file_exclusions(state, parsed["overrides"]["exclude_files"])
        else:
            logger.warning(f"{node_name}: Rejected. Feedback: {parsed.get('feedback', 'none')}")
            
            # Set rejection flag
            state.plan[f"{kind}_rejected"] = True
            state.plan[f"{kind}_rejection_feedback"] = parsed.get("feedback", "")
            state.warnings.append(f"Review rejected ({kind}): {parsed.get('feedback', 'No feedback')}")
        
        _mark_completed(state, node_name)
        return state
    
    # Set function metadata for graph introspection
    gate_node.__name__ = f"{kind}_review_gate"
    gate_node.__doc__ = f"Review gate for '{kind}' stage"
    gate_node._review_kind = kind  # type: ignore
    
    return gate_node


# =============================================================================
# Helper Functions
# =============================================================================

def _has_existing_decision(state: WorkflowState, kind: str) -> bool:
    """Check if a decision already exists for this kind."""
    decisions = getattr(state, 'review_decisions', None) or {}
    return kind in decisions


def _should_skip_hitl(state: WorkflowState) -> bool:
    """Check if HITL should be skipped based on options."""
    if not state.options:
        return False
    
    # Use the production contract method if available
    if hasattr(state.options, 'should_skip_hitl'):
        return state.options.should_skip_hitl()
    
    # Fallback to direct field checks
    if getattr(state.options, 'skip_hitl', False):
        return True
    
    hitl_mode = getattr(state.options, 'hitl_mode', 'auto')
    
    # Bug #10 Fix: Implement 'auto' mode logic
    if hitl_mode == 'auto':
        # 1. Check discovery confidence
        confidence = getattr(state, 'discovery_confidence', 0.0) or 0.0
        
        # 2. Check plan confidence if available
        plan_confidence = getattr(state, 'plan', {}).get('confidence', 0.0) if hasattr(state, 'plan') and state.plan else 0.0
        
        # Bug #10 Fix: Increased confidence threshold from 0.95 to 0.99
        # This prevents "False Positive" skips where high-but-not-perfect confidence
        # caused the gate to open when the user wanted to review.
        # Now, only virtually certain tasks skip HITL review.
        AUTO_APPROVAL_THRESHOLD = 0.99
        
        if confidence >= AUTO_APPROVAL_THRESHOLD:
            logger.info(f"Skipping HITL (auto): High discovery confidence ({confidence:.2f})")
            return True
            
        if plan_confidence >= AUTO_APPROVAL_THRESHOLD:
            logger.info(f"Skipping HITL (auto): High plan confidence ({plan_confidence:.2f})")
            return True
            
    return hitl_mode == 'never'


def _has_reviewable_content(state: WorkflowState, kind: str) -> bool:
    """Check if there's content to review for this kind."""
    if kind == "code":
        # Code review: check for generated code artifacts
        return bool(state.code_artifacts)
    elif kind == "sandbox":
        # Sandbox review: check for sandbox results
        return bool(state.sandbox_result)
    else:
        logger.warning(f"Unknown review kind: {kind}")
        return False


def _store_artifacts_for_kind(state: WorkflowState, run_id: str, kind: str) -> Dict[str, Any]:
    """
    Store artifacts for the specified review kind.
    
    Returns dict of artifact refs.
    """
    from integration_coworker.graph.review_artifacts import store_review_artifacts
    
    if kind == "code":
        # Get existing files for diff computation
        existing_files = _get_existing_files(state)
        
        # Compute unified diffs
        diff_patches = []
        if state.code_artifacts:
            diff_patches = compute_unified_diff_patches(state.code_artifacts, existing_files)
        
        return store_review_artifacts(
            run_id,
            "pre_write",
            code_artifacts=state.code_artifacts,
            diff_patches=diff_patches,
        )
    
    elif kind == "sandbox":
        return store_review_artifacts(
            run_id,
            "post_sandbox",
            sandbox_result=state.sandbox_result,
            code_artifacts=state.code_artifacts,
        )
    
    else:
        logger.warning(f"Unknown review kind: {kind}, storing empty artifacts")
        return {}


def _get_existing_files(state: WorkflowState) -> Dict[str, str]:
    """
    Get existing file contents from repo snapshot.
    
    Used for computing unified diffs.
    """
    existing = {}
    
    if state.repo_snapshot and hasattr(state.repo_snapshot, 'files'):
        for file_info in state.repo_snapshot.files:
            if hasattr(file_info, 'rel_path') and hasattr(file_info, 'content'):
                existing[file_info.rel_path] = file_info.content or ""
    
    return existing


def _build_review_payload(
    state: WorkflowState, 
    kind: str, 
    artifact_refs: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Build bounded interrupt payload with refs + summaries.
    
    Payload stays < MAX_PAYLOAD_SIZE_BYTES by using:
    - Artifact refs instead of full content
    - Bounded summaries (max N files, truncated strings)
    """
    payload = {
        "gate": f"{kind}_review",
        "kind": kind,
        "run_id": state.run_id,
        "provider_code": state.provider_code,
        "task_description": (state.task_description or "")[:200],
        "requested_at": time.time(),
        "artifact_refs": refs_to_dicts(artifact_refs) if artifact_refs else {},
    }
    
    if kind == "code":
        payload["summary"] = build_code_summary(state.code_artifacts)
        payload["repo_root"] = str(state.repo_root) if state.repo_root else None
    elif kind == "sandbox":
        payload["summary"] = build_sandbox_summary(state.sandbox_result)
        # Include code summary too for context
        payload["code_summary"] = build_code_summary(state.code_artifacts)
    
    return payload


def _parse_decision(decision: Any) -> Dict[str, Any]:
    """
    Parse the approval decision from resume.
    
    Accepts:
    - True/False (simple approve/reject)
    - {"approved": bool, "feedback": str, "overrides": {...}}
    - {"action": "approve"|"reject"|"regenerate", "feedback": str}
    
    Returns normalized decision dict.
    """
    if isinstance(decision, bool):
        return {"approved": decision, "feedback": None, "overrides": {}}
    
    if isinstance(decision, dict):
        # Check for action-based format
        action = decision.get("action")
        if action:
            return {
                "approved": action == "approve",
                "feedback": decision.get("feedback"),
                "overrides": decision.get("overrides", {}),
                "action": action,  # Preserve for regenerate handling
            }
        
        # Standard format
        return {
            "approved": bool(decision.get("approved", False)),
            "feedback": decision.get("feedback") or decision.get("comment"),
            "overrides": decision.get("overrides", {}),
        }
    
    # Unknown format - treat as rejection
    logger.warning(f"Unknown decision format: {type(decision)}, treating as rejection")
    return {"approved": False, "feedback": f"Unknown format: {decision}", "overrides": {}}


def _record_decision(
    state: WorkflowState,
    kind: str,
    approved: bool,
    auto: bool = False,
    reason: Optional[str] = None,
    feedback: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a review decision in state."""
    # Initialize review_decisions if needed
    if not hasattr(state, 'review_decisions') or state.review_decisions is None:
        state.review_decisions = {}
    
    state.review_decisions[kind] = {
        "approved": approved,
        "auto": auto,
        "reason": reason,
        "feedback": feedback,
        "overrides": overrides or {},
        "decided_at": time.time(),
    }


def _apply_file_exclusions(state: WorkflowState, exclude_files: list) -> None:
    """Remove excluded files from code_artifacts."""
    if not exclude_files or not state.code_artifacts:
        return
    
    exclude_set = set(exclude_files)
    original_count = len(state.code_artifacts)
    state.code_artifacts = [
        a for a in state.code_artifacts 
        if a.rel_path not in exclude_set
    ]
    filtered = original_count - len(state.code_artifacts)
    if filtered:
        logger.info(f"Review override: Excluded {filtered} files")


def _mark_completed(state: WorkflowState, node_name: str) -> None:
    """Mark node as completed."""
    if node_name not in state.completed_steps:
        state.completed_steps.append(node_name)


# =============================================================================
# Compatibility: Legacy hitl_review_gate
# =============================================================================

def hitl_review_gate(state: WorkflowState) -> WorkflowState:
    """
    Legacy HITL gate - wraps review_gate("code") for backwards compatibility.
    
    DEPRECATED: Use review_gate("code") or review_gate("sandbox") directly.
    This wrapper exists for existing graph definitions that reference hitl_review_gate.
    """
    logger.debug("hitl_review_gate: Delegating to review_gate('code')")
    return review_gate("code")(state)


# =============================================================================
# Guard Functions
# =============================================================================

def check_review_approved(state: WorkflowState, kind: str) -> bool:
    """
    Check if review was approved for the specified kind.
    
    Args:
        state: Current workflow state
        kind: Review kind to check ("code" or "sandbox")
        
    Returns:
        True if approved (or review not required), False if rejected
    """
    decisions = getattr(state, 'review_decisions', None) or {}
    decision = decisions.get(kind)
    
    if not decision:
        # No decision recorded - check legacy plan flags
        return not state.plan.get(f"{kind}_rejected", False)
    
    return decision.get("approved", False)


def get_review_feedback(state: WorkflowState, kind: str) -> Optional[str]:
    """
    Get feedback from review decision.
    
    Args:
        state: Current workflow state
        kind: Review kind
        
    Returns:
        Feedback string or None
    """
    decisions = getattr(state, 'review_decisions', None) or {}
    decision = decisions.get(kind)
    
    if decision:
        return decision.get("feedback")
    
    # Check legacy plan
    return state.plan.get(f"{kind}_rejection_feedback")
