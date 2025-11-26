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

def build_graph():
    """
    Build the LangGraph workflow.
    
    Per design doc Section 5.4, the graph has three checkpoint nodes:
    - persist_silver_checkpoint: After build_silver_api_model + embed_spec_chunks
    - persist_gold_checkpoint: After generate_code_and_tests
    - persist_run_outcome: After build_report (final status and metrics)
    """
    workflow = StateGraph(WorkflowState)

    # Add nodes
    workflow.add_node("plan_run", plan_run.plan_run)
    workflow.add_node("ingest_spec", ingest_spec.ingest_spec)
    workflow.add_node("detect_and_parse_spec", detect_and_parse_spec.detect_and_parse_spec)
    workflow.add_node("build_silver_api_model", build_silver_api_model.build_silver_api_model)
    workflow.add_node("embed_spec_chunks", embed_spec_chunks.embed_spec_chunks)
    
    # Silver checkpoint - per design doc Section 5.4
    workflow.add_node("persist_silver_checkpoint", persist_silver_checkpoint.persist_silver_checkpoint)
    
    workflow.add_node("understand_task", understand_task.understand_task)
    workflow.add_node("align_task_with_kg", align_task_with_kg.align_task_with_kg)
    workflow.add_node("plan_integration_flow", plan_integration_flow.plan_integration_flow)
    workflow.add_node("attach_policies_and_patterns", attach_policies_and_patterns.attach_policies_and_patterns)
    workflow.add_node("attach_repo_context", attach_repo_context.attach_repo_context)
    workflow.add_node("generate_code_and_tests", generate_code_and_tests.generate_code_and_tests)
    
    # Gold checkpoint - per design doc Section 5.4
    workflow.add_node("persist_gold_checkpoint", persist_gold_checkpoint.persist_gold_checkpoint)
    
    workflow.add_node("analyze_repo_layout", analyze_repo_layout.analyze_repo_layout)
    workflow.add_node("apply_repo_integration_changes", apply_repo_integration_changes.apply_repo_integration_changes)
    workflow.add_node("validate_integration_design", validate_integration_design.validate_integration_design)
    
    # Legacy persist_results kept for backward compatibility (delegates to checkpoints if needed)
    workflow.add_node("persist_results", persist_results.persist_results)
    
    workflow.add_node("build_report", build_report.build_report)
    
    # Run outcome checkpoint - per design doc Section 5.4
    workflow.add_node("persist_run_outcome", persist_run_outcome.persist_run_outcome)
    
    workflow.add_node("handle_error", handle_error.handle_error)

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
    workflow.add_node("persist_kg_learning", persist_kg_learning.persist_kg_learning)
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
    app = build_graph()
    final_state_dict = app.invoke(state)
    return WorkflowState(**final_state_dict)
