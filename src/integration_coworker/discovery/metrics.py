"""
Discovery Metrics (Slice 3)

Prometheus-compatible metrics for discovery observability.

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md Section J.1:
- Counters: discovery_attempts_total, discovery_success_total, discovery_hitl_total
- Histograms: discovery_latency_seconds, discovery_confidence
- Labels: source (local_catalog, apis_guru), provider, status

Metrics are opt-in and no-op if prometheus_client is not installed.
"""

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Dict, Generator, Optional

logger = logging.getLogger(__name__)

# Try to import prometheus_client, fall back to no-op
_PROMETHEUS_AVAILABLE = False
try:
    from prometheus_client import Counter, Histogram, Gauge
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    logger.debug("prometheus_client not installed, metrics disabled")


# ============================================================================
# Metric Definitions
# ============================================================================

if _PROMETHEUS_AVAILABLE:
    # Counters
    DISCOVERY_ATTEMPTS = Counter(
        "discovery_attempts_total",
        "Total number of spec discovery attempts",
        ["source"],  # local_catalog, apis_guru, hybrid
    )
    
    DISCOVERY_SUCCESS = Counter(
        "discovery_success_total",
        "Total number of successful spec discoveries",
        ["source", "provider"],
    )
    
    DISCOVERY_FAILURES = Counter(
        "discovery_failures_total",
        "Total number of failed spec discoveries",
        ["source", "reason"],  # no_candidates, validation_failed, timeout, etc.
    )
    
    DISCOVERY_HITL_TRIGGERED = Counter(
        "discovery_hitl_triggered_total",
        "Total number of HITL confirmations triggered",
        ["reason"],  # low_confidence, forced, multiple_candidates
    )
    
    DISCOVERY_HITL_COMPLETED = Counter(
        "discovery_hitl_completed_total",
        "Total number of HITL confirmations completed",
        ["outcome"],  # selected, cancelled, timeout
    )
    
    VALIDATION_ATTEMPTS = Counter(
        "discovery_validation_attempts_total",
        "Total number of spec validation attempts",
        ["result"],  # valid, invalid
    )
    
    # Histograms
    DISCOVERY_LATENCY = Histogram(
        "discovery_latency_seconds",
        "Latency of spec discovery operations",
        ["source", "outcome"],  # source=local_catalog|apis_guru, outcome=success|failure|hitl
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    )
    
    DISCOVERY_CONFIDENCE = Histogram(
        "discovery_confidence",
        "Confidence scores of discovery results",
        ["source"],
        buckets=(0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    )
    
    CATALOG_SEARCH_LATENCY = Histogram(
        "discovery_catalog_search_latency_seconds",
        "Latency of local catalog searches",
        ["search_type"],  # semantic, keyword, provider
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0),
    )
    
    CANDIDATE_COUNT = Histogram(
        "discovery_candidate_count",
        "Number of candidates found per discovery",
        ["source"],
        buckets=(0, 1, 2, 3, 5, 10, 20, 50),
    )
    
    # Gauges
    CATALOG_SIZE = Gauge(
        "discovery_catalog_size",
        "Current number of specs in local catalog",
        [],
    )
    
    CATALOG_LAST_REFRESH = Gauge(
        "discovery_catalog_last_refresh_timestamp",
        "Timestamp of last catalog refresh",
        [],
    )


# ============================================================================
# No-op Implementations (when prometheus_client not installed)
# ============================================================================

@dataclass
class NoOpCounter:
    """No-op counter for when prometheus_client is not installed."""
    
    def labels(self, **kwargs) -> "NoOpCounter":
        return self
    
    def inc(self, amount: int = 1) -> None:
        pass


@dataclass
class NoOpHistogram:
    """No-op histogram for when prometheus_client is not installed."""
    
    def labels(self, **kwargs) -> "NoOpHistogram":
        return self
    
    def observe(self, value: float) -> None:
        pass


@dataclass
class NoOpGauge:
    """No-op gauge for when prometheus_client is not installed."""
    
    def labels(self, **kwargs) -> "NoOpGauge":
        return self
    
    def set(self, value: float) -> None:
        pass
    
    def set_to_current_time(self) -> None:
        pass


# Create no-op instances if prometheus not available
if not _PROMETHEUS_AVAILABLE:
    DISCOVERY_ATTEMPTS = NoOpCounter()
    DISCOVERY_SUCCESS = NoOpCounter()
    DISCOVERY_FAILURES = NoOpCounter()
    DISCOVERY_HITL_TRIGGERED = NoOpCounter()
    DISCOVERY_HITL_COMPLETED = NoOpCounter()
    VALIDATION_ATTEMPTS = NoOpCounter()
    DISCOVERY_LATENCY = NoOpHistogram()
    DISCOVERY_CONFIDENCE = NoOpHistogram()
    CATALOG_SEARCH_LATENCY = NoOpHistogram()
    CANDIDATE_COUNT = NoOpHistogram()
    CATALOG_SIZE = NoOpGauge()
    CATALOG_LAST_REFRESH = NoOpGauge()


# ============================================================================
# Metric Recording Helpers
# ============================================================================

def record_discovery_attempt(source: str) -> None:
    """Record a discovery attempt."""
    DISCOVERY_ATTEMPTS.labels(source=source).inc()


def record_discovery_success(source: str, provider: str, confidence: float) -> None:
    """Record a successful discovery."""
    DISCOVERY_SUCCESS.labels(source=source, provider=provider).inc()
    DISCOVERY_CONFIDENCE.labels(source=source).observe(confidence)


def record_discovery_failure(source: str, reason: str) -> None:
    """Record a failed discovery."""
    DISCOVERY_FAILURES.labels(source=source, reason=reason).inc()


def record_hitl_triggered(reason: str) -> None:
    """Record HITL being triggered."""
    DISCOVERY_HITL_TRIGGERED.labels(reason=reason).inc()


def record_hitl_completed(outcome: str) -> None:
    """Record HITL completion."""
    DISCOVERY_HITL_COMPLETED.labels(outcome=outcome).inc()


def record_validation_result(valid: bool) -> None:
    """Record a validation attempt result."""
    VALIDATION_ATTEMPTS.labels(result="valid" if valid else "invalid").inc()


def record_candidate_count(source: str, count: int) -> None:
    """Record number of candidates found."""
    CANDIDATE_COUNT.labels(source=source).observe(count)


def record_catalog_size(size: int) -> None:
    """Record current catalog size."""
    CATALOG_SIZE.set(size)


def record_catalog_refresh() -> None:
    """Record catalog refresh timestamp."""
    CATALOG_LAST_REFRESH.set_to_current_time()


@contextmanager
def discovery_timer(source: str, outcome: str) -> Generator[None, None, None]:
    """
    Context manager to time discovery operations.
    
    Usage:
        with discovery_timer("local_catalog", "success"):
            # do discovery
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter() - start
        DISCOVERY_LATENCY.labels(source=source, outcome=outcome).observe(duration)


@contextmanager
def catalog_search_timer(search_type: str) -> Generator[None, None, None]:
    """
    Context manager to time catalog searches.
    
    Usage:
        with catalog_search_timer("semantic"):
            # do search
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter() - start
        CATALOG_SEARCH_LATENCY.labels(search_type=search_type).observe(duration)


def timed_discovery(source: str) -> Callable:
    """
    Decorator to time async discovery functions.
    
    Usage:
        @timed_discovery("apis_guru")
        async def search_apis_guru(...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            start = time.perf_counter()
            outcome = "failure"
            try:
                result = await func(*args, **kwargs)
                # Determine outcome based on result
                if hasattr(result, 'success'):
                    outcome = "success" if result.success else "failure"
                    if hasattr(result, 'requires_hitl') and result.requires_hitl:
                        outcome = "hitl"
                else:
                    outcome = "success"
                return result
            except Exception:
                outcome = "error"
                raise
            finally:
                duration = time.perf_counter() - start
                DISCOVERY_LATENCY.labels(source=source, outcome=outcome).observe(duration)
        return wrapper
    return decorator


# ============================================================================
# Initialization
# ============================================================================

def is_metrics_enabled() -> bool:
    """Check if metrics are enabled (prometheus_client installed)."""
    return _PROMETHEUS_AVAILABLE


def get_metrics_status() -> Dict[str, Any]:
    """Get current metrics status for debugging."""
    return {
        "enabled": _PROMETHEUS_AVAILABLE,
        "backend": "prometheus_client" if _PROMETHEUS_AVAILABLE else "no-op",
    }
