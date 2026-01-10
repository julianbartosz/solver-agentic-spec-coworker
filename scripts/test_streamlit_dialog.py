#!/usr/bin/env python3
"""
Streamlit Dialog Test Script for HITL Review Flow.

This script provides a manual test page to verify:
1. Dialog opens, submit decision, closes correctly via st.rerun()
2. Dialog reopens for different run_id with widget keys reset
3. No nested dialogs (only one @st.dialog may be open at a time)
4. Correct dismissal behavior

Per ADR-HITL-ENHANCEMENT-v2 and Streamlit dialog contract:
- https://docs.streamlit.io/develop/api-reference/execution-flow/st.dialog

DIALOG CONTRACT (per Streamlit official docs):
==============================================
1. st.rerun() IS the official way to close a dialog programmatically
   - Called at END of submit handler to trigger full-script rerun
   - Dialog closes because it's not called again during the rerun
   
2. Dialogs inherit from st.fragment
   - Widget interactions cause PARTIAL reruns (dialog function only)
   - st.rerun() triggers FULL app rerun, closing the dialog
   
3. Widget keys MUST include context (e.g., run_id)
   - Prevents stale state when dialog reopens for different data
   
4. Only ONE dialog may be open at a time
   - Calling another @st.dialog function while one is open is an error
   
5. Dismissible dialogs (default):
   - User can click outside, press ESC, or click X to dismiss
   - on_dismiss parameter controls what happens: "ignore" (default), "rerun", or callback

Run with:
    streamlit run scripts/test_streamlit_dialog.py

Manual Test Checklist:
- [ ] Open dialog for run-001, submit "Approve", verify closes
- [ ] Open dialog for run-002, verify widget keys reset (fresh state)
- [ ] Submit "Reject" with feedback, verify feedback captured
- [ ] Try rapid open/close cycles - no state corruption
- [ ] No console errors about nested dialogs
"""

import streamlit as st
from datetime import datetime
from typing import Any, Dict, Optional
import json

# =============================================================================
# Session State Keys (include run_id to reset on dialog reopen)
# =============================================================================

def get_widget_key(base: str, run_id: str) -> str:
    """
    Generate widget key that includes run_id.
    
    This ensures widget state resets when dialog reopens for a different run.
    Per ADR-HITL-ENHANCEMENT-v2 Streamlit constraints.
    """
    return f"{base}_{run_id}"


# =============================================================================
# Mock Data
# =============================================================================

MOCK_RUNS = {
    "run-001": {
        "run_id": "run-001",
        "provider_code": "stripe_api",
        "task_description": "Integrate Stripe payment processing",
        "summary": {
            "files": 5,
            "adds": 3,
            "mods": 2,
            "dels": 0,
            "total_bytes": 12500,
        },
        "files": [
            {"path": "src/payments/stripe_client.py", "action": "create", "bytes": 4200},
            {"path": "src/payments/models.py", "action": "create", "bytes": 2100},
            {"path": "src/payments/__init__.py", "action": "create", "bytes": 200},
            {"path": "src/config.py", "action": "update", "bytes": 3500},
            {"path": "tests/test_stripe.py", "action": "update", "bytes": 2500},
        ],
        "diff_preview": """--- a/src/config.py
+++ b/src/config.py
@@ -10,6 +10,8 @@
 DATABASE_URL = os.getenv("DATABASE_URL")
+STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
+STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
""",
    },
    "run-002": {
        "run_id": "run-002",
        "provider_code": "github_api",
        "task_description": "Add GitHub webhook handler",
        "summary": {
            "files": 3,
            "adds": 2,
            "mods": 1,
            "dels": 0,
            "total_bytes": 8000,
        },
        "files": [
            {"path": "src/webhooks/github.py", "action": "create", "bytes": 5000},
            {"path": "src/webhooks/__init__.py", "action": "create", "bytes": 100},
            {"path": "src/routes.py", "action": "update", "bytes": 2900},
        ],
        "diff_preview": """--- a/src/routes.py
+++ b/src/routes.py
@@ -15,6 +15,10 @@
 app = FastAPI()
+@app.post("/webhooks/github")
+async def github_webhook(request: Request):
+    return await handle_github_webhook(request)
""",
    },
}


# =============================================================================
# Dialog Implementation (matches production pattern)
# =============================================================================

@st.dialog("HITL Review", width="large")
def review_dialog(run_data: Dict[str, Any]):
    """
    Single review dialog that handles all review types.
    
    CONTRACT (per Streamlit official documentation):
    ================================================
    1. st.rerun() at END of submit handler closes the dialog
       - This triggers a full-script rerun
       - Dialog closes because it's not called again
       
    2. Widget keys MUST include run_id
       - Prevents stale widget state when dialog reopens for different data
       
    3. Store decision in session_state BEFORE calling st.rerun()
       - Main app accesses result from session_state after rerun
       
    4. NO nested dialogs
       - This is the only @st.dialog in the app
    """
    run_id = run_data["run_id"]
    
    st.markdown(f"### Review: {run_data['provider_code']}")
    st.caption(f"Run ID: {run_id}")
    st.markdown(f"**Task:** {run_data['task_description']}")
    
    # Summary metrics
    summary = run_data["summary"]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Files", summary["files"])
    col2.metric("Adds", summary["adds"], delta=f"+{summary['adds']}")
    col3.metric("Mods", summary["mods"], delta=f"~{summary['mods']}")
    col4.metric("Deletes", summary["dels"], delta=f"-{summary['dels']}" if summary['dels'] else None)
    
    # File list
    st.markdown("#### Files to be written")
    for f in run_data["files"]:
        icon = {"create": "🆕", "update": "📝", "delete": "🗑️"}.get(f["action"], "📄")
        st.text(f"{icon} {f['path']} ({f['bytes']} bytes)")
    
    # Diff preview (bounded, not HtmlDiff)
    with st.expander("Diff Preview (unified patch)", expanded=False):
        st.code(run_data.get("diff_preview", "No diff available"), language="diff")
    
    st.divider()
    
    # Decision form
    # CRITICAL: Widget keys include run_id to reset on dialog reopen
    decision = st.radio(
        "Decision",
        ["Approve", "Reject", "Regenerate with feedback"],
        key=get_widget_key("decision", run_id),
    )
    
    feedback = ""
    if decision == "Regenerate with feedback":
        feedback = st.text_area(
            "Feedback for regeneration",
            placeholder="Describe what needs to be changed...",
            key=get_widget_key("feedback", run_id),
        )
    elif decision == "Reject":
        feedback = st.text_area(
            "Rejection reason",
            placeholder="Why is this being rejected?",
            key=get_widget_key("rejection_reason", run_id),
        )
    
    # Submit buttons
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("Submit Decision", key=get_widget_key("submit", run_id), type="primary"):
            # Store decision in session state THEN rerun to close dialog
            st.session_state.last_decision = {
                "run_id": run_id,
                "decision": decision,
                "feedback": feedback,
                "timestamp": datetime.now().isoformat(),
            }
            st.session_state.dialog_submitted = True
            # st.rerun() closes dialog by triggering full-script rerun
            # Dialog closes because this function isn't called during rerun
            st.rerun()
    
    with col2:
        if st.button("Cancel", key=get_widget_key("cancel", run_id)):
            st.session_state.dialog_cancelled = True
            st.rerun()  # Close dialog via full rerun


# =============================================================================
# Main Page
# =============================================================================

def main():
    st.set_page_config(page_title="Streamlit Dialog Test", layout="wide")
    
    st.title("🧪 Streamlit Dialog Test")
    st.markdown("""
    This page tests the HITL review dialog implementation per ADR-HITL-ENHANCEMENT-v2.
    
    **Test Checklist:**
    - [ ] Dialog opens correctly for each run
    - [ ] Widget keys reset when switching runs (check widget state)
    - [ ] Submit captures decision correctly
    - [ ] No nested dialog errors in console
    - [ ] Rapid open/close cycles don't corrupt state
    """)
    
    st.divider()
    
    # Show last decision result
    if st.session_state.get("dialog_submitted"):
        st.success("✅ Decision submitted!")
        st.json(st.session_state.last_decision)
        if st.button("Clear result"):
            st.session_state.dialog_submitted = False
            st.session_state.last_decision = None
            st.rerun()
        st.divider()
    
    if st.session_state.get("dialog_cancelled"):
        st.warning("Dialog was cancelled")
        if st.button("Clear"):
            st.session_state.dialog_cancelled = False
            st.rerun()
        st.divider()
    
    # Run selection
    st.markdown("### Select a run to review")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### Run 001: Stripe Integration")
        st.caption("5 files, 12.5 KB")
        if st.button("Open Review Dialog", key="open_run_001"):
            review_dialog(MOCK_RUNS["run-001"])
    
    with col2:
        st.markdown("#### Run 002: GitHub Webhooks")
        st.caption("3 files, 8 KB")
        if st.button("Open Review Dialog", key="open_run_002"):
            review_dialog(MOCK_RUNS["run-002"])
    
    st.divider()
    
    # Debug info
    with st.expander("Debug: Session State"):
        st.json({k: str(v) for k, v in st.session_state.items()})
    
    st.markdown("""
    ---
    ### Streamlit Dialog Contract
    
    **Per official Streamlit documentation:**
    
    1. **st.rerun() closes dialogs**: Call at END of submit handler to trigger 
       full-script rerun. Dialog closes because function isn't called during rerun.
       
    2. **Widget keys include context**: `get_widget_key(base, run_id)` pattern
       ensures fresh widget state when dialog reopens for different data.
       
    3. **Single dialog rule**: Only one `@st.dialog` may be open at a time.
       Opening another while one is active is an error.
       
    4. **Dialogs inherit from st.fragment**: Widget interactions trigger partial
       reruns (dialog only). st.rerun() triggers full app rerun.
       
    5. **Dismissible by default**: User can click outside, ESC, or X button.
       Control behavior via `on_dismiss` parameter ("ignore", "rerun", callback).
    
    **References:**
    - [Streamlit Dialog Docs](https://docs.streamlit.io/develop/api-reference/execution-flow/st.dialog)
    """)


if __name__ == "__main__":
    main()
