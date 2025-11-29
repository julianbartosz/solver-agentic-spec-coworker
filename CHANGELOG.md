# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [M4-demo] - 2025-11-28

### Added

#### Workflow Engine
- 18-node LangGraph workflow with conditional routing for repo integration
- Three persistence checkpoints: `persist_silver_checkpoint`, `persist_gold_checkpoint`, `persist_run_outcome`
- LangSmith tracing integration via `set_run_context()` / `clear_run_context()`
- Full `WorkflowState` dataclass for type-safe state management

#### Persistence Layer
- PostgreSQL + pgvector schema with VECTOR(1536) embeddings support
- SQLite fallback for testing and development
- **spec_silver** schema: source_systems, spec_documents, schemas, entities, endpoints, spec_chunks
- **integration_gold** schema: integration_tasks, workflow_nodes, endpoint_bindings, policies, code_artifacts
- **kg** schema: nodes, edges, workflow_steps, step_bindings
- SQL helpers for database-agnostic queries (`sql_helpers.py`)

#### Knowledge Graph / GraphRAG
- `query_workflow_templates()` with hybrid scoring (40% graph + 40% embedding + 20% exact match)
- `persist_kg_learning` node to populate KG from successful runs
- `kg-dump` CLI command for inspecting KG contents
- In-memory fallback templates for bootstrapping (gated by env var)

#### Code Generation
- Spec-driven naming: `derive_client_class_name()`, `derive_method_name()`, `derive_flow_function_name()`
- RepoProfile-aware path derivation via `get_layout_dirs()`
- LLM refinement with AST validation and symbol checking
- Rich prompt building with schema and endpoint context
- Fallback to template skeletons when LLM fails validation

#### Multi-Provider Support
- **mock_payments** provider with checkout session templates
- **Stripe** provider (Payment Intents) with create/confirm/get/cancel templates
- Provider inference from spec filename
- 8 Stripe integration tests proving multi-provider design

#### Repository Awareness
- `detect_profile_from_repo()` with heuristics for:
  - Next.js (package.json + next.config.*)
  - Django (manage.py + settings.py)
  - FastAPI (pyproject.toml/requirements.txt with fastapi)
- RepoProfile with layout_hints and integration_hooks
- 21 repo profile detection tests

#### LLM Integration
- LangChain ChatOpenAI for automatic LangSmith tracing
- Mock LLM mode via `USE_MOCK_LLM=true`
- TOON (Token-Oriented Object Notation) for 30-40% prompt token savings
- OpenAIEmbeddings wrapper for spec chunk embedding

#### CLI
- `run` command with multi-spec support (`--spec-ref` can be repeated)
- `demo` command with built-in mock_payments fixture
- `status` command showing database and LLM configuration
- `init-db` command for schema initialization
- `kg-dump` command for KG inspection
- JSON output mode (`--json`) for scripting

#### Documentation
- `DEMO.md` with step-by-step demo instructions
- `docs/PROGRESS_M4_M5.md` with subsystem overview and evidence
- `docs/M5_ROADMAP.md` with workstreams and definitions of done
- `docs/M4_SHIP_CHECKLIST.md` with detailed verification steps
- `docs/M4_EXECUTIVE_SUMMARY.md` for stakeholders

### Tests

- **158 tests passing** in 6.37 seconds
- End-to-end integration tests (`test_end_to_end_integration.py`)
- Generated code execution test (`test_m4_generated_code_execution.py`)
- GraphRAG integration tests (`test_graphrag_integration.py`)
- Multi-provider tests (`test_stripe_integration.py`)
- Repo profile detection tests (`test_repo_profiles.py`)
- TOON serialization tests (`test_toon.py`)
- Codegen validation tests (`test_codegen_validation.py`)
- Persistence tests (`test_m4_persistence.py`)

### Changed

- Refactored LLM client to use LangChain for automatic tracing
- Deprecated `call_llm_json()` in favor of TOON serialization
- Updated `understand_task.py` and `plan_integration_flow.py` to use TOON format

### Fixed

- EntityRelationship field names aligned (source_entity_id, target_entity_id)
- Schema linking with request/response IDs (P1.3)
- Repo profile detection handles None input gracefully

---

## [M3] - 2025-11-24

### Added

- Initial LangGraph workflow with planning nodes
- Basic spec ingestion and parsing
- Integration task understanding
- Policy attachment node
- Code generation skeleton

---

## [M2] - 2025-11-20

### Added

- Project structure and package setup
- Domain models for Silver/Gold schemas
- Basic CLI entrypoint
- Configuration management

---

## [Initial] - 2025-11-15

### Added

- Repository initialization
- Design documents and architecture overview
- Initial prompts and chatmodes for Copilot
