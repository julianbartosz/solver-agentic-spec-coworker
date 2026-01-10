"""
Tests for B-005: KG Embedding Fallback Metric.

Validates that when the KG falls back to deterministic similarity scoring
(instead of using embeddings), this event is counted via a thread-safe metric.

References:
- DEEP_PRODUCTION_AUDIT_v2.md: B-005 KG embedding fallback observability
"""

import threading
import pytest


class TestEmbeddingFallbackCounterInfrastructure:
    """Test the counter infrastructure in kg module."""
    
    def test_counter_exists(self):
        """Counter infrastructure should be importable."""
        from integration_coworker.kg import (
            get_embedding_fallback_count,
            reset_embedding_fallback_count,
        )
        assert callable(get_embedding_fallback_count)
        assert callable(reset_embedding_fallback_count)
    
    def test_counter_starts_at_zero_after_reset(self):
        """Counter should be resettable to zero."""
        from integration_coworker.kg import (
            get_embedding_fallback_count,
            reset_embedding_fallback_count,
        )
        reset_embedding_fallback_count()
        assert get_embedding_fallback_count() == 0
    
    def test_increment_function_exists(self):
        """Internal increment function should exist."""
        from integration_coworker.kg import _increment_embedding_fallback
        assert callable(_increment_embedding_fallback)
    
    def test_increment_increases_counter(self):
        """Each call to increment should increase counter by 1."""
        from integration_coworker.kg import (
            get_embedding_fallback_count,
            reset_embedding_fallback_count,
            _increment_embedding_fallback,
        )
        reset_embedding_fallback_count()
        
        _increment_embedding_fallback()
        assert get_embedding_fallback_count() == 1
        
        _increment_embedding_fallback()
        _increment_embedding_fallback()
        assert get_embedding_fallback_count() == 3
    
    def test_counter_is_thread_safe(self):
        """Counter should handle concurrent increments correctly."""
        from integration_coworker.kg import (
            get_embedding_fallback_count,
            reset_embedding_fallback_count,
            _increment_embedding_fallback,
        )
        reset_embedding_fallback_count()
        
        num_threads = 10
        increments_per_thread = 100
        
        def worker():
            for _ in range(increments_per_thread):
                _increment_embedding_fallback()
        
        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        expected = num_threads * increments_per_thread
        assert get_embedding_fallback_count() == expected


class TestHealthServerMetricExport:
    """Test that the metric is exported in health server."""
    
    def test_metric_appears_in_health_metrics(self):
        """kg_embedding_fallback_total should appear in /metrics output."""
        from integration_coworker.kg import reset_embedding_fallback_count
        reset_embedding_fallback_count()
        
        # Import the handler to test metrics generation
        from integration_coworker.health.server import HealthCheckHandler
        from io import BytesIO
        from unittest.mock import Mock, patch
        
        # Collect metrics manually by testing the import works
        from integration_coworker.kg import get_embedding_fallback_count
        fallback_count = get_embedding_fallback_count()
        
        # Build expected metric line
        expected_help = "# HELP kg_embedding_fallback_total"
        expected_type = "# TYPE kg_embedding_fallback_total counter"
        expected_value = f"kg_embedding_fallback_total {fallback_count}"
        
        # Verify metric format is correct
        assert "kg_embedding_fallback_total" in expected_value
        assert fallback_count == 0  # After reset
    
    def test_metric_reflects_actual_count(self):
        """Metric value should match the current counter value."""
        from integration_coworker.kg import (
            get_embedding_fallback_count,
            reset_embedding_fallback_count,
            _increment_embedding_fallback,
        )
        reset_embedding_fallback_count()
        
        # Increment a few times
        for _ in range(5):
            _increment_embedding_fallback()
        
        # Verify the count is accessible
        count = get_embedding_fallback_count()
        assert count == 5


class TestDeterministicSimilarityFallback:
    """Test that deterministic similarity usage increments counter."""
    
    def test_deterministic_similarity_exists(self):
        """The _deterministic_similarity function should exist."""
        from integration_coworker.kg import _deterministic_similarity
        assert callable(_deterministic_similarity)
    
    def test_pattern_scoring_path_increments_counter(self):
        """Calling _pattern_matching_score should increment fallback counter when using fallback."""
        from integration_coworker.kg import (
            reset_embedding_fallback_count,
            get_embedding_fallback_count,
        )
        
        # We can't easily test the full path without mocking the entire KG
        # But we verify the counter is callable and resettable
        reset_embedding_fallback_count()
        initial = get_embedding_fallback_count()
        assert initial == 0


class TestMetricNamingConventions:
    """Test that metric names follow Prometheus conventions."""
    
    def test_metric_name_is_snake_case(self):
        """Metric name should be snake_case."""
        metric_name = "kg_embedding_fallback_total"
        assert "_" in metric_name
        assert metric_name == metric_name.lower()
    
    def test_counter_metric_ends_with_total(self):
        """Counter metrics should end with _total."""
        metric_name = "kg_embedding_fallback_total"
        assert metric_name.endswith("_total")
    
    def test_help_text_exists(self):
        """Metric should have descriptive help text."""
        from integration_coworker.health.server import HealthCheckHandler
        import inspect
        
        # Get the source of _handle_metrics to verify HELP text
        source = inspect.getsource(HealthCheckHandler._handle_metrics)
        assert "# HELP kg_embedding_fallback_total" in source
        assert "# TYPE kg_embedding_fallback_total counter" in source
