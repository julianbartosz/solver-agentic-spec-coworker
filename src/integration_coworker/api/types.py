"""
API types for the integration coworker entrypoint.

Defines options and result types per Appendix C of the design spec.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from integration_coworker.domain.models import IntegrationTask, CodeArtifact
    from integration_coworker.repo.models import RepoChangeSet


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
    
    V2.1 additions (GAP-02):
    - policy_mode: Controls code generation style.
      - "inline": Generate self-contained clients with inline auth, retry, rate-limiting (default, backwards compat)
      - "runtime": Generate thin clients using IntegrationClient runtime library (~30 LOC vs ~300 LOC)
    
    V1.1 additions:
    - no_cache (FT-001): If True, skip spec caching and always re-parse specs
    - strict_codegen (FT-008): If True, enable strict mode with auto-formatting and stricter validation
    """
    repo_integration_enabled: bool = True
    dry_run: bool = False
    override_provider_code: Optional[str] = None
    override_task_slug: Optional[str] = None
    override_llm_model: Optional[str] = None
    override_max_tokens: Optional[int] = None
    
    # V2.1 (GAP-02): Code generation style
    policy_mode: Literal["inline", "runtime"] = "inline"
    
    # V1.1 (FT-001): Spec caching control
    no_cache: bool = False
    
    # V1.1 (FT-008): Strict code generation mode
    strict_codegen: bool = False
    
    # V2.2 (Dynamic Capability Fix #7): Constrained code generation mode
    # When True, use constrained generation that injects paths/fixtures from spec
    # rather than allowing LLM to generate them (reduces hallucination risk)
    constrained_codegen: bool = False


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
    
    V2.1 additions (Section 13.6):
    - policies: List of inferred policies (auth, rate-limit, retry) for this integration
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

    # V2.1: Inferred policies (Section 13.6)
    policies: List = field(default_factory=list)

    # V1.1 (FT-001): Spec caching indicator
    # True if spec was retrieved from cache instead of re-parsed
    cache_hit: bool = False

    # Phase 4: Multi-spec and diagnostics
    spec_documents: List = field(default_factory=list)
    doc_chunks: List = field(default_factory=list)
    plan: Optional[dict] = None
    errors: List[str] = field(default_factory=list)
    completed_steps: List[str] = field(default_factory=list)
    provider_code: Optional[str] = None
