# Prompt & Context Engineering README

This doc explains how prompts, models, and context are structured for the **Agentic API Integration Designer & Code Generator**.

It’s aimed at engineers who need to:

* Understand how each LangGraph node uses LLMs and RAG.
* Safely change prompts, models, or retrieval settings.
* Debug bad outputs with LangSmith traces.

---

## 1. Big Picture

We treat LLM behavior as **configurable infrastructure**:

* **Prompt families**, not one-off strings:

  * Planning / reasoning
  * Extraction (Silver)
  * Code & test generation
  * Validation & reporting

* **Context is constrained**:

  * Explicit top-k per node
  * Bounded graph radius
  * Strict token budgets

* **All knobs live in YAML**:

  * Models, temperatures, max tokens
  * # of planning samples, codegen candidates
  * Retrieval & parallelism settings

* **Everything is observable**:

  * LangSmith traces tagged with model, role, and config values
  * RAG metrics (chunks retrieved, presence of key endpoints/entities) per node

---

## 2. Config Files & Layout

### 2.1 Model & embedding config

**File:** `config/models.yaml`

```yaml
llm:
  planning:
    model: gpt-4.1
    temperature: 0.2
    max_tokens: 2048
    num_samples: 2
    max_parallel_samples: 2

  extraction:
    model: gpt-4o-mini
    temperature: 0.0
    max_tokens: 1024

  codegen:
    model: gpt-4.1
    temperature: 0.15
    max_tokens: 4096
    num_candidates: 2
    max_parallel_modules: 3

embeddings:
  model: text-embedding-3-small
  dimensions: 1536
  max_parallel_embeddings: 4
```

**Rules:**

* `embeddings.dimensions` **must** match:

  * The embedding model.
  * `VECTOR(1536)` in `spec_silver.spec_chunks.embedding`.
* LangGraph nodes **read** from this config; don’t hard-code models/temps in code.
* LangSmith traces should tag:

  * `llm.model`, `llm.role` (`planning`, `extraction`, `codegen`)
  * `embeddings.model`, `embeddings.dimensions`

### 2.2 Prompt archetypes (per node family)

(Names may vary slightly; pattern should be clear.)

Examples:

* `config/prompts/planning.yaml`
* `config/prompts/extraction.yaml`
* `config/prompts/codegen.yaml`
* `config/prompts/validation.yaml`

Each file defines:

* System message(s)
* Output schemas / JSON shapes
* Retrieval policy hints (what to fetch, how much)
* Node-specific overrides, if needed

Nodes load their “archetype” by name rather than embedding prompt strings directly in code.

---

## 3. Node Categories & Prompt Strategies

### 3.1 Planning & reasoning nodes

Nodes:

* `plan_run`
* `understand_task`
* `align_task_with_kg`
* `plan_integration_flow`
* `attach_policies_and_patterns`

**Prompt style:**

* Chain-of-thought / reasoning oriented:

  * Reason in steps, then emit a **final, structured object** (plan, graph, constraints).
* Multiple samples per task:

  * Config: `planning.num_samples`, `planning.max_parallel_samples`.
* Optional light tree-of-thought:

  * Config: `planning.max_branches`.
  * Used to branch a small number of candidate flows, then prune.

**Input context:**

* Task description, provider code.
* Silver drafts (entities, endpoints, events).
* KG snippets (workflow templates, steps, step–endpoint bindings).
* Strict token budget and small top-k from vector/graph retrieval.

**Output shape examples:**

* `plan_run`: `plan.use_repo`, `plan.provider_code`, `plan.primary_spec_ref`
* `understand_task`: `IntegrationTask` draft (task slug, entities, constraints)
* `plan_integration_flow`: `IntegrationFlowNode`s + `IntegrationFlowEdge`s

> When editing: keep **output JSON/dict schemas stable**; other nodes rely on these.

---

### 3.2 Extraction nodes (Silver)

Nodes:

* `ingest_spec` helpers
* `detect_and_parse_spec`
* `build_silver_api_model`

**Goal:** Map specs → Silver layer:

* `schemas`, `fields`
* `entities`, `relationships`
* `events`
* `endpoints`, `endpoint_parameters`

**Prompt style:**

* Strict, schema-bound JSON responses.
* No prose; only structured data.
* Low-temperature, small model:

  * `llm.extraction.model = gpt-4o-mini`
  * `temperature = 0.0`

**Context rules:**

* Retrieval only from:

  * Endpoint/schema/parameter/event sections.
* Exclude long examples and narratives where possible.
* top-k small (e.g., 5–10) for spec chunks.

**Important:**
Extraction prompts should treat the Silver schema as **the source of truth**. Any change to `domain.models` or DB schema → update extraction prompts and tests.

---

### 3.3 Code & test generation node

Node:

* `generate_code_and_tests`

**Inputs:**

* `IntegrationTask`
* Flow graph (nodes + edges)
* Endpoints + key schema fields
* Policies (auth, retries, pagination, logging, rate limits, idempotency)
* Repo context:

  * `RepoProfile` (directories, router/settings markers)
  * `repo_markdown_context` (Markdown export via `MockedGithubRepoRetriever`)

**Prompt style:**

* Context-rich but **highly prescriptive**.
* Describes:

  * Required runtime stack:

    * `httpx` via a shared `IntegrationHttpClient`
    * Shared exception hierarchy (`IntegrationError`, `TransientIntegrationError`, `AuthIntegrationError`)
    * Standard Python logging (module-level loggers, no global config)
  * Required test stack:

    * `pytest`
    * Shared fixtures / httpx mocks
  * Repository layout:

    * Where to put clients / workflows / tests
    * How to hook routers and settings with the integration markers

**Sampling strategy:**

* Multiple candidates per module (`codegen.num_candidates`).
* Parallelism per module (`codegen.max_parallel_modules`).
* Simple evaluator (prompt or static checks) to choose:

  * Syntax-correct
  * Stack-compliant
  * Matches expected file structure and names

**Hard “do / don’t” embedded in prompts:**

* **Do** use `IntegrationHttpClient` wrapper, not `requests`.
* **Do** raise shared exception types.
* **Do** generate pytest-based, offline tests.
* **Don’t** define new HTTP client stacks per integration.
* **Don’t** configure logging globally.

---

### 3.4 Validation & reporting nodes

Nodes:

* `validate_integration_design`
* `build_report`

**Prompt style:**

* Compact, checklist-like prompts.
* Only ask for:

  * Validation findings in structured form (fields like `severity`, `message`, `location`).
  * A bounded human-readable summary for reports.

**Context:**

* All Gold drafts (task, flow, bindings, policies, code artifacts).
* Optional repo changes summary.

**Design intent:**

* Prompts stay simple → easier to reason about & cheaper.
* Validation logic is mostly explicit and deterministic, with LLM providing:

  * Explanations
  * Some fuzzy checks (e.g., “Is this flow missing an obvious step given the task description?”)

---

## 4. RAG & GraphRAG Strategy

### 4.1 Context sources

* **Unstructured**:

  * Spec text chunks (`spec_silver.spec_chunks`)
  * Repo Markdown context (from `MockedGithubRepoRetriever`)
* **Structured (Silver)**:

  * Endpoints, parameters
  * Schemas, fields
  * Entities, relationships
  * Events
* **Knowledge Graph (KG)**:

  * Workflow templates (`kg_workflow_templates`)
  * Workflow steps (`kg_workflow_steps`)
  * Step–endpoint bindings (`kg_step_bindings`)
* **Gold**:

  * Existing workflows/policies for pattern reuse

### 4.2 Retrieval controls

Per node, we define:

* **top-k**: how many chunks we’re allowed to retrieve.
* **graph radius**: how far we can walk from:

  * Entity
  * Endpoint
  * WorkflowTemplate
* **token budget**: max prompt size; excess context is trimmed via:

  * Removing duplicates
  * Dropping low-similarity content
  * Preferring schemas/fields over narratives

Examples:

* `build_silver_api_model`:

  * Filter to spec sections that look like OpenAPI structures.
* `understand_task` / `align_task_with_kg`:

  * Focus on overview pages, KG templates, and example flows.
* `attach_policies_and_patterns`:

  * Only pull rate limit/auth/pagination/logging content.

---

## 5. Parallelism & Cost Controls

### 5.1 Planning

* Run several plan samples in parallel:

  * Config: `planning.num_samples`, `planning.max_parallel_samples`.
* Optional tree-of-thought branching:

  * Config: `planning.max_branches`.
* Selector prompt picks a single plan for the run.

### 5.2 Code generation

* Generate multiple modules concurrently:

  * `codegen.max_parallel_modules`.
* For some modules, generate multiple candidates concurrently:

  * `codegen.num_candidates`.

### 5.3 Embeddings

* Chunk specs and embed in parallel:

  * `embeddings.max_parallel_embeddings`.

All parallelism decisions are tagged into LangSmith so we can evaluate:

* Total cost per run
* Node-level latency
* Impact on quality

---

## 6. How to Change Prompts or Models Safely

1. **Decide which family you are changing**

   * Planning? Extraction? Codegen? Validation?
   * Don’t mix concerns in a single PR if you can avoid it.

2. **Update YAML, not code strings**

   * For models, temps, and token limits: edit `config/models.yaml`.
   * For content/templates: edit the right `config/prompts/*.yaml`.

3. **Keep output schemas stable**

   * If you must change the shape of JSON outputs:

     * Update `domain.models` or `graph/state.py` if necessary.
     * Update the relevant tests.
     * Update any downstream node that consumes that output.

4. **Run reference tasks**

   * Run the full workflow against a known set of providers/tasks.
   * Check:

     * Run status (SUCCESS vs PARTIAL/FAILED).
     * Key counts (endpoints, entities, flow nodes).
     * That router + settings changes look reasonable for the mock repo.

5. **Inspect LangSmith traces**

   * Confirm the right models are used for the right roles.
   * Compare token usage and latency.
   * Verify that retrieval context includes the expected endpoints/entities.

---

## 7. Quick FAQ for Engineers

**Q: Where do I change which model is used for codegen?**
A: `config/models.yaml` under `llm.codegen.model`. Don’t hard-code in node code.

**Q: How do I tighten or loosen context size for a node?**
A: Adjust that node’s retrieval policy (top-k, graph radius, token budget) in its prompt archetype or retrieval config. Keep budgets per-node, not global.

**Q: I added a new field to the Silver schema. What do I update?**
A:

1. `spec_silver` DDL + migrations.
2. `domain.models` for the corresponding dataclass.
3. Extraction prompts for `build_silver_api_model`.
4. Any code that maps OpenAPI → Silver objects.
5. Tests for extraction and persistence.

**Q: I want better code style in generated modules. Where do I start?**
A:

* Update codegen prompts in `config/prompts/codegen.yaml`:

  * Add examples.
  * Clarify patterns (e.g., naming, error handling).
* Run reference tasks and inspect diff quality in the mock repo.
* Avoid fighting runtime stack constraints; adjust those only if the stack itself changes.

---
