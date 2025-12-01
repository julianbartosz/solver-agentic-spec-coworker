import functools
import logging
import os
import time
from typing import Dict, Optional, Callable
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


def timed_node(fn: Callable, metadata: Optional[Dict[str, str]] = None):
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
    
    Args:
        fn: The node function to wrap
        metadata: Optional override metadata dict with 'category' and 'responsibility'
    """
    # Get metadata from catalog or use provided override
    node_name = fn.__name__
    node_meta = metadata or NODE_METADATA.get(node_name, {
        "category": "unknown",
        "responsibility": f"Node: {node_name}",
    })

    # Check if LangSmith tracing is enabled
    tracing_enabled = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"

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
    """
    app = build_graph()

    # Set run context for LangSmith tracing
    # Use existing run_id from state or generate a new one
    run_id = state.run_id or state.plan.get("run_id", "")
    provider_code = state.provider_code

    if run_id:
        set_run_context(run_id, provider_code)

    try:
        final_state_dict = app.invoke(state)
        return WorkflowState(**final_state_dict)
    finally:
        # Clean up run context
        clear_run_context()
