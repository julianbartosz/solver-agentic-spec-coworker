# =============================================================================
# P0.2: LangSmith Byte Budget Enforcement - MUST BE FIRST
# =============================================================================
# Configure the global LangSmith client BEFORE any LangChain/LangGraph imports
# to ensure all traces (including internal graph execution) are truncated.
# The LangSmith multipart limit is 26MB per field, but we use a 2MB budget
# per trace to stay well under the limit and avoid memory pressure.
# =============================================================================
import json
import logging
import os
from typing import Any, Dict

# Byte budget for trace payloads (2MB conservative limit)
_TRACE_BYTE_BUDGET = 2_000_000
_TRUNCATION_MARKER = "... [TRUNCATED - exceeded trace budget]"


def _truncate_for_trace(data: Dict[str, Any]) -> Dict[str, Any]:
    """Truncate trace data to stay within byte budget.
    
    This function is called by LangSmith's hide_inputs/hide_outputs
    on ALL traces, including LangGraph's internal graph execution traces.
    
    Args:
        data: Dictionary to potentially truncate
        
    Returns:
        Truncated dictionary that serializes under _TRACE_BYTE_BUDGET bytes
    """
    if not isinstance(data, dict):
        return data
    
    try:
        # Fast path: check if already under budget
        serialized = json.dumps(data, default=str)
        if len(serialized) <= _TRACE_BYTE_BUDGET:
            return data
        
        # Over budget - create truncated copy
        result = {}
        current_size = 2  # Start with "{}"
        
        for key, value in data.items():
            try:
                value_json = json.dumps(value, default=str)
            except (TypeError, ValueError):
                value_json = '"[unserializable]"'
            
            # Account for key + colon + comma
            entry_size = len(json.dumps(key)) + 1 + len(value_json) + 1
            
            if current_size + entry_size > _TRACE_BYTE_BUDGET:
                # Truncate this value
                remaining = _TRACE_BYTE_BUDGET - current_size - len(json.dumps(key)) - 50
                if remaining > 100:
                    # Include truncated version
                    truncated = value_json[:remaining] + _TRUNCATION_MARKER
                    try:
                        result[key] = json.loads(truncated)
                    except json.JSONDecodeError:
                        result[key] = truncated
                else:
                    result[key] = _TRUNCATION_MARKER
                break
            else:
                result[key] = value
                current_size += entry_size
        
        return result
    except Exception:
        # Safety fallback - return minimal data
        return {"_truncated": True, "_reason": "serialization error"}


def _configure_langsmith_client():
    """Configure the global LangSmith client with trace size limits.
    
    This MUST be called before any LangChain/LangGraph code creates traces.
    It sets up hide_inputs/hide_outputs functions that truncate large payloads.
    """
    if not os.environ.get("LANGCHAIN_TRACING_V2", "").lower() == "true":
        return  # Tracing disabled, no need to configure
    
    try:
        import langsmith.run_trees as rt
        from langsmith import Client
        
        # Check if client already initialized
        if rt._CLIENT is not None:
            return  # Already configured
        
        # Initialize with our truncation functions
        with rt._LOCK:
            if rt._CLIENT is None:
                rt._CLIENT = Client(
                    hide_inputs=_truncate_for_trace,
                    hide_outputs=_truncate_for_trace,
                )
                logging.getLogger(__name__).info(
                    "P0.2: LangSmith client configured with trace byte budget enforcement"
                )
    except ImportError:
        pass  # LangSmith not installed
    except Exception as e:
        logging.getLogger(__name__).warning(
            f"Failed to configure LangSmith client: {e}"
        )


# Initialize LangSmith client with byte budget BEFORE any LangChain/LangGraph imports
_configure_langsmith_client()

# =============================================================================
# Standard imports (after LangSmith configuration)
# =============================================================================
import asyncio
import functools
import gc
import inspect
import sys
import time
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from enum import Enum, auto
from typing import Any, Callable, List, Optional, Union
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from integration_coworker.graph.state import WorkflowState
from integration_coworker.shutdown import (
    get_shutdown_manager,
    is_shutdown_requested,
    ShutdownManager,
)
from integration_coworker.graph.state_v2 import (
    WorkflowStateDict,
    dataclass_to_dict,
    dict_to_dataclass,
)
from integration_coworker.graph.bounds import (
    get_bounds_config,
    get_invoke_config,
    bounded_run_context,
    BoundedExecutionConfig,
    NodeTimeoutError,
    GlobalTimeoutError,
    get_budget_tracker,
)
# Centralized node names - single source of truth
from integration_coworker.graph.node_names import (
    WORKFLOW_NODE_ORDER as _CANONICAL_NODE_ORDER,
    NODE_DEPENDENCIES as _CANONICAL_DEPENDENCIES,
    NODE_CATEGORY_MAP,
    HITL_EXEMPT_NODES,
    get_node_names,
    get_dependent_nodes,
    is_hitl_exempt,
    get_node_category,
    CODE_REVIEW_GATE,
    SANDBOX_REVIEW_GATE,
)
# Node contract helpers for type-safe state access
from integration_coworker.graph.node_contract import (
    validate_node_result,
    normalize_node_result,
    state_attr,
    state_optional,
    get_run_id_from_state,
    get_options_attr,
    NodeResultType,
)
from integration_coworker.llm.exceptions import (
    LLMError,
    LLMAuthError,
    LLMRateLimitError,
    LLMTransientError,
    LLMContentFilterError,
    LLMContextLengthError,
)

# NOTE: Don't import via `from integration_coworker.graph.nodes import (...)`.
# The `graph/nodes/` directory is currently a namespace package (no __init__.py).
# Importing `integration_coworker.graph.nodes` can resolve to an empty namespace
# module depending on environment/tooling, which breaks node resolution.
# Import node modules explicitly instead.
from integration_coworker.graph.nodes import discover_spec  # Slice 1: Spec auto-discovery
from integration_coworker.graph.nodes import plan_run
from integration_coworker.graph.nodes import ingest_spec
from integration_coworker.graph.nodes import detect_and_parse_spec
from integration_coworker.graph.nodes import build_silver_api_model
from integration_coworker.graph.nodes import build_silver_file_model
from integration_coworker.graph.nodes import embed_spec_chunks
from integration_coworker.graph.nodes import understand_task
from integration_coworker.graph.nodes import align_task_with_kg
from integration_coworker.graph.nodes import plan_integration_flow
from integration_coworker.graph.nodes import attach_policies_and_patterns
from integration_coworker.graph.nodes import attach_repo_context
from integration_coworker.graph.nodes import generate_code_and_tests
from integration_coworker.graph.nodes import analyze_repo_layout
from integration_coworker.graph.nodes import apply_repo_integration_changes
from integration_coworker.graph.nodes import validate_integration_design
from integration_coworker.graph.nodes import persist_results
from integration_coworker.graph.nodes import build_report
from integration_coworker.graph.nodes import handle_error
from integration_coworker.graph.nodes import persist_silver_checkpoint
from integration_coworker.graph.nodes import persist_gold_checkpoint
from integration_coworker.graph.nodes import persist_run_outcome
from integration_coworker.graph.nodes import persist_kg_learning
from integration_coworker.graph.nodes import hitl_gate  # HITL approval gate (deprecated - use review_gate)
from integration_coworker.graph.nodes.review_gate import review_gate  # Parameterized review gate factory
from integration_coworker.graph.nodes.static_analysis_gate import static_analysis_gate  # PR #8: Static analysis
from integration_coworker.graph.nodes.sandbox_attribution_gate import sandbox_attribution_gate  # PR #9: Attribution
from integration_coworker.graph.nodes.targeted_regeneration import targeted_regeneration  # PR #10: Targeted regen
from integration_coworker.graph.nodes.apply_human_edits import apply_human_edits  # PR #11: Human edits
from integration_coworker.llm.client import set_run_context, clear_run_context

logger = logging.getLogger(__name__)


# =============================================================================
# V23-005: Graceful Shutdown State Tracking
# =============================================================================
# Track the current run state for atexit handling.
# When process receives SIGTERM/SIGINT or exits unexpectedly, we can
# persist the run outcome to avoid orphaned runs with "running" status.

import atexit
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from integration_coworker.graph.state import WorkflowState

# Thread-safe tracking of current run state
_current_run_state: Optional["WorkflowState"] = None
_current_run_lock = threading.Lock()


def _set_current_run_state(state: Optional["WorkflowState"]) -> None:
    """Set the current run state for atexit handling."""
    global _current_run_state
    with _current_run_lock:
        _current_run_state = state


def _get_current_run_state() -> Optional["WorkflowState"]:
    """Get the current run state for atexit handling."""
    with _current_run_lock:
        return _current_run_state


def _atexit_persist_run_outcome() -> None:
    """
    V23-005: atexit handler to persist run outcome on unexpected exit.
    
    This catches cases where the process exits without completing the workflow
    (e.g., SIGKILL after grace period, OOM kill, unhandled exception).
    """
    state = _get_current_run_state()
    if state is None:
        return  # No active run
    
    run_id = state.run_id
    if not run_id:
        return  # No run_id to persist
    
    # Check if already completed
    completed_steps = getattr(state, 'completed_steps', []) or []
    if 'persist_run_outcome' in completed_steps:
        return  # Already persisted
    
    try:
        logger.warning(f"V23-005: atexit handler persisting outcome for interrupted run {run_id}")
        
        # Add error indicating unexpected exit
        if not hasattr(state, 'errors') or state.errors is None:
            state.errors = []
        state.errors.append({
            "node": "atexit",
            "error": "Run interrupted - atexit handler invoked",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        
        # Import here to avoid circular imports
        from integration_coworker.graph.nodes.persist_run_outcome import persist_run_outcome as _persist_outcome
        _persist_outcome(state)
        logger.info(f"V23-005: atexit successfully persisted outcome for {run_id}")
    except Exception as e:
        # Can't log in atexit context reliably, use print
        print(f"V23-005 atexit: Failed to persist outcome for {run_id}: {e}")


# Register atexit handler
atexit.register(_atexit_persist_run_outcome)


# =============================================================================
# Graph Node Lifecycle Logging Infrastructure (Step 1)
# =============================================================================
#
# Log Schema v1.0 - Stable JSON-line format for graph node events
#
# This extends the sandbox logging pattern to graph-level visibility.
# Events are written to GRAPH_TRACE.jsonl in the run's artifact directory.
#
# REQUIRED FIELDS (always present):
#   ts            - ISO8601 UTC timestamp
#   level         - Log level (DEBUG, INFO, WARNING, ERROR)
#   event         - Event name from fixed taxonomy below
#   run_id        - Unique run identifier (uuid4)
#   graph_run_id  - Graph execution identifier (for parallel/nested runs)
#   node_name     - Node name being executed
#   trace_id      - W3C trace correlation ID
#   span_id       - Span ID for this node execution
#   pid           - Process ID
#
# RECOMMENDED FIELDS (when applicable):
#   attempt       - Retry attempt number (1-based)
#   input_bytes   - Size of input state
#   input_keys    - List of non-empty state keys
#   duration_ms   - Execution time in milliseconds
#   output_bytes  - Size of output state
#   output_keys   - List of modified state keys
#   error_class   - Error classification (AUTH, RATE_LIMIT, etc.)
#   error_message - Truncated error message
#   error_type    - Exception class name
#   stack_hash    - SHA256 prefix of stack trace
#
# EVENT TAXONOMY (fixed set - do not deviate):
#   graph.node.start          - Node execution starting
#   graph.node.end            - Node execution completed successfully
#   graph.node.skip           - Node skipped (already completed during resume)
#   graph.node.error          - Node execution failed
#   graph.node.timeout        - Node timed out (P2 bounded execution)
#   graph.node.checkpoint     - Checkpoint saved after node
#   graph.workflow.start      - Workflow execution starting
#   graph.workflow.end        - Workflow execution completed
#   graph.workflow.error      - Workflow execution failed
#
# SUB-STEP EVENTS (for tail node deep instrumentation):
#   validate_design.*         - validate_integration_design sub-steps
#   handle_error.*            - handle_error sub-steps
#   build_report.*            - build_report sub-steps
#   persist_run_outcome.*     - persist_run_outcome sub-steps (DB-level timings)
#
# =============================================================================
import contextvars
import hashlib
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Import shared context variables and helpers from node_trace
# (node_trace is the canonical source to avoid circular imports)
from integration_coworker.graph.node_trace import (
    _graph_run_id,
    _current_node_name,
    _trace_id,
    _node_span_id as _span_id,
    _artifacts_dir,
    _generate_span_id,
    _generate_trace_id,
    _sha256_prefix,
    write_slow_run_bundle,
    get_slowest_steps,
    clear_step_timings,
)

# Additional runtime-specific context variables
_current_attempt: contextvars.ContextVar[int] = contextvars.ContextVar("current_attempt", default=1)

# Log level name mapping
_LEVEL_NAMES = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


# =============================================================================
# V22-001.1: Hardened State Size Estimation and Memory Monitoring
# =============================================================================
# Configuration for bounded sampling estimator
_MAX_STATE_SIZE_ESTIMATE = 100_000_000  # 100MB cap for capped value
_MAX_SAMPLE_DEPTH = 4  # Maximum recursion depth
_MAX_SAMPLE_ITEMS = 50  # Items to sample from large collections

# Estimation method identifier for auditable metrics
_ESTIMATION_METHOD = "sampled_recursive"

# tracemalloc state (lazily initialized)
_tracemalloc_enabled = False


def _sample_object_size(obj: Any, depth: int = 0) -> int:
    """
    Recursively sample object size with bounded depth and deterministic traversal.
    
    V22-001.1 Hardening:
    - Sorted key traversal for dicts (deterministic across runs)
    - Stable sampling for reproducible metrics
    - Bounded depth to prevent infinite recursion
    
    Args:
        obj: Object to measure
        depth: Current recursion depth (bounded by _MAX_SAMPLE_DEPTH)
    
    Returns:
        Estimated size in bytes
    """
    if depth > _MAX_SAMPLE_DEPTH:
        return 100  # Placeholder for deeply nested content
    
    if obj is None:
        return 0
    
    # Primitives
    if isinstance(obj, (str, bytes)):
        return len(obj)
    if isinstance(obj, (int, float, bool)):
        return 8
    
    # Dictionaries - DETERMINISTIC: sort by stringified keys, then sample first N
    if isinstance(obj, dict):
        if not obj:
            return 0
        try:
            # Sort keys deterministically (stringify for mixed types)
            sorted_keys = sorted(obj.keys(), key=lambda k: str(k))
        except TypeError:
            # Fallback if comparison fails
            sorted_keys = list(obj.keys())
        
        # Take first N items in sorted order
        sample_keys = sorted_keys[:_MAX_SAMPLE_ITEMS]
        sample_size = sum(
            _sample_object_size(k, depth + 1) + _sample_object_size(obj[k], depth + 1)
            for k in sample_keys
        )
        if len(obj) > _MAX_SAMPLE_ITEMS:
            # Extrapolate based on sample
            return sample_size * (len(obj) // len(sample_keys))
        return sample_size
    
    # Lists/tuples - take first N items (already ordered)
    if isinstance(obj, (list, tuple)):
        if not obj:
            return 0
        items = list(obj)[:_MAX_SAMPLE_ITEMS]
        sample_size = sum(_sample_object_size(x, depth + 1) for x in items)
        if len(obj) > _MAX_SAMPLE_ITEMS:
            return sample_size * (len(obj) // len(items))
        return sample_size
    
    # Sets/frozensets - DETERMINISTIC: sort by stringified value, then sample
    if isinstance(obj, (set, frozenset)):
        if not obj:
            return 0
        try:
            sorted_items = sorted(obj, key=lambda x: str(x))
        except TypeError:
            sorted_items = list(obj)
        
        items = sorted_items[:_MAX_SAMPLE_ITEMS]
        sample_size = sum(_sample_object_size(x, depth + 1) for x in items)
        if len(obj) > _MAX_SAMPLE_ITEMS:
            return sample_size * (len(obj) // len(items))
        return sample_size
    
    # Objects with __dict__ (dataclasses, custom objects)
    if hasattr(obj, '__dict__'):
        return _sample_object_size(obj.__dict__, depth + 1)
    
    # Fallback to sys.getsizeof for unknown types
    return sys.getsizeof(obj)


def _get_state_size_uncapped(state: Optional['WorkflowState']) -> int:
    """
    Estimate serialized size WITHOUT capping.
    
    Returns the raw estimate for debugging regressions.
    Large values indicate either real bloat or estimation edge cases.
    """
    if state is None:
        return 0
    try:
        total = 0
        for k in dir(state):
            if k.startswith("_"):
                continue
            v = getattr(state, k, None)
            if v is None or callable(v):
                continue
            total += _sample_object_size(v, depth=0)
        return total
    except Exception:
        return 0


def _get_state_size(state: Optional['WorkflowState']) -> int:
    """
    Estimate serialized size of workflow state (CAPPED version).
    
    V22-001.1: Returns min(estimate, 100MB) to prevent misleading metrics.
    Use _get_state_size_uncapped() to see raw values for debugging.
    """
    uncapped = _get_state_size_uncapped(state)
    return min(uncapped, _MAX_STATE_SIZE_ESTIMATE)


def _get_state_size_metrics(state: Optional['WorkflowState']) -> dict:
    """
    Get full state size metrics for auditable logging.
    
    V22-001.1 Hardening: Returns both capped and uncapped values plus
    method parameters so metrics are comparable and auditable.
    
    Returns:
        dict with keys:
        - estimate_uncapped: Raw estimate in bytes
        - estimate_capped: Capped estimate (max 100MB)
        - method: "sampled_recursive"
        - max_depth: Recursion depth limit
        - max_sample_items: Sample size for large collections
    """
    uncapped = _get_state_size_uncapped(state)
    return {
        "estimate_uncapped": uncapped,
        "estimate_capped": min(uncapped, _MAX_STATE_SIZE_ESTIMATE),
        "method": _ESTIMATION_METHOD,
        "max_depth": _MAX_SAMPLE_DEPTH,
        "max_sample_items": _MAX_SAMPLE_ITEMS,
    }


def _get_rss_bytes() -> int:
    """
    Get actual process memory (RSS) in bytes.
    
    V22-001.1 Hardening: Uses psutil if available, falls back to /proc/self/statm
    on Linux, returns -1 only if both fail.
    
    Returns:
        RSS in bytes, or -1 if unavailable
    """
    # Try psutil first (cross-platform, preferred)
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except ImportError:
        pass
    except Exception:
        pass
    
    # Fallback: read /proc/self/statm on Linux
    try:
        with open('/proc/self/statm', 'r') as f:
            # Format: size resident shared text lib data dt
            # Values are in pages, typically 4KB
            parts = f.read().split()
            resident_pages = int(parts[1])
            page_size = 4096  # Standard page size
            try:
                import os
                page_size = os.sysconf('SC_PAGE_SIZE')
            except (AttributeError, ValueError):
                pass
            return resident_pages * page_size
    except (FileNotFoundError, IOError, IndexError, ValueError):
        pass
    
    return -1


def _get_rss_mb() -> float:
    """
    Get actual process memory (RSS) in MB.
    
    Returns:
        RSS in MB, or -1.0 if unavailable
    """
    rss_bytes = _get_rss_bytes()
    if rss_bytes < 0:
        return -1.0
    return rss_bytes / (1024 * 1024)


def _init_tracemalloc_if_enabled() -> bool:
    """
    Initialize tracemalloc for Python allocation tracking.
    
    V22-001.1: tracemalloc shows Python allocation hotspots separately
    from RSS (which includes native allocations and allocator overhead).
    
    Returns:
        True if tracemalloc is now running
    """
    global _tracemalloc_enabled
    if _tracemalloc_enabled:
        return True
    
    try:
        import tracemalloc
        if not tracemalloc.is_tracing():
            tracemalloc.start()
        _tracemalloc_enabled = True
        return True
    except Exception:
        return False


def _get_tracemalloc_stats() -> dict:
    """
    Get current and peak Python allocations from tracemalloc.
    
    V22-001.1: Complements RSS by showing Python-specific allocations.
    
    Returns:
        dict with keys:
        - current_bytes: Current traced Python allocations
        - peak_bytes: Peak traced Python allocations since start
        - enabled: Whether tracemalloc is active
    """
    try:
        import tracemalloc
        if not tracemalloc.is_tracing():
            return {"current_bytes": -1, "peak_bytes": -1, "enabled": False}
        
        current, peak = tracemalloc.get_traced_memory()
        return {
            "current_bytes": current,
            "peak_bytes": peak,
            "enabled": True,
        }
    except ImportError:
        return {"current_bytes": -1, "peak_bytes": -1, "enabled": False}
    except Exception:
        return {"current_bytes": -1, "peak_bytes": -1, "enabled": False}


def _get_memory_diagnostics() -> dict:
    """
    Get comprehensive memory diagnostics for debugging.
    
    V22-001.1: Combines RSS, tracemalloc, and process info for kill analysis.
    
    Returns:
        dict with keys for RSS, tracemalloc, and environment info
    """
    diag = {
        "rss_bytes": _get_rss_bytes(),
        "rss_mb": _get_rss_mb(),
    }
    
    # Add tracemalloc stats
    diag.update({f"tracemalloc_{k}": v for k, v in _get_tracemalloc_stats().items()})
    
    # Check for Docker/cgroup memory limits
    try:
        # cgroups v2
        with open('/sys/fs/cgroup/memory.max', 'r') as f:
            limit = f.read().strip()
            diag["cgroup_memory_limit"] = int(limit) if limit != "max" else -1
    except (FileNotFoundError, ValueError):
        try:
            # cgroups v1
            with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'r') as f:
                diag["cgroup_memory_limit"] = int(f.read().strip())
        except (FileNotFoundError, ValueError):
            diag["cgroup_memory_limit"] = -1
    
    # Check current cgroup usage
    try:
        with open('/sys/fs/cgroup/memory.current', 'r') as f:
            diag["cgroup_memory_current"] = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        try:
            with open('/sys/fs/cgroup/memory/memory.usage_in_bytes', 'r') as f:
                diag["cgroup_memory_current"] = int(f.read().strip())
        except (FileNotFoundError, ValueError):
            diag["cgroup_memory_current"] = -1
    
    return diag


def _check_oom_likelihood() -> dict:
    """
    Check if current memory usage suggests OOM risk.
    
    V22-001.1: For diagnosing exit code 137 (SIGKILL) events.
    
    Returns:
        dict with:
        - at_risk: bool, True if memory usage > 80% of limit
        - usage_pct: float, percentage of limit used (or -1 if unknown)
        - diagnosis: str, human-readable status
    """
    diag = _get_memory_diagnostics()
    
    limit = diag.get("cgroup_memory_limit", -1)
    current = diag.get("cgroup_memory_current", -1)
    rss = diag.get("rss_bytes", -1)
    
    if limit > 0 and current > 0:
        pct = (current / limit) * 100
        at_risk = pct > 80
        return {
            "at_risk": at_risk,
            "usage_pct": round(pct, 1),
            "limit_mb": round(limit / (1024 * 1024), 1),
            "current_mb": round(current / (1024 * 1024), 1),
            "diagnosis": f"cgroup: {pct:.1f}% of {limit / (1024**3):.1f}GB limit"
        }
    elif rss > 0:
        return {
            "at_risk": False,  # Can't determine without limit
            "usage_pct": -1,
            "rss_mb": round(rss / (1024 * 1024), 1),
            "diagnosis": f"No cgroup limit detected, RSS={rss / (1024**2):.1f}MB"
        }
    else:
        return {
            "at_risk": False,
            "usage_pct": -1,
            "diagnosis": "Unable to determine memory status"
        }


def _get_non_empty_keys(state: Optional['WorkflowState']) -> List[str]:
    """Get list of non-empty/non-default state keys."""
    if state is None:
        return []
    try:
        keys = []
        for k in dir(state):
            if k.startswith("_"):
                continue
            v = getattr(state, k, None)
            if v is None:
                continue
            if isinstance(v, (list, dict, set)) and len(v) == 0:
                continue
            if isinstance(v, str) and len(v) == 0:
                continue
            if callable(v):
                continue
            keys.append(k)
        return keys
    except Exception:
        return []


def _log_graph_event(
    event: str,
    run_id: Optional[str] = None,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """
    Emit a structured log event for graph node execution.
    
    All events follow the Log Schema v1.0 with required fields always present.
    Events use the fixed taxonomy (see module header).
    
    Also emits to the progress emitter (if set in context) for real-time
    visibility via callbacks.
    
    Args:
        event: Event name from taxonomy (e.g., "graph.node.start", "graph.node.end")
        run_id: Run ID (overrides context if provided)
        level: Log level (default INFO)
        **fields: Additional structured fields
    """
    # Build payload with REQUIRED fields first (always present)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": _LEVEL_NAMES.get(level, "INFO"),
        "event": event,
        "run_id": run_id,
        "graph_run_id": _graph_run_id.get(),
        "node_name": _current_node_name.get(),
        "trace_id": _trace_id.get(),
        "span_id": _span_id.get(),
        "pid": os.getpid(),
    }
    
    # Add optional fields
    payload.update(fields)
    
    # Emit as JSON-line (single line, no indentation)
    json_line = json.dumps(payload, default=str, separators=(",", ":"))
    
    # Log to standard logger for console/file output
    logger.log(level, f"[GRAPH] {json_line}")
    
    # Write to GRAPH_TRACE.jsonl if artifacts directory is set
    _write_graph_trace_line(json_line)
    
    # Emit to progress subscribers (if any)
    _emit_progress_event(event, run_id, fields)


def _emit_progress_event(
    event: str,
    run_id: Optional[str],
    fields: Dict[str, Any],
) -> None:
    """
    Emit a progress event to registered callbacks via the progress emitter.
    
    Maps graph events (graph.node.*) to ProgressEvent types and emits to
    all registered subscribers. This is the bridge between the internal
    graph logging and the external progress API.
    
    Thread-safe: Emitter handles callback isolation.
    Non-blocking: Errors are logged but never raised.
    
    Args:
        event: Graph event name (e.g., "graph.node.start")
        run_id: Run identifier
        fields: Additional event fields (duration_ms, error_message, etc.)
    """
    # Lazy import to avoid circular dependencies
    from integration_coworker.progress import get_progress_emitter, ProgressEvent
    
    emitter = get_progress_emitter()
    if emitter is None:
        return  # No subscribers, skip
    
    try:
        # Map graph event to progress event type
        event_type = _map_graph_event_to_progress_type(event)
        if event_type is None:
            return  # Not a mappable event
        
        # Get node context
        node_name = _current_node_name.get()
        
        # Calculate node index for progress percentage
        node_index = None
        total_nodes = len(WORKFLOW_NODE_ORDER)
        if node_name and node_name in WORKFLOW_NODE_ORDER:
            node_index = WORKFLOW_NODE_ORDER.index(node_name)
        
        # Extract duration if present
        duration_ms = fields.get("duration_ms")
        
        # Extract error info if present
        error = fields.get("error_message") or fields.get("error")
        if error and isinstance(error, str) and len(error) > 500:
            error = error[:500] + "..."
        
        # Create progress event
        progress_event = ProgressEvent.create(
            run_id=run_id or _graph_run_id.get() or "unknown",
            event_type=event_type,
            node_name=node_name,
            node_index=node_index,
            total_nodes=total_nodes,
            duration_ms=duration_ms,
            error=error,
            metadata={
                "trace_id": _trace_id.get(),
                "span_id": _span_id.get(),
            } if _trace_id.get() else None,
        )
        
        # Emit to subscribers
        emitter.emit(progress_event)
        
    except Exception as e:
        # Never fail the workflow due to progress emission
        logger.debug(f"Failed to emit progress event: {e}")


def _map_graph_event_to_progress_type(event: str) -> Optional[str]:
    """
    Map internal graph event names to progress event types.
    
    Args:
        event: Graph event name (e.g., "graph.node.start")
        
    Returns:
        Progress event type string, or None if not mappable.
    """
    mapping = {
        "graph.node.start": "node.start",
        "graph.node.end": "node.end",
        "graph.node.skip": "node.skip",
        "graph.node.error": "node.error",
        "graph.node.timeout": "node.timeout",
        "graph.workflow.start": "workflow.start",
        "graph.workflow.end": "workflow.end",
        "graph.workflow.error": "workflow.error",
    }
    return mapping.get(event)


def _write_graph_trace_line(json_line: str) -> None:
    """
    Append a JSON-line to GRAPH_TRACE.jsonl in the artifacts directory.
    
    This is an append-only log file for post-mortem analysis.
    Thread-safe via atomic append mode.
    """
    artifacts_dir = _artifacts_dir.get()
    if not artifacts_dir:
        return
    
    try:
        trace_file = Path(artifacts_dir) / "GRAPH_TRACE.jsonl"
        # Ensure directory exists
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        # Atomic append (O_APPEND is atomic on POSIX for writes < PIPE_BUF)
        with open(trace_file, "a", encoding="utf-8") as f:
            f.write(json_line + "\n")
    except Exception as e:
        logger.warning(f"Failed to write graph trace: {e}")


def _write_node_failure_bundle(
    node_name: str,
    run_id: Optional[str],
    error: Exception,
    state: Optional['WorkflowState'] = None,
) -> Optional[Path]:
    """
    Write NODE_FAILURE_BUNDLE.<node_name>.json on graph.node.error.
    
    Contains all context needed for post-mortem debugging:
    - Error details (type, message, stack trace hash)
    - Node context (name, attempt, timing)
    - State snapshot (non-empty keys, sizes)
    - Environment info
    
    Args:
        node_name: Name of the failed node
        run_id: Run identifier
        error: The exception that caused failure
        state: Optional workflow state at failure time
        
    Returns:
        Path to the written bundle, or None if writing failed
    """
    artifacts_dir = _artifacts_dir.get()
    if not artifacts_dir:
        return None
    
    try:
        # Build failure bundle
        stack_trace = traceback.format_exc()
        bundle = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "node_name": node_name,
            "run_id": run_id,
            "graph_run_id": _graph_run_id.get(),
            "trace_id": _trace_id.get(),
            "span_id": _span_id.get(),
            "attempt": _current_attempt.get(),
            "error": {
                "type": type(error).__name__,
                "message": str(error)[:1000],  # Truncate long messages
                "stack_hash": _sha256_prefix(stack_trace),
                "stack_trace": stack_trace[:10000],  # Truncate very long traces
            },
            "state_snapshot": {
                "non_empty_keys": _get_non_empty_keys(state) if state else [],
                "estimated_size_bytes": _get_state_size(state) if state else 0,
            },
            "environment": {
                "pid": os.getpid(),
                "python_version": os.sys.version,
                "hostname": os.uname().nodename if hasattr(os, "uname") else "unknown",
            },
        }
        
        # Write bundle
        bundle_path = Path(artifacts_dir) / f"NODE_FAILURE_BUNDLE.{node_name}.json"
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        with open(bundle_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2, default=str)
        
        logger.info(f"Node failure bundle written: {bundle_path}")
        return bundle_path
        
    except Exception as e:
        logger.warning(f"Failed to write node failure bundle: {e}")
        return None


def set_graph_trace_context(
    run_id: str,
    artifacts_dir: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Set up graph tracing context for a workflow run.
    
    Should be called at workflow start. Returns tokens for cleanup.
    
    Args:
        run_id: The workflow run identifier
        artifacts_dir: Directory for GRAPH_TRACE.jsonl and failure bundles
        trace_id: Optional W3C trace ID (generated if not provided)
        
    Returns:
        Dict of context var tokens for later cleanup
    """
    tokens = {}
    tokens["graph_run_id"] = _graph_run_id.set(run_id)
    tokens["trace_id"] = _trace_id.set(trace_id or _generate_trace_id())
    if artifacts_dir:
        tokens["artifacts_dir"] = _artifacts_dir.set(artifacts_dir)
    return tokens


def clear_graph_trace_context(tokens: Dict[str, Any]) -> None:
    """Reset graph tracing context using tokens from set_graph_trace_context."""
    for var_name, token in tokens.items():
        var = {
            "graph_run_id": _graph_run_id,
            "trace_id": _trace_id,
            "artifacts_dir": _artifacts_dir,
        }.get(var_name)
        if var:
            var.reset(token)


@contextmanager
def graph_trace_context(
    run_id: str,
    artifacts_dir: Optional[str] = None,
    trace_id: Optional[str] = None,
):
    """
    Context manager for graph tracing.
    
    Usage:
        with graph_trace_context(run_id, artifacts_dir):
            # Graph events will be logged with correlation IDs
            result = await run_workflow(state)
    """
    tokens = set_graph_trace_context(run_id, artifacts_dir, trace_id)
    try:
        yield
    finally:
        clear_graph_trace_context(tokens)


# =============================================================================
# Error Classification and Policy (Production Readiness v4 - P0-5)
# =============================================================================
# These enums and functions enforce fail-fast semantics at the wrapper level.
# Auth errors are NEVER retried. Rate limits MAY be retried. Other errors
# are classified and handled according to policy.
# =============================================================================

class ErrorClass(Enum):
    """Classification of errors for policy-based handling.
    
    Production Readiness v4 - P0-5:
    - AUTH: Authentication/authorization failures - fail immediately
    - RATE_LIMIT: Rate limiting - may retry with backoff
    - TRANSIENT: Temporary errors (network, 5xx) - may retry
    - CONTENT_FILTER: Content blocked by safety - fail (no retry)
    - CONTEXT_LENGTH: Input too long - fail (no retry)
    - UNKNOWN: Unclassified errors - treated as transient for safety
    """
    AUTH = auto()
    RATE_LIMIT = auto()
    TRANSIENT = auto()
    CONTENT_FILTER = auto()
    CONTEXT_LENGTH = auto()
    UNKNOWN = auto()


class ErrorPolicy(Enum):
    """Policy for how to handle an error class.
    
    Production Readiness v4 - P0-5:
    - FAIL_FAST: Re-raise immediately, never retry
    - RETRY_WITH_BACKOFF: Retry with exponential backoff
    - LOG_AND_CONTINUE: Log the error but continue execution
    """
    FAIL_FAST = auto()
    RETRY_WITH_BACKOFF = auto()
    LOG_AND_CONTINUE = auto()


# Error class to policy mapping
ERROR_POLICIES: Dict[ErrorClass, ErrorPolicy] = {
    ErrorClass.AUTH: ErrorPolicy.FAIL_FAST,
    ErrorClass.RATE_LIMIT: ErrorPolicy.RETRY_WITH_BACKOFF,
    ErrorClass.TRANSIENT: ErrorPolicy.RETRY_WITH_BACKOFF,
    ErrorClass.CONTENT_FILTER: ErrorPolicy.FAIL_FAST,
    ErrorClass.CONTEXT_LENGTH: ErrorPolicy.FAIL_FAST,
    # Production Readiness v4 (FIXED): UNKNOWN defaults to FAIL_FAST (fail-closed)
    # Previously defaulted to RETRY which could spin on programmer bugs and corrupt state.
    # Unknown errors should stop execution immediately to surface the issue.
    ErrorClass.UNKNOWN: ErrorPolicy.FAIL_FAST,
}


def classify_error(exc: Exception) -> ErrorClass:
    """Classify an exception into an ErrorClass for policy-based handling.
    
    Production Readiness v4 - P0-5:
    This function enables the wrapper to apply consistent error handling
    across all nodes. Auth errors always fail-fast. Rate limits and
    transient errors may be retried.
    
    Args:
        exc: The exception to classify
        
    Returns:
        ErrorClass indicating how to handle the error
        
    Examples:
        >>> classify_error(LLMAuthError("Invalid key"))
        ErrorClass.AUTH
        >>> classify_error(LLMRateLimitError("Too many requests"))
        ErrorClass.RATE_LIMIT
    """
    # Typed exceptions from our hierarchy
    if isinstance(exc, LLMAuthError):
        return ErrorClass.AUTH
    if isinstance(exc, LLMRateLimitError):
        return ErrorClass.RATE_LIMIT
    if isinstance(exc, LLMTransientError):
        return ErrorClass.TRANSIENT
    if isinstance(exc, LLMContentFilterError):
        return ErrorClass.CONTENT_FILTER
    if isinstance(exc, LLMContextLengthError):
        return ErrorClass.CONTEXT_LENGTH
    if isinstance(exc, LLMError):
        # Generic LLM error - treat as transient
        return ErrorClass.TRANSIENT
    
    # Pattern matching for untyped exceptions
    error_str = str(exc).lower()
    
    # Auth patterns - fail fast
    auth_patterns = ['401', '403', 'unauthorized', 'invalid api key', 
                     'invalid_api_key', 'authentication failed', 'permission denied']
    if any(p in error_str for p in auth_patterns):
        return ErrorClass.AUTH
    
    # Rate limit patterns - retry
    rate_patterns = ['429', 'rate limit', 'rate_limit', 'too many requests', 'quota']
    if any(p in error_str for p in rate_patterns):
        return ErrorClass.RATE_LIMIT
    
    # Transient patterns - retry
    transient_patterns = ['500', '502', '503', '504', 'timeout', 'connection', 
                          'temporary', 'unavailable', 'retry']
    if any(p in error_str for p in transient_patterns):
        return ErrorClass.TRANSIENT
    
    # Content filter patterns - fail fast
    filter_patterns = ['content filter', 'safety', 'blocked', 'flagged']
    if any(p in error_str for p in filter_patterns):
        return ErrorClass.CONTENT_FILTER
    
    # Context length patterns - fail fast
    context_patterns = ['context length', 'maximum context', 'too long', 'token limit']
    if any(p in error_str for p in context_patterns):
        return ErrorClass.CONTEXT_LENGTH
    
    return ErrorClass.UNKNOWN


def get_error_policy(exc: Exception) -> ErrorPolicy:
    """Get the handling policy for an exception.
    
    Args:
        exc: The exception to get policy for
        
    Returns:
        ErrorPolicy indicating how to handle the error
    """
    error_class = classify_error(exc)
    return ERROR_POLICIES.get(error_class, ErrorPolicy.RETRY_WITH_BACKOFF)


def should_fail_fast(exc: Exception) -> bool:
    """Check if an exception should cause immediate failure (no retry).
    
    Production Readiness v4 - P0-5:
    This is the primary check used by node wrappers and retry logic.
    
    Args:
        exc: The exception to check
        
    Returns:
        True if the error should not be retried
    """
    return get_error_policy(exc) == ErrorPolicy.FAIL_FAST


# =============================================================================
# Workflow Version (Production Readiness v4 - P0-6)
# =============================================================================
# Tracks workflow version for checkpoint compatibility validation.
# Version is cached per-process (not per-checkpoint) via lru_cache.
# Resume operations can validate version to prevent state corruption.
# =============================================================================

class WorkflowVersionMismatchError(Exception):
    """Raised when checkpoint version doesn't match current workflow version.
    
    Production Readiness v4 - P0-6:
    Prevents resuming from checkpoints created with incompatible workflow versions.
    Use --force to override (with warning about potential state corruption).
    """
    def __init__(self, checkpoint_version: str, current_version: str):
        self.checkpoint_version = checkpoint_version
        self.current_version = current_version
        super().__init__(
            f"Checkpoint version mismatch: checkpoint={checkpoint_version}, "
            f"current={current_version}. Use --force to override."
        )


@functools.lru_cache(maxsize=1)
def get_workflow_version() -> str:
    """
    Get the current workflow version. Cached for process lifetime.
    
    Production Readiness v4 - P0-6:
    Returns a version string that identifies the workflow implementation.
    
    Version sources (in priority order):
    1. Git commit hash (if in git repo): "git:abc123def456"
    2. Package version (if installed): "pkg:1.2.3"
    3. Fallback: "unknown"
    
    Returns:
        Version string identifying the workflow implementation
        
    Examples:
        >>> get_workflow_version()
        'git:abc123def456'
        >>> get_workflow_version()
        'pkg:0.1.0'
    """
    import subprocess
    from pathlib import Path
    
    try:
        # Try git commit hash
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / ".git").exists():
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    cwd=parent,
                    timeout=5,
                )
                if result.returncode == 0:
                    return f"git:{result.stdout.strip()[:12]}"
                break
    except Exception:
        pass
    
    try:
        # Try package version
        import importlib.metadata
        return f"pkg:{importlib.metadata.version('integration_coworker')}"
    except Exception:
        pass
    
    return "unknown"


def validate_checkpoint_version(
    checkpoint_version: str,
    force: bool = False,
) -> None:
    """
    Validate that checkpoint version is compatible with current workflow.
    
    Production Readiness v4 - P0-6:
    Prevents resuming from checkpoints created with incompatible versions.
    
    Production policy (Phase 1):
    - In production profile: "unknown" versions are blocked by default
    - Set ALLOW_UNKNOWN_CHECKPOINT_VERSION=1 to override (logs WARNING)
    - In development profile: "unknown" versions allowed with DEBUG log
    
    Args:
        checkpoint_version: Version stored in checkpoint
        force: If True, log warning but allow mismatch
        
    Raises:
        WorkflowVersionMismatchError: If versions don't match and force=False
    """
    from integration_coworker.config.profiles import is_production_profile
    
    current_version = get_workflow_version()
    
    # Handle unknown versions with production-aware policy
    if checkpoint_version == "unknown" or current_version == "unknown":
        allow_unknown = os.getenv("ALLOW_UNKNOWN_CHECKPOINT_VERSION", "0").lower() in ("1", "true", "yes")
        
        if is_production_profile() and not allow_unknown:
            raise WorkflowVersionMismatchError(
                checkpoint_version, 
                current_version,
            )
        elif is_production_profile() and allow_unknown:
            logger.warning(
                f"Checkpoint version unknown in production (checkpoint={checkpoint_version}, "
                f"current={current_version}). Allowed via ALLOW_UNKNOWN_CHECKPOINT_VERSION=1. "
                "This may cause state corruption."
            )
        else:
            # Development profile: allow with debug log
            logger.debug(
                f"Unknown checkpoint version allowed in development "
                f"(checkpoint={checkpoint_version}, current={current_version})"
            )
        return
    
    if checkpoint_version != current_version:
        if force:
            logger.warning(
                f"Workflow version mismatch: checkpoint={checkpoint_version}, "
                f"current={current_version}. Proceeding with --force (may cause state corruption)"
            )
        else:
            raise WorkflowVersionMismatchError(checkpoint_version, current_version)


def get_versioned_thread_config(run_id: str) -> Dict[str, Any]:
    """
    Get LangGraph config with workflow version in metadata.
    
    Production Readiness v4 - P0-6:
    Extends get_thread_config() to include workflow_version for checkpoints.
    
    Args:
        run_id: The workflow run ID
        
    Returns:
        LangGraph config with thread_id and workflow_version
    """
    config = get_thread_config(run_id)
    config["configurable"]["workflow_version"] = get_workflow_version()
    return config


# =============================================================================
# Thread ID Contract (Agent Harness Alignment Plan)
# =============================================================================
# CRITICAL: thread_id == run_id everywhere. This is the ONLY valid mapping.
# All CLI, Streamlit, and runtime code MUST use get_thread_config() to ensure
# the same thread_id is used for both initial run and resume.
#
# Per LangGraph docs:
# - Persistence requires stable thread_id in configurable
# - Resume must use the SAME thread_id as original run
# - interrupt() payload is stored per-thread
# =============================================================================

def get_thread_config(run_id: str) -> Dict[str, Any]:
    """
    Get the canonical LangGraph config for a run.
    
    CRITICAL CONTRACT: thread_id == run_id. Always use this helper.
    
    This ensures:
    1. Same thread_id for initial run and resume
    2. Checkpoints are isolated per-run
    3. interrupt() payloads are retrievable by run_id
    
    Args:
        run_id: The workflow run ID
        
    Returns:
        LangGraph config dict with thread_id in configurable
        
    Example:
        config = get_thread_config("run-123")
        # Returns: {"configurable": {"thread_id": "run-123"}}
    """
    if not run_id:
        raise ValueError("run_id is required for thread config")
    return {"configurable": {"thread_id": run_id}}


# =============================================================================
# LangGraph Native Checkpointing (Bug #61 Fix - Solution B)
# =============================================================================
# Uses LangGraph's built-in checkpointer for native resume support.
# PostgresSaver for Postgres, SqliteSaver for SQLite fallback.
# =============================================================================

_checkpointer_instance: Optional[BaseCheckpointSaver] = None
_checkpointer_connection = None  # Keep connection alive or hold async aexit

# Async SQLite saver objects (and their internal locks) are bound to the event
# loop they were created in. Pytest creates/destroys event loops across tests,
# so we must not reuse a cached AsyncSqliteSaver across loops.
_sqlite_checkpointer_cache_enabled = False


def _default_db_url() -> str:
    """
    Get the database URL with connection keepalive settings.
    
    Bug fix: Long-running workflows caused "connection is closed" errors because
    PostgreSQL connections timed out. This adds TCP keepalive parameters to
    prevent connection drops during long operations.
    
    Keepalive settings:
    - keepalives=1: Enable TCP keepalives
    - keepalives_idle=60: Start keepalive probes after 60s idle
    - keepalives_interval=10: Send probes every 10s
    - keepalives_count=5: Consider connection dead after 5 failed probes
    - connect_timeout=10: Initial connection timeout
    """
    base_url = os.getenv(
        "DATABASE_URL",
        "postgresql://integration:integration@localhost:5432/integration_coworker",
    )
    
    # Add keepalive parameters if not already present
    if "keepalives" not in base_url:
        separator = "&" if "?" in base_url else "?"
        keepalive_params = (
            f"{separator}keepalives=1"
            "&keepalives_idle=60"
            "&keepalives_interval=10"
            "&keepalives_count=5"
            "&connect_timeout=10"
        )
        return base_url + keepalive_params
    
    return base_url


def _sqlite_checkpoint_path() -> str:
    data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "langgraph_checkpoints.db")


@contextmanager
def checkpointer_context() -> BaseCheckpointSaver:
    """Sync checkpointer context that always yields a saver instance.
    
    Bug #101 Fix v3: Uses SlimPostgresSaver that filters large channels at checkpoint time.
    This prevents checkpoint_blobs from growing to 500MB-2GB per run.
    """
    from integration_coworker.persistence.db import get_engine_type

    engine = get_engine_type()
    if engine == "postgres":
        # Bug #101 Fix v3: Use custom checkpointer that filters large channels
        from integration_coworker.graph.checkpointer import get_slim_postgres_saver
        
        SlimPostgresSaver = get_slim_postgres_saver()
        with SlimPostgresSaver.from_conn_string(_default_db_url()) as saver:
            saver.setup()
            yield saver
        return

    # SQLite sync saver
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver

    sqlite_path = _sqlite_checkpoint_path()
    conn = sqlite3.connect(sqlite_path)
    try:
        saver = SqliteSaver(conn)
        saver.setup()
        yield saver
    finally:
        conn.close()


@asynccontextmanager
async def async_checkpointer_context() -> BaseCheckpointSaver:
    """Async checkpointer context that yields a saver instance (not a context manager).
    
    Bug #101 Fix v3: Uses SlimAsyncPostgresSaver that filters large channels at checkpoint time.
    This prevents checkpoint_blobs from growing to 500MB-2GB per run.
    
    Channels excluded: spec_chunk_ids (exponential growth), openapi_spec, schema_fields, 
    endpoints, schemas, spec_documents, etc. All data persisted to Silver/Gold layer.
    """
    from integration_coworker.persistence.db import get_engine_type

    engine = get_engine_type()
    if engine == "postgres":
        # Bug #101 Fix v3: Use custom checkpointer that filters large channels
        from integration_coworker.graph.checkpointer import get_slim_async_postgres_saver
        
        SlimAsyncPostgresSaver = get_slim_async_postgres_saver()
        async with SlimAsyncPostgresSaver.from_conn_string(_default_db_url()) as saver:
            await saver.setup()
            yield saver
        return

    # SQLite async saver
    import aiosqlite  # noqa: WPS433
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    sqlite_path = _sqlite_checkpoint_path()
    conn = await aiosqlite.connect(sqlite_path)
    try:
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        yield saver
    finally:
        await conn.close()


async def get_checkpointer() -> BaseCheckpointSaver:
    """
    Get or create a LangGraph checkpointer based on configured DB engine.
    
    Returns:
        A checkpointer instance or context manager depending on backend.
        
    Note:
        V3.0: Returns async context manager for AsyncPostgresSaver.
        The caller must use 'async with' to get the actual checkpointer.
        
    Bug #101 Fix v3: Uses SlimAsyncPostgresSaver that filters large channels.
    """
    global _checkpointer_instance, _checkpointer_connection

    if _checkpointer_instance is not None:
        return _checkpointer_instance

    from integration_coworker.persistence.db import get_engine_type
    
    engine = get_engine_type()

    if engine == "postgres":
        try:
            # Bug #101 Fix v3: Use custom checkpointer that filters large channels
            from integration_coworker.graph.checkpointer import get_slim_async_postgres_saver
            
            SlimAsyncPostgresSaver = get_slim_async_postgres_saver()
            cm = SlimAsyncPostgresSaver.from_conn_string(_default_db_url())
            saver = await cm.__aenter__()
            await saver.setup()
            _checkpointer_instance = saver
            _checkpointer_connection = cm  # store context manager for cleanup
            logger.info("Initialized AsyncPostgresSaver with SlimCheckpointSerializer")
            return saver
        except ImportError as e:
            logger.warning(f"langgraph-checkpoint-postgres not installed: {e}, falling back to SQLite")
        except Exception as e:
            logger.warning(f"Failed to initialize AsyncPostgresSaver: {e}, falling back to SQLite")

    try:
        import aiosqlite  # noqa: F401
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "SQLite async checkpointing requires 'aiosqlite' and an async-capable LangGraph saver. "
            "Install aiosqlite or configure Postgres checkpointing."
        ) from e

    sqlite_path = _sqlite_checkpoint_path()

    if not _sqlite_checkpointer_cache_enabled:
        conn = await aiosqlite.connect(sqlite_path)
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        logger.info(f"Initialized AsyncSqliteSaver (non-cached) at {sqlite_path} for LangGraph checkpointing")
        return saver

    _checkpointer_connection = await aiosqlite.connect(sqlite_path)
    _checkpointer_instance = AsyncSqliteSaver(_checkpointer_connection)
    await _checkpointer_instance.setup()

    logger.info(f"Initialized AsyncSqliteSaver (cached) at {sqlite_path} for LangGraph checkpointing")
    return _checkpointer_instance


def reset_checkpointer() -> None:
    """Reset the checkpointer singleton (useful for testing)."""
    global _checkpointer_instance, _checkpointer_connection
    if _checkpointer_connection is not None:
        try:
            # If it's an async context manager from postgres saver
            aexit = getattr(_checkpointer_connection, "__aexit__", None)
            if aexit:
                asyncio.get_event_loop().create_task(aexit(None, None, None))
            else:
                close = getattr(_checkpointer_connection, "close", None)
                if close is not None:
                    close()
        except Exception:
            pass
    _checkpointer_instance = None
    _checkpointer_connection = None


# =============================================================================
# Checkpoint Wrapper (V2 Implementation Plan Section 3.5)
# =============================================================================

def _wrap_node_with_checkpoint(node_func: Callable) -> Callable:
    """
    Decorator that saves checkpoint after successful node execution.
    
    Per V2 Implementation Plan Section 3.5, this enables:
    - Resume capability from any checkpointed node
    - True skip with dependency analysis
    - Recovery from process crashes
    """
    @functools.wraps(node_func)
    def wrapper(state: WorkflowState, *args, **kwargs) -> WorkflowState:
        # Execute node
        new_state = node_func(state, *args, **kwargs)
        
        # Save checkpoint if we have a run_id
        if new_state.run_id:
            try:
                from integration_coworker.persistence.checkpoints import save_checkpoint
                save_checkpoint(
                    run_id=new_state.run_id,
                    node_name=node_func.__name__,
                    state=new_state,
                )
            except Exception as e:
                # Log but don't fail the workflow
                logger.warning(f"Failed to save checkpoint for {node_func.__name__}: {e}")
        
        return new_state
    
    return wrapper


# =============================================================================
# Node Metadata Catalog
# =============================================================================
# Provides self-describing metadata for all non-LLM nodes.
# This makes "0.00s" nodes in LangSmith traces clearly understandable.
#
# Each entry contains:
#   - category: "pure-python" | "db-write" | "api-call" | "llm"
#   - responsibility: Human-readable description of what the node does
# =============================================================================

NODE_METADATA: Dict[str, Dict[str, str]] = {
    # Pure Python nodes - fast computation, no I/O
    "plan_run": {
        "category": "pure-python",
        "responsibility": "Generate run_id, infer provider_code from spec, validate inputs",
    },
    "ingest_spec": {
        "category": "pure-python",
        "responsibility": "Fetch spec content from URLs/files, normalize to raw text",
    },
    "detect_and_parse_spec": {
        "category": "pure-python",
        "responsibility": "Detect spec format (OpenAPI/AsyncAPI), parse to structured dict",
    },
    "build_silver_api_model": {
        "category": "pure-python",
        "responsibility": "Extract endpoints, schemas, entities from parsed spec (Silver model)",
    },
    "attach_policies_and_patterns": {
        "category": "pure-python",
        "responsibility": "Infer auth, rate-limit, retry policies from spec security schemes",
    },
    "attach_repo_context": {
        "category": "pure-python",
        "responsibility": "Detect repo profile (Python/Node/etc), build RepoProfile for codegen",
    },
    "align_task_with_kg": {
        "category": "pure-python",
        "responsibility": "Match task to existing KG templates if available (may skip LLM)",
    },
    "analyze_repo_layout": {
        "category": "pure-python",
        "responsibility": "Analyze target repo structure for marker-based code insertion",
    },
    "code_review_gate": {
        "category": "hitl",
        "responsibility": "HITL: Pause workflow for human approval before destructive writes",
    },
    "sandbox_review_gate": {
        "category": "hitl",
        "responsibility": "HITL: Pause workflow for human review of sandbox failures",
    },
    "apply_repo_integration_changes": {
        "category": "pure-python",
        "responsibility": "Insert generated code at marker locations in target repo",
    },
    "validate_integration_design": {
        "category": "pure-python",
        "responsibility": "Validate flow structure, endpoint bindings, syntax check code artifacts",
    },
    "handle_error": {
        "category": "pure-python",
        "responsibility": "Record error state, set failed flag, preserve partial results",
    },

    # DB checkpoint nodes - write to database
    "persist_silver_checkpoint": {
        "category": "db-write",
        "responsibility": "Persist Silver API model (endpoints, schemas, entities) to database",
    },
    "persist_gold_checkpoint": {
        "category": "db-write",
        "responsibility": "Persist Gold integration (task, workflow, code artifacts) to database",
    },
    "persist_run_outcome": {
        "category": "db-write",
        "responsibility": "Persist final run status, metrics, errors to database",
    },
    "persist_kg_learning": {
        "category": "db-write",
        "responsibility": "Persist workflow template to knowledge graph for future reuse",
    },
    "persist_results": {
        "category": "db-write",
        "responsibility": "Legacy persistence node (delegates to checkpoints if needed)",
    },

    # API/Embedding nodes - external API calls
    "embed_spec_chunks": {
        "category": "api-call",
        "responsibility": "Generate OpenAI embeddings for spec chunks (for RAG retrieval)",
    },

    # LLM nodes - tracked by LangSmith automatically
    "understand_task": {
        "category": "llm",
        "responsibility": "LLM: Parse task description, extract intent, identify relevant endpoints",
    },
    "plan_integration_flow": {
        "category": "llm",
        "responsibility": "LLM: Design workflow graph with nodes/edges for task implementation",
    },
    "generate_code_and_tests": {
        "category": "llm",
        "responsibility": "LLM: Generate client code, workflow code, and unit tests",
    },
    "build_report": {
        "category": "llm",
        "responsibility": "LLM: Generate executive summary + render full run report",
    },
}


# =============================================================================
# Node Dependency Graph (V2.1 Section 13.2: True Skip per ADR-0009)
# =============================================================================
# Maps each node to the nodes that MUST complete before it can run.
# Used for dependency-aware skip: if A is skipped, all nodes depending on A
# must also be skipped.
# NOTE: Use centralized version from node_names - re-export for backward compat

NODE_DEPENDENCIES: Dict[str, List[str]] = {
    k: list(v) for k, v in _CANONICAL_DEPENDENCIES.items()
}


# get_dependent_nodes is already imported from node_names - kept for backward compat
# def get_dependent_nodes(node_name: str) -> List[str]: imported above


def get_skip_cascade(skipped_node: str) -> List[str]:
    """
    Get the full list of nodes to skip when a given node is skipped.
    
    Includes the original node plus all transitively dependent nodes.
    
    Args:
        skipped_node: The node the user wants to skip
        
    Returns:
        Complete list of nodes to skip, in execution order
    """
    cascade = [skipped_node]
    cascade.extend(get_dependent_nodes(skipped_node))
    
    # Sort by execution order
    ordered_cascade: List[str] = []
    for node in WORKFLOW_NODE_ORDER:
        if node in cascade:
            ordered_cascade.append(node)
    
    return ordered_cascade


def has_skipped_dependency(state, node_name: str) -> bool:
    """
    Check if any dependency of a node was skipped.
    
    Used by nodes to determine if they should auto-skip.
    
    Args:
        state: WorkflowState with skipped_nodes list
        node_name: The node to check dependencies for
        
    Returns:
        True if any dependency was skipped
    """
    deps = set(NODE_DEPENDENCIES.get(node_name, []))
    skipped = set(getattr(state, 'skipped_nodes', []))
    return bool(deps & skipped)


def _get_bounded_timeout(node_name: str) -> Optional[float]:
    """
    Get the timeout for a node if bounded execution is enabled.
    
    Returns None if:
    - Bounded execution is disabled
    - Node is HITL-exempt (uses interrupt(), should pause indefinitely)
    """
    bounds_cfg = get_bounds_config()
    if not bounds_cfg.enabled:
        return None
    # get_timeout_for_node returns None for HITL-exempt nodes
    return bounds_cfg.get_timeout_for_node(node_name)


def _is_hitl_exempt(node_name: str) -> bool:
    """Check if a node is HITL-exempt (no timeout, pauses wall clock)."""
    bounds_cfg = get_bounds_config()
    return bounds_cfg.is_hitl_exempt(node_name)


def _check_run_budget(node_name: str) -> None:
    """
    Check run budget before executing a node.
    
    HITL-exempt nodes skip this check (they can pause indefinitely).
    
    Raises RunBudgetExceededError if budget is exceeded.
    Does nothing if no budget tracker is set.
    """
    tracker = get_budget_tracker()
    if tracker:
        # check_and_increment handles HITL exemption internally
        tracker.check_and_increment(node_name)


def _pause_wall_clock_for_hitl(node_name: str) -> None:
    """Pause wall clock if this is a HITL-exempt node."""
    if _is_hitl_exempt(node_name):
        tracker = get_budget_tracker()
        if tracker:
            tracker.pause_wall_clock()


def _resume_wall_clock_for_hitl(node_name: str) -> None:
    """Resume wall clock after HITL-exempt node completes."""
    if _is_hitl_exempt(node_name):
        tracker = get_budget_tracker()
        if tracker:
            tracker.resume_wall_clock()


def _record_timeout_error(state: WorkflowState, node_name: str, timeout: float) -> None:
    """Record a timeout error in state."""
    run_id = getattr(state, 'run_id', None) or ""
    error_msg = f"[bounded_exec:{node_name}] Node exceeded timeout of {timeout}s"
    if hasattr(state, 'errors') and isinstance(state.errors, list):
        state.errors.append(error_msg)


# Known LangGraph framework args that nodes may or may not accept
_FRAMEWORK_ARGS: frozenset = frozenset({'config', 'runtime'})


def _build_call_adapter(fn: Callable) -> Callable[[tuple, Dict[str, Any]], Dict[str, Any]]:
    """
    Build a call adapter for a node function. Called once at wrap time.
    
    Returns a function that takes (args, kwargs) and returns the kwargs dict
    to pass to fn. Handles LangGraph's positional config/runtime injection.
    
    LangGraph nodes can accept:
      - fn(state)
      - fn(state, config)
      - fn(state, config, runtime)
      - fn(state, **kwargs)
    
    Precedence: explicit kwargs override positional args.
    """
    sig = inspect.signature(fn)
    fn_params = set(sig.parameters.keys()) - {'state'}  # state is always first
    has_var_keyword = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    accepts_config = 'config' in fn_params or has_var_keyword
    accepts_runtime = 'runtime' in fn_params or has_var_keyword
    
    def adapter(args: tuple, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Adapt wrapper args/kwargs to fn's signature.
        
        LangGraph may pass config/runtime positionally:
          fn(state)                    -> args=()
          fn(state, config)            -> args=(config,)
          fn(state, config, runtime)   -> args=(config, runtime)
        
        Or via kwargs:
          fn(state, config=..., runtime=...)
        
        Precedence: kwargs override positional.
        """
        # Validate positional args count (max 2: config, runtime)
        if len(args) > 2:
            raise TypeError(
                f"{fn.__name__}() received {len(args)} extra positional args, "
                f"expected at most 2 (config, runtime)"
            )
        
        result: Dict[str, Any] = {}
        
        # Step 1: Map positional args first (kwargs will override)
        if len(args) >= 1 and accepts_config:
            result['config'] = args[0]
        if len(args) >= 2 and accepts_runtime:
            result['runtime'] = args[1]
        
        # Step 2: Process kwargs (override positional)
        for key, value in kwargs.items():
            if key in fn_params:
                # Named param in signature - always pass
                result[key] = value
            elif key == 'config':
                if accepts_config:
                    result['config'] = value  # Override positional
                # else: drop silently (fn doesn't accept it)
            elif key == 'runtime':
                if accepts_runtime:
                    result['runtime'] = value  # Override positional
                # else: drop silently (fn doesn't accept it)
            elif has_var_keyword:
                # fn has **kwargs, pass through
                result[key] = value
            else:
                # Unexpected kwarg and fn doesn't have **kwargs
                raise TypeError(
                    f"{fn.__name__}() got unexpected keyword argument '{key}'"
                )
        
        return result
    
    return adapter


def _is_tracing_enabled() -> bool:
    """
    Check if LangSmith tracing is enabled per docs.
    
    Per LangSmith docs, LANGSMITH_TRACING must be 'true' for traces to be logged.
    LANGCHAIN_TRACING_V2 alone is NOT sufficient - it's only a compat hint for
    legacy callers, not an alternate activation switch.
    
    This prevents accidental tracing activation in tests/CI where only
    LANGCHAIN_TRACING_V2 might be set, which can cause non-obvious hangs
    from background posting behavior.
    
    Also gates on is_tracing_healthy() to disable if prior payload errors occurred.
    """
    from integration_coworker.utils.trace_sanitizer import is_tracing_healthy
    
    # LANGSMITH_TRACING=true is REQUIRED for tracing activation
    langsmith_enabled = os.getenv("LANGSMITH_TRACING", "").lower() == "true"
    if not langsmith_enabled:
        return False
    
    # Gate on health check to disable if prior errors
    return is_tracing_healthy()


def timed_node(fn: Callable, metadata: Optional[Dict[str, str]] = None, enable_checkpoint: bool = True):
    """
    Decorator that records node execution time and metadata into state.
    
    This provides visibility into non-LLM nodes that may show "0.00s" in 
    LangSmith but actually do meaningful work. The timing is recorded in 
    milliseconds and surfaced in the report.
    
    When tracing is enabled (LANGSMITH_TRACING=true or LANGCHAIN_TRACING_V2=true):
    - Async nodes: decorated with @traceable for rich trace metadata
    - Sync nodes: use ls.trace() context manager (per LangSmith guidance for sync)
    
    Per V2 Implementation Plan Section 3.5, also saves checkpoints after
    successful node execution to enable recovery.
    
    Bug #61 Fix: Nodes now check completed_steps to skip execution during resume.
    If a node is already in state.completed_steps, it returns state unchanged.
    
    Args:
        fn: The node function to wrap
        metadata: Optional override metadata dict with 'category' and 'responsibility'
        enable_checkpoint: Whether to save checkpoints after this node (default True)
        
    Raises:
        ValueError: If fn is an HITL-exempt node (must not be wrapped)
    """
    node_name = fn.__name__
    
    # HITL invariant: exempt nodes must NOT be wrapped with timed_node
    from integration_coworker.graph.bounds import HITL_EXEMPT_NODES
    if node_name in HITL_EXEMPT_NODES:
        raise ValueError(
            f"HITL-exempt node must not be wrapped with timed_node: {node_name}. "
            f"Register directly without wrapper."
        )
    
    # Build call adapter once at wrap time (not per invocation)
    call_adapter = _build_call_adapter(fn)
    
    # Get metadata from catalog or use provided override
    node_meta = metadata or NODE_METADATA.get(node_name, {
        "category": "unknown",
        "responsibility": f"Node: {node_name}",
    })

    # Check if LangSmith tracing is enabled
    tracing_enabled = _is_tracing_enabled()
    
    def _should_skip_node(state: WorkflowState) -> bool:
        """Check if this node should be skipped (already completed during resume)."""
        if hasattr(state, 'completed_steps') and state.completed_steps:
            return node_name in state.completed_steps
        return False
    
    def _emit_node_start(state: WorkflowState) -> str:
        """Emit graph.node.start event and return span_id for correlation."""
        from integration_coworker.graph.memory_sampler import (
            set_current_node as sampler_set_node,
            check_ceiling,
        )
        
        # V22-011: Check if RSS ceiling was exceeded before starting node
        # This is the main-thread safe point for detecting ceiling breach
        ceiling_exc = check_ceiling()
        if ceiling_exc:
            raise ceiling_exc
        
        span_id = _generate_span_id()
        _current_node_name.set(node_name)
        _span_id.set(span_id)
        _current_attempt.set(1)  # TODO: Track retry attempts
        
        # V22-011: Update memory sampler with current node
        sampler_set_node(node_name)
        
        run_id = getattr(state, 'run_id', None)
        
        # V22-001.1: Get comprehensive size metrics at node start
        size_metrics = _get_state_size_metrics(state)
        rss_bytes = _get_rss_bytes()
        
        _log_graph_event(
            "graph.node.start",
            run_id=run_id,
            level=logging.INFO,
            attempt=1,
            node_type=node_meta.get("category", "unknown"),
            input_bytes=size_metrics["estimate_capped"],
            input_bytes_uncapped=size_metrics["estimate_uncapped"],
            input_bytes_method=size_metrics["method"],
            input_keys=_get_non_empty_keys(state),
            rss_bytes=rss_bytes,
        )
        return span_id
    
    def _emit_node_skip(state: WorkflowState) -> None:
        """Emit graph.node.skip event when node already completed."""
        _current_node_name.set(node_name)
        run_id = getattr(state, 'run_id', None)
        _log_graph_event(
            "graph.node.skip",
            run_id=run_id,
            level=logging.INFO,
            reason="already_completed",
        )
    
    def _emit_node_end(state: WorkflowState, result: WorkflowState, duration_ms: float) -> None:
        """Emit graph.node.end event on successful completion."""
        from integration_coworker.graph.memory_sampler import (
            set_current_node as sampler_set_node,
            check_ceiling,
        )
        
        run_id = getattr(state, 'run_id', None)
        
        # V22-001.1: Get comprehensive size metrics
        size_metrics = _get_state_size_metrics(result)
        tracemalloc_stats = _get_tracemalloc_stats()
        rss_bytes = _get_rss_bytes()
        
        _log_graph_event(
            "graph.node.end",
            run_id=run_id,
            level=logging.INFO,
            duration_ms=round(duration_ms, 2),
            # V22-001.1: Log both capped and uncapped for regression detection
            output_bytes=size_metrics["estimate_capped"],
            output_bytes_uncapped=size_metrics["estimate_uncapped"],
            output_bytes_method=size_metrics["method"],
            output_keys=_get_non_empty_keys(result),
            node_type=node_meta.get("category", "unknown"),
            # V22-001.1: RSS and tracemalloc for actual memory tracking
            rss_bytes=rss_bytes,
            tracemalloc_current=tracemalloc_stats["current_bytes"],
            tracemalloc_peak=tracemalloc_stats["peak_bytes"],
        )
        # V22-011: Clear node in memory sampler
        sampler_set_node(None)
        
        # Clear node context
        _current_node_name.set(None)
        _span_id.set(None)
        
        # V22-011: Check if RSS ceiling was exceeded during node execution
        # This catches runaway allocations that happened during the node
        ceiling_exc = check_ceiling()
        if ceiling_exc:
            raise ceiling_exc
    
    def _emit_node_error(state: WorkflowState, error: Exception, duration_ms: float) -> None:
        """Emit graph.node.error event and write failure bundle."""
        run_id = getattr(state, 'run_id', None)
        error_class = classify_error(error)
        
        _log_graph_event(
            "graph.node.error",
            run_id=run_id,
            level=logging.ERROR,
            duration_ms=round(duration_ms, 2),
            error_class=error_class.name,
            error_type=type(error).__name__,
            error_message=str(error)[:500],
            stack_hash=_sha256_prefix(traceback.format_exc()),
        )
        
        # Write failure bundle for post-mortem
        _write_node_failure_bundle(node_name, run_id, error, state)
        
        # Clear node context
        _current_node_name.set(None)
        _span_id.set(None)
    
    def _emit_node_timeout(state: WorkflowState, timeout: float, duration_ms: float) -> None:
        """Emit graph.node.timeout event."""
        run_id = getattr(state, 'run_id', None)
        _log_graph_event(
            "graph.node.timeout",
            run_id=run_id,
            level=logging.ERROR,
            duration_ms=round(duration_ms, 2),
            timeout_s=timeout,
        )
        # Clear node context
        _current_node_name.set(None)
        _span_id.set(None)
    
    def _emit_checkpoint_saved(state: WorkflowState) -> None:
        """Emit graph.node.checkpoint event after checkpoint save."""
        run_id = getattr(state, 'run_id', None)
        _log_graph_event(
            "graph.node.checkpoint",
            run_id=run_id,
            level=logging.DEBUG,
        )
    
    def _save_checkpoint_if_enabled(state: WorkflowState):
        """Save checkpoint after node execution if enabled and run_id exists.
        
        Skips checkpoint saving in dry_run mode to avoid FK constraint violations
        (run_checkpoints references run_status, which isn't created in dry_run).
        
        Bug #101 v22: Added timing logs to capture checkpoint overhead.
        
        CONTRACT: This function expects the MERGED state (post-node execution),
        not the input state. The caller (wrapper functions) must pass the state
        after normalize_node_result() has applied the node's updates. This ensures
        checkpoints capture the actual workflow state after each node completes.
        """
        # Skip in dry_run mode - no run_status record exists
        is_dry_run = getattr(state.options, 'dry_run', False) if state.options else False
        if is_dry_run:
            return
            
        if enable_checkpoint and state.run_id:
            try:
                import time
                checkpoint_start = time.perf_counter()
                from integration_coworker.persistence.checkpoints import save_checkpoint
                save_checkpoint(
                    run_id=state.run_id,
                    node_name=node_name,
                    state=state,
                )
                checkpoint_duration_ms = (time.perf_counter() - checkpoint_start) * 1000
                # Emit checkpoint event with timing
                _log_graph_event(
                    "graph.node.checkpoint_timing",
                    run_id=state.run_id,
                    level=logging.INFO,
                    checkpoint_duration_ms=round(checkpoint_duration_ms, 2),
                    checkpoint_node=node_name,
                )
                _emit_checkpoint_saved(state)
            except Exception as e:
                # Log but don't fail the workflow
                logger.warning(f"Failed to save checkpoint for {node_name}: {e}")

    # Check if the function is async
    is_async = asyncio.iscoroutinefunction(fn)
    
    # Import langsmith once at wrap time (not per invocation)
    ls = None
    traceable_decorator = None
    if tracing_enabled:
        try:
            import langsmith as ls_module
            from langsmith import traceable
            from integration_coworker.utils.trace_sanitizer import sanitize_trace_data
            ls = ls_module
            traceable_decorator = traceable
        except ImportError:
            logger.debug("langsmith not available, tracing disabled")
    
    # P0.2 Fix: Sanitize inputs/outputs before sending to LangSmith
    # This prevents 422 errors from oversized payloads (e.g., 26MB Twilio spec)
    def _sanitize_for_trace(data):
        """Sanitize data before LangSmith trace upload."""
        if data is None:
            return data
        # Import here only if we need it (tracing path)
        from integration_coworker.utils.trace_sanitizer import sanitize_trace_data
        # Convert dataclass or object to dict for sanitization
        if hasattr(data, '__dict__'):
            # For WorkflowState, extract serializable fields
            try:
                # Only include key fields, skip large content
                return sanitize_trace_data({
                    "run_id": getattr(data, 'run_id', None),
                    "task_type": getattr(data, 'task_type', None),
                    "provider_code": getattr(data, 'provider_code', None),
                    "status": getattr(data, 'status', None),
                    "completed_steps": list(getattr(data, 'completed_steps', []) or []),
                    "_sanitized": True,
                }, max_total_bytes=100_000)
            except Exception:
                return {"_sanitization_failed": True}
        elif isinstance(data, dict):
            return sanitize_trace_data(data, max_total_bytes=500_000)
        return data

    # =========================================================================
    # ASYNC WRAPPER (one wrapper, optionally decorated with @traceable)
    # =========================================================================
    if is_async:
        @functools.wraps(fn)
        async def async_wrapper(state: WorkflowState, *args, **kwargs) -> WorkflowState:
            # Adapt args/kwargs to fn's signature using cached adapter
            call_kwargs = call_adapter(args, kwargs)
            
            # Bug #61 Fix: Skip if node already completed during resume
            if _should_skip_node(state):
                logger.debug(f"Skipping {node_name} - already completed during previous run")
                _emit_node_skip(state)
                return state
            
            # P2: Check run budget before execution (HITL nodes exempt)
            _check_run_budget(node_name)
            
            # P2: Get timeout for bounded execution (None for HITL nodes)
            timeout = _get_bounded_timeout(node_name)
            is_hitl = _is_hitl_exempt(node_name)
            
            # P2 HITL fix: Pause wall clock during HITL interrupt
            if is_hitl:
                _pause_wall_clock_for_hitl(node_name)
            
            # Graph lifecycle: emit start event
            _emit_node_start(state)
            
            start = time.perf_counter()
            result = None
            try:
                if timeout is not None:
                    # P2: Execute with timeout (non-HITL nodes only)
                    coro = fn(state, **call_kwargs)
                    result = await asyncio.wait_for(coro, timeout=timeout)
                else:
                    # HITL nodes or disabled bounded exec: no timeout
                    result = await fn(state, **call_kwargs)
                if inspect.isawaitable(result):
                    result = await result
                
                # CONTRACT: Validate node result type
                # Nodes can return dict (partial update), WorkflowState (full), or None
                result = validate_node_result(result, node_name)
            except asyncio.TimeoutError:
                # P2: Record timeout error and re-raise as domain exception
                duration_ms = (time.perf_counter() - start) * 1000
                _emit_node_timeout(state, timeout or 0, duration_ms)
                _record_timeout_error(state, node_name, timeout or 0)
                raise NodeTimeoutError(node_name, timeout or 0, getattr(state, 'run_id', ''))
            except asyncio.CancelledError:
                # Handle cancellation explicitly for proper cleanup (per asyncio docs)
                logger.warning(f"Node {node_name} was cancelled")
                raise
            except Exception as e:
                # Graph lifecycle: emit error event
                duration_ms = (time.perf_counter() - start) * 1000
                _emit_node_error(state, e, duration_ms)
                raise
            finally:
                # P2 HITL fix: Resume wall clock after HITL completes
                # This runs on ALL exit paths: success, exception, timeout, cancellation
                if is_hitl:
                    _resume_wall_clock_for_hitl(node_name)
            
            duration_ms = (time.perf_counter() - start) * 1000

            # CONTRACT: Normalize result into (merged_state, update_dict)
            # - merged_state is used for checkpointing (post-merge state)
            # - update_dict is returned to LangGraph for its internal merge
            merged_state, update_dict = normalize_node_result(state, result, node_name)

            # Record timing into merged_state's timings dict
            if hasattr(merged_state, 'node_timings'):
                merged_state.node_timings[node_name] = duration_ms

            # Log for visibility
            logger.debug(
                f"Node {node_name} completed in {duration_ms:.2f}ms "
                f"[{node_meta.get('category', 'unknown')}]"
            )
            
            # Graph lifecycle: emit end event (uses merged_state for accurate reporting)
            _emit_node_end(state, merged_state, duration_ms)
            
            # Save checkpoint using MERGED state (post-node execution)
            # This ensures checkpoints reflect the actual state after the node ran,
            # not the stale input state (which caused checkpoint drift bugs).
            _save_checkpoint_if_enabled(merged_state)
            
            # V22: Force garbage collection after node completion to release
            # heavyweight temporary objects (LLM responses, parsed specs, etc.)
            # This prevents memory accumulation across nodes.
            gc.collect()

            # Return the update dict to LangGraph for its internal merge
            # If node returned full WorkflowState, update_dict is empty and LangGraph
            # will use the merged_state directly. If node returned dict, LangGraph
            # will merge it (achieving the same result as merged_state).
            return result if isinstance(result, WorkflowState) or result is None else update_dict

        # Optionally decorate with @traceable if tracing enabled and available
        if tracing_enabled and traceable_decorator is not None:
            async_wrapper = traceable_decorator(
                name=node_name,
                run_type="chain",
                metadata={
                    "node_type": node_meta.get("category", "unknown"),
                    "responsibility": node_meta.get("responsibility", ""),
                },
                tags=[
                    f"node_type:{node_meta.get('category', 'unknown')}",
                    "non-llm-node",
                ],
                process_inputs=_sanitize_for_trace,
                process_outputs=_sanitize_for_trace,
            )(async_wrapper)
        
        return async_wrapper

    # =========================================================================
    # SYNC WRAPPER (one wrapper, uses ls.trace context manager when enabled)
    # =========================================================================
    else:
        @functools.wraps(fn)
        def sync_wrapper(state: WorkflowState, *args, **kwargs) -> WorkflowState:
            # Adapt args/kwargs to fn's signature using cached adapter
            call_kwargs = call_adapter(args, kwargs)
            
            # Bug #61 Fix: Skip if node already completed during resume
            if _should_skip_node(state):
                logger.debug(f"Skipping {node_name} - already completed during previous run")
                _emit_node_skip(state)
                return state
            
            # P2: Check run budget before execution
            _check_run_budget(node_name)
            
            # Graph lifecycle: emit start event
            _emit_node_start(state)
            
            start = time.perf_counter()
            result = None
            
            # Use ls.trace context manager for sync tracing (per LangSmith docs)
            # This is the correct pattern since @traceable on sync may not flush reliably
            if tracing_enabled and ls is not None:
                with ls.trace(
                    name=node_name,
                    run_type="chain",
                    inputs={
                        "state_keys": _get_non_empty_keys(state),
                        "state_size": _get_state_size(state),
                    },
                    metadata={
                        "node_type": node_meta.get("category", "unknown"),
                        "responsibility": node_meta.get("responsibility", ""),
                    },
                    tags=[
                        f"node_type:{node_meta.get('category', 'unknown')}",
                        "non-llm-node",
                    ],
                ) as rt:
                    # Bulletproof single rt.end() pattern - capture in locals, end in finally
                    trace_error = None
                    trace_outputs = None
                    try:
                        result = fn(state, **call_kwargs)
                        # Capture outputs for trace end
                        trace_outputs = {
                            "result_keys": _get_non_empty_keys(result),
                            "result_size": _get_state_size(result),
                        }
                    except Exception as e:
                        # Capture error for trace end, emit node error, re-raise
                        trace_error = str(e)
                        duration_ms = (time.perf_counter() - start) * 1000
                        _emit_node_error(state, e, duration_ms)
                        raise
                    finally:
                        # Single rt.end() call - either error or outputs, never both
                        if trace_error is not None:
                            rt.end(error=trace_error)
                        elif trace_outputs is not None:
                            rt.end(outputs=trace_outputs)
            else:
                # No tracing - execute directly
                try:
                    result = fn(state, **call_kwargs)
                except Exception as e:
                    duration_ms = (time.perf_counter() - start) * 1000
                    _emit_node_error(state, e, duration_ms)
                    raise
            
            # CONTRACT: Validate node result type
            # Nodes can return dict (partial update), WorkflowState (full), or None
            result = validate_node_result(result, node_name)
            
            duration_ms = (time.perf_counter() - start) * 1000

            # CONTRACT: Normalize result into (merged_state, update_dict)
            # - merged_state is used for checkpointing (post-merge state)
            # - update_dict is returned to LangGraph for its internal merge
            merged_state, update_dict = normalize_node_result(state, result, node_name)

            # Record timing into merged_state's timings dict
            if hasattr(merged_state, 'node_timings'):
                merged_state.node_timings[node_name] = duration_ms

            # Log for visibility
            logger.debug(
                f"Node {node_name} completed in {duration_ms:.2f}ms "
                f"[{node_meta.get('category', 'unknown')}]"
            )
            
            # Graph lifecycle: emit end event (uses merged_state for accurate reporting)
            _emit_node_end(state, merged_state, duration_ms)
            
            # Save checkpoint using MERGED state (post-node execution)
            # This ensures checkpoints reflect the actual state after the node ran,
            # not the stale input state (which caused checkpoint drift bugs).
            _save_checkpoint_if_enabled(merged_state)
            
            # V22: Force garbage collection after node completion to release
            # heavyweight temporary objects (LLM responses, parsed specs, etc.)
            # This prevents memory accumulation across nodes.
            gc.collect()

            # Return the update dict to LangGraph for its internal merge
            # If node returned full WorkflowState, update_dict is empty and LangGraph
            # will use the merged_state directly. If node returned dict, LangGraph
            # will merge it (achieving the same result as merged_state).
            return result if isinstance(result, WorkflowState) or result is None else update_dict

        return sync_wrapper

def build_graph(checkpointer: Optional[BaseCheckpointSaver] = None):
    """
    Build the LangGraph workflow.
    
    Per design doc Section 5.4, the graph has three checkpoint nodes:
    - persist_silver_checkpoint: After build_silver_api_model + embed_spec_chunks
    - persist_gold_checkpoint: After generate_code_and_tests
    - persist_run_outcome: After build_report (final status and metrics)
    
    Non-LLM nodes are wrapped with timed_node() to record execution time
    in state.node_timings for observability (since LangSmith may show "0.00s").
    
    Args:
        checkpointer: Optional LangGraph checkpointer for native resume support.
                      If provided, enables automatic state persistence after each node.
    """
    workflow = StateGraph(WorkflowState)

    # Add nodes - wrap non-LLM nodes with timed_node for internal timing
    # These nodes may show "0.00s" in LangSmith but do meaningful work
    
    # Slice 1: Spec auto-discovery node (runs before plan_run)
    # When spec_refs is empty and discovery enabled, resolves task to spec URL
    # When spec_refs provided, no-ops and passes through
    workflow.add_node("discover_spec", timed_node(discover_spec.discover_spec))
    
    workflow.add_node("plan_run", timed_node(plan_run.plan_run))
    workflow.add_node("ingest_spec", timed_node(ingest_spec.ingest_spec))
    workflow.add_node("detect_and_parse_spec", timed_node(detect_and_parse_spec.detect_and_parse_spec))
    workflow.add_node("build_silver_api_model", timed_node(build_silver_api_model.build_silver_api_model))
    workflow.add_node("build_silver_file_model", timed_node(build_silver_file_model.build_silver_file_model))  # File Integration V1
    # Bug #101 v22: Wrap ALL nodes with timed_node for graph trace visibility
    # Previously missing: embed_spec_chunks, understand_task, plan_integration_flow, generate_code_and_tests
    # These LLM-heavy nodes were causing 1800+s gaps in traces with no visibility
    workflow.add_node("embed_spec_chunks", timed_node(embed_spec_chunks.embed_spec_chunks))

    # Silver checkpoint - per design doc Section 5.4
    workflow.add_node("persist_silver_checkpoint", timed_node(persist_silver_checkpoint.persist_silver_checkpoint))

    # LLM nodes - NOW wrapped with timed_node for graph trace events (Bug #101 v22)
    workflow.add_node("understand_task", timed_node(understand_task.understand_task))
    workflow.add_node("align_task_with_kg", timed_node(align_task_with_kg.align_task_with_kg))  # May skip LLM
    workflow.add_node("plan_integration_flow", timed_node(plan_integration_flow.plan_integration_flow))
    workflow.add_node("attach_policies_and_patterns", timed_node(attach_policies_and_patterns.attach_policies_and_patterns))
    workflow.add_node("attach_repo_context", timed_node(attach_repo_context.attach_repo_context))
    workflow.add_node("generate_code_and_tests", timed_node(generate_code_and_tests.generate_code_and_tests))

    # PR #8: Static analysis gate - runs after code generation, before sandbox
    # Stores results in quality_refs["static"], no routing changes
    workflow.add_node("static_analysis_gate", timed_node(static_analysis_gate))

    # PR #9: Sandbox attribution gate - runs after static analysis, before review
    # Analyzes sandbox failures for actionability, stores bounded summary
    # Signals-only: no routing changes, no interrupts
    workflow.add_node("sandbox_attribution_gate", timed_node(sandbox_attribution_gate))

    # PR #10: Targeted regeneration node - processes regenerate_targeted decisions
    # Builds constraints artifact and prepares state for targeted code regeneration
    workflow.add_node("targeted_regeneration", timed_node(targeted_regeneration))

    # PR #11: Human edits node - applies patch-based human edits
    # Budget-limited to prevent UI-driven infinite loops
    workflow.add_node("apply_human_edits", timed_node(apply_human_edits))

    # Gold checkpoint - per design doc Section 5.4
    workflow.add_node("persist_gold_checkpoint", timed_node(persist_gold_checkpoint.persist_gold_checkpoint))

    workflow.add_node("analyze_repo_layout", timed_node(analyze_repo_layout.analyze_repo_layout))
    workflow.add_node("code_review_gate", review_gate("code"))  # Parameterized review gate for code approval
    workflow.add_node("sandbox_review_gate", review_gate("sandbox"))  # Parameterized review gate for sandbox results
    workflow.add_node("apply_repo_integration_changes", timed_node(apply_repo_integration_changes.apply_repo_integration_changes))
    workflow.add_node("validate_integration_design", timed_node(validate_integration_design.validate_integration_design))

    # Legacy persist_results kept for backward compatibility (delegates to checkpoints if needed)
    workflow.add_node("persist_results", timed_node(persist_results.persist_results))

    # build_report is async; wrap so it is awaited and we record timings consistently.
    workflow.add_node("build_report", timed_node(build_report.build_report))  # Has LLM call for summary

    # Run outcome checkpoint - per design doc Section 5.4
    workflow.add_node("persist_run_outcome", timed_node(persist_run_outcome.persist_run_outcome))

    workflow.add_node("handle_error", timed_node(handle_error.handle_error))

    # Define edges
    # Slice 1: Entry point is now discover_spec (handles empty spec_refs case)
    # discover_spec no-ops when spec_refs already provided, so existing flow unchanged
    workflow.set_entry_point("discover_spec")
    workflow.add_edge("discover_spec", "plan_run")
    workflow.add_edge("plan_run", "ingest_spec")
    workflow.add_edge("ingest_spec", "detect_and_parse_spec")
    workflow.add_edge("detect_and_parse_spec", "build_silver_api_model")
    workflow.add_edge("build_silver_api_model", "build_silver_file_model")  # File Integration V1: process files after APIs
    workflow.add_edge("build_silver_file_model", "embed_spec_chunks")

    # Silver checkpoint after embedding (per design doc Section 5.4)
    workflow.add_edge("embed_spec_chunks", "persist_silver_checkpoint")
    workflow.add_edge("persist_silver_checkpoint", "understand_task")

    workflow.add_edge("understand_task", "align_task_with_kg")
    workflow.add_edge("align_task_with_kg", "plan_integration_flow")
    workflow.add_edge("plan_integration_flow", "attach_policies_and_patterns")

    # Code generation
    workflow.add_edge("attach_policies_and_patterns", "generate_code_and_tests")

    # ==========================================================================
    # BUG-007 + V39-002: ENHANCED SANDBOX VALIDATION GATE
    # ==========================================================================
    # V39-002 Enhancement: Distinguish between security failures (must block) and
    # quality failures (mypy, style) that should allow code to be written with
    # warnings for human review.
    #
    # Previously all sandbox failures blocked file writes. Now:
    # - Security failures (bandit): Route to handle_error
    # - Quality failures (mypy, ruff style, pytest): Continue with warnings
    # ==========================================================================
    
    def check_for_errors_after_codegen(state: WorkflowState) -> str:
        """
        Check if critical errors occurred during code generation.
        
        BUG-007: This gate ensures sandbox validation failures in production mode
        actually stop the pipeline instead of proceeding to apply broken code.
        
        V39-002: Enhanced to distinguish between:
        - Security failures: Always block (route to handle_error)
        - Quality failures: Allow to proceed with warnings (graceful degradation)
        
        Routes to handle_error ONLY if:
        - Security violations detected (bandit failures)
        - Critical syntax errors that prevent code execution
        
        Allows to proceed (with warnings) if:
        - Type errors (mypy) - often false positives
        - Style issues (ruff format) - cosmetic
        - Test failures (pytest) - code is still usable
        """
        if not state.errors:
            return "no_errors"
        
        # Check sandbox_result for detailed failure analysis
        sandbox_result = getattr(state, 'sandbox_result', None)
        
        if sandbox_result:
            # V39-002: Check if this is a security failure vs quality failure
            # V40-002 FIX: sandbox_result["gates"] is a LIST of gate dicts, not a dict.
            # Convert to a dict keyed by gate name for easy lookup.
            gates_list = sandbox_result.get("gates", [])
            if isinstance(gates_list, list):
                gates = {g.get("name"): g for g in gates_list if isinstance(g, dict) and g.get("name")}
            elif isinstance(gates_list, dict):
                # Handle legacy dict format (shouldn't happen but be defensive)
                gates = gates_list
            else:
                gates = {}
            
            # Security failures (bandit) must block
            bandit_result = gates.get("bandit", {})
            if bandit_result and not bandit_result.get("passed", True):
                logger.warning(
                    f"[V39-002] Security validation failed (bandit), routing to handle_error: "
                    f"{bandit_result.get('error', 'unknown')}"
                )
                return "has_errors"
            
            # Check for explicit security violations in summary
            summary = sandbox_result.get("summary", "").lower()
            if "security" in summary or "bandit" in summary:
                logger.warning(f"[V39-002] Security issue detected in summary: {summary}")
                return "has_errors"
            
            # Quality failures (mypy, ruff, pytest) - allow graceful degradation
            quality_failures = []
            for gate_name in ["mypy", "ruff", "pytest", "ruff_format", "coverage"]:
                gate_result = gates.get(gate_name, {})
                if gate_result and not gate_result.get("passed", True):
                    quality_failures.append(gate_name)
            
            if quality_failures and not bandit_result.get("passed", True) if bandit_result else True:
                # Only quality failures, no security issues
                logger.info(
                    f"[V39-002] Quality gates failed ({', '.join(quality_failures)}) but no security issues. "
                    f"Proceeding with graceful degradation - code will be written with warnings."
                )
                # Mark in state that we're in degraded mode
                if not state.plan:
                    state.plan = {}
                state.plan["sandbox_degraded"] = True
                state.plan["sandbox_warnings"] = quality_failures
                
                # Clear the blocking errors - convert to warnings
                state.errors = [
                    f"[WARNING] {e}" if "Sandbox validation failed" in str(e) else e
                    for e in state.errors
                ]
                return "no_errors"  # Continue with warnings
        
        # Check for security-related errors in error messages
        security_keywords = ["security", "bandit", "vulnerability", "injection", "exec(", "eval("]
        for error in state.errors:
            error_lower = str(error).lower()
            if any(kw in error_lower for kw in security_keywords):
                logger.warning(f"[V39-002] Security error detected: {error}")
                return "has_errors"
        
        # Default: if there are errors but none are security-related, allow graceful degradation
        non_security_errors = [e for e in state.errors if not any(
            kw in str(e).lower() for kw in security_keywords
        )]
        
        if non_security_errors and len(non_security_errors) == len(state.errors):
            logger.info(
                f"[V39-002] Non-security errors detected, proceeding with graceful degradation: "
                f"{non_security_errors[:2]}"
            )
            if not state.plan:
                state.plan = {}
            state.plan["sandbox_degraded"] = True
            return "no_errors"
        
        # If we got here with errors, some might be security-related
        return "has_errors"
    
    def check_sandbox_needs_review(state: WorkflowState) -> str:
        """
        Determine if sandbox results need human review before proceeding.
        
        Routes to sandbox_review_gate if:
        - Sandbox ran and has results (success or quality failures)
        - HITL mode is 'always'
        
        Routes directly to persist_gold_checkpoint if:
        - No sandbox result (sandbox didn't run)
        - HITL mode is 'never' or 'auto'
        """
        # Skip review if HITL disabled
        if state.options and state.options.should_skip_hitl():
            return "skip_review"
        
        # Check if sandbox ran and has results worth reviewing
        sandbox_result = getattr(state, 'sandbox_result', None)
        if not sandbox_result:
            return "skip_review"
        
        # Check if there are quality warnings that user should see
        if state.plan.get("sandbox_degraded"):
            return "needs_review"
        
        # Default: skip review for clean sandbox passes
        return "skip_review"
    
    # PR #8: Static analysis runs after code generation, before error checking
    # This is a straight edge - static analysis doesn't change routing
    workflow.add_edge("generate_code_and_tests", "static_analysis_gate")
    
    # PR #9: Attribution runs after static analysis, before error routing
    # Signals-only: no routing changes, just stores bounded summary
    workflow.add_edge("static_analysis_gate", "sandbox_attribution_gate")
    
    # Conditional routing from attribution gate to sandbox/error handling
    workflow.add_conditional_edges(
        "sandbox_attribution_gate",
        check_for_errors_after_codegen,
        {
            "has_errors": "handle_error",  # Route to error handler for security issues
            "no_errors": "sandbox_review_gate",  # Check sandbox results (may skip if no issues)
        }
    )
    
    # ==========================================================================
    # PR #10 + PR #11: SANDBOX REVIEW ROUTING
    # ==========================================================================
    # After sandbox review, route based on human decision:
    # - "continue" → persist_gold_checkpoint (normal flow)
    # - "regenerate_targeted" → targeted_regeneration → generate_code_and_tests (loop)
    # - "apply_human_edits" → apply_human_edits → static_analysis_gate (PR #11)
    # - "escalated" → persist_gold_checkpoint (budget exhausted, accept as-is)
    # ==========================================================================
    
    def check_sandbox_review_decision(state: WorkflowState) -> str:
        """
        Route based on sandbox review decision.
        
        PR #10: Supports targeted regeneration decisions.
        PR #11: Supports apply_human_edits decisions.
        
        Routes:
        - "continue": Proceed to gold checkpoint
        - "regenerate": Go to targeted_regeneration node  
        - "apply_edits": Go to apply_human_edits node (PR #11)
        - "escalate": Budget exhausted or stuck loop, proceed anyway
        """
        # Check for escalation (budget exhausted - either regeneration or edits)
        if state.plan.get("regeneration_escalated"):
            logger.info("[PR #10] Regeneration escalated, proceeding to gold checkpoint")
            return "continue"
        
        if state.plan.get("human_edit_escalated"):
            logger.info("[PR #11] Human edit budget exhausted, proceeding to gold checkpoint")
            return "continue"
        
        # Check review decision
        decisions = getattr(state, 'review_decisions', None) or {}
        sandbox_decision = decisions.get("sandbox", {})
        
        action = sandbox_decision.get("action", "continue")
        
        if action == "regenerate_targeted":
            logger.info(f"[PR #10] Sandbox review decision: regenerate_targeted")
            return "regenerate"
        
        if action == "apply_human_edits":
            logger.info(f"[PR #11] Sandbox review decision: apply_human_edits")
            return "apply_edits"
        
        # Default: continue to gold checkpoint
        return "continue"
    
    workflow.add_conditional_edges(
        "sandbox_review_gate",
        check_sandbox_review_decision,
        {
            "continue": "persist_gold_checkpoint",  # Normal flow
            "regenerate": "targeted_regeneration",  # PR #10: Targeted regeneration
            "apply_edits": "apply_human_edits",  # PR #11: Human edits
        }
    )
    
    # PR #10: After targeted regeneration, loop back to code generation
    # The targeted_regeneration node sets up constraints that codegen will use
    workflow.add_edge("targeted_regeneration", "generate_code_and_tests")

    # PR #11: After human edits, re-run static analysis and sandbox attribution
    # This ensures edits don't break typing, linting, or security checks
    workflow.add_edge("apply_human_edits", "static_analysis_gate")

    # Gold checkpoint after code generation (per design doc Section 5.4)
    # Note: Only reached if no critical errors from codegen/sandbox

    # KG learning after gold checkpoint - persists workflow templates to KG
    workflow.add_node("persist_kg_learning", timed_node(persist_kg_learning.persist_kg_learning))
    workflow.add_edge("persist_gold_checkpoint", "persist_kg_learning")

    # Conditional routing AFTER KG learning for repo integration
    def should_run_repo_nodes(state: WorkflowState) -> str:
        """Route to repo nodes if plan["use_repo"] is True, else skip to validation."""
        if state.plan.get("use_repo", False):
            return "with_repo"
        return "without_repo"

    workflow.add_conditional_edges(
        "persist_kg_learning",
        should_run_repo_nodes,
        {
            "with_repo": "attach_repo_context",
            "without_repo": "validate_integration_design",
        }
    )

    # Repo flow (when enabled) - happens AFTER gold checkpoint
    workflow.add_edge("attach_repo_context", "analyze_repo_layout")
    workflow.add_edge("analyze_repo_layout", "code_review_gate")  # Code review gate before writes
    workflow.add_edge("code_review_gate", "apply_repo_integration_changes")
    workflow.add_edge("apply_repo_integration_changes", "validate_integration_design")

    # Common path after validation
    def check_for_errors_after_validation(state: WorkflowState) -> str:
        """Check if errors occurred during validation."""
        if state.errors and not state.plan.get("failed", False):
            return "has_errors"
        return "no_errors"

    workflow.add_conditional_edges(
        "validate_integration_design",
        check_for_errors_after_validation,
        {
            "has_errors": "handle_error",
            "no_errors": "build_report",
        }
    )

    # After handle_error, still build report
    workflow.add_edge("handle_error", "build_report")

    # Run outcome checkpoint after build_report (per design doc Section 5.4)
    workflow.add_edge("build_report", "persist_run_outcome")
    workflow.add_edge("persist_run_outcome", END)

    # Compile with optional checkpointer for native resume support
    return workflow.compile(checkpointer=checkpointer)


def build_parallel_graph(checkpointer: Optional[BaseCheckpointSaver] = None):
    """
    Build the LangGraph workflow with parallel execution support (Plan 8).
    
    .. deprecated:: V2 State (December 2025)
        **DEPRECATION NOTICE - V2 STATE**
        
        This function is deprecated. Use build_graph() instead.
        Parallel mode causes checkpoint bloat and is disabled by default.
        
        **Why Deprecated:**
        - Creates 2x+ checkpoint writes due to fan-out/fan-in
        - sync_embed_task adds complexity without sufficient speedup
        - Sequential mode (build_graph) is now the production default
        
        **Migration:**
        - Replace build_parallel_graph() with build_graph()
        - Remove PARALLEL_WORKFLOW=true from environment
        - Demo script already uses sequential mode
        
        This function is kept for backwards compatibility only.
    
    When PARALLEL_WORKFLOW=true, this graph runs embed_spec_chunks and
    understand_task in parallel after build_silver_api_model.
    
    Parallel execution flow:
        build_silver_api_model
               |
         [parallel split]
             /   \\
      embed_spec_chunks   understand_task
             \\   /
        [sync_embed_task]
               |
        persist_silver_checkpoint
               |
          align_task_with_kg
               |
             ...
    
    V2 Parallel Fix:
    Uses TypedDict (WorkflowStateDict) with Annotated reducers to enable
    LangGraph's native parallel merge. Each field has a reducer that tells
    LangGraph how to combine values from parallel branches:
    - `last_non_none`: Takes most recent non-None value (for scalars)
    - `unique_list`: Combines lists without duplicates (for completed_steps)
    - `merge_dicts`: Merges dicts (for plan, node_timings)
    - `operator.add`: Concatenates lists (for embeddings)
    
    Node functions still use WorkflowState dataclass internally - we wrap them
    to convert dict <-> dataclass at boundaries.
    
    Args:
        checkpointer: Optional LangGraph checkpointer for resume support
        
    Returns:
        Compiled LangGraph application with parallel execution
    """
    from integration_coworker.graph.parallel import sync_embed_task
    
    # Use TypedDict state for parallel graph (has Annotated reducers)
    workflow = StateGraph(WorkflowStateDict)
    
    # Wrapper to convert dict state -> dataclass for node execution -> dict result
    # Also filters out 'config' kwarg that LangGraph may pass
    def wrap_node_for_dict(node_fn: Callable) -> Callable:
        """Wrap a dataclass-based node to work with dict state."""
        @functools.wraps(node_fn)
        async def async_wrapper(state_dict: WorkflowStateDict, **kwargs) -> WorkflowStateDict:
            # LangGraph may pass 'config', filter it out for node functions
            # (timed_node wrapper also filters, but direct-wrapped nodes need this)
            kwargs.pop('config', None)
            # Convert dict to dataclass (handle if already a dataclass)
            if isinstance(state_dict, WorkflowState):
                state = state_dict
            else:
                state = dict_to_dataclass(state_dict)
            # Call the node (may be async) - pass remaining kwargs to support wrappers
            result = await node_fn(state, **kwargs)
            # Convert back to dict
            if isinstance(result, dict):
                return result
            # Bug #16 fix: Do NOT exclude large fields during workflow execution
            # The placeholder strings break nodes that expect dict values
            return dataclass_to_dict(result, exclude_large_fields=False)
        
        @functools.wraps(node_fn)
        def sync_wrapper(state_dict: WorkflowStateDict, **kwargs) -> WorkflowStateDict:
            # LangGraph may pass 'config', filter it out for node functions
            kwargs.pop('config', None)
            # Convert dict to dataclass (handle if already a dataclass)
            if isinstance(state_dict, WorkflowState):
                state = state_dict
            else:
                state = dict_to_dataclass(state_dict)
            # Call the node - pass remaining kwargs to support wrappers
            result = node_fn(state, **kwargs)
            # Convert back to dict
            if isinstance(result, dict):
                return result
            # Bug #16 fix: Do NOT exclude large fields during workflow execution
            # The placeholder strings break nodes that expect dict values
            return dataclass_to_dict(result, exclude_large_fields=False)
        
        # Choose wrapper based on whether node is async
        if inspect.iscoroutinefunction(node_fn):
            return async_wrapper
        return sync_wrapper
    
    # Timed wrapper that also handles dict conversion
    def timed_dict_node(node_fn: Callable) -> Callable:
        """Timed wrapper for dict-based state."""
        return wrap_node_for_dict(timed_node(node_fn))

    # Add nodes - wrapped for dict state
    workflow.add_node("plan_run", timed_dict_node(plan_run.plan_run))
    workflow.add_node("ingest_spec", timed_dict_node(ingest_spec.ingest_spec))
    workflow.add_node("detect_and_parse_spec", timed_dict_node(detect_and_parse_spec.detect_and_parse_spec))
    workflow.add_node("build_silver_api_model", timed_dict_node(build_silver_api_model.build_silver_api_model))
    workflow.add_node("build_silver_file_model", timed_dict_node(build_silver_file_model.build_silver_file_model))
    
    # Parallel branch nodes - wrapped for dict state
    workflow.add_node("embed_spec_chunks", wrap_node_for_dict(embed_spec_chunks.embed_spec_chunks))
    workflow.add_node("understand_task", wrap_node_for_dict(understand_task.understand_task))
    
    # Sync node - wrapped for dict state
    workflow.add_node("sync_embed_task", wrap_node_for_dict(timed_node(sync_embed_task)))
    
    # Silver checkpoint after sync
    workflow.add_node("persist_silver_checkpoint", timed_dict_node(persist_silver_checkpoint.persist_silver_checkpoint))

    # Rest of the nodes - wrapped for dict state
    workflow.add_node("align_task_with_kg", timed_dict_node(align_task_with_kg.align_task_with_kg))
    workflow.add_node("plan_integration_flow", wrap_node_for_dict(plan_integration_flow.plan_integration_flow))
    workflow.add_node("attach_policies_and_patterns", timed_dict_node(attach_policies_and_patterns.attach_policies_and_patterns))
    workflow.add_node("attach_repo_context", timed_dict_node(attach_repo_context.attach_repo_context))
    workflow.add_node("generate_code_and_tests", wrap_node_for_dict(generate_code_and_tests.generate_code_and_tests))
    workflow.add_node("persist_gold_checkpoint", timed_dict_node(persist_gold_checkpoint.persist_gold_checkpoint))
    workflow.add_node("analyze_repo_layout", timed_dict_node(analyze_repo_layout.analyze_repo_layout))
    workflow.add_node("code_review_gate", wrap_node_for_dict(review_gate("code")))  # Code review gate - no timed_node (interrupt persists state)
    workflow.add_node("apply_repo_integration_changes", timed_dict_node(apply_repo_integration_changes.apply_repo_integration_changes))
    workflow.add_node("validate_integration_design", timed_dict_node(validate_integration_design.validate_integration_design))
    workflow.add_node("persist_results", timed_dict_node(persist_results.persist_results))
    workflow.add_node("build_report", timed_dict_node(build_report.build_report))
    workflow.add_node("persist_run_outcome", timed_dict_node(persist_run_outcome.persist_run_outcome))
    workflow.add_node("handle_error", timed_dict_node(handle_error.handle_error))
    workflow.add_node("persist_kg_learning", timed_dict_node(persist_kg_learning.persist_kg_learning))

    # Define edges - sequential until build_silver_api_model
    workflow.set_entry_point("plan_run")
    workflow.add_edge("plan_run", "ingest_spec")
    workflow.add_edge("ingest_spec", "detect_and_parse_spec")
    workflow.add_edge("detect_and_parse_spec", "build_silver_api_model")
    workflow.add_edge("build_silver_api_model", "build_silver_file_model")
    
    # PARALLEL BRANCHES: build_silver_file_model fans out to both nodes
    # LangGraph will execute both branches when they have the same source
    workflow.add_edge("build_silver_file_model", "embed_spec_chunks")
    workflow.add_edge("build_silver_file_model", "understand_task")
    
    # Both parallel branches merge at sync_embed_task
    workflow.add_edge("embed_spec_chunks", "sync_embed_task")
    workflow.add_edge("understand_task", "sync_embed_task")
    
    # Continue sequential after sync
    workflow.add_edge("sync_embed_task", "persist_silver_checkpoint")
    workflow.add_edge("persist_silver_checkpoint", "align_task_with_kg")

    workflow.add_edge("align_task_with_kg", "plan_integration_flow")
    workflow.add_edge("plan_integration_flow", "attach_policies_and_patterns")
    workflow.add_edge("attach_policies_and_patterns", "generate_code_and_tests")
    
    # BUG-007 FIX: Sandbox validation gate for parallel graph (deprecated but kept for compat)
    def check_for_errors_after_codegen_parallel(state: WorkflowStateDict) -> str:
        """Check if critical errors occurred during code generation (parallel graph version)."""
        errors = state.get("errors", [])
        if errors:
            sandbox_result = state.get("sandbox_result", None)
            if sandbox_result and not sandbox_result.get("success", True):
                logger.warning(f"[BUG-007] Sandbox validation failed in parallel graph")
                return "has_errors"
            for error in errors:
                if "Sandbox validation failed" in str(error):
                    return "has_errors"
        return "no_errors"
    
    workflow.add_conditional_edges(
        "generate_code_and_tests",
        check_for_errors_after_codegen_parallel,
        {
            "has_errors": "handle_error",
            "no_errors": "persist_gold_checkpoint",
        }
    )
    
    workflow.add_edge("persist_gold_checkpoint", "persist_kg_learning")

    # Conditional routing for repo integration
    def should_run_repo_nodes(state: WorkflowStateDict) -> str:
        plan = state.get("plan", {})
        if plan.get("use_repo", False):
            return "with_repo"
        return "without_repo"

    workflow.add_conditional_edges(
        "persist_kg_learning",
        should_run_repo_nodes,
        {
            "with_repo": "attach_repo_context",
            "without_repo": "validate_integration_design",
        }
    )

    # Repo flow
    workflow.add_edge("attach_repo_context", "analyze_repo_layout")
    workflow.add_edge("analyze_repo_layout", "code_review_gate")  # Code review gate before writes
    workflow.add_edge("code_review_gate", "apply_repo_integration_changes")
    workflow.add_edge("apply_repo_integration_changes", "validate_integration_design")

    # Error handling
    def check_for_errors_after_validation(state: WorkflowStateDict) -> str:
        errors = state.get("errors", [])
        plan = state.get("plan", {})
        if errors and not plan.get("failed", False):
            return "has_errors"
        return "no_errors"

    workflow.add_conditional_edges(
        "validate_integration_design",
        check_for_errors_after_validation,
        {
            "has_errors": "handle_error",
            "no_errors": "build_report",
        }
    )

    workflow.add_edge("handle_error", "build_report")
    workflow.add_edge("build_report", "persist_run_outcome")
    workflow.add_edge("persist_run_outcome", END)

    logger.info("Built parallel workflow graph (PARALLEL_WORKFLOW=true)")
    return workflow.compile(checkpointer=checkpointer)


def run_workflow(
    state: WorkflowState,
    use_checkpointer: bool = True,
    thread_id: Optional[str] = None,
) -> WorkflowState:
    """
    Execute the workflow graph with LangSmith tracing context.
    
    Sets up run context for LLM client tracing, ensuring all LLM calls
    within this run are correlated with the same run_id and provider_code.
    
    V4 Observability: Initializes and captures token usage tracking.
    
    Bug #61 Fix: Uses LangGraph's native checkpointing for resume support.
    When use_checkpointer=True, state is automatically saved after each node,
    enabling resume from interruptions.
    
    Plan 8: When PARALLEL_WORKFLOW=true, uses parallel graph for faster execution.
    
    V3.0: Uses async execution with AsyncPostgresSaver (ASYNC_MIGRATION_PLAN.md).
    
    Args:
        state: Initial workflow state
        use_checkpointer: Enable LangGraph native checkpointing (default True)
        thread_id: Optional thread ID for checkpoint isolation. If not provided,
                   uses state.run_id. Each thread_id has its own checkpoint history.
    """
    return asyncio.run(_run_workflow_async(state, use_checkpointer, thread_id))


async def _run_workflow_async(
    state: WorkflowState,
    use_checkpointer: bool = True,
    thread_id: Optional[str] = None,
    enable_shutdown_handler: bool = True,
) -> WorkflowState:
    """Internal async implementation of run_workflow.
    
    Production Readiness v4 - P0-3/P0-4:
    - Integrates with ShutdownManager for graceful shutdown
    - Properly manages async checkpointer lifecycle
    - Registers cleanup callbacks for signal handling
    
    Args:
        state: Initial workflow state
        use_checkpointer: Enable LangGraph native checkpointing
        thread_id: Optional thread ID for checkpoint isolation
        enable_shutdown_handler: Setup signal handlers for graceful shutdown (default True)
    """
    from integration_coworker.llm.client import init_token_usage, get_token_usage
    from integration_coworker.graph.parallel import is_parallel_enabled
    from integration_coworker.persistence.db import get_engine_type
    from integration_coworker.repo.io import repo_io_context, RepoIOConfig
    from integration_coworker.utils.trace_sanitizer import reset_tracing_state
    from integration_coworker.graph.memory_sampler import (
        start_memory_sampler, stop_memory_sampler, set_current_node,
        RSSCeilingExceeded, is_ceiling_exceeded,
    )
    
    # P0.2 Fix: Reset tracing state at start of each run
    # This allows retrying tracing even if previous run disabled it
    reset_tracing_state()
    
    # P0-4: Setup shutdown handler if enabled
    shutdown_manager = None
    if enable_shutdown_handler:
        shutdown_manager = get_shutdown_manager()
        await shutdown_manager.setup()
    
    # V23-005: Track current run state for atexit handling
    _set_current_run_state(state)
    
    # Set run context for LangSmith tracing
    run_id = state.run_id or state.plan.get("run_id", "")
    provider_code = state.provider_code
    if run_id:
        set_run_context(run_id, provider_code)
    
    # V23-012: Pre-run cache consistency validation
    # This detects and auto-repairs cache/database mismatches that cause slow fallback paths
    try:
        from integration_coworker.persistence.cache_consistency import (
            ensure_cache_consistency, pre_run_cache_check
        )
        if not pre_run_cache_check(provider_code):
            logger.warning(
                "V23-012: Cache consistency check failed - run may be slower due to "
                "cache/database mismatch. Consider running with --fresh mode."
            )
    except ImportError:
        pass  # Module not available, skip check
    except Exception as e:
        logger.debug(f"V23-012: Cache consistency check error (non-fatal): {e}")
    
    # Graph lifecycle: Set up graph trace context for GRAPH_TRACE.jsonl
    # Determine artifacts directory for trace output
    artifacts_dir = None
    if run_id:
        # Use standard artifacts location if available
        output_dir = getattr(state.options, 'output_dir', None) if state.options else None
        if output_dir:
            artifacts_dir = str(Path(output_dir) / "logs")
        else:
            # Fallback to tmp location
            artifacts_dir = f"/tmp/graph_traces/{run_id}"
    
    graph_tokens = set_graph_trace_context(
        run_id=run_id or "unknown",
        artifacts_dir=artifacts_dir,
    )
    
    # Track workflow start time for SLOW_RUN_BUNDLE generation
    workflow_start_time = time.perf_counter()
    
    # Clear any step timings from previous runs
    clear_step_timings()
    
    # Emit workflow start event
    _log_graph_event(
        "graph.workflow.start",
        run_id=run_id,
        level=logging.INFO,
        provider_code=provider_code,
        use_checkpointer=use_checkpointer,
        thread_id=thread_id,
    )
    
    # V4 Observability: Initialize token tracking
    init_token_usage()
    
    # V22-001.1: Initialize tracemalloc for Python allocation tracking
    # This runs in production demo mode to track memory hotspots
    _init_tracemalloc_if_enabled()
    
    # V22-001.1: Log initial memory state
    initial_diag = _get_memory_diagnostics()
    _log_graph_event(
        "graph.workflow.memory_baseline",
        run_id=run_id,
        level=logging.INFO,
        rss_bytes=initial_diag["rss_bytes"],
        rss_mb=initial_diag["rss_mb"],
        tracemalloc_enabled=initial_diag.get("tracemalloc_enabled", False),
        cgroup_limit=initial_diag.get("cgroup_memory_limit", -1),
    )
    
    # V22-011: Start memory sampler for continuous telemetry
    # The sampler runs in a background thread and logs RSS/tracemalloc samples
    # to MEMORY_TRACE.jsonl. If RSS exceeds IC_MAX_RSS_MB, it triggers graceful abort.
    memory_sampler = None
    rss_ceiling_exception: Optional[RSSCeilingExceeded] = None
    
    def _on_rss_ceiling_exceeded(exc: RSSCeilingExceeded) -> None:
        """Callback when RSS ceiling is exceeded - triggers graceful abort."""
        nonlocal rss_ceiling_exception
        rss_ceiling_exception = exc
        _log_graph_event(
            "graph.workflow.rss_ceiling_exceeded",
            run_id=run_id,
            level=logging.ERROR,
            rss_mb=exc.current_rss_mb,
            ceiling_mb=exc.ceiling_mb,
            node_name=exc.node_name,
        )
    
    memory_sampler = start_memory_sampler(
        run_id=run_id or "unknown",
        artifacts_dir=artifacts_dir,
        ceiling_callback=_on_rss_ceiling_exceeded,
    )
    if memory_sampler:
        logger.info(f"V22-011: Memory sampler started for run {run_id}")
    
    # Check if parallel mode is enabled
    parallel_mode = is_parallel_enabled()
    
    # For parallel graph, convert initial state to dict format
    # (parallel graph uses WorkflowStateDict with Annotated reducers)
    if parallel_mode:
        initial_state = dataclass_to_dict(state)
    else:
        initial_state = state

    # P1: Setup repo IO context for policy-enforced file operations
    # Only active if repo_root is specified
    repo_root = state.repo_root
    io_context = None
    
    # G-02: Get workspace boundary from repo_profile (for monorepo support)
    workspace_root = None
    if state.repo_profile and state.repo_profile.workspace_root:
        workspace_root = state.repo_profile.workspace_root
        logger.debug(f"Workspace boundary enforced: {workspace_root}")
    
    # P2: Get bounded execution config
    bounds_cfg = get_bounds_config()
    
    try:
        # Configure thread_id for checkpoint isolation
        # P2: Use get_invoke_config to include recursion_limit and max_concurrency
        effective_thread_id = thread_id or run_id or state.plan.get("run_id", "default")
        config = get_invoke_config(effective_thread_id, bounds_cfg)
        
        # P1: Wrap workflow execution with repo IO context if repo is enabled
        if repo_root:
            # Use default config - can be customized via settings in future
            # G-02: Pass workspace_root for monorepo boundary enforcement
            io_config = RepoIOConfig()
            io_context = repo_io_context(repo_root, run_id or "default", io_config, workspace_root=workspace_root)
            io_context.__enter__()
        
        # P2: Wrap workflow execution with bounded execution context
        # Item B: HARD global timeout using asyncio.timeout() (Python 3.11+)
        # This catches runaway nodes that don't check budget at transitions.
        effective_run_id = run_id or "default"
        
        async def _execute_workflow():
            """Inner workflow execution - extracted for timeout wrapping."""
            with bounded_run_context(bounds_cfg, effective_run_id) as budget_tracker:
                if use_checkpointer:
                    async with async_checkpointer_context() as checkpointer:
                        # P0-3: Register checkpointer cleanup with shutdown manager
                        if shutdown_manager:
                            async def cleanup_checkpointer():
                                """Cleanup callback for graceful shutdown."""
                                logger.debug("Cleaning up checkpointer on shutdown")
                            shutdown_manager.register_cleanup(cleanup_checkpointer)
                        
                        if parallel_mode:
                            app = build_parallel_graph(checkpointer=checkpointer)
                        else:
                            app = build_graph(checkpointer=checkpointer)
                        
                        # P0-4: Check for shutdown before starting workflow
                        if shutdown_manager and shutdown_manager.is_shutdown_requested():
                            logger.warning("Shutdown requested before workflow start, aborting")
                            raise asyncio.CancelledError("Shutdown requested")
                        
                        return await app.ainvoke(initial_state, config=config)
                else:
                    if parallel_mode:
                        app = build_parallel_graph(checkpointer=None)
                    else:
                        app = build_graph(checkpointer=None)
                    
                    # P0-4: Check for shutdown before starting workflow
                    if shutdown_manager and shutdown_manager.is_shutdown_requested():
                        logger.warning("Shutdown requested before workflow start, aborting")
                        raise asyncio.CancelledError("Shutdown requested")
                    
                    return await app.ainvoke(initial_state, config=config)
        
        # Apply global timeout only if configured (None = no timeout)
        # Note: asyncio.timeout(None) is valid ("no limit") per Python docs,
        # but we keep the explicit branch for clarity.
        if bounds_cfg.max_run_wall_seconds is not None:
            try:
                async with asyncio.timeout(bounds_cfg.max_run_wall_seconds):
                    final_state_dict = await _execute_workflow()
            except TimeoutError:
                # Per Python 3.11+ docs: asyncio.timeout() raises TimeoutError (built-in)
                logger.error(
                    f"Global workflow timeout exceeded: {bounds_cfg.max_run_wall_seconds}s "
                    f"(run_id={effective_run_id})"
                )
                raise GlobalTimeoutError(bounds_cfg.max_run_wall_seconds, effective_run_id)
        else:
            # No global timeout configured - run without time limit
            final_state_dict = await _execute_workflow()
        
        # Convert final state back to dataclass if it's a dict
        if isinstance(final_state_dict, dict):
            final_state = dict_to_dataclass(final_state_dict)
        else:
            final_state = final_state_dict
        
        # V4 Observability: Copy aggregated token usage to final state
        final_state.llm_token_usage = get_token_usage()
        
        # V5 Observability: Generate SLOW_RUN_BUNDLE if run exceeded threshold
        workflow_duration_seconds = time.perf_counter() - workflow_start_time
        node_durations = getattr(final_state, 'node_timings', {}) or {}
        try:
            write_slow_run_bundle(
                run_id=run_id or "unknown",
                total_duration_s=workflow_duration_seconds,
                node_durations=node_durations,
                artifacts_dir=artifacts_dir,
            )
        except Exception as bundle_err:
            logger.warning(f"Failed to write SLOW_RUN_BUNDLE: {bundle_err}")
        
        # V22-001.1: Log final memory state and check for OOM risk
        final_diag = _get_memory_diagnostics()
        oom_check = _check_oom_likelihood()
        _log_graph_event(
            "graph.workflow.memory_final",
            run_id=run_id,
            level=logging.WARNING if oom_check.get("at_risk") else logging.INFO,
            rss_bytes=final_diag["rss_bytes"],
            rss_mb=final_diag["rss_mb"],
            tracemalloc_current=final_diag.get("tracemalloc_current_bytes", 0),
            tracemalloc_peak=final_diag.get("tracemalloc_peak_bytes", 0),
            cgroup_limit=final_diag.get("cgroup_memory_limit", -1),
            cgroup_usage_pct=oom_check.get("usage_pct", -1),
            oom_risk=oom_check.get("at_risk", False),
            oom_diagnosis=oom_check.get("diagnosis", "unknown"),
        )
        
        # Graph lifecycle: emit workflow end event
        _log_graph_event(
            "graph.workflow.end",
            run_id=run_id,
            level=logging.INFO,
            status="success",
            completed_nodes=list(getattr(final_state, 'completed_steps', []) or []),
            token_usage=final_state.llm_token_usage,
            duration_seconds=workflow_duration_seconds,
        )
        
        # V22-011: Check if RSS ceiling was exceeded during execution
        if rss_ceiling_exception is not None:
            logger.error(
                f"V22-011: RSS ceiling was exceeded during run {run_id}: "
                f"{rss_ceiling_exception.current_rss_mb:.1f}MB > {rss_ceiling_exception.ceiling_mb:.1f}MB"
            )
            # Re-raise the ceiling exception after successful completion
            # This ensures the caller knows memory was problematic
            raise rss_ceiling_exception
        
        return final_state
    except asyncio.CancelledError:
        # P0-4: Handle cancellation from shutdown signal
        logger.warning(f"Workflow {run_id} cancelled due to shutdown signal")
        
        # V23-005: Persist run outcome on graceful shutdown
        # This ensures status is recorded even when workflow is interrupted
        try:
            # Create a minimal state for persist_run_outcome
            if 'initial_state' in dir() and initial_state is not None:
                shutdown_state = initial_state if not isinstance(initial_state, dict) else dict_to_dataclass(initial_state)
            else:
                shutdown_state = state
            
            # Set error status for shutdown
            if not hasattr(shutdown_state, 'errors') or shutdown_state.errors is None:
                shutdown_state.errors = []
            shutdown_state.errors.append({
                "node": "runtime",
                "error": "Workflow cancelled due to shutdown signal (SIGTERM/SIGINT)",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            
            # Call persist_run_outcome directly to save status
            from integration_coworker.graph.nodes.persist_run_outcome import persist_run_outcome as _persist_outcome
            logger.info(f"V23-005: Persisting run outcome for interrupted run {run_id}")
            _persist_outcome(shutdown_state)
            logger.info(f"V23-005: Successfully persisted outcome for {run_id}")
        except Exception as persist_err:
            logger.error(f"V23-005: Failed to persist outcome on shutdown: {persist_err}")
        
        # Graph lifecycle: emit workflow error event
        _log_graph_event(
            "graph.workflow.error",
            run_id=run_id,
            level=logging.ERROR,
            error_type="CancelledError",
            error_message="Workflow cancelled due to shutdown signal",
        )
        raise
    except RSSCeilingExceeded:
        # V22-011: Re-raise ceiling exceptions without logging as generic error
        raise
    except Exception as e:
        # Graph lifecycle: emit workflow error event for unexpected errors
        _log_graph_event(
            "graph.workflow.error",
            run_id=run_id,
            level=logging.ERROR,
            error_type=type(e).__name__,
            error_message=str(e)[:500],
            error_class=classify_error(e).name,
        )
        raise
    finally:
        # V22-011: Stop memory sampler and log summary
        if memory_sampler:
            # Get summary before stopping (includes per-node stats)
            summary = memory_sampler.get_summary() if hasattr(memory_sampler, 'get_summary') else None
            samples = stop_memory_sampler()
            
            if summary:
                _log_graph_event(
                    "graph.workflow.memory_sampler_summary",
                    run_id=run_id,
                    level=logging.INFO,
                    total_samples=summary.get("total_samples", len(samples) if samples else 0),
                    peak_rss_mb=summary.get("peak_rss_mb", 0),
                    peak_rss_node=summary.get("peak_rss_node"),
                    ceiling_exceeded=summary.get("ceiling_exceeded", False),
                    node_stats=summary.get("node_stats", {}),
                )
            elif samples:
                # Fallback for older sampler
                peak_rss = memory_sampler.get_peak_rss_mb() if memory_sampler else 0
                _log_graph_event(
                    "graph.workflow.memory_sampler_summary",
                    run_id=run_id,
                    level=logging.INFO,
                    sample_count=len(samples),
                    peak_rss_mb=peak_rss,
                    ceiling_exceeded=rss_ceiling_exception is not None,
                )
        
        # P1: Close repo IO context and log audit summary
        if io_context:
            io_context.__exit__(None, None, None)
        # Clean up run context
        clear_run_context()
        # Graph lifecycle: clean up trace context
        clear_graph_trace_context(graph_tokens)
        # V23-005: Clear current run state now that workflow is complete
        _set_current_run_state(None)


# =============================================================================
# Recovery Support Functions (V2 Implementation Plan Section 3.5)
# =============================================================================

# Use centralized node names - re-export for backward compatibility
# NOTE: Import from integration_coworker.graph.node_names for new code
WORKFLOW_NODE_ORDER: List[str] = list(_CANONICAL_NODE_ORDER)


# get_node_names is already imported from node_names - kept for backward compat
# def get_node_names() -> List[str]: imported above


def run_from_node(
    state: WorkflowState,
    start_node: str,
    thread_id: Optional[str] = None,
) -> WorkflowState:
    """
    Resume workflow execution using LangGraph's native checkpointing.
    
    Bug #61 Fix: Uses LangGraph's checkpointer to resume from the last
    successful checkpoint, not from a specific node.
    
    V3.0: Uses async execution with AsyncPostgresSaver (ASYNC_MIGRATION_PLAN.md).
    
    Args:
        state: The workflow state (used for config if no checkpoint exists)
        start_node: Deprecated - resume point is determined by checkpointer
        thread_id: Thread ID for checkpoint lookup (defaults to state.run_id)
        
    Returns:
        Final workflow state
        
    Raises:
        ValueError: If start_node is not a valid node name (for API compat)
    """
    if start_node not in WORKFLOW_NODE_ORDER:
        raise ValueError(f"Unknown node: {start_node}")
    
    return asyncio.run(_run_from_node_async(state, start_node, thread_id))


async def _run_from_node_async(
    state: WorkflowState,
    start_node: str,
    thread_id: Optional[str] = None,
) -> WorkflowState:
    """Internal async implementation of run_from_node."""
    from integration_coworker.llm.client import init_token_usage, get_token_usage
    from integration_coworker.persistence.db import get_engine_type
    
    # V22-MEM: Reset GC stats at start of new run for clean observability
    try:
        from integration_coworker.graph.state_gc import reset_gc_stats
        reset_gc_stats()
    except ImportError:
        pass
    
    # Set run context for LangSmith tracing
    run_id = state.run_id or state.plan.get("run_id", "")
    provider_code = state.provider_code
    
    if run_id:
        set_run_context(run_id, provider_code)
    
    try:
        # Initialize token tracking
        init_token_usage()
        
        # Use thread_id for checkpoint isolation
        effective_thread_id = thread_id or run_id or "default"
        config = {"configurable": {"thread_id": effective_thread_id}}
        
        # Use unified async checkpointer context so LangGraph gets a saver instance
        async with async_checkpointer_context() as checkpointer:
            app = build_graph(checkpointer=checkpointer)
            
            try:
                existing_state = await app.aget_state(config)
                if existing_state and existing_state.values:
                    logger.info(f"Resuming from checkpoint for thread {effective_thread_id}")
                    final_state_dict = await app.ainvoke(None, config=config)
                else:
                    logger.info(f"No checkpoint found for thread {effective_thread_id}, starting fresh")
                    final_state_dict = await app.ainvoke(state, config=config)
            except Exception as e:
                logger.warning(f"Could not load checkpoint, starting fresh: {e}")
                final_state_dict = await app.ainvoke(state, config=config)
        
        final_state = WorkflowState(**final_state_dict)
        final_state.llm_token_usage = get_token_usage()
        
        return final_state
    finally:
        clear_run_context()


# =============================================================================
# HITL Resume Support (Agent Harness Alignment Plan)
# =============================================================================

def resume_with_approval(
    thread_id: str,
    approval_decision: Union[bool, Dict[str, Any]],
) -> WorkflowState:
    """
    Resume a workflow paused at HITL gate with an approval decision.
    
    This is the synchronous entry point for resuming from an interrupt.
    Uses LangGraph's Command(resume=...) to pass the approval decision
    to the waiting interrupt() call.
    
    Args:
        thread_id: The thread_id (= run_id) of the paused workflow
        approval_decision: The approval decision - either:
            - True/False: Simple approve/reject
            - {"approved": bool, "comment": str, "overrides": {...}}: Full decision
            
    Returns:
        Final WorkflowState after resume completes
        
    Raises:
        ValueError: If no paused checkpoint exists for thread_id
        
    Example:
        # Approve with comment
        state = resume_with_approval(
            "run-123",
            {"approved": True, "comment": "LGTM"}
        )
        
        # Reject
        state = resume_with_approval("run-123", False)
        
        # Approve with file exclusions
        state = resume_with_approval(
            "run-123",
            {"approved": True, "overrides": {"exclude_files": ["src/temp.py"]}}
        )
    """
    return asyncio.run(_resume_with_approval_async(thread_id, approval_decision))


async def _resume_with_approval_async(
    thread_id: str,
    approval_decision: Union[bool, Dict[str, Any]],
) -> WorkflowState:
    """Internal async implementation of resume_with_approval."""
    from integration_coworker.llm.client import init_token_usage, get_token_usage
    
    logger.info(f"Resuming workflow for thread {thread_id} with approval: {approval_decision}")
    
    # Initialize token tracking
    init_token_usage()
    
    # Use canonical thread config (thread_id == run_id contract)
    config = get_thread_config(thread_id)
    
    try:
        async with async_checkpointer_context() as checkpointer:
            app = build_graph(checkpointer=checkpointer)
            
            # Verify checkpoint exists and is at an interrupt
            existing_state = await app.aget_state(config)
            if not existing_state or not existing_state.values:
                raise ValueError(f"No checkpoint found for thread_id: {thread_id}")
            
            # Check if workflow is at an interrupt
            if not existing_state.next:
                raise ValueError(
                    f"Workflow for thread {thread_id} is not paused at an interrupt. "
                    f"Completed steps: {existing_state.values.get('completed_steps', [])}"
                )
            
            logger.info(
                f"Resuming from interrupt. Next nodes: {existing_state.next}, "
                f"Completed: {len(existing_state.values.get('completed_steps', []))} steps"
            )
            
            # Resume with the approval decision using Command
            final_state_dict = await app.ainvoke(
                Command(resume=approval_decision),
                config=config,
            )
            
            # Convert final state back to dataclass
            if isinstance(final_state_dict, dict):
                final_state = dict_to_dataclass(final_state_dict)
            else:
                final_state = final_state_dict
            
            # Copy token usage
            final_state.llm_token_usage = get_token_usage()
            
            return final_state
            
    except Exception as e:
        logger.error(f"Failed to resume workflow: {e}")
        raise


async def get_interrupt_payload(thread_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the interrupt payload for a paused workflow.
    
    Use this to retrieve the HITL approval request details before calling
    resume_with_approval().
    
    Args:
        thread_id: The thread_id (= run_id) of the workflow
        
    Returns:
        The interrupt payload if workflow is paused at HITL gate, None otherwise
        
    Example:
        payload = await get_interrupt_payload("run-123")
        if payload:
            print(f"Pending approval for {payload['summary']['files']} files")
            print(f"Changes: +{payload['summary']['adds']} ~{payload['summary']['mods']} -{payload['summary']['dels']}")
    """
    # Use canonical thread config (thread_id == run_id contract)
    config = get_thread_config(thread_id)
    
    try:
        async with async_checkpointer_context() as checkpointer:
            app = build_graph(checkpointer=checkpointer)
            
            state = await app.aget_state(config)
            if not state or not state.values:
                return None
            
            # Check if at an interrupt
            if not state.next:
                return None
            
            # The interrupt payload is stored in state.tasks[0].interrupts
            # Access via the checkpoint metadata
            if hasattr(state, 'tasks') and state.tasks:
                for task in state.tasks:
                    if hasattr(task, 'interrupts') and task.interrupts:
                        # Return the first interrupt payload
                        return task.interrupts[0].value if task.interrupts[0] else None
            
            return None
            
    except Exception as e:
        logger.warning(f"Could not get interrupt payload: {e}")
        return None


def is_workflow_paused(thread_id: str) -> bool:
    """
    Check if a workflow is paused at an interrupt (HITL gate).
    
    Args:
        thread_id: The thread_id (= run_id) of the workflow
        
    Returns:
        True if workflow is paused and waiting for approval
    """
    return asyncio.run(_is_workflow_paused_async(thread_id))


async def _is_workflow_paused_async(thread_id: str) -> bool:
    """Internal async implementation of is_workflow_paused."""
    # Use canonical thread config (thread_id == run_id contract)
    config = get_thread_config(thread_id)
    
    try:
        async with async_checkpointer_context() as checkpointer:
            app = build_graph(checkpointer=checkpointer)
            
            state = await app.aget_state(config)
            if not state or not state.values:
                return False
            
            # Workflow is paused if it has a 'next' node to execute
            return bool(state.next)
            
    except Exception as e:
        logger.warning(f"Could not check workflow status: {e}")
        return False
