"""
API types for the integration coworker entrypoint.

Defines options and result types per Appendix C of the design spec.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class IntegrationOptions:
    """
    Optional configuration flags that influence a single run.
    
    All fields are optional and have safe defaults for v1.
    
    Semantics per Appendix C:
    - repo_integration_enabled: If False, skip analyze_repo_layout and
      apply_repo_integration_changes even if repo_root is set.
    - dry_run: If True, persist_results MUST NOT write to any database tables.
    - override_provider_code: If set, use this as provider_code instead of inference.
    - override_task_slug: If set, use this as the IntegrationTask task_slug.
    """
    repo_integration_enabled: bool = True
    dry_run: bool = False
    override_provider_code: Optional[str] = None
    override_task_slug: Optional[str] = None
    override_llm_model: Optional[str] = None
    override_max_tokens: Optional[int] = None


@dataclass
class IntegrationResult:
    """
    Result of a single integration run.
    
    Per Appendix C, this is the return type of design_and_generate_integration().
    Matches the spec exactly - NOT the success/messages variant I initially created.
    
    M4 additions:
    - persisted_ids: Dict tracking database IDs and persistence status
    - endpoints, schemas, entities: Silver model artifacts
    - workflow_nodes, workflow_edges, endpoint_bindings: Gold model artifacts
    
    Phase 4 additions:
    - spec_documents, doc_chunks, plan: For multi-spec tracking
    - errors, completed_steps: For diagnostics
    - provider_code: Inferred provider
    """
    run_id: str
    task: Optional['IntegrationTask']  # Forward reference, resolved at runtime
    code_artifacts: List['CodeArtifact'] = field(default_factory=list)
    repo_changes: Optional['RepoChangeSet'] = None
    report_markdown: str = ""
    
    # M4: Persistence tracking
    persisted_ids: Optional[dict] = None
    
    # M4: Silver artifacts
    endpoints: List = field(default_factory=list)
    schemas: List = field(default_factory=list)
    entities: List = field(default_factory=list)
    
    # M4: Gold artifacts
    workflow_nodes: List = field(default_factory=list)
    workflow_edges: List = field(default_factory=list)
    endpoint_bindings: List = field(default_factory=list)
    
    # Phase 4: Multi-spec and diagnostics
    spec_documents: List = field(default_factory=list)
    doc_chunks: List = field(default_factory=list)
    plan: Optional[dict] = None
    errors: List[str] = field(default_factory=list)
    completed_steps: List[str] = field(default_factory=list)
    provider_code: Optional[str] = None
