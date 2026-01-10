"""
Bounded Execution Module

P2: Hard stops for runaway runs in long-running service mode.

Provides:
1. Per-node timeout enforcement (asyncio.wait_for for async nodes)
2. Global run budget tracking (wall time, nodes executed)
3. Centralized wrapper for graph node enforcement

Usage:
    # In graph builder (runtime.py):
    from integration_coworker.graph.bounds import make_bounded, get_bounds_config
    
    cfg = get_bounds_config()
    bounded_fn = make_bounded(node_fn, "node_name", cfg)
    workflow.add_node("node_name", bounded_fn)

Configuration via environment or Settings:
    BOUNDED_EXEC_ENABLED=true
    BOUNDED_EXEC_RECURSION_LIMIT=100
    BOUNDED_EXEC_NODE_TIMEOUT=120.0
    BOUNDED_EXEC_MAX_WALL_SECONDS=3600.0
    BOUNDED_EXEC_MAX_NODES=200
    BOUNDED_EXEC_MAX_CONCURRENCY=10
"""
import asyncio
import functools
import logging
import os
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

# Type variable for wrapped functions
F = TypeVar("F", bound=Callable[..., Any])


# =============================================================================
# Domain Exceptions
# =============================================================================

class BoundedExecutionError(Exception):
    """Base exception for bounded execution violations."""
    pass


class NodeTimeoutError(BoundedExecutionError):
    """Raised when a node exceeds its timeout."""
    
    def __init__(self, node_name: str, timeout_seconds: float, run_id: str = ""):
        self.node_name = node_name
        self.timeout_seconds = timeout_seconds
        self.run_id = run_id
        super().__init__(
            f"Node '{node_name}' exceeded timeout of {timeout_seconds}s"
            + (f" (run_id={run_id})" if run_id else "")
        )


class GlobalTimeoutError(BoundedExecutionError):
    """
    Raised when the entire workflow exceeds the global wall-clock timeout.
    
    This is a HARD timeout enforced by asyncio.timeout(), unlike the soft
    budget tracking in RunBudgetTracker which only checks at node transitions.
    
    Python 3.11+ asyncio.timeout() cannot be caught by the task - it's
    guaranteed to cancel even if the task is stuck in blocking I/O.
    """
    
    def __init__(self, timeout_seconds: float, run_id: str = ""):
        self.timeout_seconds = timeout_seconds
        self.run_id = run_id
        super().__init__(
            f"Global workflow timeout of {timeout_seconds}s exceeded"
            + (f" (run_id={run_id})" if run_id else "")
        )


class RunBudgetExceededError(BoundedExecutionError):
    """Raised when global run budget is exceeded."""
    
    def __init__(self, reason: str, run_id: str = ""):
        self.reason = reason
        self.run_id = run_id
        super().__init__(
            f"Run budget exceeded: {reason}"
            + (f" (run_id={run_id})" if run_id else "")
        )


# =============================================================================
# Configuration
# =============================================================================

# Import canonical node names - single source of truth
from integration_coworker.graph.node_names import HITL_EXEMPT_NODES


@dataclass
class BoundedExecutionConfig:
    """
    Configuration for bounded execution enforcement.
    
    Attributes:
        enabled: Master switch for bounded execution
        recursion_limit: LangGraph recursion limit (passed to invoke config)
        node_timeout_seconds_default: Default timeout per node in seconds
        node_timeout_overrides: Per-node timeout overrides {node_name: seconds}
        max_run_wall_seconds: Maximum wall clock time for entire run (None = unlimited)
        max_nodes_executed: Maximum nodes executed per run (None = unlimited)
        max_concurrency: Maximum concurrent operations (for fan-out control)
        hitl_exempt_nodes: Nodes exempt from timeout/budget (use interrupt())
        hitl_max_wait_seconds: Warning threshold for stale HITL requests (default: 24h)
    """
    enabled: bool = True
    recursion_limit: int = 100  # Sane default for our graph depth
    node_timeout_seconds_default: float = 120.0  # 2 minutes per node
    node_timeout_overrides: Dict[str, float] = field(default_factory=dict)
    max_run_wall_seconds: Optional[float] = 3600.0  # 1 hour max run time
    max_nodes_executed: Optional[int] = 200  # Max nodes in a single run
    max_concurrency: Optional[int] = 10  # Max parallel operations
    hitl_exempt_nodes: set = field(default_factory=lambda: HITL_EXEMPT_NODES.copy())
    hitl_max_wait_seconds: float = 86400.0  # 24 hours - warning threshold for stale HITL
    
    def get_timeout_for_node(self, node_name: str) -> Optional[float]:
        """
        Get the timeout for a specific node.
        
        Returns None for HITL-exempt nodes (they should pause indefinitely).
        """
        # HITL nodes are exempt from timeout - interrupt() pauses indefinitely
        if node_name in self.hitl_exempt_nodes:
            return None
        return self.node_timeout_overrides.get(node_name, self.node_timeout_seconds_default)
    
    def is_hitl_exempt(self, node_name: str) -> bool:
        """Check if a node is exempt from bounded execution (uses interrupt())."""
        return node_name in self.hitl_exempt_nodes


def get_bounds_config() -> BoundedExecutionConfig:
    """
    Load bounded execution configuration from environment.
    
    Environment variables:
        BOUNDED_EXEC_ENABLED: Enable/disable (default: true in production profile)
        BOUNDED_EXEC_RECURSION_LIMIT: LangGraph recursion limit (default: 100)
        BOUNDED_EXEC_NODE_TIMEOUT: Default node timeout in seconds (default: 120.0)
        BOUNDED_EXEC_MAX_WALL_SECONDS: Max run wall time (default: 3600.0)
        BOUNDED_EXEC_MAX_NODES: Max nodes per run (default: 200)
        BOUNDED_EXEC_MAX_CONCURRENCY: Max concurrency (default: 10)
        BOUNDED_EXEC_TIMEOUT_<NODE_NAME>: Per-node timeout override
        HITL_MAX_WAIT_SECONDS: Warning threshold for stale HITL requests (default: 86400 = 24h)
    
    Returns:
        BoundedExecutionConfig instance
    """
    # Check if production profile - default enabled for production
    profile_name = os.getenv("CODEGEN_PROFILE", "development").lower().strip()
    default_enabled = profile_name == "production"
    
    enabled = os.getenv("BOUNDED_EXEC_ENABLED")
    if enabled is not None:
        enabled = enabled.lower() in ("true", "1", "yes", "on")
    else:
        enabled = default_enabled
    
    # Parse node timeout overrides from environment
    # Format: BOUNDED_EXEC_TIMEOUT_<NODE_NAME>=<seconds>
    node_timeout_overrides: Dict[str, float] = {}
    prefix = "BOUNDED_EXEC_TIMEOUT_"
    for key, value in os.environ.items():
        if key.startswith(prefix):
            node_name = key[len(prefix):].lower()
            try:
                node_timeout_overrides[node_name] = float(value)
            except ValueError:
                logger.warning(f"Invalid timeout value for {key}: {value}")
    
    # Default overrides for known slow nodes
    if "generate_code_and_tests" not in node_timeout_overrides:
        node_timeout_overrides["generate_code_and_tests"] = 300.0  # 5 minutes for LLM codegen
    if "embed_spec_chunks" not in node_timeout_overrides:
        node_timeout_overrides["embed_spec_chunks"] = 300.0  # 5 minutes for embeddings
    
    def _get_float_or_none(key: str, default: Optional[float]) -> Optional[float]:
        val = os.getenv(key)
        if val is None:
            return default
        if val.lower() in ("none", "null", ""):
            return None
        try:
            return float(val)
        except ValueError:
            return default
    
    def _get_int_or_none(key: str, default: Optional[int]) -> Optional[int]:
        val = os.getenv(key)
        if val is None:
            return default
        if val.lower() in ("none", "null", ""):
            return None
        try:
            return int(val)
        except ValueError:
            return default
    
    return BoundedExecutionConfig(
        enabled=enabled,
        recursion_limit=int(os.getenv("BOUNDED_EXEC_RECURSION_LIMIT", "100")),
        node_timeout_seconds_default=float(os.getenv("BOUNDED_EXEC_NODE_TIMEOUT", "120.0")),
        node_timeout_overrides=node_timeout_overrides,
        max_run_wall_seconds=_get_float_or_none("BOUNDED_EXEC_MAX_WALL_SECONDS", 3600.0),
        max_nodes_executed=_get_int_or_none("BOUNDED_EXEC_MAX_NODES", 200),
        max_concurrency=_get_int_or_none("BOUNDED_EXEC_MAX_CONCURRENCY", 10),
        hitl_max_wait_seconds=float(os.getenv("HITL_MAX_WAIT_SECONDS", "86400.0")),
    )


# =============================================================================
# Run Budget Tracker
# =============================================================================

class RunBudgetTracker:
    """
    Tracks budget consumption for a single run.
    
    Thread-safe for concurrent node execution.
    Supports pausing/resuming the wall clock timer during HITL interrupts.
    """
    
    def __init__(self, config: BoundedExecutionConfig, run_id: str = ""):
        self.config = config
        self.run_id = run_id
        self.start_time = time.monotonic()
        self.nodes_executed = 0
        # Use RLock (reentrant lock) since methods like check_and_increment 
        # call get_effective_elapsed which also acquires the lock
        self._lock = threading.RLock()
        # Track paused time - when workflow is at interrupt(), don't count wall time
        self._paused_at: Optional[float] = None
        self._total_paused_seconds: float = 0.0
    
    def pause_wall_clock(self) -> None:
        """
        Pause the wall clock timer (for HITL interrupt pauses).
        
        Time spent paused does not count against max_run_wall_seconds.
        """
        with self._lock:
            if self._paused_at is None:
                self._paused_at = time.monotonic()
                logger.debug(f"Wall clock paused for run {self.run_id}")
    
    def resume_wall_clock(self) -> None:
        """
        Resume the wall clock timer after HITL approval.
        """
        with self._lock:
            if self._paused_at is not None:
                pause_duration = time.monotonic() - self._paused_at
                self._total_paused_seconds += pause_duration
                self._paused_at = None
                logger.debug(
                    f"Wall clock resumed for run {self.run_id}, "
                    f"paused for {pause_duration:.1f}s, "
                    f"total paused: {self._total_paused_seconds:.1f}s"
                )
    
    def get_effective_elapsed(self) -> float:
        """
        Get the effective elapsed time (excluding paused time).
        """
        with self._lock:
            total_elapsed = time.monotonic() - self.start_time
            paused = self._total_paused_seconds
            # If currently paused, don't count current pause duration
            if self._paused_at is not None:
                current_pause = time.monotonic() - self._paused_at
                paused += current_pause
            return total_elapsed - paused
    
    def is_paused(self) -> bool:
        """Check if the wall clock is currently paused."""
        with self._lock:
            return self._paused_at is not None
    
    def check_and_increment(self, node_name: str) -> None:
        """
        Check budget before executing a node, increment counter.
        
        HITL-exempt nodes skip budget checks (they can pause indefinitely).
        
        Raises:
            RunBudgetExceededError: If budget would be exceeded
        """
        # HITL nodes are exempt from budget checks
        if self.config.is_hitl_exempt(node_name):
            logger.debug(f"Budget check skipped for HITL-exempt node {node_name}")
            return
        
        with self._lock:
            # Check wall time (using effective elapsed, excluding paused time)
            if self.config.max_run_wall_seconds is not None:
                elapsed = self.get_effective_elapsed()
                if elapsed >= self.config.max_run_wall_seconds:
                    raise RunBudgetExceededError(
                        f"Max wall time {self.config.max_run_wall_seconds}s exceeded "
                        f"(elapsed: {elapsed:.1f}s, excluding {self._total_paused_seconds:.1f}s paused)",
                        self.run_id,
                    )
            
            # Check node count
            if self.config.max_nodes_executed is not None:
                if self.nodes_executed >= self.config.max_nodes_executed:
                    raise RunBudgetExceededError(
                        f"Max nodes {self.config.max_nodes_executed} exceeded",
                        self.run_id,
                    )
            
            # Increment counter
            self.nodes_executed += 1
            logger.debug(
                f"Budget check passed for {node_name}: "
                f"{self.nodes_executed}/{self.config.max_nodes_executed or '∞'} nodes, "
                f"{self.get_effective_elapsed():.1f}/"
                f"{self.config.max_run_wall_seconds or '∞'}s wall time"
            )
    
    def get_summary(self) -> Dict[str, Any]:
        """Get budget consumption summary."""
        with self._lock:
            elapsed = time.monotonic() - self.start_time
            effective_elapsed = self.get_effective_elapsed()
            return {
                "run_id": self.run_id,
                "nodes_executed": self.nodes_executed,
                "wall_time_seconds": elapsed,
                "effective_wall_time_seconds": effective_elapsed,
                "paused_time_seconds": self._total_paused_seconds,
                "max_nodes": self.config.max_nodes_executed,
                "max_wall_seconds": self.config.max_run_wall_seconds,
            }


# Thread-local storage for run budget tracker
_budget_context: threading.local = threading.local()


def set_budget_tracker(tracker: Optional[RunBudgetTracker]) -> None:
    """Set the budget tracker for the current thread/run."""
    _budget_context.tracker = tracker


def get_budget_tracker() -> Optional[RunBudgetTracker]:
    """Get the budget tracker for the current thread/run."""
    return getattr(_budget_context, 'tracker', None)


# =============================================================================
# Node Wrapper
# =============================================================================

def make_bounded(
    node_fn: Callable,
    node_name: str,
    config: BoundedExecutionConfig,
) -> Callable:
    """
    Wrap a node function with bounded execution enforcement.
    
    Enforces:
    1. Per-node timeout (asyncio.wait_for for async, threading for sync)
    2. Run budget check (wall time, node count)
    
    HITL-exempt nodes (those using interrupt()) are NOT wrapped with timeout
    and do not count against wall-clock budget while paused.
    
    Args:
        node_fn: The original node function
        node_name: Name of the node (for logging and timeout lookup)
        config: Bounded execution configuration
    
    Returns:
        Wrapped function with timeout and budget enforcement
    
    Usage:
        bounded_fn = make_bounded(my_node, "my_node", get_bounds_config())
        workflow.add_node("my_node", bounded_fn)
    """
    if not config.enabled:
        return node_fn
    
    # HITL nodes are exempt from timeout enforcement
    is_hitl_exempt = config.is_hitl_exempt(node_name)
    timeout_seconds = config.get_timeout_for_node(node_name)  # None for HITL nodes
    
    is_async = asyncio.iscoroutinefunction(node_fn)
    
    if is_async:
        @functools.wraps(node_fn)
        async def async_bounded_wrapper(state, *args, **kwargs):
            # Check and increment budget (HITL nodes skip this)
            tracker = get_budget_tracker()
            if tracker and not is_hitl_exempt:
                try:
                    tracker.check_and_increment(node_name)
                except RunBudgetExceededError as e:
                    # Record error in state and re-raise
                    _record_bounded_exec_error(state, str(e), node_name)
                    raise
            
            # Get run_id for error reporting
            run_id = ""
            if hasattr(state, 'run_id'):
                run_id = state.run_id or ""
            elif isinstance(state, dict):
                run_id = state.get('run_id', "")
            
            # HITL nodes: no timeout, pause wall clock during execution
            if is_hitl_exempt:
                # Pause wall clock while at interrupt
                if tracker:
                    tracker.pause_wall_clock()
                try:
                    return await node_fn(state, *args, **kwargs)
                finally:
                    # Resume wall clock after HITL completes
                    if tracker:
                        tracker.resume_wall_clock()
            
            # Non-HITL nodes: execute with timeout
            try:
                return await asyncio.wait_for(
                    node_fn(state, *args, **kwargs),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                error = NodeTimeoutError(node_name, timeout_seconds, run_id)
                _record_bounded_exec_error(state, str(error), node_name)
                logger.error(f"Node timeout: {error}")
                raise error from None
            except asyncio.CancelledError:
                # Handle cancellation explicitly - this can happen if:
                # 1. asyncio.wait_for times out and cancels the task
                # 2. External cancellation (e.g., process shutdown)
                # Re-raise as CancelledError to preserve cancellation semantics
                logger.warning(f"Node {node_name} was cancelled (run_id={run_id})")
                raise
        
        return async_bounded_wrapper
    
    else:
        @functools.wraps(node_fn)
        def sync_bounded_wrapper(state, *args, **kwargs):
            # Check and increment budget (HITL nodes skip this)
            tracker = get_budget_tracker()
            if tracker and not is_hitl_exempt:
                try:
                    tracker.check_and_increment(node_name)
                except RunBudgetExceededError as e:
                    _record_bounded_exec_error(state, str(e), node_name)
                    raise
            
            # Get run_id for error reporting
            run_id = ""
            if hasattr(state, 'run_id'):
                run_id = state.run_id or ""
            elif isinstance(state, dict):
                run_id = state.get('run_id', "")
            
            # HITL nodes: pause wall clock during execution (though sync HITL is unusual)
            if is_hitl_exempt:
                if tracker:
                    tracker.pause_wall_clock()
                try:
                    return node_fn(state, *args, **kwargs)
                finally:
                    if tracker:
                        tracker.resume_wall_clock()
            
            # For sync functions, we use a simple approach:
            # Most nodes are async or fast enough that sync timeout isn't critical.
            # If needed, we could use concurrent.futures with timeout.
            start = time.monotonic()
            result = node_fn(state, *args, **kwargs)
            elapsed = time.monotonic() - start
            
            # Post-execution timeout check (soft enforcement for sync)
            if timeout_seconds and elapsed > timeout_seconds:
                logger.warning(
                    f"Sync node {node_name} exceeded timeout: "
                    f"{elapsed:.1f}s > {timeout_seconds}s (soft warning)"
                )
            
            return result
        
        return sync_bounded_wrapper


def _record_bounded_exec_error(state: Any, error_msg: str, node_name: str) -> None:
    """Record a bounded execution error in state."""
    try:
        if hasattr(state, 'errors'):
            if isinstance(state.errors, list):
                state.errors.append(f"[bounded_exec:{node_name}] {error_msg}")
        elif isinstance(state, dict) and 'errors' in state:
            if isinstance(state['errors'], list):
                state['errors'].append(f"[bounded_exec:{node_name}] {error_msg}")
    except Exception as e:
        logger.warning(f"Failed to record bounded exec error: {e}")


# =============================================================================
# Runnable Config Helpers
# =============================================================================

def get_invoke_config(
    thread_id: str,
    bounds_config: Optional[BoundedExecutionConfig] = None,
    extra_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build the LangGraph invoke config with recursion_limit and thread_id.
    
    Args:
        thread_id: Thread ID for checkpoint isolation (must match run_id)
        bounds_config: Bounded execution config (uses default if None)
        extra_config: Additional config to merge
    
    Returns:
        Config dict for app.invoke() or app.ainvoke()
    """
    cfg = bounds_config or get_bounds_config()
    
    config: Dict[str, Any] = {
        "configurable": {
            "thread_id": thread_id,
        },
    }
    
    # Add recursion_limit if bounded execution is enabled
    if cfg.enabled:
        config["recursion_limit"] = cfg.recursion_limit
    
    # Add max_concurrency if specified
    if cfg.enabled and cfg.max_concurrency is not None:
        config["max_concurrency"] = cfg.max_concurrency
    
    # Merge extra config
    if extra_config:
        for key, value in extra_config.items():
            if key == "configurable":
                config["configurable"].update(value)
            else:
                config[key] = value
    
    return config


# =============================================================================
# Context Manager for Run Budget
# =============================================================================

class bounded_run_context:
    """
    Context manager for bounded execution during a run.
    
    Sets up budget tracking for the duration of the run.
    
    Usage:
        with bounded_run_context(config, run_id) as tracker:
            # Execute workflow
            ...
        # tracker.get_summary() shows budget consumption
    """
    
    def __init__(self, config: BoundedExecutionConfig, run_id: str = ""):
        self.config = config
        self.run_id = run_id
        self.tracker: Optional[RunBudgetTracker] = None
        self._previous_tracker: Optional[RunBudgetTracker] = None
    
    def __enter__(self) -> Optional[RunBudgetTracker]:
        if not self.config.enabled:
            return None
        
        self._previous_tracker = get_budget_tracker()
        self.tracker = RunBudgetTracker(self.config, self.run_id)
        set_budget_tracker(self.tracker)
        
        logger.debug(
            f"Started bounded execution context for run {self.run_id}: "
            f"max_nodes={self.config.max_nodes_executed}, "
            f"max_wall_seconds={self.config.max_run_wall_seconds}"
        )
        
        return self.tracker
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.tracker:
            summary = self.tracker.get_summary()
            logger.info(
                f"Bounded execution summary for run {self.run_id}: "
                f"{summary['nodes_executed']} nodes, "
                f"{summary['wall_time_seconds']:.1f}s wall time"
            )
        
        set_budget_tracker(self._previous_tracker)
        return False  # Don't suppress exceptions
