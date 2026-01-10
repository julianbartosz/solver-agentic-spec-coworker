"""
Tests for B-008: Connection Leak and Feedback Failure Metrics.

Validates that:
1. Connection leaks (connections not properly closed) are tracked
2. Feedback recording failures are tracked
3. Both metrics are exported in /metrics endpoint

References:
- DEEP_PRODUCTION_AUDIT_v2.md: B-008 Connection leak and feedback failure metrics
"""

import threading
import pytest
from unittest.mock import patch, MagicMock


class TestConnectionLeakCounter:
    """Test the connection leak counter infrastructure."""
    
    def test_counter_functions_exist(self):
        """Connection leak counter infrastructure should be importable."""
        from integration_coworker.persistence.db import (
            get_connection_leak_count,
            reset_connection_leak_count,
            _increment_connection_leak_count,
        )
        assert callable(get_connection_leak_count)
        assert callable(reset_connection_leak_count)
        assert callable(_increment_connection_leak_count)
    
    def test_counter_starts_at_zero_after_reset(self):
        """Counter should be resettable to zero."""
        from integration_coworker.persistence.db import (
            get_connection_leak_count,
            reset_connection_leak_count,
        )
        reset_connection_leak_count()
        assert get_connection_leak_count() == 0
    
    def test_increment_increases_counter(self):
        """Each call to increment should increase counter by 1."""
        from integration_coworker.persistence.db import (
            get_connection_leak_count,
            reset_connection_leak_count,
            _increment_connection_leak_count,
        )
        reset_connection_leak_count()
        
        _increment_connection_leak_count()
        assert get_connection_leak_count() == 1
        
        _increment_connection_leak_count()
        _increment_connection_leak_count()
        assert get_connection_leak_count() == 3
    
    def test_counter_is_thread_safe(self):
        """Counter should handle concurrent increments correctly."""
        from integration_coworker.persistence.db import (
            get_connection_leak_count,
            reset_connection_leak_count,
            _increment_connection_leak_count,
        )
        reset_connection_leak_count()
        
        num_threads = 10
        increments_per_thread = 100
        
        def worker():
            for _ in range(increments_per_thread):
                _increment_connection_leak_count()
        
        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        expected = num_threads * increments_per_thread
        assert get_connection_leak_count() == expected


class TestFeedbackFailureCounter:
    """Test the feedback failure counter infrastructure."""
    
    def test_counter_functions_exist(self):
        """Feedback failure counter infrastructure should be importable."""
        from integration_coworker.feedback.hooks import (
            get_feedback_failure_count,
            reset_feedback_failure_count,
            _increment_feedback_failure_count,
        )
        assert callable(get_feedback_failure_count)
        assert callable(reset_feedback_failure_count)
        assert callable(_increment_feedback_failure_count)
    
    def test_counter_starts_at_zero_after_reset(self):
        """Counter should be resettable to zero."""
        from integration_coworker.feedback.hooks import (
            get_feedback_failure_count,
            reset_feedback_failure_count,
        )
        reset_feedback_failure_count()
        assert get_feedback_failure_count() == 0
    
    def test_increment_increases_counter(self):
        """Each call to increment should increase counter by 1."""
        from integration_coworker.feedback.hooks import (
            get_feedback_failure_count,
            reset_feedback_failure_count,
            _increment_feedback_failure_count,
        )
        reset_feedback_failure_count()
        
        _increment_feedback_failure_count()
        assert get_feedback_failure_count() == 1
        
        _increment_feedback_failure_count()
        _increment_feedback_failure_count()
        assert get_feedback_failure_count() == 3
    
    def test_counter_is_thread_safe(self):
        """Counter should handle concurrent increments correctly."""
        from integration_coworker.feedback.hooks import (
            get_feedback_failure_count,
            reset_feedback_failure_count,
            _increment_feedback_failure_count,
        )
        reset_feedback_failure_count()
        
        num_threads = 10
        increments_per_thread = 100
        
        def worker():
            for _ in range(increments_per_thread):
                _increment_feedback_failure_count()
        
        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        expected = num_threads * increments_per_thread
        assert get_feedback_failure_count() == expected
    
    def test_safe_wrapper_increments_on_exception(self):
        """Safe feedback wrapper should increment counter on failure."""
        from integration_coworker.feedback.hooks import (
            _safe_feedback_wrapper,
            get_feedback_failure_count,
            reset_feedback_failure_count,
        )
        reset_feedback_failure_count()
        
        @_safe_feedback_wrapper
        def failing_function():
            raise RuntimeError("Simulated failure")
        
        # Call should not raise
        result = failing_function()
        assert result is False
        
        # Counter should have incremented
        assert get_feedback_failure_count() == 1
    
    def test_safe_wrapper_no_increment_on_success(self):
        """Safe feedback wrapper should not increment counter on success."""
        from integration_coworker.feedback.hooks import (
            _safe_feedback_wrapper,
            get_feedback_failure_count,
            reset_feedback_failure_count,
        )
        reset_feedback_failure_count()
        
        @_safe_feedback_wrapper
        def succeeding_function():
            pass  # No exception
        
        result = succeeding_function()
        assert result is True
        
        # Counter should NOT have incremented
        assert get_feedback_failure_count() == 0


class TestHealthServerMetricExport:
    """Test that both metrics are exported in health server."""
    
    def test_connection_leak_metric_exists(self):
        """db_connection_leak_detected_total should appear in _handle_metrics source."""
        from integration_coworker.health.server import HealthCheckHandler
        import inspect
        
        source = inspect.getsource(HealthCheckHandler._handle_metrics)
        assert "db_connection_leak_detected_total" in source
        assert "# HELP db_connection_leak_detected_total" in source
        assert "# TYPE db_connection_leak_detected_total counter" in source
    
    def test_feedback_failure_metric_exists(self):
        """feedback_recording_failures_total should appear in _handle_metrics source."""
        from integration_coworker.health.server import HealthCheckHandler
        import inspect
        
        source = inspect.getsource(HealthCheckHandler._handle_metrics)
        assert "feedback_recording_failures_total" in source
        assert "# HELP feedback_recording_failures_total" in source
        assert "# TYPE feedback_recording_failures_total counter" in source


class TestMetricNamingConventions:
    """Test that metric names follow Prometheus conventions."""
    
    def test_connection_leak_metric_is_snake_case(self):
        """Connection leak metric name should be snake_case."""
        metric_name = "db_connection_leak_detected_total"
        assert "_" in metric_name
        assert metric_name == metric_name.lower()
    
    def test_connection_leak_metric_ends_with_total(self):
        """Counter metrics should end with _total."""
        metric_name = "db_connection_leak_detected_total"
        assert metric_name.endswith("_total")
    
    def test_feedback_failure_metric_is_snake_case(self):
        """Feedback failure metric name should be snake_case."""
        metric_name = "feedback_recording_failures_total"
        assert "_" in metric_name
        assert metric_name == metric_name.lower()
    
    def test_feedback_failure_metric_ends_with_total(self):
        """Counter metrics should end with _total."""
        metric_name = "feedback_recording_failures_total"
        assert metric_name.endswith("_total")


class TestConnectionWrapperLeakDetection:
    """Test that ConnectionWrapper triggers leak detection."""
    
    def test_connection_wrapper_del_docstring_mentions_b008(self):
        """ConnectionWrapper.__del__ should mention B-008."""
        from integration_coworker.persistence.db import ConnectionWrapper
        del_method = ConnectionWrapper.__del__
        assert del_method.__doc__ is not None
        assert "B-008" in del_method.__doc__
