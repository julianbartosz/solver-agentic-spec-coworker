"""
HITL (Human-in-the-Loop) gate node for approval before repo writes.

DEPRECATED: This module is a compatibility shim.
Use review_gate.py instead:
    from integration_coworker.graph.nodes.review_gate import review_gate
    workflow.add_node("code_review_gate", review_gate("code"))

This module re-exports the legacy hitl_review_gate function which wraps
review_gate("code") for backwards compatibility with existing graph definitions.

Migration:
    # Old (deprecated)
    from integration_coworker.graph.nodes.hitl_gate import hitl_review_gate
    workflow.add_node("hitl_review_gate", hitl_review_gate)
    
    # New (preferred)
    from integration_coworker.graph.nodes.review_gate import review_gate
    workflow.add_node("code_review_gate", review_gate("code"))
    workflow.add_node("sandbox_review_gate", review_gate("sandbox"))
"""

import logging
import time
import warnings
from typing import Any, Dict

# Re-export from review_gate for backwards compatibility
from integration_coworker.graph.nodes.review_gate import (
    hitl_review_gate,
    check_review_approved,
    get_review_feedback,
)
from integration_coworker.graph.state import WorkflowState

logger = logging.getLogger(__name__)


# =============================================================================
# Backwards Compatibility Exports
# =============================================================================

def _build_interrupt_payload(state: WorkflowState) -> Dict[str, Any]:
    """
    DEPRECATED: Build HITL interrupt payload for backwards compatibility.
    
    This function is kept for backwards compatibility with tests that
    import it from hitl_gate. The actual implementation has moved to
    review_gate._build_review_payload().
    
    Key difference: This returns `hitl_requested_at` (legacy name) while
    the new API uses `requested_at`.
    
    Args:
        state: WorkflowState to build payload from
        
    Returns:
        Dict with interrupt payload including hitl_requested_at timestamp
    """
    warnings.warn(
        "_build_interrupt_payload is deprecated. "
        "Use review_gate._build_review_payload() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    
    # Build minimal payload matching old API
    return {
        "gate": "code_review",
        "run_id": state.run_id,
        "provider_code": state.provider_code,
        "task_description": (state.task_description or "")[:200],
        "hitl_requested_at": time.time(),  # Legacy name for timestamp
    }


def check_hitl_approval(state) -> bool:
    """
    DEPRECATED: Use check_review_approved(state, "code") instead.
    
    Check if HITL approval was granted for code review.
    """
    warnings.warn(
        "check_hitl_approval is deprecated. Use check_review_approved(state, 'code') instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return check_review_approved(state, "code")


# Preserve docstrings and exports for anyone importing from this module
__all__ = [
    "hitl_review_gate",
    "_build_interrupt_payload",  # Deprecated but kept for test compatibility
    "check_hitl_approval",  # Deprecated
    "check_review_approved",
    "get_review_feedback",
]

