"""
Smoke tests for the Streamlit UI module.

These tests verify that the UI module can be imported without errors
and that key components are present. They do NOT test the actual
Streamlit app execution (which requires a browser).

Run with: pytest tests/test_streamlit_ui_smoke.py -v

Note: Tests that import streamlit_app require the [ui] extra:
  pip install -e '.[ui]'
"""
import pytest
from pathlib import Path


# Check if streamlit is installed
try:
    import streamlit
    STREAMLIT_AVAILABLE = True
except ImportError:
    STREAMLIT_AVAILABLE = False

# Check if typer is installed (needed for CLI tests)
try:
    import typer
    TYPER_AVAILABLE = True
except ImportError:
    TYPER_AVAILABLE = False


streamlit_required = pytest.mark.skipif(
    not STREAMLIT_AVAILABLE,
    reason="streamlit not installed (install with: pip install -e '.[ui]')"
)

typer_required = pytest.mark.skipif(
    not TYPER_AVAILABLE,
    reason="typer not installed (install with: pip install typer)"
)


class TestStreamlitUISmoke:
    """Smoke tests for Streamlit UI module."""

    @streamlit_required
    def test_ui_module_importable(self):
        """Test that the UI module can be imported."""
        # This should not raise ImportError
        from integration_coworker.ui import streamlit_app
        
        assert streamlit_app is not None

    @streamlit_required
    def test_main_function_exists(self):
        """Test that the main entry point function exists."""
        from integration_coworker.ui.streamlit_app import main
        
        assert callable(main)

    @streamlit_required
    def test_session_state_init_exists(self):
        """Test that session state initialization function exists."""
        from integration_coworker.ui.streamlit_app import _init_session_state
        
        assert callable(_init_session_state)

    @streamlit_required
    def test_layout_functions_exist(self):
        """Test that layout functions exist."""
        from integration_coworker.ui.streamlit_app import (
            _layout_sidebar_inputs,
            _layout_main_tabs,
        )
        
        assert callable(_layout_sidebar_inputs)
        assert callable(_layout_main_tabs)

    @streamlit_required
    def test_render_functions_exist(self):
        """Test that render functions exist."""
        from integration_coworker.ui.streamlit_app import (
            _render_run_status,
            _render_artifacts,
            _render_graph_trace,
            _render_errors_and_recovery,
        )
        
        assert callable(_render_run_status)
        assert callable(_render_artifacts)
        assert callable(_render_graph_trace)
        assert callable(_render_errors_and_recovery)

    @streamlit_required
    def test_helper_functions_exist(self):
        """Test that helper functions exist."""
        from integration_coworker.ui.streamlit_app import (
            _run_integration,
            _handle_recovery_action,
            _snapshot_run,
            _detect_language,
        )
        
        assert callable(_run_integration)
        assert callable(_handle_recovery_action)
        assert callable(_snapshot_run)
        assert callable(_detect_language)

    @streamlit_required
    def test_detect_language_function(self):
        """Test language detection from file extensions."""
        from integration_coworker.ui.streamlit_app import _detect_language
        
        assert _detect_language("test.py") == "python"
        assert _detect_language("test.js") == "javascript"
        assert _detect_language("test.ts") == "typescript"
        assert _detect_language("test.json") == "json"
        assert _detect_language("test.yaml") == "yaml"
        assert _detect_language("test.yml") == "yaml"
        assert _detect_language("test.md") == "markdown"
        assert _detect_language("test.unknown") == "text"

    def test_ui_init_module_exists(self):
        """Test that the ui __init__.py exists."""
        from integration_coworker import ui
        
        assert ui is not None


class TestRecoveryModuleSmoke:
    """Smoke tests for the recovery helper module."""

    def test_recovery_module_importable(self):
        """Test that the recovery module can be imported."""
        from integration_coworker.api import recovery
        
        assert recovery is not None

    def test_recovery_context_exists(self):
        """Test that RecoveryContext dataclass exists."""
        from integration_coworker.api.recovery import RecoveryContext
        
        # Should be able to create an instance
        ctx = RecoveryContext(
            run_id="test-run-id",
            spec_refs=["test.yaml"],
            task_description="Test task",
        )
        assert ctx.run_id == "test-run-id"
        assert ctx.spec_refs == ["test.yaml"]
        assert ctx.task_description == "Test task"
        assert ctx.dry_run is True  # default

    def test_recovery_functions_exist(self):
        """Test that recovery functions exist."""
        from integration_coworker.api.recovery import (
            retry_from_last_failure,
            skip_failing_step,
            restart_fresh,
            create_recovery_context,
        )
        
        assert callable(retry_from_last_failure)
        assert callable(skip_failing_step)
        assert callable(restart_fresh)
        assert callable(create_recovery_context)

    def test_create_recovery_context(self):
        """Test creating a recovery context from inputs."""
        from integration_coworker.api.recovery import create_recovery_context
        
        inputs = {
            "spec_refs": ["api.yaml"],
            "task_description": "Create payment",
            "provider_code": "stripe",
            "dry_run": False,
        }
        
        ctx = create_recovery_context(inputs, error="Test error", failed_step="build_silver")
        
        assert ctx.spec_refs == ["api.yaml"]
        assert ctx.task_description == "Create payment"
        assert ctx.provider_code == "stripe"
        assert ctx.dry_run is False
        assert ctx.last_error == "Test error"
        assert ctx.failed_step == "build_silver"

    def test_restart_fresh_is_safe(self):
        """Test that restart_fresh doesn't raise."""
        from integration_coworker.api.recovery import restart_fresh
        
        # Should not raise
        restart_fresh()


class TestCLIUICommand:
    """Tests for the CLI ui command."""

    @typer_required
    def test_cli_app_has_ui_command(self):
        """Test that the CLI app includes the ui command."""
        from integration_coworker.cli import app
        
        # Get registered command names
        command_names = [cmd.name for cmd in app.registered_commands]
        
        assert "ui" in command_names

    @typer_required
    def test_launch_ui_function_exists(self):
        """Test that the launch_ui function exists."""
        from integration_coworker.cli import launch_ui
        
        assert callable(launch_ui)
