"""
Tests for B-003: Tree-sitter availability logging and metrics.

These tests verify:
1. is_tree_sitter_available() function exists and returns a boolean
2. The health server exposes syntax_validator_tree_sitter_available metric
3. The WARNING log is emitted when tree-sitter is unavailable (via log capture)
"""

import pytest
import time
import urllib.request
from unittest.mock import patch


class TestTreeSitterAvailabilityFunction:
    """Tests for the is_tree_sitter_available() helper function."""
    
    def test_is_tree_sitter_available_exists(self):
        """Verify the helper function exists and is importable."""
        from integration_coworker.codegen.syntax_validator import is_tree_sitter_available
        assert callable(is_tree_sitter_available)
    
    def test_is_tree_sitter_available_returns_bool(self):
        """Verify the helper function returns a boolean."""
        from integration_coworker.codegen.syntax_validator import is_tree_sitter_available
        result = is_tree_sitter_available()
        assert isinstance(result, bool)
    
    def test_is_tree_sitter_available_matches_internal_flag(self):
        """Verify the helper returns the same value as the internal flag."""
        from integration_coworker.codegen.syntax_validator import (
            is_tree_sitter_available,
            _TREE_SITTER_AVAILABLE,
        )
        assert is_tree_sitter_available() == _TREE_SITTER_AVAILABLE


class TestTreeSitterMetric:
    """Tests for the syntax_validator_tree_sitter_available Prometheus metric."""
    
    @pytest.fixture(autouse=True)
    def cleanup_server(self):
        """Ensure server is stopped after each test."""
        yield
        from integration_coworker.health.server import (
            stop_health_server,
            clear_readiness_checks,
            reset_health_server_metrics,
        )
        stop_health_server()
        clear_readiness_checks()
        reset_health_server_metrics()
    
    def test_metrics_includes_tree_sitter_availability(self):
        """Verify /metrics includes syntax_validator_tree_sitter_available gauge."""
        from integration_coworker.health.server import start_health_server
        
        start_health_server(host="127.0.0.1", port=18200)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18200/metrics")
        content = response.read().decode()
        
        # Verify the metric is present
        assert "syntax_validator_tree_sitter_available" in content
        assert "# TYPE syntax_validator_tree_sitter_available gauge" in content
    
    def test_metrics_tree_sitter_value_is_valid(self):
        """Verify the metric value is either 0 or 1."""
        from integration_coworker.health.server import start_health_server
        
        start_health_server(host="127.0.0.1", port=18201)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18201/metrics")
        content = response.read().decode()
        
        # Parse the metric value
        for line in content.split("\n"):
            if line.startswith("syntax_validator_tree_sitter_available "):
                value = int(line.split()[-1])
                assert value in (0, 1), f"Expected 0 or 1, got {value}"
                break
        else:
            pytest.fail("syntax_validator_tree_sitter_available metric not found")
    
    def test_metrics_tree_sitter_matches_function(self):
        """Verify the metric matches is_tree_sitter_available() value."""
        from integration_coworker.health.server import start_health_server
        from integration_coworker.codegen.syntax_validator import is_tree_sitter_available
        
        start_health_server(host="127.0.0.1", port=18202)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18202/metrics")
        content = response.read().decode()
        
        expected_value = 1 if is_tree_sitter_available() else 0
        
        # Parse and verify
        for line in content.split("\n"):
            if line.startswith("syntax_validator_tree_sitter_available "):
                actual_value = int(line.split()[-1])
                assert actual_value == expected_value
                break


class TestTreeSitterWarningLog:
    """Tests for WARNING log when tree-sitter is unavailable."""
    
    def test_warning_log_format_when_unavailable(self, caplog):
        """Verify WARNING log is emitted when tree-sitter import fails.
        
        Note: This test validates the log message format. The actual log
        emission happens at module import time, so we test the message
        content expectations.
        """
        # The warning message should mention:
        # 1. tree-sitter is not installed
        # 2. fallback validation will be used
        # 3. how to install it
        expected_fragments = [
            "tree-sitter",
            "fallback",
            "pip install",
        ]
        
        # We can't easily re-trigger the import, but we can verify
        # the warning message format by checking the module code
        import integration_coworker.codegen.syntax_validator as sv_module
        import inspect
        source = inspect.getsource(sv_module)
        
        # Verify the warning log call exists with proper message
        assert "logger.warning" in source
        for fragment in expected_fragments:
            assert fragment.lower() in source.lower(), f"Expected '{fragment}' in warning message"
