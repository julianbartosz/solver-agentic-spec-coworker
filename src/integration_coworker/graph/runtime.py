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

def build_graph():
    workflow = StateGraph(WorkflowState)

    # Add nodes
    workflow.add_node("plan_run", plan_run.plan_run)
    workflow.add_node("ingest_spec", ingest_spec.ingest_spec)
    workflow.add_node("detect_and_parse_spec", detect_and_parse_spec.detect_and_parse_spec)
    workflow.add_node("build_silver_api_model", build_silver_api_model.build_silver_api_model)
    workflow.add_node("embed_spec_chunks", embed_spec_chunks.embed_spec_chunks)
    workflow.add_node("understand_task", understand_task.understand_task)
    workflow.add_node("align_task_with_kg", align_task_with_kg.align_task_with_kg)
    workflow.add_node("plan_integration_flow", plan_integration_flow.plan_integration_flow)
    workflow.add_node("attach_policies_and_patterns", attach_policies_and_patterns.attach_policies_and_patterns)
    workflow.add_node("attach_repo_context", attach_repo_context.attach_repo_context)
    workflow.add_node("generate_code_and_tests", generate_code_and_tests.generate_code_and_tests)
    workflow.add_node("analyze_repo_layout", analyze_repo_layout.analyze_repo_layout)
    workflow.add_node("apply_repo_integration_changes", apply_repo_integration_changes.apply_repo_integration_changes)
    workflow.add_node("validate_integration_design", validate_integration_design.validate_integration_design)
    workflow.add_node("persist_results", persist_results.persist_results)
    workflow.add_node("build_report", build_report.build_report)
    workflow.add_node("handle_error", handle_error.handle_error)

    # Define edges
    workflow.set_entry_point("plan_run")
    workflow.add_edge("plan_run", "ingest_spec")
    workflow.add_edge("ingest_spec", "detect_and_parse_spec")
    workflow.add_edge("detect_and_parse_spec", "build_silver_api_model")
    workflow.add_edge("build_silver_api_model", "embed_spec_chunks")
    workflow.add_edge("embed_spec_chunks", "understand_task")
    workflow.add_edge("understand_task", "align_task_with_kg")
    workflow.add_edge("align_task_with_kg", "plan_integration_flow")
    workflow.add_edge("plan_integration_flow", "attach_policies_and_patterns")
    workflow.add_edge("attach_policies_and_patterns", "attach_repo_context")
    workflow.add_edge("attach_repo_context", "generate_code_and_tests")
    workflow.add_edge("generate_code_and_tests", "analyze_repo_layout")
    workflow.add_edge("analyze_repo_layout", "apply_repo_integration_changes")
    workflow.add_edge("apply_repo_integration_changes", "validate_integration_design")
    workflow.add_edge("validate_integration_design", "persist_results")
    workflow.add_edge("persist_results", "build_report")
    workflow.add_edge("build_report", END)

    # TODO: Add conditional edges to handle_error based on state.errors

    return workflow.compile()

def run_workflow(state: WorkflowState) -> WorkflowState:
    app = build_graph()
    final_state_dict = app.invoke(state)
    return WorkflowState(**final_state_dict)
