# V22 Bug Fix Change Inventory

**Period**: December 22-26, 2025  
**Context**: Demo script runtime issues (25+ min runs, OOM kills)  
**Total Commits**: 17 commits with bug fixes

---

## Summary by Category

| Category | Files Changed | Key Fixes |
|----------|---------------|-----------|
| **Checkpoint Serialization** | 1 file | Channel exclusion, writes filtering |
| **Spec Embedding** | 1 file | In-loop ceiling checks |
| **Memory Monitoring** | 1 file (new) | Background sampler, ceiling enforcement |
| **LLM Timeouts** | 2 files | Per-call timeout, threading wrapper |
| **LLM Output Cleanup** | 2 files | Code fence stripping, preamble removal |
| **LLM Response Buffers** | 2 files | Explicit `del response` after content extraction |
| **State Copy Optimization** | 1 file | Custom `__deepcopy__` with shallow refs for large fields |
| **State GC Hooks** | 4 files | Clear large fields at stage boundaries |
| **Bulk Persistence** | 1 file (new) | Multi-row INSERT, COPY FROM STDIN |
| **Sandbox/Stubs** | 2 files | Dynamic stub generation, None guards |
| **Migration Logging** | 1 file | Checksum warning dedup |
| **Timing Diagnostics** | 1 file | Gap detection in SLOW_RUN_BUNDLE |

---

## 1. CHECKPOINT SERIALIZATION (Bug #101 series)

### File: `src/integration_coworker/graph/checkpointer.py`

**Commits**:
- `2b50e6a` Bug #101 v18: Filter checkpoint_writes table
- `2a98f5e` Bug #101 v20: Exclude schema_name_to_uri from plan

**Changes**:

```python
# EXCLUDE_CHANNELS - 15 large channels excluded from checkpoints
EXCLUDE_CHANNELS = {
    'spec_chunk_ids',         # operator.add causes exponential growth
    'openapi_spec',           # Raw OpenAPI dict (7MB+)
    'spec_documents', 'spec_sections', 'spec_chunk_embeddings',
    'endpoints', 'schemas', 'schema_fields', 'endpoint_parameters',
    'entities', 'relationships', 'repo_snapshot', 'repo_markdown_context',
    'doc_chunks', 'pending_specs'
}

# PLAN_EXCLUDE_KEYS - 4 keys stripped from 'plan' channel
PLAN_EXCLUDE_KEYS = {
    'openapi_specs',                     # 7MB+ each
    'chunk_index_to_spec_document_uri',  # Rebuilable from DB
    'schema_name_to_uri',                # Bug #101 v20: 880 mappings = 100KB × 22 writes
    'candidate_patterns',                # Transient, recomputable
}

# NEW: _filtered_dump_writes() function
# Filters checkpoint_writes table (not just checkpoint_blobs)
def _filtered_dump_writes(serde, thread_id, checkpoint_ns, checkpoint_id, 
                          task_id, task_path, writes) -> list:
    """Bug #101 v18: Filter intermediate writes to prevent DB bloat."""
```

**Result**: Checkpoint size reduced from 2.65 GB → 17 KB (99.999% reduction)

---

## 2. SPEC EMBEDDING CHANGES

### File: `src/integration_coworker/graph/nodes/embed_spec_chunks.py`

**Commit**: `63a764e` V22-011: production-ready ceiling enforcement

**Changes**:

```python
from integration_coworker.graph.memory_sampler import check_ceiling_in_loop

def _batch_embed(client, texts, model):
    for batch_num, batch in enumerate(batches):
        # V22-011: Check memory ceiling inside loop
        check_ceiling_in_loop(batch_num, check_interval=5)
        # ... embedding logic

def _embed_spec_chunks_legacy(state):
    for idx, chunk in enumerate(state.doc_chunks):
        # V22-011: Check memory ceiling inside loop
        check_ceiling_in_loop(idx, check_interval=50)
        # ... chunk processing

def _embed_spec_chunks_streaming(state):
    for chunk_id, chunk_index, content in iter_chunks_for_embedding(spec_doc_id):
        # V22-011: Check memory ceiling - critical for large specs
        check_ceiling_in_loop(chunk_iteration, check_interval=100)
        chunk_iteration += 1
```

**Result**: Early abort if memory ceiling exceeded during embedding loops

---

## 3. MEMORY SAMPLER (NEW FILE)

### File: `src/integration_coworker/graph/memory_sampler.py`

**Commits**:
- `946339e` V22-011: Memory sampler initial implementation
- `b9ad7ef` V22-011: Thread-safe ceiling + rolling window
- `63a764e` V22-011: Production-ready ceiling enforcement

**Key Components**:

```python
# Configuration via environment variables
IC_MEM_SAMPLER_ENABLED = True/False
IC_MEM_SAMPLER_INTERVAL_S = 1.0 (default)
IC_MAX_RSS_MB = 4096 (4GB ceiling)

# Exception for graceful abort
class RSSCeilingExceeded(Exception):
    """Raised when RSS exceeds ceiling. NOT a SIGKILL from OOM killer."""
    
# Core sampler class
class MemorySampler:
    # Uses deque(maxlen=N) for bounded memory - cannot regress
    _samples: Deque[MemorySample] = deque(maxlen=rolling_window_size)
    
    # Streaming file writes (line-buffered, immediate flush)
    _trace_file_handle = open(trace_file, "a", buffering=1)
    
    # Thread-safe ceiling check
    def check_ceiling(self) -> Optional[RSSCeilingExceeded]:
        if rss_mb > self.config.rss_ceiling_mb:
            return RSSCeilingExceeded(rss_mb, ceiling_mb, node)

# In-loop check functions
def check_ceiling_in_loop(iteration, check_interval=10):
    """Lightweight check every N iterations."""
    
def check_ceiling_or_raise():
    """Immediate check, raises if exceeded."""
```

**Tests**: 23 tests in `tests/graph/test_memory_sampler.py`

---

## 4. LLM TIMEOUTS

### File: `src/integration_coworker/llm/client.py` (sync)

**Commit**: `c68a92a` V22: Fix LLM call timeout for both sync and async clients

**Changes**:

```python
# Environment variable for timeout (default 120s)
SYNC_LLM_CALL_TIMEOUT_SECONDS = float(os.environ.get("IC_LLM_CALL_TIMEOUT", "120"))

class _SyncCallTimeout(Exception):
    """Raised when a sync LLM call times out."""
    pass

def _call_with_timeout(fn, timeout, *args, **kwargs):
    """
    V22-011: Timeout protection using threading.
    """
    result_container = {"result": None, "exception": None, "completed": False}
    
    def target():
        try:
            result_container["result"] = fn(*args, **kwargs)
            result_container["completed"] = True
        except Exception as e:
            result_container["exception"] = e
            result_container["completed"] = True
    
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    
    if not result_container["completed"]:
        raise _SyncCallTimeout(f"LLM call timed out after {timeout}s")

# Updated with_retry() to use timeout
def with_retry(fn, circuit_key=None, timeout=None):
    """Now includes per-call timeout protection."""
    result = _call_with_timeout(fn, call_timeout, *args, **kwargs)
```

### File: `src/integration_coworker/llm/async_client.py`

**Commit**: `a4052bf` V22: Memory and timeout hardening

**Changes**:

```python
# Same environment variable
LLM_CALL_TIMEOUT_SECONDS = float(os.environ.get("IC_LLM_CALL_TIMEOUT", "120"))

async def _retry_async(fn, ..., per_call_timeout=None):
    """V22-011: Added per_call_timeout to prevent indefinite hangs."""
    timeout = per_call_timeout if per_call_timeout else LLM_CALL_TIMEOUT_SECONDS
    
    # Wrap with asyncio.wait_for for timeout protection
    result = await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
    
    # Timeout now treated as retryable (not fatal)
    except asyncio.TimeoutError as e:
        logger.warning(f"LLM call timed out after {timeout}s (attempt {attempts}/{max_attempts})")
```

---

## 5. LLM OUTPUT CLEANUP (V22-002 Fix)

### File: `src/integration_coworker/llm/utils.py`

**Commit**: `f28a8f3` V22-002: Fix syntax errors at line 8

**Changes**:

```python
def clean_llm_code_output(text: str) -> str:
    """
    V22-002 Fix: Enhanced to handle edge cases causing syntax errors at line 8.
    """
    # Additional preamble patterns
    preamble_patterns = [
        # ... existing patterns ...
        r'^(?:Below is[^:]*:\s*\n)+',
        r'^(?:Here\'s (?:my |the )?(?:updated|revised|completed)[^:]*:\s*\n)+',
    ]
    
    # Additional postamble patterns
    postamble_patterns = [
        # ... existing patterns ...
        r'\n+(?:I hope this helps[^.]*\.)\s*$',
        r'\n+(?:Please let me know[^.]*\.)\s*$',
    ]
    
    # V22-002: Line-by-line filtering for LLM commentary
    commentary_starts = (
        'here is', 'here\'s', 'this is', 'below is', 
        'the code', 'note:', 'note that', 'please',
        'i hope', 'let me', 'feel free', 'sure,', 'sure!',
    )
    # Skip lines starting with these (outside docstrings)
```

### File: `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

**Commit**: `f28a8f3` V22-002

**Changes**:

```python
from integration_coworker.llm.utils import strip_code_fences, clean_llm_code_output

# Changed from strip_code_fences to clean_llm_code_output
clean_refined = clean_llm_code_output(refined)

# V22-002: Diagnostic logging for syntax errors
if not _validate_syntax(clean_refined, lang):
    lines = clean_refined.split('\n')
    logger.error(
        f"V22-002 Syntax Error Diagnostic for {artifact_type} '{module_name}':\n"
        f"First 15 lines:\n" + 
        "\n".join(f"  L{i+1}: {line}" for i, line in enumerate(lines[:15]))
    )
```

---

## 6. STATE ESTIMATION & GC

### File: `src/integration_coworker/graph/runtime.py`

**Commits**:
- `946339e` V22-011: Memory sampler integration
- `b9ad7ef` V22-011: Ceiling checks at node boundaries
- `a4052bf` V22: gc.collect() after nodes
- `fd63555` V22: Fix _check_oom_likelihood() signature

**Changes**:

```python
import gc

# In timed_node() wrapper - after successful execution:
# V22: Force garbage collection after node completion
gc.collect()

# At node start - check ceiling before proceeding:
def _emit_node_start(state):
    from integration_coworker.graph.memory_sampler import check_ceiling
    ceiling_exc = check_ceiling()
    if ceiling_exc:
        raise ceiling_exc

# At node end - check ceiling after execution:
def _emit_node_end(state, result, duration_ms):
    ceiling_exc = check_ceiling()
    if ceiling_exc:
        raise ceiling_exc
```

---

## 7. STATE COPY OPTIMIZATION (V22-MEM)

### File: `src/integration_coworker/graph/state.py`

**Commit**: `pending` V22-MEM: Memory-efficient copy semantics for LangGraph

**Purpose**: LangGraph copies state between every node. By implementing custom 
`__copy__` and `__deepcopy__` methods, we can share references for large immutable 
fields instead of deep copying them.

**Key Components**:

```python
# Fields that use shallow copy (reference sharing) during deepcopy
SHALLOW_COPY_FIELDS = frozenset({
    # Bronze layer - large raw content (immutable after ingest)
    "openapi_spec",           # 7-50MB dict
    "doc_chunks",             # 10MB+ list of strings
    "spec_documents",         # Parsed docs
    "spec_sections",          # Parsed sections
    "pending_specs",          # Multi-spec queue
    "repo_markdown_context",  # 5MB+ string
    
    # Silver layer - structured but large
    "spec_chunk_embeddings",  # Embeddings list
    "parsed_specs",           # Typed ParsedSpec
    "endpoints", "schemas", "schema_fields",  # Can be 1000+ items
    
    # Gold layer - repo content
    "repo_snapshot",          # Full file contents
    "repo_changes",           # Diffs
    
    # Control
    "plan",                   # Contains openapi_specs, indices
})

# Custom deepcopy - share refs for large fields
def __deepcopy__(self, memo):
    for f in fields(self):
        value = getattr(self, f.name)
        if f.name in SHALLOW_COPY_FIELDS:
            setattr(new_state, f.name, value)  # Share reference
        elif isinstance(value, list):
            setattr(new_state, f.name, list(value))  # New list, same elements
        else:
            setattr(new_state, f.name, copy.deepcopy(value, memo))

# Copy-on-write helper
def deep_copy_field(self, field_name: str) -> Any:
    """Use before modifying a large field."""
    return copy.deepcopy(getattr(self, field_name))
```

**Result**: 
- Deep copy of 50MB state: O(1) for large fields (ref sharing) vs O(n) (full copy)
- Small control fields still isolated (new list/dict instances)
- Tested: `openapi_spec same ref: True`, `completed_steps same ref: False`

---

## 8. STATE GC MODULE (NEW FILE - V22-MEM)

### File: `src/integration_coworker/graph/state_gc.py`

**Commit**: `pending` V22-MEM: State garbage collection for LangGraph memory issues

**Purpose**: Address LangGraph internal state copy overhead by aggressively clearing 
large fields after they're no longer needed.

**Key Components**:

```python
# Field categorization by processing stage
BRONZE_CONTENT_FIELDS = [
    "openapi_spec",          # Raw OpenAPI dict (7-50MB for large specs)
    "doc_chunks",            # Raw text chunks (can be 10MB+)
    "spec_documents",        # Parsed spec documents
    "spec_sections",         # Parsed sections
    "pending_specs",         # Multi-spec queue
    "repo_markdown_context", # Repo docs (can be 5MB+)
]

SILVER_CONTENT_FIELDS = [
    "spec_chunk_embeddings", # Embeddings (streamed to DB, can clear)
    "parsed_specs",          # Typed ParsedSpec objects
]

GOLD_CONTENT_FIELDS = [
    "repo_snapshot",         # Full repo file contents
    "repo_changes",          # Diffs
]

# Core functions
def force_gc_with_stats(tag: str) -> Dict[str, Any]:
    """Force GC with before/after RSS tracking."""
    gc.collect(0)  # Gen 0
    gc.collect(1)  # Gen 1  
    gc.collect(2)  # Gen 2 (full)
    
def clear_bronze_content(state, force_gc=True) -> Dict:
    """Clear bronze-layer after silver is built."""
    
def clear_silver_content(state, force_gc=True) -> Dict:
    """Clear silver-layer after persisted to DB."""
    
def clear_gold_content(state, force_gc=True) -> Dict:
    """Clear gold-layer after report is built."""

# Node-specific cleanup hooks (wired into nodes)
def cleanup_after_silver(state):   # Called in build_silver_api_model
def cleanup_after_embedding(state): # Called in embed_spec_chunks
def cleanup_after_codegen(state):   # Called in generate_code_and_tests
```

**Wiring**:
- `build_silver_api_model.py`: Calls `cleanup_after_silver()` at end
- `embed_spec_chunks.py`: Calls `cleanup_after_embedding()` at end
- `generate_code_and_tests.py`: Calls `cleanup_after_codegen()` at end
- `runtime.py`: Calls `reset_gc_stats()` at run start

**Result**: Large state fields cleared at stage boundaries, reducing LangGraph copy overhead

---

## 8. BULK PERSISTENCE (NEW FILE)

### File: `src/integration_coworker/persistence/bulk.py`

**Commit**: `63a764e` V22-011: production-ready ceiling enforcement

**Design**:

```python
# Decision rule based on row count
BATCH_THRESHOLD = 500

def write_rows(conn, table, columns, rows, ...):
    """Auto-selects strategy based on row count."""
    if len(rows) <= BATCH_THRESHOLD:
        return write_rows_insert(...)  # Multi-row INSERT
    else:
        return write_rows_copy(...)    # COPY FROM STDIN

def write_rows_insert(conn, table, columns, rows, schema=None, on_conflict=None):
    """
    Multi-row INSERT with optional ON CONFLICT handling.
    10-50x faster than row-by-row inserts.
    """

def write_rows_copy(conn, table, columns, rows, schema=None):
    """
    COPY FROM STDIN (Postgres only).
    Fastest bulk-ingest path, bypasses SQL parsing.
    """
```

---

## 8. SANDBOX & STUBS

### File: `src/integration_coworker/codegen/sandbox.py`

**Commits**:
- `08c730f` fix(sandbox): add stub modules for LLM-hallucinated imports
- `fcfba64` fix(sandbox): dynamic stub generation + Response None guard

**Changes**:

```python
# Structured logging infrastructure added
class SandboxLoggerAdapter(logging.LoggerAdapter):
    """Injects run_id, sandbox_id, gate for correlation."""

# Context variables for automatic log correlation
_run_id: contextvars.ContextVar[Optional[str]]
_sandbox_id: contextvars.ContextVar[Optional[str]]
_sandbox_dir: contextvars.ContextVar[Optional[str]]
_gate_name: contextvars.ContextVar[Optional[str]]
```

### File: `src/integration_coworker/codegen/field_mappings.py`

**Commit**: `086aac9` fix(field_mappings): handle None values

**Changes**:

```python
def to_snake_case(name: str) -> str:
    if name is None:
        return ""  # ← Added None guard
    # ... existing logic

def to_camel_case(name: str) -> str:
    if name is None:
        return ""  # ← Added None guard
    # ... existing logic

def _infer_body_fields_from_path(path: str, method: str):
    if path is None:
        return []  # ← Added None guard
```

---

## 9. MIGRATION LOGGING (V22-009)

### File: `src/integration_coworker/persistence/migrations.py`

**Commit**: `63a764e` V22-011: production-ready ceiling enforcement

**Changes**:

```python
# Session-level deduplication set
_warned_checksums: Set[str] = set()

def run_pending_migrations(conn):
    if existing_checksum != migration.checksum:
        # V22-009 Fix: Only warn once per session
        warn_key = f"{migration.version}:{existing_checksum}"
        if warn_key not in _warned_checksums:
            _warned_checksums.add(warn_key)
            logger.warning(
                f"Migration {migration.version} checksum mismatch... "
                f"(This warning appears once per session)"
            )
```

---

## 10. GAP DETECTION IN SLOW_RUN_BUNDLE (V22-MEM)

### File: `src/integration_coworker/graph/node_trace.py`

**Commit**: `pending` V22-MEM: Gap detection for timing analysis

**Changes**:

```python
def write_slow_run_bundle(...):
    """Added timing_analysis section to detect unaccounted time gaps."""
    
    # V22-MEM: Compute gap between total runtime and sum of node durations
    total_ms = total_duration_s * 1000
    node_sum_ms = sum(node_durations.values())
    gap_ms = total_ms - node_sum_ms
    gap_pct = (gap_ms / total_ms) * 100 if total_ms > 0 else 0
    
    # Warn if >10% of time is unaccounted
    if gap_pct > 10:
        logger.warning(
            f"[SLOW_RUN_BUNDLE] {gap_pct:.1f}% of runtime unaccounted. "
            f"Gap: {gap_ms:.0f}ms, Total: {total_ms:.0f}ms, Nodes: {node_sum_ms:.0f}ms"
        )
    
    bundle["timing_analysis"] = {
        "total_ms": total_ms,
        "node_sum_ms": node_sum_ms,
        "gap_ms": gap_ms,
        "gap_pct": round(gap_pct, 2),
        "gap_warning": gap_pct > 10,
    }
```

**Result**: SLOW_RUN_BUNDLE now includes timing gap analysis to identify hidden overhead

---

## 11. LLM RESPONSE BUFFER CLEANUP (V22-MEM)

### File: `src/integration_coworker/llm/client.py`

**Commit**: `pending` V22-MEM: Explicit response cleanup

**Changes** (3 locations - OpenAI, Anthropic, Google):

```python
# OpenAI (line 818)
response = with_retry(_invoke_llm, circuit_key=circuit_key)()
result = response.content or ""
_track_token_usage(response)
del response  # Bug #V22-MEM: Free HTTP buffer memory

# Anthropic (line 1000)
response = with_retry(_invoke_llm, circuit_key=circuit_key)()
result = response.content or ""
del response  # Bug #V22-MEM: Free HTTP buffer memory

# Google (line 1183)
response = with_retry(_invoke_llm, circuit_key=circuit_key)()
result = response.content or ""
_track_token_usage(response)
del response  # Bug #V22-MEM: Free HTTP buffer memory
```

### File: `src/integration_coworker/llm/async_client.py`

**Changes** (3 locations - OpenAI, Anthropic, Google async):

```python
# OpenAI async (line 417)
response = await _retry_async(_invoke, circuit_key=circuit_key)
result = response.content or ""
_track_token_usage(response)
del response  # Bug #V22-MEM: Free HTTP buffer memory

# Anthropic async (line 643)
response = await _retry_async(_invoke, circuit_key=circuit_key)
result = response.content or ""
del response  # Bug #V22-MEM: Free HTTP buffer memory

# Google async (line 861)
response = await _retry_async(_invoke, circuit_key=circuit_key)
result = response.content or ""
_track_token_usage(response)
del response  # Bug #V22-MEM: Free HTTP buffer memory
```

**Result**: LangChain response objects now explicitly freed after content extraction

---

## 12. EXPLICIT MEMORY CLEANUP (Previous)

### File: `src/integration_coworker/graph/nodes/understand_task.py`

**Commit**: `3e3391c` V22: Explicit LLM response cleanup

**Changes**:

```python
try:
    response = from_toon(response_text)
    del response_text  # V22: Release raw LLM response after parsing
except Exception as e:
    logger.warning(f"TOON parsing failed: {e}")
    response = _parse_toon_fallback(response_text)
    del response_text  # V22: Release raw LLM response after parsing
```

### File: `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

**Commit**: `3e3391c`

**Changes**:

```python
# After stripping code fences:
clean_refined = strip_code_fences(response)
del response  # V22: Release raw LLM response

# After retry response processing:
retry_code = strip_code_fences(retry_response)
del retry_response  # V22: Release raw LLM response immediately
```

---

## 11. ASYNC SYNC FALLBACK IN GENERATE CODE

### File: `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

**Commit**: `c68a92a` V22: Fix LLM call timeout

**Changes**:

```python
# When falling back to sync client in async context:
if hasattr(sync_client, "complete"):
    # V22-011: Run sync in executor to avoid blocking event loop
    loop = asyncio.get_running_loop()
    refined = await loop.run_in_executor(
        None, lambda: sync_client.complete(prompt, system_prompt=system_prompt)
    )
```

---

## Complete Commit History (Dec 22-26)

| Commit | Date | Description |
|--------|------|-------------|
| `f28a8f3` | Dec 26 | V22-002: Fix syntax errors at line 8 |
| `c68a92a` | Dec 26 | V22: LLM call timeout for sync/async |
| `fd63555` | Dec 26 | V22: Fix _check_oom_likelihood() signature |
| `3e3391c` | Dec 26 | V22: Explicit LLM response cleanup |
| `a4052bf` | Dec 26 | V22: Memory and timeout hardening |
| `56fc03e` | Dec 25 | chore(bd): V22-011 session 2 results |
| `63a764e` | Dec 25 | V22-011: Production-ready ceiling |
| `b9ad7ef` | Dec 25 | V22-011: Thread-safe ceiling + rolling window |
| `946339e` | Dec 24 | V22-011: Memory sampler + V22-003: AST extraction |
| `bac4172` | Dec 24 | Bug #101 v22: Type-safe retry loop |
| `fcfba64` | Dec 24 | Bug #101 v21: Dynamic stubs + Response guard |
| `2a98f5e` | Dec 24 | Bug #101 v20: Exclude schema_name_to_uri |
| `34d7a55` | Dec 23 | docs: v19 deep audit |
| `c6f09bf` | Dec 23 | docs: v19 bug analysis |
| `086aac9` | Dec 23 | fix(field_mappings): None handling |
| `08c730f` | Dec 23 | fix(sandbox): Stub modules |
| `2b50e6a` | Dec 22 | Bug #101 v18: Filter checkpoint_writes |

---

## Files Modified Summary

| File | Category | Changes |
|------|----------|---------|
| `graph/state.py` | Memory | Custom `__deepcopy__` with SHALLOW_COPY_FIELDS |
| `graph/checkpointer.py` | Serialization | Channel exclusion, writes filtering |
| `graph/memory_sampler.py` | Monitoring | NEW - Background sampler |
| `graph/state_gc.py` | Memory | NEW - State garbage collection hooks |
| `graph/runtime.py` | State/GC | Ceiling checks, gc.collect(), reset_gc_stats |
| `graph/node_trace.py` | Diagnostics | Gap detection in SLOW_RUN_BUNDLE |
| `graph/nodes/embed_spec_chunks.py` | Embedding | In-loop ceiling checks, cleanup_after_embedding |
| `graph/nodes/build_silver_api_model.py` | Model | cleanup_after_silver hook |
| `graph/nodes/generate_code_and_tests.py` | CodeGen | Ceiling checks, sync→executor, del response, cleanup_after_codegen |
| `graph/nodes/understand_task.py` | LLM | del response_text |
| `llm/client.py` | Timeout+Memory | _call_with_timeout(), threading, del response |
| `llm/async_client.py` | Timeout+Memory | asyncio.wait_for() per-call, del response |
| `llm/utils.py` | Cleanup | clean_llm_code_output() enhanced |
| `codegen/sandbox.py` | Stubs | Structured logging, dynamic stubs |
| `codegen/field_mappings.py` | Safety | None guards |
| `persistence/migrations.py` | Logging | Checksum warning dedup |
| `persistence/bulk.py` | Performance | NEW - Bulk write API |

---

## Test Coverage Added

| Test File | Count | Coverage |
|-----------|-------|----------|
| `tests/graph/test_memory_sampler.py` | 23 | Memory sampler |
| `tests/graph/test_state_estimator.py` | 20 | State estimation |
| `tests/graph/test_checkpointer_context.py` | 17 | Checkpointer |
| `tests/graph/test_git_ops.py` | 29 | Git operations |

**Total new tests**: 89

---

*Generated: 2025-12-26*
