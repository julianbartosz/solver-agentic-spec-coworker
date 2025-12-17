# Code Tour

> A 60-90 minute walkthrough to understand the codebase deeply.
> Open each file mentioned, read the code, then explore with Copilot.

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
│   ├── parallel.py         # Parallel execution support
│   └── nodes/              # Individual node implementations
├── kg/                     # Knowledge graph operations
├── llm/                    # LLM client, cache, TOON format
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
1. Which fields are inputs vs outputs?
2. What's the difference between `spec_documents` (Bronze), `endpoints` (Silver), and `integration_task` (Gold)?
3. Why are ID fields `Optional[int]`?

---

## Graph Orchestration

### File to Open
- `src/integration_coworker/graph/runtime.py`

### What to Look For
- `build_graph()`: How 22 nodes are registered with LangGraph
- Conditional edges: `should_run_repo_nodes()`, `check_for_errors_after_validation()`
- `WORKFLOW_NODE_ORDER`: Canonical list of all nodes

### Questions to Explore
1. How does `plan_run` decide whether to use repo integration?
2. If validation fails, which path does the graph take?
3. What happens when `PARALLEL_WORKFLOW=true`?

---

## Key Nodes

### Ingestion: `plan_run`
- **File**: `graph/nodes/plan_run.py`
- **Purpose**: UUID generation, input validation, provider normalization

### Silver: `build_silver_api_model`
- **File**: `graph/nodes/build_silver_api_model.py`
- **Purpose**: Walks `openapi_spec["paths"]` → creates Endpoint, Schema, Entity objects

### Gold: `understand_task`
- **File**: `graph/nodes/understand_task.py`
- **Purpose**: Task slug normalization, constraint detection

### Gold: `align_task_with_kg`
- **File**: `graph/nodes/align_task_with_kg.py`
- **Purpose**: Template matching via `WORKFLOW_TEMPLATES` dict

### Generation: `generate_code_and_tests`
- **File**: `graph/nodes/generate_code_and_tests.py`
- **Purpose**: Template-based generation of client, flow, test artifacts

---

## Domain Models

### File to Open
- `src/integration_coworker/domain/models.py`

### Silver Models
- `SpecDocument`, `Endpoint`, `Schema`, `SchemaField`, `Entity`, `EndpointParameter`, `EntityRelationship`

### Gold Models
- `IntegrationTask`, `IntegrationFlowNode`, `IntegrationFlowEdge`, `EndpointBinding`, `Policy`, `CodeArtifact`

---

## LLM Integration

### Files to Open
- `src/integration_coworker/llm/client.py` - LLM client implementations
- `src/integration_coworker/llm/cache.py` - Redis caching layer

### Key Concepts
- Multi-provider support (OpenAI, Anthropic, Google)
- Cache-through pattern for responses
- Task type tagging for observability

---

## Repo Integration

### Files to Open
- `src/integration_coworker/repo/models.py` → `RepoProfile`, `RepoChangeSet`
- `src/integration_coworker/repo/profiles.py` → Framework-specific configs
- `src/integration_coworker/repo/helpers.py` → `upsert_block_between_markers()`

### Key Concepts
- **RepoProfile**: archetype, conventions, layout_hints
- **Marker-Based Insertion**: Idempotent updates between `# <AUTO_INTEGRATION_MARKER>` comments

---

## Testing

### Key Test Suites

| Suite | File | Purpose |
|-------|------|---------|
| End-to-End | `test_end_to_end_integration.py` | Full workflow validation |
| Persistence | `test_m4_persistence.py` | DB writes, ID backfilling |
| Silver Model | `test_build_silver_api_model.py` | Endpoint/schema extraction |
| LLM Cache | `test_llm_cache.py` | Cache operations |
| Parallel | `test_parallel_execution.py` | Parallel workflow |

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
- [ ] Understand the LLM cache mechanism
- [ ] Know where to find key tests

---

[Back to Architecture](architecture.md) | [Contributing →](contributing.md)
