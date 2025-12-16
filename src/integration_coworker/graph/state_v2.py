"""
WorkflowState V2 - LangGraph-compatible state with parallel execution support.

This module provides a TypedDict-based state that supports LangGraph's parallel
node execution by using Annotated types with reducer functions.

Problem:
    When multiple nodes run in parallel and both update the same state field,
    LangGraph throws InvalidUpdateError because it doesn't know how to merge
    the values.

Solution:
    Use typing.Annotated with reducer functions that tell LangGraph how to
    combine values from parallel branches:
    - `operator.add` for lists: concatenates lists from both branches
    - Custom reducers for dicts: merge with priority rules
    - `last_value` (default) for scalars: takes the most recent value

Usage:
    # Old (breaks with parallel):
    source_refs: List[SourceRef]
    
    # New (works with parallel):
    source_refs: Annotated[List[SourceRef], operator.add]

For parallel execution, the sync node receives merged state automatically.
"""

import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Annotated, Dict, List, Optional, Sequence, TypedDict

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
)
from integration_coworker.repo.models import RepoProfile, RepoChangeSet, RepoSnapshot
from integration_coworker.api.types import IntegrationOptions


# =============================================================================
# Custom Reducers for Parallel Execution
# =============================================================================

def merge_dicts(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge two dictionaries, with right taking precedence.
    
    Used for fields like `plan`, `persisted_ids`, `node_timings`.
    """
    if left is None:
        return right or {}
    if right is None:
        return left or {}
    result = dict(left)
    result.update(right)
    return result


def last_non_none(left: Any, right: Any) -> Any:
    """
    Take the last non-None value.
    
    Used for scalar fields that should not be combined.
    """
    return right if right is not None else left


def unique_list(left: List[str], right: List[str]) -> List[str]:
    """
    Combine two lists, removing duplicates while preserving order.
    
    Used for `completed_steps`, `errors`, `warnings`.
    """
    if left is None:
        left = []
    if right is None:
        right = []
    seen = set()
    result = []
    for item in left + right:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def sum_token_usage(left: Dict[str, int], right: Dict[str, int]) -> Dict[str, int]:
    """Sum token usage dictionaries."""
    if left is None:
        left = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if right is None:
        right = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": left.get("prompt_tokens", 0) + right.get("prompt_tokens", 0),
        "completion_tokens": left.get("completion_tokens", 0) + right.get("completion_tokens", 0),
        "total_tokens": left.get("total_tokens", 0) + right.get("total_tokens", 0),
    }


# =============================================================================
# WorkflowState TypedDict with Annotated Reducers
# =============================================================================

class WorkflowStateDict(TypedDict, total=False):
    """
    LangGraph-compatible workflow state with parallel execution support.
    
    Fields are annotated with reducer functions that tell LangGraph how to
    merge values when multiple nodes run in parallel.
    
    Reducer types:
    - operator.add: Concatenate lists (for append-only fields)
    - unique_list: Combine lists without duplicates (for completed_steps, etc.)
    - merge_dicts: Merge dicts with right precedence (for plan, timings)
    - last_non_none: Take last non-None value (for scalars)
    - sum_token_usage: Sum token counts
    
    Note: TypedDict with Annotated is the LangGraph-native way to handle
    parallel state updates. This replaces the dataclass-based WorkflowState
    for parallel graph execution.
    """
    
    # Inputs - these don't change during execution, so last_non_none is fine
    source_refs: Annotated[List[SourceRef], last_non_none]
    spec_refs: Annotated[List[str], last_non_none]
    task_description: Annotated[str, last_non_none]
    provider_code: Annotated[Optional[str], last_non_none]
    options: Annotated[Optional[IntegrationOptions], last_non_none]
    
    # Bronze-level spec content - populated sequentially before parallel split
    spec_documents: Annotated[List[SpecDocument], last_non_none]
    spec_sections: Annotated[List[SpecSection], last_non_none]
    doc_chunks: Annotated[List[str], last_non_none]
    openapi_spec: Annotated[Optional[Dict[str, Any]], last_non_none]
    
    # Silver drafts - populated sequentially before parallel split
    source_system: Annotated[Optional[SourceSystem], last_non_none]
    endpoints: Annotated[List[Endpoint], last_non_none]
    endpoint_parameters: Annotated[List[EndpointParameter], last_non_none]
    schemas: Annotated[List[Schema], last_non_none]
    schema_fields: Annotated[List[SchemaField], last_non_none]
    entities: Annotated[List[Entity], last_non_none]
    relationships: Annotated[List[EntityRelationship], last_non_none]
    events: Annotated[List[Event], last_non_none]
    
    # Embeddings - populated by embed_spec_chunks (parallel branch)
    spec_chunk_embeddings: Annotated[List[SpecChunkEmbedding], operator.add]
    
    # V3 Streaming Persistence Fields
    spec_chunk_ids: Annotated[List[int], operator.add]
    chunk_count: Annotated[int, last_non_none]
    embedding_count: Annotated[int, last_non_none]
    
    # Warnings - can be added by either branch
    warnings: Annotated[List[str], unique_list]
    
    # Gold drafts - populated by understand_task (parallel branch) and later nodes
    workflow_template: Annotated[Optional[WorkflowTemplate], last_non_none]
    integration_task: Annotated[Optional[IntegrationTask], last_non_none]  # From understand_task
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
    
    # Control / bookkeeping - can be updated by both branches
    plan: Annotated[Dict[str, Any], merge_dicts]
    completed_steps: Annotated[List[str], unique_list]
    errors: Annotated[List[str], unique_list]
    persisted_ids: Annotated[Dict[str, Any], merge_dicts]
    
    # Multi-spec support
    pending_specs: Annotated[List[Dict[str, Any]], last_non_none]
    parsed_specs: Annotated[List[Dict[str, Any]], last_non_none]
    
    # Degraded mode tracking
    degraded_mode: Annotated[bool, last_non_none]
    degraded_reason: Annotated[Optional[str], last_non_none]
    
    # Skip tracking
    skipped_nodes: Annotated[List[str], unique_list]
    
    # LLM fallback tracking
    llm_fallbacks: Annotated[List[Dict[str, Any]], operator.add]
    
    # Node timing tracking - merge from both branches
    node_timings: Annotated[Dict[str, float], merge_dicts]
    
    # Token usage tracking - sum from both branches
    llm_token_usage: Annotated[Dict[str, int], sum_token_usage]
    
    # Spec caching
    cache_hit: Annotated[bool, last_non_none]
    
    # Outputs
    report_markdown: Annotated[Optional[str], last_non_none]
    run_id: Annotated[Optional[str], last_non_none]


# =============================================================================
# Conversion Functions
# =============================================================================

def dataclass_to_dict(state: "WorkflowState") -> WorkflowStateDict:
    """
    Convert dataclass WorkflowState to TypedDict for LangGraph parallel execution.
    
    Args:
        state: Dataclass-based WorkflowState
        
    Returns:
        WorkflowStateDict compatible with parallel LangGraph execution
    """
    from dataclasses import asdict, fields
    
    result = {}
    for f in fields(state):
        value = getattr(state, f.name)
        # Convert Path to string for JSON serialization
        if isinstance(value, Path):
            result[f.name] = str(value)
        else:
            result[f.name] = value
    return result


def dict_to_dataclass(state_dict: WorkflowStateDict) -> "WorkflowState":
    """
    Convert TypedDict back to dataclass WorkflowState.
    
    Args:
        state_dict: WorkflowStateDict from LangGraph execution
        
    Returns:
        Dataclass-based WorkflowState for node functions
    """
    from dataclasses import fields as dataclass_fields
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
    
    # Ensure required fields have defaults (these have no default in the dataclass)
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
        
        # Embeddings
        "spec_chunk_embeddings": [],
        "spec_chunk_ids": [],
        "chunk_count": 0,
        "embedding_count": 0,
        
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
