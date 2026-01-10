0. Design Doc Metadata
Title: Agentic API Integration Designer & Code Generator
Author: Julian Bartosz
Reviewers: Karl Simon, Aaron Sosa
Version / Status: v2 (draft)
Date: 2025-12-16

1. Overview
1.1 Problem Statement
For every new client or product integration, a senior engineer must manually read API specifications and write, completely from scratch, details such as:
•	Endpoint call order
•	Request/response mapping to internal models
•	Authentication, error handling, retry, and rate limit behavior
•	Code structure aligned to team patterns
This manual process:
•	Is slow and inconsistent
•	Does not scale with growing numbers of APIs and tasks
•	Creates variability in security, logging, and error-handling quality
We lack a consistent pipeline from:
•	API specification → integration workflow
•	API specification → working, reviewed code
•	API specification → reusable integration artifacts
This gap slows onboarding, delays project starts, and reduces consistency.
Goal
Create a runtime-callable, LangGraph-based Agentic Integration Co-Worker that can ingest an integration task + API spec and produce:
•	Parsed specification (OpenAPI / HTML / PDF)
•	A normalized model of endpoints, schemas, entities
•	A planned integration workflow
•	Generated integration code, tests, config files
•	A structured “integration design” stored for reuse
•	Repo-aware code updates inside a supported service repository that follows the reference structure
The system uses a medallion-style structure:
•	Bronze: raw specs + prompts
•	Silver: normalized API surface (endpoints, schemas, entities, events)
•	Gold: integration tasks, workflows, bindings, policies
Success Criteria (v1)
Across at least 10 diverse public APIs (Stripe, GitHub, Twilio, Slack, Shopify):
•	Generated code should run with only minor edits
•	Developers can integrate the generated modules into a repo and run smoke tests in <1 hour
•	For a reference repo, the system should generate code + register integrations into the repository automatically
•	Generated flows must apply baseline patterns:
o	Authentication
o	Logging + redaction
o	Idempotency + transient retries
o	Pagination + rate limits
Historical Note (v1)
The above success criteria describe the initial local-process milestone that established end-to-end correctness. Later sections distinguish that milestone from the v2 design targets.
Operational Targets
•	End-to-end run (spec → workflow → code → repo updates → report) in <5 minutes
•	Store Silver + Gold in Postgres
•	Single Python function + CLI entrypoint
•	IntegrationOptions includes strict modes and codegen-style controls (for example strict_codegen, policy_mode)
•	Primary value over raw LLM use through consistent patterns, knowledge graph reuse, and automatic wiring into the repo

1.2 Objectives & Success Criteria
Primary Objective:
Build a Python LangGraph workflow that, given an integration task + API spec(s), outputs:
•	Parsed specification
•	Normalized API model
•	Planned workflow
•	Generated client code, workflow modules, tests, and config
•	Repo updates (when enabled)
•	Persisted Silver/Gold records and a human-readable run report

1.3 Non-Goals
Out of scope for the initial local-process milestone (v1):
•	Production deployment stack
•	Full ETL/ELT pipelines into analytics warehouses
•	Non-Python runtimes
•	Custom LLM training or fine-tuning

The initial milestone runs on a local developer machine with a local or shared Postgres instance and is designed as an internal integration co-worker rather than a hosted service.

Future Deployment Target (v2)
This document distinguishes between the v1 local-process runtime and an additional v2 deployment target: a long-running service.
The service target is not part of v1 scope. It exists to support repeated execution, centralized observability, and controlled access to persistent state and repository workspaces.
The v2 service target would execute the same LangGraph workflow with explicit operational boundaries, including run admission control, multi-run concurrency controls, and stricter credential and log-handling policies.

1.4 High-Level Solution Summary
The solution consists of three major components:
1. Agentic LangGraph Workflow
A stateful LangGraph pipeline with nodes and tools for:
•	Task understanding
•	Spec ingestion and parsing
•	Silver model extraction
•	Workflow planning
•	Policy and pattern attachment
•	Code generation
•	Validation
•	Persistence and reporting
•	Repo analysis and wiring into a reference repo as the default v1 path
Individual nodes may reuse LangChain components for parsing, retrieval, and tool wrappers, while LangGraph acts as the primary orchestrator for control flow and state.
2. Medallion-Style Data Architecture
•	Bronze: raw spec documents for one or more specifications plus task prompts
•	Silver: normalized API models in Postgres for every spec in the run, with a shared pgvector index (dimension 1536) across their chunks
•	Gold: integration tasks, workflows, endpoint bindings, policies, and code metadata that may reference endpoints from several specification documents in a single run
3. RAG / KG / GraphRAG Context Layer
•	Vector index over spec chunks and derived notes in Postgres with pgvector
•	Knowledge graph tables in the kg schema in the same Postgres instance. These tables form a directed graph that nodes traverse by bounded breadth- or depth-first search.
•	Embeddings in kg act as a secondary ranking signal inside a local neighborhood, not as a stand-alone semantic index.
LangSmith provides traces for all LLM and embedding calls and acts as the primary debugging and inspection surface for the agentic workflow. Node-level logs and metrics complement LangSmith by recording structured input and output snapshots per node.

1.5 Intended Users & Scenarios
Primary Users
•	Integration + backend engineers
•	Solution architects
•	Platform engineers maintaining shared patterns
Key Scenarios
1. New Integration Flow
Input: Spec URL + task (e.g., “Create Stripe subscription checkout flow”).
Output: workflow graph, Python modules, config, tests, design summary.
2. Provider Upgrade / Refactor
Diff between old and new Gold models + affected code.
3. Pattern Development & Reuse
KG collects workflows/patterns so new tasks reuse consistent integration structures.

2. Context & Requirements
2.1 Business Context
The system moves early integration work from manual coding to a semi-automatic, repeatable pipeline.
Goals
•	Faster path from spec → integration skeleton
•	Consistency across integrations
•	Reusable patterns (checkout, subscription, webhooks)
•	More secure + robust integration code
The project also serves as a practical application of:
•	Medallion architecture
•	RAG + GraphRAG
•	LangGraph orchestration
•	Knowledge graph for workflow/pattern reuse

2.2 Source System Types & Inputs
The primary scope for v1 is HTTP-based APIs with machine-readable specifications, such as OpenAPI and JSON Schema. A single run can ingest multiple specifications for the same provider or related domains. Every HTTP spec in the run maps to the Silver (spec_silver) and Gold (integration_gold) schemas, and the appendices define concrete models and tables for these inputs.

v1 also supports structured non-HTTP sources that describe integration surfaces in a machine-readable way:
•	File-based schemas for CSV, fixed-width, or EDI documents
•	Message-based or event-driven interfaces that describe topics, queues, and payload schemas

For these inputs, the spec_silver.file_specs and spec_silver.message_specs tables hold source-specific metadata and link back to the same Schema, Entity, and Event tables used for HTTP. The build_silver_api_model node creates Schema, SchemaField, Entity, and Event rows for both HTTP and non-HTTP inputs whenever the source provides enough structure.

For file-oriented inputs, a dedicated build_silver_file_model node runs after build_silver_api_model to populate file-related Silver records and to attach file-derived entities when the source provides sufficient structure.

Unstructured guides such as PDF or free-form HTML for CSV/EDI or message contracts follow the text-only rules from Appendix A.3.7. The workflow still writes a Silver surface for these sources, records gaps in diagnostics fields, and lets later runs reprocess the same inputs as extraction rules improve.

2.3 Functional Requirements
The co-worker must support:
1. Spec Parsing
•	Fetch documents
•	Detect format
•	Chunk text
•	Parse OpenAPI into structured models
2. Task Understanding
•	Parse NL task
•	Infer provider, domain, entities
•	Map to known workflow templates
•	Produce a structured IntegrationTask
3. Workflow Derivation
•	Use IntegrationTask + Silver model + KG
•	Select endpoints, define call order
•	Determine data dependencies
•	Identify events/webhooks
•	Attach auth/retry/pagination/logging patterns
4. Structured Outputs
•	Silver schema tables
•	Gold schema tables
•	Machine-readable design artifacts expressed as TOON objects encoded in YAML, with JSON as a secondary format when needed
5. Code Generation
•	Provider client wrappers
•	Workflow modules
•	Config files
•	Tests
•	Supports pluggable repository layout profiles
6. Repo Integration for Reference Repos
•	Analyze repo structure
•	Place generated modules in correct directories
•	Register integrations in routers/registries
•	Produce a repo change summary
7. On-Demand Execution
•	Python API
•	CLI
8. Interactive Refinement
•	Notebook helpers
•	Node-level debugging
•	Manual corrections

2.4 Non-Functional Requirements
Performance
•	<5 min per run
•	RAG-driven retrieval
•	Cache parsed specs + embeddings
Scalability
•	10–50 providers
•	Specs up to 20 MB
Reliability
•	Write partial artifacts on failure
•	Log errors per node
Cost
•	Small top_k
•	Reuse embeddings
•	Targeted prompts
Security
•	Environment variable credentials
•	No sensitive response logging
•	Centralized secret handling patterns

3. Target Architecture (High-Level)
3.1 System Context
Core component: Agentic Integration Co-Worker (LangGraph).
Inputs: specs, task, optional repo settings.
Outputs: Silver and Gold records, code, repo changes, reports.
External systems for v1:
•	Local Git working copy
•	Local or shared Postgres 15 instance with pgvector (spec_silver, integration_gold, kg, repo_meta)
•	LLM providers accessed through configurable clients
•	LangSmith for tracing, cost tracking, and node-level metrics
•	Local logging and metrics for supplementary diagnostics
v1 assumes a local or developer-controlled environment. A later phase may move the same workflow into a managed GCP or Azure runtime once the local end-to-end workflow is stable. Cloud deployment is not in scope for v1.

The long-running service target described in Section 1.3 is treated as a later-phase deployment target and does not change the v1 system context.

3.2 Runtime Deployment (v1)
Runtime behavior is controlled by IntegrationOptions. Two flags are central:
•	dry_run: when True, the system runs the full graph, builds a report in memory, and does not write to Postgres. No records are inserted or updated in spec_silver, integration_gold, the kg schema, integration_gold.run_status, or repo_meta.
•	repo_integration_enabled: when False, the graph skips all nodes that inspect or change a repository, even if repo_root is present in the input.
The plan_run node reads these options and decides whether the run is repo-aware. It sets plan["use_repo"] based on repo_root and IntegrationOptions.repo_integration_enabled.
The four persistence nodes, persist_silver_checkpoint, persist_gold_checkpoint, persist_kg_learning, and persist_run_outcome, read IntegrationOptions.dry_run. When dry_run is True they skip all database writes. In this mode, persisted_ids is still populated with in-memory status markers (for example run_status="completed_dry_run") and the workflow still produces plan and report_markdown. When dry_run is False, persist_silver_checkpoint and persist_gold_checkpoint write Silver and Gold data (and spec_chunks when embeddings are present), persist_kg_learning writes KG templates and embeddings, and persist_run_outcome writes run_status, RAG metrics, and repo_meta tables.
apply_repo_integration_changes also respects dry_run. When dry_run is True, it does not change files on disk and records intended file changes only in RepoChangeSet. When dry_run is False it writes RepoChangeSet to the filesystem.
The reference runtime for v1 is a local process. Developers run the graph on a laptop or workstation against a local or shared Postgres instance. v1 does not include a production deployment stack. A later phase can lift the same workflow into a managed GCP or Azure environment after the local runs are stable.

3.3 Medallion Data Architecture
Bronze
•	Raw HTML/PDF/OpenAPI
•	Raw prompts
Silver
•	endpoints, parameters
•	schemas, fields
•	entities, relationships
•	events
•	embeddings
Gold
•	integration tasks
•	workflows
•	nodes/edges
•	endpoint bindings
•	policies
•	code artifacts

4. Core Domain Model & Data Contracts
4.1 Key Concepts
The core domain concepts are represented by concrete Python dataclasses and enums. Most of these types appear directly in WorkflowState. SourceSystem is managed via persistence (IDs and foreign keys) rather than as an in-memory list on WorkflowState.
Key Silver-layer concepts:
• SourceSystem
• SpecDocument
• SpecSection
• Schema and SchemaField
• Entity and EntityRelationship
• Event
• Endpoint and EndpointParameter
These types map to tables in the spec_silver schema and appear in WorkflowState.
Gold-layer concepts:
•	WorkflowTemplate, IntegrationTask, IntegrationFlowNode and IntegrationFlowEdge
•	EndpointBinding
•	Policy
•	CodeArtifact
These map to tables in the integration_gold schema, including integration_gold.workflow_templates.
Knowledge graph concepts:
•	The kg schema holds a separate set of workflow templates that support planning. These live in kg.workflow_templates, kg.workflow_steps, and kg.step_bindings. These KG tables are distinct from the integration_gold.workflow_templates table that stores designed flows for specific tasks. They use separate persistence structures from the WorkflowTemplate dataclass. In v1, the workflow reads from these tables during planning and also writes updates and new templates for patterns that pass validation.
Embedding and vector concepts:
•	SpecChunkEmbedding for per-chunk embeddings derived from doc_chunks
Repo integration concepts (see Appendix D):
•	RepoProfile
•	RepoSnapshot
•	RepoChangeSet and FileChange
These types are defined in src/integration_coworker/domain/models.py, src/integration_coworker/repo/models.py, and src/integration_coworker/api/types.py and RepoProfile and RepoChangeSet appear in WorkflowState; RepoSnapshot and FileChange are internal helper types used by repo nodes.

4.2 Logical Data Schema
The logical data model spans three layers: Silver (spec_silver), Gold (integration_gold), and the knowledge graph (kg).
•	Silver (spec_silver). The Silver layer resides in the spec_silver schema. Key tables are:
o	spec_documents for raw specification files and metadata
o	schemas for JSON Schema–like types
o	endpoints for HTTP operations
o	fields for request and response fields
o	entities and entity_relationships for higher level resource models
o	events for important domain events
o	endpoint_parameters for path, query, and header parameters
o	spec_sections for logical sections of each specification document
o	spec_chunks for text chunks used for embeddings and retrieval
•	Gold (integration_gold). This schema stores the designed integration: planned flows, module descriptions, cross-cutting policies, and references to code artifacts. It records the outputs of the LangGraph run, including status and any errors, keyed by run_id.
•	Knowledge graph (kg). This schema stores reusable, hand-curated knowledge about providers, integration patterns, and domain-specific workflows. It may include precomputed text chunks and embeddings that feed retrieval for later runs.

4.3 Physical Storage (v1)
The system stores persistent state in Postgres 15 with the following schemas:
•	spec_silver for Silver-level spec data and spec chunks
•	integration_gold for integration tasks, flows, policies, code artifacts, run status, and RAG metrics
•	kg for knowledge graph templates and bindings
•	repo_meta for repository integration metadata

All primary keys use BIGSERIAL / BIGINT. The spec_silver.spec_chunks.embedding column uses VECTOR(1536) to match the configured embedding dimension.

Database writes in v1 occur primarily at three checkpoint nodes:
•	persist_silver_checkpoint writes spec_silver.source_systems, spec_silver.spec_documents, spec_silver.spec_sections, and the core Silver tables (schemas, fields, entities, entity_relationships, events, endpoints, endpoint_parameters), and spec_silver.spec_chunks when embeddings are present. It also backfills ids and foreign keys on in-memory Silver objects.
•	persist_gold_checkpoint writes integration_gold.integration_tasks and the other Gold tables (workflow_templates, integration_flow_nodes, integration_flow_edges, endpoint_bindings, policies, code_artifacts). It backfills ids on the corresponding in-memory Gold objects.
•	persist_run_outcome writes integration_gold.run_status, integration_gold.rag_eval_metrics, and repo_meta.integrations and repo_meta.files for repo-aware runs.

Knowledge graph writes in v1 occur in a dedicated node:
•	persist_kg_learning writes KG learning rows to kg.nodes, kg.edges, kg.workflow_steps, and kg.step_bindings.

In addition, plan_run attempts to create an initial integration_gold.run_status row (status="running") before checkpoints are written, to satisfy run checkpoint foreign key constraints. This insertion is treated as non-fatal when the database is unavailable.

Each checkpoint runs in its own transaction. Between checkpoints, LangGraph nodes work with in-memory drafts and keep intermediate state in WorkflowState and LangSmith traces.

4.4 APIs & Interfaces
The system exposes a single, stable Python entrypoint for v1. This function wires external inputs (specs, task, repo, options) into the LangGraph WorkflowState, runs the graph, and returns an IntegrationResult suitable for both library and CLI usage.
File: api/entrypoint.py
from pathlib import Path
from typing import Iterable, Optional
from api.types import IntegrationOptions, IntegrationResult
from graph.state import WorkflowState
from graph.runtime import run_workflow # LangGraph executor
from repo.models import RepoProfile
def design_and_generate_integration(
spec_refs: Iterable[str],
task_description: str,
provider_code: Optional[str] = None,
repo_root: Optional[Path] = None,
repo_profile: Optional[RepoProfile] = None,
options: Optional[IntegrationOptions] = None,
) -> IntegrationResult:
"""

Public API for a single integration run.
- spec_refs: see Appendix C for semantics. A single run may include one or more specification references.
- task_description: natural-language description of the integration task.
- provider_code: optional override; may also be set via
  options.override_provider_code.
- repo_root: optional filesystem root of a target repo for wiring.
- repo_profile: optional explicit RepoProfile; if None and repo_root is set,
  attach_repo_context is responsible for detecting a profile.
- options: IntegrationOptions controlling repo integration and dry-run
  behavior (repo_integration_enabled, dry_run, overrides, etc.).
"""

state = WorkflowState(
    source_refs=[],
    spec_refs=list(spec_refs),
    task_description=task_description,
    provider_code=provider_code,
    repo_root=repo_root,
    repo_profile=repo_profile,
    options=options or IntegrationOptions(),
)

final_state = run_workflow(state)

return IntegrationResult(
    run_id=final_state.run_id or "",
    task=final_state.integration_task,
    code_artifacts=final_state.code_artifacts,
    repo_changes=final_state.repo_changes,
    report_markdown=final_state.report_markdown or "",
)
The CLI is a thin wrapper over design_and_generate_integration and is responsible only for reading inputs (spec refs, repo_root, options), invoking this API once per run, and writing any generated artifacts, diffs, or reports to disk or stdout.

5. Agentic Workflow & LangGraph Design
5.1 Workflow Overview
The LangGraph workflow is a directed graph over a single WorkflowState object. A typical v1 run follows these steps and persistence checkpoints:
1.	plan_run
2.	ingest_spec
3.	detect_and_parse_spec
4.	build_silver_api_model
5.	persist_silver_checkpoint
6.	embed_spec_chunks
7.	understand_task
8.	align_task_with_kg
9.	plan_integration_flow
10.	attach_policies_and_patterns
11.	persist_gold_checkpoint
12.	persist_kg_learning
13.	attach_repo_context (when repo context is requested)
14.	generate_code_and_tests
15.	analyze_repo_layout (for repo runs)
16.	apply_repo_integration_changes (for repo runs)
17.	validate_integration_design
18.	persist_run_outcome
19.	build_report
20.	handle_error (on failure at any step)

Each node is classified into one of four categories for observability:
•	pure-python: No external calls; used for in-memory transforms and validation
•	db-write: Writes to Postgres; applies to checkpoint nodes
•	api-call: Makes HTTP calls to spec endpoints or external services
•	llm: Invokes LLM providers for extraction, planning, or generation

The NODE_METADATA catalog in runtime.py describes each node's category and expected behavior. The timed_node() wrapper measures execution time and integrates with LangSmith @traceable for tracing.
ingest_spec, build_silver_api_model, understand_task, align_task_with_kg, plan_integration_flow, attach_policies_and_patterns, generate_code_and_tests, analyze_repo_layout, and validate_integration_design still work with in-memory drafts. The new persistence nodes write stable Silver, Gold, KG, and metrics records after key phases. A failed run has durable intermediate state up to the last completed checkpoint.

When several specification documents are present, the same workflow uses a single WorkflowState that holds Silver drafts and embeddings for all of them. Planning nodes can select endpoints and entities from any of these documents when they build IntegrationFlowNode and EndpointBinding objects.
On any failure, the graph routes to handle_error. This node records an error summary in plan["failed"] and errors and passes control forward so that persist_run_outcome and build_report can write a status and report for partial results.
Repo-aware runs follow steps 12–15. Repo-less runs skip attach_repo_context, analyze_repo_layout, and apply_repo_integration_changes and move directly from code generation to validation and persistence. The plan_run node sets plan["use_repo"] to control this branch.

5.2 Workflow State Model
WorkflowState is the single in-memory state that flows between nodes.
Inputs:
•	source_refs: List[str]
•	spec_refs: List[str]
•	task_description: str
•	provider_code: Optional[str]
•	options: Optional[IntegrationOptions]
•	source_system: Optional[str] (separate from provider_code for multi-source specs)
Bronze-level & silver fields:
•	spec_documents: List[SpecDocument]
•	doc_chunks: List[str]
•	openapi_spec: Optional[Dict[str, Any]]
Silver drafts:
•	spec_sections: List[SpecSection]
•	endpoints: List[Endpoint]
•	endpoint_parameters: List[Dict[str, Any]]
•	schemas: List[Schema]
•	schema_fields: List[Dict[str, Any]]
•	entities: List[Entity]
•	relationships: List[EntityRelationship]
•	events: List[Event]
Embeddings:
•	spec_chunk_embeddings: List[SpecChunkEmbedding]
Gold drafts:
•	integration_task: Optional[IntegrationTask]
•	workflow_template: Optional[Dict[str, Any]] (KG template match result)
•	workflow_nodes: List[IntegrationFlowNode]
•	workflow_edges: List[IntegrationFlowEdge]
•	endpoint_bindings: List[EndpointBinding]
•	policies: List[Policy]
•	code_artifacts: List[CodeArtifact]
Repo integration:
•	repo_root: Optional[Path]
•	repo_profile: Optional[RepoProfile]
•	repo_snapshot: Optional[Dict[str, Any]] (captured repo state for replay)
•	repo_changes: Optional[RepoChangeSet]
•	repo_markdown_context: Optional[str]
Control and bookkeeping:
•	plan: Dict[str, Any]
•	completed_steps: List[str]
•	errors: List[str]
•	persisted_ids: Dict[str, Any]
•	llm_fallbacks: Dict[str, Any] (LLM fallback tracking)
Observability:
•	node_timings: Dict[str, float] (per-node execution time in ms)
•	checkpoints: Dict[str, Any] (checkpoint metadata)
Outputs:
•	report_markdown: Optional[str]
•	run_id: Optional[str]
Nodes read and write a defined subset of these fields. Four persistence nodes (persist_silver_checkpoint, persist_gold_checkpoint, persist_kg_learning, and persist_run_outcome) write to the database. apply_repo_integration_changes is the only node that writes to the filesystem.

5.3 Node Definitions
The node set includes the following:
•	plan_run
o	Plans the run, selects the primary spec reference, and decides whether to use repo integration.
o	Resolves provider_code from options, the spec host, or existing state.
•	ingest_spec
o	Loads raw spec content from URLs or files into spec_documents and doc_chunks.
•	detect_and_parse_spec
o	Detects OpenAPI/Swagger.
o	Parses OpenAPI specs into openapi_spec when present.
•	build_silver_api_model
o	Converts openapi_spec or text-only doc_chunks into Silver drafts: schemas, entities, relationships, events, endpoints, and endpoint_parameters.
o	Applies the deterministic rules in Appendix A.3.
•	build_silver_file_model
o	Builds file-oriented Silver drafts (for example file specs and file fields) when file-oriented inputs are present.
•	embed_spec_chunks
o	Computes embeddings for doc_chunks.
o	Produces spec_chunk_embeddings entries that map chunk indices to embedding vectors.
•	understand_task
o	Builds an IntegrationTask with task_slug, input_entities, output_entities, and constraints using the task description and Silver drafts.
•	align_task_with_kg
o	Queries the KG and spec embeddings to find candidate workflow templates.
o	Writes plan["candidate_templates"] and may refine task constraints.
•	plan_integration_flow
o	Converts templates and Silver drafts into workflow_nodes and workflow_edges.
o	Creates EndpointBinding scaffolds for api_call nodes as in Appendix C.3 and Appendix H.5.
•	attach_policies_and_patterns
o	Attaches Policy objects to nodes and endpoints based on auth, retry, pagination, logging, and rate limit patterns.
•	generate_code_and_tests
o	Generates CodeArtifact instances for clients, workflows, and tests.
o	Refines EndpointBinding mappings when possible using schemas and the planned flow.
•	persist_gold_checkpoint
o	Upserts Gold tables for the integration task and flow.
o	In dry runs, skips database writes.
•	persist_kg_learning
o	Writes learned KG templates and steps when the run produces a valid workflow.
o	Updates existing templates when a new run has higher coverage or validation scores.
o	In dry runs, skips database writes.
•	attach_repo_context
o	Builds repo context when repo_root or a markdown file path is present.
o	Sets repo_markdown_context and, when needed, infers a RepoProfile.
•	analyze_repo_layout
o	For repo runs, maps CodeArtifact entries to target paths using RepoProfile.layout_hints and archetype rules.
o	Computes router and settings changes for FastAPI-style repos using markers and builds a RepoChangeSet.
•	apply_repo_integration_changes
o	Applies RepoChangeSet to disk under repo_root.
o	Fills FileChange.before and change_type.
•	validate_integration_design
o	Checks Silver and Gold drafts and repo changes against flow semantics in Appendix H.5.
•	persist_silver_checkpoint
o	Upserts Silver tables and spec_chunks rows.
o	In dry runs, skips database writes.
•	persist_run_outcome
o	Writes run_status, RAG metrics, and repo_meta tables.
o	In dry runs, skips database writes.
•	build_report
o	Renders a human-readable report into report_markdown.
•	handle_error
o	Aggregates error messages and sets plan["failed"] = True while leaving drafts intact.

Note on file integration terminology.
This document uses “file-oriented inputs” as a category for spec sources that are not OpenAPI documents. A concrete proposal for file integration (SpecSource routing, Silver file model tables, and related codegen artifacts) is captured in `archive/docs/reports/FILE_INTEGRATION_V1_PLAN.md`. That plan is treated here as a design reference rather than a statement of implemented behavior.
Each node appends its name to completed_steps on success. Nodes do not write to disk or the database except apply_repo_integration_changes and the four persistence nodes.

The four persistence nodes are persist_silver_checkpoint, persist_gold_checkpoint, persist_kg_learning, and persist_run_outcome.

5.4 Graph Topology
The graph has a main happy path and two variants: repo-aware and repo-less.
Core path:
•	plan_run → ingest_spec → detect_and_parse_spec → build_silver_api_model → build_silver_file_model → embed_spec_chunks → persist_silver_checkpoint → understand_task → align_task_with_kg → plan_integration_flow → attach_policies_and_patterns → generate_code_and_tests → persist_gold_checkpoint → persist_kg_learning → validate_integration_design → build_report → persist_run_outcome
Repo-aware path:
•	plan_run → ingest_spec → detect_and_parse_spec → build_silver_api_model → build_silver_file_model → embed_spec_chunks → persist_silver_checkpoint → understand_task → align_task_with_kg → plan_integration_flow → attach_policies_and_patterns → generate_code_and_tests → persist_gold_checkpoint → persist_kg_learning → attach_repo_context → analyze_repo_layout → apply_repo_integration_changes → validate_integration_design → build_report → persist_run_outcome
Repo-less path:
•	plan_run → ingest_spec → detect_and_parse_spec → build_silver_api_model → build_silver_file_model → embed_spec_chunks → persist_silver_checkpoint → understand_task → align_task_with_kg → plan_integration_flow → attach_policies_and_patterns → generate_code_and_tests → persist_gold_checkpoint → persist_kg_learning → validate_integration_design → build_report → persist_run_outcome
The entry node is plan_run. The terminal node is build_report. Persistence occurs at three checkpoint nodes (persist_silver_checkpoint, persist_gold_checkpoint, persist_run_outcome) plus a dedicated persist_kg_learning node for knowledge graph updates. Any node may route to handle_error on failure, then onward to persist_run_outcome and build_report so an error run still produces a status and report.

5.5 Prompting & Agent Configuration
The LangGraph workflow uses a clear prompting and agent configuration strategy. Nodes speak in a shared Token-Oriented Object Notation (TOON) that the system encodes as YAML by default. Planning nodes use this format for plans and flows. Extraction and code generation nodes use it for structured results that map onto the domain models.

The workflow uses three broad prompt patterns:
•	Planning and reasoning prompts for steps that design flows and policies
•	Extraction prompts for schema and endpoint parsing
•	Generation prompts for code, tests, and configuration
Agent and node configuration is stored in YAML archetype files that follow an Anthropic-style pattern. These files define the models, temperature, token limits, retrieval settings, and TOON schemas that a node uses. The archetypes describe input and output schemas in YAML so that prompts can present TOON examples and the node parser can read YAML back into dataclasses.

The workflow is LLM-agnostic. Each node can use a different provider and model family. A common pattern is:
•	Smaller OpenAI models for extraction
•	Larger OpenAI or Anthropic models for planning
•	Anthropic or OpenAI models for code generation
with the same TOON contracts across providers. The YAML archetypes and the shared models.yaml file define these bindings and can be edited without code changes.

These YAML files live in Git next to the workflow code and act as the primary control surface for prompt and parameter changes. LangSmith stores configuration values as tags or metadata on each run so that changes in prompts and parameters can be linked to changes in cost and output quality. Per-node input and output snapshots are logged as compact JSON records that reference the same configuration keys and run_id, so LangSmith traces and node logs show a consistent view of each step.
5.5.1 Prompting Strategies
Planning nodes
•	Nodes: plan_run, understand_task, align_task_with_kg, plan_integration_flow, attach_policies_and_patterns
•	Use chain-of-thought style prompts that ask the model to reason in multiple steps before it produces the final plan or flow.
•	Support self-consistency by sampling multiple plans for a single task and selecting one plan using a short evaluator prompt.
The number of samples and selection method are stored in YAML (for example planning.num_samples, planning.selector).
•	Support light tree-of-thought planning for ambiguous tasks by branching a small number of candidate flows and pruning them with simple checks such as endpoint coverage and presence of required entities.
The branching limits are stored in YAML (for example planning.max_branches).
Extraction nodes
•	Nodes: ingest_spec helpers, detect_and_parse_spec, build_silver_api_model
•	Use prompts that request strict TOON objects encoded as YAML that match the Silver schema.
•	Use low temperature and small models where possible.
•	Rely on hidden reasoning where the model supports it, while keeping the returned content bounded and machine readable.
Code generation nodes
•	Node: generate_code_and_tests
•	Use prompts that supply:
o	The IntegrationTask and IntegrationFlow graph
o	Endpoint definitions and key schema fields
o	Policy patterns for auth, retries, logging, and pagination
o	Repo profile information when repo integration is active
•	Support multiple candidate generations for selected modules, with a small static or prompt-based evaluator that selects one candidate.
The number of candidates and evaluator settings are stored in YAML (for example codegen.num_candidates).
Validation and report nodes
•	Nodes: validate_integration_design, build_report
•	Use structured prompts that compare plans, code, and Silver/Gold models and then produce structured findings and summaries.
•	Keep these prompts simple to reduce token use and to keep validation logic easy to reason about.
Each prompting strategy has explicit configuration in the YAML archetypes. Langsmith uses tags to record which strategy settings were active for a run, for example the number of planning samples or the branching factor. This link between configuration and traces supports later analysis of cost, latency, and quality for different prompting strategies.

5.6 Parallelization Strategy
The workflow uses bounded parallelism to reach the five-minute target while keeping LLM spend low. Parallel behavior is controlled through fields in the YAML archetypes.
Planning samples
•	Nodes plan_run, understand_task, align_task_with_kg, plan_integration_flow, and attach_policies_and_patterns may draw several samples in parallel.
•	The fields planning.num_samples and planning.max_parallel_samples control the number of samples and the maximum concurrency.
•	A small selector node reads these samples and chooses one plan for the run.
Code generation
•	Node generate_code_and_tests can run code generation for independent modules and their tests in parallel, up to a limit in codegen.max_parallel_modules.
•	For selected modules it can draw several candidates in parallel for the same module, controlled by codegen.num_candidates.
•	A simple evaluator chooses one candidate per module, based on static checks or a short scorer prompt.
Embedding and retrieval work
•	During ingestion the system batches or runs embeddings for document chunks in parallel, controlled by ingestion.max_parallel_embeddings.
•	When a node needs several independent retrieval calls, vector search and graph queries may run in parallel within a small fixed limit.
Cost and tuning
•	Default settings keep the number of parallel LLM calls small, for example two or three concurrent calls for planning and code generation.
•	Langsmith traces record the active parallelism settings, node call counts, tokens, and latency for each run.
•	These traces guide later tuning of the parallelism fields so that runs stay close to the cost target while meeting the time goal.

6. RAG & GraphRAG Context Infrastructure
6.1 Context Sources & Ingestion
The system draws context from four main sources:
•	Unstructured specification text in doc_chunks
•	Structured OpenAPI content in openapi_spec and Silver drafts
•	Knowledge graph tables in the kg schema
•	Repository markdown context in repo_markdown_context for repo-aware runs
ingest_spec reads the primary spec reference into spec_documents and doc_chunks. detect_and_parse_spec and build_silver_api_model turn this content into Silver drafts. embed_spec_chunks then computes embeddings for each chunk and writes SpecChunkEmbedding entries into WorkflowState.spec_chunk_embeddings. The KG tables and repo markdown are read-only context during runs.

6.2 Indexing Strategy
The vector index supports retrieval across the specification and, where present, the knowledge graph.
The core index is built from spec_silver.spec_chunks, which holds text chunks for every spec_document in the run with a fixed-size embedding vector (dimension 1536). These rows form the primary vector index for specification content.

The kg schema includes embedding columns or companion tables for text fields in workflow_templates, workflow_steps, and step_bindings. These columns store short summaries and persist their embeddings as part of the normal persistence pipeline. Together, the spec_silver.spec_chunks embeddings and the KG embeddings form the canonical vector layer in v1, backed by Postgres and pgvector. The design does not add a separate vector store or a separate graph database. All KG tables and their embeddings live in the same Postgres instance as Silver and Gold.

The retrieval layer treats these KG rows as a first-class source of context. For KG reads, nodes first pick a start set by walking the graph structure (templates, steps, bindings, endpoints) and apply embeddings only inside that set to rank or trim candidates, not as a direct vector index over all kg.* rows.

persist_kg_learning keeps the KG embeddings in sync with template changes by writing summary_embedding values for workflow_templates, workflow_steps, and step_bindings during KG learning.

When a node reads from the vector index or the knowledge graph, it receives TOON records encoded as YAML fragments. The retrieval layer passes these fragments into prompts as YAML blocks rather than as JSON.

6.3 GraphRAG Design
The context layer treats the kg schema as a directed graph in Postgres and applies bounded breadth- or depth-first traversal for all knowledge graph queries before any embedding scoring. kg.workflow_templates and kg.workflow_steps define reusable flows such as "create customer then charge card." kg.step_bindings links each step to API endpoints, auth schemes, retry rules, and code templates. These KG tables are distinct from the integration_gold.workflow_templates table that stores designed flows for specific tasks.

KG retrieval is graph-first. Nodes start from provider_code, entities, or endpoints and follow explicit links between workflow templates, steps, and step_bindings using bounded breadth- or depth-first traversal. Embeddings act as a secondary signal inside the reached neighborhood to rank and filter nodes, rather than as a primary way to jump straight into the KG.

Hybrid scoring uses the following weights:
•	40% graph distance (closer nodes score higher)
•	40% embedding similarity (cosine distance)
•	20% exact-match bonuses (provider_code, entity name, operation_id)

6.3.1 Current Bootstrap State
The v1 implementation uses a bootstrap approach where:
•	A hardcoded Python dictionary (_LEGACY_WORKFLOW_TEMPLATES) holds initial workflow templates for a small set of providers (stripe, mock_payments).
•	The kg schema tables exist in DDL and are functional, but sparse.
•	The persist_kg_learning node writes learned templates to the database after each successful run, gradually populating the KG.
•	Cross-provider pattern matching is implemented but depends on template density.

As more runs complete and templates accumulate, the system transitions from bootstrap mode to full DB-backed KG retrieval. The fallback to hardcoded templates remains for providers without learned patterns.

6.4 Context Selection & Injection
Each node retrieves context differently:
•	build_silver_api_model → schema + endpoint sections
•	understand_task / align_task_with_kg → workflow + pattern nodes + high-level docs
•	plan_integration_flow → endpoints, entities, events relevant to the task
•	attach_policies_and_patterns → rate limit, authentication, error-handling notes
Prompts keep:
•	Task description
•	Retrieved snippets
•	Output schema
all separated for clarity and stability.

6.5 Token, Latency & Cost Controls
Node behavior is controlled by configuration in config/models.yaml and internal limits.
For each node:
•	A token budget bounds the combined size of instructions, schemas, and retrieved context
•	A top_k value limits the number of retrieved chunks
•	A graph radius limits the depth of KG neighborhoods
Defaults:
•	Embeddings: text-embedding-3-small, 1536 dimensions, small batches per embed_spec_chunks call
•	Token budgets, top_k, and graph radius values match Appendix E and can change through configuration without code changes
LangSmith traces store per-node token counts, latency, and configuration tags such as model, role (planning/extraction/codegen), and embedding settings. These traces support later tuning of context limits and model sizes.

6.6 Context Precision & Effectiveness
The system treats context as a constrained and tunable resource.
Each node receives only the information it needs, controlled by explicit limits.
6.6.1 Bounding Context
Three bounding strategies:
Chunk Limit
•	Node-specific top_k (typically 5–15)
•	Extraction nodes use smaller top_k
•	Planning/code nodes use moderate top_k with schemas + examples + patterns
Graph-First Traversal
•	Retrieval starts from known anchors (provider_code, entity, or endpoint) and walks the KG graph using bounded breadth- or depth-first traversal (1–2 hops).
•	Embeddings rank and filter nodes inside the reached neighborhood; they do not replace graph traversal as the primary lookup mechanism.
Token Budgets
•	Each node has an upper-bound prompt size
•	Excess context is trimmed using heuristics:
o	duplicate content
o	low similarity
o	missing key terms
All bounds live in a configuration module and can be tuned without code changes.

6.6.2 Context Selection Policies
Specification Ingestion & Silver Extraction
•	Restrict retrieval to endpoint/schema/parameter/event sections
•	Exclude examples + long narratives
Task Understanding & Workflow Alignment
•	Prefer overview pages, key entity references, workflow descriptions in KG
Workflow Planning
•	Combine graph neighborhoods + small sets of relevant chunks
•	Avoid narrative content unrelated to call ordering or data dependencies
Policy Attachment
•	Focus on rate limiting, auth, error handling, pagination, logging
•	Prefer graph nodes tied to configuration fields or headers
Code Generation & Tests
Provide:
•	Planned IntegrationFlow graph
•	Endpoint definitions + field descriptions
•	Pattern snippets (auth, retry, pagination, logging)
Raw chunks are minimized—structured Silver/Gold is primary.

6.6.3 Measuring Context Usage
For each run/node:
•	Number of retrieved chunks
•	Estimated tokens sent to model
•	Presence of expected endpoints, entities, fields in retrieved chunks
These provide basic visibility into retrieval quality.

6.6.4 Effectiveness Signals
The system records simple signals linking context → output quality:
Reference Rate
Estimate how many retrieved chunks influenced output via:
•	Endpoint paths
•	Parameter names
•	Field names
Task-Level Coverage
For labeled tasks, confirm at least one relevant chunk (such as Stripe Checkout + PaymentIntent) is present during planning.
Sensitivity Testing
Vary top_k and graph radius to observe effects on:
•	Required endpoints/parameters in the plan
•	Planning errors (missing nodes, wrong edges)
•	Human review ratings
These guide future tuning of context retrieval.

7. Data Engineering Pipeline Design
(Shifted from warehouse ETL to integration-design pipelines.)
7.1 Ingestion Pipelines
The system uses a logical pipeline that runs inside the LangGraph workflow and writes to the database at three checkpoint nodes.

High-level sequence:
1.	plan_run sets high-level run metadata and repo usage flags.
2.	ingest_spec reads raw spec content into spec_documents and doc_chunks for all spec_refs. The node records a stable mapping from chunk indices to source documents in plan.
3.	detect_and_parse_spec and build_silver_api_model populate in-memory Silver drafts for HTTP and non-HTTP sources by constructing Schemas, fields, endpoints, entities, relationships, events, and section groupings.
4.	build_silver_file_model populates file-oriented Silver drafts when file-oriented inputs are present.
5.	embed_spec_chunks computes embeddings for doc_chunks.
6.	persist_silver_checkpoint writes Silver tables and backfills ids on in-memory objects, including SpecSection rows in spec_silver.spec_sections so section-level checks and retrieval queries can use this structure.
7.	Downstream nodes build Gold drafts, repo changes, and code artifacts.
8.	persist_gold_checkpoint writes Gold tables for the integration task and flow.
9.	persist_kg_learning writes learned KG templates and steps when the run produces a valid workflow.
10.	persist_run_outcome writes run_status, RAG metrics, and repo_meta rows for repo-aware runs.

Silver and Gold tables hold rows only after their checkpoint nodes commit. The SpecSection dataclass groups related source material and is part of WorkflowState. The persist_silver_checkpoint node writes SpecSection rows to spec_silver.spec_sections so section-level diagnostics and retrieval queries can use this structure for all supported source types, including non-HTTP specs that describe CSV or message schemas.

7.2 Transformation & Validation
The transformation and validation step checks that the Silver and Gold drafts are consistent, complete enough for code generation, and aligned with the task.
For Silver, the workflow inspects the spec_silver tables: endpoints, schemas, entities, entity_relationships, and events. It checks that key operations for the task have mapped request and response types, that important entities exist with stable identifiers, and that relationships between entities match the task description.
For Gold, the workflow inspects planned flows, modules, and cross-cutting policies. It checks that each planned step can be grounded in one or more endpoints or entities from Silver, that authentication and error handling appear in the flow where needed, and that rate limit or idempotency concerns are represented when relevant.

7.3 Storage & Serving
Storage:
•	Bronze-level spec files on disk when needed
•	Silver and Gold tables in Postgres schemas spec_silver and integration_gold
•	Spec chunk embeddings in spec_silver.spec_chunks
•	Run status in integration_gold.run_status
•	Knowledge graph tables in kg
Serving:
•	SQL access for analytics and inspection
•	Python helpers that read Silver, Gold, and KG tables
•	Query helpers keyed by provider and workflow, for example “all flows for a provider” or “all endpoints referenced by a workflow template”
run_status ties each run to its run_id and integration_task and links to LangSmith runs for trace inspection.

7.4 CI/CD for Data & Pipelines
•	DDL + schema migration scripts in Git
•	Prompt templates + node logic in Git
•	Regression runs on a fixed provider set after logic changes

8. Ops & Observability
8.1 Logs & Metrics
Each node writes structured logs with:
•	run_id, provider_code, spec_document_id, spec hash
•	Node name, start and end timestamps, status
•	Counts of endpoints, entities, steps, and code artifacts
•	Warnings and error messages
•	A compact JSON record of the node inputs and outputs, which includes key field counts, identifiers, and internal status flags in WorkflowState

The node logs form a coarse record of state transitions. LangSmith traces still hold the full prompt and model responses.

LangSmith captures traces for LLM and embedding calls. Traces include:
•	Node and model names
•	Token usage per call
•	Latency per node
•	Retrieval metrics such as number of chunks and graph radius used

RAG metrics measure retrieval quality on knowledge-graph queries. coverage_score is the fraction of expected KG entities that were actually retrieved by the RAG query. precision_score is the fraction of retrieved entities that were relevant to the task. Both scores are computed by LLM-based evaluation (prompt asks the model to classify each retrieved item as relevant or not) or, in test mode, by intersection with ground-truth entity lists.

Local logs back these traces on developer machines. Logs and traces provide a single view across the LangGraph workflow, database writes, and repo changes.

8.2 Run Status Tracking
Run status is stored in integration_gold.run_status and mirrored in WorkflowState.run_id for runs that write to the database.

Fields:
•	run_id (primary key)
•	task_id (foreign key to integration_tasks)
•	status: PENDING, RUNNING, SUCCESS, FAILED, or PARTIAL
•	started_at and finished_at timestamps
•	error_summary
•	langsmith_run_id

When IntegrationOptions.dry_run is False, persist_run_outcome writes or updates the row for a run after the Silver and Gold checkpoints complete. Status values are:
•	SUCCESS when the workflow completes and there are no errors
•	FAILED when a run stops before Gold writes and no durable artifacts exist for the task
•	PARTIAL when some writes succeed and errors remain

When IntegrationOptions.dry_run is True, persist_run_outcome does not write to integration_gold.run_status or related metrics tables. It still computes an in-memory status summary for the report, and the run_id field in WorkflowState may remain unset or may hold a transient identifier that does not appear in the database.

8.3 Stub Runbook
The v1 runbook provides guidance for triaging failures without a full support platform.
8.3.1 Common Failure Classes
1.	Specification Ingestion Failures
o	Network errors
o	Unsupported media types
o	HTML/PDF/OpenAPI parser failures
2.	Silver Extraction Failures
o	Empty/low endpoint counts
o	Missing schemas/fields
o	Mismatches between OpenAPI structures and Silver tables
3.	Task & Workflow Planning Failures
o	Cannot classify IntegrationTask
o	Missing start/terminal nodes
o	Cycles or disconnected flow components
4.	Code Generation Failures
o	LLM timeouts
o	Syntax or import errors
o	Unresolved symbols
5.	Validation Failures
o	Endpoint bindings reference nonexistent paths/methods
o	Required parameters without bindings
o	Generated tests cannot import modules

8.3.2 Triage Steps by Stage
If ingest_spec or detect_and_parse_spec fails:
•	Confirm reachable URL or path
•	Inspect raw content
•	Run standalone parser
•	Try minimal input (single file)
If build_silver_api_model is weak:
•	Inspect spec_sections, endpoints, schemas
•	Check chunk size/content-type splits
•	Turn on verbose logs
•	Add manual overrides for key endpoints
If understand_task or align_task_with_kg fails:
•	Simplify task description
•	Confirm domain workflows exist in KG
•	Bypass KG to test simpler planner
If plan_integration_flow is invalid:
•	Inspect orphan steps or missing links
•	Compare to hand-written flow
•	Increase top_k or graph radius
•	Add small constraints (e.g., “must include CreateCheckoutSession”)
If generate_code_and_tests fails:
•	Check LLM/log errors
•	Run static analysis
•	Regenerate single modules
•	Adjust templates/prompts
If validate_integration_design fails:
•	Inspect missing endpoints/fields
•	Fix mismatches in Silver/Gold
•	Rerun validation node only

8.3.3 Configuration Knobs
For most issues, adjust:
•	Model family/size per node
•	top_k, graph radius
•	Chunk size/content-type rules
•	LLM timeouts + retry settings
Each run records its configuration in the run report and in Langsmith tags or metadata so future analysis can compare cost and performance across settings.

8.3.4 When to Escalate
Record for each failing run:
•	run_id + spec version/hash
•	Provider + full task description
•	Failing node name
•	Key configuration values (model, top_k, radius, chunk size)
Attach relevant DB rows and generated files.
This produces a compact debug bundle for pair debugging or automation.

8.4 Environments and LLM Accounts
Local development can use a personal Anthropic or OpenAI account, or a team account, for LLM calls. Keys are stored in environment variables. Shared and hosted environments must use organization-managed accounts and secret storage (e.g., cloud secret manager). The code treats the LLM client as a pluggable dependency so that keys and providers change by configuration, not by code changes.

8.5 Agent Harness Alignment (v2)
This project can be understood as a domain-specific “agent harness” for integration design and code generation. The LangGraph workflow is the orchestrator, but several harness-like capabilities are implemented in the surrounding runtime. This section compares the current implementation to the “agent harness” capability set described in LangChain’s deepagents documentation, and identifies which ideas are worth adopting in v2.

8.5.1 What is already similar enough (do not chase naming)
Checkpointing and resume.
•	The workflow already supports checkpoint-based recovery and resume.
o	LangGraph native checkpoint savers (Postgres and SQLite fallbacks) are wired in `src/integration_coworker/graph/runtime.py` via `checkpointer_context`, `async_checkpointer_context`, and `get_checkpointer`.
o	The CLI includes “auto-resume” and “resume by run_id” features that detect interrupted runs and resume from persisted checkpoints (see `src/integration_coworker/cli.py`).
This covers the practical need that “cross-step state durability” provides in a harness, even if it is not expressed as a generic StoreBackend.

Filesystem access abstraction.
•	Repository inspection is already implemented behind a provider abstraction rather than ad-hoc file IO.
o	`repo_context_from_source` and `filesystem_repo_context_provider` delegate to `LocalRepoProvider` and a provider selector (`get_provider`) to build a `RepoSnapshot` (see `src/integration_coworker/repo/context.py`).
This is operationally similar to a “FilesystemBackend” concept, even though it is domain-shaped around repositories rather than generic virtual files.

8.5.2 Where adopting harness ideas is beneficial for v2
Large-result eviction → run artifact spooling.
•	The workflow produces large intermediate payloads (repo snapshots, extracted spec structures, long markdown contexts, generated code bundles). Without an explicit spooling policy, these payloads increase memory pressure, reduce debuggability, and can encourage over-logging.
•	v2 should add a run artifact store that writes large blobs to `data/runs/<run_id>/...` (or a DB-backed artifact table) and keeps only:
o	A stable pointer (path/key)
o	A short summary (counts + hashes)
in `WorkflowState`.
This is the closest analogue to deepagents “large tool result eviction”, but applied to node/state payloads rather than tool return values.

Human-in-the-loop (HITL) gates for destructive operations.
•	`dry_run` prevents writes, but does not provide controlled approval for writes.
•	v2 should introduce an approval gate before applying repo changes, especially for the long-running service target. This gate should be narrow and explicit (repo edits, DB writes in non-dry-run mode), rather than interrupting every operation.

Standardized repo IO boundary (“tool surface”).
•	v2 should formalize a small internal API for repo IO (list/glob/grep/read/write/edit) implemented on top of the existing provider layer.
•	The goal is not to become a general agent framework, but to create a single policy boundary for path validation, size limits, symlink handling, and audit logging.

Selective “subagent” delegation via subgraphs.
•	The deepagents “subagent” abstraction is best approximated in this codebase by explicitly bounded subgraphs (or dedicated nodes) with tight input/output contracts.
•	Recommended v2 uses:
o	Isolated review loops (policy review, code self-review, validation diagnosis) that return a single compact result
o	Parallelizable, specialized work (e.g., generate tests for one module) bounded by cost/concurrency limits
This preserves reproducibility (checkpoints + run artifacts) while avoiding the debugging complexity of unconstrained multi-agent spawning.

8.5.3 Practical v2 adoption checklist
The recommended “agent harness” alignment work in v2 is intentionally narrow and should be validated via existing gates:
•	Run artifact spooling: verify artifact pointers are stable per `run_id` and that strict docs and production validation runs remain debuggable.
•	HITL gates: ensure repo writes remain reviewable (dry-run + explicit apply step) and safe for the long-running service target.
•	Repo IO boundary: centralize path validation, size limits, and audit logging.
Verification hooks that already exist in-repo:
•	Docs gate: `make docs-verify` (strict MkDocs build + docs tests + docs audit).
•	Production-style validation: see scripts under `scripts/` (for example `scripts/test_production_*.py` and `scripts/verify_production_readiness.py`) and the validation matrix referenced by the docs.

9. Testing & Validation Plan
9.1 Unit & Integration Tests
Unit tests:
•	OpenAPI parsing helpers
•	Type normalization utilities
•	JSON path flattening
•	Schema, entity, and event extraction rules
•	Flow planning helpers and simple graph checks
Integration tests:
•	Node-level tests for ingest_spec, build_silver_api_model, embed_spec_chunks, plan_integration_flow, and generate_code_and_tests
•	Tests run against a test Postgres instance and stubbed or small LLM models
•	Tests for attach_repo_context, analyze_repo_layout, and apply_repo_integration_changes on a fixture repo that matches SUBATOMIC_MOCK_PROFILE
Generated code tests:
•	Checks that clients, workflows, and tests import and run under the shared runtime stack in Appendix I.

9.2 End-to-End Scenario Tests
For each provider and task:
•	Run the full workflow against test Postgres and a stub project directory or reference repo
•	Assert:
o	Successful run status in run_status for happy-path cases
o	Reasonable counts for endpoints, entities, and flow nodes
o	Presence of key steps in workflow_nodes and workflow_edges
o	All generated modules import without errors
o	Tests in code_artifacts match the patterns in Appendix I
Reference repo integration test:
•	Clone the reference repo that matches SUBATOMIC_MOCK_PROFILE
•	Run the workflow with repo_root and repo_profile set for that repo
•	Validate:
o	Files appear in the correct locations for clients, workflows, and tests
o	Router and settings files contain regenerated auto-generated blocks between markers as described in Appendix G
o	A basic smoke run passes, such as an HTTP route or CLI invocation of the generated integration

9.3 Load & Stress Testing
Scenarios:
•	Single large spec with many endpoints
•	Multiple concurrent runs
Checks:
•	Runtime
•	Memory usage
•	LLM call volume per node

9.4 Reprocessing
•	For new spec versions: insert new SpecDocument, rerun workflow, keep previous versions
•	For logic changes: rerun reference tasks and compare results

9.5 RAG- and Context-Specific Evaluation
Using Section 6.6 metrics + labeled tasks:
•	Retrieval precision & stability
•	Impact of context size on workflow quality
•	Effect of GraphRAG vs pure RAG

The system writes a compact summary of these metrics for each run into integration_gold.rag_eval_metrics. Each row links to run_status via run_id and records node name, basic retrieval counts, configuration parameters, and simple coverage and precision scores.

10. Rollout, Milestones & Project Plan
10.1 Phased Delivery

v2 Phases and Deliverables (Dec 2025)
This section defines an aggressive, calendar-anchored v2 delivery plan. It is written as an execution plan rather than an architectural specification. “Production-ready” in this context means the system meets the explicit production validation matrix, has a repeatable runbook, and passes the repo’s production-style validation scripts against Postgres and at least one real LLM provider.

Phase V2.0 — Stabilization and Freeze Candidate (Now → 2025-12-19)
Scope:
•	Stabilize the existing end-to-end workflow under production-like validation (Postgres + repo integration + real LLM calls)
•	Confirm correctness of checkpoint semantics, dry-run semantics, and repo change application across representative target repos
•	Lock down operational defaults for retries, caching, and logging boundaries (no secrets in logs; clear failure modes)
•	Document the supported command surfaces (CLI + Python entrypoint) and produce one canonical “production run recipe”
Deliverables:
•	A “freeze candidate” build on the main v2 branch
•	A minimal runbook with failure triage flow (node-level diagnosis + DB/LLM diagnostics)
•	Production validation scripts pass on at least one representative provider+task set

Checkpoint Meeting — 2025-12-19
Agenda and exit criteria:
•	Review production validation results and bug log since the last checkpoint
•	Agree on any remaining must-fix items for the final milestone
•	Declare “feature freeze” for the Dec 22 push (only fixes and documentation allowed after this meeting)

Phase V2.1 — Hardening Sprint and Final Readiness (2025-12-20 → 2025-12-22)
Scope:
•	Resolve all must-fix bugs identified at the Dec 19 checkpoint
•	Harden deterministic replay and run recovery workflows sufficiently for regression and incident triage (LLM record/replay + checkpoint recovery surfaces)
•	Complete production readiness documentation (validation matrix, runbook, and operator-facing “known failure classes”)
•	Confirm reproducibility of the production validation suite on a clean environment
Deliverables:
•	Production-ready v2 release candidate (tagged)
•	Passing production validation suite with archived artifacts (logs, run reports, DB schema validation)
•	A minimal “release checklist” that can be repeated for future releases

FINAL Milestone — 2025-12-22 (Production Ready)
Acceptance criteria:
•	All production validation gates pass (per PRODUCTION_VALIDATION_MATRIX and automation scripts)
•	The system can be executed reliably end-to-end with Postgres + a real LLM provider using documented environment setup
•	A run produces: Silver + Gold persistence, repo change set (when enabled), run report, and an auditable error story on failure
•	Docs gate passes (mkdocs strict build + docs tests + docs audit)

10.0.1 v2 Refinements and Future Work
This section records targeted refinements that extend the initial local-process milestone into a more complete v2 system. The refinements focus on repeatability, broader applicability across repositories and languages, and more explicit operational boundaries.

Multi-language support (v2)
The current workflow and artifact model are Python-first. In v2, multi-language support is treated as an explicit product surface with a single internal contract:
•	A language-neutral IntegrationFlow and EndpointBinding layer remains the canonical representation of the designed integration.
•	Language-specific generators render that canonical model into artifacts for the target runtime (for example Python, TypeScript, or Go).

Planned refinements:
•	Introduce a LanguageProfile that selects templates, runtime dependencies, formatting/linting entrypoints, and repository wiring conventions.
•	Refactor generate_code_and_tests into (a) language-neutral planning outputs and (b) per-language rendering passes, so evaluation can compare multiple languages for the same flow.
•	Extend validation to include language-specific compile/typecheck gates (for example mypy/ruff for Python, tsc/eslint for TypeScript) while reusing the same semantic validation rules for EndpointBinding consistency.
•	Add a minimal multi-language test harness that executes “import/compile + smoke test” per generated module set and stores results in the run report.

File ingestion wiring (v2)
The system currently treats “file-oriented inputs” as first-class spec sources at the Silver model level, but the end-to-end ingestion and downstream code generation are not yet fully wired as a default path.
In v2, file ingestion work focuses on a complete path from input source → Silver file records → task planning → code artifacts.

Planned refinements:
•	Adopt a SpecSource routing layer for file-oriented inputs and align extraction, validation, and embedding behavior with the plan in `archive/docs/reports/FILE_INTEGRATION_V1_PLAN.md`.
•	Wire build_silver_file_model so it consistently populates file_specs, file_fields, and record-layout metadata when the source provides sufficient structure.
•	Extend generate_code_and_tests with file-specific templates (parsers, validators, record mappers) and a test suite that runs on fixture files.
•	Extend validate_integration_design to treat file schemas as binding targets alongside HTTP endpoints.

Other v2 refinements
Beyond language expansion and file ingestion wiring, the v2 design calls out additional refinements that increase reliability and operational leverage:
•	Long-running service deployment. Formalize a service runtime around the same LangGraph workflow (see Section 1.3) with run admission control, concurrency limits, and per-run workspace isolation.
•	Determinism and replay.
o	Implemented: LLM-level record/replay exists for regression-oriented runs.
  - The synchronous LLM client supports REAL/MOCK/RECORD/REPLAY modes and persists request/response pairs under `.llm_recordings` keyed by a deterministic SHA-256 hash of (model, system_prompt, prompt). See `src/integration_coworker/llm/client.py` (“Record/Replay Support (LLM-003)”).
  - This mechanism enables deterministic re-execution of LLM calls for a fixed prompt + hardened system prompt, but it does not (yet) guarantee end-to-end determinism of the full workflow.
o	Partially implemented: workflow recovery/checkpoint resume is present.
  - The project includes a workflow checkpoint persistence module with `save_checkpoint`/`load_checkpoint` and size-limiting exclusions for large spec fields. See `src/integration_coworker/persistence/checkpoints.py`.
o	Planned: elevate replay to a first-class run-level feature by persisting (a) prompt inputs, (b) retrieval identifiers, and (c) stable artifact hashes per node, so regressions can be localized to specific nodes/config changes.
•	Evaluation hardening.
o	Implemented (minimal): the schema includes per-run evaluation storage for RAG-oriented metrics.
  - `persist_run_outcome` writes to `integration_gold.rag_eval_metrics` (currently as a basic summary row keyed by `run_id`). See `src/integration_coworker/graph/nodes/persist_run_outcome.py`.
o	Planned: expand labeled tasks and introduce regression thresholds over (a) semantic policy validation and (b) language-specific build/typecheck gates.
•	Policy and safety refinement.
o	Implemented: a semantic content policy layer flags insecure credential usage patterns and data leakage patterns in generated code, with a test-oriented whitelist. See `src/integration_coworker/llm/content_policy.py`.
o	Implemented: policy attachment can include redaction configuration (for example `redact_fields`) as part of policy drafting in `attach_policies_and_patterns`. See `src/integration_coworker/graph/nodes/attach_policies_and_patterns.py` (redaction fields).
o	Planned: treat policy attachment as a versioned layer (policy versioning / policy bundles) so policies can be upgraded without changing flow semantics, and make redaction guarantees enforceable end-to-end (logs, reports, persisted artifacts).
•	Repo profile expansion.
o	Implemented: RepoProfile coverage extends beyond a single reference structure.
  - Generic profiles exist for multiple languages (Python, TypeScript, JavaScript, Go, Java, Ruby, C#) and there is additional framework detection and backward-compatible profiles. See `src/integration_coworker/repo/profiles.py`.
o	Planned: deepen repo support for additional monorepo layouts and enforce stricter patch-application constraints in repo wiring.
•	Incremental updates.
o	Partially implemented: select nodes support idempotent or cache-hydration fast paths.
  - The Silver API model builder has a cache-hydration path (`state.cache_hit=True`) that hydrates model entities from the database instead of reparsing. See `src/integration_coworker/graph/nodes/build_silver_api_model.py` (“V1.1 Spec Caching (FT-001)”).
o	Planned: spec diffs and partial regeneration so a run can update a subset of endpoints/bindings/code artifacts without recomputing the entire integration.

Notes on legacy milestones
Earlier phase numbering and dates referenced the initial local-process milestone (v1). Those are retained in this document only as historical context; the v2 plan above is the active delivery schedule.

10.3 Risk Management
Key Risks and Mitigations

LLM Output Instability
•	Strong output schemas
•	Low-temperature settings
•	Validation and post-processing

Spec Diversity and Gaps
•	Prompts resilient to missing schemas
•	Accept partial Silver with flags
•	Record structured diagnostics for weak specs

Cost and Latency
•	Strict context limits
•	Small models for extraction

Data and Model Complexity
•	Treat the current Silver, Gold, and KG schemas as complete for v1
•	Add new fields later in a strictly additive way that keeps existing rows and code valid

Security and Code Correctness
•	Shared auth and logging templates
•	Standard CI and test routing

Value Gap vs Raw LLM Use
•	Focus on:
o	Pattern consistency
o	Security and logging baselines
o	Reuse across tasks and teams