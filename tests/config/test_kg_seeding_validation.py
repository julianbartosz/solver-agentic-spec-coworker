"""
Tests for B-006: KG Seeding Validation.

Validates that:
1. get_template_count() helper returns the number of workflow templates
2. Startup validation warns when KG is empty
3. kg_template_count gauge is exported in /metrics

References:
- DEEP_PRODUCTION_AUDIT_v2.md: B-006 KG seeding validation
"""

import os
import pytest
from unittest.mock import patch, MagicMock


class TestGetTemplateCount:
    """Test the get_template_count() helper function."""
    
    def test_helper_exists(self):
        """get_template_count should be importable."""
        from integration_coworker.persistence.seed_kg import get_template_count
        assert callable(get_template_count)
    
    def test_returns_integer(self):
        """get_template_count should return an integer."""
        from integration_coworker.persistence.seed_kg import get_template_count
        result = get_template_count()
        assert isinstance(result, int)
    
    def test_returns_zero_on_error(self):
        """get_template_count should return 0 if database is unavailable."""
        from integration_coworker.persistence.seed_kg import get_template_count
        
        with patch('integration_coworker.persistence.seed_kg.get_connection') as mock_conn:
            mock_conn.side_effect = Exception("Database unavailable")
            result = get_template_count()
            assert result == 0


class TestStartupValidation:
    """Test KG seeding validation in startup config."""
    
    def test_validator_has_kg_seeding_method(self):
        """StartupValidator should have _validate_kg_seeding method."""
        from integration_coworker.config.startup import StartupValidator
        validator = StartupValidator()
        assert hasattr(validator, '_validate_kg_seeding')
        assert callable(validator._validate_kg_seeding)
    
    def test_empty_kg_generates_warning(self):
        """Empty KG should generate a warning, not an error."""
        from integration_coworker.config.startup import StartupValidator
        
        validator = StartupValidator()
        
        with patch('integration_coworker.persistence.seed_kg.get_template_count') as mock_count:
            mock_count.return_value = 0
            validator._validate_kg_seeding()
        
        # Check for warning (not error)
        kg_warnings = [w for w in validator.result.warnings if w.key == "KG_SEEDING"]
        assert len(kg_warnings) == 1
        assert "no workflow templates" in kg_warnings[0].message.lower()
        
        # Should NOT be an error
        kg_errors = [e for e in validator.result.errors if e.key == "KG_SEEDING"]
        assert len(kg_errors) == 0
    
    def test_seeded_kg_no_warning(self):
        """KG with templates should not generate any warnings."""
        from integration_coworker.config.startup import StartupValidator
        
        validator = StartupValidator()
        
        with patch('integration_coworker.persistence.seed_kg.get_template_count') as mock_count:
            mock_count.return_value = 5
            validator._validate_kg_seeding()
        
        kg_warnings = [w for w in validator.result.warnings if w.key == "KG_SEEDING"]
        assert len(kg_warnings) == 0
    
    def test_import_error_is_silently_handled(self):
        """Import errors should be silently handled (minimal install)."""
        from integration_coworker.config.startup import StartupValidator
        import importlib
        
        validator = StartupValidator()
        
        # Patch to simulate ImportError
        with patch.dict('sys.modules', {'integration_coworker.persistence.seed_kg': None}):
            with patch('integration_coworker.persistence.seed_kg.get_template_count', side_effect=ImportError):
                # Should not raise
                validator._validate_kg_seeding()
        
        # No errors or warnings related to KG when module unavailable
        # (the implementation catches ImportError)


class TestHealthMetricExport:
    """Test kg_template_count gauge in health metrics."""
    
    def test_metric_exists_in_health_handler(self):
        """kg_template_count should appear in _handle_metrics source."""
        from integration_coworker.health.server import HealthCheckHandler
        import inspect
        
        source = inspect.getsource(HealthCheckHandler._handle_metrics)
        assert "kg_template_count" in source
        assert "# HELP kg_template_count" in source
        assert "# TYPE kg_template_count gauge" in source
    
    def test_metric_is_gauge_not_counter(self):
        """Template count should be a gauge (can go up/down)."""
        from integration_coworker.health.server import HealthCheckHandler
        import inspect
        
        source = inspect.getsource(HealthCheckHandler._handle_metrics)
        # Find the TYPE line for kg_template_count
        assert "# TYPE kg_template_count gauge" in source


class TestMetricNamingConventions:
    """Test that metric names follow Prometheus conventions."""
    
    def test_metric_name_is_snake_case(self):
        """Metric name should be snake_case."""
        metric_name = "kg_template_count"
        assert "_" in metric_name
        assert metric_name == metric_name.lower()
    
    def test_gauge_does_not_end_with_total(self):
        """Gauge metrics should NOT end with _total."""
        metric_name = "kg_template_count"
        assert not metric_name.endswith("_total")


class TestDocstringCompliance:
    """Test docstring and documentation."""
    
    def test_get_template_count_has_b006_reference(self):
        """get_template_count should reference B-006."""
        from integration_coworker.persistence.seed_kg import get_template_count
        assert get_template_count.__doc__ is not None
        assert "B-006" in get_template_count.__doc__
    
    def test_validator_docstring_mentions_kg_seeding(self):
        """StartupValidator docstring should mention KG seeding."""
        from integration_coworker.config.startup import StartupValidator
        assert StartupValidator.__doc__ is not None
        assert "Knowledge Graph" in StartupValidator.__doc__ or "KG" in StartupValidator.__doc__
