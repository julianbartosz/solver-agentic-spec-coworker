# Code Tour

> **Status**: Active (Nice-to-have)
>
> A 60-90 minute walkthrough to understand the codebase deeply.
> Open each file mentioned, read the code, then explore with Copilot.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Entry Points](#entry-points)
3. [State Management](#state-management)
4. [Graph Orchestration](#graph-orchestration)
5. [Key Nodes](#key-nodes)
6. [Domain Models](#domain-models)
7. [Repo Integration](#repo-integration)
8. [Runtime Components](#runtime-components)
9. [Testing](#testing)

---

## Project Structure

```
src/integration_coworker/
├── api/                    # Public API (entrypoint.py, types.py)
├── cli.py                  # CLI commands
├── config/                 # Configuration (archetypes, models.yaml)
├── domain/                 # Domain models (models.py)
├── graph/
│   ├── state.py            # WorkflowState, IntegrationResult
│   ├── runtime.py          # LangGraph wiring
│   └── nodes/              # Individual node implementations
├── kg/                     # Knowledge graph operations
├── llm/                    # LLM client, TOON format
├── persistence/            # Database operations
├── repo/                   # Repo profiles, marker insertion
├── retrieval/              # Semantic search
└── runtime/                # HTTP client, exceptions
```

---

## Entry Points

### Files to Open
- `src/integration_coworker/cli.py`
- `src/integration_coworker/api/entrypoint.py`

### What to Look For
- **CLI**: How arguments are parsed, how they map to `IntegrationOptions`
- **Entrypoint**: `design_and_generate_integration()` signature, builds `WorkflowState`, calls graph

### Questions to Explore
1. What happens if a user runs `--dry-run`?
2. How does `provider_code` get inferred if not provided?
3. What's the difference between `IntegrationOptions` and `WorkflowState`?

---

## State Management

### File to Open
- `src/integration_coworker/graph/state.py`

### Key Types
- **`WorkflowState`**: All fields (inputs, Bronze, Silver, Gold, control)
- **`IntegrationResult`**: Public API return type

### Questions to Explore
1. Which fields are inputs (set before graph runs) vs outputs (populated by nodes)?
2. What's the difference between `spec_documents` (Bronze), `endpoints` (Silver), and `integration_task` (Gold)?
3. Why are ID fields `Optional[int]`?

---

## Graph Orchestration

### File to Open
- `src/integration_coworker/graph/runtime.py`

### What to Look For
- `build_graph()`: How 22 nodes are registered with LangGraph (21 in the main path plus one error node)
- Conditional edges: `should_run_repo_nodes()`, `check_for_errors_after_validation()`
- `WORKFLOW_NODE_ORDER`: Canonical list of all nodes in execution order

### Questions to Explore
1. How does `plan_run` decide whether to use repo integration?
2. If validation fails, which path does the graph take?
3. Why does the graph still go to `persist_results` even after `handle_error`?

> **Error Handling**: The graph includes a `handle_error` node that catches failures from `validate_integration_design` and feeds into the report builder. When validation fails, the graph routes control through `handle_error`, then into the report builder node. This path records the failure in the final run summary.

---

## Key Nodes

### Ingestion: `plan_run`
- **File**: `graph/nodes/plan_run.py`
- **Purpose**: UUID generation, input validation, provider normalization, `plan["use_repo"]` logic

### Silver: `build_silver_api_model`
- **File**: `graph/nodes/build_silver_api_model.py`
- **Purpose**: Walks `openapi_spec["paths"]` → creates Endpoint, Schema, Entity objects

### Gold: `understand_task`
- **File**: `graph/nodes/understand_task.py`
- **Purpose**: Task slug normalization, constraint detection, target operation matching

### Gold: `align_task_with_kg`
- **File**: `graph/nodes/align_task_with_kg.py`
- **Purpose**: Template matching via `WORKFLOW_TEMPLATES` dict

### Generation: `generate_code_and_tests`
- **File**: `graph/nodes/generate_code_and_tests.py`
- **Purpose**: Template-based generation of client, flow, test artifacts

### Persistence: `persist_results`
- **File**: `graph/nodes/persist_results.py`
- **Purpose**: Primary persistence node → INSERT/UPDATE, ID backfilling

> **Note**: `persist_results` remains in the graph for backward compatibility but is not part of the main execution order (`WORKFLOW_NODE_ORDER`). Additional persistence nodes (`persist_silver_checkpoint`, `persist_gold_checkpoint`, `persist_kg_learning`, `persist_run_outcome`) handle streaming persistence for large specs. See `WORKFLOW_NODE_ORDER` in `runtime.py` for the complete list.

---

## Domain Models

### File to Open
- `src/integration_coworker/domain/models.py`

### Silver Models
- `SpecDocument`, `Endpoint`, `Schema`, `SchemaField`, `Entity`, `EndpointParameter`, `EntityRelationship`

### Gold Models
- `IntegrationTask`, `IntegrationFlowNode`, `IntegrationFlowEdge`, `EndpointBinding`, `Policy`, `CodeArtifact`

### Questions to Explore
1. Why are all `id` fields `Optional[int]` with `None` as default?
2. How do `IntegrationFlowNode` and `IntegrationFlowEdge` represent a workflow graph?

---

## Repo Integration

### Files to Open
- `src/integration_coworker/repo/models.py` → `RepoProfile`, `RepoChangeSet`
- `src/integration_coworker/repo/profiles.py` → Framework-specific configs
- `src/integration_coworker/repo/helpers.py` → `upsert_block_between_markers()`

### Key Concepts
- **RepoProfile**: archetype, conventions, layout_hints, integration_hooks
- **Marker-Based Insertion**: Idempotent updates between `# <AUTO_INTEGRATION_MARKER>` comments
- **MockedGithubRepoRetriever**: In-memory repo for RAG context

---

## Runtime Components

### Files to Open
- `src/integration_coworker/runtime/http_client.py`
- `src/integration_coworker/runtime/exceptions.py`

### Key Types
- **`IntegrationHttpClient`**: httpx wrapper, auth injection, auto-retry
- **`IntegrationError`**: Base exception
- **`TransientIntegrationError`**: Retryable (429, 5xx, timeouts)
- **`AuthIntegrationError`**: Auth failures (401, 403)

---

## Testing

### Key Test Suites

| Suite | File | Purpose |
|-------|------|---------|
| End-to-End | `test_end_to_end_integration.py` | Full workflow validation |
| Persistence | `test_m4_persistence.py` | DB writes, ID backfilling |
| HTTP Client | `test_integration_http_client.py` | Auth, retry, exceptions |
| Silver Model | `test_build_silver_api_model.py` | Endpoint/schema extraction |
| Repo Integration | `test_marker_insertion.py` | Marker-based updates |

### Run Tests

```bash
# All tests
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/ -v

# Specific test
pytest tests/test_end_to_end_integration.py::test_end_to_end_dry_run -v
```

---

## Summary Checklist

After this tour, you should be able to:

- [ ] Explain the entry points (CLI vs API)
- [ ] Describe WorkflowState and how it flows through nodes
- [ ] Trace graph execution for a dry-run
- [ ] Explain Silver vs Gold models
- [ ] Describe how workflow templates are matched
- [ ] Understand how `persist_results` writes to the database
- [ ] Explain repo integration with profiles and markers
- [ ] Know where to find key tests

**Next Steps**: See `ARCHITECTURE.md` for high-level design, or `GETTING_STARTED.md#quick-demo` to run a demo.

