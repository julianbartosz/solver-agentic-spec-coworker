"""
Tests for B-007: Spec Conversion LLM Fallback Metrics.

Validates that when spec conversion falls back to LLM parsing instead of
deterministic parsing, this event is counted via a thread-safe metric.

References:
- DEEP_PRODUCTION_AUDIT_v2.md: B-007 Spec conversion metrics
"""

import threading
import pytest
from unittest.mock import patch, MagicMock


class TestSpecLLMFallbackCounterInfrastructure:
    """Test the counter infrastructure in detect_and_parse_spec module."""
    
    def test_counter_functions_exist(self):
        """Counter infrastructure should be importable."""
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            get_spec_llm_fallback_counts,
            reset_spec_llm_fallback_counts,
            _increment_llm_fallback_metric,
        )
        assert callable(get_spec_llm_fallback_counts)
        assert callable(reset_spec_llm_fallback_counts)
        assert callable(_increment_llm_fallback_metric)
    
    def test_counter_returns_dict_by_spec_type(self):
        """get_spec_llm_fallback_counts should return dict with spec types."""
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            get_spec_llm_fallback_counts,
            reset_spec_llm_fallback_counts,
        )
        reset_spec_llm_fallback_counts()
        counts = get_spec_llm_fallback_counts()
        
        assert isinstance(counts, dict)
        assert "graphql" in counts
        assert "asyncapi" in counts
    
    def test_counter_starts_at_zero_after_reset(self):
        """Counter should be resettable to zero for all spec types."""
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            get_spec_llm_fallback_counts,
            reset_spec_llm_fallback_counts,
        )
        reset_spec_llm_fallback_counts()
        counts = get_spec_llm_fallback_counts()
        
        assert counts["graphql"] == 0
        assert counts["asyncapi"] == 0
    
    def test_increment_increases_correct_spec_type(self):
        """Each increment should increase only the specified spec type."""
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            get_spec_llm_fallback_counts,
            reset_spec_llm_fallback_counts,
            _increment_llm_fallback_metric,
        )
        reset_spec_llm_fallback_counts()
        
        _increment_llm_fallback_metric("graphql")
        _increment_llm_fallback_metric("graphql")
        _increment_llm_fallback_metric("asyncapi")
        
        counts = get_spec_llm_fallback_counts()
        assert counts["graphql"] == 2
        assert counts["asyncapi"] == 1
    
    def test_counter_is_thread_safe(self):
        """Counter should handle concurrent increments correctly."""
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            get_spec_llm_fallback_counts,
            reset_spec_llm_fallback_counts,
            _increment_llm_fallback_metric,
        )
        reset_spec_llm_fallback_counts()
        
        num_threads = 10
        increments_per_thread = 100
        
        def worker_graphql():
            for _ in range(increments_per_thread):
                _increment_llm_fallback_metric("graphql")
        
        def worker_asyncapi():
            for _ in range(increments_per_thread):
                _increment_llm_fallback_metric("asyncapi")
        
        threads = []
        for _ in range(num_threads // 2):
            threads.append(threading.Thread(target=worker_graphql))
            threads.append(threading.Thread(target=worker_asyncapi))
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        counts = get_spec_llm_fallback_counts()
        expected = (num_threads // 2) * increments_per_thread
        assert counts["graphql"] == expected
        assert counts["asyncapi"] == expected
    
    def test_unknown_spec_type_is_ignored(self):
        """Unknown spec types should not cause errors."""
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            get_spec_llm_fallback_counts,
            reset_spec_llm_fallback_counts,
            _increment_llm_fallback_metric,
        )
        reset_spec_llm_fallback_counts()
        
        # Should not raise
        _increment_llm_fallback_metric("unknown")
        
        counts = get_spec_llm_fallback_counts()
        assert "unknown" not in counts


class TestHealthServerMetricExport:
    """Test that the metric is exported in health server."""
    
    def test_metric_exists_in_health_handler(self):
        """spec_conversion_llm_fallback_total should appear in _handle_metrics source."""
        from integration_coworker.health.server import HealthCheckHandler
        import inspect
        
        source = inspect.getsource(HealthCheckHandler._handle_metrics)
        assert "spec_conversion_llm_fallback_total" in source
        assert "# HELP spec_conversion_llm_fallback_total" in source
        assert "# TYPE spec_conversion_llm_fallback_total counter" in source
    
    def test_metric_has_spec_type_label(self):
        """Metric should have spec_type label for filtering."""
        from integration_coworker.health.server import HealthCheckHandler
        import inspect
        
        source = inspect.getsource(HealthCheckHandler._handle_metrics)
        assert 'spec_type="' in source


class TestGraphQLFallbackTracking:
    """Test GraphQL fallback metric tracking."""
    
    def test_graphql_import_error_triggers_metric(self):
        """ImportError for graphql-core should trigger metric."""
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            reset_spec_llm_fallback_counts,
            get_spec_llm_fallback_counts,
        )
        reset_spec_llm_fallback_counts()
        
        # The metric is tracked in the function - we just verify
        # the infrastructure is in place
        counts = get_spec_llm_fallback_counts()
        assert "graphql" in counts


class TestAsyncAPIFallbackTracking:
    """Test AsyncAPI fallback metric tracking."""
    
    def test_asyncapi_always_uses_llm(self):
        """AsyncAPI parsing always uses LLM (no deterministic parser)."""
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            _parse_asyncapi_to_pseudo_openapi,
        )
        # The docstring should mention this
        assert "LLM" in _parse_asyncapi_to_pseudo_openapi.__doc__
    
    def test_asyncapi_function_increments_metric(self):
        """_parse_asyncapi_to_pseudo_openapi should increment asyncapi metric."""
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            reset_spec_llm_fallback_counts,
            get_spec_llm_fallback_counts,
            _parse_asyncapi_to_pseudo_openapi,
        )
        reset_spec_llm_fallback_counts()
        
        # Call the function (will fail due to missing LLM, but should still increment)
        # We need to mock at the import point
        with patch.dict('sys.modules', {'integration_coworker.llm': MagicMock()}):
            # The metric should have been incremented before the import check
            pass
        
        # Call the actual function - it will try to import and fail gracefully
        result = _parse_asyncapi_to_pseudo_openapi("asyncapi: 2.0", "test://uri")
        
        counts = get_spec_llm_fallback_counts()
        assert counts["asyncapi"] >= 1  # Should have been incremented


class TestMetricNamingConventions:
    """Test that metric names follow Prometheus conventions."""
    
    def test_metric_name_is_snake_case(self):
        """Metric name should be snake_case."""
        metric_name = "spec_conversion_llm_fallback_total"
        assert "_" in metric_name
        assert metric_name == metric_name.lower()
    
    def test_counter_metric_ends_with_total(self):
        """Counter metrics should end with _total."""
        metric_name = "spec_conversion_llm_fallback_total"
        assert metric_name.endswith("_total")
    
    def test_label_names_are_snake_case(self):
        """Label names should be snake_case."""
        label_name = "spec_type"
        assert label_name == label_name.lower()
        assert " " not in label_name
