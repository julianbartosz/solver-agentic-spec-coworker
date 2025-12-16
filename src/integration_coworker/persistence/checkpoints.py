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
    - Large field truncation
    - Bug #67 Fix: Convert integers exceeding 64-bit range to strings
    """
    # PostgreSQL BIGINT max: 2^63-1 = 9223372036854775807
    INT64_MAX = 2**63 - 1
    INT64_MIN = -(2**63)
    
    def to_serializable(obj, depth: int = 0):
        # Prevent infinite recursion
        if depth > 10:
            return str(obj)
        
        if obj is None:
            return None
        elif hasattr(obj, '__dataclass_fields__'):
            return {k: to_serializable(v, depth + 1) for k, v in asdict(obj).items()}
        elif isinstance(obj, list):
            return [to_serializable(item, depth + 1) for item in obj]
        elif isinstance(obj, dict):
            return {k: to_serializable(v, depth + 1) for k, v in obj.items()}
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
    
    # Truncate large fields to prevent DB bloat
    if 'doc_chunks' in result and isinstance(result['doc_chunks'], list) and len(result['doc_chunks']) > 100:
        result['doc_chunks'] = result['doc_chunks'][:100]
        result['_truncated'] = {'doc_chunks': True}
    
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
    
    Args:
        run_id: The run identifier
        node_name: The node that just completed
        state: The current workflow state
    """
    state_json = json.dumps(_serialize_state(state))
    
    engine = get_engine_type()
    
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
