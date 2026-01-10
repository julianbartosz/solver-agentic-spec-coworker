"""
Streamlit review dialog for HITL approval gates.

Single dialog component that handles both 'code' and 'sandbox' review kinds.
Implements refs-not-blobs discipline by rendering summaries and loading
full artifacts only on "View Details" actions.

Per ADR-HITL-ENHANCEMENT-v2 + PR #4:
- Detects pending_review_kind and loads corresponding payload
- Renders bounded summaries (never full blobs in state)
- Provides Approve / Reject / Reject+Feedback actions
- Persists decision to review_decisions[kind]
- Uses @st.dialog for Streamlit best practices

IMPORT SAFETY:
This module uses lazy imports for streamlit to allow importing without
streamlit installed. Functions that use streamlit will raise ImportError
at call time if streamlit is not available.

Usage:
    from integration_coworker.ui.review_dialog import render_review_dialog
    
    # In main app, after detecting pending_review_kind:
    render_review_dialog(run_id, pending_kind, review_artifact_refs)
"""

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

# Lazy import for streamlit - allows module to be imported without streamlit
if TYPE_CHECKING:
    import streamlit as st

logger = logging.getLogger(__name__)


def _get_streamlit():
    """Lazy import streamlit, raising ImportError with helpful message if not installed."""
    try:
        import streamlit as st
        return st
    except ImportError:
        raise ImportError(
            "Streamlit is required for the review dialog UI. "
            "Install with: pip install 'solver-agentic-spec-coworker[ui]'"
        )


# =============================================================================
# Constants
# =============================================================================

# Review kind display names
REVIEW_KIND_LABELS = {
    "code": "Code Review",
    "sandbox": "Sandbox Validation Review",
}

# Status icons
STATUS_ICONS = {
    "passed": "✅",
    "failed": "❌",
    "warning": "⚠️",
    "skipped": "⏭️",
}


# =============================================================================
# Public API
# =============================================================================

def render_review_dialog(
    run_id: str,
    pending_kind: str,
    artifact_refs: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Render the review dialog for a pending HITL approval.
    
    This function renders a Streamlit dialog that displays:
    - Bounded summary of code/sandbox changes
    - "View Details" expandable sections for full artifacts
    - Approve/Reject buttons with optional feedback
    
    STREAMLIT CONTRACT:
    - Returns decision dict when user submits
    - Caller is responsible for persisting to session_state
    - Caller is responsible for calling st.rerun() to close dialog
    - NO callback plumbing - direct return pattern
    
    Args:
        run_id: The workflow run ID
        pending_kind: The review kind ("code" or "sandbox")
        artifact_refs: Dict of artifact refs from review_artifact_refs[kind]
        
    Returns:
        Decision dict if submitted, None if dialog is still open
    """""
    st = _get_streamlit()
    
    # Dialog header
    kind_label = REVIEW_KIND_LABELS.get(pending_kind, f"{pending_kind.title()} Review")
    st.subheader(f"🔍 {kind_label}")
    st.caption(f"Run ID: `{run_id}`")
    
    st.markdown("---")
    
    # Render appropriate content based on kind
    if pending_kind == "code":
        _render_code_review_content(artifact_refs)
    elif pending_kind == "sandbox":
        _render_sandbox_review_content(artifact_refs)
    else:
        st.warning(f"Unknown review kind: {pending_kind}")
    
    st.markdown("---")
    
    # Decision form - returns decision dict directly (no callback)
    return _render_decision_form(run_id, pending_kind)


def show_review_dialog(
    run_id: str,
    pending_kind: str,
    artifact_refs: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Show the review dialog as a Streamlit modal.
    
    This wraps render_review_dialog with @st.dialog decorator.
    Use this when you want a modal popup.
    
    CRITICAL INVARIANTS (per Streamlit docs):
    
    1. Only ONE dialog function may be called per script run
       - The caller MUST ensure this is called exactly once
       - Called at top-level of main(), not inside tabs/columns/expanders
    
    2. Dialog is implicitly opened when the decorated function is called
       - The @st.dialog decorator creates the modal automatically
       - No separate "open" call needed
    
    3. Dialog closes via st.rerun() - NOT by returning from function
       - The function returns the decision but doesn't close the dialog
       - Caller is responsible for calling st.rerun() after persisting decision
       - This is intentional: ensures state is persisted BEFORE rerun
    
    Args:
        run_id: The workflow run ID
        pending_kind: The review kind ("code" or "sandbox")
        artifact_refs: Dict of artifact refs from review_artifact_refs[kind]
        
    Returns:
        Decision dict if submitted (caller should persist then st.rerun())
        None if dialog still open (no action needed, user is interacting)
    """
    st = _get_streamlit()
    
    # Apply dialog decorator dynamically (allows lazy import pattern)
    # The decorator creates a modal with the given title and width
    @st.dialog("Review Required", width="large")
    def _dialog_inner():
        return render_review_dialog(run_id, pending_kind, artifact_refs)
    
    return _dialog_inner()


def check_pending_review(session_state: Any) -> Optional[Dict[str, Any]]:
    """
    Check session state for a pending review.
    
    Call this in your main app loop to detect when review dialog should be shown.
    
    Args:
        session_state: Streamlit session state object
        
    Returns:
        Dict with {run_id, kind, artifact_refs} if review pending, else None
    """
    # Check for last_result_dict from harness runner
    result = getattr(session_state, 'last_result_dict', None)
    if not result:
        return None
    
    # Check for pending_review_kind in result
    pending_kind = result.get('pending_review_kind')
    if not pending_kind:
        return None
    
    run_id = result.get('run_id')
    artifact_refs = result.get('review_artifact_refs', {}).get(pending_kind, {})
    
    return {
        'run_id': run_id,
        'kind': pending_kind,
        'artifact_refs': artifact_refs,
    }


# =============================================================================
# Code Review Content
# =============================================================================

def _render_code_review_content(artifact_refs: Dict[str, Any]) -> None:
    """Render code review content with summaries and expandable details."""
    st = _get_streamlit()
    
    # Load code snapshot summary
    snapshot = _load_artifact_safe(artifact_refs.get('code_snapshot'))
    diff_patches = _load_artifact_safe(artifact_refs.get('diff_patches'))
    
    if not snapshot and not diff_patches:
        st.info("No code changes to review.")
        return
    
    # Summary section
    st.markdown("### 📄 Files Changed")
    
    if snapshot:
        # File list (bounded summary)
        file_count = len(snapshot)
        col1, col2 = st.columns([3, 1])
        with col1:
            st.metric("Files", file_count)
        with col2:
            total_size = sum(f.get('size', 0) for f in snapshot)
            st.metric("Total Size", _format_bytes(total_size))
        
        # File table
        for file_info in snapshot[:10]:  # Bounded to 10
            _render_file_summary(file_info)
        
        if file_count > 10:
            st.caption(f"... and {file_count - 10} more files")
    
    # Diff preview section
    if diff_patches:
        with st.expander("📝 View Diff Patches", expanded=False):
            for i, patch in enumerate(diff_patches[:5]):  # Bounded to 5
                st.code(patch, language="diff")
                if i < len(diff_patches) - 1:
                    st.divider()
            
            if len(diff_patches) > 5:
                st.caption(f"... and {len(diff_patches) - 5} more patches")


def _render_file_summary(file_info: Dict[str, Any]) -> None:
    """Render a single file summary row."""
    st = _get_streamlit()
    
    path = file_info.get('path', 'unknown')
    size = file_info.get('size', 0)
    file_type = file_info.get('type', 'unknown')
    sha = file_info.get('sha256', '')[:8]
    
    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        st.text(f"📁 {path}")
    with col2:
        st.caption(_format_bytes(size))
    with col3:
        st.caption(f"sha: {sha}...")


# =============================================================================
# Sandbox Review Content
# =============================================================================

def _render_sandbox_review_content(artifact_refs: Dict[str, Any]) -> None:
    """Render sandbox review content with validation results."""
    st = _get_streamlit()
    
    # Load sandbox result
    sandbox_result = _load_artifact_safe(artifact_refs.get('sandbox_result'))
    
    if not sandbox_result:
        st.info("No sandbox results to review.")
        return
    
    # Summary section
    st.markdown("### 🧪 Sandbox Validation Results")
    
    # Overall status
    overall_passed = sandbox_result.get('passed', False)
    summary = sandbox_result.get('summary', 'No summary available')
    
    if overall_passed:
        st.success(f"✅ All validation checks passed")
    else:
        st.warning(f"⚠️ Some validation checks failed")
    
    st.caption(summary)
    
    # Gate results
    gates = sandbox_result.get('gates', [])
    if gates:
        st.markdown("#### Gate Results")
        
        # Convert list to dict if needed
        if isinstance(gates, list):
            gates_dict = {g.get('name', f'gate_{i}'): g for i, g in enumerate(gates)}
        else:
            gates_dict = gates
        
        cols = st.columns(min(len(gates_dict), 4))
        for i, (gate_name, gate_result) in enumerate(gates_dict.items()):
            with cols[i % 4]:
                passed = gate_result.get('passed', True)
                icon = STATUS_ICONS['passed'] if passed else STATUS_ICONS['failed']
                st.markdown(f"{icon} **{gate_name}**")
                if not passed and gate_result.get('error'):
                    error_preview = gate_result['error'][:100]
                    st.caption(f"Error: {error_preview}...")
    
    # Detailed errors section
    errors = sandbox_result.get('errors', [])
    if errors:
        with st.expander(f"❌ View Errors ({len(errors)})", expanded=True):
            for error in errors[:10]:  # Bounded
                st.error(error[:500])  # Truncate long errors
            
            if len(errors) > 10:
                st.caption(f"... and {len(errors) - 10} more errors")
    
    # Warnings section
    warnings = sandbox_result.get('warnings', [])
    if warnings:
        with st.expander(f"⚠️ View Warnings ({len(warnings)})", expanded=False):
            for warning in warnings[:10]:
                st.warning(warning[:500])


# =============================================================================
# Decision Form
# =============================================================================

def _render_decision_form(
    run_id: str,
    pending_kind: str,
) -> Optional[Dict[str, Any]]:
    """
    Render the approve/reject decision form.
    
    STREAMLIT CONTRACT:
    - Returns decision dict when submitted
    - Caller persists to session_state, then calls st.rerun() to close
    - NO callback plumbing
    
    Returns:
        Decision dict if submitted, None if user has not yet submitted
    """
    st = _get_streamlit()
    
    st.markdown("### 📋 Your Decision")
    
    # Initialize session state for this dialog
    form_key = f"review_decision_{run_id}_{pending_kind}"
    if form_key not in st.session_state:
        st.session_state[form_key] = {
            'feedback': '',
            'exclude_files': [],
            'submitted': False,
        }
    
    # Feedback text area
    feedback = st.text_area(
        "Feedback (optional)",
        value=st.session_state[form_key]['feedback'],
        placeholder="Enter feedback for regeneration or audit trail...",
        key=f"feedback_{run_id}_{pending_kind}",
        height=100,
    )
    st.session_state[form_key]['feedback'] = feedback
    
    # File exclusion (for code reviews)
    if pending_kind == "code":
        exclude_files = st.text_input(
            "Exclude files (comma-separated paths)",
            value=",".join(st.session_state[form_key]['exclude_files']),
            placeholder="src/temp.py, tests/scratch.py",
            key=f"exclude_{run_id}_{pending_kind}",
        )
        if exclude_files:
            st.session_state[form_key]['exclude_files'] = [
                f.strip() for f in exclude_files.split(",") if f.strip()
            ]
    
    # Action buttons
    col1, col2, col3 = st.columns([1, 1, 1])
    
    decision = None
    
    with col1:
        if st.button("✅ Approve", type="primary", use_container_width=True):
            decision = _build_decision(
                approved=True,
                feedback=feedback,
                exclude_files=st.session_state[form_key].get('exclude_files', []),
            )
    
    with col2:
        if st.button("❌ Reject", type="secondary", use_container_width=True):
            decision = _build_decision(
                approved=False,
                feedback=feedback,
            )
    
    with col3:
        if st.button("🔄 Reject + Regenerate", type="secondary", use_container_width=True):
            if not feedback:
                st.error("Please provide feedback for regeneration.")
            else:
                decision = _build_decision(
                    approved=False,
                    feedback=feedback,
                    regenerate=True,
                )
    
    # Process decision - just return it, caller handles persistence + rerun
    if decision:
        st.session_state[form_key]['submitted'] = True
        return decision
    
    return None


def _build_decision(
    approved: bool,
    feedback: Optional[str] = None,
    exclude_files: Optional[list] = None,
    regenerate: bool = False,
) -> Dict[str, Any]:
    """Build a decision dict for the review gate.
    
    IMPORTANT: This function builds decisions for code/pre-write review gates.
    For sandbox review gates that need action/patches, use create_sandbox_decision()
    from integration_coworker.graph.human_edit_models instead.
    """
    # Import schema version for consistency (ADR-0010 requirement)
    from integration_coworker.graph.human_edit_models import DECISION_SCHEMA_VERSION
    
    decision = {
        "approved": approved,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "decision_version": DECISION_SCHEMA_VERSION,  # Required for schema validation
    }
    
    if feedback:
        decision["feedback"] = feedback
    
    if exclude_files:
        decision["overrides"] = {"exclude_files": exclude_files}
    
    if regenerate:
        decision["regenerate"] = True
    
    return decision


# =============================================================================
# Artifact Loading
# =============================================================================

def _load_artifact_safe(ref_dict: Optional[Dict[str, Any]]) -> Optional[Any]:
    """
    Safely load an artifact from a ref dict.
    
    Returns None if ref is missing or load fails.
    """
    if not ref_dict:
        return None
    
    try:
        from integration_coworker.graph.review_artifacts import load_review_artifact_from_dict
        return load_review_artifact_from_dict(ref_dict)
    except Exception as e:
        logger.warning(f"Failed to load artifact: {e}")
        return None


# =============================================================================
# Utilities
# =============================================================================

def _format_bytes(size: int) -> str:
    """Format byte size as human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# =============================================================================
# Integration with Main App
# =============================================================================

def integrate_review_dialog_into_app():
    """
    Example integration code showing how to add review dialog to main app.
    
    Add this pattern to streamlit_app.py:
    
    ```python
    from integration_coworker.ui.review_dialog import (
        check_pending_review,
        show_review_dialog,
    )
    
    # After running workflow and getting result:
    pending = check_pending_review(st.session_state)
    if pending:
        decision = show_review_dialog(
            pending['run_id'],
            pending['kind'],
            pending['artifact_refs'],
        )
        if decision:
            # Decision was submitted, resume workflow
            from integration_coworker.graph.runtime import resume_with_approval
            resume_with_approval(pending['run_id'], decision)
            st.rerun()
    ```
    """
    pass  # Documentation only


# =============================================================================
# Direct Resume Helper
# =============================================================================

def resume_with_decision(run_id: str, decision: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resume a paused workflow with the given decision.
    
    Wrapper around runtime.resume_with_approval for UI use.
    
    Args:
        run_id: The workflow run ID
        decision: Decision dict from render_decision_form
        
    Returns:
        Result dict with 'success', 'errors', 'warnings' keys
    """
    try:
        from integration_coworker.graph.runtime import resume_with_approval
        
        result = resume_with_approval(run_id, decision)
        
        return {
            'success': True,
            'run_id': run_id,
            'decision': decision,
            'completed_steps': getattr(result, 'completed_steps', []),
            'errors': getattr(result, 'errors', []),
            'warnings': getattr(result, 'warnings', []),
        }
    except ValueError as e:
        return {
            'success': False,
            'error': f"Invalid state: {e}",
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
        }
