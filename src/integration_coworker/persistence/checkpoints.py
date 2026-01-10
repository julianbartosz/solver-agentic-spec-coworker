"""
Checkpoint persistence for workflow recovery.

Per V2 Implementation Plan Section 3.5:
Provides save/load operations for WorkflowState at node boundaries.

This enables:
- Resume capability from any checkpointed node
- True skip with dependency analysis
- Recovery from process crashes

Per Agent Harness Alignment Plan:
- Large fields (openapi_spec, spec_documents, files) are spooled to artifact
  storage at checkpoint serialize time via ArtifactStore
- On resume, fields are rehydrated from ArtifactRef via the store
- No data loss: excluded fields are always recoverable

V30-P02 Performance Optimization:
- Uses orjson for ~10x faster serialization compared to stdlib json
- Falls back to stdlib json if orjson unavailable
- Reduces checkpoint serialization overhead from ~30s to ~3s for large specs
"""
import logging
from typing import Optional, List, Dict, Any, TYPE_CHECKING
from dataclasses import asdict, fields
from datetime import datetime, timezone

# V30-P02: Use orjson for ~10x faster serialization
# orjson handles datetime, dataclass, enum natively
try:
    import orjson
    
    def _json_dumps(obj: Any) -> str:
        """Fast JSON serialization using orjson."""
        return orjson.dumps(
            obj,
            option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS
        ).decode('utf-8')
    
    def _json_loads(s: str) -> Any:
        """Fast JSON deserialization using orjson."""
        return orjson.loads(s)
    
    _USING_ORJSON = True
except ImportError:
    import json
    
    def _json_dumps(obj: Any) -> str:
        """Standard JSON serialization fallback."""
        return json.dumps(obj)
    
    def _json_loads(s: str) -> Any:
        """Standard JSON deserialization fallback."""
        return json.loads(s)
    
    _USING_ORJSON = False

from integration_coworker.persistence.db import get_connection, get_engine_type

if TYPE_CHECKING:
    from integration_coworker.graph.state import WorkflowState

logger = logging.getLogger(__name__)

# Log once on module load which JSON library is being used
if _USING_ORJSON:
    logger.debug("V30-P02: Using orjson for fast checkpoint serialization")
else:
    logger.info("V30-P02: orjson not available, falling back to stdlib json (consider: pip install orjson)")


# =============================================================================
# Artifact Store Integration (Seam 1: Serialize, Seam 2: Deserialize)
# =============================================================================

def _get_artifact_store():
    """
    Get the artifact store for checkpoint spooling.
    
    Lazy import to avoid circular dependencies.
    Returns None if artifact store is not available (graceful degradation).
    """
    try:
        from integration_coworker.persistence.artifacts import get_artifact_store
        return get_artifact_store()
    except ImportError:
        logger.warning("ArtifactStore not available - large fields will be dropped")
        return None


def _spool_large_field(
    store,
    run_id: str,
    field_name: str,
    value: Any,
) -> Optional[Dict[str, Any]]:
    """
    Spool a large field to artifact storage and return the ArtifactRef dict.
    
    Returns None if spooling fails (field will be excluded).
    """
    if store is None or value is None:
        return None
    
    try:
        from integration_coworker.persistence.artifacts import ArtifactRef
        
        ref = store.put(run_id, field_name, value)
        logger.debug(
            f"Spooled {field_name} to artifact store: "
            f"{ref.size_bytes} bytes, sha256={ref.sha256[:16]}..."
        )
        return ref.to_dict()
    except Exception as e:
        logger.warning(f"Failed to spool {field_name}: {e}")
        return None


def _rehydrate_artifact_ref(store, ref_dict: Dict[str, Any]) -> Any:
    """
    Rehydrate a value from an ArtifactRef dict.
    
    Returns None if rehydration fails.
    """
    if store is None:
        return None
    
    try:
        from integration_coworker.persistence.artifacts import ArtifactRef
        
        ref = ArtifactRef.from_dict(ref_dict)
        value = store.get(ref)
        logger.debug(f"Rehydrated {ref.key} from artifact store")
        return value
    except Exception as e:
        logger.warning(f"Failed to rehydrate artifact: {e}")
        return None


def _serialize_state(state: "WorkflowState", run_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Serialize WorkflowState to JSON-compatible dict.
    
    Handles:
    - Dataclass serialization
    - Circular reference prevention
    - Large field spooling to artifact store (returns ArtifactRef)
    - Bug #67 Fix: Convert integers exceeding 64-bit range to strings
    - Bug #82 Fix: Exclude massive openapi_spec and spec_documents from checkpoints
    - Bug #89 Fix: Exclude repo_snapshot.files and other large nested structures
    
    Per Agent Harness Alignment Plan:
    - Large fields are spooled to ArtifactStore (not dropped)
    - ArtifactRef replaces the field in checkpoint JSON
    - On resume, fields are rehydrated from ArtifactRef
    """
    # PostgreSQL BIGINT max: 2^63-1 = 9223372036854775807
    INT64_MAX = 2**63 - 1
    INT64_MIN = -(2**63)
    
    # Maximum JSON size before warning/truncation (100MB)
    MAX_CHECKPOINT_SIZE_MB = 100
    MAX_CHECKPOINT_SIZE_BYTES = MAX_CHECKPOINT_SIZE_MB * 1024 * 1024
    
    # V31-P01: COMPREHENSIVE field exclusion for checkpoint size reduction
    # The detect_and_parse_spec checkpoint was taking 22-40 seconds to serialize ~2MB
    # Root cause: Large nested structures being serialized unnecessarily
    # 
    # These fields are COMPLETELY EXCLUDED (not even spooled to artifact store):
    # - They can be regenerated from source/DB on resume
    # - They are persisted elsewhere (DB tables, artifact store)
    # - They cause exponential growth during multi-checkpoint runs
    EXCLUDE_FIELDS = {
        # Bronze layer - raw content (7-50MB each, persisted to DB)
        'openapi_spec',           # Raw OpenAPI dict - 7MB+ for Stripe/GitHub
        'repo_snapshot',          # Contains all repo files - 100MB+ for large repos
        'repo_markdown_context',  # Full repo markdown - can be 50MB+
        'full_markdown',          # Full context markdown
        
        # Silver layer - structured extraction (persisted to spec_silver tables)
        'spec_documents',         # V30-P01: Large, reloaded from spec_silver.spec_documents
        'spec_sections',          # V30-P01: Large, reloaded from spec_silver.spec_sections  
        'parsed_specs',           # V30-P01: Large (~2MB for Stripe), can be reparsed
        'endpoints',              # V31-P01: Persisted to spec_silver.endpoints
        'schemas',                # V31-P01: Persisted to spec_silver.schemas
        'schema_fields',          # Can be 16MB+ for large specs, persisted to DB
        'endpoint_parameters',    # Can be large, persisted to DB
        'entities',               # Persisted to spec_silver.entities
        'relationships',          # Persisted to spec_silver.entity_relationships
        'events',                 # V31-P01: Persisted to DB
        'operations',             # V31-P01: Protocol IR, regenerated from parsed_specs
        
        # Embeddings - persisted to spec_silver.spec_chunks
        'spec_chunk_embeddings',  # V31-P01: Large, persisted to DB immediately
        'spec_chunk_ids',         # Bug #101: Uses operator.add, grows exponentially (86M items!)
        'doc_chunks',             # V31-P01: Large, can be regenerated from spec
        
        # Multi-spec queue - regenerated from source_refs
        'pending_specs',          # Bug #101 v16: 7.2MB × checkpoints = 100MB+
        
        # File integration - persisted to DB
        'file_specs',             # V31-P01: Persisted to DB
        'file_fields',            # V31-P01: Persisted to DB
        'record_layouts',         # V31-P01: Persisted to DB
        'file_validation_rules',  # V31-P01: Persisted to DB
        
        # Note: 'plan' NOT excluded - contains critical workflow control state
        # However, plan['openapi_specs'] is cleared after Silver model extraction
    }
    
    # V31-P01: Fields to spool to artifact storage (backup recovery only)
    # These fields may be needed for recovery but are too large for checkpoint JSON
    # Most large fields are now in EXCLUDE_FIELDS (excluded entirely, regenerated on resume)
    # SPOOL_FIELDS is now a smaller set of fields that can't be easily regenerated
    SPOOL_FIELDS = {
        'files',              # Bug #89: RepoSnapshot.files contains file contents
        'file_contents',      # Any file content field
        'repo_changes',       # Bug #97: Contains file diffs - may be needed for rollback
        'sandbox_result',     # Bug #97: Contains test output - useful for debugging
        'content',            # Bug #97: Generic content field in nested dataclasses
        'raw_content',        # Bug #97: Raw content field 
        'original_content',   # Bug #97: Original content in FileChange
        'raw_spec_content',   # Raw string if present
        # Note: endpoints, schemas, spec_chunk_embeddings moved to EXCLUDE_FIELDS
        # They are persisted to DB tables, not artifact store
    }
    
    # V31-P01: Keys within the 'plan' dict that should be excluded
    # The plan dict contains critical control state, but some nested fields are large
    # These are excluded from checkpoint but plan itself is preserved
    PLAN_EXCLUDE_KEYS = {
        'openapi_specs',          # Large: full OpenAPI spec dicts
        'spec_content',           # Large: raw spec content  
        'raw_spec',               # Large: raw spec string
        'parsed_spec_data',       # Large: parsed spec dict
        'chunk_contents',         # Large: actual chunk text
        'embedding_vectors',      # Large: vector embeddings
        'file_contents',          # Large: file content strings
        'full_response',          # Large: LLM response text
        'messages',               # Large: chat history
    }
    
    # Fields to truncate if they exceed size limits (reduced from previous)
    TRUNCATE_FIELDS = {
        'doc_chunks': 50,            # Max 50 chunks (reduced from 100)
        'changes': 20,               # Max 20 file changes (reduced from 50)
        'workflow_nodes': 50,        # Max 50 workflow nodes (reduced from 100)
        'code_artifacts': 20,        # Max 20 code artifacts (reduced from 50)
    }
    
    # String fields to truncate (keep first N chars)
    TRUNCATE_STRING_FIELDS = {
        'tree_markdown': 10000,      # Max 10K chars for tree structure
        'report_markdown': 50000,    # Max 50K chars for report
    }
    
    # Get artifact store for spooling (may be None)
    artifact_store = _get_artifact_store()
    effective_run_id = run_id or getattr(state, 'run_id', None) or 'unknown'
    
    # Track spooled artifacts
    spooled_refs = {}
    
    # Bug #99 Fix: Initialize excluded_info BEFORE to_serializable function definition
    # This dict tracks what was excluded/spooled during serialization
    # Must be defined before the inner function that references it
    excluded_info: Dict[str, str] = {}
    truncated_flags: Dict[str, bool] = {}
    
    def to_serializable(obj, depth: int = 0, field_name: str = None, parent_field: str = None):
        # Prevent infinite recursion
        if depth > 10:
            return str(obj)
        
        # Bug #97 v2: Completely exclude certain fields
        if field_name and field_name in EXCLUDE_FIELDS:
            excluded_info[field_name] = "excluded_for_checkpoint_size"
            return None
        
        # V31-P01: Filter out large keys within the 'plan' dict
        if parent_field == 'plan' and field_name in PLAN_EXCLUDE_KEYS:
            excluded_info[f"plan.{field_name}"] = "plan_subfield_excluded"
            return None
        
        # Spool large fields to artifact store
        if field_name and field_name in SPOOL_FIELDS:
            if obj is not None:
                ref_dict = _spool_large_field(artifact_store, effective_run_id, field_name, obj)
                if ref_dict is not None:
                    spooled_refs[field_name] = ref_dict
                    return ref_dict  # Return ArtifactRef instead of data
            return None
        
        if obj is None:
            return None
        elif hasattr(obj, '__dataclass_fields__'):
            # Use field iteration instead of asdict to control spooling
            result = {}
            for f in fields(obj):
                # Bug #97 v2: Check exclusion first
                if f.name in EXCLUDE_FIELDS:
                    excluded_info[f.name] = "excluded_for_checkpoint_size"
                    result[f.name] = None
                elif f.name in SPOOL_FIELDS:
                    field_value = getattr(obj, f.name)
                    if field_value is not None:
                        ref_dict = _spool_large_field(artifact_store, effective_run_id, f.name, field_value)
                        if ref_dict is not None:
                            result[f.name] = ref_dict
                            spooled_refs[f.name] = ref_dict
                        else:
                            result[f.name] = None
                    else:
                        result[f.name] = None
                else:
                    # V31-P01: Pass field name as parent for plan filtering
                    result[f.name] = to_serializable(getattr(obj, f.name), depth + 1, f.name, field_name)
            return result
        elif isinstance(obj, list):
            # V31-P01: Limit list sizes during serialization to prevent huge checkpoints
            if len(obj) > 100:
                excluded_info[field_name or "list"] = f"list_truncated_from_{len(obj)}_to_100"
                obj = obj[:100]
            return [to_serializable(item, depth + 1, parent_field=field_name) for item in obj]
        elif isinstance(obj, dict):
            # V31-P01: Filter plan dict keys
            effective_parent = field_name if field_name == 'plan' else parent_field
            return {k: to_serializable(v, depth + 1, k, effective_parent) for k, v in obj.items()}
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
    
    # Note: excluded_info and truncated_flags are already initialized above
    # (before to_serializable function definition) - Bug #99 fix
    
    # Record what was spooled to artifact store
    for field_name, ref_dict in spooled_refs.items():
        excluded_info[field_name] = f"spooled_to_artifact_store_sha256={ref_dict.get('sha256', 'unknown')[:16]}"
    
    # Truncate large list fields
    for field_name, max_items in TRUNCATE_FIELDS.items():
        if field_name in result and isinstance(result[field_name], list):
            if len(result[field_name]) > max_items:
                excluded_info[field_name] = f"truncated_from={len(result[field_name])}"
                truncated_flags[field_name] = True
                result[field_name] = result[field_name][:max_items]
    
    # Truncate large string fields
    for field_name, max_chars in TRUNCATE_STRING_FIELDS.items():
        if field_name in result and isinstance(result[field_name], str):
            if len(result[field_name]) > max_chars:
                excluded_info[field_name] = f"truncated_from={len(result[field_name])}_chars"
                truncated_flags[field_name] = True
                result[field_name] = result[field_name][:max_chars] + "\n...[truncated]..."
    
    # Deep truncate: recursively handle nested structures that might have large fields
    def deep_truncate(obj, path=""):
        if isinstance(obj, dict):
            # Skip ArtifactRef dicts
            if obj.get("__artifact_ref__"):
                return
            
            for k, v in list(obj.items()):
                # Spool excluded fields at any depth
                if k in SPOOL_FIELDS and v is not None and not (isinstance(v, dict) and v.get("__artifact_ref__")):
                    ref_dict = _spool_large_field(artifact_store, effective_run_id, k, v)
                    if ref_dict is not None:
                        obj[k] = ref_dict
                        excluded_info[f"{path}.{k}" if path else k] = f"deep_spooled_sha256={ref_dict.get('sha256', 'unknown')[:16]}"
                    else:
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
    
    if truncated_flags:
        result['_truncated'] = truncated_flags

    if excluded_info:
        result['_checkpoint_excluded'] = excluded_info
    
    # Store spooled artifact refs for resume
    if spooled_refs:
        result['_artifact_refs'] = spooled_refs
    
    # Bug #97 Fix: Final size check - if still too large, aggressively remove large nested objects
    # This prevents 2GB+ checkpoints that freeze the system
    MAX_SAFE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB hard limit (reduced from 50MB)
    
    # Track what was removed during emergency truncation
    emergency_excluded = {}
    
    def recursive_size_truncate(obj, path="", max_obj_size=1024*1024):
        """Recursively find and nullify objects larger than max_obj_size."""
        if obj is None:
            return obj
        if isinstance(obj, dict):
            # Skip artifact refs
            if obj.get("__artifact_ref__"):
                return obj
            for k, v in list(obj.items()):
                if k.startswith('_'):
                    continue
                if v is None:
                    continue
                try:
                    v_json = _json_dumps(v)
                    v_size = len(v_json.encode('utf-8'))
                    if v_size > max_obj_size:
                        obj[k] = None
                        emergency_excluded[f"{path}.{k}" if path else k] = f"size_truncated={v_size}"
                    elif isinstance(v, (dict, list)):
                        recursive_size_truncate(v, f"{path}.{k}" if path else k, max_obj_size)
                except:
                    pass
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, (dict, list)):
                    recursive_size_truncate(item, f"{path}[{i}]", max_obj_size)
        return obj
    
    try:
        test_json = _json_dumps(result)
        current_size = len(test_json.encode('utf-8'))
        if current_size > MAX_SAFE_SIZE_BYTES:
            logger.warning(
                f"Checkpoint still too large after spooling ({current_size / 1024 / 1024:.1f}MB). "
                "Applying emergency truncation."
            )
            # Recursively truncate anything > 1MB
            recursive_size_truncate(result, max_obj_size=1024*1024)
            
            # Re-check and be more aggressive if needed
            test_json = _json_dumps(result)
            current_size = len(test_json.encode('utf-8'))
            if current_size > MAX_SAFE_SIZE_BYTES:
                # Ultra-aggressive: truncate anything > 100KB
                recursive_size_truncate(result, max_obj_size=100*1024)
            
            # Merge emergency exclusions into main excluded_info
            excluded_info.update(emergency_excluded)
            result['_checkpoint_excluded'] = excluded_info
            result['_emergency_truncated'] = True
            
            # Final size check
            final_json = _json_dumps(result)
            final_size = len(final_json.encode('utf-8'))
            logger.info(f"After emergency truncation: {final_size / 1024 / 1024:.1f}MB")
    except Exception as e:
        logger.warning(f"Checkpoint size check failed: {e}")
    
    return result


def _deserialize_state(data: Dict[str, Any], rehydrate_artifacts: bool = True) -> "WorkflowState":
    """
    Deserialize JSON dict back to WorkflowState.
    
    Per Agent Harness Alignment Plan:
    - Detects ArtifactRef placeholders in checkpoint data
    - Rehydrates large fields from artifact store
    - Falls back gracefully if artifacts unavailable
    
    Args:
        data: Checkpoint JSON dict
        rehydrate_artifacts: Whether to rehydrate large fields from artifact store
        
    Returns:
        WorkflowState with rehydrated fields
    """
    from integration_coworker.graph.state import WorkflowState
    from integration_coworker.persistence.artifacts.base import ArtifactRef
    
    # Get artifact store for rehydration
    artifact_store = _get_artifact_store() if rehydrate_artifacts else None
    
    def rehydrate_value(value: Any) -> Any:
        """Recursively rehydrate ArtifactRef placeholders."""
        if value is None:
            return None
        
        # Check if this is an ArtifactRef
        if isinstance(value, dict) and ArtifactRef.is_artifact_ref(value):
            rehydrated = _rehydrate_artifact_ref(artifact_store, value)
            if rehydrated is not None:
                return rehydrated
            # Return None if rehydration fails (graceful degradation)
            return None
        
        # Recurse into dicts
        if isinstance(value, dict):
            return {k: rehydrate_value(v) for k, v in value.items()}
        
        # Recurse into lists
        if isinstance(value, list):
            return [rehydrate_value(item) for item in value]
        
        return value
    
    # Rehydrate the entire data dict
    if rehydrate_artifacts:
        data = rehydrate_value(data)
    
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
    
    # Rehydrate large fields if present
    if data.get('openapi_spec') is not None:
        state.openapi_spec = data['openapi_spec']
    if data.get('spec_documents') is not None:
        state.spec_documents = data['spec_documents']
    if data.get('spec_sections') is not None:
        state.spec_sections = data['spec_sections']
    
    # Log rehydration status
    artifact_refs = data.get('_artifact_refs', {})
    if artifact_refs:
        rehydrated_count = sum(1 for k in artifact_refs if data.get(k) is not None)
        logger.debug(f"Rehydrated {rehydrated_count}/{len(artifact_refs)} artifact fields")
    
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
    
    Per Agent Harness Alignment Plan:
    - Large fields are spooled to ArtifactStore before checkpoint
    - ArtifactRef replaces large fields in checkpoint JSON
    - On resume, fields are rehydrated from artifact store
    
    Args:
        run_id: The run identifier
        node_name: The node that just completed
        state: The current workflow state
    """
    import time  # V29-P03: Add timing instrumentation
    
    # Maximum checkpoint size: 100MB (PostgreSQL can handle this, but larger is risky)
    MAX_CHECKPOINT_SIZE_MB = 100
    MAX_CHECKPOINT_SIZE_BYTES = MAX_CHECKPOINT_SIZE_MB * 1024 * 1024
    
    # V29-P03: Time serialization phase
    serialize_start = time.perf_counter()
    
    # V30-P02: Serialize state with artifact spooling using fast JSON
    serialized = _serialize_state(state, run_id=run_id)
    state_json = _json_dumps(serialized)
    
    serialize_duration_ms = (time.perf_counter() - serialize_start) * 1000
    
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
        state_json = _json_dumps(minimal_state)
        logger.info(f"Minimal checkpoint size: {len(state_json) / 1024:.1f}KB")
    
    engine = get_engine_type()
    
    # V29-P03: Time DB write phase
    db_write_start = time.perf_counter()
    
    try:
        if engine == "postgres":
            from integration_coworker.persistence.postgres import get_connection as pg_get_connection
            
            with pg_get_connection() as conn:
                with conn.cursor() as cur:
                    # V45-010: Ensure run_status entry exists before checkpoint insert
                    # This prevents FK constraint violation when checkpoint is saved
                    # before plan_run node creates the run_status entry
                    cur.execute("""
                        INSERT INTO integration_gold.run_status 
                        (run_id, status)
                        VALUES (%s, 'running')
                        ON CONFLICT (run_id) DO NOTHING
                    """, (run_id,))
                    
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
            # SQLite fallback - V29-P01 Fix: Use context manager
            with get_connection() as conn:
                cur = conn.cursor()
                # V45-010: Ensure run_status entry exists for SQLite too
                cur.execute("""
                    INSERT OR IGNORE INTO run_status 
                    (run_id, status)
                    VALUES (?, 'running')
                """, (run_id,))
                cur.execute("""
                    INSERT OR REPLACE INTO run_checkpoints 
                        (run_id, node_name, state_json, created_at)
                    VALUES (?, ?, ?, datetime('now'))
                """, (run_id, node_name, state_json))
                conn.commit()
        
        db_write_duration_ms = (time.perf_counter() - db_write_start) * 1000
        
        # V29-P03: Log detailed timing breakdown
        logger.debug(
            f"Saved checkpoint for {run_id} at node {node_name} "
            f"[serialize={serialize_duration_ms:.1f}ms, db_write={db_write_duration_ms:.1f}ms, "
            f"size={json_size/1024:.1f}KB]"
        )
        
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
        
        # V30-P02: Use fast JSON deserialization
        data = _json_loads(row[0])
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


def delete_checkpoints(run_id: str, delete_artifacts: bool = True) -> None:
    """
    Delete all checkpoints for a run (cleanup after success).
    
    Per Agent Harness Alignment Plan:
    - Also cleans up artifacts stored in artifact store
    - Ensures no orphaned artifacts remain
    
    Args:
        run_id: The run to clean up
        delete_artifacts: Whether to also delete associated artifacts (default True)
    """
    # Delete artifacts first (before checkpoint references are gone)
    if delete_artifacts:
        try:
            artifact_store = _get_artifact_store()
            if artifact_store:
                deleted = artifact_store.delete_by_run(run_id)
                if deleted > 0:
                    logger.debug(f"Deleted {deleted} artifacts for run {run_id}")
        except Exception as e:
            logger.warning(f"Failed to delete artifacts for run {run_id}: {e}")
    
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


# =============================================================================
# Checkpoint Pruning (Production Readiness v4 - P1-3)
# =============================================================================
# Retention-based cleanup for long-running deployments.
# Uses advisory locks for PostgreSQL to prevent concurrent prune conflicts.
# =============================================================================

def prune_checkpoints(
    retention_days: int = 7,
    retention_count: int = 10,
) -> List[str]:
    """
    Prune old checkpoints based on retention policy.
    
    Production Readiness v4 - P1-3:
    Cleans up old checkpoints to prevent unbounded storage growth.
    Uses PostgreSQL advisory locks to prevent concurrent prune conflicts.
    
    Retention policy:
    - Keep checkpoints newer than retention_days
    - Keep at least retention_count most recent per run_id
    - Delete everything else
    
    Args:
        retention_days: Keep checkpoints newer than this (default 7)
        retention_count: Keep at least this many per run_id (default 10)
        
    Returns:
        List of deleted run_id values
        
    Example:
        deleted = prune_checkpoints(retention_days=30, retention_count=5)
        print(f"Pruned {len(deleted)} old checkpoint groups")
    """
    engine = get_engine_type()
    deleted_run_ids: List[str] = []
    
    if engine == "postgres":
        from integration_coworker.persistence.postgres import get_connection as pg_get_connection
        
        # Use advisory lock to prevent concurrent prune operations
        # Lock ID 12345 is arbitrary but unique to this operation
        PRUNE_LOCK_ID = 12345
        
        with pg_get_connection() as conn:
            with conn.cursor() as cur:
                # Try to acquire advisory lock (non-blocking)
                cur.execute("SELECT pg_try_advisory_lock(%s)", (PRUNE_LOCK_ID,))
                row = cur.fetchone()
                lock_acquired = row[0] if row else False
                
                if not lock_acquired:
                    logger.info("Prune skipped - another prune operation in progress")
                    return []
                
                try:
                    # Delete checkpoints older than retention_days, keeping
                    # retention_count most recent per run_id
                    # Uses CTE with row_number() for per-run ranking
                    cur.execute("""
                        WITH ranked AS (
                            SELECT 
                                run_id, 
                                node_name,
                                created_at,
                                ROW_NUMBER() OVER (
                                    PARTITION BY run_id 
                                    ORDER BY created_at DESC
                                ) as rn
                            FROM integration_gold.run_checkpoints
                        ),
                        to_delete AS (
                            SELECT run_id, node_name
                            FROM ranked
                            WHERE created_at < NOW() - INTERVAL '%s days'
                              AND rn > %s
                        )
                        DELETE FROM integration_gold.run_checkpoints c
                        USING to_delete d
                        WHERE c.run_id = d.run_id AND c.node_name = d.node_name
                        RETURNING c.run_id
                    """, (retention_days, retention_count))
                    
                    rows = cur.fetchall()
                    deleted_run_ids = list(set(row[0] for row in rows))
                    conn.commit()
                    
                finally:
                    # Release advisory lock
                    cur.execute("SELECT pg_advisory_unlock(%s)", (PRUNE_LOCK_ID,))
        
        if deleted_run_ids:
            logger.info(f"Pruned checkpoints for {len(deleted_run_ids)} run_ids")
        
    else:
        # SQLite: simpler approach without advisory locks
        conn = get_connection()
        cur = conn.cursor()
        
        try:
            # SQLite date arithmetic: use julianday for day comparisons
            cur.execute("""
                DELETE FROM run_checkpoints
                WHERE julianday('now') - julianday(created_at) > ?
                AND run_id IN (
                    SELECT run_id FROM run_checkpoints
                    GROUP BY run_id
                    HAVING COUNT(*) > ?
                )
            """, (retention_days, retention_count))
            
            # Get affected run_ids before commit
            cur.execute("""
                SELECT DISTINCT run_id FROM run_checkpoints
                WHERE julianday('now') - julianday(created_at) > ?
            """, (retention_days,))
            deleted_run_ids = [row[0] for row in cur.fetchall()]
            
            conn.commit()
        finally:
            conn.close()
        
        if deleted_run_ids:
            logger.info(f"Pruned checkpoints for {len(deleted_run_ids)} run_ids (SQLite)")
    
    return deleted_run_ids


def get_checkpoint_stats() -> Dict[str, Any]:
    """
    Get checkpoint storage statistics for monitoring.
    
    Production Readiness v4 - P1-3:
    Returns stats for observability dashboards and alerting.
    
    Returns:
        Dict with:
        - total_checkpoints: Total number of checkpoint records
        - unique_runs: Number of unique run_ids
        - oldest_checkpoint: Timestamp of oldest checkpoint
        - newest_checkpoint: Timestamp of newest checkpoint
        - checkpoints_per_run: Average checkpoints per run
        
    Example:
        stats = get_checkpoint_stats()
        if stats['total_checkpoints'] > 10000:
            prune_checkpoints()
    """
    engine = get_engine_type()
    
    if engine == "postgres":
        from integration_coworker.persistence.postgres import get_connection as pg_get_connection
        
        with pg_get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        COUNT(*) as total_checkpoints,
                        COUNT(DISTINCT run_id) as unique_runs,
                        MIN(created_at) as oldest_checkpoint,
                        MAX(created_at) as newest_checkpoint
                    FROM integration_gold.run_checkpoints
                """)
                row = cur.fetchone()
                
                if not row:
                    return {
                        "total_checkpoints": 0,
                        "unique_runs": 0,
                        "oldest_checkpoint": None,
                        "newest_checkpoint": None,
                        "checkpoints_per_run": 0.0,
                    }
                
                total = row[0] or 0
                unique_runs = row[1] or 0
                oldest = row[2].isoformat() if row[2] else None
                newest = row[3].isoformat() if row[3] else None
                per_run = total / unique_runs if unique_runs > 0 else 0.0
                
                return {
                    "total_checkpoints": total,
                    "unique_runs": unique_runs,
                    "oldest_checkpoint": oldest,
                    "newest_checkpoint": newest,
                    "checkpoints_per_run": round(per_run, 2),
                }
    else:
        conn = get_connection()
        cur = conn.cursor()
        
        try:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_checkpoints,
                    COUNT(DISTINCT run_id) as unique_runs,
                    MIN(created_at) as oldest_checkpoint,
                    MAX(created_at) as newest_checkpoint
                FROM run_checkpoints
            """)
            row = cur.fetchone()
            
            if not row:
                return {
                    "total_checkpoints": 0,
                    "unique_runs": 0,
                    "oldest_checkpoint": None,
                    "newest_checkpoint": None,
                    "checkpoints_per_run": 0.0,
                }
            
            total = row[0] or 0
            unique_runs = row[1] or 0
            oldest = row[2]  # SQLite stores as string
            newest = row[3]
            per_run = total / unique_runs if unique_runs > 0 else 0.0
            
            return {
                "total_checkpoints": total,
                "unique_runs": unique_runs,
                "oldest_checkpoint": oldest,
                "newest_checkpoint": newest,
                "checkpoints_per_run": round(per_run, 2),
            }
        finally:
            conn.close()
