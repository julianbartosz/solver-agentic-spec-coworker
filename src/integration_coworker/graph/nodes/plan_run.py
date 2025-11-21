from integration_coworker.graph.state import WorkflowState

def plan_run(state: WorkflowState) -> WorkflowState:
    """
    Reads: task_description, spec_refs, provider_code, options
    Writes: plan["use_repo"], plan["provider_code"], plan["primary_spec_ref"], plan["steps"]
    """
    # Derive provider_code if not provided
    if not state.provider_code and state.spec_refs:
        primary_ref = state.spec_refs[0]
        # Simple heuristic: extract from path/URL
        if "stripe" in primary_ref.lower():
            state.provider_code = "stripe"
        elif "mock_payments" in primary_ref.lower():
            state.provider_code = "mock_payments"
        else:
            # Default fallback
            state.provider_code = "unknown"
    
    # Build execution plan
    state.plan = {
        "provider_code": state.provider_code,
        "primary_spec_ref": state.spec_refs[0] if state.spec_refs else None,
        "use_repo": state.options.repo_integration_enabled if state.options else False,
        "steps": [
            "ingest_spec",
            "detect_and_parse_spec",
            "build_silver_api_model",
            "embed_spec_chunks",
            "understand_task",
            "align_task_with_kg",
            "plan_integration_flow",
            "attach_policies_and_patterns",
        ],
    }
    
    # Add repo-specific steps if enabled
    if state.plan["use_repo"]:
        state.plan["steps"].extend([
            "attach_repo_context",
            "generate_code_and_tests",
            "analyze_repo_layout",
            "apply_repo_integration_changes",
        ])
    else:
        state.plan["steps"].append("generate_code_and_tests")
    
    # Always validate, persist, and report
    state.plan["steps"].extend([
        "validate_integration_design",
        "persist_results",
        "build_report",
    ])
    
    state.completed_steps.append("plan_run")
    return state
