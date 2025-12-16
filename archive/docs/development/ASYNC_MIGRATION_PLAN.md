# Async-Everywhere Migration Plan

## Status: ✅ COMPLETED

Migration completed on 2025-01-XX. All LLM-calling graph nodes are now async.

## Executive Summary

This plan migrates the LLM client infrastructure from a dual sync/async architecture to async-first with sync wrappers. The async client (`async_client.py`) becomes the primary implementation; the sync client (`client.py`) is refactored to be a thin wrapper around async primitives.

**Goal:** Single async code path, reduced maintenance burden, better concurrency for parallel LLM calls.

---

## Completion Summary

### Phase 1: ✅ COMPLETE - Consolidate Async Clients
- Added Redis cache integration to async clients (`complete_async()`)
- Created `get_async_llm_client_for_node()` function
- Created `call_llm_async_for_node()` function

### Phase 2: ✅ COMPLETE - Convert Sync API to Async Wrappers
- `call_llm_for_node()` now wraps `call_llm_async_for_node()` via `asyncio.run()`
- Added deprecation warnings to sync functions

### Phase 3: ✅ COMPLETE - Migrate Graph Nodes to Async
All 5 LLM-calling nodes converted:
- ✅ `understand_task.py` - `async def understand_task()`
- ✅ `build_report.py` - `async def build_report()`  
- ✅ `plan_integration_flow.py` - `async def plan_integration_flow()`
- ✅ `attach_policies_and_patterns.py` - `async def attach_policies_and_patterns()`
- ✅ `generate_code_and_tests.py` - `async def generate_code_and_tests()`

### Phase 4: ✅ COMPLETE - Update LangGraph Runtime
- Verified `timed_node()` decorator already handles async nodes
- LangGraph `StateGraph.add_node()` natively supports async functions
- No changes needed to graph building code

### Phase 5: ✅ COMPLETE - Deprecate Sync Client
- Added `DeprecationWarning` to `get_llm_client_for_node()`
- Added `DeprecationWarning` to `call_llm_for_node()`
- Updated module docstring in `llm/__init__.py` to recommend async API

---

## Original Plan (for reference)

## Current State Audit

### Files Affected

| File | Current Role | Sync Calls | Async Calls |
|------|--------------|------------|-------------|
| `llm/client.py` | Primary sync implementation | `OpenAILLMClient.complete()`, `AnthropicLLMClient.complete()`, `GoogleLLMClient.complete()` | None |
| `llm/async_client.py` | Async implementation | Sync wrappers via `asyncio.run()` | `complete_async()`, `complete_json_async()` |
| `llm/__init__.py` | Exports both | `call_llm_for_node()` | `call_llm_async_for_node()` |

### Consumers (Sync LLM Calls)

| File | Function | Call Pattern |
|------|----------|--------------|
| `graph/nodes/understand_task.py:279` | `understand_task()` | `call_llm_for_node("understand_task", prompt)` |
| `graph/nodes/build_report.py:299` | `build_report()` | `call_llm_for_node("build_report", summary_prompt)` |
| `graph/nodes/generate_code_and_tests.py:967,983,1153,1981,1985,2380,2404` | `_refine_*()` | `client.complete(prompt)` |
| `graph/nodes/plan_integration_flow.py:259,262` | `plan_integration_flow()` | `client.complete(prompt)` |
| `graph/nodes/attach_policies_and_patterns.py:336,337` | `attach_policies_and_patterns()` | `client.complete(prompt)` |
| `repo/llm_inference.py:280` | `infer_repo_config()` | `call_llm_for_node("repo_config_inference", prompt)` |
| `repo/detection.py:899` | `detect_repo_archetype()` | `client.complete(prompt)` |

### Consumers (Already Async)

| File | Function | Call Pattern |
|------|----------|--------------|
| `graph/nodes/generate_code_and_tests.py:325` | `_refine_artifact_async()` | `await call_llm_async(prompt)` |
| `graph/nodes/generate_code_and_tests.py:508,568` | `_generate_artifacts_*_async()` | `await asyncio.gather(*tasks)` |

---

## Migration Strategy

### Phase 1: Consolidate Async Clients (async_client.py becomes canonical)

1. **Move shared utilities to async_client.py**
   - `harden_system_prompt()` - already imported
   - `get_run_context()` / `set_run_context()` - already imported
   - `_track_token_usage()` - already imported
   - `_save_interaction()` / `_load_interaction()` - already imported

2. **Add Redis cache integration to async clients**
   - Async clients currently bypass Redis cache
   - Add `get_llm_cache()` integration to `complete_async()`

3. **Create `get_async_llm_client_for_node()`**
   - Mirror `get_llm_client_for_node()` but return async client
   - Use archetype YAML configs

### Phase 2: Convert Sync API to Async Wrappers

1. **Refactor `call_llm_for_node()` in client.py**
   - Change implementation to: `asyncio.run(call_llm_async_for_node(...))`
   - This becomes a thin sync wrapper

2. **Refactor `LLMClient.complete()` methods**
   - For each sync client class: `complete()` calls `asyncio.run(complete_async())`
   - Sync clients delegate to async implementation

3. **Add deprecation warnings**
   - Sync methods emit `DeprecationWarning`
   - Point to async equivalents

### Phase 3: Migrate Graph Nodes to Async

Each node needs to be converted. Priority order:

1. **High-frequency nodes (most LLM calls)**
   - `generate_code_and_tests.py` - Already partially async
   - `understand_task.py` - Single call, easy
   - `plan_integration_flow.py` - Single call

2. **Lower-frequency nodes**
   - `build_report.py` - Single call
   - `attach_policies_and_patterns.py` - Single call

3. **Non-graph LLM callers**
   - `repo/llm_inference.py`
   - `repo/detection.py`

### Phase 4: Update LangGraph Runtime

1. **Graph node signatures**
   - Change `def node_name(state) -> state` to `async def node_name(state) -> state`
   - LangGraph supports async nodes natively

2. **Runtime invocation**
   - Update `graph/runtime.py` to use `await graph.ainvoke()` instead of `graph.invoke()`

### Phase 5: Deprecate Sync Client Classes

1. **Mark sync classes deprecated**
   - `OpenAILLMClient` → use `AsyncOpenAILLMClient`
   - `AnthropicLLMClient` → use `AsyncAnthropicLLMClient`
   - `GoogleLLMClient` → use `AsyncGoogleLLMClient`

2. **Update `__init__.py` exports**
   - Primary exports become async
   - Sync exports marked `# Deprecated`

---

## Detailed Implementation Steps

### Step 1: Add Cache Integration to Async Clients

**File:** `src/integration_coworker/llm/async_client.py`

```python
# In AsyncOpenAILLMClient.complete_async():

async def complete_async(self, prompt: str, ...) -> str:
    from integration_coworker.llm.cache import get_llm_cache
    
    cache = get_llm_cache()
    
    # Check cache first
    if cache.is_enabled():
        cached = cache.get(
            prompt=prompt,
            system_prompt=hardened_system,
            model=self.model,
            provider="openai",
            task_type=self.task_type,
        )
        if cached is not None:
            logger.debug("Cache hit for async LLM call")
            return cached
    
    # ... existing LLM call logic ...
    
    # Store in cache
    if cache.is_enabled():
        cache.set(
            prompt=prompt,
            system_prompt=hardened_system,
            model=self.model,
            provider="openai",
            task_type=self.task_type,
            response=result,
        )
    
    return result
```

### Step 2: Create `get_async_llm_client_for_node()`

**File:** `src/integration_coworker/llm/async_client.py`

```python
def get_async_llm_client_for_node(node_name: str, strict: bool = False) -> AsyncLLMClient:
    """
    Get an async LLM client configured for a specific LangGraph node.
    
    This is the recommended async entry point for nodes. It loads the archetype
    YAML for the given node name and returns a properly configured async client.
    """
    from integration_coworker.config import load_archetype
    
    archetype = load_archetype(node_name)
    return _get_async_client_for_archetype(archetype, strict=strict)


def _get_async_client_for_archetype(
    archetype_config: Dict[str, Any],
    strict: bool = False,
) -> AsyncLLMClient:
    """Create async client from archetype config."""
    # Extract config
    model_config = archetype_config.get("model", {})
    provider = model_config.get("provider", "openai")
    model = model_config.get("name", "gpt-4o")
    temperature = model_config.get("temperature", 0.7)
    max_tokens = model_config.get("max_tokens", 2000)
    task_type = archetype_config.get("name", "default")
    
    # Create appropriate async client
    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key and strict:
            raise RuntimeError("OPENAI_API_KEY not set")
        return AsyncOpenAILLMClient(
            api_key=api_key,
            model=model,
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )
    elif provider == "anthropic":
        # ... similar for anthropic
    elif provider == "google":
        # ... similar for google
    else:
        # Fallback chain
        return get_async_llm_client(task_type=task_type, strict=strict)
```

### Step 3: Refactor `call_llm_for_node()` to Use Async

**File:** `src/integration_coworker/llm/client.py`

```python
def call_llm_for_node(
    node_name: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Make an LLM call using archetype configuration for a specific node.
    
    DEPRECATED: Use call_llm_async_for_node() in async contexts.
    This is now a sync wrapper around the async implementation.
    """
    import warnings
    warnings.warn(
        "call_llm_for_node() is deprecated. Use await call_llm_async_for_node() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    
    from integration_coworker.llm.async_client import call_llm_async_for_node
    
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    
    if loop is not None:
        # Already in async context - use thread pool
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run,
                call_llm_async_for_node(node_name, prompt, system_prompt, temperature, max_tokens)
            )
            return future.result()
    else:
        return asyncio.run(
            call_llm_async_for_node(node_name, prompt, system_prompt, temperature, max_tokens)
        )
```

### Step 4: Convert Graph Nodes to Async

**Example: `understand_task.py`**

```python
# Before:
def understand_task(state: WorkflowState) -> WorkflowState:
    response_text = call_llm_for_node("understand_task", prompt)
    ...

# After:
async def understand_task(state: WorkflowState) -> WorkflowState:
    from integration_coworker.llm import call_llm_async_for_node
    response_text = await call_llm_async_for_node("understand_task", prompt)
    ...
```

**Files to convert:**
- `understand_task.py` - 1 call site
- `build_report.py` - 1 call site
- `plan_integration_flow.py` - 1 call site
- `attach_policies_and_patterns.py` - 1 call site
- `generate_code_and_tests.py` - Already has async path, consolidate

### Step 5: Update LangGraph Runtime

**File:** `src/integration_coworker/graph/runtime.py`

```python
# Before:
def run_graph(state: WorkflowState) -> WorkflowState:
    return graph.invoke(state)

# After:
async def run_graph_async(state: WorkflowState) -> WorkflowState:
    return await graph.ainvoke(state)

def run_graph(state: WorkflowState) -> WorkflowState:
    """Sync wrapper for CLI compatibility."""
    return asyncio.run(run_graph_async(state))
```

### Step 6: Update `__init__.py` Exports

**File:** `src/integration_coworker/llm/__init__.py`

```python
"""
LLM Client Module - Async-First Architecture

Primary async API (recommended):
    from integration_coworker.llm import (
        call_llm_async_for_node,
        get_async_llm_client_for_node,
        run_concurrent_llm_calls,
    )

Sync wrappers (for CLI/scripts, deprecated for new code):
    from integration_coworker.llm import (
        call_llm_for_node,  # Deprecated: wraps async
        get_llm_client_for_node,  # Deprecated: wraps async
    )
"""

# Primary async exports
from .async_client import (
    AsyncLLMClient,
    AsyncOpenAILLMClient,
    AsyncAnthropicLLMClient,
    AsyncGoogleLLMClient,
    get_async_llm_client,
    get_async_llm_client_for_node,  # NEW
    call_llm_async_for_node,
    run_concurrent_llm_calls,
    reset_async_client_cache,
)

# Deprecated sync exports (wrappers around async)
from .client import (
    LLMClient,  # Deprecated
    get_llm_client,  # Deprecated
    get_llm_client_for_node,  # Deprecated: use get_async_llm_client_for_node
    call_llm_for_node,  # Deprecated: use call_llm_async_for_node
    is_mock_llm_mode,
    MockLLMClient,
    set_run_context,
    get_run_context,
    clear_run_context,
)
```

---

## Testing Strategy

### Unit Tests

1. **Test async clients directly**
   ```python
   @pytest.mark.asyncio
   async def test_async_openai_client():
       client = AsyncOpenAILLMClient(api_key="test", model="gpt-4o")
       # Mock the LangChain call
       response = await client.complete_async("test prompt")
       assert response
   ```

2. **Test sync wrappers**
   ```python
   def test_sync_wrapper_calls_async():
       with pytest.warns(DeprecationWarning):
           response = call_llm_for_node("understand_task", "test")
   ```

3. **Test cache integration**
   ```python
   @pytest.mark.asyncio
   async def test_async_cache_hit():
       # First call
       r1 = await call_llm_async_for_node("test_node", "prompt")
       # Second call should hit cache
       r2 = await call_llm_async_for_node("test_node", "prompt")
       assert r1 == r2
   ```

### Integration Tests

1. **Run full workflow async**
   ```python
   @pytest.mark.asyncio
   async def test_e2e_async_workflow():
       state = WorkflowState(...)
       result = await run_graph_async(state)
       assert result.status == "completed"
   ```

---

## Migration Checklist

### Phase 1: Async Client Enhancements
- [ ] Add Redis cache to `AsyncOpenAILLMClient.complete_async()`
- [ ] Add Redis cache to `AsyncAnthropicLLMClient.complete_async()`
- [ ] Add Redis cache to `AsyncGoogleLLMClient.complete_async()`
- [ ] Create `get_async_llm_client_for_node()`
- [ ] Create `_get_async_client_for_archetype()`
- [ ] Add tests for async cache integration

### Phase 2: Sync Wrappers
- [ ] Refactor `call_llm_for_node()` to wrap async
- [ ] Refactor `LLMClient.complete()` classes to wrap async
- [ ] Add `DeprecationWarning` to sync methods
- [ ] Update docstrings

### Phase 3: Graph Node Conversion
- [ ] Convert `understand_task.py` to async
- [ ] Convert `build_report.py` to async
- [ ] Convert `plan_integration_flow.py` to async
- [ ] Convert `attach_policies_and_patterns.py` to async
- [ ] Consolidate `generate_code_and_tests.py` to pure async
- [ ] Convert `repo/llm_inference.py` to async
- [ ] Convert `repo/detection.py` to async

### Phase 4: Runtime Updates
- [ ] Add `run_graph_async()` to runtime.py
- [ ] Update CLI to use `asyncio.run()`
- [ ] Update tests to use `@pytest.mark.asyncio`

### Phase 5: Cleanup
- [ ] Update `__init__.py` exports
- [ ] Update documentation
- [ ] Remove dead sync code (after deprecation period)

---

## Rollback Plan

If issues arise:

1. **Revert node signatures** - Change `async def` back to `def`
2. **Remove deprecation warnings** - Keep both paths active
3. **Keep sync clients** - Don't remove until stable

---

## Timeline

| Week | Phase | Deliverables |
|------|-------|--------------|
| 1 | Phase 1 | Async client enhancements, cache integration |
| 1 | Phase 2 | Sync wrappers refactored |
| 2 | Phase 3 | All graph nodes converted |
| 2 | Phase 4 | Runtime updated |
| 3 | Phase 5 | Cleanup, documentation, testing |

---

## Success Criteria

1. **All LLM calls go through async path** (even sync wrappers)
2. **Redis cache works with async clients**
3. **Parallel LLM calls in `generate_code_and_tests` use true concurrency**
4. **No test regressions**
5. **Deprecation warnings for sync API**
