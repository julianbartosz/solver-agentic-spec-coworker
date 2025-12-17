"""
Checkpoint persistence for workflow recovery.

Per V2 Implementation Plan Section 3.5:
Provides save/load operations for WorkflowState at node boundaries.

This enables:
- Resume capability from any checkpointed node
- True skip with dependency analysis
- Recovery from process crashes
"""
import json
import logging
from typing import Optional, List, Dict, Any
from dataclasses import asdict, fields
from datetime import datetime, timezone

from integration_coworker.persistence.db import get_connection, get_engine_type

logger = logging.getLogger(__name__)


def _serialize_state(state: "WorkflowState") -> Dict[str, Any]:
    """
    Serialize WorkflowState to JSON-compatible dict.
    
    Handles:
    - Dataclass serialization
    - Circular reference prevention
    - Large field truncation/exclusion
    - Bug #67 Fix: Convert integers exceeding 64-bit range to strings
    - Bug #82 Fix: Exclude massive openapi_spec and spec_documents from checkpoints
    - Bug #89 Fix: Exclude repo_snapshot.files and other large nested structures
    """
    # PostgreSQL BIGINT max: 2^63-1 = 9223372036854775807
    INT64_MAX = 2**63 - 1
    INT64_MIN = -(2**63)
    
    # Maximum JSON size before warning/truncation (100MB)
    MAX_CHECKPOINT_SIZE_MB = 100
    MAX_CHECKPOINT_SIZE_BYTES = MAX_CHECKPOINT_SIZE_MB * 1024 * 1024
    
    # Fields to completely exclude from checkpoints (too large, can be reloaded)
    # These fields can be 7MB+ for large specs like Stripe/GitHub
    EXCLUDE_FIELDS = {
        'openapi_spec',       # Raw OpenAPI dict - can be 7MB+
        'spec_documents',     # List of SpecDocument with content
        'spec_sections',      # Can be very large
        'raw_spec_content',   # Raw string if present
        'parsed_specs',       # Can be very large
        'files',              # Bug #89: RepoSnapshot.files contains file contents
        'file_contents',      # Any file content field
        'content',            # Generic content field (could be large)
        'full_markdown',      # RepoSnapshot.full_markdown can be huge
        'repo_markdown_context',  # Can be large for big repos
    }
    
    # Fields to truncate if they exceed size limits
    TRUNCATE_FIELDS = {
        'doc_chunks': 100,           # Max 100 chunks
        'spec_chunk_embeddings': 50, # Max 50 embeddings in checkpoint
        'endpoints': 200,            # Max 200 endpoints
        'schemas': 200,              # Max 200 schemas
        'changes': 50,               # Max 50 file changes in RepoChangeSet
        'workflow_nodes': 100,       # Max 100 workflow nodes
        'code_artifacts': 50,        # Max 50 code artifacts
    }
    
    # String fields to truncate (keep first N chars)
    TRUNCATE_STRING_FIELDS = {
        'tree_markdown': 10000,      # Max 10K chars for tree structure
        'report_markdown': 50000,    # Max 50K chars for report
    }
    
    def to_serializable(obj, depth: int = 0, field_name: str = None):
        # Prevent infinite recursion
        if depth > 10:
            return str(obj)
        
        # Bug #82 Fix: Skip excluded fields BEFORE serializing
        if field_name and field_name in EXCLUDE_FIELDS:
            return None
        
        if obj is None:
            return None
        elif hasattr(obj, '__dataclass_fields__'):
            # Use field iteration instead of asdict to control exclusion
            result = {}
            for f in fields(obj):
                if f.name in EXCLUDE_FIELDS:
                    result[f.name] = None  # Exclude large fields
                else:
                    result[f.name] = to_serializable(getattr(obj, f.name), depth + 1, f.name)
            return result
        elif isinstance(obj, list):
            return [to_serializable(item, depth + 1) for item in obj]
        elif isinstance(obj, dict):
            return {k: to_serializable(v, depth + 1, k) for k, v in obj.items()}
        elif hasattr(obj, 'value'):  # Enum
            return obj.value
        elif isinstance(obj, bool):
            # Check bool before int since bool is subclass of int
            return obj
        elif isinstance(obj, int):
            # Bug #67 Fix: Convert large integers that exceed PostgreSQL BIGINT range
            # This prevents "Integer exceeds 64-bit range" errors with large specs
            if obj > INT64_MAX or obj < INT64_MIN:
                return str(obj)
            return obj
        elif isinstance(obj, (str, float)):
            return obj
        elif isinstance(obj, datetime):
            return obj.isoformat()
        else:
            return str(obj)
    
    result = to_serializable(state)
    
    # Track what was excluded/truncated for debugging
    excluded_info = {}
    
    # Record what was excluded
    for field_name in EXCLUDE_FIELDS:
        if field_name in result and result.get(field_name) is None:
            # Check if original state had data
            if hasattr(state, field_name) and getattr(state, field_name):
                original = getattr(state, field_name)
                if isinstance(original, dict):
                    excluded_info[field_name] = f"dict_excluded_keys={len(original)}"
                elif isinstance(original, list):
                    excluded_info[field_name] = f"list_excluded_len={len(original)}"
                else:
                    excluded_info[field_name] = "excluded"
    
    # Truncate large list fields
    for field_name, max_items in TRUNCATE_FIELDS.items():
        if field_name in result and isinstance(result[field_name], list):
            if len(result[field_name]) > max_items:
                excluded_info[field_name] = f"truncated_from={len(result[field_name])}"
                result[field_name] = result[field_name][:max_items]
    
    # Truncate large string fields
    for field_name, max_chars in TRUNCATE_STRING_FIELDS.items():
        if field_name in result and isinstance(result[field_name], str):
            if len(result[field_name]) > max_chars:
                excluded_info[field_name] = f"truncated_from={len(result[field_name])}_chars"
                result[field_name] = result[field_name][:max_chars] + "\n...[truncated]..."
    
    # Deep truncate: recursively handle nested structures that might have large fields
    def deep_truncate(obj, path=""):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                # Skip excluded fields at any depth
                if k in EXCLUDE_FIELDS:
                    obj[k] = None
                    excluded_info[f"{path}.{k}" if path else k] = "deep_excluded"
                elif isinstance(v, str) and len(v) > 50000:
                    # Truncate any string > 50KB
                    obj[k] = v[:10000] + f"\n...[truncated from {len(v)} chars]..."
                    excluded_info[f"{path}.{k}" if path else k] = f"deep_truncated_str={len(v)}"
                elif isinstance(v, (dict, list)):
                    deep_truncate(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, (dict, list)):
                    deep_truncate(item, f"{path}[{i}]")
    
    deep_truncate(result)
    
    if excluded_info:
        result['_checkpoint_excluded'] = excluded_info
    
    return result


def _deserialize_state(data: Dict[str, Any]) -> "WorkflowState":
    """
    Deserialize JSON dict back to WorkflowState.
    
    Note: Some fields may be truncated; full data is in DB tables.
    This is primarily used for recovery to get completed_steps and plan.
    """
    from integration_coworker.graph.state import WorkflowState
    
    # Create a new state with required fields from checkpoint data
    state = WorkflowState(
        source_refs=data.get('source_refs', []),
        spec_refs=data.get('spec_refs', []),
        task_description=data.get('task_description', ''),
    )
    
    # Copy simple fields
    state.run_id = data.get('run_id')
    state.provider_code = data.get('provider_code')
    state.completed_steps = data.get('completed_steps', [])
    state.errors = data.get('errors', [])
    state.warnings = data.get('warnings', [])
    state.plan = data.get('plan', {})
    state.doc_chunks = data.get('doc_chunks', [])
    state.node_timings = data.get('node_timings', {})
    
    # For checkpoint resume, we primarily need completed_steps and plan
    # Complex objects (endpoints, schemas, etc.) are reconstructed from DB
    
    return state


def save_checkpoint(
    run_id: str,
    node_name: str,
    state: "WorkflowState",
) -> None:
    """
    Save a checkpoint after node execution.
    
    Uses UPSERT to handle re-runs of the same node.
    Includes safety checks for oversized payloads (Bug #89).
    
    Args:
        run_id: The run identifier
        node_name: The node that just completed
        state: The current workflow state
    """
    # Maximum checkpoint size: 100MB (PostgreSQL can handle this, but larger is risky)
    MAX_CHECKPOINT_SIZE_MB = 100
    MAX_CHECKPOINT_SIZE_BYTES = MAX_CHECKPOINT_SIZE_MB * 1024 * 1024
    
    serialized = _serialize_state(state)
    state_json = json.dumps(serialized)
    
    # Safety check: if checkpoint is still too large, create a minimal checkpoint
    json_size = len(state_json.encode('utf-8'))
    if json_size > MAX_CHECKPOINT_SIZE_BYTES:
        logger.warning(
            f"Checkpoint for {node_name} is {json_size / 1024 / 1024:.1f}MB "
            f"(exceeds {MAX_CHECKPOINT_SIZE_MB}MB limit). Creating minimal checkpoint."
        )
        # Create minimal checkpoint with only essential recovery data
        minimal_state = {
            'run_id': getattr(state, 'run_id', run_id),
            'provider_code': getattr(state, 'provider_code', None),
            'completed_steps': getattr(state, 'completed_steps', []),
            'plan': getattr(state, 'plan', {}),
            'errors': getattr(state, 'errors', []),
            'warnings': getattr(state, 'warnings', []),
            'node_timings': getattr(state, 'node_timings', {}),
            'spec_refs': getattr(state, 'spec_refs', []),
            'task_description': getattr(state, 'task_description', ''),
            '_checkpoint_truncated': True,
            '_original_size_mb': round(json_size / 1024 / 1024, 2),
        }
        state_json = json.dumps(minimal_state)
        logger.info(f"Minimal checkpoint size: {len(state_json) / 1024:.1f}KB")
    
    engine = get_engine_type()
    
    try:
        if engine == "postgres":
            from integration_coworker.persistence.postgres import get_connection as pg_get_connection
            
            with pg_get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO integration_gold.run_checkpoints 
                            (run_id, node_name, state_json)
                        VALUES (%s, %s, %s::jsonb)
                        ON CONFLICT (run_id, node_name) 
                        DO UPDATE SET state_json = EXCLUDED.state_json,
                                      created_at = NOW()
                    """, (run_id, node_name, state_json))
                conn.commit()
        else:
            # SQLite fallback
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO run_checkpoints 
                    (run_id, node_name, state_json, created_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (run_id, node_name, state_json))
            conn.commit()
            conn.close()
        
        logger.debug(f"Saved checkpoint for {run_id} at node {node_name}")
        
    except Exception as e:
        # Log but don't fail the workflow - checkpoints are for recovery, not critical path
        logger.warning(
            f"Failed to save checkpoint for {run_id}/{node_name}: {e}. "
            "Workflow will continue but resume capability may be limited."
        )


def load_checkpoint(
    run_id: str,
    node_name: Optional[str] = None,
) -> Optional["WorkflowState"]:
    """
    Load a checkpoint for a run.
    
    Args:
        run_id: The run to load
        node_name: Specific node to load, or None for latest
        
    Returns:
        WorkflowState if checkpoint exists, None otherwise
    """
    engine = get_engine_type()
    
    if engine == "postgres":
        from integration_coworker.persistence.postgres import get_connection as pg_get_connection
        
        with pg_get_connection() as conn:
            with conn.cursor() as cur:
                if node_name:
                    cur.execute("""
                        SELECT state_json FROM integration_gold.run_checkpoints
                        WHERE run_id = %s AND node_name = %s
                    """, (run_id, node_name))
                else:
                    cur.execute("""
                        SELECT state_json FROM integration_gold.run_checkpoints
                        WHERE run_id = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (run_id,))
                
                row = cur.fetchone()
                if not row:
                    return None
                
                data = row[0]  # JSONB auto-converts to dict
                return _deserialize_state(data)
    else:
        conn = get_connection()
        cur = conn.cursor()
        
        if node_name:
            cur.execute("""
                SELECT state_json FROM run_checkpoints
                WHERE run_id = ? AND node_name = ?
            """, (run_id, node_name))
        else:
            cur.execute("""
                SELECT state_json FROM run_checkpoints
                WHERE run_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (run_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        data = json.loads(row[0])
        return _deserialize_state(data)


def get_completed_nodes(run_id: str) -> List[str]:
    """
    Get list of completed node names for a run.
    
    Args:
        run_id: The run identifier
        
    Returns:
        List of node names that have checkpoints, in execution order
    """
    engine = get_engine_type()
    
    if engine == "postgres":
        from integration_coworker.persistence.postgres import get_connection as pg_get_connection
        
        with pg_get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT node_name FROM integration_gold.run_checkpoints
                    WHERE run_id = %s
                    ORDER BY created_at
                """, (run_id,))
                return [row[0] for row in cur.fetchall()]
    else:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT node_name FROM run_checkpoints
            WHERE run_id = ?
            ORDER BY created_at
        """, (run_id,))
        result = [row[0] for row in cur.fetchall()]
        conn.close()
        return result


def delete_checkpoints(run_id: str) -> None:
    """
    Delete all checkpoints for a run (cleanup after success).
    
    Args:
        run_id: The run to clean up
    """
    engine = get_engine_type()
    
    if engine == "postgres":
        from integration_coworker.persistence.postgres import get_connection as pg_get_connection
        
        with pg_get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM integration_gold.run_checkpoints
                    WHERE run_id = %s
                """, (run_id,))
            conn.commit()
    else:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM run_checkpoints WHERE run_id = ?", (run_id,))
        conn.commit()
        conn.close()
    
    logger.debug(f"Deleted checkpoints for run {run_id}")
