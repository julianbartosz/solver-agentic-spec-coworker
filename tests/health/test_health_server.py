"""
Health Server Tests (Production Hardening C-4)

Tests for the HTTP health server implementation.
"""

import json
import pytest
import threading
import time
import urllib.request
from unittest.mock import patch

from integration_coworker.health.server import (
    start_health_server,
    stop_health_server,
    is_health_server_running,
    get_health_server_address,
    register_readiness_check,
    unregister_readiness_check,
    clear_readiness_checks,
    get_health_server_config,
    reset_health_server_metrics,
)


# Allow socket access for health server tests
pytestmark = pytest.mark.enable_socket


@pytest.fixture(autouse=True)
def enable_socket_for_test():
    """Enable sockets for health server tests."""
    try:
        from pytest_socket import enable_socket
        enable_socket()
    except ImportError:
        pass
    yield


@pytest.fixture(autouse=True)
def cleanup_server():
    """Ensure server is stopped and state reset after each test."""
    yield
    stop_health_server()
    clear_readiness_checks()
    reset_health_server_metrics()


class TestHealthServerBasics:
    """Basic health server functionality tests."""

    def test_server_starts_and_stops(self):
        """Test that server can start and stop."""
        # Use a high port to avoid conflicts
        start_health_server(host="127.0.0.1", port=18080)
        assert is_health_server_running() is True
        
        stop_health_server()
        assert is_health_server_running() is False

    def test_server_address(self):
        """Test that server reports correct address."""
        start_health_server(host="127.0.0.1", port=18081)
        
        addr = get_health_server_address()
        assert addr == ("127.0.0.1", 18081)

    def test_server_already_running(self):
        """Test that starting server twice doesn't raise."""
        start_health_server(host="127.0.0.1", port=18082)
        start_health_server(host="127.0.0.1", port=18082)  # Should not raise
        assert is_health_server_running() is True

    def test_config_from_env(self):
        """Test configuration from environment variables."""
        with patch.dict("os.environ", {
            "HEALTH_SERVER_HOST": "0.0.0.0",
            "HEALTH_SERVER_PORT": "9090",
            "HEALTH_SERVER_TIMEOUT": "10.0",
        }):
            config = get_health_server_config()
            assert config["host"] == "0.0.0.0"
            assert config["port"] == 9090
            assert config["timeout"] == 10.0

    def test_config_defaults(self):
        """Test default configuration values."""
        with patch.dict("os.environ", {}, clear=True):
            config = get_health_server_config()
            assert config["host"] == "127.0.0.1"
            assert config["port"] == 8080
            assert config["timeout"] == 5.0


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_returns_200(self):
        """Test that /health returns 200."""
        start_health_server(host="127.0.0.1", port=18083)
        time.sleep(0.1)  # Wait for server to start
        
        response = urllib.request.urlopen("http://127.0.0.1:18083/health")
        assert response.status == 200
        
        data = json.loads(response.read().decode())
        assert data["status"] == "healthy"
        assert "timestamp" in data

    def test_healthz_alias(self):
        """Test that /healthz endpoint works (Kubernetes standard)."""
        start_health_server(host="127.0.0.1", port=18100)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18100/healthz")
        assert response.status == 200
        
        data = json.loads(response.read().decode())
        assert data["status"] == "healthy"

    def test_health_content_type(self):
        """Test that /health returns JSON content type."""
        start_health_server(host="127.0.0.1", port=18084)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18084/health")
        assert "application/json" in response.headers["Content-Type"]


class TestReadinessEndpoint:
    """Tests for /readiness endpoint."""

    def test_readiness_returns_200_when_no_checks(self):
        """Test that /readiness returns 200 when no checks registered."""
        start_health_server(host="127.0.0.1", port=18085)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18085/readiness")
        assert response.status == 200
        
        data = json.loads(response.read().decode())
        assert data["status"] == "ready"

    def test_readyz_alias(self):
        """Test that /readyz endpoint works (Kubernetes standard)."""
        start_health_server(host="127.0.0.1", port=18101)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18101/readyz")
        assert response.status == 200
        
        data = json.loads(response.read().decode())
        assert data["status"] == "ready"

    def test_readiness_returns_200_when_all_pass(self):
        """Test that /readiness returns 200 when all checks pass."""
        register_readiness_check("check1", lambda: True)
        register_readiness_check("check2", lambda: True)
        
        start_health_server(host="127.0.0.1", port=18086)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18086/readiness")
        assert response.status == 200
        
        data = json.loads(response.read().decode())
        assert data["status"] == "ready"
        assert "check1" in data["checks_passed"]
        assert "check2" in data["checks_passed"]

    def test_readiness_returns_503_when_check_fails(self):
        """Test that /readiness returns 503 when a check fails."""
        register_readiness_check("passing", lambda: True)
        register_readiness_check("failing", lambda: False)
        
        start_health_server(host="127.0.0.1", port=18087)
        time.sleep(0.1)
        
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen("http://127.0.0.1:18087/readiness")
        
        assert exc_info.value.code == 503
        data = json.loads(exc_info.value.read().decode())
        assert data["status"] == "not_ready"
        assert "passing" in data["checks_passed"]
        assert "failing" in data["checks_failed"]

    def test_readiness_handles_check_exception(self):
        """Test that /readiness handles check exceptions gracefully."""
        def failing_check():
            raise RuntimeError("Check failed!")
        
        register_readiness_check("error_check", failing_check)
        
        start_health_server(host="127.0.0.1", port=18088)
        time.sleep(0.1)
        
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen("http://127.0.0.1:18088/readiness")
        
        assert exc_info.value.code == 503
        data = json.loads(exc_info.value.read().decode())
        assert "error_check" in data["checks_failed"]


class TestMetricsEndpoint:
    """Tests for /metrics endpoint."""

    def test_metrics_returns_prometheus_format(self):
        """Test that /metrics returns Prometheus format."""
        register_readiness_check("db", lambda: True)
        
        start_health_server(host="127.0.0.1", port=18089)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18089/metrics")
        assert response.status == 200
        assert "text/plain" in response.headers["Content-Type"]
        
        content = response.read().decode()
        assert "health_server_up 1" in content
        assert "readiness_check_db 1" in content


class TestReadinessCheckManagement:
    """Tests for readiness check registration."""

    def test_register_and_unregister(self):
        """Test registering and unregistering checks."""
        register_readiness_check("test", lambda: True)
        register_readiness_check("test2", lambda: True)
        
        unregister_readiness_check("test")
        
        start_health_server(host="127.0.0.1", port=18090)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18090/readiness")
        data = json.loads(response.read().decode())
        
        assert "test2" in data["checks_passed"]
        assert "test" not in data["checks_passed"]

    def test_clear_readiness_checks(self):
        """Test clearing all checks."""
        register_readiness_check("test1", lambda: True)
        register_readiness_check("test2", lambda: True)
        
        clear_readiness_checks()
        
        start_health_server(host="127.0.0.1", port=18091)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18091/readiness")
        data = json.loads(response.read().decode())
        
        assert data["checks_passed"] == []


class TestNotFoundEndpoint:
    """Tests for unknown endpoints."""

    def test_unknown_path_returns_404(self):
        """Test that unknown paths return 404."""
        start_health_server(host="127.0.0.1", port=18092)
        time.sleep(0.1)
        
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen("http://127.0.0.1:18092/unknown")
        
        assert exc_info.value.code == 404


class TestSecurityDefaults:
    """Tests for security defaults."""

    def test_default_bind_localhost(self):
        """Test that default config binds to localhost."""
        with patch.dict("os.environ", {}, clear=True):
            config = get_health_server_config()
            assert config["host"] == "127.0.0.1"  # Not 0.0.0.0


class TestReadinessCheckTimeout:
    """Tests for timeout-bounded readiness checks."""

    def test_slow_check_times_out(self):
        """Test that slow readiness checks are timed out and marked as failed.
        
        This ensures /readyz doesn't hang if a dependency (e.g., database) stalls.
        """
        import threading
        
        slow_check_started = threading.Event()
        slow_check_done = threading.Event()
        
        def slow_check():
            slow_check_started.set()
            time.sleep(10)  # Longer than HEALTH_CHECK_TIMEOUT
            slow_check_done.set()
            return True
        
        register_readiness_check("slow_db", slow_check)
        register_readiness_check("fast_check", lambda: True)
        
        # Use 0.5s timeout for faster test
        with patch.dict("os.environ", {"HEALTH_CHECK_TIMEOUT": "0.5"}):
            start_health_server(host="127.0.0.1", port=18093)
            time.sleep(0.1)
            
            start_time = time.time()
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen("http://127.0.0.1:18093/readiness")
            elapsed = time.time() - start_time
            
            # Should complete quickly (not wait for slow_check)
            assert elapsed < 2.0, f"Request took too long: {elapsed}s"
            
            # Should return 503 with slow_db in failed checks
            assert exc_info.value.code == 503
            data = json.loads(exc_info.value.read().decode())
            assert "slow_db" in data["checks_failed"]
            assert "fast_check" in data["checks_passed"]
            
            # Check should have started but not completed
            assert slow_check_started.is_set()
            assert not slow_check_done.is_set()


class TestHealthServerMetrics:
    """Tests for /metrics endpoint with comprehensive observability."""

    def test_metrics_returns_prometheus_format(self):
        """Test that /metrics returns Prometheus-formatted metrics."""
        start_health_server(host="127.0.0.1", port=18094)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18094/metrics")
        content = response.read().decode()
        
        # Verify Prometheus format basics
        assert "# HELP" in content
        assert "# TYPE" in content
        
        # Core health server metrics must be present
        assert "health_server_up 1" in content
        assert "health_server_requests_total" in content
        assert "health_server_readiness_timeouts_total" in content
        assert "health_server_pool_saturations_total" in content

    def test_metrics_tracks_request_counts(self):
        """Test that /metrics tracks request counters."""
        from integration_coworker.health.server import reset_health_server_metrics
        reset_health_server_metrics()
        
        start_health_server(host="127.0.0.1", port=18095)
        time.sleep(0.1)
        
        # Make some requests
        urllib.request.urlopen("http://127.0.0.1:18095/healthz")
        urllib.request.urlopen("http://127.0.0.1:18095/healthz")
        urllib.request.urlopen("http://127.0.0.1:18095/readyz")
        
        # Check metrics
        response = urllib.request.urlopen("http://127.0.0.1:18095/metrics")
        content = response.read().decode()
        
        # Should show request counts (at least the ones we made + metrics calls)
        assert "health_server_health_checks_total 2" in content
        assert "health_server_readiness_checks_total 1" in content

    def test_metrics_includes_readiness_check_status(self):
        """Test that /metrics includes per-check status gauges."""
        from integration_coworker.health.server import reset_health_server_metrics
        reset_health_server_metrics()
        
        register_readiness_check("database", lambda: True)
        register_readiness_check("cache", lambda: False)
        
        start_health_server(host="127.0.0.1", port=18096)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18096/metrics")
        content = response.read().decode()
        
        # Per-check gauges
        assert "readiness_check_database 1" in content
        assert "readiness_check_cache 0" in content

    def test_metrics_golden_invariants(self):
        """Golden test: verify all expected metric names are present.
        
        This test ensures we don't accidentally remove metrics that
        operators may be alerting on. Add new metrics here when added.
        """
        start_health_server(host="127.0.0.1", port=18097)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18097/metrics")
        content = response.read().decode()
        
        # Golden list of required metrics (health server)
        golden_metrics = [
            "health_server_up",
            "health_server_requests_total",
            "health_server_health_checks_total",
            "health_server_readiness_checks_total",
            "health_server_pool_saturations_total",
            "health_server_readiness_timeouts_total",
            "health_server_readiness_failures_total",
            "health_server_request_rejections_total",
        ]
        
        for metric in golden_metrics:
            assert metric in content, f"Missing golden metric: {metric}"

    def test_metrics_tracks_readiness_timeouts(self):
        """Test that readiness check timeouts increment the counter."""
        from integration_coworker.health.server import (
            reset_health_server_metrics,
            get_health_server_metrics,
        )
        reset_health_server_metrics()
        
        def slow_check():
            time.sleep(10)
            return True
        
        register_readiness_check("slow_service", slow_check)
        
        with patch.dict("os.environ", {"HEALTH_CHECK_TIMEOUT": "0.2"}):
            start_health_server(host="127.0.0.1", port=18098)
            time.sleep(0.1)
            
            # This should timeout
            try:
                urllib.request.urlopen("http://127.0.0.1:18098/readyz")
            except urllib.error.HTTPError:
                pass  # Expected 503
            
            # Check the timeout counter
            metrics = get_health_server_metrics()
            assert metrics.readiness_timeouts >= 1

    def test_metrics_tracks_method_rejections(self):
        """Test that POST/PUT rejections are tracked."""
        from integration_coworker.health.server import (
            reset_health_server_metrics,
            get_health_server_metrics,
        )
        reset_health_server_metrics()
        
        start_health_server(host="127.0.0.1", port=18099)
        time.sleep(0.1)
        
        # Try POST (should be rejected)
        req = urllib.request.Request(
            "http://127.0.0.1:18099/healthz",
            data=b"",
            method="POST"
        )
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            assert e.code == 405
        
        # Check rejection counter
        metrics = get_health_server_metrics()
        assert metrics.request_rejections >= 1


class TestMetricsNamingConvention:
    """
    Tests for Prometheus metrics naming conventions (MUST rules only).
    
    Enforced (MUST):
    - Counter names MUST end with _total
    - Names MUST use snake_case (lowercase + underscores)
    
    Not enforced (SHOULD - guidance only):
    - Gauge names SHOULD NOT end with _total (warning logged, not failure)
    - Names SHOULD start with component prefix (not enforced - causes churn)
    
    Reference: https://prometheus.io/docs/practices/naming/
    """

    def test_counter_metrics_end_with_total(self):
        """All counter metrics MUST end with _total suffix.
        
        This test parses the /metrics output and validates that every line
        marked as '# TYPE ... counter' has a metric name ending in _total.
        
        This prevents issues where prometheus_client would auto-add _total,
        and ensures consistency with Prometheus naming best practices.
        """
        start_health_server(host="127.0.0.1", port=18100)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18100/metrics")
        content = response.read().decode()
        
        # Parse TYPE lines to find counters
        lines = content.split("\n")
        counter_names = []
        violations = []
        
        for line in lines:
            if line.startswith("# TYPE ") and " counter" in line:
                # Format: "# TYPE metric_name counter"
                parts = line.split()
                if len(parts) >= 4:
                    metric_name = parts[2]
                    counter_names.append(metric_name)
                    if not metric_name.endswith("_total"):
                        violations.append(metric_name)
        
        # Assert we found some counters (sanity check)
        assert len(counter_names) > 0, "No counter metrics found in /metrics output"
        
        # Assert no violations
        assert len(violations) == 0, (
            f"Counter metrics MUST end with _total suffix per Prometheus convention. "
            f"Violations: {violations}"
        )

    def test_metric_names_use_snake_case(self):
        """All metric names MUST use snake_case.
        
        Prometheus convention requires lowercase with underscores.
        CamelCase or kebab-case are not valid.
        """
        import re
        
        start_health_server(host="127.0.0.1", port=18102)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18102/metrics")
        content = response.read().decode()
        
        # Parse TYPE lines to get all metric names
        lines = content.split("\n")
        violations = []
        
        for line in lines:
            if line.startswith("# TYPE "):
                # Format: "# TYPE metric_name type"
                parts = line.split()
                if len(parts) >= 3:
                    metric_name = parts[2]
                    # Check for valid prometheus metric name: lowercase + underscores only
                    if not re.match(r'^[a-z][a-z0-9_]*$', metric_name):
                        violations.append(metric_name)
        
        assert len(violations) == 0, (
            f"Metric names MUST use snake_case (lowercase + underscores). "
            f"Violations: {violations}"
        )

    def test_phase3_metrics_have_help_and_type(self):
        """Phase 3 observability metrics MUST have proper HELP and TYPE declarations.
        
        Tests that all new metrics from B-005 through B-008 are properly exported
        with Prometheus text format compliance.
        """
        start_health_server(host="127.0.0.1", port=18103)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18103/metrics")
        content = response.read().decode()
        lines = content.split("\n")
        
        # Build sets of metrics that have HELP and TYPE
        help_metrics = set()
        type_metrics = set()
        
        for line in lines:
            if line.startswith("# HELP "):
                # Format: "# HELP metric_name description"
                parts = line.split()
                if len(parts) >= 3:
                    help_metrics.add(parts[2])
            elif line.startswith("# TYPE "):
                # Format: "# TYPE metric_name type"
                parts = line.split()
                if len(parts) >= 3:
                    type_metrics.add(parts[2])
        
        # Phase 3 metrics that MUST be present with HELP and TYPE
        required_metrics = [
            "kg_embedding_fallback_total",       # B-005
            "kg_template_count",                  # B-006
            "spec_conversion_llm_fallback_total", # B-007
            "db_connection_leak_detected_total",  # B-008
            "feedback_recording_failures_total",  # B-008
        ]
        
        missing_help = []
        missing_type = []
        
        for metric in required_metrics:
            if metric not in help_metrics:
                missing_help.append(metric)
            if metric not in type_metrics:
                missing_type.append(metric)
        
        assert len(missing_help) == 0, (
            f"Phase 3 metrics missing # HELP declaration: {missing_help}"
        )
        assert len(missing_type) == 0, (
            f"Phase 3 metrics missing # TYPE declaration: {missing_type}"
        )

    def test_labeled_metrics_have_valid_format(self):
        """Labeled metrics MUST use valid Prometheus label syntax.
        
        Tests that spec_conversion_llm_fallback_total uses proper label format:
        metric_name{label="value"} count
        """
        import re
        
        start_health_server(host="127.0.0.1", port=18104)
        time.sleep(0.1)
        
        response = urllib.request.urlopen("http://127.0.0.1:18104/metrics")
        content = response.read().decode()
        
        # Find all lines with labels (contain {})
        labeled_lines = [line for line in content.split("\n") 
                         if "{" in line and not line.startswith("#")]
        
        # Validate label format: metric_name{label="value"} number
        # Prometheus label format regex
        label_pattern = re.compile(
            r'^[a-z][a-z0-9_]*\{[a-z_]+="[^"]*"\}\s+\d+(\.\d+)?$'
        )
        
        invalid_labels = []
        for line in labeled_lines:
            if not label_pattern.match(line):
                invalid_labels.append(line)
        
        assert len(invalid_labels) == 0, (
            f"Invalid Prometheus label format in metrics:\n" +
            "\n".join(invalid_labels)
        )
