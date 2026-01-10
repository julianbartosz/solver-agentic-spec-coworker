"""
Node Subspan Tracing Infrastructure (Step 2)

Provides fine-grained, per-step lifecycle logging inside graph nodes.
This extends the graph-level logging (graph.node.start/end) with
sub-step visibility for debugging slow tail nodes.

Log Schema v1.0 - Node Step Events
==================================

REQUIRED FIELDS (always present):
  ts            - ISO8601 UTC timestamp
  level         - Log level (DEBUG, INFO, WARNING, ERROR)
  event         - Event name from taxonomy below
  run_id        - Unique run identifier
  trace_id      - W3C-compatible 32 hex trace ID
  span_id       - W3C-compatible 16 hex span ID (unique per step)
  parent_span_id - Parent span ID (node's span_id)
  node_name     - Node name (e.g., "build_report")
  step          - Step name (e.g., "collect_inputs", "llm.call")
  attempt       - Attempt number (1-based)
  pid           - Process ID

RECOMMENDED FIELDS (when applicable):
  duration_ms   - Execution time in milliseconds
  bytes_in      - Input size in bytes
  bytes_out     - Output size in bytes
  sha256_prefix - Content hash prefix (16 chars)
  error_type    - Exception class name
  error_message - Truncated error message

EVENT TAXONOMY:
  node.step.start     - Step execution starting
  node.step.end       - Step completed successfully
  node.step.error     - Step failed

  llm.call.start      - LLM API call starting
  llm.call.end        - LLM API call completed
  llm.call.error      - LLM API call failed

  db.tx.begin         - Database transaction started
  db.tx.commit        - Database transaction committed
  db.tx.rollback      - Database transaction rolled back
  db.op               - Database operation (insert/update/select)

  fs.write.start      - File write starting
  fs.write.end        - File write completed
  fs.write.error      - File write failed

  report.section      - Report section rendered

Usage:
    from integration_coworker.graph.node_trace import (
        node_step, step_context, log_step_event, get_node_trace_context
    )

    # Context manager for automatic start/end logging
    async with step_context("build_report", "collect_inputs", run_id=state.run_id):
        # ... do work ...
        pass

    # Or use decorator
    @node_step("persist_run_outcome", "db_write")
    def write_to_database(data): ...

    # Manual events for custom steps
    log_step_event("llm.call.start", node="build_report", step="summarize",
                   model="gpt-4", prompt_bytes=1234)
"""
import contextvars
import functools
import hashlib
import json
import logging
import os
import sys
import time
import traceback
import uuid
from contextlib import contextmanager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

# Type var for decorated functions
F = TypeVar('F', bound=Callable[..., Any])


# =============================================================================
# Context Variables (Shared naming convention with runtime.py)
# =============================================================================
# Note: We duplicate these contextvars here rather than importing from runtime
# to avoid circular imports. The runtime module also defines these, and both
# modules should coordinate their usage via public API functions.

import uuid
import hashlib


def _generate_span_id() -> str:
    """Generate a W3C-compatible 16-character hex span ID."""
    return uuid.uuid4().hex[:16]


def _generate_trace_id() -> str:
    """Generate a W3C-compatible 32-character hex trace ID."""
    return uuid.uuid4().hex


def _sha256_prefix(content: str | bytes, length: int = 16) -> str:
    """Compute SHA256 prefix of content."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:length]


# Context vars - these are accessed by runtime.py via getter functions
# to avoid circular imports
_graph_run_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("graph_run_id", default=None)
_current_node_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_node_name", default=None)
_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
_node_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("span_id", default=None)
_artifacts_dir: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("artifacts_dir", default=None)

# Step-level context (nested within node)
_current_step: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_step", default=None)
_step_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("step_span_id", default=None)
_step_attempt: contextvars.ContextVar[int] = contextvars.ContextVar("step_attempt", default=1)

# Log level name mapping
_LEVEL_NAMES = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


# =============================================================================
# Step Timing Accumulator (for SLOW_RUN_BUNDLE)
# =============================================================================

@dataclass
class StepTiming:
    """Timing record for a single step execution."""
    node_name: str
    step: str
    event: str
    duration_ms: float
    ts: str
    extra: Dict[str, Any] = field(default_factory=dict)


# Per-run accumulator for step timings (for slow bundle generation)
_step_timings: contextvars.ContextVar[List[StepTiming]] = contextvars.ContextVar(
    "step_timings", default=None
)


def _get_step_timings() -> List[StepTiming]:
    """Get or create step timings accumulator."""
    timings = _step_timings.get()
    if timings is None:
        timings = []
        _step_timings.set(timings)
    return timings


def _record_step_timing(
    node_name: str,
    step: str,
    event: str,
    duration_ms: float,
    **extra: Any,
) -> None:
    """Record a step timing for potential slow bundle generation."""
    timings = _get_step_timings()
    timings.append(StepTiming(
        node_name=node_name,
        step=step,
        event=event,
        duration_ms=duration_ms,
        ts=datetime.now(timezone.utc).isoformat(),
        extra=extra,
    ))


def get_slowest_steps(n: int = 20) -> List[StepTiming]:
    """Get the N slowest steps from the current run."""
    timings = _step_timings.get() or []
    return sorted(timings, key=lambda t: t.duration_ms, reverse=True)[:n]


def clear_step_timings() -> None:
    """Clear accumulated step timings (call at run end)."""
    _step_timings.set(None)


# =============================================================================
# Node Trace File Management
# =============================================================================

def _get_node_trace_path(node_name: str) -> Optional[Path]:
    """
    Get path to node-specific trace file.
    
    Returns: artifacts/<run_id>/nodes/<node_name>.jsonl
    """
    artifacts_dir = _artifacts_dir.get()
    if not artifacts_dir:
        return None
    
    nodes_dir = Path(artifacts_dir) / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)
    return nodes_dir / f"{node_name}.jsonl"


def _write_node_trace_line(node_name: str, json_line: str) -> None:
    """
    Append a JSON-line to the node-specific trace file.
    
    Thread-safe via atomic append mode.
    """
    trace_path = _get_node_trace_path(node_name)
    if not trace_path:
        return
    
    try:
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write(json_line + "\n")
    except Exception as e:
        logger.warning(f"Failed to write node trace for {node_name}: {e}")


def _write_graph_trace_line(json_line: str) -> None:
    """
    Append to main GRAPH_TRACE.jsonl (for step events too).
    """
    artifacts_dir = _artifacts_dir.get()
    if not artifacts_dir:
        return
    
    try:
        trace_file = Path(artifacts_dir) / "GRAPH_TRACE.jsonl"
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        with open(trace_file, "a", encoding="utf-8") as f:
            f.write(json_line + "\n")
    except Exception as e:
        logger.warning(f"Failed to write graph trace: {e}")


# =============================================================================
# Core Logging Function
# =============================================================================

def log_step_event(
    event: str,
    node_name: Optional[str] = None,
    step: Optional[str] = None,
    run_id: Optional[str] = None,
    level: int = logging.INFO,
    duration_ms: Optional[float] = None,
    **fields: Any,
) -> None:
    """
    Emit a structured step event in Log Schema v1.0 format.
    
    All events are written to:
    - Standard logger (console/file)
    - GRAPH_TRACE.jsonl (main trace)
    - nodes/<node_name>.jsonl (node-specific trace)
    
    Args:
        event: Event name from taxonomy (e.g., "node.step.start")
        node_name: Override node name (defaults to context)
        step: Override step name (defaults to context)
        run_id: Override run ID (defaults to context)
        level: Log level (default INFO)
        duration_ms: Execution duration for end events
        **fields: Additional structured fields
    """
    # Resolve context
    effective_node = node_name or _current_node_name.get()
    effective_step = step or _current_step.get()
    effective_run_id = run_id or _graph_run_id.get()
    
    # Build payload with REQUIRED fields
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": _LEVEL_NAMES.get(level, "INFO"),
        "event": event,
        "run_id": effective_run_id,
        "trace_id": _trace_id.get(),
        "span_id": _step_span_id.get() or _generate_span_id(),
        "parent_span_id": _node_span_id.get(),
        "node_name": effective_node,
        "step": effective_step,
        "attempt": _step_attempt.get(),
        "pid": os.getpid(),
    }
    
    # Add duration if provided
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 2)
        # Record for slow bundle
        if effective_node and effective_step:
            _record_step_timing(
                effective_node, effective_step, event, duration_ms, **fields
            )
    
    # Add optional fields
    payload.update(fields)
    
    # Emit as JSON-line
    json_line = json.dumps(payload, default=str, separators=(",", ":"))
    
    # Log to standard logger
    logger.log(level, f"[STEP] {json_line}")
    
    # Write to trace files
    _write_graph_trace_line(json_line)
    if effective_node:
        _write_node_trace_line(effective_node, json_line)


# =============================================================================
# Context Managers for Step Tracing
# =============================================================================

@contextmanager
def step_context(
    node_name: str,
    step: str,
    run_id: Optional[str] = None,
    **start_fields: Any,
):
    """
    Context manager for synchronous step tracing.
    
    Automatically emits node.step.start and node.step.end/error events.
    
    Usage:
        with step_context("build_report", "collect_inputs", run_id=state.run_id):
            # ... do work ...
            pass
    """
    # Set context
    step_token = _current_step.set(step)
    span_token = _step_span_id.set(_generate_span_id())
    
    # Use provided node_name, but don't override global context
    # (that's set by timed_node wrapper)
    
    # Emit start event
    log_step_event(
        "node.step.start",
        node_name=node_name,
        step=step,
        run_id=run_id,
        **start_fields,
    )
    
    start_time = time.perf_counter()
    try:
        yield
        # Success - emit end event
        duration_ms = (time.perf_counter() - start_time) * 1000
        log_step_event(
            "node.step.end",
            node_name=node_name,
            step=step,
            run_id=run_id,
            duration_ms=duration_ms,
        )
    except Exception as e:
        # Error - emit error event
        duration_ms = (time.perf_counter() - start_time) * 1000
        log_step_event(
            "node.step.error",
            node_name=node_name,
            step=step,
            run_id=run_id,
            duration_ms=duration_ms,
            level=logging.ERROR,
            error_type=type(e).__name__,
            error_message=str(e)[:500],
            stack_hash=_sha256_prefix(traceback.format_exc()),
        )
        raise
    finally:
        # Reset context
        _current_step.reset(step_token)
        _step_span_id.reset(span_token)


@asynccontextmanager
async def async_step_context(
    node_name: str,
    step: str,
    run_id: Optional[str] = None,
    **start_fields: Any,
):
    """
    Async context manager for step tracing.
    
    Usage:
        async with async_step_context("build_report", "llm_call", run_id=state.run_id):
            result = await call_llm(...)
    """
    # Set context
    step_token = _current_step.set(step)
    span_token = _step_span_id.set(_generate_span_id())
    
    # Emit start event
    log_step_event(
        "node.step.start",
        node_name=node_name,
        step=step,
        run_id=run_id,
        **start_fields,
    )
    
    start_time = time.perf_counter()
    try:
        yield
        # Success - emit end event
        duration_ms = (time.perf_counter() - start_time) * 1000
        log_step_event(
            "node.step.end",
            node_name=node_name,
            step=step,
            run_id=run_id,
            duration_ms=duration_ms,
        )
    except Exception as e:
        # Error - emit error event
        duration_ms = (time.perf_counter() - start_time) * 1000
        log_step_event(
            "node.step.error",
            node_name=node_name,
            step=step,
            run_id=run_id,
            duration_ms=duration_ms,
            level=logging.ERROR,
            error_type=type(e).__name__,
            error_message=str(e)[:500],
            stack_hash=_sha256_prefix(traceback.format_exc()),
        )
        raise
    finally:
        # Reset context
        _current_step.reset(step_token)
        _step_span_id.reset(span_token)


def node_step(node_name: str, step: str):
    """
    Decorator for step tracing.
    
    Usage:
        @node_step("persist_run_outcome", "insert_run_status")
        def insert_run_status(cur, data): ...
    """
    def decorator(func: F) -> F:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                async with async_step_context(node_name, step):
                    return await func(*args, **kwargs)
            return async_wrapper  # type: ignore
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                with step_context(node_name, step):
                    return func(*args, **kwargs)
            return sync_wrapper  # type: ignore
    return decorator


# =============================================================================
# LLM Call Tracing
# =============================================================================

@asynccontextmanager
async def llm_call_context(
    node_name: str,
    step: str = "llm_call",
    model: Optional[str] = None,
    provider: Optional[str] = None,
    run_id: Optional[str] = None,
):
    """
    Context manager for LLM call tracing.
    
    Tracks prompt/response sizes, token counts (if available), retry info.
    
    Usage:
        async with llm_call_context("build_report", model="gpt-4") as ctx:
            ctx.set_prompt_bytes(len(prompt))
            response = await call_llm(prompt)
            ctx.set_response_bytes(len(response))
            ctx.set_tokens(usage.prompt_tokens, usage.completion_tokens)
    """
    class LLMCallContext:
        def __init__(self):
            self.prompt_bytes = 0
            self.response_bytes = 0
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.retry_count = 0
            self.backoff_ms = 0
        
        def set_prompt_bytes(self, n: int):
            self.prompt_bytes = n
        
        def set_response_bytes(self, n: int):
            self.response_bytes = n
        
        def set_tokens(self, prompt: int, completion: int):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
        
        def set_retry(self, count: int, backoff_ms: float):
            self.retry_count = count
            self.backoff_ms = backoff_ms
    
    ctx = LLMCallContext()
    step_token = _current_step.set(step)
    span_token = _step_span_id.set(_generate_span_id())
    
    # Emit start event
    log_step_event(
        "llm.call.start",
        node_name=node_name,
        step=step,
        run_id=run_id,
        model=model,
        provider=provider,
    )
    
    start_time = time.perf_counter()
    try:
        yield ctx
        # Success
        duration_ms = (time.perf_counter() - start_time) * 1000
        log_step_event(
            "llm.call.end",
            node_name=node_name,
            step=step,
            run_id=run_id,
            duration_ms=duration_ms,
            model=model,
            provider=provider,
            prompt_bytes=ctx.prompt_bytes,
            response_bytes=ctx.response_bytes,
            prompt_tokens=ctx.prompt_tokens,
            completion_tokens=ctx.completion_tokens,
            retry_count=ctx.retry_count,
            backoff_ms=ctx.backoff_ms,
        )
    except Exception as e:
        # Error
        duration_ms = (time.perf_counter() - start_time) * 1000
        log_step_event(
            "llm.call.error",
            node_name=node_name,
            step=step,
            run_id=run_id,
            duration_ms=duration_ms,
            level=logging.ERROR,
            model=model,
            provider=provider,
            error_type=type(e).__name__,
            error_message=str(e)[:500],
        )
        raise
    finally:
        _current_step.reset(step_token)
        _step_span_id.reset(span_token)


# =============================================================================
# Database Operation Tracing
# =============================================================================

@contextmanager
def db_transaction_context(
    node_name: str,
    run_id: Optional[str] = None,
):
    """
    Context manager for database transaction tracing.
    
    Usage:
        with db_transaction_context("persist_run_outcome", run_id=state.run_id) as tx:
            cur.execute(...)
            tx.record_op("insert", "run_status", rows=1)
            cur.execute(...)
            tx.record_op("insert", "rag_metrics", rows=5)
    """
    class TransactionContext:
        def __init__(self):
            self.ops: List[Dict[str, Any]] = []
            self.start_time = time.perf_counter()
        
        def record_op(
            self,
            op_name: str,
            table: str,
            rows: int = 0,
            duration_ms: Optional[float] = None,
        ):
            """Record a database operation within the transaction."""
            op_duration = duration_ms or 0
            self.ops.append({
                "op_name": op_name,
                "table": table,
                "rows": rows,
                "duration_ms": op_duration,
            })
            log_step_event(
                "db.op",
                node_name=node_name,
                step=f"db.{op_name}.{table}",
                run_id=run_id,
                duration_ms=op_duration,
                op_name=op_name,
                table=table,
                rows=rows,
            )
    
    tx = TransactionContext()
    
    # Emit begin event
    log_step_event(
        "db.tx.begin",
        node_name=node_name,
        step="db.tx",
        run_id=run_id,
    )
    
    try:
        yield tx
        # Commit
        duration_ms = (time.perf_counter() - tx.start_time) * 1000
        log_step_event(
            "db.tx.commit",
            node_name=node_name,
            step="db.tx",
            run_id=run_id,
            duration_ms=duration_ms,
            op_count=len(tx.ops),
            total_rows=sum(op.get("rows", 0) for op in tx.ops),
        )
    except Exception as e:
        # Rollback
        duration_ms = (time.perf_counter() - tx.start_time) * 1000
        log_step_event(
            "db.tx.rollback",
            node_name=node_name,
            step="db.tx",
            run_id=run_id,
            duration_ms=duration_ms,
            level=logging.ERROR,
            error_type=type(e).__name__,
            error_message=str(e)[:500],
        )
        raise


# =============================================================================
# Filesystem Write Tracing
# =============================================================================

def log_fs_write(
    node_name: str,
    path: str,
    bytes_written: int,
    run_id: Optional[str] = None,
    content_preview: Optional[str] = None,
    sha256_prefix: Optional[str] = None,
    duration_ms: Optional[float] = None,
):
    """
    Log a filesystem write event.
    
    Usage:
        start = time.perf_counter()
        with open(path, 'w') as f:
            f.write(content)
        log_fs_write("build_report", path, len(content),
                     sha256_prefix=hashlib.sha256(content.encode()).hexdigest()[:16],
                     duration_ms=(time.perf_counter() - start) * 1000)
    """
    log_step_event(
        "fs.write.end",
        node_name=node_name,
        step=f"fs.write.{Path(path).name}",
        run_id=run_id,
        duration_ms=duration_ms,
        path=path,
        bytes_written=bytes_written,
        sha256_prefix=sha256_prefix,
        content_preview=content_preview[:100] if content_preview else None,
    )


# =============================================================================
# Report Section Tracing
# =============================================================================

def log_report_section(
    section_name: str,
    run_id: Optional[str] = None,
    lines_added: int = 0,
    duration_ms: Optional[float] = None,
):
    """
    Log a report section render event.
    
    Usage:
        start = time.perf_counter()
        _add_what_i_did_section(lines, state)
        log_report_section("what_i_did", run_id=state.run_id,
                          lines_added=len(lines) - prev_len,
                          duration_ms=(time.perf_counter() - start) * 1000)
    """
    log_step_event(
        "report.section",
        node_name="build_report",
        step=f"render.{section_name}",
        run_id=run_id,
        duration_ms=duration_ms,
        section_name=section_name,
        lines_added=lines_added,
    )


# =============================================================================
# Slow Run Bundle Generation
# =============================================================================

# Thresholds for slow run bundle generation (in seconds)
SLOW_NODE_THRESHOLD_SECONDS = 60  # Node taking > 60s is notable
SLOW_RUN_THRESHOLD_SECONDS = 300  # Run taking > 5min is slow


def write_slow_run_bundle(
    run_id: str,
    total_duration_s: float,
    node_durations: Dict[str, float],
    artifacts_dir: Optional[str] = None,
) -> Optional[Path]:
    """
    Write SLOW_RUN_BUNDLE.json if the run exceeded threshold.
    
    Includes:
    - Top 20 slowest steps
    - Node duration breakdown
    - LLM call, DB op, and FS write counts
    - Pointers to per-node JSONL traces
    
    Args:
        run_id: Run identifier
        total_duration_s: Total run duration in seconds
        node_durations: Dict of node_name -> duration_ms
        artifacts_dir: Override artifacts directory
        
    Returns:
        Path to bundle if written, None otherwise
    """
    if total_duration_s < SLOW_RUN_THRESHOLD_SECONDS:
        return None
    
    effective_dir = artifacts_dir or _artifacts_dir.get()
    if not effective_dir:
        return None
    
    try:
        # Get slowest steps
        slowest = get_slowest_steps(20)
        
        # Count event types
        all_timings = _step_timings.get() or []
        llm_calls = sum(1 for t in all_timings if t.event.startswith("llm."))
        db_ops = sum(1 for t in all_timings if t.event.startswith("db."))
        fs_writes = sum(1 for t in all_timings if t.event.startswith("fs."))
        
        # Find per-node trace files
        nodes_dir = Path(effective_dir) / "nodes"
        node_traces = {}
        if nodes_dir.exists():
            for trace_file in nodes_dir.glob("*.jsonl"):
                node_traces[trace_file.stem] = str(trace_file)
        
        # V22-011: Gap detection - compare sum of node times vs total workflow time
        # This detects unaccounted overhead (LangGraph internals, checkpoint serialization, etc.)
        total_ms = total_duration_s * 1000
        node_sum_ms = sum(node_durations.values()) if node_durations else 0
        gap_ms = total_ms - node_sum_ms
        gap_pct = (gap_ms / total_ms) * 100 if total_ms > 0 else 0
        
        # Flag significant gaps (>10% unaccounted time is suspicious)
        gap_suspicious = gap_pct > 10.0
        if gap_suspicious:
            logger.warning(
                f"V22-011 GAP DETECTED: {gap_pct:.1f}% of runtime unaccounted! "
                f"Total={total_ms:.0f}ms, NodeSum={node_sum_ms:.0f}ms, Gap={gap_ms:.0f}ms. "
                f"Investigate: checkpoint overhead, LangGraph internals, or missing timed_node wrappers."
            )
        
        bundle = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "total_duration_s": round(total_duration_s, 2),
            "threshold_s": SLOW_RUN_THRESHOLD_SECONDS,
            # V22-011: Timing gap analysis
            "timing_analysis": {
                "total_ms": round(total_ms, 2),
                "node_sum_ms": round(node_sum_ms, 2),
                "unaccounted_gap_ms": round(gap_ms, 2),
                "gap_percent": round(gap_pct, 1),
                "gap_suspicious": gap_suspicious,
                "gap_threshold_pct": 10.0,
            },
            "node_durations_ms": {
                k: round(v, 2) for k, v in sorted(
                    node_durations.items(),
                    key=lambda x: x[1],
                    reverse=True
                )
            },
            "slowest_steps": [
                {
                    "node": t.node_name,
                    "step": t.step,
                    "event": t.event,
                    "duration_ms": round(t.duration_ms, 2),
                    "ts": t.ts,
                }
                for t in slowest
            ],
            "counts": {
                "llm_calls": llm_calls,
                "db_ops": db_ops,
                "fs_writes": fs_writes,
                "total_steps": len(all_timings),
            },
            "trace_files": {
                "graph_trace": str(Path(effective_dir) / "GRAPH_TRACE.jsonl"),
                "node_traces": node_traces,
            },
        }
        
        bundle_path = Path(effective_dir) / "SLOW_RUN_BUNDLE.json"
        with open(bundle_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2, default=str)
        
        logger.warning(
            f"Slow run detected: {total_duration_s:.1f}s > {SLOW_RUN_THRESHOLD_SECONDS}s threshold. "
            f"Bundle: {bundle_path}"
        )
        return bundle_path
        
    except Exception as e:
        logger.warning(f"Failed to write slow run bundle: {e}")
        return None


# =============================================================================
# Node Trace Context Retrieval
# =============================================================================

def get_node_trace_context() -> Dict[str, Any]:
    """
    Get current tracing context for external use.
    
    Returns dict with trace_id, span_id, parent_span_id, etc.
    """
    return {
        "run_id": _graph_run_id.get(),
        "trace_id": _trace_id.get(),
        "span_id": _step_span_id.get() or _node_span_id.get(),
        "parent_span_id": _node_span_id.get() if _step_span_id.get() else None,
        "node_name": _current_node_name.get(),
        "step": _current_step.get(),
    }


# Need asyncio for coroutine check
import asyncio
