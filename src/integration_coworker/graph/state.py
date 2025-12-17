from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from integration_coworker.sources.base import ParsedSpec
from integration_coworker.repo.models import RepoProfile, RepoChangeSet, RepoSnapshot
from integration_coworker.api.types import IntegrationOptions


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
