"""
Trace Sanitizer for LangSmith.

Truncates large payloads before uploading to LangSmith to prevent 422 errors.

LangSmith has a ~10MB payload limit. Large OpenAPI specs (e.g., Twilio at 26MB)
cause trace uploads to fail with "Payload size exceeded limit".

This module provides:
1. Recursive truncation of large strings in trace data
2. Total payload size estimation and capping
3. Clear [TRUNCATED] markers for debugging
4. Graceful degradation (disable remote tracing on repeated failures)

P0.2 Fix: Prevents "422 Unprocessable Entity" errors from LangSmith.

Usage:
    from integration_coworker.utils.trace_sanitizer import (
        sanitize_trace_data,
        is_tracing_healthy,
        record_tracing_error,
    )
    
    # Sanitize before upload
    sanitized = sanitize_trace_data(trace_data, max_total_bytes=5_000_000)
    
    # Check if tracing should be attempted
    if is_tracing_healthy():
        # ... proceed with trace upload
    else:
        # Tracing has been auto-disabled due to repeated failures
        logger.info("Tracing disabled due to prior errors")
"""
import json
import logging
import sys
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# LangSmith payload limits
LANGSMITH_MAX_PAYLOAD_BYTES = 10_000_000  # 10MB official limit
DEFAULT_MAX_PAYLOAD_BYTES = 5_000_000     # 5MB safe target (half of limit)
DEFAULT_MAX_STRING_LENGTH = 50_000        # 50KB per string
DEFAULT_MAX_LIST_ITEMS = 100              # Max items in arrays
TRUNCATION_MARKER = "[TRUNCATED - original size: {}]"


def estimate_json_size(obj: Any) -> int:
    """
    Estimate JSON-serialized size of an object.
    
    Uses sys.getsizeof as a fast approximation rather than
    actually serializing (which would be slow for large objects).
    
    Args:
        obj: Any JSON-serializable object
        
    Returns:
        Estimated size in bytes
    """
    try:
        # For strings, length is a good approximation
        if isinstance(obj, str):
            return len(obj)
        
        # For bytes, use actual length
        if isinstance(obj, bytes):
            return len(obj)
        
        # For dicts/lists, serialize a small sample and extrapolate
        # This is faster than full serialization for large objects
        if isinstance(obj, (dict, list)):
            # Quick estimate based on structure
            return len(json.dumps(obj, default=str))
        
        # For primitives, use repr as approximation
        return len(repr(obj))
        
    except Exception:
        # Fallback to sys.getsizeof if serialization fails
        return sys.getsizeof(obj)


def truncate_string(s: str, max_length: int = DEFAULT_MAX_STRING_LENGTH) -> str:
    """
    Truncate a string with clear marker.
    
    Args:
        s: String to truncate
        max_length: Maximum length in characters
        
    Returns:
        Truncated string with marker, or original if within limit
    """
    if len(s) <= max_length:
        return s
    
    original_size = len(s)
    # Reserve space for truncation marker
    marker = TRUNCATION_MARKER.format(f"{original_size:,} chars")
    truncated_length = max_length - len(marker) - 10
    
    if truncated_length < 100:
        # String is very short, just return marker
        return marker
    
    # Keep beginning for context
    return s[:truncated_length] + "..." + marker


def sanitize_value(
    value: Any,
    max_string_length: int = DEFAULT_MAX_STRING_LENGTH,
    max_list_items: int = DEFAULT_MAX_LIST_ITEMS,
    depth: int = 0,
    max_depth: int = 20,
) -> Any:
    """
    Recursively sanitize a value for trace upload.
    
    Args:
        value: Value to sanitize
        max_string_length: Max characters per string
        max_list_items: Max items per list
        depth: Current recursion depth
        max_depth: Maximum recursion depth
        
    Returns:
        Sanitized value
    """
    if depth > max_depth:
        return "[MAX_DEPTH_EXCEEDED]"
    
    if value is None:
        return None
    
    if isinstance(value, str):
        return truncate_string(value, max_string_length)
    
    if isinstance(value, bytes):
        # Convert bytes to string preview
        preview = value[:1000].decode("utf-8", errors="replace")
        return truncate_string(f"[BYTES: {len(value)} bytes] {preview}", max_string_length)
    
    if isinstance(value, (int, float, bool)):
        return value
    
    if isinstance(value, dict):
        return {
            sanitize_value(k, max_string_length, max_list_items, depth + 1, max_depth): 
            sanitize_value(v, max_string_length, max_list_items, depth + 1, max_depth)
            for k, v in value.items()
        }
    
    if isinstance(value, (list, tuple)):
        items = list(value)
        if len(items) > max_list_items:
            sanitized_items = [
                sanitize_value(item, max_string_length, max_list_items, depth + 1, max_depth)
                for item in items[:max_list_items]
            ]
            sanitized_items.append(
                f"[TRUNCATED - {len(items) - max_list_items} more items]"
            )
            return sanitized_items
        return [
            sanitize_value(item, max_string_length, max_list_items, depth + 1, max_depth)
            for item in items
        ]
    
    # For other types, try to convert to string
    try:
        return truncate_string(str(value), max_string_length)
    except Exception:
        return "[UNSERIALIZABLE]"


def sanitize_trace_data(
    data: Dict[str, Any],
    max_total_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    max_string_length: int = DEFAULT_MAX_STRING_LENGTH,
    max_list_items: int = DEFAULT_MAX_LIST_ITEMS,
) -> Dict[str, Any]:
    """
    Sanitize trace data to fit within LangSmith payload limits.
    
    Performs two passes:
    1. Recursive value sanitization (truncate strings, limit arrays)
    2. Total size check and further truncation if needed
    
    Args:
        data: Trace data dictionary
        max_total_bytes: Maximum total payload size in bytes
        max_string_length: Maximum characters per string field
        max_list_items: Maximum items per array field
        
    Returns:
        Sanitized trace data dictionary
        
    Example:
        >>> large_data = {"spec_content": "..." * 10_000_000}
        >>> sanitized = sanitize_trace_data(large_data)
        >>> len(json.dumps(sanitized)) < 5_000_000
        True
    """
    if not isinstance(data, dict):
        logger.warning(f"sanitize_trace_data expects dict, got {type(data)}")
        return data
    
    # Pass 1: Recursive sanitization
    sanitized = sanitize_value(
        data,
        max_string_length=max_string_length,
        max_list_items=max_list_items,
    )
    
    # Pass 2: Check total size
    try:
        serialized = json.dumps(sanitized, default=str)
        current_size = len(serialized)
        
        if current_size > max_total_bytes:
            # Need more aggressive truncation
            logger.info(
                f"[trace_sanitizer] Payload still too large ({current_size:,} bytes > "
                f"{max_total_bytes:,} bytes), applying aggressive truncation"
            )
            
            # Calculate target string length to hit size target
            reduction_ratio = max_total_bytes / current_size
            aggressive_string_length = int(max_string_length * reduction_ratio * 0.8)
            aggressive_string_length = max(500, aggressive_string_length)  # Min 500 chars
            
            sanitized = sanitize_value(
                data,
                max_string_length=aggressive_string_length,
                max_list_items=max(10, int(max_list_items * reduction_ratio)),
            )
            
            # Add metadata about sanitization
            sanitized["_trace_sanitized"] = {
                "original_size_estimate": current_size,
                "target_size": max_total_bytes,
                "aggressive_truncation": True,
            }
        else:
            sanitized["_trace_sanitized"] = {
                "original_size_estimate": current_size,
                "target_size": max_total_bytes,
                "aggressive_truncation": False,
            }
            
    except Exception as e:
        logger.warning(f"[trace_sanitizer] Size check failed: {e}")
        sanitized["_trace_sanitized"] = {"error": str(e)}
    
    return sanitized


def create_langsmith_callback(
    max_total_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> Any:
    """
    Create a LangSmith callback handler with payload sanitization.
    
    This wraps the standard LangSmith callback to intercept and
    sanitize trace data before upload.
    
    Args:
        max_total_bytes: Maximum payload size in bytes
        
    Returns:
        Configured callback handler (or None if LangSmith not available)
        
    Usage:
        from integration_coworker.utils.trace_sanitizer import create_langsmith_callback
        
        callback = create_langsmith_callback()
        if callback:
            # Use with LangChain
            llm.invoke(..., callbacks=[callback])
    """
    try:
        from langsmith import Client
        from langsmith.run_trees import RunTree
        
        # For now, return None - full implementation would wrap the callback
        # The sanitization is better done at the state level before tracing
        logger.debug("[trace_sanitizer] LangSmith callback creation not yet implemented")
        return None
        
    except ImportError:
        logger.debug("[trace_sanitizer] LangSmith not available")
        return None


def should_sanitize_run(run_data: Dict[str, Any]) -> bool:
    """
    Determine if a run's data should be sanitized based on heuristics.
    
    Returns True if the data is likely to exceed LangSmith limits.
    
    Args:
        run_data: Run data dictionary
        
    Returns:
        True if sanitization is recommended
    """
    try:
        # Quick size estimate
        size = estimate_json_size(run_data)
        
        # Sanitize if over 50% of limit
        threshold = LANGSMITH_MAX_PAYLOAD_BYTES * 0.5
        should_sanitize = size > threshold
        
        if should_sanitize:
            logger.debug(
                f"[trace_sanitizer] Run data size ({size:,} bytes) exceeds threshold "
                f"({threshold:,} bytes), recommending sanitization"
            )
        
        return should_sanitize
        
    except Exception as e:
        logger.warning(f"[trace_sanitizer] Size estimation failed: {e}")
        return True  # Sanitize to be safe


# =============================================================================
# P0.2 Graceful Degradation: Disable tracing on repeated payload errors
# =============================================================================

# Track consecutive tracing failures for graceful degradation
_trace_failure_count = 0
_trace_disabled = False
MAX_CONSECUTIVE_FAILURES = 3  # Disable after this many consecutive failures
PAYLOAD_ERROR_CODES = {422, 403, 413}  # HTTP codes that indicate payload issues


def is_tracing_healthy() -> bool:
    """
    Check if tracing should be attempted.
    
    Returns False if tracing has been auto-disabled due to repeated failures.
    This allows the workflow to continue without tracing rather than failing.
    
    Returns:
        True if tracing should be attempted, False if disabled
    """
    return not _trace_disabled


def record_tracing_error(error: Exception) -> bool:
    """
    Record a tracing error and potentially disable tracing.
    
    Call this when a LangSmith trace upload fails. After MAX_CONSECUTIVE_FAILURES
    failures with payload-related errors (422, 403, 413), tracing is auto-disabled
    for the remainder of the run.
    
    Args:
        error: The exception that occurred during tracing
        
    Returns:
        True if tracing was disabled, False otherwise
    """
    global _trace_failure_count, _trace_disabled
    
    if _trace_disabled:
        return False  # Already disabled
    
    # Check if this is a payload-related error
    error_str = str(error).lower()
    is_payload_error = any(
        indicator in error_str
        for indicator in ["422", "403", "413", "payload", "size", "exceeded", "limit"]
    )
    
    if is_payload_error:
        _trace_failure_count += 1
        logger.warning(
            f"[trace_sanitizer] Trace upload failed ({_trace_failure_count}/{MAX_CONSECUTIVE_FAILURES}): {error}"
        )
        
        if _trace_failure_count >= MAX_CONSECUTIVE_FAILURES:
            _trace_disabled = True
            logger.error(
                f"[trace_sanitizer] Tracing auto-disabled after {_trace_failure_count} "
                f"consecutive payload errors. Workflow will continue without remote tracing."
            )
            return True
    else:
        # Non-payload error, reset counter
        _trace_failure_count = 0
    
    return False


def record_tracing_success() -> None:
    """
    Record a successful trace upload.
    
    Resets the failure counter to prevent premature disabling.
    """
    global _trace_failure_count
    _trace_failure_count = 0


def reset_tracing_state() -> None:
    """
    Reset tracing state to enabled.
    
    Call this at the start of a new run to allow retrying tracing.
    """
    global _trace_failure_count, _trace_disabled
    _trace_failure_count = 0
    _trace_disabled = False


def get_tracing_status() -> Dict[str, Any]:
    """
    Get current tracing health status.
    
    Returns:
        Dict with tracing state information
    """
    return {
        "healthy": is_tracing_healthy(),
        "disabled": _trace_disabled,
        "failure_count": _trace_failure_count,
        "max_failures": MAX_CONSECUTIVE_FAILURES,
    }


# =============================================================================
# P0.2 Byte Budget Enforcement: Hard size cap at serialization boundary
# =============================================================================

# Budget limits (per LangSmith documentation and forum reports)
BATCH_SIZE_BUDGET = 5_000_000  # 5MB per batch (conservative, limit is ~10MB)
SINGLE_TRACE_BUDGET = 2_000_000  # 2MB per individual trace
HIGH_PRIORITY_FIELDS = {"run_id", "name", "run_type", "start_time", "end_time", "error", "status"}
DROPPABLE_FIELDS = {"inputs", "outputs", "serialized", "extra", "events"}


def serialize_with_budget(
    data: Dict[str, Any],
    max_bytes: int = SINGLE_TRACE_BUDGET,
    drop_order: Optional[List[str]] = None,
) -> bytes:
    """
    Serialize trace data with a hard byte budget.
    
    If initial serialization exceeds max_bytes, progressively drops large
    fields (starting with droppable_fields) until under budget.
    
    This is the enforcement point that MUST be called before any LangSmith
    upload to prevent 413/422 errors.
    
    Args:
        data: Trace data dictionary
        max_bytes: Maximum serialized size in bytes (default: 2MB per trace)
        drop_order: Fields to drop in order (default: inputs, outputs, serialized, extra, events)
        
    Returns:
        JSON-encoded bytes within budget
        
    Example:
        >>> trace = {"run_id": "abc", "inputs": {"large_spec": "..." * 10000}}
        >>> serialized = serialize_with_budget(trace, max_bytes=1000)
        >>> len(serialized) <= 1000
        True
    """
    if drop_order is None:
        drop_order = list(DROPPABLE_FIELDS)
    
    # First pass: sanitize large strings
    sanitized = sanitize_trace_data(data, max_total_bytes=max_bytes)
    
    try:
        serialized = json.dumps(sanitized, default=str).encode("utf-8")
        
        if len(serialized) <= max_bytes:
            return serialized
        
        # Over budget: progressively drop fields
        working_data = dict(sanitized)
        for field in drop_order:
            if field in working_data:
                original_value = working_data[field]
                working_data[field] = f"[DROPPED - exceeded {max_bytes:,} byte budget]"
                
                serialized = json.dumps(working_data, default=str).encode("utf-8")
                current_size = len(serialized)
                
                logger.info(
                    f"[trace_sanitizer] Dropped '{field}' to meet budget: "
                    f"{current_size:,}/{max_bytes:,} bytes"
                )
                
                if current_size <= max_bytes:
                    working_data["_budget_dropped_fields"] = working_data.get(
                        "_budget_dropped_fields", []
                    ) + [field]
                    return json.dumps(working_data, default=str).encode("utf-8")
        
        # Still over budget: keep only high priority fields
        minimal_data = {
            k: v for k, v in sanitized.items() 
            if k in HIGH_PRIORITY_FIELDS
        }
        minimal_data["_budget_emergency_truncation"] = True
        minimal_data["_original_size"] = len(json.dumps(data, default=str))
        
        serialized = json.dumps(minimal_data, default=str).encode("utf-8")
        logger.warning(
            f"[trace_sanitizer] Emergency truncation to {len(serialized):,} bytes "
            f"(kept only: {list(minimal_data.keys())})"
        )
        return serialized
        
    except Exception as e:
        logger.error(f"[trace_sanitizer] Serialization failed: {e}")
        # Return minimal valid JSON
        return json.dumps({"error": str(e), "run_id": data.get("run_id")}).encode("utf-8")


def batch_traces_with_budget(
    traces: List[Dict[str, Any]],
    batch_budget: int = BATCH_SIZE_BUDGET,
    per_trace_budget: int = SINGLE_TRACE_BUDGET,
) -> List[List[bytes]]:
    """
    Batch traces respecting per-batch and per-trace size limits.
    
    Splits traces into batches that fit within LangSmith's batch size limit.
    Each trace is first serialized within its individual budget.
    
    Args:
        traces: List of trace dictionaries
        batch_budget: Maximum size per batch in bytes (default: 5MB)
        per_trace_budget: Maximum size per trace in bytes (default: 2MB)
        
    Returns:
        List of batches, where each batch is a list of serialized trace bytes
        
    Example:
        >>> traces = [{"run_id": "1", ...}, {"run_id": "2", ...}]
        >>> batches = batch_traces_with_budget(traces)
        >>> all(sum(len(t) for t in batch) <= 5_000_000 for batch in batches)
        True
    """
    batches: List[List[bytes]] = []
    current_batch: List[bytes] = []
    current_batch_size = 0
    
    for trace in traces:
        # Serialize with per-trace budget
        serialized = serialize_with_budget(trace, max_bytes=per_trace_budget)
        trace_size = len(serialized)
        
        # Check if adding to current batch would exceed budget
        if current_batch_size + trace_size > batch_budget and current_batch:
            # Start new batch
            batches.append(current_batch)
            current_batch = []
            current_batch_size = 0
        
        # Add to current batch
        current_batch.append(serialized)
        current_batch_size += trace_size
    
    # Don't forget the last batch
    if current_batch:
        batches.append(current_batch)
    
    logger.debug(
        f"[trace_sanitizer] Created {len(batches)} batches from {len(traces)} traces"
    )
    return batches


def wrap_langsmith_ingest(
    original_ingest: callable,
    batch_budget: int = BATCH_SIZE_BUDGET,
) -> callable:
    """
    Wrap LangSmith's batch ingest function with budget enforcement.
    
    This should be applied at the LangSmith client boundary to ensure
    all trace uploads respect size limits.
    
    Args:
        original_ingest: The original LangSmith ingest function
        batch_budget: Maximum batch size in bytes
        
    Returns:
        Wrapped function that enforces budget
        
    Usage:
        from langsmith import Client
        client = Client()
        client.batch_ingest_runs = wrap_langsmith_ingest(client.batch_ingest_runs)
    """
    import functools
    
    @functools.wraps(original_ingest)
    def wrapped_ingest(runs: List[Dict[str, Any]], **kwargs):
        if not is_tracing_healthy():
            logger.debug("[trace_sanitizer] Tracing disabled, skipping ingest")
            return None
        
        # Serialize with budget
        batches = batch_traces_with_budget(runs, batch_budget=batch_budget)
        
        results = []
        for batch_idx, batch in enumerate(batches):
            try:
                # Convert back to dicts for the original function
                batch_dicts = [json.loads(b.decode("utf-8")) for b in batch]
                result = original_ingest(batch_dicts, **kwargs)
                results.append(result)
                record_tracing_success()
            except Exception as e:
                logger.warning(f"[trace_sanitizer] Batch {batch_idx} failed: {e}")
                if record_tracing_error(e):
                    # Tracing was disabled, stop trying
                    break
        
        return results
    
    return wrapped_ingest
