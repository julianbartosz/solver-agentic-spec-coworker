# System Patterns & Architecture

This file gives Copilot the **core architecture, patterns, and constraints** that should stay stable over time.

Implementation details and current tasks live in other prompt files.

---

## 1. High-Level Architecture

Core component: **Agentic Integration Co-Worker (LangGraph workflow)**

- **Inputs:**
  - `spec_refs`
  - `task_description`
  - Optional:
    - `provider_code`
    - `repo_root`
    - `repo_profile`
    - additional options

- **Outputs:**
  - Silver and Gold records in Postgres
  - Generated code, config, tests
  - Optional repo wiring changes
  - A human-readable run report

**Environment (v1):**

- Local dev:
  - Python 3.11+
  - LangGraph + LLM libraries
  - Dockerized Postgres with pgvector
  - Local clone of a Subatomic-style mock service repo
- Hosted (Subatomic GCP):
  - Managed Postgres + pgvector for Silver/Gold
  - Access to reference service repo or internal repo loaders
  - Langsmith for traces, metrics, and cost tracking

---

## 2. Medallion Data Architecture

**Bronze**

- Raw spec documents:
  - HTML, Markdown, OpenAPI JSON/YAML, PDFs
- Raw prompts and task descriptions
- Files stored on disk or object store; metadata in Postgres

**Silver**

- Normalized API surface + embeddings
- Postgres schema: `spec_silver`
- Core tables (logical model):
  - `source_systems`
  - `spec_documents`
  - `spec_sections`
  - `endpoints`
  - `endpoint_parameters`
  - `schemas`
  - `fields`
  - `entities`
  - `entity_relationships`
  - `events`
  - Embedding tables for chunks and summaries

**Gold**

- Integration intent and workflow design
- Postgres schema: `integration_gold`
- Core tables (logical model):
  - `integration_tasks`
  - `workflow_templates`
  - `integration_flow_nodes`
  - `integration_flow_edges`
  - `endpoint_bindings`
  - `policies`
  - `code_artifacts`

**Graph & Vector views**

- Graph:
  - Workflow graphs (nodes/edges)
  - Entity, endpoint, field, event nodes
  - Pattern nodes (pagination, webhooks, retries, logging, etc.)
- Vector:
  - Chunks and short summaries for:
    - Spec sections
    - Endpoints, entities, events, patterns
  - Stored using `pgvector` with embedding dimension:
    - `EMBEDDING_DIM = 1536` (must match the embedding model and DB column type)

---

## 3. LangGraph Workflow Topology

**Node order (default repo-aware run):**

1. `plan_run`
2. `ingest_spec`
3. `detect_and_parse_spec`
4. `build_silver_api_model`
5. `understand_task`
6. `align_task_with_kg`
7. `plan_integration_flow`
8. `attach_policies_and_patterns`
9. `generate_code_and_tests`
10. `analyze_repo_layout`
11. `apply_repo_integration_changes`
12. `validate_integration_design`
13. `persist_results`
14. `build_report`

**Repo-less runs:**

- Skip `analyze_repo_layout` and `apply_repo_integration_changes`.
- Flow:
  - … → `generate_code_and_tests` → `validate_integration_design` → `persist_results` → `build_report`

**Entry / Terminal:**

- Entry node: `plan_run`
- Terminal node: `build_report`
- Failures route to a dedicated error node, but partial artifacts should still be written.

**WorkflowState (conceptual fields):**

- Context:
  - `source_refs`, `spec_refs`, `task_description`
- Spec content:
  - `doc_chunks`, `openapi_spec`
- Silver drafts:
  - `endpoints`, `schemas`, `fields`, `entities`, `relationships`, `events`
- Gold drafts:
  - `integration_task`, `workflow_templates`
  - `flow_nodes`, `flow_edges`
  - `endpoint_bindings`, `policies`
  - `code_artifacts`
- Control:
  - `plan`
  - `completed_steps`
  - `errors`
  - `persisted_ids` (e.g. spec_document_id, integration_task_id)
  - `report`

Implementation should use a typed model (e.g. `@dataclass` or Pydantic).

---

## 4. RAG & GraphRAG Patterns

**Context sources:**

- Unstructured spec text:
  - HTML, Markdown, PDFs
- Structured content:
  - OpenAPI / JSON Schema fragments
- Derived Silver structures:
  - Entities, endpoints, fields, events, relationships
- Gold content:
  - Workflows, templates, endpoint bindings, policies, patterns

**Ingestion flow:**

1. Insert `SourceSystem` and `SpecDocument`.
2. Store raw spec file(s) on disk; metadata in Postgres.
3. `ingest_spec`:
   - Fetch raw content (URL or file)
   - Convert to text
   - Chunk into `SpecSections` with metadata
4. `build_silver_api_model`:
   - Parse OpenAPI / schemas where present
   - Populate Silver tables
5. Generate embeddings for relevant chunks and structured summaries.

**Indexing strategy:**

- Vector index contains:
  - Text chunks from `spec_sections`
  - Short summaries of endpoints, entities, events, patterns
- Each entry carries metadata like:
  - Provider, document id, endpoint, entity, section type

**Retrieval modes:**

- Metadata filters by provider / document / endpoint / entity
- Vector search inside filtered sets
- GraphRAG:
  - Query small graph neighborhoods:
    - Given `IntegrationTask` → candidate `WorkflowTemplates` and steps
    - Given step → bound endpoints / entities
    - Given endpoint → related patterns (pagination, retries, auth, etc.)

---

## 5. Prompting Strategies

Prompting patterns (encoded in YAML archetypes):

- **Planning nodes** (`plan_run`, `understand_task`, `align_task_with_kg`, `plan_integration_flow`, `attach_policies_and_patterns`):
  - Use chain-of-thought prompts with explicit reasoning steps.
  - Support multiple samples + a small selector for self-consistency.
  - Branching / tree-of-thought for ambiguous tasks with tight limits:
    - `planning.num_samples`
    - `planning.max_branches`
- **Extraction nodes** (`ingest_spec` helpers, `detect_and_parse_spec`, `build_silver_api_model`):
  - Constrained prompts that emit strict JSON matching Silver schema.
  - Low temperature, small models, bounded outputs.
- **Code generation** (`generate_code_and_tests`):
  - Prompts include:
    - IntegrationTask + IntegrationFlow graph
    - Endpoint definitions & key schemas
    - Policy patterns (auth, retries, logging, pagination)
    - Repo profile info for repo-aware runs
  - May generate multiple candidates per module:
    - `codegen.num_candidates`
    - Selector / evaluator prompt to choose one.
- **Validation & reporting** (`validate_integration_design`, `build_report`):
  - Structured prompts that compare plans, code, and Silver/Gold models.
  - Focus on simple, reliable checks.

**Config storage:**

- Agent / node configuration lives in YAML archetype files in Git.
- These define:
  - Model selection + fallbacks
  - Tools / APIs per node
  - Token budgets and temperatures
  - Retrieval parameters (`top_k`, graph radius)
  - Parallelism / sampling settings
- Langsmith tags capture which settings were active for each run.

---

## 6. Parallelization & Performance

**Goals:**

- End-to-end run in **< 5 minutes**
- Keep LLM cost low (prefer small models, small `top_k`, and tight prompts)

**Bounded parallelism:**

- Planning samples:
  - Small number of parallel samples per planning node:
    - `planning.num_samples`, `planning.max_parallel_samples`
- Code generation:
  - Parallelization across modules and per-module candidates:
    - `codegen.max_parallel_modules`
    - `codegen.num_candidates`
- Embeddings & retrieval:
  - Batched / parallel embeddings:
    - `ingestion.max_parallel_embeddings`
  - Parallel retrieval calls when needed, with small concurrency caps.

**Measurement (via Langsmith & logs):**

- Per-node:
  - Latency
  - Token counts
  - LLM call counts
- Per-run:
  - Total cost estimate
  - Context sizes (chunks, tokens)
  - Parallelism settings used

These metrics guide tuning of:
- Model sizes
- Retrieval bounds
- Parallelism settings

---

## 7. Context Bounding & Selection

Context is treated as a limited, tunable resource.

**Bounding strategies:**

- Chunk limit:
  - Node-specific `top_k`:
    - Extraction nodes: smaller (`~5`)
    - Planning / codegen: moderate (`~5–15`)
- Graph radius:
  - 1–2 hops around:
    - Entity
    - WorkflowTemplate
    - Endpoint
- Token budgets:
  - Hard upper bounds per node
  - Trimming heuristics:
    - Remove duplicates
    - Drop low-similarity content
    - Prioritize chunks with required endpoints / entities / fields

**Selection policies:**

- For ingestion / Silver extraction:
  - Prefer endpoint / schema / parameter / event sections
  - Avoid long narrative or marketing text
- For task understanding & workflow alignment:
  - Prefer high-level overviews, workflow pages, domain docs, and KG nodes
- For workflow planning:
  - Combine graph neighborhoods with a small set of highly relevant text chunks
- For policy attachment:
  - Focus on:
    - Auth mechanisms
    - Rate limiting
    - Error handling
    - Pagination
    - Logging / redaction patterns
- For code generation:
  - Use structured Silver/Gold data as the primary source
  - Keep raw text chunks minimal

**Context effectiveness signals:**

- Number and type of retrieved chunks per node
- Whether the required endpoints / entities / fields appear
- Effects of varying `top_k` and graph radius on:
  - Coverage
  - Planning errors (missing nodes, wrong edges)
  - Human review ratings

---

## 8. Non-Functional Requirements

**Performance & scalability:**

- Run time target: **< 5 minutes** per run
- Handle:
  - 10–50 providers
  - Specs up to ~20 MB

**Reliability:**

- Write partial artifacts on failure (e.g., some Silver/Gold data even if later nodes fail)
- Maintain a `run_status` table with:
  - Status: `PENDING | RUNNING | SUCCESS | FAILED | PARTIAL`
  - Timestamps
  - Error summaries
  - Link to Langsmith run id

**Cost:**

- Use:
  - Small models for structured extraction
  - Larger models only where planning or codegen needs deeper reasoning
- Reuse embeddings across runs when specs haven’t changed
- Keep context small and targeted

**Security:**

- Credentials via environment variables or central secret manager
- Avoid logging sensitive response payloads
- Standardized auth and logging patterns in generated code

---

## 9. Ops, Observability & Testing

**Logging & metrics:**

- Each node logs:
  - `run_id`, provider code, spec_document_id, spec hash
  - Node name, start/end timestamps, status
  - Core counts: endpoints, entities, steps, code artifacts
  - Warnings / errors
- Langsmith:
  - Traces
  - Token + cost estimates
  - Retrieval / context metrics

**Testing levels:**

- Unit tests:
  - OpenAPI parsing helpers
  - Type normalization & JSON path flattening
  - Mapping endpoints / entities to workflows
- Integration tests (node-level):
  - `ingest_spec`
  - `build_silver_api_model`
  - `plan_integration_flow`
  - `generate_code_and_tests`
- End-to-end tests:
  - Full run for selected provider/tasks against test Postgres and stub project directory
  - Validate:
    - Successful run
    - Reasonable counts for objects
    - Presence of key steps
    - Importable generated modules
    - Tests pass with stubbed network clients
- Repo integration tests:
  - Run against reference repo root + profile
  - Ensure:
    - Files appear in correct locations
    - Registration hooks (routes/services/jobs) are present
    - Basic smoke test (HTTP route or CLI) succeeds

---

Use this file to keep architectural decisions and patterns **stable**.  
Dynamic status, current tasks, and decisions should be updated in other prompt files.
