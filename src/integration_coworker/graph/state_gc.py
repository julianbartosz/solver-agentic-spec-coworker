"""
State Garbage Collection - V22-MEM Fix for LangGraph Memory Issues.

This module provides explicit memory management for WorkflowState to address:
1. LangGraph internal copies: Each node receives a full state copy
2. Large spec documents: 50MB+ specs need aggressive cleanup
3. Python GC timing: del is non-deterministic, force gc.collect()

Design Principles:
- Clear heavyweight fields ASAP after they're no longer needed
- Use lightweight references (IDs, counts) instead of full content
- Force GC at strategic points (not just after nodes)
- Track memory pressure and adapt behavior

Usage:
    from integration_coworker.graph.state_gc import (
        clear_bronze_content,
        clear_silver_content,
        force_gc_with_stats,
    )
    
    # After silver model is built, bronze content isn't needed
    clear_bronze_content(state)
    force_gc_with_stats("post_silver")
"""
import gc
import sys
import logging
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Tuple

if TYPE_CHECKING:
    from integration_coworker.graph.state import WorkflowState

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Fields that hold large content and can be cleared after specific stages
BRONZE_CONTENT_FIELDS = [
    "openapi_spec",          # Raw OpenAPI dict (7-50MB for large specs)
    "doc_chunks",            # Raw text chunks (can be 10MB+)
    "spec_documents",        # Parsed spec documents
    "spec_sections",         # Parsed sections
    "pending_specs",         # Multi-spec queue
    "repo_markdown_context", # Repo docs (can be 5MB+)
]

SILVER_CONTENT_FIELDS = [
    "spec_chunk_embeddings", # Embeddings (streamed to DB, can clear)
    "parsed_specs",          # Typed ParsedSpec objects
]

GOLD_CONTENT_FIELDS = [
    "repo_snapshot",         # Full repo file contents
    "repo_changes",          # Diffs
]

# V23-002: Repo integration fields - cleared after repo wiring is complete
# These accumulate during attach_repo_context and need to be cleared after
# apply_repo_integration_changes to avoid 3+ hour state bloat gaps
REPO_CONTENT_FIELDS = [
    "repo_snapshot",         # Full repo file contents
    "repo_markdown_context", # Repo docs (can be 5MB+)
]

# Track GC stats for observability
_gc_stats: Dict[str, Dict[str, Any]] = {}


# =============================================================================
# Core GC Functions
# =============================================================================

def force_gc_with_stats(tag: str) -> Dict[str, Any]:
    """
    Force garbage collection with before/after memory stats.
    
    This addresses Python GC timing issues where del marks objects for
    collection but actual memory release is non-deterministic.
    
    Args:
        tag: Label for this GC point (for logging/tracking)
        
    Returns:
        Dict with memory stats (before, after, freed bytes)
    """
    import os
    import resource
    
    # Get RSS before GC
    try:
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == 'darwin':
            rss_before = rss_before  # macOS: bytes
        else:
            rss_before = rss_before * 1024  # Linux: KB -> bytes
    except Exception:
        rss_before = 0
    
    # Track object counts before
    gc_counts_before = gc.get_count()
    
    # Force full collection (all generations)
    gc.collect(0)  # Gen 0 (youngest)
    gc.collect(1)  # Gen 1
    gc.collect(2)  # Gen 2 (oldest, full collection)
    
    # Get RSS after GC
    try:
        rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == 'darwin':
            rss_after = rss_after
        else:
            rss_after = rss_after * 1024
    except Exception:
        rss_after = 0
    
    gc_counts_after = gc.get_count()
    
    freed_bytes = rss_before - rss_after
    
    stats = {
        "tag": tag,
        "rss_before_mb": round(rss_before / (1024 * 1024), 2),
        "rss_after_mb": round(rss_after / (1024 * 1024), 2),
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "gc_counts_before": gc_counts_before,
        "gc_counts_after": gc_counts_after,
    }
    
    _gc_stats[tag] = stats
    
    if freed_bytes > 10 * 1024 * 1024:  # Log if >10MB freed
        logger.info(
            f"[STATE_GC:{tag}] Freed {stats['freed_mb']:.1f}MB "
            f"(RSS: {stats['rss_before_mb']:.1f} -> {stats['rss_after_mb']:.1f} MB)"
        )
    else:
        logger.debug(f"[STATE_GC:{tag}] GC complete, freed {stats['freed_mb']:.1f}MB")
    
    return stats


def _estimate_field_size(value: Any) -> int:
    """Estimate memory size of a field value (bounded to avoid recursion)."""
    if value is None:
        return 0
    if isinstance(value, (str, bytes)):
        return len(value)
    if isinstance(value, dict):
        # Rough estimate: sum of key/value sizes, capped at first level
        total = 0
        for k, v in list(value.items())[:100]:  # Cap iteration
            total += len(str(k)) + (len(str(v)) if isinstance(v, (str, int, float)) else 100)
        return min(total, 50_000_000)  # Cap at 50MB estimate
    if isinstance(value, list):
        if len(value) == 0:
            return 0
        # Sample first 10 elements
        sample_size = sum(_estimate_field_size(v) for v in value[:10])
        return sample_size * (len(value) // 10 + 1)
    return sys.getsizeof(value)


def clear_field(state: "WorkflowState", field_name: str, replacement: Any = None) -> int:
    """
    Clear a field from state and return estimated bytes freed.
    
    Args:
        state: WorkflowState to modify
        field_name: Name of field to clear
        replacement: Value to replace with (default: None for Optional, [] for List)
        
    Returns:
        Estimated bytes that were in the field
    """
    if not hasattr(state, field_name):
        return 0
    
    current_value = getattr(state, field_name, None)
    if current_value is None:
        return 0
    
    # Estimate size before clearing
    estimated_size = _estimate_field_size(current_value)
    
    # Determine appropriate replacement
    if replacement is not None:
        new_value = replacement
    elif isinstance(current_value, list):
        new_value = []
    elif isinstance(current_value, dict):
        new_value = {}
    else:
        new_value = None
    
    # Clear the field
    setattr(state, field_name, new_value)
    
    # Explicitly delete the old value
    del current_value
    
    if estimated_size > 1_000_000:  # Log if >1MB
        logger.debug(
            f"[STATE_GC] Cleared field '{field_name}': ~{estimated_size / (1024*1024):.1f}MB"
        )
    
    return estimated_size


def clear_bronze_content(state: "WorkflowState", force_gc: bool = True) -> Dict[str, Any]:
    """
    Clear bronze-layer content after it's been processed into silver layer.
    
    Call this after build_silver_api_model completes. The raw spec content
    is no longer needed - we have structured Endpoint/Schema/Entity objects.
    
    Args:
        state: WorkflowState to clear
        force_gc: Whether to force GC after clearing
        
    Returns:
        Stats about what was cleared
    """
    total_cleared = 0
    cleared_fields = []
    
    for field_name in BRONZE_CONTENT_FIELDS:
        bytes_cleared = clear_field(state, field_name)
        if bytes_cleared > 0:
            cleared_fields.append(field_name)
            total_cleared += bytes_cleared
    
    stats = {
        "stage": "bronze",
        "fields_cleared": cleared_fields,
        "estimated_bytes_cleared": total_cleared,
        "estimated_mb_cleared": round(total_cleared / (1024 * 1024), 2),
    }
    
    if force_gc and total_cleared > 0:
        gc_stats = force_gc_with_stats("post_bronze_clear")
        stats["gc_stats"] = gc_stats
    
    if cleared_fields:
        logger.info(
            f"[STATE_GC] Cleared bronze content: {cleared_fields}, "
            f"~{stats['estimated_mb_cleared']:.1f}MB"
        )
    
    return stats


def clear_silver_content(state: "WorkflowState", force_gc: bool = True) -> Dict[str, Any]:
    """
    Clear silver-layer content after it's been persisted or processed into gold.
    
    Call this after embeddings are stored in DB and gold layer is built.
    
    Args:
        state: WorkflowState to clear
        force_gc: Whether to force GC after clearing
        
    Returns:
        Stats about what was cleared
    """
    total_cleared = 0
    cleared_fields = []
    
    for field_name in SILVER_CONTENT_FIELDS:
        bytes_cleared = clear_field(state, field_name)
        if bytes_cleared > 0:
            cleared_fields.append(field_name)
            total_cleared += bytes_cleared
    
    stats = {
        "stage": "silver",
        "fields_cleared": cleared_fields,
        "estimated_bytes_cleared": total_cleared,
        "estimated_mb_cleared": round(total_cleared / (1024 * 1024), 2),
    }
    
    if force_gc and total_cleared > 0:
        gc_stats = force_gc_with_stats("post_silver_clear")
        stats["gc_stats"] = gc_stats
    
    if cleared_fields:
        logger.info(
            f"[STATE_GC] Cleared silver content: {cleared_fields}, "
            f"~{stats['estimated_mb_cleared']:.1f}MB"
        )
    
    return stats


def clear_gold_content(state: "WorkflowState", force_gc: bool = True) -> Dict[str, Any]:
    """
    Clear gold-layer content after report is built.
    
    Call this after build_report completes.
    """
    total_cleared = 0
    cleared_fields = []
    
    for field_name in GOLD_CONTENT_FIELDS:
        bytes_cleared = clear_field(state, field_name)
        if bytes_cleared > 0:
            cleared_fields.append(field_name)
            total_cleared += bytes_cleared
    
    stats = {
        "stage": "gold",
        "fields_cleared": cleared_fields,
        "estimated_bytes_cleared": total_cleared,
        "estimated_mb_cleared": round(total_cleared / (1024 * 1024), 2),
    }
    
    if force_gc and total_cleared > 0:
        gc_stats = force_gc_with_stats("post_gold_clear")
        stats["gc_stats"] = gc_stats
    
    return stats


# =============================================================================
# Node-Specific Cleanup Hooks
# =============================================================================

def cleanup_after_ingest(state: "WorkflowState") -> None:
    """
    Cleanup hook for after ingest_spec node.
    
    At this point, raw content has been chunked. We can clear the raw
    content if streaming persistence is enabled (chunks are in DB).
    """
    import os
    if os.environ.get("STREAMING_PERSISTENCE", "").lower() == "true":
        # Chunks are already in DB, clear the in-memory copies
        clear_field(state, "doc_chunks")
        force_gc_with_stats("post_ingest_streaming")


def cleanup_after_silver(state: "WorkflowState") -> None:
    """
    Cleanup hook for after build_silver_api_model and build_silver_file_model.
    
    At this point, openapi_spec has been processed into structured entities.
    The raw dict is no longer needed.
    """
    # Clear the large openapi_spec dict
    clear_field(state, "openapi_spec")
    
    # If streaming persistence, chunks are in DB
    import os
    if os.environ.get("STREAMING_PERSISTENCE", "").lower() == "true":
        clear_bronze_content(state, force_gc=True)
    else:
        # At minimum, clear openapi_spec and force GC
        force_gc_with_stats("post_silver")


def cleanup_after_embedding(state: "WorkflowState") -> None:
    """
    Cleanup hook for after embed_spec_chunks node.
    
    Embeddings are stored in DB (or state.spec_chunk_embeddings).
    If streaming, we can clear the embeddings list.
    """
    import os
    if os.environ.get("STREAMING_PERSISTENCE", "").lower() == "true":
        clear_field(state, "spec_chunk_embeddings")
        force_gc_with_stats("post_embedding_streaming")


def cleanup_after_codegen(state: "WorkflowState") -> None:
    """
    Cleanup hook for after generate_code_and_tests node.
    
    At this point, repo context was used for generation.
    Large repo snapshots can be cleared.
    """
    clear_gold_content(state, force_gc=True)


def clear_repo_content(state: "WorkflowState", force_gc: bool = False) -> None:
    """
    V23-002: Clear repo integration fields from state.
    
    These fields are set by attach_repo_context and used by
    apply_repo_integration_changes. After repo wiring is complete,
    they should be cleared to avoid memory bloat.
    """
    for field in REPO_CONTENT_FIELDS:
        clear_field(state, field)
    
    if force_gc:
        force_gc_with_stats("post_repo_wiring")


def cleanup_after_repo_wiring(state: "WorkflowState") -> None:
    """
    V23-002: Clear repo content after repo integration changes are applied.
    
    Called at the end of apply_repo_integration_changes to free
    repo_snapshot and repo_markdown_context that are no longer needed.
    
    This fixes the 3+ hour state bloat gap where repo_snapshot was
    set in attach_repo_context but never cleared because cleanup_after_codegen
    runs BEFORE attach_repo_context in the workflow order.
    
    Workflow order:
      generate_code_and_tests -> (cleanup_after_codegen) -> ... ->
      attach_repo_context -> (sets repo_snapshot) ->
      analyze_repo_layout -> apply_repo_integration_changes ->
      (cleanup_after_repo_wiring - THIS HOOK) -> validate_integration_design -> ...
    """
    clear_repo_content(state, force_gc=True)


# =============================================================================
# Aggressive GC for Large Specs
# =============================================================================

def estimate_spec_size_mb(state: "WorkflowState") -> float:
    """Estimate total spec content size in MB."""
    total = 0
    
    if state.openapi_spec:
        total += _estimate_field_size(state.openapi_spec)
    
    for doc in (state.spec_documents or []):
        if hasattr(doc, 'content'):
            total += len(doc.content) if doc.content else 0
    
    for chunk in (state.doc_chunks or []):
        total += len(chunk) if isinstance(chunk, str) else 0
    
    return total / (1024 * 1024)


def should_use_aggressive_gc(state: "WorkflowState", threshold_mb: float = 20.0) -> bool:
    """
    Determine if aggressive GC should be used based on spec size.
    
    For large specs (>threshold_mb), we clear content more aggressively
    and force GC more frequently.
    """
    return estimate_spec_size_mb(state) > threshold_mb


def get_gc_stats() -> Dict[str, Dict[str, Any]]:
    """Get all recorded GC stats for observability."""
    return dict(_gc_stats)


def reset_gc_stats() -> None:
    """Reset GC stats (call at start of new run)."""
    global _gc_stats
    _gc_stats = {}
