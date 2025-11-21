from pathlib import Path
from integration_coworker.graph.state import WorkflowState

def apply_repo_integration_changes(state: WorkflowState) -> WorkflowState:
    """
    Reads: repo_root, repo_changes, options.dry_run
    Writes: (filesystem only, or dry-run summary)
    """
    if not state.repo_changes or not state.repo_root:
        state.completed_steps.append("apply_repo_integration_changes")
        return state

    # Check dry_run flag
    is_dry_run = state.options.dry_run if state.options else False
    
    if is_dry_run:
        # Don't write files, just log what would be changed
        summary_lines = ["DRY RUN - Would apply the following changes:\n"]
        for change in state.repo_changes.changes:
            summary_lines.append(f"  {change.change_type.upper()}: {change.rel_path}")
        
        # Store summary in state for reporting
        if not hasattr(state, "dry_run_summary"):
            state.plan["dry_run_summary"] = "\n".join(summary_lines)
    else:
        # Actually write files
        for change in state.repo_changes.changes:
            target_path = Path(state.repo_root) / change.rel_path
            
            # Ensure parent directory exists
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            if change.change_type == "create":
                # Write new file
                target_path.write_text(change.after, encoding="utf-8")
            
            elif change.change_type == "update":
                # For Phase 2: simple overwrite
                # Real implementation would:
                # - Look for markers in integration_hooks
                # - Insert imports below markers
                # - Preserve existing code
                target_path.write_text(change.after, encoding="utf-8")

    state.completed_steps.append("apply_repo_integration_changes")
    return state
