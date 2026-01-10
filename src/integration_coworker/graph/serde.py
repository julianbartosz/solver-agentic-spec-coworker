"""
Custom LangGraph Serializer - Bug #101 Fix (v2)

This module provides a custom serializer for LangGraph checkpoints that
excludes large fields from WorkflowState to prevent checkpoint bloat.

Problem:
- LangGraph's default JsonPlusSerializer serializes the ENTIRE WorkflowState
- Fields like openapi_spec (7MB+), repo_snapshot (100MB+), spec_documents
  cause checkpoints to grow to 500MB-2GB per run
- CRITICAL: Fields using `operator.add` reducer (like spec_chunk_ids) cause
  EXPONENTIAL growth because they concatenate across checkpoints
- This causes:
  1. Extreme memory usage (4-5GB per process)
  2. Slow checkpoint writes (10+ seconds)
  3. Disk/database bloat
  4. Demo failures from OOM

Solution:
- Custom serializer that excludes large fields from checkpoint
- Replace accumulating lists with placeholders to prevent exponential growth
- Large data is already persisted to Silver/Gold layer tables
- On resume, data can be reloaded from DB if needed

Per PRODUCTION_HARDENING_FINAL_REPORT.md, checkpoints should be <10MB.
"""

import json
import logging
from typing import Any, Optional, Tuple

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

logger = logging.getLogger(__name__)


# Fields to completely exclude from LangGraph checkpoints
# These fields cause exponential checkpoint growth (500MB-2GB)
# Data is persisted to Silver/Gold layer tables and can be reloaded
EXCLUDE_FROM_CHECKPOINT = {
    # Large raw content (7MB+ each for Stripe/GitHub specs)
    'openapi_spec',
    'raw_spec_content',
    
    # Repository data (100MB+ for large repos)
    'repo_snapshot',
    'repo_markdown_context',
    'full_markdown',
    
    # Parsed content with file bodies
    'parsed_specs',
    
    # Content fields on nested objects
    'content',
    'original_content',
    'files',  # RepoSnapshot.files dict
    
    # Embeddings (large float arrays) - persisted to spec_chunks table
    'embedding',
    'embeddings',
    'spec_chunk_embeddings',  # Full list of SpecChunkEmbedding objects (7MB+ each)
    
    # Silver layer data - persisted to DB, can be reloaded if needed
    'spec_documents',   # Persisted to spec_documents table
    'spec_sections',    # Persisted to spec_sections table
    'endpoints',        # Persisted to endpoints table - LARGE
    'schemas',          # Persisted to schemas table - LARGE
    'schema_fields',    # Persisted to schema_fields table - LARGE (16MB!)
    'endpoint_parameters',  # Persisted to endpoint_parameters table
    'doc_chunks',       # Large list of chunk strings
    
    # CRITICAL: Fields that use operator.add reducer cause EXPONENTIAL GROWTH
    # because they concatenate across checkpoints (1M → 2M → 4M → 86M items!)
    'spec_chunk_ids',   # Uses operator.add, grows exponentially - 827MB!
    
    # Large nested structures
    'entities',
    'relationships',
}

# Large string fields to truncate (keep first N chars)
# These fields should be summarized, not stored in full
TRUNCATE_STRING_FIELDS = {
    'tree_markdown': 5000,
    'report_markdown': 10000,
    'plan': 50000,  # plan can get large with full context
}

# List fields to cap at max items (for debugging, not full data)
MAX_LIST_ITEMS = {
    'workflow_nodes': 50,
    'code_artifacts': 20,
    'policies': 20,
    'errors': 100,
    'warnings': 100,
    'completed_steps': 200,
}


class SlimCheckpointSerializer(JsonPlusSerializer):
    """
    Custom serializer that excludes large fields from checkpoints.
    
    Extends JsonPlusSerializer to:
    1. Exclude fields in EXCLUDE_FROM_CHECKPOINT
    2. Truncate large lists and strings
    3. Log what was excluded for debugging
    
    Usage:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from integration_coworker.graph.serde import SlimCheckpointSerializer
        
        async with AsyncPostgresSaver.from_conn_string(
            db_url, 
            serde=SlimCheckpointSerializer()
        ) as saver:
            ...
    """
    
    def __init__(self):
        super().__init__()
        self._last_excluded = {}  # Track what was excluded for logging
    
    def _slim_value(self, value: Any, field_name: Optional[str] = None, depth: int = 0) -> Any:
        """
        Recursively slim a value for checkpointing.
        
        - Excludes fields in EXCLUDE_FROM_CHECKPOINT
        - Truncates large lists
        - Truncates large strings
        - Handles nested dicts and dataclasses
        """
        if depth > 15:
            return str(value) if value is not None else None
        
        # Check field name exclusion at top level
        if field_name and field_name in EXCLUDE_FROM_CHECKPOINT:
            self._last_excluded[field_name] = "excluded"
            return None
        
        if value is None:
            return None
        
        # Handle dataclasses (have __dataclass_fields__)
        if hasattr(value, '__dataclass_fields__'):
            from dataclasses import fields
            result = {}
            for f in fields(value):
                # Check field exclusion - ALWAYS check, not just at top level
                if f.name in EXCLUDE_FROM_CHECKPOINT:
                    self._last_excluded[f.name] = "excluded"
                    result[f.name] = None
                else:
                    field_value = getattr(value, f.name)
                    result[f.name] = self._slim_value(field_value, f.name, depth + 1)
            
            # Add type info for proper deserialization
            result["__dataclass__"] = type(value).__name__
            return result
        
        # Handle dicts
        if isinstance(value, dict):
            result = {}
            for k, v in value.items():
                if k in EXCLUDE_FROM_CHECKPOINT:
                    self._last_excluded[k] = "excluded"
                    result[k] = None
                else:
                    result[k] = self._slim_value(v, k, depth + 1)
            return result
        
        # Handle lists - recursively slim each item
        if isinstance(value, list):
            if field_name and field_name in MAX_LIST_ITEMS:
                max_items = MAX_LIST_ITEMS[field_name]
                if len(value) > max_items:
                    self._last_excluded[field_name] = f"truncated from {len(value)} to {max_items}"
                    value = value[:max_items]
            # Recurse into each list item (dataclasses in lists will have their content excluded)
            return [self._slim_value(item, None, depth + 1) for item in value]
        
        # Handle strings with truncation
        if isinstance(value, str):
            if field_name and field_name in TRUNCATE_STRING_FIELDS:
                max_chars = TRUNCATE_STRING_FIELDS[field_name]
                if len(value) > max_chars:
                    self._last_excluded[field_name] = f"truncated from {len(value)} chars to {max_chars}"
                    return value[:max_chars] + "\n...[truncated]..."
            return value
        
        # Handle enums
        if hasattr(value, 'value'):
            return value.value
        
        # Primitives pass through
        if isinstance(value, (bool, int, float)):
            return value
        
        # DateTime handling
        from datetime import datetime
        if isinstance(value, datetime):
            return value.isoformat()
        
        # Path handling
        from pathlib import Path
        if isinstance(value, Path):
            return str(value)
        
        # Fallback to string
        return str(value)
    
    def dumps_typed(self, obj: Any) -> Tuple[str, bytes]:
        """
        Serialize object, excluding large fields.
        
        Overrides parent to slim WorkflowState and any nested dataclasses
        before standard serialization.
        
        LangGraph serializes channel values individually, so we need to handle:
        - Direct dataclass serialization (WorkflowState)
        - List of dataclasses (spec_documents, endpoints, etc.)
        - Dicts containing dataclasses
        """
        self._last_excluded = {}
        
        # Check if this is any dataclass (including WorkflowState)
        if hasattr(obj, '__dataclass_fields__'):
            # Slim the dataclass to exclude large fields
            slimmed = self._slim_value(obj)
            if self._last_excluded:
                logger.debug(f"Checkpoint slimmed: excluded {list(self._last_excluded.keys())}")
            # Use parent's serialization on the slimmed dict
            return super().dumps_typed(slimmed)
        
        # For lists (LangGraph may serialize channel lists directly)
        if isinstance(obj, list) and len(obj) > 0:
            # Check if list contains dataclasses that need slimming
            slimmed = self._slim_value(obj)
            if self._last_excluded:
                logger.debug(f"List slimmed: {len(self._last_excluded)} fields excluded")
            return super().dumps_typed(slimmed)
        
        # For dicts that might contain state
        if isinstance(obj, dict):
            slimmed = self._slim_value(obj)
            if self._last_excluded:
                logger.debug(f"Checkpoint slimmed: {len(self._last_excluded)} fields excluded/truncated")
            return super().dumps_typed(slimmed)
        
        # Default: use parent serialization
        return super().dumps_typed(obj)


def get_slim_serializer() -> SlimCheckpointSerializer:
    """
    Get the singleton slim checkpoint serializer.
    
    Returns:
        SlimCheckpointSerializer instance
    """
    return SlimCheckpointSerializer()
