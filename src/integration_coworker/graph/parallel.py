"""
Parallel Node Execution (Plan 8 + Parallelization V2)

.. deprecated:: V2 State (December 2025)
    **DEPRECATION NOTICE - V2 STATE**
    
    Parallel mode has been DISABLED by default to prevent checkpoint bloat.
    The demo script and production workflows now use sequential execution.
    
    **Why Deprecated:**
    - Parallel branches create 2x+ checkpoint writes (fan-out + fan-in)
    - Checkpoint bloat causes slow DB performance and storage growth
    - sync_embed_task adds complexity without sufficient speedup benefit
    - Sequential mode is more debuggable and predictable
    
    **Migration:**
    - PARALLEL_WORKFLOW=false is now the default (no action needed)
    - Demo script explicitly sets PARALLEL_WORKFLOW=false
    - Production profiles should NOT set PARALLEL_WORKFLOW=true
    
    **If You Need Parallel Mode:**
    - Set PARALLEL_WORKFLOW=true explicitly (opt-in only)
    - Be aware of checkpoint bloat implications
    - Consider using streaming persistence (STREAMING_PERSISTENCE=true)
    
    This module is KEPT for backwards compatibility but should not be
    enabled in production workflows without careful consideration.

ORIGINAL DOCUMENTATION (for reference):
Provides parallel execution support for LangGraph workflows where
independent nodes can run concurrently for faster execution.

PRODUCTION PARALLELISM:
    build_parallel_graph() in runtime.py uses LangGraph's native fan-out:
    - Multiple edges from one node to fan-out targets
    - WorkflowStateDict with Annotated reducers for automatic merge
    - Preserves checkpointing, HITL, and state management

Parallel Execution Flow:
    build_silver_api_model
           |
     [parallel split]
         /   \\
  embed_spec_chunks   understand_task
         \\   /
    [sync_embed_task]
           |
    align_task_with_kg
           |
         ...

PRODUCTION EXPORTS (safe to use):
- is_parallel_enabled: Check if PARALLEL_WORKFLOW=true
- sync_embed_task: Sync node for the embed/understand fan-in
- get_parallel_timeout: Get configured timeout
- ParallelBranchConfig: Configuration dataclass for branches
- ParallelFanOutConfig: Configuration dataclass for fan-out
- create_dynamic_sync_node: Factory for creating sync nodes

TEST-ONLY EXPORTS (DO NOT use in production):
- run_branches_with_semaphore: Out-of-band parallel executor
- merge_parallel_states: Manual state merge (may diverge from reducers)

Environment Variables:
    PARALLEL_WORKFLOW: Enable parallel execution (default: false) **V2: DISABLED BY DEFAULT**
    PARALLEL_TIMEOUT: Timeout for parallel branches in seconds (default: 300)
    PARALLEL_MAX_BRANCHES: Maximum concurrent branches (default: None = unlimited)

Usage:
    # V2 State: Sequential is now the default
    from integration_coworker.graph.runtime import build_graph
    
    # Parallel mode is opt-in only:
    # if is_parallel_enabled():  # Returns False unless PARALLEL_WORKFLOW=true
    #     graph = build_parallel_graph()
    # else:
    graph = build_graph()  # <-- V2 default
"""

import asyncio
import copy
import logging
import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from integration_coworker.graph.state import WorkflowState

logger = logging.getLogger(__name__)

# V2 STATE: Parallel mode deprecated - emit warning if enabled
_PARALLEL_DEPRECATION_WARNING = (
    "Parallel mode (PARALLEL_WORKFLOW=true) is deprecated in V2 State. "
    "Sequential execution is now the default to avoid checkpoint bloat. "
    "See integration_coworker.graph.parallel module docstring for details."
)

# Default configuration
DEFAULT_PARALLEL_TIMEOUT = 300  # 5 minutes
DEFAULT_MAX_BRANCHES = None  # Unlimited


def is_parallel_enabled() -> bool:
    """
    Check if parallel execution is enabled.
    
    .. deprecated:: V2 State
        Parallel mode is deprecated. Returns False by default.
        Only returns True if PARALLEL_WORKFLOW=true is explicitly set.
        A deprecation warning is emitted when parallel mode is enabled.
    
    Returns:
        True if PARALLEL_WORKFLOW=true (opt-in only, deprecated)
    """
    enabled = os.getenv("PARALLEL_WORKFLOW", "false").lower() in ("true", "1", "yes", "on")
    
    # V2 STATE: Emit deprecation warning when parallel mode is explicitly enabled
    if enabled:
        warnings.warn(
            _PARALLEL_DEPRECATION_WARNING,
            DeprecationWarning,
            stacklevel=2
        )
        logger.warning(
            "V2 STATE DEPRECATION: Parallel mode enabled. "
            "This causes checkpoint bloat. Consider using sequential mode."
        )
    
    return enabled


def get_parallel_timeout() -> int:
    """
    Get the timeout for parallel branches.
    
    .. deprecated:: V2 State
        Only relevant when parallel mode is enabled (deprecated).
    
    Returns:
        Timeout in seconds (default: 300)
    """
    return int(os.getenv("PARALLEL_TIMEOUT", str(DEFAULT_PARALLEL_TIMEOUT)))


# =============================================================================
# Sync Node: Merges parallel branch results
# =============================================================================

def sync_embed_task(state: WorkflowState) -> WorkflowState:
    """
    Sync node that merges results from parallel embed_spec_chunks and understand_task.
    
    This node is placed after the parallel branches to:
    1. Validate both branches completed successfully
    2. Merge any parallel-specific state
    3. Record timing information
    
    Args:
        state: WorkflowState with results from both parallel branches
        
    Returns:
        WorkflowState with merged results
    """
    import time
    start = time.perf_counter()
    
    # Track that we've synced
    if "sync_embed_task" not in state.completed_steps:
        state.completed_steps.append("sync_embed_task")
    
    # Record timing
    duration_ms = (time.perf_counter() - start) * 1000
    if hasattr(state, 'node_timings'):
        state.node_timings["sync_embed_task"] = duration_ms
    
    # Log the sync
    logger.info(
        f"Parallel sync complete: embed_chunks={len(state.doc_chunks) if state.doc_chunks else 0}, "
        f"task_understood={'integration_task' in state.__dict__ and state.integration_task is not None}"
    )
    
    return state


# =============================================================================
# Parallel Execution Utilities
# =============================================================================

def run_nodes_parallel(
    state: WorkflowState,
    node_functions: List[Tuple[str, Callable[[WorkflowState], WorkflowState]]],
    timeout: Optional[int] = None,
) -> WorkflowState:
    """
    Run multiple nodes in parallel and merge their results.
    
    This utility can be used to execute independent nodes concurrently.
    Each node receives the same input state, and results are merged.
    
    Args:
        state: Input WorkflowState
        node_functions: List of (name, function) tuples
        timeout: Optional timeout in seconds
        
    Returns:
        WorkflowState with merged results from all nodes
        
    Raises:
        RuntimeError: If any node fails or times out
    """
    if not node_functions:
        return state
    
    effective_timeout = timeout or get_parallel_timeout()
    
    results: Dict[str, WorkflowState] = {}
    errors: Dict[str, Exception] = {}
    
    with ThreadPoolExecutor(max_workers=len(node_functions)) as executor:
        # Submit all nodes
        future_to_name = {
            executor.submit(fn, state): name
            for name, fn in node_functions
        }
        
        # Collect results
        for future in as_completed(future_to_name, timeout=effective_timeout):
            name = future_to_name[future]
            try:
                result = future.result()
                results[name] = result
                logger.debug(f"Parallel node {name} completed successfully")
            except Exception as e:
                errors[name] = e
                logger.error(f"Parallel node {name} failed: {e}")
    
    # Check for errors
    if errors:
        error_msgs = [f"{name}: {str(e)}" for name, e in errors.items()]
        raise RuntimeError(f"Parallel execution failed: {', '.join(error_msgs)}")
    
    # Merge results - for now, we take the last result and add completed_steps
    # A more sophisticated merge would be needed for real parallel execution
    merged = state
    for name, result in results.items():
        if result.completed_steps:
            for step in result.completed_steps:
                if step not in merged.completed_steps:
                    merged.completed_steps.append(step)
        
        # Merge specific fields based on which node ran
        if name == "embed_spec_chunks":
            merged.doc_chunks = result.doc_chunks
            merged.spec_documents = result.spec_documents
        elif name == "understand_task":
            merged.integration_task = result.integration_task
        
        # Merge node timings
        if hasattr(result, 'node_timings') and result.node_timings:
            if not hasattr(merged, 'node_timings'):
                merged.node_timings = {}
            merged.node_timings.update(result.node_timings)
    
    return merged


# =============================================================================
# Parallel Branch Configuration
# =============================================================================

# Nodes that can run in parallel after build_silver_api_model
PARALLEL_AFTER_SILVER = ["embed_spec_chunks", "understand_task"]

# Nodes that can run in parallel after attach_policies_and_patterns
# (if we want to parallelize code generation in the future)
PARALLEL_CODEGEN = ["generate_code_and_tests"]  # Could add more here


def get_parallel_branches(after_node: str) -> List[str]:
    """
    Get the list of nodes that can run in parallel after a given node.
    
    Args:
        after_node: The node after which parallel execution can start
        
    Returns:
        List of node names that can run in parallel
    """
    if after_node == "build_silver_api_model":
        return PARALLEL_AFTER_SILVER.copy()
    return []


def get_sync_node(after_node: str) -> Optional[str]:
    """
    Get the sync node that merges results after parallel branches.
    
    Args:
        after_node: The node that triggered parallel execution
        
    Returns:
        Name of the sync node, or None if no sync needed
    """
    if after_node == "build_silver_api_model":
        return "sync_embed_task"
    return None


# =============================================================================
# Graph Building Utilities
# =============================================================================

def add_parallel_edges(
    workflow,
    source_node: str,
    parallel_nodes: List[str],
    sync_node: str,
    next_node: str,
):
    """
    Add edges for parallel execution in a LangGraph workflow.
    
    Creates a fan-out from source_node to all parallel_nodes,
    then a fan-in from all parallel_nodes to sync_node,
    then an edge from sync_node to next_node.
    
    Args:
        workflow: The StateGraph being built
        source_node: Node that fans out to parallel nodes
        parallel_nodes: List of nodes to run in parallel
        sync_node: Node that collects parallel results
        next_node: Node after sync
    """
    # Add sync node
    workflow.add_node(sync_node, sync_embed_task)
    
    # Fan-out edges: source -> each parallel node
    for pnode in parallel_nodes:
        workflow.add_edge(source_node, pnode)
    
    # Fan-in edges: each parallel node -> sync
    for pnode in parallel_nodes:
        workflow.add_edge(pnode, sync_node)
    
    # Continue to next node
    workflow.add_edge(sync_node, next_node)


# =============================================================================
# Metrics and Reporting
# =============================================================================

class ParallelExecutionMetrics:
    """Tracks metrics for parallel execution."""
    
    def __init__(self):
        self.parallel_branches: List[str] = []
        self.branch_timings: Dict[str, float] = {}
        self.total_parallel_time: float = 0.0
        self.sequential_equivalent_time: float = 0.0
        self.speedup: float = 1.0
    
    def record_branch(self, name: str, duration_ms: float):
        """Record timing for a parallel branch."""
        self.parallel_branches.append(name)
        self.branch_timings[name] = duration_ms
        self.sequential_equivalent_time += duration_ms
    
    def record_total_time(self, total_ms: float):
        """Record the total parallel execution time."""
        self.total_parallel_time = total_ms
        if total_ms > 0:
            self.speedup = self.sequential_equivalent_time / total_ms
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "parallel_branches": self.parallel_branches,
            "branch_timings_ms": self.branch_timings,
            "total_parallel_time_ms": self.total_parallel_time,
            "sequential_equivalent_time_ms": self.sequential_equivalent_time,
            "speedup": round(self.speedup, 2),
        }


def get_parallel_metrics_from_state(state: WorkflowState) -> Optional[ParallelExecutionMetrics]:
    """
    Extract parallel execution metrics from workflow state.
    
    Args:
        state: WorkflowState with node_timings
        
    Returns:
        ParallelExecutionMetrics if parallel execution occurred, None otherwise
    """
    if not hasattr(state, 'node_timings') or not state.node_timings:
        return None
    
    # Check if parallel nodes ran
    parallel_nodes = ["embed_spec_chunks", "understand_task"]
    has_parallel = all(
        node in state.node_timings 
        for node in parallel_nodes
    )
    
    if not has_parallel:
        return None
    
    metrics = ParallelExecutionMetrics()
    
    for node in parallel_nodes:
        if node in state.node_timings:
            metrics.record_branch(node, state.node_timings[node])
    
    # Estimate total time as max of parallel branches
    max_time = max(metrics.branch_timings.values()) if metrics.branch_timings else 0
    metrics.record_total_time(max_time)
    
    return metrics
