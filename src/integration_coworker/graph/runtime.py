import asyncio
import functools
import inspect
import logging
import os
import time
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from typing import Dict, Optional, Callable, List, Union
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.state_v2 import (
    WorkflowStateDict,
    dataclass_to_dict,
    dict_to_dataclass,
)

# NOTE: Don't import via `from integration_coworker.graph.nodes import (...)`.
# The `graph/nodes/` directory is currently a namespace package (no __init__.py).
# Importing `integration_coworker.graph.nodes` can resolve to an empty namespace
# module depending on environment/tooling, which breaks node resolution.
# Import node modules explicitly instead.
from integration_coworker.graph.nodes import plan_run
from integration_coworker.graph.nodes import ingest_spec
from integration_coworker.graph.nodes import detect_and_parse_spec
from integration_coworker.graph.nodes import build_silver_api_model
from integration_coworker.graph.nodes import build_silver_file_model
from integration_coworker.graph.nodes import embed_spec_chunks
from integration_coworker.graph.nodes import understand_task
from integration_coworker.graph.nodes import align_task_with_kg
from integration_coworker.graph.nodes import plan_integration_flow
from integration_coworker.graph.nodes import attach_policies_and_patterns
from integration_coworker.graph.nodes import attach_repo_context
from integration_coworker.graph.nodes import generate_code_and_tests
from integration_coworker.graph.nodes import analyze_repo_layout
from integration_coworker.graph.nodes import apply_repo_integration_changes
from integration_coworker.graph.nodes import validate_integration_design
from integration_coworker.graph.nodes import persist_results
from integration_coworker.graph.nodes import build_report
from integration_coworker.graph.nodes import handle_error
from integration_coworker.graph.nodes import persist_silver_checkpoint
from integration_coworker.graph.nodes import persist_gold_checkpoint
from integration_coworker.graph.nodes import persist_run_outcome
from integration_coworker.graph.nodes import persist_kg_learning
from integration_coworker.llm.client import set_run_context, clear_run_context

logger = logging.getLogger(__name__)


# =============================================================================
# LangGraph Native Checkpointing (Bug #61 Fix - Solution B)
# =============================================================================
# Uses LangGraph's built-in checkpointer for native resume support.
# PostgresSaver for Postgres, SqliteSaver for SQLite fallback.
# =============================================================================

_checkpointer_instance: Optional[BaseCheckpointSaver] = None
_checkpointer_connection = None  # Keep connection alive or hold async aexit

# Async SQLite saver objects (and their internal locks) are bound to the event
# loop they were created in. Pytest creates/destroys event loops across tests,
# so we must not reuse a cached AsyncSqliteSaver across loops.
_sqlite_checkpointer_cache_enabled = False


def _default_db_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://integration:integration@localhost:5432/integration_coworker",
    )


def _sqlite_checkpoint_path() -> str:
    data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "langgraph_checkpoints.db")


@contextmanager
def checkpointer_context() -> BaseCheckpointSaver:
    """Sync checkpointer context that always yields a saver instance."""
    from integration_coworker.persistence.db import get_engine_type

    engine = get_engine_type()
    if engine == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(_default_db_url()) as saver:
            saver.setup()
            yield saver
        return

    # SQLite sync saver
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver

    sqlite_path = _sqlite_checkpoint_path()
    conn = sqlite3.connect(sqlite_path)
    try:
        saver = SqliteSaver(conn)
        saver.setup()
        yield saver
    finally:
        conn.close()


@asynccontextmanager
async def async_checkpointer_context() -> BaseCheckpointSaver:
    """Async checkpointer context that yields a saver instance (not a context manager)."""
    from integration_coworker.persistence.db import get_engine_type

    engine = get_engine_type()
    if engine == "postgres":
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(_default_db_url()) as saver:
            await saver.setup()
            yield saver
        return

    # SQLite async saver
    import aiosqlite  # noqa: WPS433
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    sqlite_path = _sqlite_checkpoint_path()
    conn = await aiosqlite.connect(sqlite_path)
    try:
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        yield saver
    finally:
        await conn.close()


async def get_checkpointer() -> BaseCheckpointSaver:
    """
    Get or create a LangGraph checkpointer based on configured DB engine.
    
    Returns:
        A checkpointer instance or context manager depending on backend.
        
    Note:
        V3.0: Returns async context manager for AsyncPostgresSaver.
        The caller must use 'async with' to get the actual checkpointer.
    """
    global _checkpointer_instance, _checkpointer_connection

    if _checkpointer_instance is not None:
        return _checkpointer_instance

    from integration_coworker.persistence.db import get_engine_type
    engine = get_engine_type()

    if engine == "postgres":
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            cm = AsyncPostgresSaver.from_conn_string(_default_db_url())
            saver = await cm.__aenter__()
            await saver.setup()
            _checkpointer_instance = saver
            _checkpointer_connection = cm  # store context manager for cleanup
            logger.info("Initialized AsyncPostgresSaver for LangGraph checkpointing")
            return saver
        except ImportError as e:
            logger.warning(f"langgraph-checkpoint-postgres not installed: {e}, falling back to SQLite")
        except Exception as e:
            logger.warning(f"Failed to initialize AsyncPostgresSaver: {e}, falling back to SQLite")

    try:
        import aiosqlite  # noqa: F401
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "SQLite async checkpointing requires 'aiosqlite' and an async-capable LangGraph saver. "
            "Install aiosqlite or configure Postgres checkpointing."
        ) from e

    sqlite_path = _sqlite_checkpoint_path()

    if not _sqlite_checkpointer_cache_enabled:
        conn = await aiosqlite.connect(sqlite_path)
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        logger.info(f"Initialized AsyncSqliteSaver (non-cached) at {sqlite_path} for LangGraph checkpointing")
        return saver

    _checkpointer_connection = await aiosqlite.connect(sqlite_path)
    _checkpointer_instance = AsyncSqliteSaver(_checkpointer_connection)
    await _checkpointer_instance.setup()

    logger.info(f"Initialized AsyncSqliteSaver (cached) at {sqlite_path} for LangGraph checkpointing")
    return _checkpointer_instance


def reset_checkpointer() -> None:
    """Reset the checkpointer singleton (useful for testing)."""
    global _checkpointer_instance, _checkpointer_connection
    if _checkpointer_connection is not None:
        try:
            # If it's an async context manager from postgres saver
            aexit = getattr(_checkpointer_connection, "__aexit__", None)
            if aexit:
                asyncio.get_event_loop().create_task(aexit(None, None, None))
            else:
                close = getattr(_checkpointer_connection, "close", None)
                if close is not None:
                    close()
        except Exception:
            pass
    _checkpointer_instance = None
    _checkpointer_connection = None


# =============================================================================
# Checkpoint Wrapper (V2 Implementation Plan Section 3.5)
# =============================================================================

def _wrap_node_with_checkpoint(node_func: Callable) -> Callable:
    """
    Decorator that saves checkpoint after successful node execution.
    
    Per V2 Implementation Plan Section 3.5, this enables:
    - Resume capability from any checkpointed node
    - True skip with dependency analysis
    - Recovery from process crashes
    """
    @functools.wraps(node_func)
    def wrapper(state: WorkflowState, *args, **kwargs) -> WorkflowState:
        # Execute node
        new_state = node_func(state, *args, **kwargs)
        
        # Save checkpoint if we have a run_id
        if new_state.run_id:
            try:
                from integration_coworker.persistence.checkpoints import save_checkpoint
                save_checkpoint(
                    run_id=new_state.run_id,
                    node_name=node_func.__name__,
                    state=new_state,
                )
            except Exception as e:
                # Log but don't fail the workflow
                logger.warning(f"Failed to save checkpoint for {node_func.__name__}: {e}")
        
        return new_state
    
    return wrapper


# =============================================================================
# Node Metadata Catalog
# =============================================================================
# Provides self-describing metadata for all non-LLM nodes.
# This makes "0.00s" nodes in LangSmith traces clearly understandable.
#
# Each entry contains:
#   - category: "pure-python" | "db-write" | "api-call" | "llm"
#   - responsibility: Human-readable description of what the node does
# =============================================================================

NODE_METADATA: Dict[str, Dict[str, str]] = {
    # Pure Python nodes - fast computation, no I/O
    "plan_run": {
        "category": "pure-python",
        "responsibility": "Generate run_id, infer provider_code from spec, validate inputs",
    },
    "ingest_spec": {
        "category": "pure-python",
        "responsibility": "Fetch spec content from URLs/files, normalize to raw text",
    },
    "detect_and_parse_spec": {
        "category": "pure-python",
        "responsibility": "Detect spec format (OpenAPI/AsyncAPI), parse to structured dict",
    },
    "build_silver_api_model": {
        "category": "pure-python",
        "responsibility": "Extract endpoints, schemas, entities from parsed spec (Silver model)",
    },
    "attach_policies_and_patterns": {
        "category": "pure-python",
        "responsibility": "Infer auth, rate-limit, retry policies from spec security schemes",
    },
    "attach_repo_context": {
        "category": "pure-python",
        "responsibility": "Detect repo profile (Python/Node/etc), build RepoProfile for codegen",
    },
    "align_task_with_kg": {
        "category": "pure-python",
        "responsibility": "Match task to existing KG templates if available (may skip LLM)",
    },
    "analyze_repo_layout": {
        "category": "pure-python",
        "responsibility": "Analyze target repo structure for marker-based code insertion",
    },
    "apply_repo_integration_changes": {
        "category": "pure-python",
        "responsibility": "Insert generated code at marker locations in target repo",
    },
    "validate_integration_design": {
        "category": "pure-python",
        "responsibility": "Validate flow structure, endpoint bindings, syntax check code artifacts",
    },
    "handle_error": {
        "category": "pure-python",
        "responsibility": "Record error state, set failed flag, preserve partial results",
    },

    # DB checkpoint nodes - write to database
    "persist_silver_checkpoint": {
        "category": "db-write",
        "responsibility": "Persist Silver API model (endpoints, schemas, entities) to database",
    },
    "persist_gold_checkpoint": {
        "category": "db-write",
        "responsibility": "Persist Gold integration (task, workflow, code artifacts) to database",
    },
    "persist_run_outcome": {
        "category": "db-write",
        "responsibility": "Persist final run status, metrics, errors to database",
    },
    "persist_kg_learning": {
        "category": "db-write",
        "responsibility": "Persist workflow template to knowledge graph for future reuse",
    },
    "persist_results": {
        "category": "db-write",
        "responsibility": "Legacy persistence node (delegates to checkpoints if needed)",
    },

    # API/Embedding nodes - external API calls
    "embed_spec_chunks": {
        "category": "api-call",
        "responsibility": "Generate OpenAI embeddings for spec chunks (for RAG retrieval)",
    },

    # LLM nodes - tracked by LangSmith automatically
    "understand_task": {
        "category": "llm",
        "responsibility": "LLM: Parse task description, extract intent, identify relevant endpoints",
    },
    "plan_integration_flow": {
        "category": "llm",
        "responsibility": "LLM: Design workflow graph with nodes/edges for task implementation",
    },
    "generate_code_and_tests": {
        "category": "llm",
        "responsibility": "LLM: Generate client code, workflow code, and unit tests",
    },
    "build_report": {
        "category": "llm",
        "responsibility": "LLM: Generate executive summary + render full run report",
    },
}


# =============================================================================
# Node Dependency Graph (V2.1 Section 13.2: True Skip per ADR-0009)
# =============================================================================
# Maps each node to the nodes that MUST complete before it can run.
# Used for dependency-aware skip: if A is skipped, all nodes depending on A
# must also be skipped.

NODE_DEPENDENCIES: Dict[str, List[str]] = {
    # Phase 1: Spec ingestion (linear chain)
    "plan_run": [],
    "ingest_spec": ["plan_run"],
    "detect_and_parse_spec": ["ingest_spec"],
    "build_silver_api_model": ["detect_and_parse_spec"],
    "build_silver_file_model": ["build_silver_api_model"],
    "embed_spec_chunks": ["build_silver_file_model"],
    "persist_silver_checkpoint": ["embed_spec_chunks"],
    
    # Phase 2: Task understanding (depends on Silver model)
    "understand_task": ["persist_silver_checkpoint"],
    "align_task_with_kg": ["understand_task"],
    "plan_integration_flow": ["align_task_with_kg"],
    "attach_policies_and_patterns": ["plan_integration_flow"],
    
    # Phase 3: Code generation (depends on flow)
    "generate_code_and_tests": ["attach_policies_and_patterns"],
    "persist_gold_checkpoint": ["generate_code_and_tests"],
    "persist_kg_learning": ["persist_gold_checkpoint"],
    
    # Phase 4: Repo integration (optional, conditional)
    "attach_repo_context": ["persist_kg_learning"],  # Only if use_repo
    "analyze_repo_layout": ["attach_repo_context"],
    "apply_repo_integration_changes": ["analyze_repo_layout"],
    
    # Phase 5: Validation and reporting
    "validate_integration_design": ["persist_kg_learning"],  # OR apply_repo_integration_changes
    "build_report": ["validate_integration_design"],
    "persist_run_outcome": ["build_report"],
    
    # Error handling
    "handle_error": [],  # Can run anytime
}


def get_dependent_nodes(node_name: str) -> List[str]:
    """
    Get all nodes that transitively depend on the given node.
    
    If node X is skipped, all nodes returned by this function must also be skipped.
    
    Args:
        node_name: The node being skipped
        
    Returns:
        List of node names that depend on the skipped node (in execution order)
    """
    dependents: List[str] = []
    
    for name, deps in NODE_DEPENDENCIES.items():
        if node_name in deps:
            dependents.append(name)
            # Recursively get nodes that depend on this dependent
            dependents.extend(get_dependent_nodes(name))
    
    # Remove duplicates while preserving order
    seen = set()
    result: List[str] = []
    for name in dependents:
        if name not in seen:
            seen.add(name)
            result.append(name)
    
    return result


def get_skip_cascade(skipped_node: str) -> List[str]:
    """
    Get the full list of nodes to skip when a given node is skipped.
    
    Includes the original node plus all transitively dependent nodes.
    
    Args:
        skipped_node: The node the user wants to skip
        
    Returns:
        Complete list of nodes to skip, in execution order
    """
    cascade = [skipped_node]
    cascade.extend(get_dependent_nodes(skipped_node))
    
    # Sort by execution order
    ordered_cascade: List[str] = []
    for node in WORKFLOW_NODE_ORDER:
        if node in cascade:
            ordered_cascade.append(node)
    
    return ordered_cascade


def has_skipped_dependency(state, node_name: str) -> bool:
    """
    Check if any dependency of a node was skipped.
    
    Used by nodes to determine if they should auto-skip.
    
    Args:
        state: WorkflowState with skipped_nodes list
        node_name: The node to check dependencies for
        
    Returns:
        True if any dependency was skipped
    """
    deps = set(NODE_DEPENDENCIES.get(node_name, []))
    skipped = set(getattr(state, 'skipped_nodes', []))
    return bool(deps & skipped)


def timed_node(fn: Callable, metadata: Optional[Dict[str, str]] = None, enable_checkpoint: bool = True):
    """
    Decorator that records node execution time and metadata into state.
    
    This provides visibility into non-LLM nodes that may show "0.00s" in 
    LangSmith but actually do meaningful work. The timing is recorded in 
    milliseconds and surfaced in the report.
    
    When LANGCHAIN_TRACING_V2=true, also uses LangSmith's @traceable decorator
    to add rich metadata to the trace, including:
    - node_type: "pure-python" | "db-write" | "api-call" | "llm"
    - responsibility: Human-readable description
    - duration_ms: Internal timing measurement
    
    Per V2 Implementation Plan Section 3.5, also saves checkpoints after
    successful node execution to enable recovery.
    
    Bug #61 Fix: Nodes now check completed_steps to skip execution during resume.
    If a node is already in state.completed_steps, it returns state unchanged.
    
    Args:
        fn: The node function to wrap
        metadata: Optional override metadata dict with 'category' and 'responsibility'
        enable_checkpoint: Whether to save checkpoints after this node (default True)
    """
    # Get metadata from catalog or use provided override
    node_name = fn.__name__
    node_meta = metadata or NODE_METADATA.get(node_name, {
        "category": "unknown",
        "responsibility": f"Node: {node_name}",
    })

    # Check if LangSmith tracing is enabled
    tracing_enabled = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
    
    def _should_skip_node(state: WorkflowState) -> bool:
        """Check if this node should be skipped (already completed during resume)."""
        if hasattr(state, 'completed_steps') and state.completed_steps:
            return node_name in state.completed_steps
        return False
    
    def _save_checkpoint_if_enabled(result: WorkflowState):
        """Save checkpoint after node execution if enabled and run_id exists.
        
        Skips checkpoint saving in dry_run mode to avoid FK constraint violations
        (run_checkpoints references run_status, which isn't created in dry_run).
        """
        # Skip in dry_run mode - no run_status record exists
        is_dry_run = getattr(result.options, 'dry_run', False) if result.options else False
        if is_dry_run:
            return
            
        if enable_checkpoint and result.run_id:
            try:
                from integration_coworker.persistence.checkpoints import save_checkpoint
                save_checkpoint(
                    run_id=result.run_id,
                    node_name=node_name,
                    state=result,
                )
            except Exception as e:
                # Log but don't fail the workflow
                logger.warning(f"Failed to save checkpoint for {node_name}: {e}")

    # Check if the function is async
    is_async = asyncio.iscoroutinefunction(fn)

    if tracing_enabled:
        try:
            from langsmith import traceable

            if is_async:
                @traceable(
                    name=node_name,
                    run_type="chain",
                    metadata={
                        "node_type": node_meta.get("category", "unknown"),
                        "responsibility": node_meta.get("responsibility", ""),
                    },
                    tags=[
                        f"node_type:{node_meta.get('category', 'unknown')}",
                        "non-llm-node",
                    ],
                )
                @functools.wraps(fn)
                async def traced_async_wrapper(state: WorkflowState, *args, **kwargs):
                    # Bug #61 Fix: Skip if node already completed during resume
                    if _should_skip_node(state):
                        logger.debug(f"Skipping {node_name} - already completed during previous run")
                        return state
                    
                    # Filter out 'config' that LangGraph may pass
                    kwargs.pop('config', None)
                    
                    start = time.perf_counter()
                    result = await fn(state)  # Node functions only take state
                    if inspect.isawaitable(result):
                        result = await result
                    duration_ms = (time.perf_counter() - start) * 1000

                    # Record into result state's timings dict
                    if hasattr(result, 'node_timings'):
                        result.node_timings[node_name] = duration_ms

                    # Log for visibility
                    logger.debug(
                        f"Node {node_name} completed in {duration_ms:.2f}ms "
                        f"[{node_meta.get('category', 'unknown')}]"
                    )
                    
                    # Save checkpoint after successful execution
                    _save_checkpoint_if_enabled(result)

                    return result

                return traced_async_wrapper
            else:
                @traceable(
                    name=node_name,
                    run_type="chain",
                    metadata={
                        "node_type": node_meta.get("category", "unknown"),
                        "responsibility": node_meta.get("responsibility", ""),
                    },
                    tags=[
                        f"node_type:{node_meta.get('category', 'unknown')}",
                        "non-llm-node",
                    ],
                )
                @functools.wraps(fn)
                def traced_wrapper(state: WorkflowState, *args, **kwargs):
                    # Bug #61 Fix: Skip if node already completed during resume
                    if _should_skip_node(state):
                        logger.debug(f"Skipping {node_name} - already completed during previous run")
                        return state
                    
                    # Filter out 'config' that LangGraph may pass
                    kwargs.pop('config', None)
                    
                    start = time.perf_counter()
                    result = fn(state)  # Node functions only take state
                    duration_ms = (time.perf_counter() - start) * 1000

                    # Record into result state's timings dict
                    if hasattr(result, 'node_timings'):
                        result.node_timings[node_name] = duration_ms

                    # Log for visibility
                    logger.debug(
                        f"Node {node_name} completed in {duration_ms:.2f}ms "
                        f"[{node_meta.get('category', 'unknown')}]"
                    )
                    
                    # Save checkpoint after successful execution
                    _save_checkpoint_if_enabled(result)

                    return result

                return traced_wrapper

        except ImportError:
            logger.debug("langsmith not available, using basic timing wrapper")

    # Fallback: basic timing without LangSmith traceable
    if is_async:
        @functools.wraps(fn)
        async def async_wrapper(state: WorkflowState, *args, **kwargs):
            # Bug #61 Fix: Skip if node already completed during resume
            if _should_skip_node(state):
                logger.debug(f"Skipping {node_name} - already completed during previous run")
                return state
            
            # Filter out 'config' that LangGraph may pass
            kwargs.pop('config', None)
            
            start = time.perf_counter()
            result = await fn(state)  # Node functions only take state
            if inspect.isawaitable(result):
                result = await result
            duration_ms = (time.perf_counter() - start) * 1000

            # Record into result state's timings dict
            if hasattr(result, 'node_timings'):
                result.node_timings[node_name] = duration_ms
            
            # Save checkpoint after successful execution
            _save_checkpoint_if_enabled(result)

            return result

        return async_wrapper
    else:
        @functools.wraps(fn)
        def wrapper(state: WorkflowState, *args, **kwargs):
            # Bug #61 Fix: Skip if node already completed during resume
            if _should_skip_node(state):
                logger.debug(f"Skipping {node_name} - already completed during previous run")
                return state
            
            # Filter out 'config' that LangGraph may pass
            kwargs.pop('config', None)
            
            start = time.perf_counter()
            result = fn(state)  # Node functions only take state
            duration_ms = (time.perf_counter() - start) * 1000

            # Record into result state's timings dict
            if hasattr(result, 'node_timings'):
                result.node_timings[node_name] = duration_ms
            
            # Save checkpoint after successful execution
            _save_checkpoint_if_enabled(result)

            return result

        return wrapper

def build_graph(checkpointer: Optional[BaseCheckpointSaver] = None):
    """
    Build the LangGraph workflow.
    
    Per design doc Section 5.4, the graph has three checkpoint nodes:
    - persist_silver_checkpoint: After build_silver_api_model + embed_spec_chunks
    - persist_gold_checkpoint: After generate_code_and_tests
    - persist_run_outcome: After build_report (final status and metrics)
    
    Non-LLM nodes are wrapped with timed_node() to record execution time
    in state.node_timings for observability (since LangSmith may show "0.00s").
    
    Args:
        checkpointer: Optional LangGraph checkpointer for native resume support.
                      If provided, enables automatic state persistence after each node.
    """
    workflow = StateGraph(WorkflowState)

    # Add nodes - wrap non-LLM nodes with timed_node for internal timing
    # These nodes may show "0.00s" in LangSmith but do meaningful work
    workflow.add_node("plan_run", timed_node(plan_run.plan_run))
    workflow.add_node("ingest_spec", timed_node(ingest_spec.ingest_spec))
    workflow.add_node("detect_and_parse_spec", timed_node(detect_and_parse_spec.detect_and_parse_spec))
    workflow.add_node("build_silver_api_model", timed_node(build_silver_api_model.build_silver_api_model))
    workflow.add_node("build_silver_file_model", timed_node(build_silver_file_model.build_silver_file_model))  # File Integration V1
    workflow.add_node("embed_spec_chunks", embed_spec_chunks.embed_spec_chunks)  # Has API call, no wrapper needed

    # Silver checkpoint - per design doc Section 5.4
    workflow.add_node("persist_silver_checkpoint", timed_node(persist_silver_checkpoint.persist_silver_checkpoint))

    # LLM nodes - no timed_node wrapper (LangSmith already tracks them well)
    workflow.add_node("understand_task", understand_task.understand_task)
    workflow.add_node("align_task_with_kg", timed_node(align_task_with_kg.align_task_with_kg))  # May skip LLM
    workflow.add_node("plan_integration_flow", plan_integration_flow.plan_integration_flow)
    workflow.add_node("attach_policies_and_patterns", timed_node(attach_policies_and_patterns.attach_policies_and_patterns))
    workflow.add_node("attach_repo_context", timed_node(attach_repo_context.attach_repo_context))
    workflow.add_node("generate_code_and_tests", generate_code_and_tests.generate_code_and_tests)  # LLM node

    # Gold checkpoint - per design doc Section 5.4
    workflow.add_node("persist_gold_checkpoint", timed_node(persist_gold_checkpoint.persist_gold_checkpoint))

    workflow.add_node("analyze_repo_layout", timed_node(analyze_repo_layout.analyze_repo_layout))
    workflow.add_node("apply_repo_integration_changes", timed_node(apply_repo_integration_changes.apply_repo_integration_changes))
    workflow.add_node("validate_integration_design", timed_node(validate_integration_design.validate_integration_design))

    # Legacy persist_results kept for backward compatibility (delegates to checkpoints if needed)
    workflow.add_node("persist_results", timed_node(persist_results.persist_results))

    # build_report is async; wrap so it is awaited and we record timings consistently.
    workflow.add_node("build_report", timed_node(build_report.build_report))  # Has LLM call for summary

    # Run outcome checkpoint - per design doc Section 5.4
    workflow.add_node("persist_run_outcome", timed_node(persist_run_outcome.persist_run_outcome))

    workflow.add_node("handle_error", timed_node(handle_error.handle_error))

    # Define edges
    workflow.set_entry_point("plan_run")
    workflow.add_edge("plan_run", "ingest_spec")
    workflow.add_edge("ingest_spec", "detect_and_parse_spec")
    workflow.add_edge("detect_and_parse_spec", "build_silver_api_model")
    workflow.add_edge("build_silver_api_model", "build_silver_file_model")  # File Integration V1: process files after APIs
    workflow.add_edge("build_silver_file_model", "embed_spec_chunks")

    # Silver checkpoint after embedding (per design doc Section 5.4)
    workflow.add_edge("embed_spec_chunks", "persist_silver_checkpoint")
    workflow.add_edge("persist_silver_checkpoint", "understand_task")

    workflow.add_edge("understand_task", "align_task_with_kg")
    workflow.add_edge("align_task_with_kg", "plan_integration_flow")
    workflow.add_edge("plan_integration_flow", "attach_policies_and_patterns")

    # Code generation
    workflow.add_edge("attach_policies_and_patterns", "generate_code_and_tests")

    # Gold checkpoint after code generation (per design doc Section 5.4)
    workflow.add_edge("generate_code_and_tests", "persist_gold_checkpoint")

    # KG learning after gold checkpoint - persists workflow templates to KG
    workflow.add_node("persist_kg_learning", timed_node(persist_kg_learning.persist_kg_learning))
    workflow.add_edge("persist_gold_checkpoint", "persist_kg_learning")

    # Conditional routing AFTER KG learning for repo integration
    def should_run_repo_nodes(state: WorkflowState) -> str:
        """Route to repo nodes if plan["use_repo"] is True, else skip to validation."""
        if state.plan.get("use_repo", False):
            return "with_repo"
        return "without_repo"

    workflow.add_conditional_edges(
        "persist_kg_learning",
        should_run_repo_nodes,
        {
            "with_repo": "attach_repo_context",
            "without_repo": "validate_integration_design",
        }
    )

    # Repo flow (when enabled) - happens AFTER gold checkpoint
    workflow.add_edge("attach_repo_context", "analyze_repo_layout")
    workflow.add_edge("analyze_repo_layout", "apply_repo_integration_changes")
    workflow.add_edge("apply_repo_integration_changes", "validate_integration_design")

    # Common path after validation
    def check_for_errors_after_validation(state: WorkflowState) -> str:
        """Check if errors occurred during validation."""
        if state.errors and not state.plan.get("failed", False):
            return "has_errors"
        return "no_errors"

    workflow.add_conditional_edges(
        "validate_integration_design",
        check_for_errors_after_validation,
        {
            "has_errors": "handle_error",
            "no_errors": "build_report",
        }
    )

    # After handle_error, still build report
    workflow.add_edge("handle_error", "build_report")

    # Run outcome checkpoint after build_report (per design doc Section 5.4)
    workflow.add_edge("build_report", "persist_run_outcome")
    workflow.add_edge("persist_run_outcome", END)

    # Compile with optional checkpointer for native resume support
    return workflow.compile(checkpointer=checkpointer)


def build_parallel_graph(checkpointer: Optional[BaseCheckpointSaver] = None):
    """
    Build the LangGraph workflow with parallel execution support (Plan 8).
    
    When PARALLEL_WORKFLOW=true, this graph runs embed_spec_chunks and
    understand_task in parallel after build_silver_api_model.
    
    Parallel execution flow:
        build_silver_api_model
               |
         [parallel split]
             /   \\
      embed_spec_chunks   understand_task
             \\   /
        [sync_embed_task]
               |
        persist_silver_checkpoint
               |
          align_task_with_kg
               |
             ...
    
    V2 Parallel Fix:
    Uses TypedDict (WorkflowStateDict) with Annotated reducers to enable
    LangGraph's native parallel merge. Each field has a reducer that tells
    LangGraph how to combine values from parallel branches:
    - `last_non_none`: Takes most recent non-None value (for scalars)
    - `unique_list`: Combines lists without duplicates (for completed_steps)
    - `merge_dicts`: Merges dicts (for plan, node_timings)
    - `operator.add`: Concatenates lists (for embeddings)
    
    Node functions still use WorkflowState dataclass internally - we wrap them
    to convert dict <-> dataclass at boundaries.
    
    Args:
        checkpointer: Optional LangGraph checkpointer for resume support
        
    Returns:
        Compiled LangGraph application with parallel execution
    """
    from integration_coworker.graph.parallel import sync_embed_task
    
    # Use TypedDict state for parallel graph (has Annotated reducers)
    workflow = StateGraph(WorkflowStateDict)
    
    # Wrapper to convert dict state -> dataclass for node execution -> dict result
    # Also filters out 'config' kwarg that LangGraph may pass
    def wrap_node_for_dict(node_fn: Callable) -> Callable:
        """Wrap a dataclass-based node to work with dict state."""
        @functools.wraps(node_fn)
        async def async_wrapper(state_dict: WorkflowStateDict, **kwargs) -> WorkflowStateDict:
            # LangGraph may pass 'config', filter it out for node functions
            # (timed_node wrapper also filters, but direct-wrapped nodes need this)
            kwargs.pop('config', None)
            # Convert dict to dataclass (handle if already a dataclass)
            if isinstance(state_dict, WorkflowState):
                state = state_dict
            else:
                state = dict_to_dataclass(state_dict)
            # Call the node (may be async) - pass remaining kwargs to support wrappers
            result = await node_fn(state, **kwargs)
            # Convert back to dict
            if isinstance(result, dict):
                return result
            return dataclass_to_dict(result)
        
        @functools.wraps(node_fn)
        def sync_wrapper(state_dict: WorkflowStateDict, **kwargs) -> WorkflowStateDict:
            # LangGraph may pass 'config', filter it out for node functions
            kwargs.pop('config', None)
            # Convert dict to dataclass (handle if already a dataclass)
            if isinstance(state_dict, WorkflowState):
                state = state_dict
            else:
                state = dict_to_dataclass(state_dict)
            # Call the node - pass remaining kwargs to support wrappers
            result = node_fn(state, **kwargs)
            # Convert back to dict
            if isinstance(result, dict):
                return result
            return dataclass_to_dict(result)
        
        # Choose wrapper based on whether node is async
        if inspect.iscoroutinefunction(node_fn):
            return async_wrapper
        return sync_wrapper
    
    # Timed wrapper that also handles dict conversion
    def timed_dict_node(node_fn: Callable) -> Callable:
        """Timed wrapper for dict-based state."""
        return wrap_node_for_dict(timed_node(node_fn))

    # Add nodes - wrapped for dict state
    workflow.add_node("plan_run", timed_dict_node(plan_run.plan_run))
    workflow.add_node("ingest_spec", timed_dict_node(ingest_spec.ingest_spec))
    workflow.add_node("detect_and_parse_spec", timed_dict_node(detect_and_parse_spec.detect_and_parse_spec))
    workflow.add_node("build_silver_api_model", timed_dict_node(build_silver_api_model.build_silver_api_model))
    workflow.add_node("build_silver_file_model", timed_dict_node(build_silver_file_model.build_silver_file_model))
    
    # Parallel branch nodes - wrapped for dict state
    workflow.add_node("embed_spec_chunks", wrap_node_for_dict(embed_spec_chunks.embed_spec_chunks))
    workflow.add_node("understand_task", wrap_node_for_dict(understand_task.understand_task))
    
    # Sync node - wrapped for dict state
    workflow.add_node("sync_embed_task", wrap_node_for_dict(timed_node(sync_embed_task)))
    
    # Silver checkpoint after sync
    workflow.add_node("persist_silver_checkpoint", timed_dict_node(persist_silver_checkpoint.persist_silver_checkpoint))

    # Rest of the nodes - wrapped for dict state
    workflow.add_node("align_task_with_kg", timed_dict_node(align_task_with_kg.align_task_with_kg))
    workflow.add_node("plan_integration_flow", wrap_node_for_dict(plan_integration_flow.plan_integration_flow))
    workflow.add_node("attach_policies_and_patterns", timed_dict_node(attach_policies_and_patterns.attach_policies_and_patterns))
    workflow.add_node("attach_repo_context", timed_dict_node(attach_repo_context.attach_repo_context))
    workflow.add_node("generate_code_and_tests", wrap_node_for_dict(generate_code_and_tests.generate_code_and_tests))
    workflow.add_node("persist_gold_checkpoint", timed_dict_node(persist_gold_checkpoint.persist_gold_checkpoint))
    workflow.add_node("analyze_repo_layout", timed_dict_node(analyze_repo_layout.analyze_repo_layout))
    workflow.add_node("apply_repo_integration_changes", timed_dict_node(apply_repo_integration_changes.apply_repo_integration_changes))
    workflow.add_node("validate_integration_design", timed_dict_node(validate_integration_design.validate_integration_design))
    workflow.add_node("persist_results", timed_dict_node(persist_results.persist_results))
    workflow.add_node("build_report", timed_dict_node(build_report.build_report))
    workflow.add_node("persist_run_outcome", timed_dict_node(persist_run_outcome.persist_run_outcome))
    workflow.add_node("handle_error", timed_dict_node(handle_error.handle_error))
    workflow.add_node("persist_kg_learning", timed_dict_node(persist_kg_learning.persist_kg_learning))

    # Define edges - sequential until build_silver_api_model
    workflow.set_entry_point("plan_run")
    workflow.add_edge("plan_run", "ingest_spec")
    workflow.add_edge("ingest_spec", "detect_and_parse_spec")
    workflow.add_edge("detect_and_parse_spec", "build_silver_api_model")
    workflow.add_edge("build_silver_api_model", "build_silver_file_model")
    
    # PARALLEL BRANCHES: build_silver_file_model fans out to both nodes
    # LangGraph will execute both branches when they have the same source
    workflow.add_edge("build_silver_file_model", "embed_spec_chunks")
    workflow.add_edge("build_silver_file_model", "understand_task")
    
    # Both parallel branches merge at sync_embed_task
    workflow.add_edge("embed_spec_chunks", "sync_embed_task")
    workflow.add_edge("understand_task", "sync_embed_task")
    
    # Continue sequential after sync
    workflow.add_edge("sync_embed_task", "persist_silver_checkpoint")
    workflow.add_edge("persist_silver_checkpoint", "align_task_with_kg")

    workflow.add_edge("align_task_with_kg", "plan_integration_flow")
    workflow.add_edge("plan_integration_flow", "attach_policies_and_patterns")
    workflow.add_edge("attach_policies_and_patterns", "generate_code_and_tests")
    workflow.add_edge("generate_code_and_tests", "persist_gold_checkpoint")
    workflow.add_edge("persist_gold_checkpoint", "persist_kg_learning")

    # Conditional routing for repo integration
    def should_run_repo_nodes(state: WorkflowStateDict) -> str:
        plan = state.get("plan", {})
        if plan.get("use_repo", False):
            return "with_repo"
        return "without_repo"

    workflow.add_conditional_edges(
        "persist_kg_learning",
        should_run_repo_nodes,
        {
            "with_repo": "attach_repo_context",
            "without_repo": "validate_integration_design",
        }
    )

    # Repo flow
    workflow.add_edge("attach_repo_context", "analyze_repo_layout")
    workflow.add_edge("analyze_repo_layout", "apply_repo_integration_changes")
    workflow.add_edge("apply_repo_integration_changes", "validate_integration_design")

    # Error handling
    def check_for_errors_after_validation(state: WorkflowStateDict) -> str:
        errors = state.get("errors", [])
        plan = state.get("plan", {})
        if errors and not plan.get("failed", False):
            return "has_errors"
        return "no_errors"

    workflow.add_conditional_edges(
        "validate_integration_design",
        check_for_errors_after_validation,
        {
            "has_errors": "handle_error",
            "no_errors": "build_report",
        }
    )

    workflow.add_edge("handle_error", "build_report")
    workflow.add_edge("build_report", "persist_run_outcome")
    workflow.add_edge("persist_run_outcome", END)

    logger.info("Built parallel workflow graph (PARALLEL_WORKFLOW=true)")
    return workflow.compile(checkpointer=checkpointer)


def run_workflow(
    state: WorkflowState,
    use_checkpointer: bool = True,
    thread_id: Optional[str] = None,
) -> WorkflowState:
    """
    Execute the workflow graph with LangSmith tracing context.
    
    Sets up run context for LLM client tracing, ensuring all LLM calls
    within this run are correlated with the same run_id and provider_code.
    
    V4 Observability: Initializes and captures token usage tracking.
    
    Bug #61 Fix: Uses LangGraph's native checkpointing for resume support.
    When use_checkpointer=True, state is automatically saved after each node,
    enabling resume from interruptions.
    
    Plan 8: When PARALLEL_WORKFLOW=true, uses parallel graph for faster execution.
    
    V3.0: Uses async execution with AsyncPostgresSaver (ASYNC_MIGRATION_PLAN.md).
    
    Args:
        state: Initial workflow state
        use_checkpointer: Enable LangGraph native checkpointing (default True)
        thread_id: Optional thread ID for checkpoint isolation. If not provided,
                   uses state.run_id. Each thread_id has its own checkpoint history.
    """
    return asyncio.run(_run_workflow_async(state, use_checkpointer, thread_id))


async def _run_workflow_async(
    state: WorkflowState,
    use_checkpointer: bool = True,
    thread_id: Optional[str] = None,
) -> WorkflowState:
    """Internal async implementation of run_workflow."""
    from integration_coworker.llm.client import init_token_usage, get_token_usage
    from integration_coworker.graph.parallel import is_parallel_enabled
    from integration_coworker.persistence.db import get_engine_type
    
    # Set run context for LangSmith tracing
    run_id = state.run_id or state.plan.get("run_id", "")
    provider_code = state.provider_code
    if run_id:
        set_run_context(run_id, provider_code)
    
    # V4 Observability: Initialize token tracking
    init_token_usage()
    
    # Check if parallel mode is enabled
    parallel_mode = is_parallel_enabled()
    
    # For parallel graph, convert initial state to dict format
    # (parallel graph uses WorkflowStateDict with Annotated reducers)
    if parallel_mode:
        initial_state = dataclass_to_dict(state)
    else:
        initial_state = state

    try:
        # Configure thread_id for checkpoint isolation
        effective_thread_id = thread_id or run_id or state.plan.get("run_id", "default")
        config = {"configurable": {"thread_id": effective_thread_id}}
        
        if use_checkpointer:
            async with async_checkpointer_context() as checkpointer:
                if parallel_mode:
                    app = build_parallel_graph(checkpointer=checkpointer)
                else:
                    app = build_graph(checkpointer=checkpointer)
                final_state_dict = await app.ainvoke(initial_state, config=config)
        else:
            if parallel_mode:
                app = build_parallel_graph(checkpointer=None)
            else:
                app = build_graph(checkpointer=None)
            final_state_dict = await app.ainvoke(initial_state, config=config)
        
        # Convert final state back to dataclass if it's a dict
        if isinstance(final_state_dict, dict):
            final_state = dict_to_dataclass(final_state_dict)
        else:
            final_state = final_state_dict
        
        # V4 Observability: Copy aggregated token usage to final state
        final_state.llm_token_usage = get_token_usage()
        
        return final_state
    finally:
        # Clean up run context
        clear_run_context()


# =============================================================================
# Recovery Support Functions (V2 Implementation Plan Section 3.5)
# =============================================================================

# Ordered list of all node names in the workflow for recovery
WORKFLOW_NODE_ORDER: List[str] = [
    "plan_run",
    "ingest_spec",
    "detect_and_parse_spec",
    "build_silver_api_model",
    "build_silver_file_model",
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


def get_node_names() -> List[str]:
    """
    Get the list of workflow node names in execution order.
    
    Returns:
        List of node names
    """
    return WORKFLOW_NODE_ORDER.copy()


def run_from_node(
    state: WorkflowState,
    start_node: str,
    thread_id: Optional[str] = None,
) -> WorkflowState:
    """
    Resume workflow execution using LangGraph's native checkpointing.
    
    Bug #61 Fix: Uses LangGraph's checkpointer to resume from the last
    successful checkpoint, not from a specific node.
    
    V3.0: Uses async execution with AsyncPostgresSaver (ASYNC_MIGRATION_PLAN.md).
    
    Args:
        state: The workflow state (used for config if no checkpoint exists)
        start_node: Deprecated - resume point is determined by checkpointer
        thread_id: Thread ID for checkpoint lookup (defaults to state.run_id)
        
    Returns:
        Final workflow state
        
    Raises:
        ValueError: If start_node is not a valid node name (for API compat)
    """
    if start_node not in WORKFLOW_NODE_ORDER:
        raise ValueError(f"Unknown node: {start_node}")
    
    return asyncio.run(_run_from_node_async(state, start_node, thread_id))


async def _run_from_node_async(
    state: WorkflowState,
    start_node: str,
    thread_id: Optional[str] = None,
) -> WorkflowState:
    """Internal async implementation of run_from_node."""
    from integration_coworker.llm.client import init_token_usage, get_token_usage
    from integration_coworker.persistence.db import get_engine_type
    
    # Set run context for LangSmith tracing
    run_id = state.run_id or state.plan.get("run_id", "")
    provider_code = state.provider_code
    
    if run_id:
        set_run_context(run_id, provider_code)
    
    try:
        # Initialize token tracking
        init_token_usage()
        
        # Use thread_id for checkpoint isolation
        effective_thread_id = thread_id or run_id or "default"
        config = {"configurable": {"thread_id": effective_thread_id}}
        
        # Use unified async checkpointer context so LangGraph gets a saver instance
        async with async_checkpointer_context() as checkpointer:
            app = build_graph(checkpointer=checkpointer)
            
            try:
                existing_state = await app.aget_state(config)
                if existing_state and existing_state.values:
                    logger.info(f"Resuming from checkpoint for thread {effective_thread_id}")
                    final_state_dict = await app.ainvoke(None, config=config)
                else:
                    logger.info(f"No checkpoint found for thread {effective_thread_id}, starting fresh")
                    final_state_dict = await app.ainvoke(state, config=config)
            except Exception as e:
                logger.warning(f"Could not load checkpoint, starting fresh: {e}")
                final_state_dict = await app.ainvoke(state, config=config)
        
        final_state = WorkflowState(**final_state_dict)
        final_state.llm_token_usage = get_token_usage()
        
        return final_state
    finally:
        clear_run_context()
