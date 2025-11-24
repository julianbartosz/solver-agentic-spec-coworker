import re
import uuid
from integration_coworker.graph.state import WorkflowState


def plan_run(state: WorkflowState) -> WorkflowState:
    """
    Reads: task_description, spec_refs, provider_code, options
    Writes: run_id, plan["use_repo"], plan["provider_code"], plan["primary_spec_ref"], plan["steps"]
    
    Contract per Appendix C.3.1:
    - Enforces v1 constraint: exactly one spec_ref
    - Honors options.override_provider_code
    - Sets plan["use_repo"] only if repo_root is set AND repo_integration_enabled
    - Generates run_id for tracking
    """
    # 0. Generate run_id
    if not state.run_id:
        state.run_id = str(uuid.uuid4())
    
    # 1. Enforce v1 spec_refs constraint
    if not state.spec_refs or len(state.spec_refs) != 1:
        error_msg = f"v1 requires exactly one spec_ref, got {len(state.spec_refs)}"
        state.errors.append(error_msg)
        state.plan["failed"] = True
        state.completed_steps.append("plan_run")
        raise ValueError(error_msg)
    
    # 2. Honor override_provider_code from options
    if state.options and state.options.override_provider_code:
        # Normalize: lowercase, replace non-alphanumeric with underscore
        normalized = re.sub(r'[^a-z0-9]+', '_', state.options.override_provider_code.lower())
        state.provider_code = normalized.strip('_')
    elif not state.provider_code:
        # Infer from spec_ref if not already set
        primary_ref = state.spec_refs[0]
        if "stripe" in primary_ref.lower():
            state.provider_code = "stripe"
        elif "mock_payments" in primary_ref.lower():
            state.provider_code = "mock_payments"
        elif "github" in primary_ref.lower():
            state.provider_code = "github"
        else:
            # Extract from hostname or path
            if "://" in primary_ref:
                # URL: extract domain
                parts = primary_ref.split("://")[1].split("/")[0].split(".")
                state.provider_code = parts[0] if parts else "unknown"
            else:
                # File path: extract from filename
                state.provider_code = "unknown"
    
    # 3. Fix plan["use_repo"] logic per spec
    # Must have both repo_root AND repo_integration_enabled
    repo_integration_enabled = getattr(state.options, "repo_integration_enabled", True) if state.options else True
    state.plan = {
        "provider_code": state.provider_code,
        "primary_spec_ref": state.spec_refs[0],
        "use_repo": bool(state.repo_root) and bool(repo_integration_enabled),
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
            "analyze_repo_layout",
            "apply_repo_integration_changes",
        ])
    
    # Code generation happens after policies (and repo context if enabled)
    state.plan["steps"].append("generate_code_and_tests")
    
    # Always validate, persist, and report
    state.plan["steps"].extend([
        "validate_integration_design",
        "persist_results",
        "build_report",
    ])
    
    state.completed_steps.append("plan_run")
    return state
