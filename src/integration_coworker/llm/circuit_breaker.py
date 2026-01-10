"""
Circuit Breaker for LLM Clients (Production Hardening C-2)

Implements the circuit breaker pattern to prevent cascading failures
when external LLM APIs are down or degraded.

Design Decisions:
- Internal implementation (not pybreaker) to avoid external dependencies
- Per-provider+endpoint keying for fine-grained circuit control
- threading.Lock for thread and async safety
- Failures counted at logical request boundary (after retries exhausted)
- CircuitOpenError is a fail-fast signal (not an LLM error)
- LRU cache with TTL to prevent unbounded memory growth

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Circuit tripped, requests fail fast with CircuitOpenError
- HALF_OPEN: Testing recovery, limited requests allowed

Configuration:
    LLM_CIRCUIT_FAILURE_THRESHOLD: Failures to trip circuit (default: 5)
    LLM_CIRCUIT_RECOVERY_TIMEOUT: Seconds before half-open (default: 60)
    LLM_CIRCUIT_HALF_OPEN_REQUESTS: Requests allowed in half-open (default: 1)
    LLM_CIRCUIT_MAX_ENTRIES: Max unique circuits to track (default: 100)
    LLM_CIRCUIT_ENTRY_TTL: TTL for circuit entries in seconds (default: 3600)
"""

import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """
    Raised when circuit breaker is open.
    
    This is NOT an LLMError - it's a fail-fast signal indicating
    the circuit is open and requests should not be attempted.
    Callers should handle this separately from LLM errors.
    """
    def __init__(self, circuit_key: str, time_until_recovery: float):
        self.circuit_key = circuit_key
        self.time_until_recovery = time_until_recovery
        super().__init__(
            f"Circuit breaker open for '{circuit_key}'. "
            f"Recovery in {time_until_recovery:.1f}s"
        )


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""
    
    # Number of failures to trip the circuit
    failure_threshold: int = field(
        default_factory=lambda: int(os.getenv("LLM_CIRCUIT_FAILURE_THRESHOLD", "5"))
    )
    
    # Seconds to wait before transitioning to half-open
    recovery_timeout: float = field(
        default_factory=lambda: float(os.getenv("LLM_CIRCUIT_RECOVERY_TIMEOUT", "60"))
    )
    
    # Number of requests to allow in half-open state
    half_open_requests: int = field(
        default_factory=lambda: int(os.getenv("LLM_CIRCUIT_HALF_OPEN_REQUESTS", "1"))
    )
    
    # Maximum number of circuit entries (LRU eviction when exceeded)
    max_entries: int = field(
        default_factory=lambda: int(os.getenv("LLM_CIRCUIT_MAX_ENTRIES", "100"))
    )
    
    # TTL for circuit entries in seconds (stale entries are evicted)
    entry_ttl: float = field(
        default_factory=lambda: float(os.getenv("LLM_CIRCUIT_ENTRY_TTL", "3600"))  # 1 hour
    )


@dataclass
class CircuitBreakerState:
    """Internal state for a single circuit breaker."""
    
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0  # monotonic time
    half_open_attempts: int = 0
    
    # Timestamp for LRU/TTL tracking (monotonic clock to avoid wall-clock issues)
    last_access_time: float = field(default_factory=time.monotonic)
    created_time: float = field(default_factory=time.monotonic)
    
    # Lock for thread safety
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class CircuitBreakerMetrics:
    """
    Aggregate metrics across all circuits.
    
    These counters track overall circuit breaker activity for observability.
    """
    total_opens: int = 0           # Total times any circuit opened
    total_short_circuits: int = 0  # Total requests blocked by open circuits
    total_half_open_probes: int = 0  # Total half-open test requests
    total_resets: int = 0          # Total circuit resets (manual or recovery)
    total_evictions: int = 0       # Total LRU/TTL evictions
    
    # Lock for thread-safe counter updates
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    def increment_opens(self) -> None:
        with self._lock:
            self.total_opens += 1
    
    def increment_short_circuits(self) -> None:
        with self._lock:
            self.total_short_circuits += 1
    
    def increment_half_open_probes(self) -> None:
        with self._lock:
            self.total_half_open_probes += 1
    
    def increment_resets(self) -> None:
        with self._lock:
            self.total_resets += 1
    
    def increment_evictions(self, count: int = 1) -> None:
        with self._lock:
            self.total_evictions += count
    
    def to_dict(self) -> Dict[str, int]:
        """Return metrics as a dictionary."""
        with self._lock:
            return {
                "total_opens": self.total_opens,
                "total_short_circuits": self.total_short_circuits,
                "total_half_open_probes": self.total_half_open_probes,
                "total_resets": self.total_resets,
                "total_evictions": self.total_evictions,
            }


class CircuitBreaker:
    """
    Circuit breaker with per-key state management.
    
    Each unique key (typically provider+endpoint) gets its own circuit.
    All operations are thread-safe via per-circuit locks.
    
    Memory Management:
    - Uses OrderedDict for LRU ordering
    - Evicts oldest entries when max_entries exceeded
    - Evicts entries older than entry_ttl
    - Metrics track evictions for observability
    """
    
    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        """Initialize circuit breaker manager."""
        self.config = config or CircuitBreakerConfig()
        # OrderedDict for LRU ordering - most recently accessed at end
        self._circuits: OrderedDict[str, CircuitBreakerState] = OrderedDict()
        self._global_lock = threading.Lock()
        self._metrics = CircuitBreakerMetrics()
    
    def _evict_stale_entries(self) -> None:
        """
        Evict entries that exceed TTL or max_entries limit.
        
        Uses monotonic clock for TTL to avoid wall-clock issues (DST, NTP adjustments).
        
        Must be called with _global_lock held.
        """
        now = time.monotonic()
        evicted = 0
        
        # Evict TTL-expired entries
        keys_to_remove = []
        for key, circuit in self._circuits.items():
            if now - circuit.last_access_time > self.config.entry_ttl:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del self._circuits[key]
            evicted += 1
            logger.debug(f"Evicted stale circuit '{key}' (TTL expired)")
        
        # Evict LRU entries if still over limit
        while len(self._circuits) > self.config.max_entries:
            # OrderedDict.popitem(last=False) removes oldest (first) item
            key, _ = self._circuits.popitem(last=False)
            evicted += 1
            logger.debug(f"Evicted LRU circuit '{key}' (max_entries exceeded)")
        
        if evicted > 0:
            self._metrics.increment_evictions(evicted)
    
    def _get_circuit(self, key: str) -> CircuitBreakerState:
        """Get or create circuit state for a key, updating LRU order."""
        with self._global_lock:
            if key in self._circuits:
                # Move to end (most recently used)
                self._circuits.move_to_end(key)
                circuit = self._circuits[key]
                circuit.last_access_time = time.monotonic()
                return circuit
            
            # Run eviction before adding new entry
            self._evict_stale_entries()
            
            # Create new circuit
            self._circuits[key] = CircuitBreakerState()
            return self._circuits[key]
    
    def can_execute(self, key: str) -> bool:
        """
        Check if a request can be executed.
        
        Uses monotonic clock for recovery timeout to avoid wall-clock issues.
        
        Args:
            key: Circuit identifier (e.g., "openai:gpt-4o" or "anthropic:claude-3")
        
        Returns:
            True if request should proceed, False if circuit is open
            
        Raises:
            CircuitOpenError: If circuit is open (for fail-fast pattern)
        """
        circuit = self._get_circuit(key)
        
        with circuit.lock:
            if circuit.state == CircuitState.CLOSED:
                return True
            
            if circuit.state == CircuitState.OPEN:
                # Check if recovery timeout has passed (monotonic clock)
                time_since_failure = time.monotonic() - circuit.last_failure_time
                if time_since_failure >= self.config.recovery_timeout:
                    # Transition to half-open
                    logger.info(f"Circuit '{key}' transitioning to half-open after {time_since_failure:.1f}s")
                    circuit.state = CircuitState.HALF_OPEN
                    circuit.half_open_attempts = 0
                else:
                    # Still open, fail fast
                    self._metrics.increment_short_circuits()
                    time_until_recovery = self.config.recovery_timeout - time_since_failure
                    raise CircuitOpenError(key, time_until_recovery)
            
            if circuit.state == CircuitState.HALF_OPEN:
                # Allow limited requests in half-open state
                if circuit.half_open_attempts < self.config.half_open_requests:
                    circuit.half_open_attempts += 1
                    self._metrics.increment_half_open_probes()
                    return True
                else:
                    # Already at limit, fail fast
                    self._metrics.increment_short_circuits()
                    raise CircuitOpenError(key, 0.0)
        
        return True
    
    def record_success(self, key: str) -> None:
        """
        Record a successful request.
        
        In half-open state, success resets the circuit to closed.
        In closed state, resets failure count.
        """
        circuit = self._get_circuit(key)
        
        with circuit.lock:
            if circuit.state == CircuitState.HALF_OPEN:
                # Recovery successful, close circuit
                logger.info(f"Circuit '{key}' recovered, transitioning to closed")
                circuit.state = CircuitState.CLOSED
                circuit.failure_count = 0
                circuit.success_count = 0
                circuit.half_open_attempts = 0
                self._metrics.increment_resets()
            elif circuit.state == CircuitState.CLOSED:
                # Reset failure count on success
                circuit.success_count += 1
                if circuit.failure_count > 0:
                    circuit.failure_count = max(0, circuit.failure_count - 1)
    
    def record_failure(self, key: str) -> None:
        """
        Record a failed request (after all retries exhausted).
        
        Uses monotonic clock for timing to avoid wall-clock issues (DST, NTP).
        
        This should be called ONLY after the retry loop has exhausted
        all attempts. Individual retry failures should NOT call this.
        """
        circuit = self._get_circuit(key)
        
        with circuit.lock:
            circuit.failure_count += 1
            circuit.last_failure_time = time.monotonic()
            
            if circuit.state == CircuitState.HALF_OPEN:
                # Failure during recovery, re-open circuit
                logger.warning(f"Circuit '{key}' failed during recovery, reopening")
                circuit.state = CircuitState.OPEN
                circuit.half_open_attempts = 0
                self._metrics.increment_opens()
            elif circuit.state == CircuitState.CLOSED:
                # Check if threshold reached
                if circuit.failure_count >= self.config.failure_threshold:
                    logger.warning(
                        f"Circuit '{key}' tripped after {circuit.failure_count} failures, "
                        f"opening for {self.config.recovery_timeout}s"
                    )
                    circuit.state = CircuitState.OPEN
                    self._metrics.increment_opens()
    
    def get_state(self, key: str) -> CircuitState:
        """Get the current state of a circuit."""
        circuit = self._get_circuit(key)
        with circuit.lock:
            return circuit.state
    
    def get_metrics(self, key: str) -> Dict[str, Any]:
        """Get metrics for a circuit."""
        circuit = self._get_circuit(key)
        with circuit.lock:
            return {
                "state": circuit.state.value,
                "failure_count": circuit.failure_count,
                "success_count": circuit.success_count,
                "last_failure_time": circuit.last_failure_time,
                "half_open_attempts": circuit.half_open_attempts,
            }
    
    def get_aggregate_metrics(self) -> Dict[str, Any]:
        """
        Get aggregate metrics across all circuits.
        
        Returns:
            Dict containing:
            - total_opens: Total times any circuit opened
            - total_short_circuits: Total requests blocked by open circuits
            - total_half_open_probes: Total half-open test requests
            - total_resets: Total circuit resets
            - total_evictions: Total LRU/TTL evictions
            - active_circuits: Number of currently tracked circuits
            - circuits_by_state: Count of circuits in each state
        """
        with self._global_lock:
            circuits_by_state = {"closed": 0, "open": 0, "half_open": 0}
            for circuit in self._circuits.values():
                with circuit.lock:
                    circuits_by_state[circuit.state.value] += 1
            
            result = self._metrics.to_dict()
            result["active_circuits"] = len(self._circuits)
            result["circuits_by_state"] = circuits_by_state
            return result
    
    def reset(self, key: Optional[str] = None) -> None:
        """
        Reset circuit(s) to closed state.
        
        Args:
            key: Specific circuit to reset, or None to reset all
        """
        if key is not None:
            circuit = self._get_circuit(key)
            with circuit.lock:
                circuit.state = CircuitState.CLOSED
                circuit.failure_count = 0
                circuit.success_count = 0
                circuit.half_open_attempts = 0
                self._metrics.increment_resets()
                logger.info(f"Circuit '{key}' manually reset to closed")
        else:
            with self._global_lock:
                reset_count = 0
                for k, circuit in self._circuits.items():
                    with circuit.lock:
                        circuit.state = CircuitState.CLOSED
                        circuit.failure_count = 0
                        circuit.success_count = 0
                        circuit.half_open_attempts = 0
                        reset_count += 1
                if reset_count > 0:
                    for _ in range(reset_count):
                        self._metrics.increment_resets()
                logger.info(f"All {reset_count} circuits manually reset to closed")


# Global circuit breaker instance
_circuit_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    """Get the global circuit breaker instance."""
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker()
    return _circuit_breaker


def reset_circuit_breaker() -> None:
    """Reset the global circuit breaker (for testing)."""
    global _circuit_breaker
    _circuit_breaker = None


def get_circuit_breaker_metrics() -> Dict[str, Any]:
    """
    Get aggregate metrics from the global circuit breaker.
    
    Convenience function for observability/monitoring integration.
    
    Returns:
        Dict with aggregate metrics (see CircuitBreaker.get_aggregate_metrics)
    """
    return get_circuit_breaker().get_aggregate_metrics()


def make_circuit_key(provider: str, model: str, base_url: Optional[str] = None) -> str:
    """
    Create a circuit breaker key from provider, model, and optionally base_url.
    
    Including base_url is important for distinguishing between:
    - Different API endpoints for the same provider (e.g., proxy vs direct)
    - Self-hosted/enterprise deployments vs cloud endpoints
    - HTTP vs HTTPS endpoints (different security postures)
    
    Args:
        provider: LLM provider name (e.g., "openai", "anthropic")
        model: Model name (e.g., "gpt-4o", "claude-3-opus")
        base_url: Optional custom API endpoint URL
    
    Returns:
        Circuit key string. If base_url is provided, normalizes to scheme+host+port.
        Path and query strings are NOT included to avoid key explosion.
    
    Examples:
        make_circuit_key("openai", "gpt-4o") -> "openai:gpt-4o"
        make_circuit_key("anthropic", "claude-3-opus") -> "anthropic:claude-3-opus"
        make_circuit_key("openai", "gpt-4o", "https://proxy.example.com/v1") 
            -> "openai:gpt-4o@https://proxy.example.com"
        make_circuit_key("openai", "gpt-4o", "http://localhost:8080/api")
            -> "openai:gpt-4o@http://localhost:8080"
    """
    if base_url:
        # Normalize to scheme+host+port only (no path/query to avoid key explosion)
        try:
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            # Build normalized endpoint: scheme://host[:port]
            scheme = parsed.scheme or "https"
            host = parsed.hostname or parsed.netloc or base_url
            port = parsed.port
            
            if port:
                # Include port if explicitly specified
                normalized = f"{scheme}://{host}:{port}"
            else:
                normalized = f"{scheme}://{host}"
            
            return f"{provider}:{model}@{normalized}"
        except Exception:
            # Fall back to raw base_url if parsing fails
            return f"{provider}:{model}@{base_url}"
    return f"{provider}:{model}"
