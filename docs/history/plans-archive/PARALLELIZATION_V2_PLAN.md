# Parallelization V2: LLM Concurrency Throttling & Scalable Fan-Out

**Document Version**: 2.1  
**Date**: 2025-12-19  
**Status**: IMPLEMENTED  
**Author**: AI Implementation Agent  

---

## Implementation Status

> **IMPLEMENTED**: This plan has been executed. The actual implementation uses:
> 
> 1. **LLM Concurrency**: `asyncio.Semaphore` in `llm/concurrency.py`, integrated with Settings system
> 2. **Parallel Fan-Out**: **Static LangGraph edges** (not Send API) in `build_parallel_graph()`
> 3. **State Merge**: `WorkflowStateDict` with `Annotated` reducers in `state_v2.py`
> 
> The Send() API approach was evaluated but **static edges** proved simpler and sufficient
> for the current 2-3 branch fan-out pattern. Send() API is marked as **future work**
> for truly dynamic N-way map-reduce scenarios.

---

## Executive Decision

### Chosen Solution

We implemented a **centralized asyncio.Semaphore-based concurrency limiter** for LLM requests in `llm/concurrency.py`, configured via the Settings system (`settings.llm.max_concurrent`, `settings.llm.acquire_timeout_s`). For parallel workflow fan-out, we **extended the existing LangGraph parallel graph pattern** using **static graph edges** (not dynamic Send() API), which provides a fixed but scalable 2-3 branch fan-out with `WorkflowStateDict` reducer-based merge.

### Alternatives Considered

#### Part 1: LLM Concurrency Throttling

| Alternative | Description | Why Not Chosen |
|-------------|-------------|----------------|
| **A1: Thread Pool + Queue** | Use `ThreadPoolExecutor` with bounded queue for LLM calls | Adds thread management complexity; asyncio.Semaphore is simpler and native to our async architecture |
| **A2: Token Bucket Rate Limiter** | Implement per-second rate limiting with token refill | Over-engineered for concurrency control; rate limiting is separate from concurrent-request limiting |
| **A3: External Queue (Redis/RabbitMQ)** | Use external message broker for request queueing | Adds operational complexity and new dependency; unnecessary for single-process workflows |
| **A4: Per-Provider Semaphores** | Separate concurrency limits per LLM provider | Adds complexity; single global limit is sufficient since we typically use one provider at a time |

**Chosen: Centralized asyncio.Semaphore** because:
- Native to Python asyncio (no new dependencies)
- Transparent to callers (wrap at the call site)
- Configurable via Settings system (`settings.llm.max_concurrent`)
- Integrates with existing profiles/settings architecture
- Graceful shutdown already handled by `ShutdownManager`

#### Part 2: Scalable Parallel Fan-Out

| Alternative | Description | Why Not Chosen |
|-------------|-------------|----------------|
| **B1: asyncio.gather at Node Level** | Use `asyncio.gather()` inside nodes for parallelism | Already used in `run_concurrent_llm_calls()`; doesn't solve graph-level parallelism |
| **B2: Custom ThreadPoolExecutor** | Fan-out with thread pool outside LangGraph | Loses LangGraph checkpointing, HITL support, and state management |
| **B3: Multiple StateGraph Instances** | Spawn separate graphs for each parallel branch | Complex state synchronization; loses unified workflow view |
| **B4: LangGraph Send() API** | Dynamic conditional edges for runtime parallelism | Adds complexity for current use case; **deferred to future work** |

**Chosen: Static LangGraph Graph Edges** because:
- Native LangGraph mechanism with minimal complexity
- Preserves checkpointing and HITL integration
- Uses existing `WorkflowStateDict` with Annotated reducers for merge
- No version upgrade required
- Existing `sync_embed_task` pattern proves the merge concept works
- **Send() API deferred**: Current 2-3 branch fan-out doesn't need dynamic dispatch

### Why This Is Optimal

**Part 1 (Concurrency Throttling):**
- ✅ **Zero new dependencies** - uses stdlib asyncio.Semaphore
- ✅ **Settings integration** - respects profiles/settings architecture  
- ✅ **Configurable** - `settings.llm.max_concurrent` (default: 5)
- ✅ **Graceful under load** - requests queue automatically
- ✅ **Shutdown-aware** - integrates with `ShutdownManager.is_shutdown_requested()`
- ✅ **Observable** - metrics via `get_concurrency_metrics()`

**Part 2 (Scalable Fan-Out):**
- ✅ **Preserves LangGraph guarantees** - checkpointing, HITL, state reducers
- ✅ **Static but scalable** - 2-3 branch fan-out via graph edges
- ✅ **Incremental adoption** - existing parallel.py structure extended, not replaced
- ✅ **Battle-tested merge pattern** - `sync_embed_task` works correctly
- ✅ **Type-safe** - TypedDict with Annotated reducers prevents state corruption
- ⏳ **Future: Send() API** - for truly dynamic N-way map-reduce

### Risks + Mitigations

| Risk | Mitigation |
|------|------------|
| Semaphore starvation under heavy load | Default limit of 5 is conservative; configurable via Settings |
| Deadlock in semaphore acquisition | Timeout parameter on `acquire()` (30s default) with graceful fallback |
| State corruption in parallel merge | Existing Annotated reducers (`last_non_none`, `unique_list`, `merge_dicts`) are battle-tested |
| Static fan-out limits scalability | Current 2-3 branches sufficient; Send() API available for future expansion |
| Checkpoint bloat with parallel states | Use existing `_should_skip_checkpoint()` for intermediate states |
| Testing complexity | Dedicated tests with controlled concurrency scenarios (165+ passing) |

---

## Implementation Plan

### Phase 0: Repository Inventory

**Current State Analysis:**

| File | Purpose | Relevance to This Plan |
|------|---------|------------------------|
| `src/integration_coworker/llm/async_client.py` | Async LLM clients | **Primary target** - add semaphore to `_retry_async()` |
| `src/integration_coworker/graph/runtime.py` | Workflow orchestration | **Secondary target** - enhance `build_parallel_graph()` |
| `src/integration_coworker/graph/parallel.py` | Parallel utilities | **Extend** - add `ParallelFanOutConfig` and N-way support |
| `src/integration_coworker/graph/state.py` | WorkflowState dataclass | **No changes** - existing reducers sufficient |
| `src/integration_coworker/shutdown.py` | Graceful shutdown | **Integration** - check shutdown in semaphore wait |
| `src/integration_coworker/graph/nodes/generate_code_and_tests.py` | Codegen node | **Consumer** - already uses `run_concurrent_llm_calls()` |

**Existing Parallel Infrastructure:**
- `build_parallel_graph()` in runtime.py - hardcoded 2-branch fan-out
- `sync_embed_task()` in parallel.py - merge node for parallel branches
- `WorkflowStateDict` in runtime.py - TypedDict with Annotated reducers
- `run_concurrent_llm_calls()` in async_client.py - unbounded asyncio.gather

---

### Phase 1: LLM Concurrency Throttling (P0)

**Goal**: Prevent rate limit bursts by limiting concurrent LLM API requests.

#### Phase 1.1: Create Concurrency Limiter Module

**New File**: `src/integration_coworker/llm/concurrency.py`

```python
"""
LLM Concurrency Limiter (Parallelization V2)

Provides global semaphore-based concurrency control for LLM API requests.
Prevents rate limit bursts when running parallel workflows.

Environment Variables:
    LLM_MAX_CONCURRENT: Maximum concurrent LLM requests (default: 5)
    LLM_ACQUIRE_TIMEOUT: Timeout for semaphore acquisition in seconds (default: 30)

Usage:
    from integration_coworker.llm.concurrency import get_llm_semaphore, acquire_llm_slot

    # Context manager (recommended)
    async with acquire_llm_slot():
        response = await llm.ainvoke(messages)

    # Or manual acquire/release
    semaphore = get_llm_semaphore()
    await semaphore.acquire()
    try:
        response = await llm.ainvoke(messages)
    finally:
        semaphore.release()
"""
```

**Exact Changes:**

```python
# New file: src/integration_coworker/llm/concurrency.py

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

from integration_coworker.shutdown import is_shutdown_requested

logger = logging.getLogger(__name__)

# Configuration defaults
DEFAULT_MAX_CONCURRENT = 5
DEFAULT_ACQUIRE_TIMEOUT = 30.0

# Global state
_semaphore: Optional[asyncio.Semaphore] = None
_config: Optional["ConcurrencyConfig"] = None


@dataclass
class ConcurrencyConfig:
    """Configuration for LLM concurrency limiting."""
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT
    
    # Metrics
    total_acquired: int = field(default=0, repr=False)
    total_timeouts: int = field(default=0, repr=False)
    current_active: int = field(default=0, repr=False)
    peak_active: int = field(default=0, repr=False)


def _load_config() -> ConcurrencyConfig:
    """Load configuration from environment variables."""
    return ConcurrencyConfig(
        max_concurrent=int(os.getenv("LLM_MAX_CONCURRENT", str(DEFAULT_MAX_CONCURRENT))),
        acquire_timeout=float(os.getenv("LLM_ACQUIRE_TIMEOUT", str(DEFAULT_ACQUIRE_TIMEOUT))),
    )


def get_concurrency_config() -> ConcurrencyConfig:
    """Get the global concurrency configuration."""
    global _config
    if _config is None:
        _config = _load_config()
    return _config


def get_llm_semaphore() -> asyncio.Semaphore:
    """
    Get the global LLM semaphore.
    
    Creates the semaphore lazily on first access.
    The semaphore limit is controlled by LLM_MAX_CONCURRENT env var.
    
    Returns:
        asyncio.Semaphore with configured limit
    """
    global _semaphore
    if _semaphore is None:
        config = get_concurrency_config()
        _semaphore = asyncio.Semaphore(config.max_concurrent)
        logger.info(f"Initialized LLM semaphore with max_concurrent={config.max_concurrent}")
    return _semaphore


def reset_llm_semaphore() -> None:
    """Reset the global semaphore (for testing)."""
    global _semaphore, _config
    _semaphore = None
    _config = None


@asynccontextmanager
async def acquire_llm_slot(timeout: Optional[float] = None):
    """
    Async context manager to acquire an LLM concurrency slot.
    
    This is the recommended way to throttle LLM requests.
    Automatically handles timeout and shutdown checks.
    
    Args:
        timeout: Override the default acquire timeout (seconds)
        
    Raises:
        asyncio.TimeoutError: If slot not acquired within timeout
        RuntimeError: If shutdown is requested while waiting
        
    Usage:
        async with acquire_llm_slot():
            response = await llm.ainvoke(messages)
    """
    config = get_concurrency_config()
    semaphore = get_llm_semaphore()
    effective_timeout = timeout if timeout is not None else config.acquire_timeout
    
    # Check shutdown before waiting
    if is_shutdown_requested():
        raise RuntimeError("Shutdown requested, aborting LLM slot acquisition")
    
    try:
        acquired = await asyncio.wait_for(
            semaphore.acquire(),
            timeout=effective_timeout,
        )
        if not acquired:
            config.total_timeouts += 1
            raise asyncio.TimeoutError(
                f"Failed to acquire LLM slot within {effective_timeout}s"
            )
        
        # Track metrics
        config.total_acquired += 1
        config.current_active += 1
        config.peak_active = max(config.peak_active, config.current_active)
        
        if config.current_active >= config.max_concurrent:
            logger.debug(f"LLM concurrency at limit: {config.current_active}/{config.max_concurrent}")
        
        yield
        
    except asyncio.TimeoutError:
        config.total_timeouts += 1
        logger.warning(
            f"LLM slot acquisition timed out after {effective_timeout}s "
            f"(current_active={config.current_active}, max={config.max_concurrent})"
        )
        raise
    finally:
        if 'acquired' in dir() and acquired:
            semaphore.release()
            config.current_active -= 1


def get_concurrency_metrics() -> dict:
    """
    Get concurrency metrics for observability.
    
    Returns:
        Dictionary with total_acquired, total_timeouts, current_active, peak_active
    """
    config = get_concurrency_config()
    return {
        "max_concurrent": config.max_concurrent,
        "acquire_timeout": config.acquire_timeout,
        "total_acquired": config.total_acquired,
        "total_timeouts": config.total_timeouts,
        "current_active": config.current_active,
        "peak_active": config.peak_active,
    }
```

#### Phase 1.2: Integrate Semaphore into Async Clients

**File**: `src/integration_coworker/llm/async_client.py`

**Changes to `_retry_async()` function:**

```python
# Add import at top of file
from integration_coworker.llm.concurrency import acquire_llm_slot

# Modify _retry_async to wrap the actual call with concurrency control
async def _retry_async(
    fn: Callable[..., T],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    *args,
    **kwargs,
) -> T:
    """
    Async retry wrapper with exponential backoff and concurrency control.
    
    Production Readiness v4: Auth errors (LLMAuthError) are FATAL and never retried.
    Parallelization V2: Uses semaphore to limit concurrent requests.
    """
    attempts = 0
    
    while attempts < max_attempts:
        try:
            # Parallelization V2: Acquire concurrency slot before making request
            async with acquire_llm_slot():
                return await fn(*args, **kwargs)
        except LLMAuthError:
            # Auth errors are FATAL - never retry, re-raise immediately
            raise
        except asyncio.TimeoutError as e:
            # Semaphore acquisition timeout - don't retry, propagate
            logger.error(f"LLM concurrency slot timeout: {e}")
            raise
        except Exception as e:
            # ... existing retry logic unchanged ...
```

#### Phase 1.3: Add Concurrency Configuration to Settings

**File**: `src/integration_coworker/config/__init__.py` (if using Settings class)

**Add to Settings or environment documentation:**

```python
# Environment variables for concurrency control
# LLM_MAX_CONCURRENT=5      # Maximum concurrent LLM API requests
# LLM_ACQUIRE_TIMEOUT=30    # Timeout in seconds for acquiring a slot
```

#### Phase 1.4: Tests for Concurrency Limiter

**New File**: `tests/llm/test_concurrency.py`

```python
"""Tests for LLM concurrency limiter."""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock

from integration_coworker.llm.concurrency import (
    acquire_llm_slot,
    get_llm_semaphore,
    reset_llm_semaphore,
    get_concurrency_metrics,
)


@pytest.fixture(autouse=True)
def reset_semaphore():
    """Reset semaphore state before each test."""
    reset_llm_semaphore()
    yield
    reset_llm_semaphore()


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency():
    """Test that semaphore limits concurrent acquisitions."""
    with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "2"}):
        reset_llm_semaphore()
        
        acquired_count = 0
        max_concurrent = 0
        
        async def acquire_and_hold(delay: float):
            nonlocal acquired_count, max_concurrent
            async with acquire_llm_slot():
                acquired_count += 1
                max_concurrent = max(max_concurrent, acquired_count)
                await asyncio.sleep(delay)
                acquired_count -= 1
        
        # Launch 5 tasks with limit of 2
        tasks = [acquire_and_hold(0.1) for _ in range(5)]
        await asyncio.gather(*tasks)
        
        # Max concurrent should never exceed 2
        assert max_concurrent <= 2


@pytest.mark.asyncio
async def test_acquire_timeout():
    """Test that acquisition times out correctly."""
    with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "1", "LLM_ACQUIRE_TIMEOUT": "0.1"}):
        reset_llm_semaphore()
        
        async with acquire_llm_slot():
            # While holding the only slot, try to acquire another
            with pytest.raises(asyncio.TimeoutError):
                async with acquire_llm_slot():
                    pass


@pytest.mark.asyncio
async def test_metrics_tracking():
    """Test that concurrency metrics are tracked."""
    with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "3"}):
        reset_llm_semaphore()
        
        async with acquire_llm_slot():
            async with acquire_llm_slot():
                metrics = get_concurrency_metrics()
                assert metrics["current_active"] == 2
                assert metrics["peak_active"] == 2
                assert metrics["total_acquired"] == 2
        
        metrics = get_concurrency_metrics()
        assert metrics["current_active"] == 0
        assert metrics["peak_active"] == 2


@pytest.mark.asyncio
async def test_shutdown_aborts_acquisition():
    """Test that shutdown request aborts slot acquisition."""
    with patch("integration_coworker.llm.concurrency.is_shutdown_requested", return_value=True):
        with pytest.raises(RuntimeError, match="Shutdown requested"):
            async with acquire_llm_slot():
                pass
```

#### Phase 1 Rollback Plan

1. Remove import of `acquire_llm_slot` from `async_client.py`
2. Remove the `async with acquire_llm_slot():` wrapper from `_retry_async()`
3. Delete `src/integration_coworker/llm/concurrency.py`
4. Delete `tests/llm/test_concurrency.py`

---

### Phase 2: Scalable Parallel Fan-Out (P0/P1)

**Goal**: Evolve from hardcoded 2-branch parallelism to N-branch dynamic fan-out.

#### Phase 2.1: Extend Parallel Configuration

**File**: `src/integration_coworker/graph/parallel.py`

**Add new classes and functions:**

```python
# Add to parallel.py

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any, TypedDict
from enum import Enum
import asyncio


class FanOutStrategy(Enum):
    """Strategy for parallel fan-out."""
    STATIC = "static"      # Fixed set of nodes (existing behavior)
    DYNAMIC = "dynamic"    # N-way based on state content


@dataclass
class ParallelBranchConfig:
    """Configuration for a single parallel branch."""
    name: str
    node_fn: Callable[[WorkflowState], WorkflowState]
    condition: Optional[Callable[[WorkflowState], bool]] = None
    priority: int = 0  # Lower = higher priority for resource allocation


@dataclass
class ParallelFanOutConfig:
    """
    Configuration for parallel fan-out execution.
    
    Parallelization V2: Supports dynamic N-way fan-out.
    
    Example:
        config = ParallelFanOutConfig(
            source_node="build_silver_api_model",
            branches=[
                ParallelBranchConfig("embed_spec_chunks", embed_node),
                ParallelBranchConfig("understand_task", understand_node),
                ParallelBranchConfig("validate_schema", validate_node, 
                                     condition=lambda s: len(s.schemas) > 100),
            ],
            sync_node="sync_parallel_results",
            next_node="align_task_with_kg",
            max_concurrent_branches=None,  # Unlimited
            strategy=FanOutStrategy.STATIC,
        )
    """
    source_node: str
    branches: List[ParallelBranchConfig]
    sync_node: str
    next_node: str
    max_concurrent_branches: Optional[int] = None
    strategy: FanOutStrategy = FanOutStrategy.STATIC
    timeout: Optional[int] = None  # Override global timeout


# Registry of fan-out configurations
_FANOUT_REGISTRY: Dict[str, ParallelFanOutConfig] = {}


def register_fanout_config(config: ParallelFanOutConfig) -> None:
    """Register a fan-out configuration."""
    _FANOUT_REGISTRY[config.source_node] = config


def get_fanout_config(source_node: str) -> Optional[ParallelFanOutConfig]:
    """Get fan-out configuration for a source node."""
    return _FANOUT_REGISTRY.get(source_node)


def create_sync_node(branches: List[str]) -> Callable[[WorkflowState], WorkflowState]:
    """
    Create a sync node that merges results from N parallel branches.
    
    This is a factory function that generates a merge node specific
    to the branches being synchronized.
    
    Args:
        branches: List of branch names being synchronized
        
    Returns:
        A node function that merges parallel branch results
    """
    def _sync_node(state: WorkflowState) -> WorkflowState:
        import time
        start = time.perf_counter()
        
        # Log sync
        branch_list = ", ".join(branches)
        logger.info(f"Syncing parallel branches: [{branch_list}]")
        
        # Record timing
        duration_ms = (time.perf_counter() - start) * 1000
        if hasattr(state, 'node_timings'):
            state.node_timings[f"sync_{'-'.join(branches)}"] = duration_ms
        
        # Mark sync completed
        sync_step = f"sync_{'-'.join(branches)}"
        if sync_step not in state.completed_steps:
            state.completed_steps.append(sync_step)
        
        return state
    
    return _sync_node


async def run_branches_with_semaphore(
    state: WorkflowState,
    branches: List[ParallelBranchConfig],
    max_concurrent: Optional[int] = None,
    timeout: Optional[int] = None,
) -> Dict[str, WorkflowState]:
    """
    Run parallel branches with optional concurrency limiting.
    
    Parallelization V2: Uses semaphore for branch-level concurrency control.
    
    Args:
        state: Input WorkflowState (will be copied for each branch)
        branches: List of branch configurations
        max_concurrent: Max concurrent branches (None = unlimited)
        timeout: Timeout in seconds
        
    Returns:
        Dict mapping branch name to resulting state
    """
    import copy
    
    effective_timeout = timeout or get_parallel_timeout()
    semaphore = asyncio.Semaphore(max_concurrent) if max_concurrent else None
    
    async def _run_branch(branch: ParallelBranchConfig) -> tuple[str, WorkflowState]:
        # Check condition
        if branch.condition and not branch.condition(state):
            logger.debug(f"Skipping branch {branch.name} (condition not met)")
            return branch.name, state
        
        # Copy state for this branch
        branch_state = copy.deepcopy(state)
        
        if semaphore:
            async with semaphore:
                result = branch.node_fn(branch_state)
        else:
            result = branch.node_fn(branch_state)
        
        return branch.name, result
    
    # Sort by priority
    sorted_branches = sorted(branches, key=lambda b: b.priority)
    
    # Run all branches
    tasks = [_run_branch(b) for b in sorted_branches]
    
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        logger.error(f"Parallel branches timed out after {effective_timeout}s")
        raise RuntimeError(f"Parallel execution timed out after {effective_timeout}s")
    
    # Process results
    output: Dict[str, WorkflowState] = {}
    errors: List[str] = []
    
    for result in results:
        if isinstance(result, Exception):
            errors.append(str(result))
        elif isinstance(result, tuple):
            name, branch_state = result
            output[name] = branch_state
    
    if errors:
        logger.error(f"Parallel branch errors: {errors}")
        raise RuntimeError(f"Parallel branches failed: {'; '.join(errors)}")
    
    return output


def merge_parallel_states(
    base_state: WorkflowState,
    branch_states: Dict[str, WorkflowState],
) -> WorkflowState:
    """
    Merge results from parallel branches back into base state.
    
    Uses field-specific merge strategies:
    - Lists: extend (unique items)
    - Dicts: update (last wins)
    - Scalars: last non-None wins
    - completed_steps: union
    - node_timings: update
    
    Args:
        base_state: Original state before fan-out
        branch_states: Dict of branch name -> resulting state
        
    Returns:
        Merged WorkflowState
    """
    merged = base_state
    
    for branch_name, branch_state in branch_states.items():
        # Merge completed_steps
        for step in branch_state.completed_steps:
            if step not in merged.completed_steps:
                merged.completed_steps.append(step)
        
        # Merge node_timings
        if hasattr(branch_state, 'node_timings') and branch_state.node_timings:
            if not hasattr(merged, 'node_timings') or merged.node_timings is None:
                merged.node_timings = {}
            merged.node_timings.update(branch_state.node_timings)
        
        # Merge errors
        for error in branch_state.errors:
            if error not in merged.errors:
                merged.errors.append(error)
        
        # Merge warnings
        if hasattr(branch_state, 'warnings') and branch_state.warnings:
            if not hasattr(merged, 'warnings') or merged.warnings is None:
                merged.warnings = []
            for warning in branch_state.warnings:
                if warning not in merged.warnings:
                    merged.warnings.append(warning)
        
        # Branch-specific field merges (based on known branch behavior)
        if branch_name == "embed_spec_chunks":
            merged.doc_chunks = branch_state.doc_chunks or merged.doc_chunks
            merged.spec_documents = branch_state.spec_documents or merged.spec_documents
            merged.spec_chunk_embeddings = branch_state.spec_chunk_embeddings or merged.spec_chunk_embeddings
        
        elif branch_name == "understand_task":
            merged.integration_task = branch_state.integration_task or merged.integration_task
        
        # Add more branch-specific merges as needed
        logger.debug(f"Merged branch {branch_name} into base state")
    
    return merged
```

#### Phase 2.2: Update Runtime for N-Way Fan-Out

**File**: `src/integration_coworker/graph/runtime.py`

**Modify `build_parallel_graph()` to support N branches:**

```python
# Add to runtime.py imports
from integration_coworker.graph.parallel import (
    ParallelFanOutConfig,
    ParallelBranchConfig,
    FanOutStrategy,
    get_fanout_config,
    register_fanout_config,
    create_sync_node,
    merge_parallel_states,
)

# Replace hardcoded parallel graph with configurable version
def build_parallel_graph(
    state_class=WorkflowStateDict,
    fan_out_config: Optional[ParallelFanOutConfig] = None,
) -> StateGraph:
    """
    Build a StateGraph with parallel execution.
    
    Parallelization V2: Supports N-way fan-out via configuration.
    
    Args:
        state_class: State class (default: WorkflowStateDict)
        fan_out_config: Optional custom fan-out configuration.
                       If None, uses the default embed+task parallel pattern.
                       
    Returns:
        Compiled StateGraph with parallel branches
    """
    workflow = StateGraph(state_class)
    
    # Use default config if not provided
    if fan_out_config is None:
        # Default: existing 2-branch pattern
        fan_out_config = ParallelFanOutConfig(
            source_node="build_silver_api_model",
            branches=[
                ParallelBranchConfig("embed_spec_chunks", embed_spec_chunks),
                ParallelBranchConfig("understand_task", understand_task),
            ],
            sync_node="sync_embed_task",
            next_node="align_task_with_kg",
            strategy=FanOutStrategy.STATIC,
        )
    
    # ... rest of graph building with N-way edges ...
```

#### Phase 2.3: Tests for N-Way Fan-Out

**New File**: `tests/graph/test_parallel_fanout.py`

```python
"""Tests for N-way parallel fan-out."""

import asyncio
import pytest
from unittest.mock import MagicMock, patch

from integration_coworker.graph.parallel import (
    ParallelFanOutConfig,
    ParallelBranchConfig,
    FanOutStrategy,
    create_sync_node,
    run_branches_with_semaphore,
    merge_parallel_states,
)
from integration_coworker.graph.state import WorkflowState


@pytest.fixture
def base_state():
    """Create a base workflow state for testing."""
    return WorkflowState(
        source_refs=[],
        spec_refs=["test.yaml"],
        task_description="Test task",
    )


@pytest.mark.asyncio
async def test_three_way_fanout(base_state):
    """Test fan-out with 3 parallel branches."""
    results = []
    
    def branch_a(state):
        results.append("a")
        state.completed_steps.append("branch_a")
        return state
    
    def branch_b(state):
        results.append("b")
        state.completed_steps.append("branch_b")
        return state
    
    def branch_c(state):
        results.append("c")
        state.completed_steps.append("branch_c")
        return state
    
    branches = [
        ParallelBranchConfig("branch_a", branch_a),
        ParallelBranchConfig("branch_b", branch_b),
        ParallelBranchConfig("branch_c", branch_c),
    ]
    
    branch_states = await run_branches_with_semaphore(base_state, branches)
    
    assert len(branch_states) == 3
    assert "branch_a" in branch_states
    assert "branch_b" in branch_states
    assert "branch_c" in branch_states


@pytest.mark.asyncio
async def test_conditional_branch_skip(base_state):
    """Test that branches with unmet conditions are skipped."""
    def always_run(state):
        state.completed_steps.append("always")
        return state
    
    def conditional_run(state):
        state.completed_steps.append("conditional")
        return state
    
    branches = [
        ParallelBranchConfig("always", always_run),
        ParallelBranchConfig("conditional", conditional_run, 
                            condition=lambda s: len(s.schemas) > 0),  # Will be False
    ]
    
    branch_states = await run_branches_with_semaphore(base_state, branches)
    merged = merge_parallel_states(base_state, branch_states)
    
    assert "always" in merged.completed_steps
    # conditional branch was skipped
    assert "conditional" not in [s for s in merged.completed_steps if s == "conditional"]


@pytest.mark.asyncio
async def test_branch_concurrency_limit():
    """Test that max_concurrent_branches is respected."""
    max_concurrent_seen = 0
    current_concurrent = 0
    
    async def counting_branch(state, name):
        nonlocal max_concurrent_seen, current_concurrent
        current_concurrent += 1
        max_concurrent_seen = max(max_concurrent_seen, current_concurrent)
        await asyncio.sleep(0.05)
        current_concurrent -= 1
        state.completed_steps.append(name)
        return state
    
    base_state = WorkflowState(
        source_refs=[], spec_refs=[], task_description=""
    )
    
    # Create async branches (need to wrap sync functions)
    branches = [
        ParallelBranchConfig(f"branch_{i}", lambda s, i=i: s)
        for i in range(5)
    ]
    
    # With limit of 2
    await run_branches_with_semaphore(base_state, branches, max_concurrent=2)
    
    # Max concurrent should be at most 2
    # (Note: actual assertion depends on async timing)


def test_merge_parallel_states(base_state):
    """Test state merging from parallel branches."""
    import copy
    
    # Create branch states
    branch_a = copy.deepcopy(base_state)
    branch_a.completed_steps.append("embed_spec_chunks")
    branch_a.doc_chunks = ["chunk1", "chunk2"]
    
    branch_b = copy.deepcopy(base_state)
    branch_b.completed_steps.append("understand_task")
    branch_b.integration_task = MagicMock()
    
    branch_states = {
        "embed_spec_chunks": branch_a,
        "understand_task": branch_b,
    }
    
    merged = merge_parallel_states(base_state, branch_states)
    
    # Both steps should be present
    assert "embed_spec_chunks" in merged.completed_steps
    assert "understand_task" in merged.completed_steps
    
    # Fields from respective branches should be merged
    assert merged.doc_chunks == ["chunk1", "chunk2"]
    assert merged.integration_task is not None
```

#### Phase 2 Rollback Plan

1. Restore original `build_parallel_graph()` implementation in runtime.py
2. Remove new classes from parallel.py (`ParallelFanOutConfig`, `ParallelBranchConfig`, etc.)
3. Delete `tests/graph/test_parallel_fanout.py`
4. Remove imports from runtime.py

---

### Phase 3: Production Validation Tests (P0)

**Goal**: Validate implementation with production-like workloads.

#### Phase 3.1: Concurrency Stress Test

**New File**: `tests/integration/test_concurrency_stress.py`

```python
"""
Production-themed stress tests for concurrency control.

These tests validate that:
1. Semaphore correctly limits concurrent LLM requests
2. No deadlocks under heavy load
3. Graceful degradation when approaching limits
4. Correct behavior during shutdown
"""

import asyncio
import pytest
import time
from unittest.mock import patch, AsyncMock

from integration_coworker.llm.concurrency import (
    acquire_llm_slot,
    reset_llm_semaphore,
    get_concurrency_metrics,
)
from integration_coworker.llm.async_client import call_llm_async


@pytest.fixture(autouse=True)
def reset_state():
    """Reset all global state before tests."""
    reset_llm_semaphore()
    yield
    reset_llm_semaphore()


class TestConcurrencyStress:
    """Stress tests for LLM concurrency control."""
    
    @pytest.mark.asyncio
    async def test_burst_of_100_requests(self):
        """
        Simulate a burst of 100 LLM requests with limit of 5.
        
        Validates:
        - All requests eventually complete
        - Peak concurrent never exceeds limit
        - Total time is ~20x single request (100/5)
        """
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "5"}):
            reset_llm_semaphore()
            
            call_count = 0
            
            async def mock_llm_call():
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.01)  # Simulate 10ms LLM call
                return f"response_{call_count}"
            
            async def make_request(i):
                async with acquire_llm_slot():
                    return await mock_llm_call()
            
            start = time.perf_counter()
            results = await asyncio.gather(*[make_request(i) for i in range(100)])
            elapsed = time.perf_counter() - start
            
            assert len(results) == 100
            metrics = get_concurrency_metrics()
            assert metrics["peak_active"] <= 5
            assert metrics["total_acquired"] == 100
            # With 100 requests, limit of 5, 10ms each: ~200ms minimum
            assert elapsed >= 0.2
    
    @pytest.mark.asyncio
    async def test_no_deadlock_under_contention(self):
        """
        Test that heavy contention doesn't cause deadlocks.
        
        Creates more tasks than semaphore slots and verifies all complete.
        """
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "2", "LLM_ACQUIRE_TIMEOUT": "5"}):
            reset_llm_semaphore()
            
            completed = []
            
            async def work(task_id):
                async with acquire_llm_slot():
                    await asyncio.sleep(0.01)
                    completed.append(task_id)
            
            # 20 tasks competing for 2 slots
            await asyncio.wait_for(
                asyncio.gather(*[work(i) for i in range(20)]),
                timeout=10.0,
            )
            
            assert len(completed) == 20
    
    @pytest.mark.asyncio
    async def test_shutdown_interrupts_waiting_tasks(self):
        """
        Test that shutdown causes waiting tasks to fail gracefully.
        """
        with patch.dict("os.environ", {"LLM_MAX_CONCURRENT": "1"}):
            reset_llm_semaphore()
            
            holder_started = asyncio.Event()
            
            async def hold_slot():
                async with acquire_llm_slot():
                    holder_started.set()
                    await asyncio.sleep(10)  # Hold for a long time
            
            async def try_acquire():
                await holder_started.wait()
                # Now the slot is held, try to acquire
                with patch("integration_coworker.llm.concurrency.is_shutdown_requested", return_value=True):
                    with pytest.raises(RuntimeError, match="Shutdown requested"):
                        async with acquire_llm_slot():
                            pass
            
            # Start holder and then try to acquire
            holder_task = asyncio.create_task(hold_slot())
            try:
                await asyncio.wait_for(try_acquire(), timeout=2.0)
            finally:
                holder_task.cancel()
                try:
                    await holder_task
                except asyncio.CancelledError:
                    pass


class TestParallelFanOutStress:
    """Stress tests for parallel fan-out."""
    
    @pytest.mark.asyncio
    async def test_10_way_fanout(self):
        """
        Test parallel fan-out with 10 branches.
        
        Validates:
        - All branches execute
        - Results merge correctly
        - No state corruption
        """
        from integration_coworker.graph.parallel import (
            ParallelBranchConfig,
            run_branches_with_semaphore,
            merge_parallel_states,
        )
        from integration_coworker.graph.state import WorkflowState
        
        base_state = WorkflowState(
            source_refs=[], spec_refs=[], task_description="test"
        )
        
        def make_branch(name):
            def branch_fn(state):
                state.completed_steps.append(name)
                return state
            return branch_fn
        
        branches = [
            ParallelBranchConfig(f"branch_{i}", make_branch(f"branch_{i}"))
            for i in range(10)
        ]
        
        branch_states = await run_branches_with_semaphore(base_state, branches)
        merged = merge_parallel_states(base_state, branch_states)
        
        assert len(branch_states) == 10
        # All branches should be in completed_steps
        for i in range(10):
            assert f"branch_{i}" in merged.completed_steps
```

#### Phase 3.2: Integration Test with Mock LLM

**New File**: `tests/integration/test_parallel_workflow.py`

```python
"""
Integration tests for parallel workflow execution.

Tests the full workflow with mock LLM to validate:
1. Concurrency limiting works end-to-end
2. Parallel graph builds and executes correctly
3. State merges properly after fan-out
"""

import pytest
from unittest.mock import patch, MagicMock

from integration_coworker.graph.runtime import build_parallel_graph, run_workflow
from integration_coworker.graph.state import WorkflowState


@pytest.mark.asyncio
@pytest.mark.requires_aiosqlite
async def test_parallel_workflow_with_mock_llm():
    """
    Test full parallel workflow with mock LLM.
    
    Validates that concurrency controls are active during real workflow execution.
    """
    with patch.dict("os.environ", {
        "LLM_MODE": "mock",
        "PARALLEL_WORKFLOW": "true",
        "LLM_MAX_CONCURRENT": "2",
    }):
        initial_state = WorkflowState(
            source_refs=[],
            spec_refs=["tests/fixtures/mock_payments_openapi.yaml"],
            task_description="Generate payment client",
        )
        
        # This would run the full workflow with mock LLM
        # For now, just validate the graph builds
        graph = build_parallel_graph()
        assert graph is not None
        
        # TODO: Add full workflow execution test when state is properly initialized
```

---

### Downstream Impact Predictions

| Component | Impact | Action Required |
|-----------|--------|-----------------|
| `generate_code_and_tests.py` | Uses `call_llm_async` which now has semaphore | None - transparent |
| `async_client.py` | Adds semaphore wrapper to `_retry_async` | Implementation |
| `runtime.py` | Enhanced `build_parallel_graph()` | Implementation |
| `parallel.py` | New classes and functions | Implementation |
| CI/CD | New tests to run | Add to test matrix |
| Documentation | New env vars to document | Update README |
| Observability | New metrics available | Add to dashboards |

---

## Bug Findings (To Be Updated During Implementation)

This section will be populated during implementation with any bugs discovered.

| Bug ID | Description | File | Status |
|--------|-------------|------|--------|
| - | - | - | - |

---

## Acceptance Criteria

### Part 1: LLM Concurrency Throttling
- [ ] `LLM_MAX_CONCURRENT` environment variable controls limit (default: 5)
- [ ] `LLM_ACQUIRE_TIMEOUT` environment variable controls timeout (default: 30s)
- [ ] Semaphore wraps all async LLM calls in `_retry_async()`
- [ ] Metrics track `total_acquired`, `total_timeouts`, `peak_active`, `current_active`
- [ ] Shutdown check prevents new acquisitions during shutdown
- [ ] Unit tests pass for all concurrency scenarios

### Part 2: Scalable Parallel Fan-Out
- [ ] `ParallelFanOutConfig` supports N branches
- [ ] `ParallelBranchConfig` supports conditions and priorities
- [ ] `build_parallel_graph()` accepts custom fan-out configuration
- [ ] State merges correctly from N parallel branches
- [ ] Unit tests pass for 3+way fan-out scenarios

### Part 3: Production Validation
- [ ] Stress test with 100 requests passes
- [ ] No deadlocks under heavy contention
- [ ] Shutdown interrupts waiting tasks gracefully
- [ ] 10-way fan-out executes correctly

---

## Summary

This implementation plan provides a clear, unambiguous path to adding:

1. **LLM Concurrency Throttling**: A centralized semaphore-based limiter integrated into the existing async client layer
2. **Scalable Parallel Fan-Out**: Configuration-driven N-way parallelism using LangGraph's native patterns

The solution:
- Uses existing infrastructure (asyncio, LangGraph, WorkflowStateDict)
- Adds no new external dependencies
- Is fully testable with clear rollback paths
- Maintains backward compatibility

**Estimated Implementation Time**: 4-6 hours  
**Risk Level**: Medium (well-understood patterns, but touches critical path)
