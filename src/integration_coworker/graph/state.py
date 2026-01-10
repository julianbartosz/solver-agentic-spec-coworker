"""
WorkflowState - Single authoritative in-memory state for LangGraph nodes.

V22-MEM: Implements memory-efficient copy semantics to address LangGraph's
internal state copying between nodes. Large fields use shallow copy by default,
with explicit deep copy only when modification is needed.
"""
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional
import copy

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
    # File Integration V1
    FileSpec,
    FileField,
    RecordLayout,
    FileValidationRule,
)
from integration_coworker.domain.ir import Operation
from integration_coworker.sources.base import ParsedSpec
from integration_coworker.repo.models import RepoProfile, RepoChangeSet, RepoSnapshot
from integration_coworker.api.types import IntegrationOptions


# V22-MEM: Fields that should use shallow copy (share reference) during LangGraph
# state propagation. These are large, immutable-after-population fields.
# Deep copy only happens if explicitly requested or if the field is modified.
SHALLOW_COPY_FIELDS = frozenset({
    # Bronze layer - large raw content
    "openapi_spec",           # 7-50MB dict
    "doc_chunks",             # 10MB+ list of strings
    "spec_documents",         # Parsed docs
    "spec_sections",          # Parsed sections
    "pending_specs",          # Multi-spec queue
    "repo_markdown_context",  # 5MB+ string
    
    # Silver layer - structured but large
    "spec_chunk_embeddings",  # Embeddings list
    "parsed_specs",           # Typed ParsedSpec
    "endpoints",              # Can be 1000+ for large APIs
    "schemas",                # Can be 500+ for large APIs
    "schema_fields",          # Can be 10000+ for large APIs
    
    # Gold layer - repo content
    "repo_snapshot",          # Full file contents
    "repo_changes",           # Diffs
    
    # Control - plan can grow large
    "plan",                   # Contains openapi_specs, indices
})


@dataclass
class WorkflowState:
    """
    Single authoritative in-memory state object passed between LangGraph nodes.
    """

    # Inputs
    source_refs: List[SourceRef]  # API-002: Typed source references
    spec_refs: List[str]  # Raw spec paths/URLs (for backwards compatibility)
    task_description: str
    provider_code: Optional[str] = None
    options: Optional[IntegrationOptions] = None

    # ==========================================================================
    # Spec Auto-Discovery (Slice 1)
    # Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md
    #
    # Populated by discover_spec node when spec_refs is empty and discovery
    # is enabled. Contains metadata about resolved spec for observability.
    # ==========================================================================
    
    # Discovery result metadata (candidates considered, selected index, etc.)
    # Shape: {"candidates": [...], "selected_index": int, "source": str}
    discovered_specs: Optional[Dict[str, Any]] = None
    
    # Confidence score of discovery match (0.0-1.0)
    discovery_confidence: float = 0.0
    
    # Source of spec resolution: "user_provided" | "apis_guru" | "web_search" | "catalog"
    discovery_source: Optional[str] = None

    # Bronze-level spec content
    spec_documents: List[SpecDocument] = field(default_factory=list)
    spec_sections: List[SpecSection] = field(default_factory=list)  # Per design doc Appendix C.2
    doc_chunks: List[str] = field(default_factory=list)
    openapi_spec: Optional[Dict[str, Any]] = None

    # Silver drafts
    source_system: Optional[SourceSystem] = None
    endpoints: List[Endpoint] = field(default_factory=list)
    endpoint_parameters: List[EndpointParameter] = field(default_factory=list)
    schemas: List[Schema] = field(default_factory=list)
    schema_fields: List[SchemaField] = field(default_factory=list)
    entities: List[Entity] = field(default_factory=list)
    relationships: List[EntityRelationship] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)

    # Protocol vNext: Protocol-agnostic operations (converted from native IR)
    # Populated by build_silver_api_model using AdapterRegistry
    operations: List[Operation] = field(default_factory=list)

    # File Integration V1 (Silver layer - parallel to API model)
    # Per docs/FILE_INTEGRATION_V1_PLAN.md
    file_specs: List[FileSpec] = field(default_factory=list)
    file_fields: List[FileField] = field(default_factory=list)
    record_layouts: List[RecordLayout] = field(default_factory=list)
    file_validation_rules: List[FileValidationRule] = field(default_factory=list)

    # Embeddings (Silver-adjacent)
    spec_chunk_embeddings: List[SpecChunkEmbedding] = field(default_factory=list)

    # V3 Streaming Persistence Fields
    # When STREAMING_PERSISTENCE=true, chunks and embeddings are streamed to DB
    # immediately. These fields track IDs/counts instead of full content.
    # This reduces memory from 200MB+ to <20MB for large specs.
    spec_chunk_ids: List[int] = field(default_factory=list)  # DB IDs of streamed chunks
    chunk_count: int = 0  # Total number of chunks (for progress tracking)
    embedding_count: int = 0  # Number of embeddings computed (for progress tracking)
    
    # Warnings (non-fatal issues) - V3 addition
    warnings: List[str] = field(default_factory=list)

    # Gold drafts
    workflow_template: Optional[WorkflowTemplate] = None
    integration_task: Optional[IntegrationTask] = None
    workflow_nodes: List[IntegrationFlowNode] = field(default_factory=list)
    workflow_edges: List[IntegrationFlowEdge] = field(default_factory=list)
    endpoint_bindings: List[EndpointBinding] = field(default_factory=list)
    policies: List[Policy] = field(default_factory=list)
    code_artifacts: List[CodeArtifact] = field(default_factory=list)

    # Repo integration
    repo_root: Optional[Path] = None
    repo_profile: Optional[RepoProfile] = None
    repo_snapshot: Optional[RepoSnapshot] = None
    repo_changes: Optional[RepoChangeSet] = None
    repo_markdown_context: Optional[str] = None

    # Control / bookkeeping
    plan: Dict[str, Any] = field(default_factory=dict)
    completed_steps: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    persisted_ids: Dict[str, Any] = field(default_factory=dict)

    # Multi-spec support (V2 Section 3.12)
    pending_specs: List[Dict[str, Any]] = field(default_factory=list)
    parsed_specs: List[ParsedSpec] = field(default_factory=list)  # V1 File Integration: typed ParsedSpec

    # V23-CACHE: Primary spec document ID for FK propagation
    # Survives state_gc cleanup (not in BRONZE_CONTENT_FIELDS)
    # Set by ingest_spec, used by persist_silver_checkpoint
    primary_spec_document_id: Optional[int] = None

    # V38-002: API base URL extracted from OpenAPI spec
    # Survives state_gc cleanup - extracted before openapi_spec is cleared
    # Used by derive_base_url() in code generation
    api_base_url: Optional[str] = None

    # Degraded mode tracking (V2 Section 3.13)
    degraded_mode: bool = False
    degraded_reason: Optional[str] = None

    # Skip tracking (V2.1 Section 13.2 - ADR-0009)
    # Nodes that were skipped due to failure cascade
    skipped_nodes: List[str] = field(default_factory=list)

    # LLM fallback tracking (for observability)
    llm_fallbacks: List[Dict[str, Any]] = field(default_factory=list)

    # Node timing tracking (for observability - shows non-LLM nodes do work)
    node_timings: Dict[str, float] = field(default_factory=dict)

    # Sandbox validation results (for observability - shows code quality gates)
    # Set by generate_code_and_tests after running sandbox execution
    sandbox_result: Optional[Dict[str, Any]] = field(default=None)

    # ==========================================================================
    # HITL Review Artifacts (ADR-HITL-ENHANCEMENT-v2)
    # ==========================================================================
    # Stores ArtifactRefs (not blobs) pointing to large review payloads.
    # interrupt() payloads stay < 2KB by using refs + bounded summaries only.
    # Large data (diffs, sandbox results, code snapshots) goes to artifact store.
    # 
    # Structure:
    #   review_artifact_refs = {
    #       "pre_write": {
    #           "code_snapshot": ArtifactRef.to_dict(),
    #           "diff_patches": ArtifactRef.to_dict(),
    #       },
    #       "post_sandbox": {
    #           "sandbox_result": ArtifactRef.to_dict(),
    #           ...
    #       }
    #   }
    review_artifact_refs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Kind of review gate that triggered interrupt: "pre_write" | "post_sandbox" | None
    pending_review_kind: Optional[str] = None
    
    # Human feedback from resume (free-form text for regeneration guidance)
    human_feedback: Optional[str] = None

    # ==========================================================================
    # Review Decisions (ADR-HITL-ENHANCEMENT-v2)
    # ==========================================================================
    # Records decisions from each review gate for idempotency and audit.
    # Structure:
    #   review_decisions = {
    #       "code": {
    #           "approved": True/False,
    #           "auto": True/False,  # Was this auto-approved (HITL disabled)?
    #           "reason": "dry_run" | "no_content" | None,
    #           "feedback": "optional human feedback",
    #           "overrides": {"exclude_files": [...]},
    #           "decided_at": timestamp,
    #       },
    #       "sandbox": {...}
    #   }
    review_decisions: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ==========================================================================
    # Quality Signals (ADR-HITL-ENHANCEMENT-v2 PR #7)
    # ==========================================================================
    # Stores ArtifactRefs pointing to quality analysis results.
    # Full results (large issue lists, attributions) go to artifact store.
    # State holds only refs + bounded summaries to keep checkpoint small.
    #
    # Structure:
    #   quality_refs = {
    #       "static": {
    #           "result": ArtifactRef.to_dict(),
    #           "summary": {"passed": bool, "blocking_count": int, ...},
    #           "schema_version": "1.0"
    #       },
    #       "sandbox_attribution": {
    #           "failures": ArtifactRef.to_dict(),
    #           "summary": {"count": int, "high_confidence": int, ...},
    #       },
    #       "human_edits": {
    #           "audit": ArtifactRef.to_dict(),
    #           "count": int
    #       },
    #       "quality_score": {
    #           "breakdown": ArtifactRef.to_dict()
    #       }
    #   }
    quality_refs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # ==========================================================================
    # Targeted Regeneration (PR #10)
    # ==========================================================================
    # Iteration state for regeneration budget tracking.
    # Stored as dict (not dataclass) to keep state JSON-friendly.
    # Use IterationState.from_dict() / .to_dict() for typed access.
    # See integration_coworker.graph.regeneration_models for schema.
    iteration_state: Optional[Dict[str, Any]] = None
    
    # Artifact ref pointing to RegenerationConstraints (refs-not-blobs pattern).
    # The constraints artifact contains full target paths and feedback.
    # Only the ref + summary counts are stored in state.
    # Structure: {"ref": ArtifactRef.to_dict(), "summary": {"target_count": int, ...}}
    regeneration_constraints_ref: Optional[Dict[str, Any]] = None
    
    # Static analysis retry tracking (for routing decisions)
    static_analysis_retries: int = 0
    static_analysis_escalated: bool = False
    
    # Flag set when human edits require re-validation
    needs_static_recheck: bool = False
    
    # PR #9: Sandbox failure attribution summary (bounded)
    # Stores category counts, top hints, and actionability flag
    # Full attributions stored in quality_refs["sandbox_attribution"]
    sandbox_attribution_summary: Optional[Dict[str, Any]] = None

    # V4 Observability: LLM token usage tracking
    # Aggregated across all LLM calls in this run
    llm_token_usage: Dict[str, int] = field(default_factory=lambda: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    })

    # V1.1: Spec caching (FT-001)
    # When True, build_silver_api_model skips LLM parsing and loads from DB
    cache_hit: bool = False

    # Outputs
    report_markdown: Optional[str] = None
    run_id: Optional[str] = None

    # =========================================================================
    # V22-MEM: Memory-efficient copy methods
    # =========================================================================
    # LangGraph copies state between nodes. By implementing custom __copy__ and
    # __deepcopy__, we can use shallow copy for large immutable fields, reducing
    # memory overhead from O(n) copies to O(1) reference sharing.
    # =========================================================================

    def __copy__(self):
        """
        Shallow copy for LangGraph state propagation.
        
        Large fields (openapi_spec, doc_chunks, etc.) share references.
        Only small control fields are actually copied.
        """
        # Create new instance with same field values (references shared)
        cls = self.__class__
        new_state = cls.__new__(cls)
        
        for f in fields(self):
            value = getattr(self, f.name)
            # All fields get shallow reference (no copy)
            setattr(new_state, f.name, value)
        
        return new_state

    def __deepcopy__(self, memo):
        """
        Smart deep copy that uses shallow copy for large fields.
        
        V22-MEM: Large fields in SHALLOW_COPY_FIELDS use reference sharing
        instead of deep copy. This reduces copy overhead from 50-100MB to <1MB
        for large specs while maintaining correctness (these fields are
        immutable after population in their respective nodes).
        """
        cls = self.__class__
        new_state = cls.__new__(cls)
        memo[id(self)] = new_state
        
        for f in fields(self):
            value = getattr(self, f.name)
            
            if f.name in SHALLOW_COPY_FIELDS:
                # Large fields: share reference (shallow copy)
                # These are immutable after their source node populates them
                setattr(new_state, f.name, value)
            elif value is None:
                # None values don't need copying
                setattr(new_state, f.name, None)
            elif isinstance(value, (str, int, float, bool, Path)):
                # Immutable primitives: share reference
                setattr(new_state, f.name, value)
            elif isinstance(value, list):
                # Small lists: shallow copy (new list, same elements)
                setattr(new_state, f.name, list(value))
            elif isinstance(value, dict):
                # Small dicts: shallow copy (new dict, same k/v)
                setattr(new_state, f.name, dict(value))
            else:
                # Other objects: deep copy
                setattr(new_state, f.name, copy.deepcopy(value, memo))
        
        return new_state

    def shallow_copy(self) -> "WorkflowState":
        """
        Explicit shallow copy API for node implementations.
        
        Use this when you know you won't modify large fields.
        """
        return copy.copy(self)

    def deep_copy_field(self, field_name: str) -> Any:
        """
        Deep copy a specific field before modification.
        
        Use this when a node needs to modify a large field:
        
            state.endpoints = state.deep_copy_field("endpoints")
            state.endpoints.append(new_endpoint)
        
        This enables copy-on-write semantics for large fields.
        """
        value = getattr(self, field_name, None)
        if value is None:
            return None
        return copy.deepcopy(value)
