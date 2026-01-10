"""
TEST-ONLY Parallel Execution Utilities

WARNING: These utilities are for TESTING ONLY. They bypass LangGraph's
native fan-out and reducer semantics.

PRODUCTION parallelism must use:
- build_parallel_graph() in runtime.py (LangGraph edges + WorkflowStateDict reducers)
- LangGraph Send API for dynamic map-reduce (future work)

DO NOT import this module into src/ code paths.
"""

import asyncio
import copy
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.parallel import get_parallel_timeout


class FanOutStrategy(str, Enum):
    """
    TEST-ONLY: Fan-out execution strategies.
    
    For production, use LangGraph's native fan-out via static graph edges.
    """
    STATIC = "static"
    DYNAMIC = "dynamic"

logger = logging.getLogger(__name__)


@dataclass
class ParallelBranchConfig:
    """
    TEST-ONLY: Configuration for a single parallel branch.
    
    For production, use LangGraph graph edges with WorkflowStateDict reducers.
    
    Attributes:
        name: Unique identifier for the branch
        node_fn: The node function to execute (state) -> state
        condition: Optional predicate to check if branch should run
        priority: Lower values = higher priority for resource allocation
        timeout: Optional timeout override for this branch
    """
    name: str
    node_fn: Callable[[WorkflowState], WorkflowState]
    condition: Optional[Callable[[WorkflowState], bool]] = None
    priority: int = 0
    timeout: Optional[int] = None


@dataclass
class ParallelFanOutConfig:
    """
    TEST-ONLY: Configuration for parallel fan-out execution.
    
    WARNING: This is for testing only. Production parallelism uses 
    build_parallel_graph() with static LangGraph edges.
    
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
            max_concurrent_branches=5,
            strategy=FanOutStrategy.STATIC,
        )
    
    Attributes:
        source_node: Node that triggers the fan-out
        branches: List of branch configurations
        sync_node: Node that merges branch results
        next_node: Node to execute after sync
        max_concurrent_branches: Limit concurrent branches (None = unlimited)
        strategy: Static or dynamic fan-out strategy
        timeout: Timeout for all branches (overrides global default)
    """
    source_node: str
    branches: List[ParallelBranchConfig]
    sync_node: str
    next_node: str
    max_concurrent_branches: Optional[int] = None
    strategy: FanOutStrategy = FanOutStrategy.STATIC
    timeout: Optional[int] = None


# Registry for fan-out configurations (TEST-ONLY)
_FANOUT_REGISTRY: Dict[str, ParallelFanOutConfig] = {}


def register_fanout_config(config: ParallelFanOutConfig) -> None:
    """
    TEST-ONLY: Register a fan-out configuration for a source node.
    
    Args:
        config: The fan-out configuration to register
    """
    _FANOUT_REGISTRY[config.source_node] = config
    logger.debug(f"Registered fan-out config for {config.source_node} with {len(config.branches)} branches")


def get_fanout_config(source_node: str) -> Optional[ParallelFanOutConfig]:
    """
    TEST-ONLY: Get the fan-out configuration for a source node.
    
    Args:
        source_node: The node to look up
        
    Returns:
        ParallelFanOutConfig if registered, None otherwise
    """
    return _FANOUT_REGISTRY.get(source_node)


def clear_fanout_registry() -> None:
    """TEST-ONLY: Clear all registered fan-out configurations."""
    global _FANOUT_REGISTRY
    _FANOUT_REGISTRY = {}


async def run_branches_with_semaphore(
    state: WorkflowState,
    branches: List[ParallelBranchConfig],
    max_concurrent: Optional[int] = None,
    timeout: Optional[int] = None,
) -> Dict[str, WorkflowState]:
    """
    TEST-ONLY: Run parallel branches with optional concurrency limiting.
    
    WARNING: This is an out-of-band executor that bypasses LangGraph.
    For production parallel execution, use build_parallel_graph() which
    leverages LangGraph's native fan-out with TypedDict reducers.
    
    Args:
        state: Input WorkflowState (will be deep copied for each branch)
        branches: List of branch configurations
        max_concurrent: Max concurrent branches (None = unlimited)
        timeout: Timeout in seconds (None = use PARALLEL_TIMEOUT)
        
    Returns:
        Dict mapping branch name to resulting state
        
    Raises:
        RuntimeError: If any branch fails or times out
    """
    if not branches:
        return {}
    
    effective_timeout = timeout or get_parallel_timeout()
    semaphore = asyncio.Semaphore(max_concurrent) if max_concurrent else None
    
    async def _run_branch(branch: ParallelBranchConfig) -> Tuple[str, WorkflowState]:
        """Execute a single branch with condition check and semaphore."""
        # Check condition
        if branch.condition and not branch.condition(state):
            logger.debug(f"Skipping branch '{branch.name}' (condition not met)")
            return branch.name, copy.deepcopy(state)
        
        # Deep copy state for this branch
        branch_state = copy.deepcopy(state)
        
        start = time.perf_counter()
        try:
            if semaphore:
                async with semaphore:
                    logger.debug(f"Running branch '{branch.name}' (with semaphore)")
                    result = branch.node_fn(branch_state)
            else:
                logger.debug(f"Running branch '{branch.name}'")
                result = branch.node_fn(branch_state)
            
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(f"Branch '{branch.name}' completed in {duration_ms:.1f}ms")
            
            return branch.name, result
            
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(f"Branch '{branch.name}' failed after {duration_ms:.1f}ms: {e}")
            raise RuntimeError(f"Branch '{branch.name}' failed: {e}") from e
    
    # Sort branches by priority
    sorted_branches = sorted(branches, key=lambda b: b.priority)
    
    # Run all branches concurrently
    tasks = [_run_branch(b) for b in sorted_branches]
    
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        logger.error(f"Parallel branches timed out after {effective_timeout}s")
        raise RuntimeError(
            f"Parallel execution timed out after {effective_timeout}s. "
            f"Consider increasing PARALLEL_TIMEOUT or reducing branch count."
        )
    
    # Process results
    output: Dict[str, WorkflowState] = {}
    errors: List[str] = []
    
    for result in results:
        if isinstance(result, Exception):
            errors.append(str(result))
        elif isinstance(result, tuple) and len(result) == 2:
            name, branch_state = result
            output[name] = branch_state
        else:
            errors.append(f"Unexpected result type: {type(result)}")
    
    if errors:
        error_summary = "; ".join(errors[:5])
        if len(errors) > 5:
            error_summary += f" (and {len(errors) - 5} more)"
        logger.error(f"Parallel branch errors: {error_summary}")
        raise RuntimeError(f"Parallel branches failed: {error_summary}")
    
    logger.info(f"All {len(output)} branches completed successfully")
    return output


def merge_parallel_states(
    base_state: WorkflowState,
    branch_states: Dict[str, WorkflowState],
    merge_rules: Optional[Dict[str, str]] = None,
) -> WorkflowState:
    """
    TEST-ONLY: Merge results from parallel branches back into base state.
    
    WARNING: This is a manual merge that may diverge from LangGraph reducer semantics.
    For production parallel merging, use WorkflowStateDict with Annotated reducers
    (see state_v2.py) which LangGraph applies automatically at fan-in points.
    
    Default merge strategies:
    - Lists: extend with unique items
    - Dicts: update (later branches override)
    - Scalars: last non-None wins
    
    Args:
        base_state: Original state before fan-out
        branch_states: Dict of branch name -> resulting state
        merge_rules: Optional dict mapping field names to strategies
        
    Returns:
        Merged WorkflowState
    """
    if not branch_states:
        return base_state
    
    # Start with a copy of base state
    merged = copy.deepcopy(base_state)
    
    # Merge each branch
    for branch_name, branch_state in branch_states.items():
        # Merge completed_steps (unique list)
        if branch_state.completed_steps:
            for step in branch_state.completed_steps:
                if step not in merged.completed_steps:
                    merged.completed_steps.append(step)
        
        # Merge node_timings (dict update)
        if hasattr(branch_state, 'node_timings') and branch_state.node_timings:
            if not hasattr(merged, 'node_timings'):
                merged.node_timings = {}
            merged.node_timings.update(branch_state.node_timings)
        
        # Merge errors (unique list)
        if branch_state.errors:
            for error in branch_state.errors:
                if error not in merged.errors:
                    merged.errors.append(error)
        
        # Merge warnings (unique list)
        if hasattr(branch_state, 'warnings') and branch_state.warnings:
            if not hasattr(merged, 'warnings'):
                merged.warnings = []
            for warning in branch_state.warnings:
                if warning not in merged.warnings:
                    merged.warnings.append(warning)
        
        # Branch-specific merges
        if branch_name == "embed_spec_chunks":
            if branch_state.spec_chunk_embeddings:
                merged.spec_chunk_embeddings = branch_state.spec_chunk_embeddings
            if hasattr(branch_state, 'spec_chunk_ids') and branch_state.spec_chunk_ids:
                merged.spec_chunk_ids = branch_state.spec_chunk_ids
            # Also copy doc_chunks, spec_documents, counts
            if hasattr(branch_state, 'doc_chunks') and branch_state.doc_chunks:
                merged.doc_chunks = branch_state.doc_chunks
            if hasattr(branch_state, 'spec_documents') and branch_state.spec_documents:
                merged.spec_documents = branch_state.spec_documents
            if hasattr(branch_state, 'chunk_count') and branch_state.chunk_count:
                merged.chunk_count = branch_state.chunk_count
            if hasattr(branch_state, 'embedding_count') and branch_state.embedding_count:
                merged.embedding_count = branch_state.embedding_count
        
        if branch_name == "understand_task":
            if branch_state.integration_task:
                merged.integration_task = branch_state.integration_task
    
    return merged


def create_dynamic_sync_node(
    branches: List[str],
    name: Optional[str] = None,
) -> Callable[[WorkflowState], WorkflowState]:
    """
    TEST-ONLY: Create a sync node that merges results from N parallel branches.
    
    This is a factory function that generates a merge node specific
    to the branches being synchronized.
    
    WARNING: For production, use LangGraph's native fan-in with WorkflowStateDict reducers.
    
    Args:
        branches: List of branch names being synchronized
        name: Optional custom name for the sync node
        
    Returns:
        A node function that merges parallel branch results
    """
    sync_name = name or f"sync_{'-'.join(branches)}"
    
    def _sync_node(state: WorkflowState) -> WorkflowState:
        start = time.perf_counter()
        
        # Log sync
        branch_list = ", ".join(branches)
        logger.info(f"Syncing {len(branches)} parallel branches: [{branch_list}]")
        
        # Record timing
        duration_ms = (time.perf_counter() - start) * 1000
        if hasattr(state, 'node_timings'):
            state.node_timings[sync_name] = duration_ms
        
        # Mark sync completed
        if sync_name not in state.completed_steps:
            state.completed_steps.append(sync_name)
        
        return state
    
    # Set function name for debugging
    _sync_node.__name__ = sync_name
    
    return _sync_node


# Default max branches for testing (None = unlimited)
DEFAULT_MAX_BRANCHES = None


def get_max_branches() -> Optional[int]:
    """
    TEST-ONLY: Get the maximum number of concurrent branches from environment.
    
    For production, this should be integrated with the profiles/settings system.
    
    Returns:
        Max branches limit or None for unlimited
    """
    import os
    value = os.getenv("PARALLEL_MAX_BRANCHES", "")
    if not value:
        return DEFAULT_MAX_BRANCHES
    try:
        limit = int(value)
        return limit if limit > 0 else None
    except ValueError:
        return DEFAULT_MAX_BRANCHES
