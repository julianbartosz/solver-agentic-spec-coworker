# Architecture Audit: Runtime/Workflow + Data/State Views

> **Date**: December 9, 2025  
> **Scope**: P0 questions for Runtime/Workflow View and Data/State View only  
> **Status**: Audit findings ready for review before doc edits

---

## Runtime / Workflow View – P0 Audit

### Q1: Which function builds the `StateGraph` and what is the exact sequence of `add_node()` and `add_edge()` calls?

**Answer**: The `build_graph()` function in `src/integration_coworker/graph/runtime.py` (lines 310-414) constructs the graph.

**Node registration order** (from `add_node()` calls):
1. `plan_run`
2. `ingest_spec`
3. `detect_and_parse_spec`
4. `build_silver_api_model`
5. `embed_spec_chunks`
6. `persist_silver_checkpoint`
7. `understand_task`
8. `align_task_with_kg`
9. `plan_integration_flow`
10. `attach_policies_and_patterns`
11. `attach_repo_context`
12. `generate_code_and_tests`
13. `persist_gold_checkpoint`
14. `analyze_repo_layout`
15. `apply_repo_integration_changes`
16. `validate_integration_design`
17. `persist_results`
18. `build_report`
19. `persist_run_outcome`
20. `handle_error`
21. `persist_kg_learning` *(added later, line 388)*

**Edge wiring** (sequential unless noted):
- Entry: `plan_run`
- `plan_run` → `ingest_spec` → `detect_and_parse_spec` → `build_silver_api_model` → `embed_spec_chunks` → `persist_silver_checkpoint` → `understand_task` → `align_task_with_kg` → `plan_integration_flow` → `attach_policies_and_patterns` → `generate_code_and_tests` → `persist_gold_checkpoint` → `persist_kg_learning`
- **Conditional #1** (`should_run_repo_nodes`): After `persist_kg_learning`
  - `with_repo` → `attach_repo_context` → `analyze_repo_layout` → `apply_repo_integration_changes` → `validate_integration_design`
  - `without_repo` → `validate_integration_design` (skip repo nodes)
- **Conditional #2** (`check_for_errors_after_validation`): After `validate_integration_design`
  - `has_errors` → `handle_error` → `build_report`
  - `no_errors` → `build_report`
- `build_report` → `persist_run_outcome` → `END`

**Doc-vs-Code Discrepancy**:
- **Doc says**: "22 LangGraph nodes: 21 in the main path plus one error node"
- **Code shows**: 21 nodes are registered in `build_graph()` (including `persist_kg_learning` added separately)
- **Files in `graph/nodes/`**: 22 `.py` files, but includes `persist_results.py` AND `persist_results_backup.py` (backup is not a real node)
- **Conclusion**: **Partially accurate**. There are 21 unique node registrations. The "22 files" count includes a backup file that's not wired.

---

### Q2: What is the complete ordered list of nodes for a successful run?

**Answer**: From `WORKFLOW_NODE_ORDER` constant (lines 594-614):

```python
WORKFLOW_NODE_ORDER: List[str] = [
    "plan_run",
    "ingest_spec",
    "detect_and_parse_spec",
    "build_silver_api_model",
    "embed_spec_chunks",
    "persist_silver_checkpoint",
    "understand_task",
    "align_task_with_kg",
    "plan_integration_flow",
    "attach_policies_and_patterns",
    "generate_code_and_tests",
    "persist_gold_checkpoint",
    "persist_kg_learning",
    # Optional repo nodes (may be skipped)
    "attach_repo_context",
    "analyze_repo_layout",
    "apply_repo_integration_changes",
    # Final nodes
    "validate_integration_design",
    "build_report",
    "persist_run_outcome",
]
```

**Count**: 19 nodes in `WORKFLOW_NODE_ORDER` (excludes `handle_error` and `persist_results`).

**Doc-vs-Code Discrepancy**:
- **Doc says**: Lists 22 nodes total
- **Code shows**: 19 in main sequence + `handle_error` + `persist_results` (legacy) = 21 registered
- **Conclusion**: **Minor inaccuracy**. The "22" claim is off by one (21 actual).

---

### Q3: What conditional edges exist and what are the branching conditions?

**Answer**: Two conditional edges exist in `build_graph()`:

**1. `should_run_repo_nodes`** (line 391-400):
```python
def should_run_repo_nodes(state: WorkflowState) -> str:
    """Route to repo nodes if plan["use_repo"] is True, else skip to validation."""
    if state.plan.get("use_repo", False):
        return "with_repo"
    return "without_repo"
```
- Source node: `persist_kg_learning`
- Branches:
  - `"with_repo"` → `attach_repo_context`
  - `"without_repo"` → `validate_integration_design`

**2. `check_for_errors_after_validation`** (line 408-413):
```python
def check_for_errors_after_validation(state: WorkflowState) -> str:
    """Check if errors occurred during validation."""
    if state.errors and not state.plan.get("failed", False):
        return "has_errors"
    return "no_errors"
```
- Source node: `validate_integration_design`
- Branches:
  - `"has_errors"` → `handle_error`
  - `"no_errors"` → `build_report`

**Doc-vs-Code**:
- **Doc says**: "Conditional Routing: Repo integration only runs if `plan["use_repo"]=True`"
- **Code shows**: This is accurate. Additionally, error routing exists.
- **Conclusion**: **Accurate but incomplete** – doc mentions repo conditional but not the error conditional.

---

### Q4: How does checkpoint recovery work—state persistence, skip detection, `timed_node()` contract?

**Answer**:

**Checkpointer Initialization** (`get_checkpointer()`, lines 44-97):
- Uses singleton pattern
- Returns `PostgresSaver` if Postgres is configured, else `SqliteSaver`
- Creates checkpoint tables via `.setup()`

**State Persistence** (two mechanisms):

1. **LangGraph Native Checkpointing** (line 425):
   - `workflow.compile(checkpointer=checkpointer)` enables automatic state save after each node
   - Uses `thread_id` for checkpoint isolation (defaults to `run_id`)

2. **Application-level checkpoints** (via `timed_node()` decorator, lines 246-293):
   - `_save_checkpoint_if_enabled()` calls `save_checkpoint()` from `persistence/checkpoints.py`
   - Saves to `run_checkpoints` table with node_name and serialized state

**Skip Detection** (`_should_skip_node()` inside `timed_node()`, lines 221-225):
```python
def _should_skip_node(state: WorkflowState) -> bool:
    """Check if this node should be skipped (already completed during resume)."""
    if hasattr(state, 'completed_steps') and state.completed_steps:
        return node_name in state.completed_steps
    return False
```
- Checks if `node_name` is in `state.completed_steps`
- Returns state unchanged if already completed

**`timed_node()` Contract** (lines 194-293):
- Wraps node function with timing and checkpoint logic
- Records execution time in `state.node_timings[node_name]`
- Adds LangSmith tracing metadata when `LANGCHAIN_TRACING_V2=true`
- Saves checkpoint after successful execution (unless `dry_run=True`)
- Skips execution if node is in `completed_steps`

**Doc-vs-Code**:
- **Doc says**: No specific checkpoint recovery documentation
- **Code shows**: Dual-layer checkpointing (LangGraph native + application-level)
- **Conclusion**: **Missing from docs** – checkpoint recovery mechanism is undocumented.

---

### Q5: What is `WORKFLOW_NODE_ORDER` and how is it used?

**Answer**: 
- Defined at lines 594-614 in `runtime.py`
- Used by:
  - `get_node_names()` – returns a copy of the list
  - `get_skip_cascade()` – sorts skipped nodes by execution order
  - `run_from_node()` – validates that `start_node` is in the list

**Purpose**: Provides canonical execution order for recovery and skip cascade calculations.

---

### Q6: How does the parallel graph differ from the sequential graph?

**Answer**: `build_parallel_graph()` (lines 432-539) differs by:

1. **Fan-out after `build_silver_api_model`**:
   - Sequential: `build_silver_api_model` → `embed_spec_chunks` → ...
   - Parallel: `build_silver_api_model` → `[embed_spec_chunks, understand_task]` (concurrent)

2. **Sync node** (`sync_embed_task`):
   - Both branches merge at `sync_embed_task` before continuing
   - Implemented in `graph/parallel.py`

3. **Enabled via**: `PARALLEL_WORKFLOW=true` environment variable
   - Checked by `is_parallel_enabled()` in `parallel.py`

**Doc-vs-Code**:
- **Doc says**: No mention of parallel execution
- **Code shows**: Full parallel graph implementation exists
- **Conclusion**: **Missing from docs** – parallel execution feature is undocumented.

---

## Data / State View – P0 Audit

### Q1: What is the exact structure of `WorkflowState` and its field groupings?

**Answer**: `WorkflowState` is a `@dataclass` in `graph/state.py` with **~35 fields** (not "~50" as previously estimated).

**Field Groupings by Layer**:

**Inputs** (5 fields):
- `source_refs: List[SourceRef]`
- `spec_refs: List[str]`
- `task_description: str`
- `provider_code: Optional[str]`
- `options: Optional[IntegrationOptions]`

**Bronze Layer** (4 fields):
- `spec_documents: List[SpecDocument]`
- `spec_sections: List[SpecSection]`
- `doc_chunks: List[str]`
- `openapi_spec: Optional[Dict[str, Any]]`

**Silver Layer** (8 fields):
- `source_system: Optional[SourceSystem]`
- `endpoints: List[Endpoint]`
- `endpoint_parameters: List[EndpointParameter]`
- `schemas: List[Schema]`
- `schema_fields: List[SchemaField]`
- `entities: List[Entity]`
- `relationships: List[EntityRelationship]`
- `events: List[Event]`

**Embeddings (Silver-adjacent)** (4 fields):
- `spec_chunk_embeddings: List[SpecChunkEmbedding]`
- `spec_chunk_ids: List[int]`
- `chunk_count: int`
- `embedding_count: int`

**Gold Layer** (7 fields):
- `workflow_template: Optional[WorkflowTemplate]`
- `integration_task: Optional[IntegrationTask]`
- `workflow_nodes: List[IntegrationFlowNode]`
- `workflow_edges: List[IntegrationFlowEdge]`
- `endpoint_bindings: List[EndpointBinding]`
- `policies: List[Policy]`
- `code_artifacts: List[CodeArtifact]`

**Repo Integration** (5 fields):
- `repo_root: Optional[Path]`
- `repo_profile: Optional[RepoProfile]`
- `repo_snapshot: Optional[RepoSnapshot]`
- `repo_changes: Optional[RepoChangeSet]`
- `repo_markdown_context: Optional[str]`

**Control / Bookkeeping** (13 fields):
- `plan: Dict[str, Any]`
- `completed_steps: List[str]`
- `errors: List[str]`
- `persisted_ids: Dict[str, Any]`
- `pending_specs: List[Dict[str, Any]]`
- `parsed_specs: List[Dict[str, Any]]`
- `degraded_mode: bool`
- `degraded_reason: Optional[str]`
- `skipped_nodes: List[str]`
- `llm_fallbacks: List[Dict[str, Any]]`
- `node_timings: Dict[str, float]`
- `llm_token_usage: Dict[str, int]`
- `cache_hit: bool`
- `warnings: List[str]`

**Outputs** (2 fields):
- `report_markdown: Optional[str]`
- `run_id: Optional[str]`

**Doc-vs-Code**:
- **Doc says**: "Bronze → openapi_spec (parsed dict)" as key state transition
- **Code shows**: Bronze includes `spec_documents`, `spec_sections`, `doc_chunks`, AND `openapi_spec`
- **Conclusion**: **Partially accurate** – doc simplifies Bronze layer

---

### Q2: Which nodes populate which fields?

**Answer** (based on docstrings and code inspection):

| Node | Reads | Writes |
|------|-------|--------|
| `plan_run` | `spec_refs`, `options`, `provider_code` | `run_id`, `plan`, `provider_code` (inferred), `completed_steps` |
| `ingest_spec` | `spec_refs` | `spec_documents`, `doc_chunks`, `chunk_count` (streaming) |
| `detect_and_parse_spec` | `spec_documents` | `openapi_spec`, `spec_sections` |
| `build_silver_api_model` | `openapi_spec`, `cache_hit` | `endpoints`, `schemas`, `entities`, `source_system` |
| `embed_spec_chunks` | `doc_chunks` or DB | `spec_chunk_embeddings`, `embedding_count` |
| `persist_silver_checkpoint` | All Silver fields | `persisted_ids` (silver subset), backfills IDs |
| `understand_task` | `task_description`, `endpoints` | `integration_task`, `degraded_mode`/`degraded_reason` (on fallback) |
| `align_task_with_kg` | `integration_task` | `workflow_template` |
| `plan_integration_flow` | `integration_task`, `endpoints` | `workflow_nodes`, `workflow_edges`, `endpoint_bindings` |
| `attach_policies_and_patterns` | `endpoints`, Security schemes | `policies` |
| `generate_code_and_tests` | `workflow_nodes`, `policies`, `endpoints` | `code_artifacts` |
| `persist_gold_checkpoint` | All Gold fields | `persisted_ids` (gold subset) |
| `persist_kg_learning` | `workflow_template` | `persisted_ids` (kg subset) |
| `attach_repo_context` | `repo_root` | `repo_profile` |
| `analyze_repo_layout` | `repo_profile` | `repo_snapshot` |
| `apply_repo_integration_changes` | `code_artifacts`, `repo_profile` | `repo_changes` |
| `validate_integration_design` | `workflow_nodes`, `code_artifacts` | `errors` (if validation fails) |
| `handle_error` | `errors` | `plan["failed"]`, `completed_steps` |
| `build_report` | All state | `report_markdown` |
| `persist_run_outcome` | `run_id`, `errors`, `completed_steps` | `persisted_ids` (outcome subset) |

**Doc-vs-Code**:
- **Doc says**: General flow description without field-level detail
- **Code shows**: Clear read/write contracts in node docstrings
- **Conclusion**: **Missing from docs** – field-level contracts are in code, not docs.

---

### Q3: What dataclasses represent Silver vs. Gold layer objects?

**Answer**: From `domain/models.py`:

**Silver Layer Models**:
- `SourceSystem` – API provider metadata
- `SpecDocument` – Raw spec file
- `SpecSection` – Logical section within spec
- `Endpoint` – API operation
- `EndpointParameter` – Path/query/header parameter
- `Schema` – JSON schema
- `SchemaField` – Field within schema
- `Entity` – Business object
- `EntityRelationship` – Entity relationship
- `Event` – Async event
- `SpecChunkEmbedding` – Chunk with embedding

**Gold Layer Models**:
- `IntegrationTask` – Task definition
- `IntegrationFlowNode` – Workflow node
- `IntegrationFlowEdge` – Workflow edge
- `EndpointBinding` – Node→endpoint mapping
- `Policy` – Auth/retry/rate-limit policy
- `CodeArtifact` – Generated code file
- `WorkflowTemplate` – Reusable template

**Doc-vs-Code**:
- **Doc says**: Lists Silver/Gold tables, but not all model classes
- **Code shows**: More models exist than documented (e.g., `SpecSection`, `SpecChunkEmbedding`, `WorkflowTemplate`)
- **Conclusion**: **Incomplete** – doc omits some models.

---

### Q4: What invariants should hold at checkpoint boundaries?

**Answer**: Based on `persist_silver_checkpoint` docstring:

**After `persist_silver_checkpoint`**:
- `state.source_system.id` is backfilled
- All `state.endpoints[*].id` are backfilled
- All `state.schemas[*].id` are backfilled
- `state.persisted_ids["silver_checkpoint"] == "completed"`
- `state.persisted_ids["source_system_id"]` is set

**After `persist_gold_checkpoint`** (implied by code):
- `state.integration_task` has persisted ID
- `state.workflow_nodes[*]` have persisted IDs
- `state.code_artifacts[*]` have persisted IDs
- `state.persisted_ids["gold_checkpoint"] == "completed"`

**Doc-vs-Code**:
- **Doc says**: No invariants documented
- **Code shows**: Implicit invariants in persistence node docstrings
- **Conclusion**: **Missing from docs** – invariants exist but undocumented.

---

## Doc-Drift Summary (Runtime + Data/State)

| Issue | Doc Claim | Code Reality | Impact |
|-------|-----------|--------------|--------|
| **Node count** | "22 LangGraph nodes: 21 in main path plus one error node" | 21 nodes registered; 22 files includes backup file | Minor – off by one |
| **Bronze layer** | "Bronze → openapi_spec (parsed dict)" only | Bronze includes `spec_documents`, `spec_sections`, `doc_chunks`, `openapi_spec` | Oversimplified |
| **Conditional edges** | Only mentions repo conditional | Two conditionals: repo + error routing | Incomplete |
| **Checkpoint recovery** | Not documented | Dual-layer: LangGraph native + application-level | Missing |
| **Parallel execution** | Not documented | Full `build_parallel_graph()` exists, `PARALLEL_WORKFLOW` flag | Missing feature |
| **Field-level contracts** | General flow only | Specific read/write per node in docstrings | Missing detail |
| **Silver/Gold models** | Lists tables, not all models | More models exist (SpecSection, SpecChunkEmbedding, WorkflowTemplate) | Incomplete |
| **Checkpoint invariants** | None | Implicit in persist node docstrings | Missing |
| **`WORKFLOW_NODE_ORDER`** | Not documented | 19-node constant for recovery | Missing |
| **`timed_node()` decorator** | Not documented | Critical for timing, tracing, checkpoint, skip logic | Missing |

---

## Key Findings for ARCHITECTURE.md Rewrite

### Must Fix (P0 Discrepancies):
1. **Node count**: Clarify there are 21 registered nodes, not 22
2. **Bronze layer definition**: Expand to include all four Bronze fields
3. **Conditional edges**: Document both repo and error conditionals
4. **Checkpoint recovery**: Add section explaining dual-layer checkpointing

### Should Add (Missing Sections):
1. **Parallel execution**: Document `PARALLEL_WORKFLOW` and `build_parallel_graph()`
2. **Node contracts table**: Add field-level read/write contracts
3. **State invariants**: Document what is guaranteed at each checkpoint
4. **`timed_node()` decorator**: Explain its role in observability and recovery

### Minor Fixes:
1. Update Silver/Gold model lists to include all domain classes
2. Document `WORKFLOW_NODE_ORDER` constant and its uses
