"""
Tests for feedback hooks - safe wrappers for auto-recording.

These tests verify that:
1. Hooks properly record feedback when enabled
2. Hooks are truly isolated and don't propagate errors
3. Hooks can be disabled for testing scenarios

NOTE: These tests are marked with pytest.mark.no_db to skip the database
fixture since they only test the wrapper logic with mocks.
"""
import pytest
from unittest.mock import patch, MagicMock

# Mark all tests in this module to skip database setup
pytestmark = pytest.mark.no_db

from integration_coworker.feedback.hooks import (
    safe_record_compile_result,
    safe_record_syntax_check,
    safe_record_test_result,
    safe_record_lint_result,
    safe_record_validation_result,
    safe_record_security_check,
    enable_feedback_hooks,
    disable_feedback_hooks,
    are_hooks_enabled,
    conditional_record,
    _safe_feedback_wrapper,
)


class TestSafeFeedbackWrapper:
    """Tests for the _safe_feedback_wrapper decorator."""

    def test_wrapper_returns_true_on_success(self):
        """Wrapper should return True when underlying function succeeds."""
        @_safe_feedback_wrapper
        def success_func():
            pass
        
        result = success_func()
        assert result is True

    def test_wrapper_returns_false_on_exception(self):
        """Wrapper should return False (not raise) when underlying function fails."""
        @_safe_feedback_wrapper
        def failing_func():
            raise RuntimeError("Database connection failed")
        
        # Should NOT raise
        result = failing_func()
        assert result is False

    def test_wrapper_catches_all_exceptions(self):
        """Wrapper should catch any exception type."""
        @_safe_feedback_wrapper
        def type_error_func():
            raise TypeError("bad type")
        
        @_safe_feedback_wrapper
        def value_error_func():
            raise ValueError("bad value")
        
        @_safe_feedback_wrapper
        def io_error_func():
            raise IOError("disk full")
        
        # None should raise
        assert type_error_func() is False
        assert value_error_func() is False
        assert io_error_func() is False


class TestHookEnableDisable:
    """Tests for enabling/disabling feedback hooks."""

    def test_hooks_enabled_by_default(self):
        """Hooks should be enabled by default."""
        # Reset to default state
        enable_feedback_hooks()
        assert are_hooks_enabled() is True

    def test_disable_hooks(self):
        """disable_feedback_hooks should turn off hooks."""
        enable_feedback_hooks()
        disable_feedback_hooks()
        assert are_hooks_enabled() is False
        # Cleanup
        enable_feedback_hooks()

    def test_enable_hooks(self):
        """enable_feedback_hooks should turn on hooks."""
        disable_feedback_hooks()
        enable_feedback_hooks()
        assert are_hooks_enabled() is True


class TestConditionalRecord:
    """Tests for conditional_record helper."""

    def test_conditional_record_calls_function_when_enabled(self):
        """conditional_record should call function when hooks enabled."""
        enable_feedback_hooks()
        
        mock_func = MagicMock(return_value=True)
        result = conditional_record(mock_func, "arg1", kwarg="value")
        
        mock_func.assert_called_once_with("arg1", kwarg="value")
        assert result is True

    def test_conditional_record_skips_when_disabled(self):
        """conditional_record should skip and return True when hooks disabled."""
        disable_feedback_hooks()
        
        mock_func = MagicMock(return_value=True)
        result = conditional_record(mock_func, "arg1")
        
        mock_func.assert_not_called()
        assert result is True
        
        # Cleanup
        enable_feedback_hooks()


class TestSafeRecordFunctions:
    """Tests for the safe_record_* functions."""

    @patch('integration_coworker.feedback.hooks.record_compile_result')
    def test_safe_record_compile_result_success(self, mock_record):
        """safe_record_compile_result should call underlying function."""
        result = safe_record_compile_result(
            run_id="run-123",
            template_key="workflow.stripe.create_payment",
            success=True,
        )
        
        # Note: error_message is accepted but not passed to underlying function
        mock_record.assert_called_once_with(
            run_id="run-123",
            template_key="workflow.stripe.create_payment",
            success=True,
        )
        assert result is True

    @patch('integration_coworker.feedback.hooks.record_compile_result')
    def test_safe_record_compile_result_handles_error(self, mock_record):
        """safe_record_compile_result should not raise on error."""
        mock_record.side_effect = RuntimeError("DB error")
        
        # Should NOT raise
        result = safe_record_compile_result(
            run_id="run-123",
            template_key="workflow.stripe.create_payment",
            success=True,
        )
        
        assert result is False

    @patch('integration_coworker.feedback.hooks.record_compile_result')
    def test_safe_record_syntax_check_qualified_key(self, mock_record):
        """safe_record_syntax_check should use qualified template key."""
        result = safe_record_syntax_check(
            run_id="run-456",
            template_key="workflow.stripe.create_payment",
            artifact_type="client",
            success=True,
        )
        
        # Should use qualified key, no error_message passed
        mock_record.assert_called_once_with(
            run_id="run-456",
            template_key="workflow.stripe.create_payment:client",
            success=True,
        )
        assert result is True

    @patch('integration_coworker.feedback.hooks.record_test_result')
    def test_safe_record_test_result(self, mock_record):
        """safe_record_test_result should pass through correctly."""
        result = safe_record_test_result(
            run_id="run-789",
            template_key="workflow.stripe.create_payment",
            passed=8,
            total=10,
            failures=["test1 failed", "test2 failed"],
        )
        
        # Note: total is converted to failed, failures is logged but not passed
        mock_record.assert_called_once_with(
            run_id="run-789",
            passed=8,
            failed=2,  # total - passed
            template_key="workflow.stripe.create_payment",
        )
        assert result is True

    @patch('integration_coworker.feedback.hooks.record_lint_result')
    def test_safe_record_lint_result(self, mock_record):
        """safe_record_lint_result should pass through correctly."""
        result = safe_record_lint_result(
            run_id="run-abc",
            template_key="workflow.stripe.create_payment",
            success=False,
            warnings=["W001: unused import"],
            errors=["E001: syntax error"],
        )
        
        # Note: warnings/errors are converted to violations count
        mock_record.assert_called_once_with(
            run_id="run-abc",
            violations=2,  # 1 warning + 1 error
            total_lines=100,  # default estimate
            template_key="workflow.stripe.create_payment",
        )
        assert result is True

    @patch('integration_coworker.feedback.hooks.record_validation_result')
    def test_safe_record_validation_result(self, mock_record):
        """safe_record_validation_result should pass through correctly."""
        result = safe_record_validation_result(
            run_id="run-def",
            template_key="workflow.stripe.create_payment",
            success=False,
            validation_errors=["Missing start node"],
        )
        
        # Note: success/errors converted to has_errors/error_count
        mock_record.assert_called_once_with(
            run_id="run-def",
            has_errors=True,
            error_count=1,
            warning_count=0,
            template_key="workflow.stripe.create_payment",
        )
        assert result is True

    @patch('integration_coworker.feedback.hooks.record_validation_result')
    def test_safe_record_security_check(self, mock_record):
        """safe_record_security_check should use validation_result internally."""
        result = safe_record_security_check(
            run_id="run-ghi",
            template_key="workflow.stripe.create_payment",
            success=False,
            violations=["Dangerous eval() call detected"],
        )
        
        # Uses record_validation_result with converted parameters
        mock_record.assert_called_once_with(
            run_id="run-ghi",
            has_errors=True,
            error_count=1,
            warning_count=0,
            template_key="workflow.stripe.create_payment",
        )
        assert result is True


class TestHooksIntegrationWithNodes:
    """Integration tests verifying hooks don't break workflow nodes."""

    def test_hooks_dont_break_on_db_unavailable(self):
        """Hooks should gracefully handle DB connection failures."""
        # Patch to simulate DB failure
        with patch('integration_coworker.feedback.hooks.record_compile_result') as mock:
            mock.side_effect = ConnectionError("Database unavailable")
            
            # Should NOT raise
            result = safe_record_compile_result(
                run_id="run-123",
                template_key="workflow.test",
                success=True,
            )
            
            assert result is False

    def test_multiple_hook_failures_dont_cascade(self):
        """Multiple hook failures should be independent."""
        with patch('integration_coworker.feedback.hooks.record_compile_result') as mock1, \
             patch('integration_coworker.feedback.hooks.record_validation_result') as mock2:
            mock1.side_effect = RuntimeError("First failure")
            mock2.side_effect = RuntimeError("Second failure")
            
            # Both should fail independently
            r1 = safe_record_compile_result("run-1", "template-1", True)
            r2 = safe_record_validation_result("run-2", "template-2", True)
            
            assert r1 is False
            assert r2 is False
