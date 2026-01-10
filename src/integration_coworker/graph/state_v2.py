"""
WorkflowState V2 - LangGraph-compatible state with parallel execution support.

This module provides a TypedDict-based state that supports LangGraph's parallel
node execution by using Annotated types with reducer functions.

ARCHITECTURE (Option 3 - Single Source of Truth):
    
    WorkflowState (state.py)     →  state_schema.py  →  WorkflowStateDict (here)
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━     ━━━━━━━━━━━━━━━━     ━━━━━━━━━━━━━━━━━━━━━━━━
    Authoritative dataclass         Generates              TypedDict for LangGraph
    - All field definitions         reducers from          parallel execution
    - Type annotations              field names            - Auto-synced fields
    - Default values                                       - Correct reducers

This eliminates the dual-maintenance problem that caused bugs V24-007, etc.

Reducer types (from state_schema.py):
- last_non_none: Take last non-None value (SAFE DEFAULT for all fields)
- unique_list: Combine lists without duplicates (for completed_steps, etc.)
- merge_dicts: Merge dicts with right precedence (for plan, timings)
- sum_token_usage: Sum token counts

CRITICAL: We NEVER use operator.add, which caused the Dec 22 checkpoint bloat.
"""

from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, Annotated, Dict, List, Optional, TypedDict, TYPE_CHECKING

# Import reducers and generator from state_schema
from integration_coworker.graph.state_schema import (
    # Reducers
    merge_dicts,
    last_non_none,
    unique_list,
    sum_token_usage,
    # Generator functions
    generate_state_dict_annotations,
    get_all_field_names,
    verify_field_parity,
    verify_no_operator_add,
)

# Import domain models for type hints
from integration_coworker.domain.models import (
    SourceSystem,
    SourceRef,
    SpecDocument,
    SpecSection,
    Endpoint,
    EndpointParameter,
    Schema,
    SchemaField,
    Entity,
    EntityRelationship,
    Event,
    IntegrationTask,
    IntegrationFlowNode,
    IntegrationFlowEdge,
    EndpointBinding,
    Policy,
    CodeArtifact,
    SpecChunkEmbedding,
    WorkflowTemplate,
    FileSpec,
    FileField,
    RecordLayout,
    FileValidationRule,
)
from integration_coworker.domain.ir import Operation
from integration_coworker.repo.models import RepoProfile, RepoChangeSet, RepoSnapshot
from integration_coworker.api.types import IntegrationOptions
from integration_coworker.sources.base import ParsedSpec

if TYPE_CHECKING:  # pragma: no cover
    from integration_coworker.graph.state import WorkflowState


# =============================================================================
# WorkflowStateDict - Generated from WorkflowState dataclass
# =============================================================================
#
# This TypedDict is AUTO-GENERATED from state.py via state_schema.py.
# The annotations below are kept for IDE support and type checking, but
# the reducer assignments are DERIVED from the schema generator.
#
# To add a new field:
# 1. Add it to WorkflowState in state.py
# 2. If it needs a special reducer, add it to the appropriate set in state_schema.py
# 3. Add it here for IDE support (the generator ensures parity)
#
# =============================================================================

class WorkflowStateDict(TypedDict, total=False):
    """
    LangGraph-compatible workflow state with parallel execution support.
    
    This TypedDict is kept in sync with WorkflowState (state.py) via the
    state_schema.py generator. All fields use safe reducers (no operator.add).
    """
    
    # Inputs
    source_refs: Annotated[List[SourceRef], last_non_none]
    spec_refs: Annotated[List[str], last_non_none]
    task_description: Annotated[str, last_non_none]
    provider_code: Annotated[Optional[str], last_non_none]
    options: Annotated[Optional[IntegrationOptions], last_non_none]
    
    # Spec discovery (Slice 1: Auto-discovery from task description)
    discovered_specs: Annotated[Optional[Dict[str, Any]], last_non_none]
    discovery_confidence: Annotated[float, last_non_none]
    discovery_source: Annotated[Optional[str], last_non_none]
    
    # Bronze-level spec content
    spec_documents: Annotated[List[SpecDocument], last_non_none]
    spec_sections: Annotated[List[SpecSection], last_non_none]
    doc_chunks: Annotated[List[str], last_non_none]
    openapi_spec: Annotated[Optional[Dict[str, Any]], last_non_none]
    
    # Silver drafts
    source_system: Annotated[Optional[SourceSystem], last_non_none]
    endpoints: Annotated[List[Endpoint], last_non_none]
    endpoint_parameters: Annotated[List[EndpointParameter], last_non_none]
    schemas: Annotated[List[Schema], last_non_none]
    schema_fields: Annotated[List[SchemaField], last_non_none]
    entities: Annotated[List[Entity], last_non_none]
    relationships: Annotated[List[EntityRelationship], last_non_none]
    events: Annotated[List[Event], last_non_none]

    # Protocol vNext: Protocol-agnostic operations
    operations: Annotated[List[Operation], last_non_none]

    # File Integration V1 (Silver layer)
    file_specs: Annotated[List[FileSpec], last_non_none]
    file_fields: Annotated[List[FileField], last_non_none]
    record_layouts: Annotated[List[RecordLayout], last_non_none]
    file_validation_rules: Annotated[List[FileValidationRule], last_non_none]
    
    # Embeddings - uses last_non_none (NOT operator.add!)
    # The embedding node sets this once, parallel branches don't modify it
    spec_chunk_embeddings: Annotated[List[SpecChunkEmbedding], last_non_none]
    
    # V3 Streaming Persistence Fields
    # Uses last_non_none (NOT operator.add!) to prevent checkpoint bloat
    spec_chunk_ids: Annotated[List[int], last_non_none]
    chunk_count: Annotated[int, last_non_none]
    embedding_count: Annotated[int, last_non_none]
    
    # Primary spec document ID for FK propagation
    primary_spec_document_id: Annotated[Optional[int], last_non_none]
    
    # V38-002: API base URL extracted from OpenAPI spec (survives state_gc)
    api_base_url: Annotated[Optional[str], last_non_none]
    
    # Warnings - uses unique_list to dedupe
    warnings: Annotated[List[str], unique_list]
    
    # Gold drafts
    workflow_template: Annotated[Optional[WorkflowTemplate], last_non_none]
    integration_task: Annotated[Optional[IntegrationTask], last_non_none]
    workflow_nodes: Annotated[List[IntegrationFlowNode], last_non_none]
    workflow_edges: Annotated[List[IntegrationFlowEdge], last_non_none]
    endpoint_bindings: Annotated[List[EndpointBinding], last_non_none]
    policies: Annotated[List[Policy], last_non_none]
    code_artifacts: Annotated[List[CodeArtifact], last_non_none]
    
    # Repo integration
    repo_root: Annotated[Optional[Path], last_non_none]
    repo_profile: Annotated[Optional[RepoProfile], last_non_none]
    repo_snapshot: Annotated[Optional[RepoSnapshot], last_non_none]
    repo_changes: Annotated[Optional[RepoChangeSet], last_non_none]
    repo_markdown_context: Annotated[Optional[str], last_non_none]
    
    # Control / bookkeeping - uses appropriate reducers
    plan: Annotated[Dict[str, Any], merge_dicts]
    completed_steps: Annotated[List[str], unique_list]
    errors: Annotated[List[str], unique_list]
    persisted_ids: Annotated[Dict[str, Any], merge_dicts]
    
    # Multi-spec support
    pending_specs: Annotated[List[Dict[str, Any]], last_non_none]
    parsed_specs: Annotated[List[ParsedSpec], last_non_none]
    
    # Degraded mode tracking
    degraded_mode: Annotated[bool, last_non_none]
    degraded_reason: Annotated[Optional[str], last_non_none]
    
    # Skip tracking - uses unique_list
    skipped_nodes: Annotated[List[str], unique_list]
    
    # LLM fallback tracking - uses unique_list to dedupe from parallel branches
    llm_fallbacks: Annotated[List[Dict[str, Any]], unique_list]
    
    # Node timing tracking - merge from both branches
    node_timings: Annotated[Dict[str, float], merge_dicts]
    
    # Sandbox validation results
    sandbox_result: Annotated[Optional[Dict[str, Any]], last_non_none]
    
    # ==========================================================================
    # HITL Review Artifacts (ADR-HITL-ENHANCEMENT-v2)
    # ==========================================================================
    # Stores ArtifactRefs (not blobs) pointing to large review payloads.
    # Uses last_non_none - set once during interrupt(), not modified by parallel branches.
    review_artifact_refs: Annotated[Dict[str, Dict[str, Any]], last_non_none]
    
    # Kind of review gate that triggered interrupt: "pre_write" | "post_sandbox" | None
    pending_review_kind: Annotated[Optional[str], last_non_none]
    
    # Human feedback from resume (free-form text for regeneration guidance)
    human_feedback: Annotated[Optional[str], last_non_none]
    
    # Review decisions - records decisions from each review gate
    # Uses merge_dicts to combine decisions from different gates
    review_decisions: Annotated[Dict[str, Dict[str, Any]], merge_dicts]
    
    # PR #7: Quality pipeline refs (refs-not-blobs pattern)
    # Contains artifact refs + bounded summaries for static analysis, attribution, etc.
    quality_refs: Annotated[Dict[str, Dict[str, Any]], merge_dicts]
    
    # PR #7: Iteration state for regeneration budget tracking
    iteration_state: Annotated[Optional[Dict[str, Any]], last_non_none]
    
    # PR #10: Regeneration constraints artifact ref (refs-not-blobs pattern)
    # Contains ref to RegenerationConstraints artifact + bounded summary
    regeneration_constraints_ref: Annotated[Optional[Dict[str, Any]], last_non_none]
    
    # PR #7/8: Static analysis tracking
    static_analysis_retries: Annotated[int, last_non_none]
    static_analysis_escalated: Annotated[bool, last_non_none]
    needs_static_recheck: Annotated[bool, last_non_none]
    
    # PR #9: Sandbox failure attribution summary (bounded)
    sandbox_attribution_summary: Annotated[Optional[Dict[str, Any]], last_non_none]
    
    # Token usage tracking - sum from both branches
    llm_token_usage: Annotated[Dict[str, int], sum_token_usage]
    
    # Spec caching
    cache_hit: Annotated[bool, last_non_none]
    
    # Outputs
    report_markdown: Annotated[Optional[str], last_non_none]
    run_id: Annotated[Optional[str], last_non_none]


# =============================================================================
# Runtime Parity Verification
# =============================================================================

def _verify_at_import():
    """
    Verify field parity at import time.
    
    This catches field sync issues immediately rather than at runtime.
    Raises ImportError if parity is broken.
    """
    is_valid, issues = verify_field_parity(WorkflowStateDict)
    if not is_valid:
        raise ImportError(
            f"WorkflowStateDict is out of sync with WorkflowState!\n"
            f"Issues: {issues}\n"
            f"Fix: Add missing fields to WorkflowStateDict in state_v2.py"
        )
    
    is_valid, issues = verify_no_operator_add(WorkflowStateDict)
    if not is_valid:
        raise ImportError(
            f"WorkflowStateDict uses operator.add which causes checkpoint bloat!\n"
            f"Issues: {issues}\n"
            f"Fix: Change to last_non_none or unique_list"
        )


# Run verification at import time
_verify_at_import()


# =============================================================================
# Conversion Functions
# =============================================================================

# Fields to exclude from dict representation for tracing/checkpointing
_LARGE_FIELDS_TO_EXCLUDE_FROM_TRACING = {
    "openapi_spec",       # Raw OpenAPI dict (7MB+ for Stripe)
    "raw_spec_content",   # Raw spec text/bytes
    "spec_sections",      # Parsed sections from spec
}

# Max string length for traced fields
_MAX_TRACED_STRING_LENGTH = 50_000  # 50KB per string field


def dataclass_to_dict(
    state: "WorkflowState",
    exclude_large_fields: bool = True,
) -> WorkflowStateDict:
    """
    Convert dataclass WorkflowState to TypedDict for LangGraph parallel execution.
    
    Args:
        state: Dataclass-based WorkflowState
        exclude_large_fields: If True, excludes large fields like openapi_spec
            to prevent LangSmith payload size issues. Default True.
        
    Returns:
        WorkflowStateDict compatible with parallel LangGraph execution
    """
    # Required fields that must ALWAYS be included (no defaults in dataclass)
    # LangGraph's StateGraph uses the dataclass schema and will fail to construct
    # WorkflowState if these are missing.
    REQUIRED_FIELDS = frozenset({"source_refs", "spec_refs", "task_description"})
    
    # Construct a "default" instance for comparison
    default_state = None
    try:
        default_state = state.__class__(source_refs=[], spec_refs=[], task_description="")
    except Exception:
        default_state = None

    def _is_default_value(field_name: str, value: Any) -> bool:
        if default_state is None:
            return False
        try:
            return getattr(default_state, field_name) == value
        except Exception:
            return False

    def _truncate_for_tracing(value: Any) -> Any:
        """Truncate large strings for tracing."""
        if isinstance(value, str) and len(value) > _MAX_TRACED_STRING_LENGTH:
            return value[:_MAX_TRACED_STRING_LENGTH] + f"... [truncated, {len(value)} chars total]"
        return value

    result: WorkflowStateDict = {}
    for f in dataclass_fields(state):
        value = getattr(state, f.name)

        # Never skip required fields - LangGraph needs them to construct state
        # Skip other default values to reduce parallel update conflicts
        if f.name not in REQUIRED_FIELDS and _is_default_value(f.name, value):
            continue

        # Exclude large fields from tracing
        if exclude_large_fields and f.name in _LARGE_FIELDS_TO_EXCLUDE_FROM_TRACING:
            if value is not None:
                size_hint = ""
                if isinstance(value, str):
                    size_hint = f" ({len(value)} chars)"
                elif isinstance(value, (dict, list)):
                    size_hint = f" ({len(value)} items)"
                result[f.name] = f"<excluded from trace{size_hint}>"
            continue

        # Convert Path to string for JSON serialization
        if isinstance(value, Path):
            result[f.name] = str(value)
        else:
            result[f.name] = _truncate_for_tracing(value)

    return result


def dict_to_dataclass(state_dict: WorkflowStateDict) -> "WorkflowState":
    """
    Convert TypedDict back to dataclass WorkflowState.
    
    Args:
        state_dict: WorkflowStateDict from LangGraph execution
        
    Returns:
        Dataclass-based WorkflowState for node functions
    """
    from integration_coworker.graph.state import WorkflowState
    
    # Get valid field names from dataclass
    valid_field_names = {f.name for f in dataclass_fields(WorkflowState)}
    
    # Filter to only valid fields and handle special conversions
    filtered = {}
    for k, v in state_dict.items():
        if k in valid_field_names:
            # Convert string back to Path if needed
            if k == 'repo_root' and v is not None and not isinstance(v, Path):
                filtered[k] = Path(v)
            else:
                filtered[k] = v
    
    # Ensure required fields have defaults
    defaults_for_required = {
        'source_refs': [],
        'spec_refs': [],
        'task_description': '',
    }
    for field_name, default_value in defaults_for_required.items():
        if field_name not in filtered:
            filtered[field_name] = default_value
    
    return WorkflowState(**filtered)


# =============================================================================
# State Factory for Parallel Graph
# =============================================================================

def create_initial_state_dict(
    source_refs: List[SourceRef],
    spec_refs: List[str],
    task_description: str,
    provider_code: Optional[str] = None,
    options: Optional[IntegrationOptions] = None,
    repo_root: Optional[Path] = None,
    run_id: Optional[str] = None,
) -> WorkflowStateDict:
    """
    Create initial state dictionary for parallel graph execution.
    
    Args:
        source_refs: API source references
        spec_refs: Raw spec paths/URLs
        task_description: Task to accomplish
        provider_code: Provider identifier
        options: Integration options
        repo_root: Target repository path
        run_id: Unique run identifier
        
    Returns:
        WorkflowStateDict ready for LangGraph parallel execution
    """
    return {
        # Inputs
        "source_refs": source_refs,
        "spec_refs": spec_refs,
        "task_description": task_description,
        "provider_code": provider_code,
        "options": options,
        
        # Bronze-level spec content
        "spec_documents": [],
        "spec_sections": [],
        "doc_chunks": [],
        "openapi_spec": None,
        
        # Silver drafts
        "source_system": None,
        "endpoints": [],
        "endpoint_parameters": [],
        "schemas": [],
        "schema_fields": [],
        "entities": [],
        "relationships": [],
        "events": [],
        
        # Protocol vNext
        "operations": [],
        
        # File Integration V1
        "file_specs": [],
        "file_fields": [],
        "record_layouts": [],
        "file_validation_rules": [],
        
        # Embeddings
        "spec_chunk_embeddings": [],
        "spec_chunk_ids": [],
        "chunk_count": 0,
        "embedding_count": 0,
        
        # Primary spec document ID
        "primary_spec_document_id": None,
        
        # V38-002: API base URL extracted from OpenAPI spec
        "api_base_url": None,
        
        # Warnings
        "warnings": [],
        
        # Gold drafts
        "workflow_template": None,
        "integration_task": None,
        "workflow_nodes": [],
        "workflow_edges": [],
        "endpoint_bindings": [],
        "policies": [],
        "code_artifacts": [],
        
        # Repo integration
        "repo_root": str(repo_root) if repo_root else None,
        "repo_profile": None,
        "repo_snapshot": None,
        "repo_changes": None,
        "repo_markdown_context": None,
        
        # Control / bookkeeping
        "plan": {},
        "completed_steps": [],
        "errors": [],
        "persisted_ids": {},
        
        # Multi-spec support
        "pending_specs": [],
        "parsed_specs": [],
        
        # Degraded mode tracking
        "degraded_mode": False,
        "degraded_reason": None,
        
        # Skip tracking
        "skipped_nodes": [],
        
        # LLM fallback tracking
        "llm_fallbacks": [],
        
        # Node timing tracking
        "node_timings": {},
        
        # Sandbox validation
        "sandbox_result": None,
        
        # Token usage tracking
        "llm_token_usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        
        # Spec caching
        "cache_hit": False,
        
        # Outputs
        "report_markdown": None,
        "run_id": run_id,
    }
