# Architecture Audit Question Set (v2)

> **Purpose**: A structured question map for comprehensively auditing this repository and rewriting `docs/ARCHITECTURE.md` to accurately reflect the as-built implementation.
>
> **Date**: December 9, 2025  
> **Status**: Question set ready for audit execution
> **Methodology**: Grounded in architecture description best practices (views, quality attributes, constraints, interfaces, risks)

---

## Architecture Views

A good `ARCHITECTURE.md` for this project should cover the following views, tailored to what exists in this codebase:

- **Runtime / Workflow View** — How the LangGraph StateGraph executes: nodes, edges, control flow, checkpointing, error routing, and parallelism. The "moving parts" of a run.
- **Logical / Module View** — Package structure, module boundaries, and component responsibilities. What code lives where and why.
- **Data / State View** — The Bronze → Silver → Gold medallion model, `WorkflowState` structure, domain models, and data transformations between nodes.
- **Persistence / Storage View** — Database schema (Postgres + pgvector), SQLite fallback, streaming for large specs, connection pooling, and checkpoint tables.
- **LLM / External Services View** — Archetype-based model configuration, multi-provider support (OpenAI, Anthropic, Google), caching (Redis), retry logic, and mock modes.
- **Operational / CLI-API View** — User-facing entrypoints (`cli.py`, `api/entrypoint.py`), `IntegrationOptions`, output formats, resumption, and demo mode.
- **Configuration / Environment View** — Settings hierarchy (env vars → archetypes → defaults), feature flags, and how config changes affect runtime behavior.
- **Security / Safety View** — Prompt injection mitigation, LLM sanitization, codegen security validation, secrets handling, and content policies.
- **Observability View** — LangSmith tracing, logging configuration, token usage tracking, `persist_run_outcome`, and run inspection tooling.
- **Quality Attributes View** — Reliability (degraded mode, fallbacks), performance (parallelism, streaming), maintainability (archetype patterns), and stated bounds/limits.

---

## Questions by View

### View: Runtime / Workflow

*How the LangGraph StateGraph executes—nodes, edges, control flow, checkpointing, error routing, and parallelism.*

1. **[P0]** Which function builds the `StateGraph` and what is the exact sequence of `add_node()` and `add_edge()` calls? (File: `runtime.py`)
2. **[P0]** What is the complete ordered list of nodes for a successful run, from entry point to `END`? How does this differ for repo-aware vs. non-repo runs?
3. **[P0]** What conditional edges exist, what functions evaluate them, and what branches do they create? (e.g., `should_run_repo_nodes`, `check_for_errors_after_validation`)
4. **[P0]** How does checkpoint recovery work? What does `get_checkpointer()` return, when is state persisted, and how does `_should_skip_node()` detect completed nodes during resume?
5. **[P0]** What is the contract of `timed_node()` decorator—what does it wrap, what metadata does it record, and when does it save checkpoints?
6. **[P1]** What is `NODE_DEPENDENCIES` and how does `get_dependent_nodes()` / `get_skip_cascade()` implement dependency-aware skip?
7. **[P1]** How does the parallel graph (`build_parallel_graph`) differ from the sequential graph? What is `sync_embed_task` and when is `PARALLEL_WORKFLOW` enabled?
8. **[P1]** What is `WORKFLOW_NODE_ORDER` and how is it used for recovery and skip cascade calculations?
9. **[P1]** How does `run_workflow()` set up run context for LangSmith tracing and token tracking?
10. **[P2]** Is there any timeout or cancellation mechanism for long-running nodes? What happens if a node hangs indefinitely?
11. **[P2]** How does `run_from_node()` differ from `run_workflow()`? Is it still used or deprecated?

### View: Logical / Module

*Package structure, module boundaries, and component responsibilities.*

1. **[P0]** What are the top-level packages under `src/integration_coworker/` and what is the single responsibility of each?
2. **[P0]** How do `api/`, `graph/`, `domain/`, `persistence/`, `llm/`, `config/`, and `repo/` relate to each other in terms of import dependencies?
3. **[P1]** What is the role of each file in `graph/nodes/`? Can node responsibilities be grouped into phases (ingestion, silver, gold, repo, finalization)?
4. **[P1]** What is the difference between `domain/models.py` and `graph/state.py`? Which is the source of truth for domain objects?
5. **[P1]** What packages are considered "internal" vs. "public API"? Is `api/entrypoint.py` the only official public interface?
6. **[P2]** Are there any circular import risks or known import order sensitivities?
7. **[P2]** What is the purpose of `runtime/`, `feedback/`, `retrieval/`, `spec/`, `parsers/`, `kg/`, and `ui/` subdirectories?

### View: Data / State

*The Bronze → Silver → Gold medallion model, `WorkflowState` structure, and data transformations.*

1. **[P0]** What are the exact field groupings in `WorkflowState` for Bronze, Silver, and Gold layers? (Cross-reference with ARCHITECTURE.md claims)
2. **[P0]** For each major field in `WorkflowState`, which node populates it and which nodes consume it?
3. **[P0]** What dataclasses in `domain/models.py` represent Silver layer objects vs. Gold layer objects?
4. **[P0]** How do `SourceRef`, `SourceSystem`, `SpecDocument`, `Endpoint`, `Schema`, `Entity` relate hierarchically?
5. **[P1]** How do `IntegrationTask`, `IntegrationFlowNode`, `IntegrationFlowEdge`, `EndpointBinding`, `Policy`, `CodeArtifact` compose the Gold model?
6. **[P1]** What invariants should hold at each checkpoint boundary (e.g., after `persist_silver_checkpoint`, what is guaranteed about state)?
7. **[P1]** What is `SpecChunkEmbedding` and how does it relate to streaming persistence?
8. **[P2]** What serialization is used for checkpoints? Is `WorkflowState` pickleable or JSON-serialized?
9. **[P2]** What fields are added by "V3 Streaming Persistence" (`spec_chunk_ids`, `chunk_count`, `embedding_count`)?

### View: Persistence / Storage

*Database schema, Postgres + pgvector, SQLite fallback, streaming, and checkpointing.*

1. **[P0]** What tables exist in the database schema? (List all tables from `init_schema()` or SQL scripts)
2. **[P0]** What is the difference between Postgres (production) and SQLite (tests)? What features are Postgres-only (e.g., pgvector)?
3. **[P0]** What does `get_connection()` return and how does `ConnectionWrapper` ensure proper cleanup?
4. **[P1]** What does each `persist_*` node write and to which tables? (`persist_silver_checkpoint`, `persist_gold_checkpoint`, `persist_run_outcome`, `persist_kg_learning`, `persist_results`)
5. **[P1]** How does `streaming.py` work? What is `STREAMING_PERSISTENCE`, when is it auto-enabled, and what thresholds apply?
6. **[P1]** What is the separation between `db.py` (abstraction), `postgres.py` (Postgres-specific), and `sql_helpers.py`?
7. **[P1]** What checkpoint tables does `PostgresSaver` / `SqliteSaver` create for LangGraph's native checkpointing?
8. **[P2]** What migrations exist and how are they applied? Is there schema versioning?
9. **[P2]** How is connection pooling configured? What are the default pool sizes?

### View: LLM / External Services

*Archetype-based configuration, multi-provider support, caching, and retry logic.*

1. **[P0]** How does an archetype YAML file translate to an LLM call? Trace from `load_archetype()` → `get_archetype_model_config()` → `call_llm_for_node()` → provider client.
2. **[P0]** What providers are supported (OpenAI, Anthropic, Google, mock) and how is provider selection determined per node?
3. **[P0]** What is the difference between `call_llm()`, `call_llm_for_node()`, and `call_llm_async_for_node()`? When should each be used?
4. **[P0]** What is the archetype YAML structure? Document `model`, `prompting`, `retrieval`, `parallelism`, `input_schema`, `output_schema` sections.
5. **[P1]** How does the LLM cache work? What is the cache key structure (`llm:<provider>:<model>:<task_type>:<sha256>`)?
6. **[P1]** How does `with_retry()` implement retry logic? What errors are retryable vs. terminal (rate limits vs. credit errors)?
7. **[P1]** What does `harden_system_prompt()` in `safety.py` do? How does it protect against prompt injection?
8. **[P1]** What LLM modes exist (`LLMMode.REAL`, `MOCK`, `RECORD`, `REPLAY`)? How are they configured?
9. **[P2]** What is `toon.py` and what role does TOON format play in structured output parsing?
10. **[P2]** How is `models.yaml` related to archetypes? Is it deprecated or still used?
11. **[P2]** How does `_apply_env_overrides()` handle provider/model switching when `LLM_PROVIDER` or `LLM_MODEL` env vars are set?

### View: Operational / CLI-API

*User-facing entrypoints, options, output formats, and resumption.*

1. **[P0]** What is the full CLI surface? List all commands, options, and their behaviors. (File: `cli.py`)
2. **[P0]** What is the signature and contract of `design_and_generate_integration()`? Document all parameters and return type.
3. **[P0]** What fields does `IntegrationOptions` contain and what is the default for each?
4. **[P0]** What fields does `IntegrationResult` contain? How do they map to Silver/Gold artifacts?
5. **[P1]** How does auto-resume work? What does `_try_resume_run()` check and when is it triggered?
6. **[P1]** What does demo mode do? What example specs and tasks are pre-configured?
7. **[P1]** What is `recovery.py` and what does it provide beyond LangGraph's checkpoint-based resume?
8. **[P2]** What output formats are supported (JSON, Markdown, files)? How does `--json` flag change output?
9. **[P2]** How does `--verbose` / `-v` change logging behavior?

### View: Configuration / Environment

*Settings hierarchy, feature flags, and how config changes affect runtime.*

1. **[P0]** What environment variables are required vs. optional? Document each with its effect.
2. **[P0]** What is the precedence order for settings? (env var → archetype → `models.yaml` → hardcoded default)
3. **[P0]** How does `get_settings()` work? What is the `Settings` dataclass structure?
4. **[P1]** What is `llm_mode.py` and how does `LLMMode` interact with archetype loading?
5. **[P1]** What is the current status of `models.yaml`—fully deprecated, partially used, or still the source of truth?
6. **[P1]** What feature flags exist? (`PARALLEL_WORKFLOW`, `STREAMING_PERSISTENCE`, `LLM_CACHE_ENABLED`, etc.)
7. **[P2]** How is `.env` file loading handled? What takes precedence when both file and env var exist?
8. **[P2]** Are there any configuration options that change graph structure (include/exclude nodes)?

### View: Security / Safety

*Prompt injection mitigation, codegen security, secrets handling.*

1. **[P1]** How does `sanitizer.py` work? What `SUSPICIOUS_PATTERNS` are detected and what action is taken?
2. **[P1]** What is `sanitize_input()` vs. `sanitize_spec_content()` vs. `sanitize_task_description()`? How do aggressiveness levels differ?
3. **[P1]** What does `content_policy.py` provide? Are there content filters applied to LLM responses?
4. **[P1]** How are API keys handled? Are they ever logged, persisted in DB, or included in `WorkflowState`?
5. **[P1]** What does `test_codegen_security.py` validate? What dangerous patterns in generated code are checked?
6. **[P2]** What happens if generated code contains shell execution, network calls, or file writes? Is it blocked, warned, or allowed?
7. **[P2]** Is there validation on user-provided spec URLs or task descriptions before processing?

### View: Observability

*LangSmith tracing, logging, token tracking, and run inspection.*

1. **[P1]** How is LangSmith tracing configured? What env vars control it (`LANGCHAIN_TRACING_V2`, `LANGSMITH_PROJECT`)?
2. **[P1]** What metadata is attached to LangSmith traces? (`run_id`, `provider_code`, `node_type`, `responsibility`)
3. **[P1]** How does token usage tracking work? What does `init_token_usage()` and `_track_token_usage()` do?
4. **[P1]** What does `persist_run_outcome` persist? What table and what fields?
5. **[P2]** How can a user inspect a completed run after the fact? What tools exist?
6. **[P2]** What do `inspect_node_details.py` and `inspect_langsmith_traces.py` in the root provide?
7. **[P2]** What logging levels are used and how is logging configured?

### View: Quality Attributes

*Reliability, performance, scalability, maintainability, and stated bounds.*

1. **[P0]** What are the stated outer bounds in ARCHITECTURE.md and do they match enforcement in code? (`MAX SPEC SIZE`, `MAX ENDPOINTS`, `MAX RUN TIME`, etc.)
2. **[P1]** What is `degraded_mode`? What conditions set it, what nodes read it, and what behavior changes?
3. **[P1]** What template fallbacks exist? Where does the system fall back to templates when LLM fails?
4. **[P1]** What concurrency/parallelism exists? What is `PARALLEL_TIMEOUT` and thread pool sizing?
5. **[P1]** What is the expected latency for a typical run? Are there any hard timeouts enforced?
6. **[P2]** What are the memory characteristics? How does streaming persistence reduce memory from "200MB+" to "<20MB"?
7. **[P2]** What are the known limitations and unsupported use cases?

---

## Cross-Cutting & Doc-Drift Questions

*Questions that span multiple views and specifically target mismatches between docs and code.*

### Doc-Drift Detection

1. **[P0]** ARCHITECTURE.md claims "22 LangGraph nodes: 21 in the main path plus one error node"—does `graph/nodes/` actually have 22 node files? What are the actual node names?
2. **[P0]** ARCHITECTURE.md claims "MAX SPEC SIZE: ~20 MB per document (design target, not runtime-enforced)"—is this accurate? Is there any enforcement?
3. **[P0]** ARCHITECTURE.md claims "Idempotent Operations: Running same inputs twice produces same outputs"—what code enforces this? Is it true given LLM variability?
4. **[P0]** ARCHITECTURE.md claims "Generated code is syntactically valid: AST validation + template fallback"—where is AST validation implemented? What triggers template fallback?
5. **[P1]** ARCHITECTURE.md claims "Dry-run never writes to DB: All nodes check `options.dry_run`"—do ALL persist nodes actually check this? Is there any code path that writes?
6. **[P1]** ARCHITECTURE.md claims `persist_kg_learning` node exists and runs after `persist_gold_checkpoint`—is this node implemented and wired?
7. **[P1]** ARCHITECTURE.md describes "Confidence Thresholds" for repo profile at 0.65—where is this threshold defined and used?
8. **[P2]** ARCHITECTURE.md section "Hybrid Retrieval (KG + Semantic Search)" describes combined scoring—is this implemented or aspirational? What does `kg/` actually contain?

### Cross-Cutting Concerns

9. **[P0]** What is the complete end-to-end data flow? Trace a spec from ingestion → Silver model → Gold model → code artifacts → report, listing nodes and state fields touched.
10. **[P0]** What is the minimum viable environment to run successfully? Document required env vars, DB setup, and API keys.
11. **[P1]** How do errors propagate from a node exception to user-visible error message? What is recorded in `state.errors`?
12. **[P1]** What is the testing strategy? How do the ~70 test files in `tests/` divide responsibilities (unit, integration, E2E)?
13. **[P1]** How does `WorkflowState` backward compatibility work? What happens if an old checkpoint has missing fields?
14. **[P2]** What extension points exist? Can users add custom nodes, archetypes, or parsers without modifying core code?
15. **[P2]** How does this system differ from "vanilla" LangChain/LangGraph patterns? What is custom vs. idiomatic?
16. **[P2]** What versioning guarantees exist? How are breaking changes to `WorkflowState` or checkpoint format communicated?

---

## Questions Sorted by Impact on ARCHITECTURE.md

These are the top P0 questions that, once answered, will have the biggest impact on correctness and completeness of the architecture documentation.

| # | Question | Affects Section |
|---|----------|-----------------|
| 1 | What is the complete ordered list of nodes for a successful run? Does it match the 22 claimed? | Workflow overview, Data Flow |
| 2 | What is the exact structure of `WorkflowState` and its Bronze/Silver/Gold field groupings? | Data Flow, State Transitions |
| 3 | How does checkpoint recovery work—state persistence, skip detection, `timed_node()` contract? | Workflow Engine subsection |
| 4 | What conditional edges exist and what are the branching conditions? | Data Flow diagram, Conditional Routing |
| 5 | How does the archetype system work end-to-end, from YAML to LLM call? | LLM Configuration subsection |
| 6 | What providers are supported and how is provider selection configured per node? | External Dependencies table |
| 7 | What tables exist in the database schema? | Persistence subsection, Silver-Gold tables |
| 8 | What is the difference between Postgres and SQLite modes? | Deployment View, Database Options |
| 9 | What environment variables are required vs. optional? | Configuration section, Quick Reference |
| 10 | What is the signature and contract of `design_and_generate_integration()`? | API entrypoint, Quick Reference |
| 11 | What does `IntegrationOptions` control and what are defaults? | API section, Options table |
| 12 | What does `IntegrationResult` contain? | Output section, Artifacts |
| 13 | What are the stated bounds and are they enforced? (`MAX_SPEC_SIZE`, `MAX_RUN_TIME`) | Agent Behavior Bounds |
| 14 | What is `degraded_mode` and what triggers/reads it? | Quality attributes, Failure modes |
| 15 | Does ARCHITECTURE.md node list match actual `graph/nodes/` files? | Doc drift fix |
| 16 | Is "idempotent operations" claim accurate given LLM variability? | Consistency Guarantees |
| 17 | Where is AST validation for generated code implemented? | Code Generation subsection |
| 18 | What does `persist_kg_learning` persist and is KG fully implemented? | KG subsection, Data Flow |
| 19 | How do `call_llm()` / `call_llm_for_node()` / async variant differ? | LLM Client API |
| 20 | What is the minimum viable environment setup? | Getting Started cross-ref, Quick Reference |
| 21 | What is the LLM cache key structure and when does caching apply? | Performance, External Services |
| 22 | How does `sanitizer.py` protect against prompt injection? | Security view |
| 23 | What is the testing strategy and how are ~70 test files organized? | Testing section (if added) |
| 24 | How does parallel graph differ from sequential? When is it enabled? | Performance, Parallelism |
| 25 | What feature flags exist and what do they control? | Configuration section |

---

## Priority Summary

| Priority | Meaning                                      | Count |
|----------|----------------------------------------------|-------|
| **P0**   | Must answer before editing ARCHITECTURE.md  | 35    |
| **P1**   | Important for depth, completeness, maintainer understanding | 42    |
| **P2**   | Nice to have, advanced, or edge cases        | 28    |
| **Total**|                                              | 105   |

---

## Audit Execution Plan

1. **Phase 1: Answer all P0 questions** by pointing to specific files, functions, line numbers, and tests.
2. **Phase 2: Doc-drift validation**—for each claim in ARCHITECTURE.md, verify or refute with code evidence.
3. **Phase 3: Answer P1 questions** to fill in depth and maintainer-level understanding.
4. **Phase 4: Compile answers** into a structured audit report.
5. **Phase 5: Rewrite ARCHITECTURE.md** using audit findings, organized by views.

---

*This question set was generated by walking the repository structure, reading key implementation files, and comparing against the existing ARCHITECTURE.md to identify areas of potential doc drift.*
