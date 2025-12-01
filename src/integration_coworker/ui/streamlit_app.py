"""
Streamlit UI for the Agentic Integration Co-Worker.

Provides:
- Multi-panel interface (Inputs / Run View / Artifacts / Logs)
- Real-time run status and progress tracking
- Error capture with interactive recovery (Retry/Skip/Restart)
- Artifact browser with code highlighting
- Graph trace visualization
"""
import streamlit as st
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import traceback


# -----------------------------------------------------------------
# Session State Initialization
# -----------------------------------------------------------------
def _init_session_state() -> None:
    """Initialize Streamlit session state with default values."""
    defaults = {
        "last_result": None,
        "last_error": None,
        "run_history": [],
        "pending_recovery_action": None,
        "is_running": False,
        "current_run_id": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# -----------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------
def main() -> None:
    """Main entry point for the Streamlit application."""
    st.set_page_config(
        page_title="Integration Co-Worker",
        page_icon="🔧",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _init_session_state()

    # Title and description
    st.title("🔧 Agentic Integration Co-Worker")
    st.caption("Design and generate API integrations from OpenAPI specs")

    # Sidebar for inputs
    with st.sidebar:
        _layout_sidebar_inputs()

    # Main content area with tabs
    _layout_main_tabs()


# -----------------------------------------------------------------
# Sidebar Inputs
# -----------------------------------------------------------------
def _layout_sidebar_inputs() -> None:
    """Render the sidebar input controls."""
    st.header("Configuration")

    # Spec reference(s)
    st.subheader("API Specification")
    spec_input_type = st.radio(
        "Spec Source",
        options=["URL", "File Path", "Demo (Mock Payments)"],
        horizontal=True,
        key="spec_input_type",
    )

    spec_refs: List[str] = []
    if spec_input_type == "URL":
        spec_url = st.text_input(
            "OpenAPI Spec URL",
            placeholder="https://api.example.com/openapi.yaml",
            key="spec_url",
        )
        if spec_url:
            spec_refs = [spec_url]
    elif spec_input_type == "File Path":
        spec_path = st.text_input(
            "OpenAPI Spec Path",
            placeholder="/path/to/openapi.yaml",
            key="spec_path",
        )
        if spec_path:
            spec_refs = [spec_path]
    else:
        # Demo mode - use built-in mock spec
        spec_refs = ["demo"]
        st.info("Using built-in mock payments spec")

    # Task description
    st.subheader("Task")
    task_description = st.text_area(
        "Task Description",
        placeholder="Create a checkout session for payment processing",
        height=100,
        key="task_description",
    )

    # Options
    st.subheader("Options")

    col1, col2 = st.columns(2)
    with col1:
        dry_run = st.checkbox("Dry Run", value=True, key="dry_run",
                              help="Run without persisting to database")
    with col2:
        verbose = st.checkbox("Verbose Logs", value=False, key="verbose",
                              help="Show detailed debug logging")

    provider_code = st.text_input(
        "Provider Code (optional)",
        placeholder="Auto-detected from spec",
        key="provider_code",
    )

    repo_root = st.text_input(
        "Repository Root (optional)",
        placeholder="/path/to/repo",
        key="repo_root",
        help="Target repo for file integration",
    )

    # Run button
    st.divider()
    run_disabled = not spec_refs or not task_description or st.session_state.is_running

    if st.button(
        "🚀 Run Integration" if not st.session_state.is_running else "⏳ Running...",
        disabled=run_disabled,
        type="primary",
        use_container_width=True,
    ):
        _run_integration(
            spec_refs=spec_refs,
            task_description=task_description,
            provider_code=provider_code if provider_code else None,
            repo_root=repo_root if repo_root else None,
            dry_run=dry_run,
            verbose=verbose,
        )

    # Show input validation hints
    if not spec_refs:
        st.warning("Please provide an API specification")
    if not task_description:
        st.warning("Please describe the integration task")


# -----------------------------------------------------------------
# Run Integration (with Error Capture)
# -----------------------------------------------------------------
def _run_integration(
    spec_refs: List[str],
    task_description: str,
    provider_code: Optional[str],
    repo_root: Optional[str],
    dry_run: bool,
    verbose: bool,
) -> None:
    """Execute the integration workflow with error capture."""
    from integration_coworker.api.entrypoint import design_and_generate_integration
    from integration_coworker.api.types import IntegrationOptions

    # Handle demo mode
    if spec_refs == ["demo"]:
        # Find mock spec
        possible_paths = [
            Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "mock_payments_openapi.yaml",
            Path.cwd() / "tests" / "fixtures" / "mock_payments_openapi.yaml",
        ]
        mock_spec = None
        for p in possible_paths:
            if p.exists():
                mock_spec = str(p.resolve())
                break

        if not mock_spec:
            st.session_state.last_error = {
                "error": "Demo spec not found. Run from project root.",
                "traceback": None,
                "timestamp": datetime.now().isoformat(),
            }
            return

        spec_refs = [mock_spec]

    # Build options
    options = IntegrationOptions(
        dry_run=dry_run,
        repo_integration_enabled=repo_root is not None,
        override_provider_code=provider_code,
    )

    # Clear previous state
    st.session_state.is_running = True
    st.session_state.last_error = None
    st.session_state.pending_recovery_action = None

    try:
        with st.spinner("Running integration workflow..."):
            result = design_and_generate_integration(
                spec_refs=spec_refs,
                task_description=task_description,
                provider_code=provider_code,
                repo_root=repo_root,
                options=options,
            )

        # Store result
        st.session_state.last_result = result
        st.session_state.current_run_id = result.run_id

        # Add to history
        _snapshot_run(result, None)

        st.success(f"✅ Integration completed! Run ID: {result.run_id}")

    except Exception as e:
        error_info = {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "timestamp": datetime.now().isoformat(),
            "inputs": {
                "spec_refs": spec_refs,
                "task_description": task_description,
                "provider_code": provider_code,
                "repo_root": repo_root,
                "dry_run": dry_run,
            },
        }
        st.session_state.last_error = error_info
        _snapshot_run(None, error_info)
        st.error(f"❌ Integration failed: {str(e)}")

    finally:
        st.session_state.is_running = False


# -----------------------------------------------------------------
# Main Tabs Layout
# -----------------------------------------------------------------
def _layout_main_tabs() -> None:
    """Render the main content area with tabs."""
    tab_run, tab_artifacts, tab_graph, tab_errors = st.tabs([
        "📊 Run Status",
        "📁 Artifacts",
        "🔀 Graph Trace",
        "⚠️ Errors & Recovery",
    ])

    with tab_run:
        _render_run_status()

    with tab_artifacts:
        _render_artifacts()

    with tab_graph:
        _render_graph_trace()

    with tab_errors:
        _render_errors_and_recovery()


# -----------------------------------------------------------------
# Run Status Tab
# -----------------------------------------------------------------
def _render_run_status() -> None:
    """Render the run status view."""
    result = st.session_state.last_result

    if result is None:
        st.info("No integration run yet. Configure inputs in the sidebar and click 'Run Integration'.")
        return

    # Header with run info
    st.subheader(f"Run: {result.run_id}")

    col1, col2, col3 = st.columns(3)
    with col1:
        provider = result.task.provider_code if result.task else "N/A"
        st.metric("Provider", provider)
    with col2:
        task_slug = result.task.task_slug if result.task else "N/A"
        st.metric("Task", task_slug)
    with col3:
        artifact_count = len(result.code_artifacts)
        st.metric("Artifacts", artifact_count)

    # Silver Model summary
    st.subheader("📦 Silver API Model")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Endpoints", len(result.endpoints))
    with col2:
        st.metric("Schemas", len(result.schemas))
    with col3:
        st.metric("Entities", len(result.entities))

    # Gold Model summary
    st.subheader("🏆 Gold Integration Model")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Workflow Nodes", len(result.workflow_nodes))
    with col2:
        st.metric("Workflow Edges", len(result.workflow_edges))
    with col3:
        st.metric("Endpoint Bindings", len(result.endpoint_bindings))

    # Completed Steps
    if result.completed_steps:
        st.subheader("✅ Completed Steps")
        steps_text = " → ".join(result.completed_steps)
        st.code(steps_text, language=None)

    # Report Markdown
    if result.report_markdown:
        with st.expander("📄 Full Report", expanded=False):
            st.markdown(result.report_markdown)


# -----------------------------------------------------------------
# Artifacts Tab
# -----------------------------------------------------------------
def _render_artifacts() -> None:
    """Render the artifacts browser."""
    result = st.session_state.last_result

    if result is None or not result.code_artifacts:
        st.info("No artifacts to display. Run an integration first.")
        return

    st.subheader("Generated Code Artifacts")

    # Artifact selector
    artifact_options = [
        f"[{a.artifact_type}] {a.rel_path}"
        for a in result.code_artifacts
    ]
    selected_idx = st.selectbox(
        "Select Artifact",
        options=range(len(artifact_options)),
        format_func=lambda i: artifact_options[i],
        key="artifact_selector",
    )

    if selected_idx is not None:
        artifact = result.code_artifacts[selected_idx]

        # Metadata
        col1, col2, col3 = st.columns(3)
        with col1:
            st.caption(f"**Type:** {artifact.artifact_type}")
        with col2:
            st.caption(f"**Path:** {artifact.rel_path}")
        with col3:
            size_kb = len(artifact.content) / 1024
            st.caption(f"**Size:** {size_kb:.1f} KB")

        # Determine language for syntax highlighting
        language = _detect_language(artifact.rel_path)

        # Code display
        st.code(artifact.content, language=language, line_numbers=True)

        # Download button
        st.download_button(
            label="⬇️ Download",
            data=artifact.content,
            file_name=Path(artifact.rel_path).name,
            mime="text/plain",
        )

    # Repo changes (if any)
    if result.repo_changes and result.repo_changes.changes:
        st.divider()
        st.subheader("📁 Repository Changes")

        for change in result.repo_changes.changes:
            icon = "➕" if change.change_type == "create" else "📝"
            st.text(f"{icon} {change.rel_path}")


def _detect_language(file_path: str) -> str:
    """Detect programming language from file extension."""
    ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".txt": "text",
    }
    ext = Path(file_path).suffix.lower()
    return ext_map.get(ext, "text")


# -----------------------------------------------------------------
# Graph Trace Tab
# -----------------------------------------------------------------
def _render_graph_trace() -> None:
    """Render the graph/workflow trace visualization."""
    result = st.session_state.last_result

    if result is None:
        st.info("No workflow trace to display. Run an integration first.")
        return

    st.subheader("Workflow Graph Trace")

    # Show completed steps as a flow
    if result.completed_steps:
        st.write("**Execution Order:**")

        # Create a simple flow visualization
        for i, step in enumerate(result.completed_steps, 1):
            col1, col2 = st.columns([1, 5])
            with col1:
                st.write(f"**{i}.**")
            with col2:
                st.success(f"✅ {step}")

    # Show workflow nodes
    if result.workflow_nodes:
        st.divider()
        st.write("**Workflow Nodes:**")
        for node in result.workflow_nodes:
            node_info = f"• {node.key if hasattr(node, 'key') else str(node)}"
            st.text(node_info)

    # Show workflow edges
    if result.workflow_edges:
        st.divider()
        st.write("**Workflow Edges:**")
        for edge in result.workflow_edges:
            if hasattr(edge, 'src_key') and hasattr(edge, 'dst_key'):
                edge_info = f"  {edge.src_key} → {edge.dst_key}"
            else:
                edge_info = f"  {str(edge)}"
            st.text(edge_info)


# -----------------------------------------------------------------
# Errors & Recovery Tab
# -----------------------------------------------------------------
def _render_errors_and_recovery() -> None:
    """Render error information and recovery actions."""
    error = st.session_state.last_error
    result = st.session_state.last_result

    # Show result errors (non-fatal)
    if result and result.errors:
        st.subheader("⚠️ Warnings/Errors from Run")
        for err in result.errors:
            if "Warning:" in err:
                st.warning(err)
            else:
                st.error(err)
        st.divider()

    # Show fatal error (if any)
    if error:
        st.subheader("❌ Last Error")

        st.error(error["error"])

        if error.get("traceback"):
            with st.expander("🔍 Full Traceback", expanded=False):
                st.code(error["traceback"], language="python")

        st.caption(f"Occurred at: {error.get('timestamp', 'Unknown')}")

        # Recovery actions
        st.divider()
        st.subheader("🔄 Recovery Actions")

        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("🔁 Retry", type="primary", use_container_width=True,
                         help="Re-run with the same inputs"):
                _handle_recovery_action("retry", error.get("inputs", {}))

        with col2:
            if st.button("⏭️ Skip", use_container_width=True,
                         help="Skip the failed step (if possible)"):
                _handle_recovery_action("skip", error.get("inputs", {}))

        with col3:
            if st.button("🔄 Restart", type="secondary", use_container_width=True,
                         help="Clear state and start fresh"):
                _handle_recovery_action("restart", {})

    elif not result:
        st.info("No errors to display. Run an integration to see results.")
    else:
        st.success("✅ Last run completed without errors.")


def _handle_recovery_action(action: str, context: Dict[str, Any]) -> None:
    """Handle a recovery action (retry, skip, restart)."""
    st.session_state.pending_recovery_action = action

    if action == "restart":
        # Clear all state
        st.session_state.last_result = None
        st.session_state.last_error = None
        st.session_state.pending_recovery_action = None
        st.session_state.current_run_id = None
        st.rerun()

    elif action == "retry" and context:
        # Re-run with same inputs
        _run_integration(
            spec_refs=context.get("spec_refs", []),
            task_description=context.get("task_description", ""),
            provider_code=context.get("provider_code"),
            repo_root=context.get("repo_root"),
            dry_run=context.get("dry_run", True),
            verbose=False,
        )
        st.rerun()

    elif action == "skip":
        # Note: Skip is a placeholder - actual implementation would require
        # more sophisticated workflow state management
        st.warning("Skip functionality requires workflow checkpoint support. "
                   "Use 'Restart' for now.")


# -----------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------
def _snapshot_run(result: Any, error: Optional[Dict[str, Any]]) -> None:
    """Add a run snapshot to history."""
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "run_id": result.run_id if result else None,
        "success": error is None,
        "provider_code": result.task.provider_code if result and result.task else None,
        "error_summary": error.get("error") if error else None,
    }
    st.session_state.run_history.append(snapshot)

    # Keep only last 10 runs
    if len(st.session_state.run_history) > 10:
        st.session_state.run_history = st.session_state.run_history[-10:]


# -----------------------------------------------------------------
# App Runner
# -----------------------------------------------------------------
if __name__ == "__main__":
    main()
