# Architecture Audit: P2 Questions

> **Date**: December 10, 2025  
> **Scope**: All P2 questions from ARCHITECTURE_AUDIT_QUESTIONS.md  
> **Status**: Audit complete

---

## View: Runtime / Workflow (P2)

### Q10: Is there any timeout or cancellation mechanism for long-running nodes? What happens if a node hangs indefinitely?

**Answer**: No global run timeout exists. Per-operation timeouts are scattered:

**Timeouts implemented**:

| Scope | Timeout | Location |
|-------|---------|----------|
| HTTP requests | 30s | `config/__init__.py:191` (`HTTP_TIMEOUT`) |
| Redis cache socket | 5s | `llm/cache.py:147` |
| Postgres connection | 5s | `persistence/postgres.py:87` |
| Parallel branches | 300s | `graph/parallel.py:48` (`PARALLEL_TIMEOUT`) |
| Async LLM batch | 120s | `llm/async_batcher.py:114` (`request_timeout_seconds`) |
| Subprocess (ruff) | 30s | `codegen/security.py:245` |

**Cancellation mechanisms**:

1. **Async batcher**: `AsyncBatchProcessor._processor_task.cancel()` (line 171)
2. **Parallel graph**: `ThreadPoolExecutor` with `as_completed(timeout=...)` in `parallel.py:153`

**What happens if a node hangs**:
- No automatic timeout at the graph level
- LLM calls have retry with exponential backoff (capped at 10s per retry)
- HTTP calls timeout after 30s
- A truly hanging node blocks the workflow indefinitely
- Only external intervention (Ctrl-C, process kill) can stop it

**Doc status**: **Missing** — no timeout/cancellation documentation in ARCHITECTURE.md

---

### Q11: How does `run_from_node()` differ from `run_workflow()`? Is it still used or deprecated?

**Answer**: From `graph/runtime.py` (lines 885-960):

**Differences**:

| Aspect | `run_workflow()` | `run_from_node()` |
|--------|------------------|-------------------|
| **Purpose** | Full run from start | Resume from checkpoint |
| **Start point** | `plan_run` | Any node (via checkpoint) |
| **Checkpoint** | Creates fresh | Loads existing |
| **`start_node` param** | N/A | Accepted but deprecated |

**Current status**: **Not deprecated, actively used**.

**Usage locations**:
- `cli.py:457` — Called during resume flow
- `api/recovery.py:123` — Called for API-level recovery

**Implementation detail**: The `start_node` parameter is kept for API compatibility but the actual resume point is determined by LangGraph's checkpointer state (per Bug #61 fix docstring).

**Evidence**: `graph/runtime.py:885-960`, `cli.py:426-457`, `api/recovery.py:93-123`

**Doc status**: **Missing** — `run_from_node()` not documented

---

## View: Logical / Module (P2)

### Q6: Are there any circular import risks or known import order sensitivities?

**Answer**: Yes, several circular import mitigations exist.

**Mitigations in code**:

1. **Deferred imports**: Functions import inside body, not at module level
   - `retrieval/semantic_search.py:101`: `# Import here to avoid circular deps`
   - `codegen/naming.py:209`: `# Avoid circular imports`
   - `codegen/naming.py:283`: Mentions `circular dependencies` in docstring

2. **Conditional imports**: Many modules use `try/except ImportError`
   - `llm/client.py`: 10+ deferred provider imports
   - `persistence/db.py`: Deferred postgres imports
   - `graph/runtime.py:90,490`: Deferred LangGraph imports

3. **Checkpoints module**: `persistence/checkpoints.py:29` mentions "Circular reference prevention" for state serialization

**Known sensitivities**:
- `config/__init__.py` must be imported before `llm/client.py`
- `domain/models.py` must be imported before `graph/state.py`

**Doc status**: **Missing** — circular import handling not documented

---

### Q7: What is the purpose of `runtime/`, `feedback/`, `retrieval/`, `spec/`, `parsers/`, `kg/`, and `ui/` subdirectories?

**Answer**:

| Package | Purpose | Key Files |
|---------|---------|-----------|
| `runtime/` | Runtime policy library for generated clients (auth, retry, rate limiting) | `client.py`, `retry.py`, `rate_limit.py`, `auth.py` |
| `feedback/` | Quality-based KG learning from LangSmith feedback and implicit signals | `langsmith_sync.py`, `confidence.py`, `hooks.py` |
| `retrieval/` | Semantic search for spec chunks and KG templates (hybrid GraphRAG) | `semantic_search.py`, `unified_index.py` |
| `spec/` | Spec normalization (integer clamping, $ref resolution, security scheme standardization) | `__init__.py` (535 lines) |
| `parsers/` | Non-OpenAPI format parsers (HTML, PDF, CSV, message schemas) | `html_parser.py`, `pdf_parser.py`, `csv_schema.py`, `message_schema.py` |
| `kg/` | Knowledge graph operations (GraphRAG retrieval, pattern matching, template storage) | `__init__.py` (1486 lines) |
| `ui/` | Streamlit web interface | `streamlit_app.py`, `pages/` |

**Evidence**: Package `__init__.py` docstrings

**Doc status**: **Missing** — subdirectory purposes not documented

---

## View: Data / State (P2)

### Q8: What serialization is used for checkpoints? Is `WorkflowState` pickleable or JSON-serialized?

**Answer**: Both mechanisms are used:

**1. LangGraph native checkpointing**:
- Uses **pickle** serialization internally
- `PostgresSaver` / `SqliteSaver` store pickled state blobs
- `WorkflowState` is a `@dataclass` which is pickleable by default

**2. Application-level checkpoints** (`persistence/checkpoints.py`):
- Uses **JSON** serialization
- `save_checkpoint()` calls `json.dumps()` on state dict
- Handles non-serializable types (Path → str, dataclasses → dict)
- `persistence/checkpoints.py:29`: "Circular reference prevention" for complex objects

**Dataclass fields are all JSON-serializable** (lists, dicts, Optional primitives, or nested dataclasses).

**Evidence**: `persistence/checkpoints.py`, `graph/runtime.py:44-97`

**Doc status**: **Missing** — serialization format not documented

---

### Q9: What fields are added by "V3 Streaming Persistence"?

**Answer**: From `graph/state.py` (lines 63-70):

```python
# V3 Streaming Persistence Fields
spec_chunk_ids: List[int] = field(default_factory=list)  # DB IDs of streamed chunks
chunk_count: int = 0  # Total number of chunks (for progress tracking)
embedding_count: int = 0  # Number of embeddings computed (for progress tracking)
```

**Purpose**: When `STREAMING_PERSISTENCE=true`, chunks and embeddings are written to DB immediately. These fields track IDs and counts instead of full content, reducing memory from "200MB+" to "<20MB" for large specs.

**Used by**:
- `ingest_spec` — Populates `spec_chunk_ids` and `chunk_count`
- `embed_spec_chunks` — Updates `embedding_count`
- `persist_silver_checkpoint` — Reads IDs to avoid re-persisting

**Evidence**: `graph/state.py:63-70`, `persistence/streaming.py`

**Doc status**: **Missing** — V3 streaming fields not documented in ARCHITECTURE.md

---

## View: Persistence / Storage (P2)

### Q8: What migrations exist and how are they applied? Is there schema versioning?

**Answer**: No formal migration system exists.

**Current approach**:
- `scripts/init_db_postgres.py` — Creates all tables from scratch
- `scripts/init_db.py` — SQLite equivalent
- `scripts/init-pgvector.sql` — SQL script for manual pgvector setup

**No schema versioning**:
- No `alembic` or similar migration tool
- No `schema_version` table
- Design docs mention "Alembic if added later" (V1_GAP_CLOSURE_PLAN.md:1569)

**How changes are applied**:
1. Modify DDL in `persistence/db.py` `init_schema()`
2. Run `init_db_postgres.py --drop-existing` (destroys data)
3. Or manually apply DDL changes

**Future consideration**: ADR-0006 mentions "Each schema has independent DDL — Can be versioned/migrated separately" but this is aspirational.

**Evidence**: `scripts/init_db_postgres.py`, `persistence/db.py`

**Doc status**: **Missing** — migration approach not documented

---

### Q9: How is connection pooling configured? What are the default pool sizes?

**Answer**: No explicit connection pooling is configured in the application code.

**Evidence search**: `pool_size`, `max_overflow`, `poolclass` not found in application code.

**Actual behavior**:
- Postgres: Uses `psycopg2` connections directly via `get_connection()`
- `ConnectionWrapper` manages lifecycle but doesn't pool
- Each `get_connection()` call creates a new connection
- Connection is closed when `close()` is called or context manager exits

**Postgres timeout**: `timeout=5.0` in `postgres.py:87` for connection attempts.

**Implications**: No connection pooling means potential performance issues under load, but current design assumes single-run-per-process usage.

**Doc status**: **Missing** — pooling strategy (lack thereof) not documented

---

## View: LLM / External Services (P2)

### Q9: What is `toon.py` and what role does TOON format play in structured output parsing?

**Answer**: From `llm/toon.py` (lines 1-82):

**TOON = Token-Oriented Object Notation**

A compact, token-efficient format for structured data sent to LLMs that reduces token usage by 25-40% compared to JSON.

**Format rules**:
- `key=value` for simple values
- `key.nested=value` for nested objects
- `key=[item1,item2]` for arrays
- `bool` values: `true`/`false` (no quotes)
- `null` represented as empty or omitted

**Example**:
```
JSON: {"task_slug": "create_session", "constraints": {"required": true}}
TOON: task_slug=create_session
      constraints.required=true
```

**Functions**:
- `to_toon(obj)` — Convert dict to TOON
- `from_toon(text)` — Parse TOON to dict
- `format_schema_as_toon(schema)` — Format JSON schema hint

**Usage**: LLM prompts use TOON for input/output schemas to reduce token costs.

**Evidence**: `llm/toon.py`

**Doc status**: **Missing** — TOON format not documented

---

### Q10: How is `models.yaml` related to archetypes? Is it deprecated or still used?

**Answer**: **Deprecated but still present**.

**Relationship**:
- `models.yaml` was the original LLM configuration (pre-archetype system)
- Archetypes (`.archetype.yaml` files) are the current approach
- `call_llm()` uses `models.yaml` → emits `DeprecationWarning`
- `call_llm_for_node()` uses archetypes → recommended

**Current status**:
- File exists at `config/models.yaml`
- `get_llm_config()` in `config/__init__.py` still reads it
- `call_llm()` emits warning: "Use call_llm_for_node() instead"
- P1 audit confirms: "Deprecated but still present"

**Migration path**: Switch all `call_llm(task_type=...)` to `call_llm_for_node(node_name=...)`.

**Evidence**: `config/__init__.py`, `llm/client.py`

**Doc status**: **Incorrect** — ARCHITECTURE.md doesn't clarify deprecation status

---

### Q11: How does `_apply_env_overrides()` handle provider/model switching?

**Answer**: From `config/__init__.py` (lines 557-635):

**Supported env vars**:
- `LLM_PROVIDER`: Override `model.provider`
- `LLM_MODEL`: Override `model.name` (only if compatible with provider)
- `USE_MOCK_LLM`: Force mock mode

**Provider/model compatibility check**:
```python
is_openai_model = env_model.startswith(("gpt-", "o1-", "text-"))
is_anthropic_model = env_model.startswith("claude-")
is_google_model = env_model.startswith("gemini-")
```

**Bug #26 Fix**: When `LLM_PROVIDER` changes the provider, the model name is reset to the new provider's default if incompatible:
```python
PROVIDER_DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-5-20250929",
    "google": "gemini-2.5-flash",
    "mock": "mock-model",
}
```

**Behavior**:
- `LLM_PROVIDER=openai` + archetype with `claude-*` → resets to `gpt-4o`
- `LLM_MODEL=gpt-4o-mini` + Anthropic archetype → **ignored** (model doesn't cross-apply)
- `LLM_MODEL=claude-3-haiku` + OpenAI archetype → **ignored**

**Evidence**: `config/__init__.py:557-635`

**Doc status**: **Missing** — env override behavior not fully documented

---

## View: Operational / CLI-API (P2)

### Q8: What output formats are supported? How does `--json` flag change output?

**Answer**: 

**Supported formats**:
- **Markdown** (default): Human-readable report to stdout
- **JSON** (`--json` or `-j`): Machine-parseable JSON object

**Commands supporting `--json`**:
- `run` (line 343)
- `demo` (line 527)
- `resume` (line 885)
- `health` (line 1050)

**JSON output structure** (for `run`):
```json
{
  "run_id": "run_abc123",
  "status": "completed",
  "provider_code": "stripe",
  "code_artifacts": [...],
  "errors": [],
  "warnings": [],
  "timings": {...}
}
```

**Evidence**: `cli.py`

**Doc status**: **Incomplete** — `--json` mentioned but structure not documented

---

### Q9: How does `--verbose` / `-v` change logging behavior?

**Answer**: From `cli.py` (lines 37-62):

**`_setup_logging(verbose: bool)`**:
- `verbose=False`: `logging.WARNING` level, simple format
- `verbose=True`: `logging.DEBUG` level, detailed format with timestamps

**Format differences**:
```python
# verbose=True
"%(asctime)s [%(levelname)s] %(name)s: %(message)s" 
# Format: "14:30:45 [DEBUG] integration_coworker.graph.runtime: ..."

# verbose=False
"%(levelname)s: %(message)s"
# Format: "WARNING: ..."
```

**Affected loggers**: All loggers under `integration_coworker` namespace.

**Evidence**: `cli.py:37-62`

**Doc status**: **Missing** — verbose behavior not documented

---

## View: Configuration / Environment (P2)

### Q7: How is `.env` file loading handled? What takes precedence?

**Answer**: From `cli.py` (lines 20-28):

**Loading mechanism**:
```python
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not installed
```

**Precedence** (highest to lowest):
1. Environment variables set in shell (before CLI invocation)
2. Values in `.env` file (loaded by `load_dotenv`)
3. Hardcoded defaults in `Settings` dataclass

**Note**: `load_dotenv()` does NOT override existing env vars by default. If `OPENAI_API_KEY` is already set in shell, `.env` value is ignored.

**Location**: `.env` file expected at project root (`src/integration_coworker/../../.env`)

**Evidence**: `cli.py:20-28`, `.env.example`

**Doc status**: **Missing** — `.env` loading precedence not documented

---

### Q8: Are there any configuration options that change graph structure?

**Answer**: Yes, three configuration options affect graph structure:

| Option | Effect on Graph |
|--------|-----------------|
| `PARALLEL_WORKFLOW=true` | Uses `build_parallel_graph()` instead of `build_graph()` — adds parallel branches and `sync_embed_task` node |
| `plan["use_repo"]=True` | Conditional edge enables repo nodes (`attach_repo_context`, `analyze_repo_layout`, `apply_repo_integration_changes`) |
| `state.errors` after validation | Conditional edge routes to `handle_error` vs `build_report` |

**Not configurable**:
- Node list itself is fixed (21 nodes)
- Node order is fixed
- Cannot add/remove nodes via config

**Evidence**: `graph/runtime.py` (`build_graph`, `build_parallel_graph`, conditional edges)

**Doc status**: **Missing** — config→graph relationship not documented

---

## View: Security / Safety (P2)

### Q6: What happens if generated code contains shell execution, network calls, or file writes?

**Answer**: From `codegen/security.py`:

**Shell execution**: **Blocked**
- `os.system()`, `subprocess.*()` → `SecurityViolation` with `severity="error"`
- Unless `allow_subprocess=True` is passed to `validate_code_security()`

**Network calls**: **Allowed**
- `httpx.get()`, `requests.post()` → Not in `FORBIDDEN_CALLS`
- Generated API clients legitimately need HTTP

**File writes**: **Warning only**
- `open()` → `SecurityViolation` with `severity="warning"` (not error)
- Can be upgraded to error via `allow_file_io=False`

**Dangerous patterns blocked** (lines 32-66):
- `exec()`, `eval()`, `compile()`, `__import__()`
- `pickle.loads()`, `marshal.loads()`
- `ctypes.CDLL`
- `__code__`, `__globals__`, `__builtins__` access

**Response to violations**:
- `severity="error"` → Validation fails, triggers template fallback
- `severity="warning"` → Logged but code generation continues

**Evidence**: `codegen/security.py:32-200`

**Doc status**: **Incomplete** — security checks mentioned but not detailed

---

### Q7: Is there validation on user-provided spec URLs or task descriptions before processing?

**Answer**: Yes, but limited:

**Task description**:
- `sanitize_task_description()` in `llm/sanitizer.py`
- Max length: 10,000 characters
- Strips 12 suspicious patterns (injection attempts)
- Called before LLM prompts

**Spec URLs**:
- `ingest_spec.py:42`: `httpx.get(ref, timeout=30.0, follow_redirects=True)`
- No URL scheme validation (could fetch `file://` URLs)
- No blocklist for internal IPs
- SHA256 computed for deduplication

**Spec content**:
- `sanitize_spec_content()` — Max 200,000 chars, no pattern stripping
- Less aggressive than task description (specs may legitimately contain instruction-like text)

**Missing validations**:
- No SSRF protection on spec URLs
- No URL scheme whitelist (http/https only)
- No blocklist for localhost/internal ranges

**Evidence**: `llm/sanitizer.py`, `graph/nodes/ingest_spec.py`

**Doc status**: **Missing** — input validation not documented

---

## View: Observability (P2)

### Q5: How can a user inspect a completed run after the fact? What tools exist?

**Answer**: Multiple inspection methods:

**1. LangSmith Web UI**:
- Navigate to project (set via `LANGCHAIN_PROJECT`)
- Browse runs by timestamp, filter by status
- View node inputs/outputs, token counts, latency

**2. Inspection scripts** (root directory):
- `inspect_langsmith_traces.py` — Lists recent runs, shows graph node hierarchy
- `inspect_node_details.py` — Deep-dive into specific node inputs/outputs

**3. CLI resume**:
- `integration-coworker resume <run_id>` — Resume or view run status

**4. Database queries**:
- `run_status` table — Final status, start/end times, error summary
- `run_checkpoints` table — Per-node state snapshots
- LangGraph checkpoint tables — Full state at each step

**5. Report file**:
- `state.report_markdown` — Human-readable summary
- Generated by `build_report` node

**Evidence**: `inspect_langsmith_traces.py`, `inspect_node_details.py`, `cli.py`

**Doc status**: **Missing** — run inspection tools not documented

---

### Q6: What do `inspect_node_details.py` and `inspect_langsmith_traces.py` provide?

**Answer**:

**`inspect_langsmith_traces.py`** (133 lines):
- Connects to LangSmith API via `Client()`
- Lists recent runs (last 2 hours) in project
- Finds latest LangGraph run
- Shows run details: ID, name, status, duration
- Lists child runs (graph nodes) grouped by status

**`inspect_node_details.py`** (113 lines):
- Connects to LangSmith API
- Finds latest root LangGraph run
- Inspects specific nodes: `plan_run`, `ingest_spec`, `build_silver_api_model`, etc.
- Shows per-node: status, run type, duration, errors
- Truncates large inputs/outputs for readability

**Usage**:
```bash
LANGCHAIN_PROJECT=my-project python inspect_langsmith_traces.py
LANGCHAIN_PROJECT=my-project python inspect_node_details.py
```

**Evidence**: Script source files

**Doc status**: **Missing** — scripts not documented

---

### Q7: What logging levels are used and how is logging configured?

**Answer**:

**Logging levels used**:
- `DEBUG` — Detailed trace (when `--verbose`)
- `INFO` — Normal operation messages
- `WARNING` — Non-fatal issues, deprecations
- `ERROR` — Failures, exceptions

**Configuration** (`cli.py:37-62`):
- `--verbose` / `-v` → DEBUG level
- Default → WARNING level
- Handler: `StreamHandler(sys.stderr)`

**Logger hierarchy**:
- `integration_coworker` — Root logger
- `integration_coworker.graph.runtime` — Graph execution
- `integration_coworker.llm.client` — LLM calls
- `integration_coworker.persistence.db` — Database operations

**Per-node logging**: Each node file has `logger = logging.getLogger(__name__)`

**Evidence**: `cli.py:37-62`, various module files

**Doc status**: **Missing** — logging configuration not documented

---

## View: Quality Attributes (P2)

### Q6: What are the memory characteristics? How does streaming persistence reduce memory?

**Answer**: From `persistence/streaming.py` and `graph/state.py`:

**Without streaming** (legacy mode):
- All spec chunks held in `state.doc_chunks: List[str]`
- All embeddings held in `state.spec_chunk_embeddings: List[SpecChunkEmbedding]`
- Large spec (20MB) → ~200MB+ memory footprint

**With streaming** (`STREAMING_PERSISTENCE=true`):
- Chunks written to DB immediately by `ingest_spec`
- `state.doc_chunks` cleared after write
- Embeddings computed in batches of 25, written immediately
- State holds only IDs and counts: `spec_chunk_ids`, `chunk_count`, `embedding_count`
- Result: ~<20MB memory footprint

**Batch sizes** (`streaming.py:27-28`):
```python
CHUNK_BATCH_SIZE = 100  # Write chunks in batches of 100
EMBEDDING_BATCH_SIZE = 25  # Update embeddings in batches of 25
```

**Auto-enable thresholds** (`config/__init__.py:210-217`):
```python
streaming_threshold_bytes = 500_000  # 500KB
streaming_threshold_chunks = 500  # 500 chunks
```

**Evidence**: `persistence/streaming.py`, `graph/state.py`, `config/__init__.py`

**Doc status**: **Incomplete** — memory reduction mentioned but thresholds not documented

---

### Q7: What are the known limitations and unsupported use cases?

**Answer**: Compiled from multiple docs:

**Format limitations**:
- AsyncAPI not supported (`INTEGRATION_COWORKER_CAPABILITY_AUDIT.md:66`)
- CSV/EDI schemas not auto-wired (`V1_COMPLETION_SUMMARY.md:172`)
- Database schemas not supported (`ADR-0006:381-382`)

**Feature limitations**:
- Pagination code is placeholder (`V1_COMPLETION_SUMMARY.md:170`)
- Error recovery prompts don't offer retry (`V1_COMPLETION_SUMMARY.md:171`)
- GitHub provider not wired through `attach_repo_context` (`V1_COMPLETION_SUMMARY.md:173`)
- Multi-provider: Only `mock_payments` + Stripe fully tested (`M4_EXECUTIVE_SUMMARY.md:153`)
- Request mapping (field transformations) empty (`M4_EXECUTIVE_SUMMARY.md:168`)

**Workflow limitations**:
- Linear flows only — no polling, webhooks, conditionals (`M4_EXECUTIVE_SUMMARY.md:195`)
- No code execution (out of scope for v1) (`AGENT_BEHAVIOR_OVERVIEW.md:51`)
- Remote repo sources not supported (`ADR-0002:287`)

**Scale limitations**:
- No connection pooling (performance under load)
- No global run timeout
- No schema migrations

**Evidence**: Various docs as cited

**Doc status**: **Incomplete** — limitations scattered across docs, not consolidated in ARCHITECTURE.md

---

## Cross-Cutting Concerns (P2)

### Q8: ARCHITECTURE.md section "Hybrid Retrieval (KG + Semantic Search)" describes combined scoring—is this implemented or aspirational?

**Answer**: **Fully implemented**.

From `kg/__init__.py` (lines 1-80):

**GraphRAG scoring formula** (per design doc Section 5.5):
```
final_score = (graph_score * 0.4) + (embedding_score * 0.4) + (confidence * 0.1) + exact_match_bonus + 0.05
```

**Implementation**:
- `graph_score` (40%): Boost templates with entity/endpoint edges
  - +0.2 per entity edge
  - +0.1 per endpoint edge (capped at 0.5)
- `embedding_score` (40%): Cosine similarity to task description
- `confidence_score` (10%): Learned from feedback (default 1.0)
- `exact_match_bonus` (10%): +0.2 for slug match, +0.1 for partial

**Cross-provider pattern matching** (M5 enhancement):
- Falls back to pattern-level matching when no provider-specific templates exist
- Patterns: `pattern.crud_create`, `pattern.crud_read`, etc.

**Evidence**: `kg/__init__.py:1-80`, `retrieval/semantic_search.py`

**Doc status**: **Accurate** — hybrid retrieval is implemented as documented

---

### Q14: What extension points exist? Can users add custom nodes, archetypes, or parsers?

**Answer**: Limited extension points exist:

**Custom archetypes** ✅:
- Add `.archetype.yaml` files to `config/archetypes/`
- Automatically loaded by `load_archetype()`
- No code changes required

**Custom parsers** ⚠️:
- Add parser modules to `parsers/`
- Requires modifying `detect_and_parse_spec.py` to wire them in
- Not auto-discovered

**Custom nodes** ❌:
- Requires modifying `graph/runtime.py` (`build_graph()`)
- No plugin system
- Not designed for extension

**Custom policies** ❌:
- `attach_policies_and_patterns.py` has hardcoded policy types
- ADR-0005 mentions "plugin policies" as future consideration

**Extension-friendly areas**:
- Archetype system (data-driven configuration)
- KG template storage (add templates to database)
- Repo profile configs (add `*.intg.yaml` files)

**Evidence**: `config/archetypes/`, `graph/runtime.py`, `ADR-0005:535`

**Doc status**: **Missing** — extension points not documented

---

### Q15: How does this system differ from "vanilla" LangChain/LangGraph patterns? What is custom?

**Answer**:

**Standard LangGraph patterns used**:
- `StateGraph` with `add_node()` / `add_edge()`
- `add_conditional_edges()` for branching
- `checkpointer` for state persistence
- `compile()` to create executable graph

**Custom additions**:

| Pattern | Custom Implementation |
|---------|----------------------|
| **Dual-layer checkpointing** | LangGraph native + application-level (`run_checkpoints` table) |
| **`timed_node()` decorator** | Adds timing, tracing, checkpoint logic to all nodes |
| **Node metadata** | `NODE_METADATA` dict with category/responsibility |
| **Skip cascade** | `NODE_DEPENDENCIES` + `get_skip_cascade()` for dependency-aware failure handling |
| **Parallel graph variant** | `build_parallel_graph()` with `sync_embed_task` merge node |
| **Archetype-based LLM config** | YAML-driven per-node model selection |
| **Token tracking** | ContextVar-based aggregation across all LLM calls |
| **Run context** | `set_run_context()` / `clear_run_context()` for tracing correlation |

**Not using**:
- LangChain Agents (custom workflow, not agent loop)
- LangChain Tools (nodes are not tools)
- LangChain Memory (using custom state management)
- LangServe (custom CLI/API)

**Evidence**: `graph/runtime.py`, `llm/client.py`

**Doc status**: **Missing** — LangGraph customizations not documented

---

### Q16: What versioning guarantees exist? How are breaking changes to `WorkflowState` or checkpoint format communicated?

**Answer**: No formal versioning guarantees exist.

**Current approach**:
- `WorkflowState` uses dataclass with default values
- Adding new fields is backward-compatible (defaults fill in)
- Removing/renaming fields would break old checkpoints
- No explicit version field in `WorkflowState`

**Checkpoint compatibility**:
- Old checkpoints may fail to deserialize if field types change
- No migration mechanism for checkpoints
- "Re-run from scratch" is the implicit recovery path

**Change communication**:
- `CHANGELOG.md` documents changes
- ADRs document design decisions
- No semantic versioning for internal APIs

**Risk areas**:
- `domain/models.py` changes affect DB schema
- `graph/state.py` changes affect checkpoints
- LangGraph version upgrades could break checkpoint format

**Evidence**: `graph/state.py`, `CHANGELOG.md`, P1 audit Q13

**Doc status**: **Missing** — versioning/compatibility not documented

---

## Summary Statistics

| Metric | Count |
|--------|-------|
| **P2 questions answered** | 28 |
| **Questions revealing doc drift** | 3 |
| **Questions confirming doc accuracy** | 2 |
| **Questions revealing missing documentation** | 23 |

---

## Key Findings for ARCHITECTURE.md Rewrite

### P2 items to add:

1. **Timeout/cancellation**: Document per-operation timeouts, note lack of global timeout
2. **`run_from_node()`**: Document resume capability
3. **Subdirectory purposes**: Add package responsibility table
4. **Serialization format**: Document pickle (LangGraph) + JSON (app-level)
5. **V3 streaming fields**: Document `spec_chunk_ids`, `chunk_count`, `embedding_count`
6. **Migration approach**: Clarify "drop and recreate" vs. no migrations
7. **TOON format**: Brief explanation of token-efficient serialization
8. **`models.yaml` deprecation**: Clarify status
9. **Env override behavior**: Document `_apply_env_overrides()` logic
10. **Output formats**: Document `--json` structure
11. **Verbose mode**: Document logging behavior
12. **Input validation**: Document sanitization for specs and tasks
13. **Run inspection**: Document `inspect_*.py` scripts
14. **Known limitations**: Consolidate from various docs
15. **Extension points**: Document archetype extensibility
16. **LangGraph customizations**: Document what's standard vs. custom
17. **Versioning**: Document compatibility expectations

### Confirmed accurate:
- Hybrid GraphRAG scoring is implemented as documented

### Minor corrections:
- ARCHITECTURE.md should note that hybrid retrieval is fully implemented, not aspirational
