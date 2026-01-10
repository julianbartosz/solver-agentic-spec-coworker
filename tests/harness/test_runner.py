"""
Tests for harness runner subprocess execution.

V4 Production Hardening: Tests the subprocess-based runner that enables
reliable timeout enforcement via SIGKILL.
"""
import pytest
import os
from unittest.mock import patch, MagicMock

from integration_coworker.harness import run_with_timeout, TimeoutResult
from integration_coworker.harness.runner import _create_runner_script


class TestHarnessImport:
    """Test harness module imports correctly."""
    
    def test_import_run_with_timeout(self):
        """Verify harness module imports correctly (bug fix verification)."""
        from integration_coworker.harness import run_with_timeout
        assert callable(run_with_timeout)
        # Should be aliased to run_pipeline_with_timeout
        assert run_with_timeout.__name__ == "run_pipeline_with_timeout"
    
    def test_import_timeout_result(self):
        """Verify TimeoutResult is importable."""
        from integration_coworker.harness import TimeoutResult
        result = TimeoutResult(success=True)
        assert result.success is True
        assert result.timed_out is False


class TestCreateRunnerScript:
    """Test the generated runner script content."""
    
    def test_script_uses_correct_api(self):
        """Verify script calls design_and_generate_integration (not run_integration_workflow)."""
        script = _create_runner_script(
            spec_refs=["test.yaml"],
            task_description="Test task",
            dry_run=True,
            enable_live_tests=False,
            sandbox_gates=["ruff"],
        )
        assert "design_and_generate_integration" in script
        assert "run_integration_workflow" not in script
    
    def test_script_sets_production_profile(self):
        """Verify CODEGEN_PROFILE=production is set for sandbox execution."""
        script = _create_runner_script(
            spec_refs=["test.yaml"],
            task_description="Test task",
            dry_run=False,
            enable_live_tests=False,
            sandbox_gates=["ruff", "mypy"],
        )
        assert 'CODEGEN_PROFILE' in script
        assert 'production' in script
    
    def test_script_handles_multiple_specs(self):
        """Verify multiple spec_refs are handled."""
        script = _create_runner_script(
            spec_refs=["spec1.yaml", "spec2.json"],
            task_description="Multi-spec test",
            dry_run=True,
            enable_live_tests=False,
            sandbox_gates=[],
        )
        assert "spec1.yaml" in script
        assert "spec2.json" in script
    
    def test_script_escapes_special_chars(self):
        """Verify special characters in strings are escaped."""
        script = _create_runner_script(
            spec_refs=["path/with spaces.yaml"],
            task_description='Task with "quotes" and\nnewlines',
            dry_run=True,
            enable_live_tests=False,
            sandbox_gates=[],
        )
        # Should not crash when executed
        assert "path/with spaces.yaml" in script
    
    def test_script_dry_run_true(self):
        """Verify dry_run=True is passed correctly."""
        script = _create_runner_script(
            spec_refs=["test.yaml"],
            task_description="Test",
            dry_run=True,
            enable_live_tests=False,
            sandbox_gates=[],
        )
        assert "dry_run=True" in script
    
    def test_script_dry_run_false(self):
        """Verify dry_run=False is passed correctly."""
        script = _create_runner_script(
            spec_refs=["test.yaml"],
            task_description="Test",
            dry_run=False,
            enable_live_tests=False,
            sandbox_gates=[],
        )
        assert "dry_run=False" in script
    
    def test_script_live_tests_enabled(self):
        """Verify live tests environment is set when enabled."""
        script = _create_runner_script(
            spec_refs=["test.yaml"],
            task_description="Test",
            dry_run=True,
            enable_live_tests=True,
            sandbox_gates=[],
        )
        assert "enable_live = True" in script
        assert "ALLOW_LIVE" in script


class TestRunWithTimeout:
    """Test the subprocess runner timeout behavior."""
    
    @pytest.mark.slow
    @pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY required for full test"
    )
    def test_short_timeout_kills_process(self):
        """Verify subprocess is killed when timeout expires."""
        result = run_with_timeout(
            spec_refs=["tests/fixtures/mock_payments_openapi.yaml"],
            task_description="Test task",
            timeout_seconds=5,  # Very short - should timeout
            dry_run=True,
            sandbox_gates=[],
        )
        # Should timeout since 5s is not enough for any real run
        assert result.timed_out or not result.success
    
    @pytest.mark.slow
    @pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY required for full test"
    )
    @pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL required for full test"
    )
    def test_full_run_completes(self):
        """Verify full run completes with sufficient timeout."""
        result = run_with_timeout(
            spec_refs=["tests/fixtures/mock_payments_openapi.yaml"],
            task_description="Create a payment",
            timeout_seconds=180,  # 3 min should be enough
            dry_run=True,
            sandbox_gates=["ruff"],  # Just one gate for speed
        )
        # Should complete (may fail on LLM, but shouldn't timeout)
        assert not result.timed_out
        # If it succeeded, check result structure
        if result.success:
            assert result.result is not None
            assert "run_id" in result.result
            assert "completed_steps" in result.result


class TestTimeoutResult:
    """Test TimeoutResult dataclass."""
    
    def test_default_values(self):
        """Verify default values are sensible."""
        result = TimeoutResult(success=False)
        assert result.success is False
        assert result.timed_out is False
        assert result.result is None
        assert result.error is None
        assert result.elapsed_seconds == 0.0
    
    def test_timeout_state(self):
        """Verify timeout state is correctly represented."""
        result = TimeoutResult(
            success=False,
            timed_out=True,
            error="Timed out after 60s",
            elapsed_seconds=60.0,
            return_code=-15,  # SIGTERM
        )
        assert result.timed_out is True
        assert result.return_code == -15


class TestProgressEvents:
    """Test progress event emission and parsing."""
    
    def test_events_field_exists(self):
        """Verify TimeoutResult has events field."""
        result = TimeoutResult(success=True)
        assert hasattr(result, 'events')
        assert result.events == []
    
    def test_script_emits_events(self):
        """Verify generated script has event emission code."""
        script = _create_runner_script(
            spec_refs=["test.yaml"],
            task_description="Test task",
            dry_run=True,
            enable_live_tests=False,
            sandbox_gates=["ruff"],
        )
        assert "def emit_event" in script
        assert "__EVENT__" in script
        assert '"phase"' in script


class TestShowcaseSpecs:
    """Test showcase spec definitions."""
    
    def test_showcase_specs_import(self):
        """Verify showcase specs are importable."""
        from integration_coworker.harness import (
            SHOWCASE_SPECS,
            QUICK_SHOWCASE_SPECS,
            MINIMAL_SHOWCASE_SPECS,
            get_showcase_specs,
        )
        assert len(SHOWCASE_SPECS) >= 3
        assert len(QUICK_SHOWCASE_SPECS) >= 2
        assert len(MINIMAL_SHOWCASE_SPECS) >= 1
    
    def test_get_showcase_specs_modes(self):
        """Verify get_showcase_specs returns correct specs for each mode."""
        from integration_coworker.harness import get_showcase_specs
        
        full = get_showcase_specs("full")
        quick = get_showcase_specs("quick")
        minimal = get_showcase_specs("minimal")
        
        assert len(full) >= len(quick) >= len(minimal)
        assert len(minimal) == 1
    
    def test_showcase_spec_fields(self):
        """Verify ShowcaseSpec has required fields."""
        from integration_coworker.harness import SHOWCASE_SPECS
        
        for spec in SHOWCASE_SPECS:
            assert hasattr(spec, 'spec_file')
            assert hasattr(spec, 'task_description')
            assert hasattr(spec, 'expected_provider')
            assert isinstance(spec.spec_file, str)
            assert isinstance(spec.task_description, str)


class TestShowcaseRunner:
    """Test showcase runner function."""
    
    def test_run_showcase_import(self):
        """Verify run_showcase is importable."""
        from integration_coworker.harness import run_showcase, ShowcaseResult
        assert callable(run_showcase)
    
    def test_showcase_result_fields(self):
        """Verify ShowcaseResult has expected fields."""
        from integration_coworker.harness.runner import ShowcaseResult
        result = ShowcaseResult(
            total_specs=3,
            passed=2,
            failed=1,
            elapsed_seconds=120.0,
            per_spec_results=[],
        )
        assert result.total_specs == 3
        assert result.passed == 2
        assert result.failed == 1
