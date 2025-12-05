import functools
import logging
import os
import time
from typing import Dict, Optional, Callable, List
from langgraph.graph import StateGraph, END
from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.nodes import (
    plan_run,
    ingest_spec,
    detect_and_parse_spec,
    build_silver_api_model,
    embed_spec_chunks,
    understand_task,
    align_task_with_kg,
    plan_integration_flow,
    attach_policies_and_patterns,
    attach_repo_context,
    generate_code_and_tests,
    analyze_repo_layout,
    apply_repo_integration_changes,
    validate_integration_design,
    persist_results,
    build_report,
    handle_error,
)
from integration_coworker.graph.nodes import (
    persist_silver_checkpoint,
    persist_gold_checkpoint,
    persist_run_outcome,
    persist_kg_learning,
)
from integration_coworker.llm.client import set_run_context, clear_run_context

logger = logging.getLogger(__name__)


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
    "embed_spec_chunks": ["build_silver_api_model"],
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

    if tracing_enabled:
        try:
            from langsmith import traceable

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
                start = time.perf_counter()
                result = fn(state, *args, **kwargs)
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
    @functools.wraps(fn)
    def wrapper(state: WorkflowState, *args, **kwargs):
        start = time.perf_counter()
        result = fn(state, *args, **kwargs)
        duration_ms = (time.perf_counter() - start) * 1000

        # Record into result state's timings dict
        if hasattr(result, 'node_timings'):
            result.node_timings[node_name] = duration_ms
        
        # Save checkpoint after successful execution
        _save_checkpoint_if_enabled(result)

        return result

    return wrapper

def build_graph():
    """
    Build the LangGraph workflow.
    
    Per design doc Section 5.4, the graph has three checkpoint nodes:
    - persist_silver_checkpoint: After build_silver_api_model + embed_spec_chunks
    - persist_gold_checkpoint: After generate_code_and_tests
    - persist_run_outcome: After build_report (final status and metrics)
    
    Non-LLM nodes are wrapped with timed_node() to record execution time
    in state.node_timings for observability (since LangSmith may show "0.00s").
    """
    workflow = StateGraph(WorkflowState)

    # Add nodes - wrap non-LLM nodes with timed_node for internal timing
    # These nodes may show "0.00s" in LangSmith but do meaningful work
    workflow.add_node("plan_run", timed_node(plan_run.plan_run))
    workflow.add_node("ingest_spec", timed_node(ingest_spec.ingest_spec))
    workflow.add_node("detect_and_parse_spec", timed_node(detect_and_parse_spec.detect_and_parse_spec))
    workflow.add_node("build_silver_api_model", timed_node(build_silver_api_model.build_silver_api_model))
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

    workflow.add_node("build_report", build_report.build_report)  # Has LLM call for summary

    # Run outcome checkpoint - per design doc Section 5.4
    workflow.add_node("persist_run_outcome", timed_node(persist_run_outcome.persist_run_outcome))

    workflow.add_node("handle_error", timed_node(handle_error.handle_error))

    # Define edges
    workflow.set_entry_point("plan_run")
    workflow.add_edge("plan_run", "ingest_spec")
    workflow.add_edge("ingest_spec", "detect_and_parse_spec")
    workflow.add_edge("detect_and_parse_spec", "build_silver_api_model")
    workflow.add_edge("build_silver_api_model", "embed_spec_chunks")

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

    return workflow.compile()

def run_workflow(state: WorkflowState) -> WorkflowState:
    """
    Execute the workflow graph with LangSmith tracing context.
    
    Sets up run context for LLM client tracing, ensuring all LLM calls
    within this run are correlated with the same run_id and provider_code.
    
    V4 Observability: Initializes and captures token usage tracking.
    """
    from integration_coworker.llm.client import init_token_usage, get_token_usage
    
    app = build_graph()

    # Set run context for LangSmith tracing
    # Use existing run_id from state or generate a new one
    run_id = state.run_id or state.plan.get("run_id", "")
    provider_code = state.provider_code

    if run_id:
        set_run_context(run_id, provider_code)
    
    # V4 Observability: Initialize token tracking
    init_token_usage()

    try:
        final_state_dict = app.invoke(state)
        final_state = WorkflowState(**final_state_dict)
        
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
) -> WorkflowState:
    """
    Execute the workflow starting from a specific node.
    
    Used for recovery/resume operations.
    
    Note: This is a simplified implementation. Full support would require
    LangGraph's interrupt/resume features.
    
    Args:
        state: The workflow state to resume from
        start_node: The node to start execution from
        
    Returns:
        Final workflow state
        
    Raises:
        ValueError: If start_node is not a valid node name
    """
    if start_node not in WORKFLOW_NODE_ORDER:
        raise ValueError(f"Unknown node: {start_node}")
    
    # For now, we rebuild and run the full graph
    # LangGraph doesn't have a simple "start from node X" API
    # We mark the completed steps so nodes can detect they should be skipped
    
    # Set run context for LangSmith tracing
    run_id = state.run_id or state.plan.get("run_id", "")
    provider_code = state.provider_code
    
    if run_id:
        set_run_context(run_id, provider_code)
    
    try:
        app = build_graph()
        final_state_dict = app.invoke(state)
        return WorkflowState(**final_state_dict)
    finally:
        clear_run_context()
