from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from integration_coworker.domain.models import (
    SourceSystem,
    SpecDocument,
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


@dataclass
class WorkflowState:
    """
    Single authoritative in-memory state object passed between LangGraph nodes.
    """

    # Inputs
    source_refs: List[str]
    spec_refs: List[str]
    task_description: str
    provider_code: Optional[str] = None
    options: Optional[IntegrationOptions] = None

    # Bronze-level spec content
    spec_documents: List[SpecDocument] = field(default_factory=list)
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

    # Embeddings (Silver-adjacent)
    spec_chunk_embeddings: List[SpecChunkEmbedding] = field(default_factory=list)

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

    # Outputs
    report_markdown: Optional[str] = None
    run_id: Optional[str] = None
