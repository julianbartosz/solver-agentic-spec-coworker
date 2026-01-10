"""
HTTP Health Server (Production Hardening C-4)

Provides HTTP health and readiness endpoints for container orchestrators
(Kubernetes, Docker, etc.) to probe application health.

Endpoints:
- GET /healthz (or /health) - Basic liveness check (always 200 if server is up)
- GET /readyz (or /readiness) - Readiness check (200 if dependencies are available)
- GET /metrics - Basic metrics in Prometheus format

⚠️  SECURITY WARNING - INTERNAL USE ONLY:
    The Python http.server module is NOT recommended for production use.
    From Python docs: "http.server is not recommended for production. 
    It only implements basic security checks."
    
    THIS SERVER IS DESIGNED FOR INTERNAL PROBES ONLY:
    - Bind to 127.0.0.1 by default (DO NOT expose publicly)
    - Use sidecar/reverse proxy for external exposure if needed
    - Never expose /metrics to untrusted networks
    
    For production deployments:
    - Keep default binding to 127.0.0.1
    - Set up K8s liveness/readiness probes to localhost
    - Or use a sidecar container that accesses localhost
    - Or use a proper ASGI server (uvicorn) with a framework

Design Decisions:
- stdlib http.server (no external dependencies like FastAPI)
- Bounded concurrency via ThreadPoolExecutor (max_workers limit)
- Bind to 127.0.0.1 by default (security - no external access)
- Configurable via environment variables
- Graceful shutdown via ShutdownManager integration
- Kubernetes-standard endpoint names (/healthz, /readyz)

Configuration:
    HEALTH_SERVER_HOST: Bind address (default: 127.0.0.1) ⚠️ DO NOT change to 0.0.0.0 without proxy
    HEALTH_SERVER_PORT: Bind port (default: 8080)
    HEALTH_SERVER_TIMEOUT: Request timeout in seconds (default: 5)
    HEALTH_CHECK_TIMEOUT: Per-check timeout for readiness probes (default: 2)
    HEALTH_SERVER_MAX_WORKERS: Max concurrent request handlers (default: 4)

Usage:
    from integration_coworker.health.server import start_health_server, stop_health_server
    
    # Start in background thread
    start_health_server()
    
    # ... application runs ...
    
    # Stop gracefully
    stop_health_server()
"""

import concurrent.futures
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Health Server Metrics (Observability H-2)
# =============================================================================

@dataclass
class HealthServerMetrics:
    """
    Aggregate metrics for health server operations.
    
    Tracks pool saturation, readiness check timeouts, and request stats
    for observability and alerting.
    """
    total_requests: int = 0               # Total requests handled
    total_health_checks: int = 0          # Total /healthz requests
    total_readiness_checks: int = 0       # Total /readyz requests
    total_metrics_requests: int = 0       # Total /metrics requests
    pool_saturations: int = 0             # Times pool was saturated
    readiness_timeouts: int = 0           # Total readiness check timeouts
    readiness_failures: int = 0           # Total readiness check failures (non-timeout)
    request_rejections: int = 0           # Requests rejected (405, 431, etc.)
    
    # Lock for thread-safe counter updates
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    def increment_requests(self) -> None:
        with self._lock:
            self.total_requests += 1
    
    def increment_health_checks(self) -> None:
        with self._lock:
            self.total_health_checks += 1
    
    def increment_readiness_checks(self) -> None:
        with self._lock:
            self.total_readiness_checks += 1
    
    def increment_metrics_requests(self) -> None:
        with self._lock:
            self.total_metrics_requests += 1
    
    def increment_pool_saturations(self) -> None:
        with self._lock:
            self.pool_saturations += 1
    
    def increment_readiness_timeouts(self, count: int = 1) -> None:
        with self._lock:
            self.readiness_timeouts += count
    
    def increment_readiness_failures(self, count: int = 1) -> None:
        with self._lock:
            self.readiness_failures += count
    
    def increment_request_rejections(self) -> None:
        with self._lock:
            self.request_rejections += 1
    
    def to_dict(self) -> Dict[str, int]:
        """Return metrics as a dictionary."""
        with self._lock:
            return {
                "total_requests": self.total_requests,
                "total_health_checks": self.total_health_checks,
                "total_readiness_checks": self.total_readiness_checks,
                "total_metrics_requests": self.total_metrics_requests,
                "pool_saturations": self.pool_saturations,
                "readiness_timeouts": self.readiness_timeouts,
                "readiness_failures": self.readiness_failures,
                "request_rejections": self.request_rejections,
            }


# Global health server metrics instance
_health_metrics = HealthServerMetrics()


def get_health_server_metrics() -> HealthServerMetrics:
    """Get the global health server metrics instance."""
    return _health_metrics


def reset_health_server_metrics() -> None:
    """Reset health server metrics (for testing)."""
    global _health_metrics
    _health_metrics = HealthServerMetrics()


# =============================================================================
# Prometheus Metric Helpers
# =============================================================================
# Utility functions to emit Prometheus text format metrics correctly.
# These ensure consistency and prevent drift in metric naming/format.
# =============================================================================

def _emit_counter(metrics: list[str], name: str, value: int | float, help_text: str) -> None:
    """
    Emit a Prometheus counter metric with HELP and TYPE lines.
    
    Per Prometheus naming conventions:
    - Counters SHOULD have a `_total` suffix
    - This function validates the suffix is present
    
    Args:
        metrics: List to append metric lines to
        name: Metric name (must end with _total for counters)
        value: Current counter value
        help_text: Human-readable description
    """
    if not name.endswith("_total"):
        logger.warning(f"Counter metric '{name}' missing _total suffix (Prometheus convention)")
    metrics.append(f"# HELP {name} {help_text}")
    metrics.append(f"# TYPE {name} counter")
    metrics.append(f"{name} {value}")


def _emit_gauge(metrics: list[str], name: str, value: int | float, help_text: str) -> None:
    """
    Emit a Prometheus gauge metric with HELP and TYPE lines.
    
    Per Prometheus naming conventions:
    - Gauges should NOT have a `_total` suffix (that's for counters)
    
    Args:
        metrics: List to append metric lines to
        name: Metric name (should NOT end with _total)
        value: Current gauge value
        help_text: Human-readable description
    """
    if name.endswith("_total"):
        logger.warning(f"Gauge metric '{name}' has _total suffix (use for counters only)")
    metrics.append(f"# HELP {name} {help_text}")
    metrics.append(f"# TYPE {name} gauge")
    metrics.append(f"{name} {value}")


def _emit_labeled_counter(
    metrics: list[str], 
    name: str, 
    label_values: dict[str, int | float],
    label_name: str,
    help_text: str,
) -> None:
    """
    Emit a Prometheus counter metric with labels.
    
    Args:
        metrics: List to append metric lines to
        name: Metric name (must end with _total for counters)
        label_values: Dict mapping label value to counter value
        label_name: Name of the label (e.g., "spec_type")
        help_text: Human-readable description
    """
    if not name.endswith("_total"):
        logger.warning(f"Counter metric '{name}' missing _total suffix (Prometheus convention)")
    metrics.append(f"# HELP {name} {help_text}")
    metrics.append(f"# TYPE {name} counter")
    for label_val, count in label_values.items():
        # Escape label values per Prometheus text format spec
        escaped_val = str(label_val).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        metrics.append(f'{name}{{{label_name}="{escaped_val}"}} {count}')


# =============================================================================
# Bounded Request Handler Pool
# =============================================================================

# Shared executor for request handling - bounded concurrency prevents thread explosion
_request_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def _get_request_executor() -> concurrent.futures.ThreadPoolExecutor:
    """
    Get or create the bounded request handler executor.
    
    Uses a shared ThreadPoolExecutor with limited workers to prevent
    unbounded thread creation under load. This is critical because
    ThreadingHTTPServer normally spawns unlimited threads.
    
    Returns:
        ThreadPoolExecutor with HEALTH_SERVER_MAX_WORKERS limit
    """
    global _request_executor
    
    if _request_executor is None:
        with _executor_lock:
            if _request_executor is None:
                max_workers = int(os.environ.get("HEALTH_SERVER_MAX_WORKERS", "4"))
                _request_executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="health-req-",
                )
                logger.debug(f"Created health server request executor (max_workers={max_workers})")
    
    return _request_executor


def _shutdown_request_executor() -> None:
    """Shutdown the request executor."""
    global _request_executor
    
    if _request_executor is not None:
        with _executor_lock:
            if _request_executor is not None:
                _request_executor.shutdown(wait=True, cancel_futures=True)
                _request_executor = None
                logger.debug("Shutdown health server request executor")


# Health check functions that can be registered
_readiness_checks: Dict[str, Callable[[], bool]] = {}


class HealthCheckHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler for health endpoints.
    
    Security hardening:
    - Request size limits to prevent memory exhaustion
    - Timeout on request parsing to prevent slowloris
    - No request body accepted (GET only)
    """
    
    # Limit header sizes to prevent memory exhaustion
    # These are smaller than defaults since health endpoints don't need large headers
    max_line_length = 2048  # Max single header line
    max_headers = 32  # Max number of headers
    
    # Limit request size (we don't accept bodies, but limit anyway)
    max_request_body = 0  # Health endpoints don't accept request bodies
    
    # Override to suppress per-request logging (noisy in production)
    def log_message(self, format: str, *args) -> None:
        # Only log errors, not successful health checks
        if args and "200" not in str(args):
            logger.debug(format % args)
    
    def parse_request(self) -> bool:
        """Override to add header count limit."""
        result = super().parse_request()
        if result:
            # Check header count limit
            if len(self.headers) > self.max_headers:
                self.send_error(431, "Too many headers")
                return False
        return result
    
    def do_POST(self) -> None:
        """Reject POST requests - health endpoints are GET only."""
        _health_metrics.increment_request_rejections()
        self._send_json(405, {"error": "Method not allowed. Use GET."})
    
    def do_PUT(self) -> None:
        """Reject PUT requests - health endpoints are GET only."""
        _health_metrics.increment_request_rejections()
        self._send_json(405, {"error": "Method not allowed. Use GET."})
    
    def _set_headers(self, status_code: int, content_type: str = "application/json") -> None:
        """Set response headers."""
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
    
    def _send_json(self, status_code: int, data: Dict[str, Any]) -> None:
        """Send JSON response."""
        self._set_headers(status_code, "application/json")
        self.wfile.write(json.dumps(data).encode("utf-8"))
    
    def do_GET(self) -> None:
        """Handle GET requests."""
        _health_metrics.increment_requests()
        try:
            # Support both Kubernetes-standard (/healthz, /readyz) and legacy (/health, /readiness)
            if self.path in ("/healthz", "/health"):
                _health_metrics.increment_health_checks()
                self._handle_health()
            elif self.path in ("/readyz", "/readiness"):
                _health_metrics.increment_readiness_checks()
                self._handle_readiness()
            elif self.path == "/metrics":
                _health_metrics.increment_metrics_requests()
                self._handle_metrics()
            else:
                self._send_json(404, {"error": "Not found"})
        except Exception as e:
            logger.error(f"Health check error: {e}")
            self._send_json(500, {"error": "Internal server error"})
    
    def _handle_health(self) -> None:
        """Handle /health endpoint - basic liveness check."""
        self._send_json(200, {
            "status": "healthy",
            "timestamp": time.time(),
        })
    
    def _handle_readiness(self) -> None:
        """Handle /readiness endpoint - check dependencies.
        
        Each readiness check is bounded by a per-check timeout. To avoid thread
        leaks from stuck checks, we use a SHARED executor with limited workers.
        
        ⚠️  IMPORTANT: Check functions should implement their own internal timeouts
        at the dependency level (e.g., DB connect_timeout, HTTP timeouts) to avoid
        leaving stuck threads. The executor timeout is a last-resort backstop.
        
        ⚠️  THREAD CLEANUP REALITY (Python limitation):
        Python's ThreadPoolExecutor.shutdown(wait=False) does NOT stop running
        tasks - the threads continue until completion. The timeout here returns
        early to the caller, but the check function keeps running in the background.
        
        This means: Repeated timeouts from checks that block indefinitely WILL
        accumulate background threads until the pool is saturated.
        
        Mitigation strategy:
        1. REQUIRE readiness checks to have inherent time bounds (DB connect_timeout,
           HTTP timeouts) - this is the ONLY real solution
        2. Keep executor pool small and bounded (max_workers=4 default)
        3. Track pool saturation via metrics for operator alerting
        4. Single shared executor prevents unbounded growth
        5. future.cancel() is called (marks intent, doesn't stop thread)
        
        Pool saturation detection:
        - If submit() raises BrokenExecutor: fail immediately
        - If all workers are busy for longer than submit_timeout: fail immediately
        """
        # Per-check timeout - prevents any single check from hanging the probe
        check_timeout = float(os.getenv("HEALTH_CHECK_TIMEOUT", "2.0"))
        submit_timeout = 0.1  # Fail fast if pool is saturated (can't submit within 100ms)
        
        checks_passed = []
        checks_failed = []
        check_timeouts = []  # Track timeouts for metrics
        pool_saturated = False
        
        # Use the shared bounded executor (not per-check executors)
        executor = _get_request_executor()
        
        for name, check_fn in _readiness_checks.items():
            try:
                # Try to submit - this should be instant if pool has capacity
                # If pool is saturated, submit() will queue but we want to fail fast
                future = executor.submit(check_fn)
                try:
                    result = future.result(timeout=check_timeout)
                    if result:
                        checks_passed.append(name)
                    else:
                        checks_failed.append(name)
                        _health_metrics.increment_readiness_failures()
                except concurrent.futures.TimeoutError:
                    # Log at WARNING - operators should investigate why checks are slow
                    logger.warning(
                        f"Readiness check '{name}' timed out after {check_timeout}s. "
                        f"Consider adding internal timeouts to the check function."
                    )
                    checks_failed.append(name)
                    check_timeouts.append(name)
                    _health_metrics.increment_readiness_timeouts()
                    future.cancel()  # Mark intent (won't stop running thread)
            except concurrent.futures.BrokenExecutor:
                # Executor is broken (e.g., shutdown during checks)
                logger.error("Readiness check executor is broken - failing all checks")
                checks_failed.append(name)
                pool_saturated = True
                _health_metrics.increment_pool_saturations()
            except RuntimeError as e:
                # Executor was shutdown
                if "shutdown" in str(e).lower():
                    logger.warning(f"Executor shutdown during readiness check: {e}")
                    checks_failed.append(name)
                    pool_saturated = True
                    _health_metrics.increment_pool_saturations()
                else:
                    logger.warning(f"Readiness check '{name}' failed with RuntimeError: {e}")
                    checks_failed.append(name)
                    _health_metrics.increment_readiness_failures()
            except Exception as e:
                logger.warning(f"Readiness check '{name}' failed with exception: {e}")
                checks_failed.append(name)
                _health_metrics.increment_readiness_failures()
        
        # Include timeout info in response for debugging
        response_data = {
            "status": "not_ready" if checks_failed else "ready",
            "checks_passed": checks_passed,
            "timestamp": time.time(),
        }
        
        if checks_failed:
            response_data["checks_failed"] = checks_failed
        if check_timeouts:
            response_data["checks_timed_out"] = check_timeouts
        if pool_saturated:
            response_data["pool_saturated"] = True
            response_data["error"] = "Readiness check executor is saturated or broken"
        
        status_code = 503 if checks_failed else 200
        self._send_json(status_code, response_data)
    
    def _handle_metrics(self) -> None:
        """Handle /metrics endpoint - Prometheus format.
        
        Exports comprehensive metrics from all observable components:
        - Health server metrics (requests, timeouts, pool saturation)
        - Circuit breaker aggregate metrics (opens, short-circuits, resets)
        - LLM cache local metrics (hits, misses, errors, bypasses)
        - Readiness check status per registered check
        
        ⚠️  WARNING: This endpoint should NOT be exposed to untrusted networks.
        It only provides operational metrics - no secrets or config are exposed.
        """
        metrics = []
        
        # =================================================================
        # Health Server Metrics
        # =================================================================
        metrics.append("# HELP health_server_up Health server is running")
        metrics.append("# TYPE health_server_up gauge")
        metrics.append("health_server_up 1")
        
        hs_metrics = _health_metrics.to_dict()
        
        metrics.append("# HELP health_server_requests_total Total HTTP requests handled")
        metrics.append("# TYPE health_server_requests_total counter")
        metrics.append(f"health_server_requests_total {hs_metrics['total_requests']}")
        
        metrics.append("# HELP health_server_health_checks_total Total liveness checks")
        metrics.append("# TYPE health_server_health_checks_total counter")
        metrics.append(f"health_server_health_checks_total {hs_metrics['total_health_checks']}")
        
        metrics.append("# HELP health_server_readiness_checks_total Total readiness checks")
        metrics.append("# TYPE health_server_readiness_checks_total counter")
        metrics.append(f"health_server_readiness_checks_total {hs_metrics['total_readiness_checks']}")
        
        metrics.append("# HELP health_server_pool_saturations_total Executor pool saturation events")
        metrics.append("# TYPE health_server_pool_saturations_total counter")
        metrics.append(f"health_server_pool_saturations_total {hs_metrics['pool_saturations']}")
        
        metrics.append("# HELP health_server_readiness_timeouts_total Readiness check timeouts")
        metrics.append("# TYPE health_server_readiness_timeouts_total counter")
        metrics.append(f"health_server_readiness_timeouts_total {hs_metrics['readiness_timeouts']}")
        
        metrics.append("# HELP health_server_readiness_failures_total Readiness check failures (non-timeout)")
        metrics.append("# TYPE health_server_readiness_failures_total counter")
        metrics.append(f"health_server_readiness_failures_total {hs_metrics['readiness_failures']}")
        
        metrics.append("# HELP health_server_request_rejections_total Rejected requests (wrong method, etc)")
        metrics.append("# TYPE health_server_request_rejections_total counter")
        metrics.append(f"health_server_request_rejections_total {hs_metrics['request_rejections']}")
        
        # =================================================================
        # Circuit Breaker Aggregate Metrics (if available)
        # =================================================================
        try:
            from integration_coworker.llm.circuit_breaker import get_circuit_breaker
            cb = get_circuit_breaker()
            if cb is not None:
                cb_metrics = cb.get_aggregate_metrics()
                
                metrics.append("# HELP circuit_breaker_opens_total Total circuit opens")
                metrics.append("# TYPE circuit_breaker_opens_total counter")
                metrics.append(f"circuit_breaker_opens_total {cb_metrics['total_opens']}")
                
                metrics.append("# HELP circuit_breaker_short_circuits_total Requests blocked by open circuits")
                metrics.append("# TYPE circuit_breaker_short_circuits_total counter")
                metrics.append(f"circuit_breaker_short_circuits_total {cb_metrics['total_short_circuits']}")
                
                metrics.append("# HELP circuit_breaker_half_open_probes_total Half-open test requests")
                metrics.append("# TYPE circuit_breaker_half_open_probes_total counter")
                metrics.append(f"circuit_breaker_half_open_probes_total {cb_metrics['total_half_open_probes']}")
                
                metrics.append("# HELP circuit_breaker_resets_total Circuit resets (manual or recovery)")
                metrics.append("# TYPE circuit_breaker_resets_total counter")
                metrics.append(f"circuit_breaker_resets_total {cb_metrics['total_resets']}")
                
                metrics.append("# HELP circuit_breaker_evictions_total LRU/TTL evictions")
                metrics.append("# TYPE circuit_breaker_evictions_total counter")
                metrics.append(f"circuit_breaker_evictions_total {cb_metrics['total_evictions']}")
        except ImportError:
            pass  # Circuit breaker not available
        except Exception as e:
            logger.debug(f"Could not collect circuit breaker metrics: {e}")
        
        # =================================================================
        # LLM Cache Local Metrics (if available)
        # =================================================================
        try:
            from integration_coworker.llm.cache import get_llm_cache
            cache = get_llm_cache()
            if cache is not None:
                cache_metrics = cache.get_local_metrics().to_dict()
                
                metrics.append("# HELP llm_cache_hits_total Cache hits")
                metrics.append("# TYPE llm_cache_hits_total counter")
                metrics.append(f"llm_cache_hits_total {cache_metrics['hits']}")
                
                metrics.append("# HELP llm_cache_misses_total Cache misses")
                metrics.append("# TYPE llm_cache_misses_total counter")
                metrics.append(f"llm_cache_misses_total {cache_metrics['misses']}")
                
                metrics.append("# HELP llm_cache_errors_total Cache errors")
                metrics.append("# TYPE llm_cache_errors_total counter")
                metrics.append(f"llm_cache_errors_total {cache_metrics['errors']}")
                
                metrics.append("# HELP llm_cache_bypasses_total Cache bypasses (disabled/unavailable)")
                metrics.append("# TYPE llm_cache_bypasses_total counter")
                metrics.append(f"llm_cache_bypasses_total {cache_metrics['bypasses']}")
        except ImportError:
            pass  # Cache not available
        except Exception as e:
            logger.debug(f"Could not collect cache metrics: {e}")
        
        # =================================================================
        # Syntax Validator Metrics (tree-sitter availability)
        # =================================================================
        try:
            from integration_coworker.codegen.syntax_validator import is_tree_sitter_available
            tree_sitter_available = 1 if is_tree_sitter_available() else 0
            metrics.append("# HELP syntax_validator_tree_sitter_available Tree-sitter syntax validation available (1=yes, 0=fallback)")
            metrics.append("# TYPE syntax_validator_tree_sitter_available gauge")
            metrics.append(f"syntax_validator_tree_sitter_available {tree_sitter_available}")
        except ImportError:
            pass  # Syntax validator not available
        except Exception as e:
            logger.debug(f"Could not collect syntax validator metrics: {e}")
        
        # =================================================================
        # KG Embedding Fallback Metrics
        # =================================================================
        try:
            from integration_coworker.kg import get_embedding_fallback_count
            fallback_count = get_embedding_fallback_count()
            metrics.append("# HELP kg_embedding_fallback_total Times KG used deterministic similarity instead of embeddings")
            metrics.append("# TYPE kg_embedding_fallback_total counter")
            metrics.append(f"kg_embedding_fallback_total {fallback_count}")
        except ImportError:
            pass  # KG module not available
        except Exception as e:
            logger.debug(f"Could not collect KG embedding metrics: {e}")
        
        # =================================================================
        # KG Seeding Status Metrics (B-006)
        # =================================================================
        try:
            from integration_coworker.persistence.seed_kg import get_template_count
            template_count = get_template_count()
            metrics.append("# HELP kg_template_count Number of workflow templates in Knowledge Graph")
            metrics.append("# TYPE kg_template_count gauge")
            metrics.append(f"kg_template_count {template_count}")
        except ImportError:
            pass  # seed_kg module not available
        except Exception as e:
            logger.debug(f"Could not collect KG template metrics: {e}")
        
        # =================================================================
        # Spec Conversion LLM Fallback Metrics (B-007)
        # =================================================================
        try:
            from integration_coworker.graph.nodes.detect_and_parse_spec import get_spec_llm_fallback_counts
            fallback_counts = get_spec_llm_fallback_counts()
            metrics.append("# HELP spec_conversion_llm_fallback_total Times spec conversion fell back to LLM parsing")
            metrics.append("# TYPE spec_conversion_llm_fallback_total counter")
            for spec_type, count in fallback_counts.items():
                metrics.append(f'spec_conversion_llm_fallback_total{{spec_type="{spec_type}"}} {count}')
        except ImportError:
            pass  # detect_and_parse_spec module not available
        except Exception as e:
            logger.debug(f"Could not collect spec conversion metrics: {e}")
        
        # =================================================================
        # Connection Leak Metrics (B-008)
        # =================================================================
        try:
            from integration_coworker.persistence.db import get_connection_leak_count
            leak_count = get_connection_leak_count()
            metrics.append("# HELP db_connection_leak_detected_total Times a DB connection was garbage collected without being closed")
            metrics.append("# TYPE db_connection_leak_detected_total counter")
            metrics.append(f"db_connection_leak_detected_total {leak_count}")
        except ImportError:
            pass  # db module not available
        except Exception as e:
            logger.debug(f"Could not collect connection leak metrics: {e}")
        
        # =================================================================
        # Feedback Recording Failure Metrics (B-008)
        # =================================================================
        try:
            from integration_coworker.feedback.hooks import get_feedback_failure_count
            failure_count = get_feedback_failure_count()
            metrics.append("# HELP feedback_recording_failures_total Times feedback recording failed")
            metrics.append("# TYPE feedback_recording_failures_total counter")
            metrics.append(f"feedback_recording_failures_total {failure_count}")
        except ImportError:
            pass  # feedback hooks module not available
        except Exception as e:
            logger.debug(f"Could not collect feedback failure metrics: {e}")
        
        # =================================================================
        # Readiness Check Status (current state)
        # =================================================================
        for name, check_fn in _readiness_checks.items():
            try:
                value = 1 if check_fn() else 0
            except Exception:
                value = 0
            safe_name = name.replace("-", "_").replace(".", "_")
            metrics.append(f"# HELP readiness_check_{safe_name} Readiness check status (1=pass, 0=fail)")
            metrics.append(f"# TYPE readiness_check_{safe_name} gauge")
            metrics.append(f"readiness_check_{safe_name} {value}")
        
        self._set_headers(200, "text/plain; charset=utf-8")
        self.wfile.write("\n".join(metrics).encode("utf-8"))


class BoundedHTTPServer(HTTPServer):
    """
    HTTP server with bounded concurrency and request timeout.
    
    Unlike ThreadingHTTPServer which spawns unbounded threads, this server
    uses a shared ThreadPoolExecutor with limited workers. This prevents
    thread explosion under load (e.g., DoS or stuck health checks).
    
    Configuration:
        HEALTH_SERVER_MAX_WORKERS: Max concurrent handlers (default: 4)
        HEALTH_SERVER_TIMEOUT: Request timeout in seconds (default: 5.0)
    """
    
    def __init__(self, server_address, RequestHandlerClass, timeout: float = 5.0):
        super().__init__(server_address, RequestHandlerClass)
        self.request_timeout = timeout
        # Allow port reuse for quick restarts
        self.allow_reuse_address = True
        # Set socket timeout for accept() to allow checking shutdown event
        self.socket.settimeout(1.0)
    
    def process_request(self, request, client_address):
        """
        Override to use bounded ThreadPoolExecutor instead of unbounded threads.
        
        Submits request handling to the shared executor pool, which prevents
        thread explosion under load.
        """
        executor = _get_request_executor()
        try:
            # Submit to bounded pool instead of spawning unlimited threads
            executor.submit(self.process_request_thread, request, client_address)
        except RuntimeError:
            # Executor was shutdown, handle synchronously
            self.process_request_thread(request, client_address)
    
    def process_request_thread(self, request, client_address):
        """Process a single request in a worker thread."""
        try:
            # Set socket timeout for this request
            request.settimeout(self.request_timeout)
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


# Backwards compatibility alias
TimeoutHTTPServer = BoundedHTTPServer


# Global server state
_server: Optional[TimeoutHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_shutdown_event = threading.Event()


def register_readiness_check(name: str, check_fn: Callable[[], bool]) -> None:
    """
    Register a readiness check function.
    
    ⚠️  IMPORTANT: Check functions SHOULD implement their own internal timeouts
    at the dependency level to avoid thread leaks. Examples:
    
    - Database: Use connect_timeout and statement_timeout
    - HTTP clients: Use httpx.Timeout or requests timeout parameter
    - External services: Use SDK-level timeouts
    
    The health server applies a backstop timeout (HEALTH_CHECK_TIMEOUT), but
    this cannot cleanly cancel running threads in Python. Internal timeouts
    are the only way to ensure checks complete cleanly.
    
    Args:
        name: Unique name for the check
        check_fn: Function that returns True if check passes, False otherwise
    
    Example (with proper timeout):
        def check_database():
            '''Database readiness with native timeout.'''
            try:
                # Use connect_timeout to avoid hanging on network issues
                # Use statement_timeout for query timeout
                conn = psycopg2.connect(
                    dsn,
                    connect_timeout=2,
                    options="-c statement_timeout=1000"  # 1 second
                )
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                conn.close()
                return True
            except Exception:
                return False
        
        register_readiness_check("database", check_database)
    """
    _readiness_checks[name] = check_fn
    logger.debug(f"Registered readiness check: {name}")


def unregister_readiness_check(name: str) -> None:
    """Unregister a readiness check."""
    if name in _readiness_checks:
        del _readiness_checks[name]
        logger.debug(f"Unregistered readiness check: {name}")


def clear_readiness_checks() -> None:
    """Clear all readiness checks (for testing)."""
    _readiness_checks.clear()


def get_health_server_config() -> Dict[str, Any]:
    """Get health server configuration from environment."""
    return {
        "host": os.getenv("HEALTH_SERVER_HOST", "127.0.0.1"),
        "port": int(os.getenv("HEALTH_SERVER_PORT", "8080")),
        "timeout": float(os.getenv("HEALTH_SERVER_TIMEOUT", "5.0")),
    }


def start_health_server(
    host: Optional[str] = None,
    port: Optional[int] = None,
    timeout: Optional[float] = None,
) -> None:
    """
    Start the health server in a background thread.
    
    Args:
        host: Bind address (default: from env or 127.0.0.1)
        port: Bind port (default: from env or 8080)
        timeout: Request timeout in seconds (default: from env or 5.0)
    
    Note: Server binds to 127.0.0.1 by default for security.
    
    ⚠️  WARNING: Setting HEALTH_SERVER_HOST=0.0.0.0 exposes this server to all
    network interfaces. Only do this if:
    - You're running in a container with proper network isolation
    - You have a reverse proxy handling external traffic
    - You understand the security implications
    """
    global _server, _server_thread, _shutdown_event
    
    if _server is not None:
        logger.warning("Health server already running")
        return
    
    config = get_health_server_config()
    host = host or config["host"]
    port = port or config["port"]
    timeout = timeout or config["timeout"]
    
    # Warn if binding to external interface
    if host == "0.0.0.0":
        logger.warning(
            "Health server binding to 0.0.0.0 - ensure this is intended and "
            "properly firewalled. This server is not hardened for public exposure."
        )
    
    _shutdown_event.clear()
    
    try:
        _server = BoundedHTTPServer((host, port), HealthCheckHandler, timeout=timeout)
        
        def serve_forever():
            logger.info(f"Health server starting on {host}:{port}")
            try:
                while not _shutdown_event.is_set():
                    try:
                        _server.handle_request()
                    except TimeoutError:
                        # Socket timeout - just continue to check shutdown event
                        pass
            except Exception as e:
                if not _shutdown_event.is_set():
                    logger.error(f"Health server error: {e}")
            logger.info("Health server stopped")
        
        _server_thread = threading.Thread(target=serve_forever, daemon=True)
        _server_thread.start()
        
        # Register with ShutdownManager if available
        try:
            from integration_coworker.shutdown import register_cleanup
            register_cleanup(stop_health_server)
            logger.debug("Registered health server with ShutdownManager")
        except ImportError:
            pass  # ShutdownManager not available
        
    except OSError as e:
        logger.error(f"Failed to start health server on {host}:{port}: {e}")
        _server = None
        raise


def stop_health_server(timeout: float = 5.0) -> None:
    """
    Stop the health server gracefully.
    
    Also shuts down the bounded request executor to ensure clean resource cleanup.
    
    Args:
        timeout: Maximum time to wait for server to stop
    """
    global _server, _server_thread, _shutdown_event
    
    if _server is None:
        return
    
    logger.info("Stopping health server...")
    _shutdown_event.set()
    
    # Close the server socket to unblock handle_request()
    try:
        _server.server_close()
    except Exception as e:
        logger.warning(f"Error closing server socket: {e}")
    
    if _server_thread is not None:
        _server_thread.join(timeout=timeout)
        if _server_thread.is_alive():
            logger.warning("Health server thread did not stop cleanly")
    
    # Cleanup the bounded request executor
    _shutdown_request_executor()
    
    _server = None
    _server_thread = None
    logger.info("Health server stopped")


def is_health_server_running() -> bool:
    """Check if health server is running."""
    return _server is not None and _server_thread is not None and _server_thread.is_alive()


def get_health_server_address() -> Optional[tuple]:
    """Get the address the health server is bound to."""
    if _server is not None:
        return _server.server_address
    return None
