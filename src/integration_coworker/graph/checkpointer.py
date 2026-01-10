"""
Custom LangGraph Checkpointer - Bug #101 Fix (v3)

This module provides a custom checkpointer that filters out large channels
BEFORE serialization, preventing checkpoint bloat.

Problem:
- LangGraph serializes EVERY channel value in `_dump_blobs()`
- Fields like spec_chunk_ids (using operator.add reducer) accumulate
  exponentially across checkpoints (1M → 2M → 4M → 86M items → 800MB!)
- The serializer can't filter by channel name because it only sees values

Solution:
- Override `_dump_blobs()` in custom checkpointer to skip excluded channels
- This prevents large data from ever reaching the serializer
- Data is already persisted to Silver/Gold layer tables

Per PRODUCTION_HARDENING_FINAL_REPORT.md, checkpoints should be <10MB.
"""

import logging
from typing import Any, Sequence, cast

from langgraph.checkpoint.base import ChannelVersions
from langgraph.checkpoint.serde.base import SerializerProtocol

logger = logging.getLogger(__name__)


# Channels to completely exclude from checkpoints
# These channels cause bloat and their data is persisted elsewhere
EXCLUDE_CHANNELS = {
    # CRITICAL: Fields using operator.add that grow exponentially
    'spec_chunk_ids',         # Uses operator.add, grows to 86M items (800MB+)
    
    # Large content - persisted to Silver layer tables
    'openapi_spec',           # Raw OpenAPI dict (7MB+ for Stripe/GitHub)
    'spec_documents',         # List of SpecDocument (persisted to DB)
    'spec_sections',          # List of SpecSection (persisted to DB)
    'spec_chunk_embeddings',  # Embeddings (persisted to spec_chunks table)
    
    # Silver layer data - all persisted to DB tables
    'endpoints',              # Persisted to endpoints table
    'schemas',                # Persisted to schemas table  
    'schema_fields',          # Persisted to schema_fields table (16MB!)
    'endpoint_parameters',    # Persisted to endpoint_parameters table
    'entities',               # Persisted to entities table
    'relationships',          # Persisted to entity_relationships table
    
    # Repository data - too large for checkpoints
    'repo_snapshot',          # Contains all repo files (100MB+)
    'repo_markdown_context',  # Full repo markdown (50MB+)
    
    # Other large content
    'doc_chunks',             # Large list of chunk strings
    
    # Bug #101 v16/v17 discovery: Additional large channels
    'pending_specs',          # List of pending spec sections (7.2MB × 14 checkpoints = 100MB!)
}

# Keys to strip from the 'plan' dict channel before checkpointing
# These are large transient values that don't need to persist for resume
PLAN_EXCLUDE_KEYS = {
    'openapi_specs',                     # Large API specs (7MB+ each) - cleared after Silver extraction
    'chunk_index_to_spec_document_uri',  # Can be rebuilt from DB
    'schema_name_to_uri',                # Bug #101 v20: 880 schema mappings = 100KB per write × 22 writes = 2.2MB bloat!
    'candidate_patterns',                # Transient pattern matching data, can be recomputed
}


def _slim_plan_value(plan_dict: dict) -> dict:
    """
    Create a slim copy of the plan dict with large keys removed.
    
    This preserves critical workflow state while removing large transient data.
    """
    if not isinstance(plan_dict, dict):
        return plan_dict
    
    return {k: v for k, v in plan_dict.items() if k not in PLAN_EXCLUDE_KEYS}


def _filtered_dump_blobs(
    serde: SerializerProtocol,
    thread_id: str,
    checkpoint_ns: str,
    values: dict[str, Any],
    versions: ChannelVersions,
) -> list[tuple[str, str, str, str, str, bytes | None]]:
    """
    Dump channel values to blobs, filtering out excluded channels.
    
    This replaces LangGraph's default _dump_blobs to prevent large channels
    from being serialized and stored.
    
    Special handling:
    - Completely excluded channels get 'empty' type
    - 'plan' channel is slimmed to remove large transient keys
    """
    if not versions:
        return []
    
    excluded = []
    result = []
    
    for k, ver in versions.items():
        if k in EXCLUDE_CHANNELS:
            excluded.append(k)
            # Store a placeholder to indicate the channel was excluded
            # Using "empty" type signals to LangGraph this channel has no blob
            result.append((thread_id, checkpoint_ns, k, cast(str, ver), "empty", None))
        elif k in values:
            value = values[k]
            # Special handling for 'plan' dict - remove large transient keys
            if k == 'plan' and isinstance(value, dict):
                value = _slim_plan_value(value)
            # Normal serialization
            type_str, blob = serde.dumps_typed(value)
            result.append((thread_id, checkpoint_ns, k, cast(str, ver), type_str, blob))
        else:
            result.append((thread_id, checkpoint_ns, k, cast(str, ver), "empty", None))
    
    if excluded:
        logger.debug(f"Checkpoint: excluded {len(excluded)} large channels: {excluded}")
    
    return result


def _filtered_dump_writes(
    serde: SerializerProtocol,
    thread_id: str,
    checkpoint_ns: str,
    checkpoint_id: str,
    task_id: str,
    task_path: str,
    writes: "Sequence[tuple[str, Any]]",
) -> list[tuple[str, str, str, str, str, int, str, str, bytes]]:
    """
    Dump intermediate writes, filtering out excluded channels.
    
    Bug #101 Fix v18: LangGraph has TWO separate save paths:
    1. _dump_blobs() - saves to checkpoint_blobs table (we were filtering this)
    2. _dump_writes() - saves to checkpoint_writes table (we were NOT filtering this!)
    
    The checkpoint_writes table stores intermediate writes during parallel execution.
    Without filtering, excluded channels like 'pending_specs' (7.4MB) were still
    being stored in checkpoint_writes, causing DB bloat.
    
    This function filters writes the same way _dump_blobs filters blobs.
    """
    from langgraph.checkpoint.postgres.base import WRITES_IDX_MAP
    
    result = []
    excluded_count = 0
    
    for idx, (channel, value) in enumerate(writes):
        if channel in EXCLUDE_CHANNELS:
            excluded_count += 1
            # Skip excluded channels entirely - don't store any write
            continue
        
        # Special handling for 'plan' dict - remove large transient keys
        if channel == 'plan' and isinstance(value, dict):
            value = _slim_plan_value(value)
        
        # Normal serialization
        type_str, blob = serde.dumps_typed(value)
        result.append((
            thread_id,
            checkpoint_ns,
            checkpoint_id,
            task_id,
            task_path,
            WRITES_IDX_MAP.get(channel, idx),
            channel,
            type_str,
            blob,
        ))
    
    if excluded_count > 0:
        logger.debug(f"Checkpoint writes: excluded {excluded_count} large channel writes")
    
    return result


# Create custom checkpointers that use filtered dump

try:
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    
    class SlimPostgresSaver(PostgresSaver):
        """
        PostgresSaver that excludes large channels from checkpoints.
        
        Bug #101 Fix v18: Also filters _dump_writes to prevent large channels
        from being stored in the checkpoint_writes table during parallel execution.
        
        Usage:
            with SlimPostgresSaver.from_conn_string(db_url) as saver:
                ...
        """
        
        def _dump_blobs(
            self,
            thread_id: str,
            checkpoint_ns: str,
            values: dict[str, Any],
            versions: ChannelVersions,
        ) -> list[tuple[str, str, str, str, str, bytes | None]]:
            """Override to filter excluded channels from checkpoint_blobs."""
            return _filtered_dump_blobs(
                self.serde, thread_id, checkpoint_ns, values, versions
            )
        
        def _dump_writes(
            self,
            thread_id: str,
            checkpoint_ns: str,
            checkpoint_id: str,
            task_id: str,
            task_path: str,
            writes: Sequence[tuple[str, Any]],
        ) -> list[tuple[str, str, str, str, str, int, str, str, bytes]]:
            """Override to filter excluded channels from checkpoint_writes."""
            return _filtered_dump_writes(
                self.serde, thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, writes
            )
    
    class SlimAsyncPostgresSaver(AsyncPostgresSaver):
        """
        AsyncPostgresSaver that excludes large channels from checkpoints.
        
        Bug #101 Fix v18: Also filters _dump_writes to prevent large channels
        from being stored in the checkpoint_writes table during parallel execution.
        
        Usage:
            async with SlimAsyncPostgresSaver.from_conn_string(db_url) as saver:
                ...
        """
        
        def _dump_blobs(
            self,
            thread_id: str,
            checkpoint_ns: str,
            values: dict[str, Any],
            versions: ChannelVersions,
        ) -> list[tuple[str, str, str, str, str, bytes | None]]:
            """Override to filter excluded channels from checkpoint_blobs."""
            return _filtered_dump_blobs(
                self.serde, thread_id, checkpoint_ns, values, versions
            )
        
        def _dump_writes(
            self,
            thread_id: str,
            checkpoint_ns: str,
            checkpoint_id: str,
            task_id: str,
            task_path: str,
            writes: Sequence[tuple[str, Any]],
        ) -> list[tuple[str, str, str, str, str, int, str, str, bytes]]:
            """Override to filter excluded channels from checkpoint_writes."""
            return _filtered_dump_writes(
                self.serde, thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, writes
            )
    
    POSTGRES_AVAILABLE = True

except ImportError:
    POSTGRES_AVAILABLE = False
    SlimPostgresSaver = None  # type: ignore
    SlimAsyncPostgresSaver = None  # type: ignore


def get_slim_postgres_saver():
    """Get the SlimPostgresSaver class if available."""
    if not POSTGRES_AVAILABLE:
        raise ImportError("langgraph-checkpoint-postgres is not installed")
    return SlimPostgresSaver


def get_slim_async_postgres_saver():
    """Get the SlimAsyncPostgresSaver class if available."""
    if not POSTGRES_AVAILABLE:
        raise ImportError("langgraph-checkpoint-postgres is not installed")
    return SlimAsyncPostgresSaver


# =============================================================================
# Checkpointer Context (Canonical Export)
# =============================================================================
# Re-exported from runtime.py to make checkpointer.py the canonical import point
# for checkpointer-related APIs. This avoids leaking runtime internals.

def async_checkpointer_context():
    """
    Async checkpointer context manager.
    
    Canonical import point for checkpointer context.
    Re-exports from runtime.py to keep checkpointer API coherent.
    
    Usage:
        from integration_coworker.graph.checkpointer import async_checkpointer_context
        
        async with async_checkpointer_context() as checkpointer:
            app = build_graph(checkpointer=checkpointer)
            ...
    """
    from integration_coworker.graph.runtime import async_checkpointer_context as _impl
    return _impl()
