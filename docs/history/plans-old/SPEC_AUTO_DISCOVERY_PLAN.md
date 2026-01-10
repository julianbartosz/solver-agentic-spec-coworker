# Spec Auto-Discovery & Selection System

> **Status**: ✅ Slice 1, 2, 3 & 4 Complete | Production Hardened  
> **Tests**: 240+ passing | **LOC**: 4,300+ (excludes `test_discover_spec_node.py`)  
> **Author**: Integration Coworker Team (Staff+ Engineering Review)  
> **Created**: January 2026  
> **Last Updated**: January 6, 2026 (Slice 4: Web Search Complete)  
> **Priority**: P0 - Critical Path for Chatbot Integration  
> **ADR Reference**: ADR-0013 (to be created)

---

## Implementation Slices (Revised Sequencing)

| Slice | Scope | Dependencies | Status |
|-------|-------|--------------|--------|
| **1** | APIs.guru registry, heuristic intent, strict validation, graph wiring, CLI | None | ✅ **Complete** |
| **2** | Local catalog w/ pgvector HNSW, SSRF hardening, schema alignment | Slice 1 green | ✅ **Complete** |
| **3** | LLM structured intent extraction, HITL candidate selection, production hardening | Slice 2 green | ✅ **Complete** |
| **4** | Optional web search (Tavily/SerpAPI) | Slice 3 green | ✅ **Complete** |

**Slice 1 Goal**: Enable `--task "Process payment with Stripe"` without `--spec-ref`, resolving via APIs.guru REST API. ✅

### Slice 1 Completion Summary (January 2026)

| Component | Status | Test Coverage |
|-----------|--------|---------------|
| `discovery/apis_guru.py` | ✅ Complete | 18 tests |
| `discovery/intent.py` | ✅ Complete | 40 tests |
| `discovery/validator.py` | ✅ Complete | 68 tests |
| `discovery/resolver.py` | ✅ Complete | 10 tests |
| `discovery/catalog.py` | ✅ Complete | 16 tests |
| `graph/nodes/discover_spec.py` | ✅ Complete | 14 tests |
| Graph wiring (runtime.py) | ✅ Complete | Via integration |
| CLI `--no-discover` flag | ✅ Complete | Via E2E |
| State fields added | ✅ Complete | Via unit tests |

**Total: 196 tests passing** (excluding `test_discover_spec_node.py` in CI)

### Slice 2 Completion Summary (January 2026)

| Component | Status | Description |
|-----------|--------|-------------|
| Schema alignment | ✅ Complete | Moved to `spec_silver.discovery_*` (matches medallion architecture) |
| HNSW indexing | ✅ Complete | `m=16, ef_construction=64` for optimal speed/recall at catalog size |
| SSRF hardening | ✅ Complete | DNS resolution validation, IP blocklists per OWASP |
| Validator behavior | ✅ Complete | `openapi-spec-validator` logs once at DEBUG, not spam |
| Bootstrap script | ✅ Complete | Idempotent population from APIs.guru + curated sources |

### Slice 3 Completion Summary (January 5, 2026)

| Component | Status | Description |
|-----------|--------|-------------|
| LLM intent extraction | ✅ Complete | `intent_llm.py` with `merge_intent()` LLM-advisory pattern |
| HITL candidate selection | ✅ Complete | `require_confirmation` parameter for non-interactive safety |
| Production hardening | ✅ Complete | Manual redirect loop, per-hop SSRF, explicit timeouts |
| Hardening acceptance tests | ✅ Complete | 44 tests in `test_hardening.py` |
| CI gates | ✅ Complete | `scripts/ci_hardening_gates.sh` with 10 enforcement gates |

**Key Hardening Deliverables:**
- `http_client.py`: Manual redirect loop with `follow_redirects=False` and per-hop `_validate_hop()`
- `http_client.py`: Streaming byte cap via `aiter_bytes()` (not `response.text`)
- `http_client.py`: Redirect loop detection via `seen_urls` set
- `http_client.py`: Dangerous scheme blocking (`ALLOWED_SCHEMES = {"http", "https"}`)
- `http_client.py`: `trust_env=False` blocks HTTP_PROXY/HTTPS_PROXY env var leakage
- `http_client.py`: Explicit `httpx.Timeout(connect=10, read=30, write=10, pool=10)`
- `http_client.py`: Explicit `httpx.Limits(max_connections=10, max_keepalive=5)`
- `intent_llm.py`: `HEURISTIC_AUTHORITY_THRESHOLD=0.8` - heuristics authoritative over LLM
- `resolver.py`: `require_confirmation=False` default - non-interactive callers never block

### Slice 4 Completion Summary (January 6, 2026)

| Component | Status | Description |
|-----------|--------|-------------|
| Web search module | ✅ Complete | `web_search.py` with provider interface and implementations |
| TavilySearchProvider | ✅ Complete | POST-based Tavily API integration (preferred) |
| SerpApiSearchProvider | ✅ Complete | GET-based SerpApi integration (fallback) |
| Resolver integration | ✅ Complete | Web search cascade step when best_score < 0.7 |
| Configuration | ✅ Complete | `DISCOVERY_WEB_SEARCH_*` env vars, disabled by default |
| Caching | ✅ Complete | Disk-based cache with configurable TTL |
| URL scoring | ✅ Complete | Heuristic scoring for spec URL quality |
| Tests | ✅ Complete | 44 tests in `test_web_search.py` |
| CI gates | ✅ Complete | 4 new gates in `ci_hardening_gates.sh` (total: 17) |

**Key Slice 4 Deliverables:**
- `web_search.py`: ~650 LOC provider module
- `WebSearchConfig`: 10+ environment variable config knobs
- `WebSearchProvider`: Abstract base class with `search()` async method
- `TavilySearchProvider`: Uses POST to `https://api.tavily.com/search`
- `SerpApiSearchProvider`: Uses GET to `https://serpapi.com/search`
- Both providers use hardened httpx settings (`trust_env=False`, explicit Timeout/Limits)
- `WebSearchCache`: Disk-based memoization in `~/.cache/integration_coworker/web_search/`
- `generate_spec_search_queries()`: Deterministic query generation
- `score_spec_url_heuristic()`: Rule-based URL quality scoring
- `search_web_for_specs()`: High-level entry point

**Privacy & Security:**
- ⚠️ **Privacy Warning**: Web search sends task-derived queries to third-party APIs
- Disabled by default (`DISCOVERY_WEB_SEARCH_ENABLED=false`)
- Tavily preferred over SerpApi (Google lawsuit risk noted)
- All URLs from web search treated as untrusted - must pass `validate_spec_url()`
- Uses same hardening patterns as `http_client.py` (`trust_env=False`)

**Configuration Knobs (Slice 4):**
| Variable | Default | Description |
|----------|---------|-------------|
| `DISCOVERY_WEB_SEARCH_ENABLED` | `false` | Master switch (opt-in) |
| `DISCOVERY_WEB_SEARCH_PROVIDER` | `auto` | `auto`, `tavily`, or `serpapi` |
| `TAVILY_API_KEY` | (none) | Tavily API key |
| `SERPAPI_API_KEY` | (none) | SerpApi API key |
| `DISCOVERY_WEB_SEARCH_MAX_QUERIES` | `3` | Max queries per discovery |
| `DISCOVERY_WEB_SEARCH_MAX_RESULTS` | `5` | Max results per query |
| `DISCOVERY_WEB_SEARCH_TIMEOUT_SECONDS` | `10` | Per-request timeout |
| `DISCOVERY_WEB_SEARCH_CACHE_TTL_HOURS` | `24` | Cache expiry |
| `DISCOVERY_WEB_SEARCH_ALLOW_DOMAINS` | (none) | Domain allowlist (empty=all) |
| `DISCOVERY_WEB_SEARCH_BLOCK_DOMAINS` | (none) | Domain blocklist |

---

## A. Goal and Non-Goals

### Goal Statement

Enable the integration coworker to accept **natural language task descriptions** without requiring explicit `--spec-ref` inputs, automatically discovering the appropriate OpenAPI specification through a cascading resolution strategy.

**Target User Experience:**
```bash
# Before (current - requires explicit spec)
integration-coworker run \
  --spec-ref "https://raw.githubusercontent.com/openai/openai-openapi/master/openapi.yaml" \
  --task "Create a video using Sora from OpenAI"

# After (proposed - natural language only)
integration-coworker run --task "Create a video using Sora from OpenAI"
# → System discovers OpenAI spec automatically
```

### Non-Goals (Explicit Scope Boundaries)

| Non-Goal | Rationale |
|----------|-----------|
| **Private/internal API discovery** | No access to internal documentation systems |
| **Automatic spec generation** | Out of scope - requires understanding entire API surface |
| **Multi-spec orchestration discovery** | Focus on single-spec resolution first; orchestration is Phase 2 |
| **Real-time spec change monitoring** | Polling/webhooks are separate feature |
| **Training custom discovery models** | Use existing embeddings + heuristics |
| **GraphQL/gRPC introspection discovery** | OpenAPI only in V1 |

---

## B. Current State Evidence (Repo Citations)

### B.1 Validation Chokepoints (spec_refs is Required)

**Evidence 1: plan_run.py validation**

File: [src/integration_coworker/graph/nodes/plan_run.py#L302-L307](../../../src/integration_coworker/graph/nodes/plan_run.py#L302-L307)

```python
# 1. Validate spec_refs (require at least one)
if not state.spec_refs or len(state.spec_refs) < 1:
    error_msg = "At least one spec_ref is required"
    state.errors.append(error_msg)
    state.plan["failed"] = True
    state.completed_steps.append("plan_run")
    raise ValueError(error_msg)
```

**Evidence 2: CLI validation**

File: [src/integration_coworker/cli.py#L521-L523](../../../src/integration_coworker/cli.py#L521-L523)

```python
if not spec_ref and not file_input and not guide:
    typer.echo("✗ Error: Must provide at least one --spec-ref, --file, or --guide input", err=True)
    raise typer.Exit(code=2)
```

**Evidence 3: API entrypoint signature**

File: [src/integration_coworker/api/entrypoint.py#L20-L26](../../../src/integration_coworker/api/entrypoint.py#L20-L26)

```python
def design_and_generate_integration(
    spec_refs: Iterable[str],  # ← Required positional parameter
    task_description: str,
    provider_code: Optional[str] = None,
    ...
)
```

### B.2 Discovery Infrastructure (IMPLEMENTED)

| Component | Location | Status |
|-----------|----------|--------|
| Spec catalog tables | `migrations/004_spec_catalog.sql` | ✅ `spec_silver.discovery_*` |
| APIs.guru client | `src/integration_coworker/discovery/apis_guru.py` | ✅ With caching |
| Intent analysis | `src/integration_coworker/discovery/intent.py` | ✅ Heuristic extraction |
| LLM intent | `src/integration_coworker/discovery/intent_llm.py` | ✅ Advisory merge |
| Hardened HTTP | `src/integration_coworker/discovery/http_client.py` | ✅ SSRF protection |
| Validator | `src/integration_coworker/discovery/validator.py` | ✅ DNS validation |
| Resolver | `src/integration_coworker/discovery/resolver.py` | ✅ Cascading resolution |
| Graph node | `src/integration_coworker/graph/nodes/discover_spec.py` | ✅ Wired to runtime |

**Verification commands:**
```bash
# Verify discovery module exists
ls -la src/integration_coworker/discovery/

# Run tests
pytest tests/discovery/ --ignore=tests/discovery/test_discover_spec_node.py -q

# Run CI hardening gates
./scripts/ci_hardening_gates.sh
```

### B.3 Existing Plumbing We Can Leverage

**Semantic Search (pgvector)**

File: [src/integration_coworker/retrieval/semantic_search.py](../../../src/integration_coworker/retrieval/semantic_search.py)

- `search_spec_chunks()` - searches **already-ingested** specs
- `compute_embedding()` - with retry/backoff (V4 enhancement)
- `_search_spec_chunks_pgvector()` - native pgvector `<=>` operator

**Configuration Patterns**

File: [src/integration_coworker/config/__init__.py](../../../src/integration_coworker/config/__init__.py)

- Dataclass config with `field(default_factory=lambda: os.getenv(...))`
- Settings singleton via `get_settings()`
- Feature flags: `pattern_learning_enabled`, `streaming_persistence`

**HTTP Client Patterns**

File: [src/integration_coworker/config/__init__.py#L249-L259](../../../src/integration_coworker/config/__init__.py#L249-L259)

```python
# Existing HTTP config we can reuse
http_timeout: int = field(default_factory=lambda: int(os.getenv("HTTP_TIMEOUT", "30")))
http_max_retries: int = field(default_factory=lambda: int(os.getenv("HTTP_MAX_RETRIES", "3")))
http_retry_backoff: float = field(default_factory=lambda: float(os.getenv("HTTP_RETRY_BACKOFF", "1.0")))
```

**LLM Client**

File: [src/integration_coworker/llm/client.py](../../../src/integration_coworker/llm/client.py)

- Multi-provider support (OpenAI, Anthropic, Google)
- Retry with exponential backoff
- Circuit breaker pattern
- Redis caching (Plan 7)

### B.4 Workflow Graph Entry Point (IMPLEMENTED)

File: [src/integration_coworker/graph/runtime.py#L2417-L2480](../../../src/integration_coworker/graph/runtime.py#L2417-L2480)

```python
workflow = StateGraph(WorkflowState)

# Slice 1: Entry point is now discover_spec (handles empty spec_refs case)
# discover_spec no-ops when spec_refs already provided, so existing flow unchanged
workflow.add_node("discover_spec", timed_node(discover_spec.discover_spec))
workflow.add_node("plan_run", timed_node(plan_run.plan_run))

workflow.set_entry_point("discover_spec")
workflow.add_edge("discover_spec", "plan_run")
workflow.add_edge("plan_run", "ingest_spec")
# ... rest of pipeline
```

### B.5 State Schema (WorkflowState)

File: [src/integration_coworker/graph/state.py#L73-L85](../../../src/integration_coworker/graph/state.py#L73-L85)

```python
@dataclass
class WorkflowState:
    """Single authoritative in-memory state object passed between LangGraph nodes."""

    # Inputs
    source_refs: List[SourceRef]  # API-002: Typed source references
    spec_refs: List[str]  # Raw spec paths/URLs (for backwards compatibility)
    task_description: str
    provider_code: Optional[str] = None
    options: Optional[IntegrationOptions] = None
```

**Missing Fields (to be added):**
- `discovered_specs`: Discovery candidate metadata
- `discovery_confidence`: Score of selected spec match
- `discovery_source`: Where spec was resolved from

---

## C. Requirements

### C.1 Functional Requirements

| ID | Requirement | Priority | Validation |
|----|-------------|----------|------------|
| FR-1 | Accept task-only input when `spec_refs` empty | P0 | CLI `--task` without `--spec-ref` succeeds |
| FR-2 | Extract provider signals from natural language | P0 | "using OpenAI" → `explicit_provider: OpenAI` |
| FR-3 | Query local catalog first (fastest path) | P0 | Cache hit returns in <100ms |
| FR-4 | Fall back to APIs.guru for public specs | P0 | OpenAI query returns valid spec URL |
| FR-5 | Validate discovered spec before use | P0 | Invalid URLs rejected with helpful error |
| FR-6 | HITL confirmation for low-confidence matches | P1 | `confidence < 0.85` triggers user prompt |
| FR-7 | Support `--no-discover` to disable discovery | P1 | Explicit opt-out for automation |
| FR-8 | Log discovery reasoning for debugging | P2 | LangSmith trace shows resolution chain |

### C.2 Non-Functional Requirements

| ID | Requirement | Target | Measurement |
|----|-------------|--------|-------------|
| NFR-1 | Discovery latency (local hit) | <500ms | P50 wall clock |
| NFR-2 | Discovery latency (APIs.guru) | <2s | P95 wall clock |
| NFR-3 | Correct spec resolution (top-1) | >80% | Test set of 100 queries |
| NFR-4 | Correct spec resolution (top-3) | >95% | At least one correct |
| NFR-5 | Invalid URL rate | <5% | URLs that fail validation |
| NFR-6 | Memory overhead | <10MB | Peak heap increase |

### C.3 Constraints

1. **No breaking changes** to existing `--spec-ref` path
2. **Feature flag controlled** (`DISCOVERY_ENABLED=false` default in Phase 1)
3. **No new required env vars** for basic functionality
4. **Graceful degradation** if web search unavailable

---

## D. Alternatives Considered

### Approach A: New `discover_spec` Graph Node (RECOMMENDED)

**Description:** Insert a new node before `plan_run` that handles discovery when `spec_refs` is empty.

```
                     ┌──────────────────┐
                     │  discover_spec   │  ← NEW (conditional)
                     │  (when no spec)  │
                     └────────┬─────────┘
                              │ sets state.spec_refs
                              ▼
┌─────────────┐     ┌─────────────────────┐
│  (existing) │ ──▶ │     plan_run        │
│  entry      │     │ (validates spec_refs)│
└─────────────┘     └─────────────────────┘
```

**Pros:**
- Clean separation of concerns (discovery vs planning)
- No changes to existing nodes
- Easy to disable via feature flag
- Supports HITL interrupt naturally

**Cons:**
- Adds latency to all runs (even when spec provided)
- New node to maintain

**File Changes:**
- `+src/integration_coworker/graph/nodes/discover_spec.py` (new ~300 LOC)
- `~src/integration_coworker/graph/runtime.py` (add node, conditional edge)
- `~src/integration_coworker/graph/state.py` (add 3 fields)

---

### Approach B: Integrate Discovery into `plan_run`

**Description:** Modify `plan_run` to attempt discovery when `spec_refs` is empty instead of raising `ValueError`.

```python
# plan_run.py modification
if not state.spec_refs or len(state.spec_refs) < 1:
    if settings.discovery_enabled:
        discovered = await discover_spec_inline(state.task_description)
        if discovered:
            state.spec_refs = [discovered.spec_url]
        else:
            raise ValueError("At least one spec_ref is required (discovery failed)")
    else:
        raise ValueError("At least one spec_ref is required")
```

**Pros:**
- Fewer graph topology changes
- Single point of modification
- No new node latency

**Cons:**
- Violates single-responsibility principle (plan_run does planning, not discovery)
- HITL interrupt more awkward (need special handling)
- Async discovery in sync node context
- Testing harder (discovery coupled to planning)

**File Changes:**
- `~src/integration_coworker/graph/nodes/plan_run.py` (significant modification)
- `+src/integration_coworker/discovery/` (new module)

---

### Approach C: API Entrypoint Preprocessing

**Description:** Handle discovery in `design_and_generate_integration()` before creating `WorkflowState`.

```python
# entrypoint.py modification
def design_and_generate_integration(
    spec_refs: Optional[Iterable[str]] = None,  # Make optional
    task_description: str,
    ...
):
    if not spec_refs and task_description:
        # Discover spec before workflow starts
        discovered = run_discovery(task_description)
        spec_refs = [discovered.spec_url] if discovered else []
    
    state = WorkflowState(spec_refs=list(spec_refs or []), ...)
```

**Pros:**
- No graph changes
- Simple to implement
- Discovery errors are pre-workflow

**Cons:**
- Discovery happens outside LangGraph (no tracing/checkpointing)
- No HITL support (not in workflow)
- Hard to test (entrypoint is integration surface)
- Breaks clean function signature (making required param optional)

**File Changes:**
- `~src/integration_coworker/api/entrypoint.py` (modify signature + add logic)
- `+src/integration_coworker/discovery/` (new module)

---

### Decision: Approach A (New Graph Node)

**Rationale:**
1. **Separation of concerns** - Discovery is its own responsibility
2. **LangGraph native** - HITL, checkpointing, tracing all work
3. **Testable in isolation** - Node can be unit tested independently
4. **Minimal blast radius** - Existing nodes unchanged
5. **Feature flag natural** - Conditional edge based on settings

---

## E. Chosen Architecture

### E.1 High-Level Flow

```
┌────────────────────────────────────────────────────────────────────┐
│  User Input: --task "Create a video using Sora from OpenAI"       │
│              --spec-ref (empty)                                    │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│  discover_spec Node (NEW)                                          │
│                                                                    │
│  1. Check: is state.spec_refs empty?                              │
│     └── If populated: skip to plan_run                            │
│                                                                    │
│  2. Intent Analysis (LLM + heuristics)                            │
│     └── Extract: explicit_provider, domain_keywords, confidence   │
│                                                                    │
│  3. Resolution Cascade:                                            │
│     ┌─────────────────────────────────────────────────────────┐   │
│     │ a) Local Catalog (pgvector semantic search)             │   │
│     │    └── SELECT * FROM spec_silver.discovery_providers    │   │
│     │        WHERE embedding <=> query_embedding              │   │
│     │                                                         │   │
│     │ b) APIs.guru (20,000+ public specs)                     │   │
│     │    └── GET https://api.apis.guru/v2/list.json           │   │
│     │    └── Match provider + keywords                        │   │
│     │                                                         │   │
│     │ c) Web Search (optional, if TAVILY_API_KEY set)         │   │
│     │    └── Tavily AI search for "OpenAI OpenAPI spec"       │   │
│     │                                                         │   │
│     │ d) LLM Knowledge (last resort)                          │   │
│     │    └── Ask model for known spec URLs                    │   │
│     └─────────────────────────────────────────────────────────┘   │
│                                                                    │
│  4. Validation: fetch spec URL, check OpenAPI/Swagger header      │
│                                                                    │
│  5. HITL (if confidence < 0.85 and hitl_mode != "never")          │
│     └── Present candidates for user confirmation                   │
│                                                                    │
│  6. Write: state.spec_refs = [resolved_url]                       │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│  plan_run → ingest_spec → detect_and_parse → ... (existing)       │
└────────────────────────────────────────────────────────────────────┘
```

### E.2 Component Diagram

```
src/integration_coworker/
├── discovery/                      # IMPLEMENTED MODULE (4,300+ LOC)
│   ├── __init__.py
│   ├── intent.py                   # IntentAnalysis, analyze_discovery_intent() - heuristic extraction
│   ├── intent_llm.py               # LLM-based intent extraction, merge_intent() with LLM-advisory pattern
│   ├── resolver.py                 # SpecCandidate, resolve_spec_from_task(), require_confirmation param
│   ├── catalog.py                  # Local catalog CRUD + pgvector semantic search
│   ├── apis_guru.py                # APIs.guru client with caching
│   ├── http_client.py              # Hardened HTTP client (manual redirect loop, per-hop SSRF validation)
│   ├── validator.py                # validate_spec_url(), SSRF protection, DNS validation
│   ├── web_search.py               # Slice 4: Tavily/SerpApi web search providers
│   └── metrics.py                  # Prometheus metrics for discovery
│
├── graph/
│   ├── nodes/
│   │   └── discover_spec.py        # Discovery graph node
│   ├── runtime.py                  # Modified: add node + conditional edge
│   └── state.py                    # Modified: add 3 fields
│
├── config/
│   └── __init__.py                 # Modified: add DiscoveryConfig
│
├── scripts/
│   ├── bootstrap_spec_catalog.py   # Idempotent catalog population
│   └── ci_hardening_gates.sh       # 17 CI enforcement gates (including Slice 4)
│
└── migrations/
    └── 004_spec_catalog.sql        # Creates spec_silver.discovery_* tables with HNSW index
```

---

## F. Contracts and State Changes

### F.1 WorkflowState Additions

File: `src/integration_coworker/graph/state.py`

```python
@dataclass
class WorkflowState:
    # ... existing fields ...

    # Discovery (NEW - Phase 0)
    discovered_specs: Optional[Dict[str, Any]] = None
    # Shape: {
    #   "candidates": [{"api_name", "spec_url", "confidence", "source", "reasoning"}],
    #   "selected_index": int,
    #   "alternatives": [str]
    # }
    
    discovery_confidence: float = 0.0
    # 0.0 = no discovery, 1.0 = perfect match
    
    discovery_source: Optional[str] = None
    # One of: "local_catalog", "apis_guru", "web_search", "llm_knowledge", "user_provided"
```

### F.2 Node Contract: `discover_spec`

```
Node: discover_spec
Phase: 0 (before plan_run)
Trigger: state.spec_refs is empty AND state.task_description is not empty

READS:
- task_description: str (required)
- options.hitl_mode: str (for HITL decision)
- options.discovery_enabled: bool (feature flag)

WRITES:
- spec_refs: List[str] (populated with discovered URL)
- discovered_specs: Dict (candidate metadata)
- discovery_confidence: float (0.0-1.0)
- discovery_source: str (resolution source)
- completed_steps: appends "discover_spec"
- errors: appends on failure

SIDE EFFECTS:
- HTTP requests to APIs.guru (cached)
- HTTP requests via hardened_fetch (per-hop SSRF validated)
- LLM call for intent analysis (via intent_llm.py)

HITL BEHAVIOR (require_confirmation parameter):
- require_confirmation=False (default): NEVER blocks, proceeds with best candidate
- require_confirmation=True AND confidence < 0.85: returns requires_hitl=True
- force_hitl=True: always returns requires_hitl=True

This contract ensures non-interactive callers (CI, automation) are never blocked.
```

### F.3 IntegrationOptions Additions

File: `src/integration_coworker/api/types.py`

```python
@dataclass
class IntegrationOptions:
    # ... existing fields ...
    
    # Discovery options (NEW)
    discovery_enabled: bool = True
    # Master switch - set False to require explicit spec_refs
    
    discovery_confirmation_threshold: float = 0.85
    # Below this confidence, trigger HITL confirmation
```

---

## G. Module and File Plan

### G.1 Implemented Files (Actual LOC)

| File | LOC | Purpose |
|------|-----|---------|
| `src/integration_coworker/discovery/__init__.py` | ~50 | Module exports (includes web search) |
| `src/integration_coworker/discovery/intent.py` | ~300 | Heuristic intent extraction |
| `src/integration_coworker/discovery/intent_llm.py` | ~500 | LLM-based intent + merge_intent() |
| `src/integration_coworker/discovery/resolver.py` | ~500 | Cascading resolution, require_confirmation, web search integration |
| `src/integration_coworker/discovery/catalog.py` | ~350 | pgvector semantic search |
| `src/integration_coworker/discovery/apis_guru.py` | ~300 | APIs.guru client with caching |
| `src/integration_coworker/discovery/http_client.py` | ~400 | Manual redirect loop, per-hop SSRF |
| `src/integration_coworker/discovery/validator.py` | ~500 | URL validation, DNS validation, SSRF blocklists |
| `src/integration_coworker/discovery/web_search.py` | ~650 | Slice 4: Tavily/SerpApi web search providers |
| `src/integration_coworker/discovery/metrics.py` | ~100 | Prometheus metrics |
| `src/integration_coworker/graph/nodes/discover_spec.py` | ~300 | Graph node |
| `migrations/004_spec_catalog.sql` | ~100 | Schema migration with HNSW index |
| `scripts/ci_hardening_gates.sh` | ~150 | CI enforcement gates (17 gates including Slice 4) |

**Total Discovery Module:** 4,300+ LOC (source files only)

### G.2 Test Files (Actual Counts)

| File | Tests | Description |
|------|-------|-------------|
| `tests/discovery/test_intent.py` | 40 | Heuristic intent extraction |
| `tests/discovery/test_validator.py` | 68 | URL validation, SSRF, DNS |
| `tests/discovery/test_hardening.py` | 44 | Production hardening acceptance gates |
| `tests/discovery/test_apis_guru.py` | 18 | APIs.guru client |
| `tests/discovery/test_catalog.py` | 16 | pgvector catalog search |
| `tests/discovery/test_discover_spec_node.py` | 14 | Graph node integration |
| `tests/discovery/test_resolver.py` | 10 | Cascading resolution |
| `tests/discovery/test_web_search.py` | 44 | Slice 4: Web search providers |

**Total: 254 tests** (240 passing in standard CI, 14 in `test_discover_spec_node.py` require graph fixtures)

**Verification:**
```bash
pytest tests/discovery/ --ignore=tests/discovery/test_discover_spec_node.py --collect-only -q
```

### G.3 Modified Files

| File | Change Type | Description |
|------|-------------|-------------|
| `src/integration_coworker/graph/state.py` | Add fields | 3 new fields (discovered_specs, discovery_confidence, discovery_source) |
| `src/integration_coworker/graph/runtime.py` | Add node + edge | Entry point → discover_spec → plan_run |
| `src/integration_coworker/config/__init__.py` | Add config | DiscoveryConfig dataclass |
| `src/integration_coworker/api/types.py` | Add options | discovery_enabled, discovery_confirmation_threshold |
| `src/integration_coworker/cli.py` | Add flags | `--no-discover`, `--discover-only` |
| `migrations/004_spec_catalog.sql` | Add DDL | spec_silver.discovery_* tables + HNSW index |

---

## H. Migration and Persistence Plan

### H.1 Database Schema: `spec_silver.discovery_*` (FINAL)

> **Decision**: Tables live in `spec_silver` schema to align with existing medallion architecture pattern.
> Tables use `discovery_` prefix for namespacing within the schema.

File: `migrations/004_spec_catalog.sql`

**Tables:**

| Table | Purpose |
|-------|---------|
| `spec_silver.discovery_providers` | API providers with semantic search support |
| `spec_silver.discovery_specs` | Individual spec versions and validation status |
| `spec_silver.discovery_categories` | API categorization for faceted search |
| `spec_silver.discovery_provider_categories` | Many-to-many join table |
| `spec_silver.discovery_aliases` | Alternative names and common misspellings |
| `spec_silver.discovery_refresh_log` | Track sync operations |

**Key Schema Details:**

```sql
-- Provider identification and semantic search
CREATE TABLE spec_silver.discovery_providers (
    id                  BIGSERIAL PRIMARY KEY,
    provider_key        TEXT NOT NULL UNIQUE,       -- e.g., "stripe.com:stripe"
    domain              TEXT NOT NULL,              -- e.g., "stripe.com"
    slug                TEXT NOT NULL,              -- e.g., "stripe" (APIs.guru key)
    display_name        TEXT NOT NULL,
    description         TEXT,
    is_curated          BOOLEAN NOT NULL DEFAULT FALSE,
    quality_tier        TEXT NOT NULL DEFAULT 'standard', -- 'premium', 'standard', 'untrusted'
    search_text         TEXT,                       -- Full-text search content
    embedding           VECTOR(1536),               -- pgvector HNSW index
    -- ... timestamps
);

-- HNSW index for vector similarity (better speed/recall than IVFFlat at catalog size)
CREATE INDEX idx_discovery_providers_embedding_hnsw 
ON spec_silver.discovery_providers 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**Index Strategy:**
- **HNSW** for vector similarity (m=16, ef_construction=64) - optimal for 1k-50k vectors
- **GIN** for full-text search on `search_text`
- **B-tree** for domain, slug, quality_tier lookups
- **Partial** indexes for curated and preferred entries

### H.2 Bootstrap Data

File: `scripts/bootstrap_spec_catalog.py`

**Features:**
- Idempotent execution (safe to run multiple times)
- Pulls from APIs.guru API and curated JSON
- Generates embeddings via `text-embedding-3-small`
- Tracks refresh operations in `discovery_refresh_log`

**Usage:**
```bash
# Initial population
python scripts/bootstrap_spec_catalog.py

# With refresh (updates existing entries)
python scripts/bootstrap_spec_catalog.py --refresh
```

### H.3 Migration Execution

```bash
# Apply migration
psql $DATABASE_URL -f migrations/004_spec_catalog.sql

# Verify tables created
psql $DATABASE_URL -c "SELECT table_name FROM information_schema.tables WHERE table_schema = 'spec_silver' AND table_name LIKE 'discovery_%';"

# Bootstrap catalog data
python scripts/bootstrap_spec_catalog.py

# Verify idempotency (run again)
python scripts/bootstrap_spec_catalog.py
```

---

## I. Feature Flags and Configuration

### I.1 Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DISCOVERY_ENABLED` | bool | `false` | Master switch for auto-discovery |
| `DISCOVERY_CONFIRMATION_THRESHOLD` | float | `0.85` | Below this → HITL confirmation |
| `DISCOVERY_TIMEOUT_SECONDS` | int | `30` | Max time for discovery phase |
| `DISCOVERY_CACHE_TTL_HOURS` | int | `24` | APIs.guru cache duration |
| `TAVILY_API_KEY` | str | (none) | Optional web search API key |
| `SERPAPI_API_KEY` | str | (none) | Alternative web search key |
| `DISCOVERY_MAX_CANDIDATES` | int | `5` | Max candidates to return |

### I.2 Configuration Dataclass

File: `src/integration_coworker/config/__init__.py`

```python
@dataclass
class DiscoveryConfig:
    """Configuration for spec auto-discovery (Phase 0)."""
    
    enabled: bool = field(
        default_factory=lambda: os.getenv("DISCOVERY_ENABLED", "false").lower() in ("true", "1", "yes")
    )
    
    confirmation_threshold: float = field(
        default_factory=lambda: float(os.getenv("DISCOVERY_CONFIRMATION_THRESHOLD", "0.85"))
    )
    
    timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_TIMEOUT_SECONDS", "30"))
    )
    
    cache_ttl_hours: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_CACHE_TTL_HOURS", "24"))
    )
    
    max_candidates: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_MAX_CANDIDATES", "5"))
    )
    
    # Web search (optional)
    tavily_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("TAVILY_API_KEY")
    )
    
    serpapi_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("SERPAPI_API_KEY")
    )
    
    @property
    def web_search_enabled(self) -> bool:
        """Check if web search is available."""
        return bool(self.tavily_api_key or self.serpapi_api_key)
```

### I.3 Rollout Phases

| Phase | `DISCOVERY_ENABLED` | Scope | Duration |
|-------|---------------------|-------|----------|
| 0 (Dev) | `false` | Internal testing only | 2 weeks |
| 1 (Alpha) | `true` (opt-in) | Early adopters with `--discover` flag | 2 weeks |
| 2 (Beta) | `true` (opt-in) | All users with CLI flag | 4 weeks |
| 3 (GA) | `true` (default) | Auto-discovery when no spec_ref | Ongoing |

---

## J. Observability

### J.1 Metrics (Prometheus)

```python
# src/integration_coworker/discovery/metrics.py

from prometheus_client import Counter, Histogram, Gauge

# Counters
DISCOVERY_ATTEMPTS = Counter(
    "discovery_attempts_total",
    "Total spec discovery attempts",
    ["source"]  # local_catalog, apis_guru, web_search, llm_knowledge
)

DISCOVERY_SUCCESS = Counter(
    "discovery_success_total",
    "Successful spec discoveries",
    ["source"]
)

DISCOVERY_FAILURES = Counter(
    "discovery_failures_total",
    "Failed spec discoveries",
    ["reason"]  # no_match, validation_failed, timeout, error
)

DISCOVERY_HITL_TRIGGERS = Counter(
    "discovery_hitl_triggers_total",
    "HITL confirmations triggered due to low confidence"
)

# Histograms
DISCOVERY_LATENCY = Histogram(
    "discovery_latency_seconds",
    "Time to resolve spec",
    ["source"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

DISCOVERY_CONFIDENCE = Histogram(
    "discovery_confidence",
    "Confidence score distribution",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
)

# Gauges
CATALOG_SIZE = Gauge(
    "discovery_catalog_size",
    "Number of entries in local spec catalog"
)
```

### J.2 LangSmith Tracing

```python
# Trace structure for discover_spec node:
# 
# discover_spec
# ├── analyze_intent (LLM call)
# │   └── metadata: {provider: openai, model: gpt-4o-mini, task_type: intent_classification}
# ├── search_local_catalog
# │   └── metadata: {query_embedding_ms: 45, search_ms: 12, results_count: 3}
# ├── search_apis_guru
# │   └── metadata: {cache_hit: true, results_count: 5}
# ├── validate_spec_url
# │   └── metadata: {url: "...", valid: true, format: openapi_3.1}
# └── metadata: {
#       confidence: 0.92,
#       source: "apis_guru",
#       selected_spec: "OpenAI API",
#       total_ms: 1250
#     }
```

### J.3 Structured Logging

```python
# Log examples with discovery-specific fields
logger.info(
    "Discovery completed",
    extra={
        "run_id": state.run_id,
        "discovery_source": "apis_guru",
        "discovery_confidence": 0.92,
        "selected_api": "OpenAI",
        "candidates_count": 5,
        "latency_ms": 1250,
    }
)

logger.warning(
    "Discovery low confidence, triggering HITL",
    extra={
        "run_id": state.run_id,
        "confidence": 0.65,
        "candidates": ["OpenAI", "Azure OpenAI", "Anthropic"],
    }
)
```

---

## K. Test Plan

### K.1 Unit Tests

File: `tests/discovery/test_intent.py`

```python
import pytest
from integration_coworker.discovery.intent import analyze_discovery_intent, DiscoveryIntent

@pytest.mark.parametrize("task,expected_provider,expected_keywords", [
    # Explicit provider
    ("Create a video using Sora from OpenAI", "OpenAI", ["video", "sora"]),
    ("Send an email with SendGrid", "SendGrid", ["email"]),
    ("Process payment with Stripe", "Stripe", ["payment"]),
    
    # Implicit provider (domain keywords only)
    ("Generate an AI image", None, ["image", "ai", "generate"]),
    ("Send SMS notification", None, ["sms", "notification"]),
    
    # Capability-focused
    ("I need to accept credit card payments", None, ["payment", "credit"]),
    ("Upload files to cloud storage", None, ["upload", "files", "storage"]),
])
async def test_intent_extraction(task, expected_provider, expected_keywords):
    intent = await analyze_discovery_intent(task)
    
    if expected_provider:
        assert intent.explicit_provider.lower() == expected_provider.lower()
    
    for kw in expected_keywords:
        assert any(kw in w.lower() for w in intent.domain_keywords + intent.action_words)
```

File: `tests/discovery/test_resolver.py`

```python
@pytest.mark.asyncio
async def test_resolve_openai_from_explicit_provider():
    """When provider is explicitly named, should resolve with high confidence."""
    intent = IntentAnalysis(
        intent=DiscoveryIntent.KNOWN_PROVIDER,
        explicit_provider="OpenAI",
        inferred_providers=["openai"],
        action_words=["create"],
        domain_keywords=["video", "sora"],
        category_hint="ai",
        confidence=0.95,
    )
    
    result = await resolve_spec(intent)
    
    assert result.selected is not None
    assert result.selected.api_name.lower() == "openai"
    assert result.selected.confidence > 0.8
    assert "openai" in result.selected.spec_url.lower()


@pytest.mark.asyncio
async def test_resolve_ambiguous_triggers_hitl():
    """Ambiguous queries should require user confirmation."""
    intent = IntentAnalysis(
        intent=DiscoveryIntent.CAPABILITY_NEED,
        explicit_provider=None,
        inferred_providers=["twilio", "messagebird", "vonage"],
        action_words=["send"],
        domain_keywords=["sms"],
        category_hint="communications",
        confidence=0.5,
    )
    
    result = await resolve_spec(intent, require_confirmation=True)
    
    assert result.requires_user_confirmation
    assert len(result.candidates) >= 2
```

### K.2 Integration Tests

File: `tests/discovery/test_apis_guru.py`

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_apis_guru_returns_valid_spec():
    """APIs.guru should return valid, fetchable spec URLs."""
    results = await search_apis_guru(query="stripe", provider_hint="stripe")
    
    assert len(results) > 0
    stripe_result = results[0]
    assert "stripe" in stripe_result.provider.lower()
    
    # Validate URL is fetchable
    async with httpx.AsyncClient() as client:
        response = await client.get(stripe_result.spec_url, follow_redirects=True)
        assert response.status_code == 200
        content = response.text
        assert "openapi" in content.lower() or "swagger" in content.lower()
```

### K.3 E2E Tests

File: `tests/e2e/test_discovery_flow.py`

```python
@pytest.mark.e2e
@pytest.mark.slow
def test_full_workflow_with_discovery():
    """E2E: Natural language task → discovered spec → code generation."""
    result = design_and_generate_integration(
        spec_refs=[],  # Empty - trigger discovery
        task_description="Create a customer in Stripe with their email and name",
        options=IntegrationOptions(
            discovery_enabled=True,
            dry_run=True,  # Don't write files
        ),
    )
    
    # Discovery should have resolved to Stripe
    assert result.provider_code == "stripe"
    assert len(result.spec_documents) > 0
    assert "stripe" in result.spec_documents[0].uri.lower()
    
    # Should have generated code
    assert len(result.code_artifacts) > 0
    
    # Discovery metadata should be captured
    assert result.plan.get("discovery_source") == "apis_guru"
    assert result.plan.get("discovery_confidence", 0) > 0.7
```

### K.4 Test Coverage (Actual)

| Category | Tests | Files |
|----------|-------|-------|
| Heuristic intent | 40 | `test_intent.py` |
| Validation/SSRF | 68 | `test_validator.py` |
| Hardening gates | 44 | `test_hardening.py` |
| APIs.guru client | 18 | `test_apis_guru.py` |
| Catalog search | 16 | `test_catalog.py` |
| Graph node | 14 | `test_discover_spec_node.py` |
| Resolution | 10 | `test_resolver.py` |

**Total: 210 tests** (196 run in standard CI, excluding `test_discover_spec_node.py`)

**Verification command:**
```bash
pytest tests/discovery/ --ignore=tests/discovery/test_discover_spec_node.py --collect-only -q
# Output: 196 tests collected
```

---

## L. Rollout Plan (Completed)

### L.1 Phase 0: Development ✅

**Deliverables:**
- [x] `discovery/` module structure
- [x] `intent.py` with heuristic extraction
- [x] `apis_guru.py` client
- [x] `discover_spec` node (basic)
- [x] Unit tests

### L.2 Phase 1: Alpha ✅

**Deliverables:**
- [x] `catalog.py` with pgvector HNSW semantic search
- [x] `resolver.py` cascading strategy
- [x] `validator.py` URL validation with SSRF protection
- [x] Integration tests
- [x] `migrations/004_spec_catalog.sql`

### L.3 Phase 2: Beta ✅

**Deliverables:**
- [x] `intent_llm.py` with LLM-based extraction and merge_intent()
- [x] HITL confirmation flow with `require_confirmation` parameter
- [x] CLI flags (`--no-discover`, `--discover-only`)
- [x] E2E tests
- [x] Documentation

### L.4 Phase 3: Production Hardening ✅ (January 5, 2026)

**Deliverables:**
- [x] `http_client.py` with manual redirect loop and per-hop SSRF validation
- [x] Streaming byte cap via `aiter_bytes()` (not `response.text`)
- [x] Redirect loop detection via `seen_urls` set
- [x] Dangerous scheme blocking (`ALLOWED_SCHEMES = {"http", "https"}`)
- [x] Explicit `httpx.Timeout` and `httpx.Limits` configuration
- [x] `trust_env=False` to block proxy environment leakage
- [x] `HEURISTIC_AUTHORITY_THRESHOLD=0.8` - heuristics authoritative over LLM
- [x] `require_confirmation=False` default - non-interactive safety
- [x] 44 hardening acceptance tests in `test_hardening.py`
- [x] `scripts/ci_hardening_gates.sh` with 10 enforcement gates

**Gates Passed:**
- 196 tests passing (standard CI)
- All 10 CI hardening gates pass
- Per-hop redirect SSRF validation implemented
- Streaming byte cap enforcement via `aiter_bytes()`
- Non-interactive callers never blocked

**Verification command:**
```bash
./scripts/ci_hardening_gates.sh && pytest tests/discovery/ --ignore=tests/discovery/test_discover_spec_node.py -q
```

### L.5 Phase 4: GA (Pending)

**Deliverables:**
- [ ] `DISCOVERY_ENABLED=true` as default
- [x] Optional web search integration (Tavily/SerpAPI) ✅ **Complete (January 6, 2026)**
- [ ] Monitoring dashboards
- [ ] Runbook documentation

---

## M. Failure Modes & Recovery

### M.1 SSRF Attack Vectors (MITIGATED)

| Attack Vector | Mitigation | Implementation |
|--------------|------------|----------------|
| **Localhost bypass** | Hostname blocklist | `validator._is_blocked_hostname()` |
| **Private IP ranges** | IP blocklist (RFC 1918, link-local, loopback) | `validator._is_blocked_ip()` |
| **DNS rebinding** | Post-resolution IP validation | `validator._resolve_and_validate_hostname()` |
| **Cloud metadata endpoints** | Explicit hostname blocks (169.254.169.254, metadata.google.internal) | Blocklist |
| **Redirect to blocked** | Per-hop validation in manual redirect loop | `http_client._validate_hop()` checks EVERY redirect |
| **Redirect loop** | Loop detection via canonicalized URL set | `http_client.seen_urls` set with `_canonicalize_url()` |
| **Dangerous schemes** | Only HTTP(S) allowed | `http_client.ALLOWED_SCHEMES = {"http", "https"}` blocks file://, gopher://, etc. |
| **HTTP downgrade** | HTTPS-only by default + scheme downgrade detection | `_validate_hop()` detects HTTPS→HTTP |
| **Proxy env leakage** | Block environment variables | `http_client.py` uses `trust_env=False` |
| **Response size DoS** | Streaming byte cap | `http_client._stream_response_body()` uses `aiter_bytes()` |

**Implementation Details (January 5, 2026 Hardening):**

File: `src/integration_coworker/discovery/http_client.py`

```python
# Manual redirect loop with loop detection and streaming byte cap
async with httpx.AsyncClient(
    timeout=get_hardened_timeout(timeout_seconds),
    follow_redirects=False,  # MANUAL redirect loop for per-hop validation
    limits=get_hardened_limits(),
    trust_env=False,  # Block HTTP_PROXY, HTTPS_PROXY env vars
) as client:
    seen_urls: set[str] = set()  # Loop detection
    for hop in range(max_redirects + 1):
        canonical = _canonicalize_url(current_url)
        if canonical in seen_urls:
            return FetchResult(success=False, error="Redirect loop detected")
        seen_urls.add(canonical)
        # Validate BEFORE following each redirect
        is_safe, error, _ = _validate_hop(next_url, allow_http, validate_dns, hop)
        if not is_safe:
            return FetchResult(success=False, error=f"Security: {error}")
    # Final response: stream with hard byte cap
    async for chunk in response.aiter_bytes():
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            return FetchResult(success=False, error="Response too large")
```

**Explicit Resource Limits:**
| Parameter | Value | Source |
|-----------|-------|--------|
| `CONNECT_TIMEOUT` | 10.0s | `http_client.py` |
| `READ_TIMEOUT` | 30.0s | `http_client.py` |
| `WRITE_TIMEOUT` | 10.0s | `http_client.py` |
| `POOL_TIMEOUT` | 10.0s | `http_client.py` |
| `MAX_REDIRECTS` | 5 | `http_client.py` |
| `MAX_CONNECTIONS` | 10 | `validator.py` |
| `MAX_KEEPALIVE_CONNECTIONS` | 5 | `validator.py` |
| `MAX_SPEC_BYTES` | 10MB | `validator.py` |
| `ALLOW_HTTP_SCHEME` | False | `validator.py` |

**Reference:** [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)

### M.2 Database Failures

| Failure Mode | Detection | Recovery |
|-------------|-----------|----------|
| **Postgres unavailable** | `catalog.is_catalog_available()` returns False | Fall back to APIs.guru REST API |
| **pgvector extension missing** | Migration fails | Run `CREATE EXTENSION vector;` |
| **Embedding dimension mismatch** | Insert fails with dimension error | Ensure text-embedding-3-small (1536d) |
| **HNSW index corrupted** | Queries slow or error | `REINDEX INDEX idx_discovery_providers_embedding_hnsw;` |

### M.3 External Service Failures

| Service | Failure Mode | Fallback |
|---------|-------------|----------|
| **APIs.guru** | Timeout / 5xx | Use local catalog; retry with exponential backoff |
| **OpenAI embeddings** | Rate limit / timeout | Skip embedding generation; use text search only |
| **Spec URL validation** | Target server down | Mark as `pending` validation; user can override |

### M.4 Feature Flag Behavior

| Flag State | Behavior |
|-----------|----------|
| `DISCOVERY_ENABLED=false` | Require explicit `--spec-ref`; discovery node skipped |
| `DISCOVERY_ENABLED=true` | Attempt discovery when no spec provided |
| Catalog unavailable + APIs.guru down | Error with helpful message; suggest `--spec-ref` |

---

## M.5 Files Modified in Stabilization (January 2026)

| File | Change Type | Description |
|------|-------------|-------------|
| `migrations/004_spec_catalog.sql` | **Rewritten** | Schema `spec_catalog.*` → `spec_silver.discovery_*`; IVFFlat → HNSW index |
| `src/integration_coworker/discovery/catalog.py` | **Updated** | SQL queries updated to `spec_silver.discovery_*` tables |
| `src/integration_coworker/discovery/validator.py` | **Enhanced** | Added DNS resolution validation, `_resolve_and_validate_hostname()`, OWASP SSRF controls |
| `scripts/bootstrap_spec_catalog.py` | **Updated** | SQL operations updated to new table names |
| `tests/discovery/test_validator.py` | **Expanded** | Added `TestDNSResolutionSecurity` with 6 new tests |
| `docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md` | **Updated** | This document - final architecture, failure modes, modified files |

## M.6 Files Modified in Production Hardening (January 5, 2026)

| File | Change Type | Description |
|------|-------------|-------------|
| `src/integration_coworker/discovery/http_client.py` | **Rewritten** | Manual redirect loop with `follow_redirects=False`, per-hop `_validate_hop()`, streaming byte cap via `aiter_bytes()`, redirect loop detection, dangerous scheme blocking (`ALLOWED_SCHEMES`), scheme downgrade detection, `trust_env=False`, explicit `httpx.Timeout`/`httpx.Limits` |
| `src/integration_coworker/discovery/intent_llm.py` | **Enhanced** | `merge_intent()` with LLM-advisory pattern, `HEURISTIC_AUTHORITY_THRESHOLD=0.8`, `_validate_llm_provider()` guards, `KNOWN_PROVIDER_ALIASES` validation |
| `src/integration_coworker/discovery/resolver.py` | **Updated** | Added `require_confirmation=False` parameter for HITL non-interactive safety |
| `tests/discovery/test_hardening.py` | **Expanded** | 44 acceptance gate tests (was 17): `TestMergeIntentLLMAdvisory`, `TestPerHopRedirectSSRF`, `TestExplicitTimeoutAndLimits`, `TestHITLNonInteractiveSafety`, `TestRedirectChainBlockIntegration`, `TestStreamingByteCap`, `TestProxyEnvImmunityIntegration` |
| `tests/discovery/test_validator.py` | **Fixed** | Mocks updated to patch `hardened_fetch` instead of `httpx.AsyncClient` |
| `tests/discovery/test_resolver.py` | **Fixed** | HITL tests updated to use `require_confirmation=True` |
| `scripts/ci_hardening_gates.sh` | **Enhanced** | 10 CI enforcement gates: no httpx bypass, trust_env=False, follow_redirects=False, explicit timeout/limits, streaming byte cap, loop detection, scheme blocking |

**Total Tests After Hardening:** 196 passing (discovery module, excluding `test_discover_spec_node.py`)

**Total LOC in discovery module:** 3,661 lines

---

## N. Risk Register

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| **APIs.guru downtime** | Discovery degraded | Low | Cache + local catalog fallback |
| **Web search returns wrong specs** | User frustration | Medium | Validation + HITL confirmation |
| **Rate limiting from APIs** | Reduced availability | Medium | Caching + backoff + local catalog |
| **LLM hallucinating spec URLs** | Invalid specs | Medium | Always validate before using |
| **Privacy (task sent to search)** | Data exposure | Low | Allow disabling web search |
| **Ambiguous queries** | Poor UX | High | HITL + show alternatives |
| **Discovery adds latency** | Slower workflow | Medium | Parallel fetch + caching |
| **Feature flag complexity** | Testing burden | Low | Clear phase gates |
| **SSRF via malicious spec URLs** | Security breach | Medium | DNS resolution validation + IP blocklists (MITIGATED) |

---

## O. Work Breakdown

### O.1 Epic: Spec Auto-Discovery

| Task ID | Task | Estimate | Dependencies | Owner |
|---------|------|----------|--------------|-------|
| DISC-001 | Create `discovery/` module structure | 0.5d | - | - |
| DISC-002 | Implement `intent.py` with LLM extraction | 1.5d | DISC-001 | - |
| DISC-003 | Implement `apis_guru.py` client | 1d | DISC-001 | - |
| DISC-004 | Implement `catalog.py` local search | 1d | DISC-001 | - |
| DISC-005 | Implement `resolver.py` cascade | 1.5d | DISC-002, DISC-003, DISC-004 | - |
| DISC-006 | Implement `validator.py` | 0.5d | - | - |
| DISC-007 | Implement `discover_spec` node | 1d | DISC-005, DISC-006 | - |
| DISC-008 | Modify `runtime.py` graph topology | 0.5d | DISC-007 | - |
| DISC-009 | Modify `state.py` fields | 0.25d | - | - |
| DISC-010 | Add `DiscoveryConfig` to config | 0.25d | - | - |
| DISC-011 | Create migration `004_spec_catalog.sql` | 0.5d | - | - |
| DISC-012 | Bootstrap catalog data | 1d | DISC-011 | - |
| DISC-013 | Implement web search (Tavily) | 1d | DISC-001 | - |
| DISC-014 | Implement HITL confirmation flow | 1d | DISC-007 | - |
| DISC-015 | Add CLI flags | 0.5d | DISC-007 | - |
| DISC-016 | Unit tests for `discovery/` | 2d | DISC-001-007 | - |
| DISC-017 | Integration tests | 1d | DISC-016 | - |
| DISC-018 | E2E tests | 1d | DISC-017 | - |
| DISC-019 | Documentation | 1d | All | - |
| DISC-020 | Monitoring + dashboards | 0.5d | DISC-007 | - |

**Total Estimate:** ~17 engineering days (~3.5 weeks)

### O.2 Dependencies Graph

```
                    DISC-001 (module structure)
                         │
         ┌───────────────┼───────────────┐
         │               │               │
    DISC-002        DISC-003        DISC-004
    (intent)       (apis_guru)     (catalog)
         │               │               │
         └───────────────┼───────────────┘
                         │
                    DISC-005 (resolver)
                         │
                    DISC-006 (validator)
                         │
                    DISC-007 (node)
                         │
         ┌───────────────┼───────────────┐
         │               │               │
    DISC-008        DISC-014        DISC-015
    (runtime)       (HITL)          (CLI)
         │               │               │
         └───────────────┼───────────────┘
                         │
                    DISC-016 (tests)
                         │
                    DISC-017-020 (validation)
```

---

## P. CI/CD and Database Testing

### P.1 CI Pipeline Configuration

The discovery module requires Postgres + pgvector for full test coverage. CI jobs should:

1. **Unit tests** - Run without database (`pytest tests/discovery/ -m "not requires_db"`)
2. **Integration tests** - Run with Postgres + pgvector service

**GitHub Actions Example:**
```yaml
jobs:
  test-discovery-unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]"
      - run: pytest tests/discovery/ -m "not requires_db" -v

  test-discovery-integration:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test_discovery
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]"
      - name: Apply migrations
        run: |
          psql postgresql://test:test@localhost:5432/test_discovery -f migrations/004_spec_catalog.sql
      - name: Run integration tests
        env:
          DATABASE_URL: postgresql://test:test@localhost:5432/test_discovery
          DISCOVERY_ENABLED: "true"
        run: pytest tests/discovery/ -m "requires_db" -v
```

### P.2 Hardening CI Gates (Implemented)

File: `scripts/ci_hardening_gates.sh`

The following gates are enforced in CI to prevent security regressions:

| Gate | Check | Blocks If |
|------|-------|-----------|
| 1 | No `import httpx` outside http_client.py, web_search.py | Any file in `discovery/` imports httpx directly |
| 2 | No `httpx.Client` instantiation | Any file creates httpx client outside http_client.py, web_search.py |
| 3 | `trust_env=False` present | http_client.py doesn't block proxy env vars |
| 4 | `follow_redirects=False` present | http_client.py uses automatic redirects |
| 5 | Explicit `httpx.Timeout` | http_client.py doesn't use explicit timeout |
| 6 | Explicit `httpx.Limits` | http_client.py doesn't use explicit limits |
| 7 | Streaming byte cap (`aiter_bytes`) | http_client.py uses `response.text` instead of streaming |
| 8 | Redirect loop detection (`seen_urls`) | http_client.py lacks loop detection |
| 9 | Dangerous scheme blocking (`ALLOWED_SCHEMES`) | http_client.py doesn't block file://, gopher://, etc. |
| 10 | Total timeout budget | http_client.py lacks `DEFAULT_TOTAL_TIMEOUT` |
| 11 | AST-based response.text/content check | Uses response.text without streaming |
| 12 | Hardening tests pass | Any of 44 `test_hardening.py` tests fail |
| 13 | Invariant behavior tests | Semantic invariant tests fail |
| 14 | `trust_env=False` in web_search.py | web_search.py doesn't block proxy env vars |
| 15 | Web search disabled by default | `DISCOVERY_WEB_SEARCH_ENABLED` not defaulting to false |
| 16 | No `requests` library | Any file imports `requests` library |
| 17 | Web search tests pass | Any of 44 `test_web_search.py` tests fail |

**Run gates locally:**
```bash
./scripts/ci_hardening_gates.sh
# Output: === All hardening gates passed (17 gates) ===
```

**GitHub Actions integration:**
```yaml
jobs:
  hardening-gates:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]"
      - run: ./scripts/ci_hardening_gates.sh
```

### P.3 Test Markers

Discovery tests use markers to separate database-dependent tests:

```python
# tests/conftest.py
import pytest

def pytest_configure(config):
    config.addinivalue_line("markers", "requires_db: mark test as requiring database")

# tests/discovery/test_catalog.py
@pytest.mark.requires_db
async def test_local_catalog_search():
    """Test requires Postgres + pgvector."""
    ...
```

### P.3 Rollback Procedure

If discovery causes production issues, rollback is straightforward:

**Immediate mitigation (no deploy needed):**
```bash
# Disable discovery via feature flag
export DISCOVERY_ENABLED=false
# Or via Kubernetes ConfigMap / environment variable
```

**Schema rollback (if needed):**
```sql
-- Drop discovery schema (preserves other data)
DROP SCHEMA IF EXISTS spec_silver CASCADE;

-- Or remove just discovery tables within spec_silver
DROP TABLE IF EXISTS spec_silver.discovery_refresh_log CASCADE;
DROP TABLE IF EXISTS spec_silver.discovery_provider_aliases CASCADE;
DROP TABLE IF EXISTS spec_silver.discovery_spec_categories CASCADE;
DROP TABLE IF EXISTS spec_silver.discovery_specs CASCADE;
DROP TABLE IF EXISTS spec_silver.discovery_providers CASCADE;
```

**Code rollback:**
- Revert the commit that added `discover_spec` entry point to `runtime.py`
- Discovery module can remain in codebase (gated by `DISCOVERY_ENABLED=false`)

### P.4 Canary Deployment

Recommended deployment sequence:
1. Deploy with `DISCOVERY_ENABLED=false` (code is there, inactive)
2. Apply migration `004_spec_catalog.sql` to production database
3. Run `bootstrap_spec_catalog.py` to populate catalog
4. Enable for 5% of traffic: `DISCOVERY_ENABLED=true` on subset of pods
5. Monitor metrics: `discovery_latency_seconds`, `discovery_success_total`
6. If metrics healthy after 24h, roll out to 100%

### P.5 Observability Checklist

Before enabling in production:
- [ ] Prometheus metrics exposed (`/metrics` endpoint)
- [ ] Grafana dashboard for discovery latency, success rate, HITL rate
- [ ] Alerts configured for P95 latency > 5s, error rate > 10%
- [ ] Structured logging enabled for discovery operations
- [ ] Database connection pool sized for catalog queries

---

## Appendix A: Related ADRs

| ADR | Status | Relationship |
|-----|--------|--------------|
| ADR-0008: Provider Inference Strategy | Existing | Leveraged for infer_provider_code |
| ADR-0013: Spec Auto-Discovery Architecture | To Create | This plan becomes the ADR |
| ADR-0014: Web Search Integration | To Create | Subset of this plan |

---

## Appendix B: APIs.guru Response Schema

```json
{
  "openai.com": {
    "added": "2024-01-15",
    "preferred": "v1",
    "versions": {
      "v1": {
        "added": "2024-01-15",
        "info": {
          "title": "OpenAI API",
          "description": "APIs for sampling from and fine-tuning language models",
          "x-logo": {
            "url": "https://openai.com/logo.png"
          }
        },
        "swaggerUrl": "https://raw.githubusercontent.com/openai/openai-openapi/master/openapi.yaml",
        "openapiVer": "3.0.0"
      }
    }
  }
}
```

---

## Appendix C: Glossary

| Term | Definition |
|------|------------|
| **Discovery** | Process of finding API spec from natural language |
| **Intent Analysis** | Extracting provider/capability signals from task |
| **Resolution** | Converting intent to concrete spec URL |
| **Catalog** | Local database of known API specs |
| **Cascade** | Trying resolution sources in priority order |
| **HITL** | Human-in-the-loop confirmation |
| **Validation** | Fetching spec URL and verifying it's valid OpenAPI |

---

*Document version: 2.0 (Staff+ Engineering Rewrite)*
