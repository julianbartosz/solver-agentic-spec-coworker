import re
import uuid
from integration_coworker.graph.state import WorkflowState


def plan_run(state: WorkflowState) -> WorkflowState:
    """
    Reads: task_description, spec_refs, provider_code, options
    Writes: run_id, plan["use_repo"], plan["provider_code"], plan["primary_spec_ref"], 
            plan["supporting_spec_refs"], plan["steps"]
    
    Contract per Appendix C.3.1:
    - Supports multiple spec_refs (Phase 4 / M5)
    - spec_refs[0] is primary, spec_refs[1:] are supporting
    - Honors options.override_provider_code
    - Sets plan["use_repo"] only if repo_root is set AND repo_integration_enabled
    - Generates run_id for tracking
    """
    # 0. Generate run_id
    if not state.run_id:
        state.run_id = str(uuid.uuid4())
    
    # 1. Validate spec_refs (require at least one)
    if not state.spec_refs or len(state.spec_refs) < 1:
        error_msg = "At least one spec_ref is required"
        state.errors.append(error_msg)
        state.plan["failed"] = True
        state.completed_steps.append("plan_run")
        raise ValueError(error_msg)
    
    # Per design doc Appendix C.1.1: first spec_ref is primary, rest are supporting
    primary_ref = state.spec_refs[0]
    supporting_refs = state.spec_refs[1:] if len(state.spec_refs) > 1 else []
    
    # 2. Honor override_provider_code from options
    if state.options and state.options.override_provider_code:
        # Normalize: lowercase, replace non-alphanumeric with underscore
        normalized = re.sub(r'[^a-z0-9]+', '_', state.options.override_provider_code.lower())
        state.provider_code = normalized.strip('_')
    elif not state.provider_code:
        # Infer from primary spec_ref if not already set
        if "stripe" in primary_ref.lower():
            state.provider_code = "stripe"
        elif "mock_payments" in primary_ref.lower():
            state.provider_code = "mock_payments"
        elif "github" in primary_ref.lower():
            state.provider_code = "github"
        elif "petstore" in primary_ref.lower():
            state.provider_code = "petstore"
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
        "primary_spec_ref": primary_ref,
        "supporting_spec_refs": supporting_refs,  # Per design doc Appendix C.1.1
        "spec_count": len(state.spec_refs),  # For multi-spec tracking
        "use_repo": bool(state.repo_root) and bool(repo_integration_enabled),
        "steps": [
            "ingest_spec",
            "detect_and_parse_spec",
            "build_silver_api_model",
            "embed_spec_chunks",
            "persist_silver_checkpoint",  # Per design doc Section 5.4
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
    
    # Gold checkpoint after code generation (per design doc Section 5.4)
    state.plan["steps"].append("persist_gold_checkpoint")
    
    # Always validate and report
    state.plan["steps"].extend([
        "validate_integration_design",
        "build_report",
        "persist_run_outcome",  # Per design doc Section 5.4
    ])
    
    state.completed_steps.append("plan_run")
    return state
