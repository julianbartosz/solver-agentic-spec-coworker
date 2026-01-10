"""
Human Edits Node (PR #11)

Applies patch-based human edits to code artifacts.

Per ADR-HITL-ENHANCEMENT-v2 PR #11:
- UI submits unified diff patches (NOT full file blobs)
- Patches are validated before application
- All edits are audited as artifacts
- State keeps only refs + bounded summary

Flow:
    sandbox_review_gate (decision=apply_human_edits)
        └── apply_human_edits
              └── static_analysis_gate
                    └── sandbox_attribution_gate
                          └── sandbox_review_gate (loop with budget)

NON-NEGOTIABLE INVARIANTS:
1. Single-file patches only
2. Strict context matching (no fuzzy)
3. AST validation for Python files
4. Audit artifact for every edit
5. Budget limit prevents infinite loops

CRITICAL: Importing this module must NOT import:
- Streamlit
- LangGraph internals (beyond what's needed)
- Database/persistence backends
"""

import logging
import time
from typing import Any, Dict, List, Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.human_edit_models import (
    HumanEditPatch,
    EditValidationResult,
    EditAuditEntry,
    HumanEditSummary,
    validate_patch,
    apply_patch,
    PatchApplyError,
    PatchParseError,
    EditValidationError,
    # Decision schema validation
    validate_sandbox_decision,
    DECISION_SCHEMA_VERSION,
)
from integration_coworker.graph.quality_artifacts import (
    store_human_edits_artifact,
)
# Single source of truth for budget
from integration_coworker.graph.production_guardrails import (
    MAX_HUMAN_EDIT_BUDGET,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Budget Constants
# =============================================================================

# Key in state.plan for tracking budget
EDIT_BUDGET_KEY = "human_edit_budget_used"


# =============================================================================
# Main Node Function
# =============================================================================

def apply_human_edits(state: WorkflowState) -> WorkflowState:
    """
    Apply human edit patches to code artifacts.
    
    This node:
    1. Checks edit budget (prevents infinite loops)
    2. Retrieves pending patches from review decision
    3. Validates each patch (context, AST, bounds)
    4. Applies valid patches to code_artifacts
    5. Creates audit artifacts
    6. Updates state with bounded summary
    
    On error:
    - Invalid patches are logged but don't fail the run
    - Budget exhausted triggers escalation
    
    Args:
        state: Current workflow state
        
    Returns:
        Updated state with applied edits
    """
    node_name = "apply_human_edits"
    start_time = time.time()
    
    run_id = state.run_id or f"unknown-{int(start_time)}"
    
    # =================================================================
    # 1. Budget Check
    # =================================================================
    
    budget_used = state.plan.get(EDIT_BUDGET_KEY, 0)
    
    if budget_used >= MAX_HUMAN_EDIT_BUDGET:
        logger.warning(
            f"{node_name}: Budget exhausted ({budget_used}/{MAX_HUMAN_EDIT_BUDGET}). "
            "Escalating to human review."
        )
        state.plan["human_edit_escalated"] = True
        state.warnings.append(
            f"Human edit budget exhausted ({MAX_HUMAN_EDIT_BUDGET} edits). "
            "Review and approve as-is or file a separate issue."
        )
        _mark_completed(state, node_name)
        return state
    
    # =================================================================
    # 2. Get Pending Patches from Review Decision
    # =================================================================
    
    patches = _get_pending_patches(state)
    
    if not patches:
        logger.info(f"{node_name}: No patches to apply")
        _mark_completed(state, node_name)
        return state
    
    logger.info(f"{node_name}: Processing {len(patches)} patch(es)")
    
    # =================================================================
    # 3. Build file content map from code_artifacts
    # =================================================================
    
    file_contents: Dict[str, str] = {}
    file_to_artifact: Dict[str, int] = {}  # Map path to artifact index
    
    for i, artifact in enumerate(state.code_artifacts or []):
        if artifact.rel_path:
            file_contents[artifact.rel_path] = artifact.content or ""
            file_to_artifact[artifact.rel_path] = i
    
    # =================================================================
    # 4. Validate and Apply Patches
    # =================================================================
    
    applied_patches: List[HumanEditPatch] = []
    audit_entries: List[Dict[str, Any]] = []
    validation_errors: List[str] = []
    
    for patch in patches:
        try:
            # Parse the patch
            patch.parse()
            
            # Check file exists in artifacts
            if patch.file_path not in file_contents:
                error = f"File not in code artifacts: {patch.file_path}"
                validation_errors.append(error)
                audit_entries.append(
                    EditAuditEntry.from_patch(
                        run_id,
                        patch,
                        EditValidationResult(valid=False, errors=[error]),
                        applied=False,
                    ).to_dict()
                )
                continue
            
            original_content = file_contents[patch.file_path]
            
            # Validate patch
            validation = validate_patch(patch, original_content)
            
            if not validation.valid:
                validation_errors.extend(validation.errors)
                audit_entries.append(
                    EditAuditEntry.from_patch(
                        run_id,
                        patch,
                        validation,
                        applied=False,
                    ).to_dict()
                )
                logger.warning(
                    f"{node_name}: Patch validation failed for {patch.file_path}: "
                    f"{validation.errors}"
                )
                continue
            
            # Apply patch
            new_content = apply_patch(patch, original_content)
            
            # Update the code artifact
            artifact_idx = file_to_artifact[patch.file_path]
            state.code_artifacts[artifact_idx].content = new_content
            
            # Record success
            applied_patches.append(patch)
            audit_entries.append(
                EditAuditEntry.from_patch(
                    run_id,
                    patch,
                    validation,
                    applied=True,
                ).to_dict()
            )
            
            # Update file_contents for subsequent patches to same file
            file_contents[patch.file_path] = new_content
            
            logger.info(
                f"{node_name}: Applied patch to {patch.file_path} "
                f"(reason: {patch.reason[:50]}...)"
            )
            
        except (PatchParseError, PatchApplyError) as e:
            error = f"Patch error for {patch.file_path}: {e}"
            validation_errors.append(error)
            audit_entries.append(
                EditAuditEntry.from_patch(
                    run_id,
                    patch,
                    EditValidationResult(valid=False, errors=[str(e)]),
                    applied=False,
                ).to_dict()
            )
            logger.warning(f"{node_name}: {error}")
        
        except Exception as e:
            error = f"Unexpected error applying patch to {patch.file_path}: {e}"
            validation_errors.append(error)
            logger.error(f"{node_name}: {error}", exc_info=True)
    
    # =================================================================
    # 5. Store Audit Artifact
    # =================================================================
    
    if audit_entries:
        try:
            audit_ref = store_human_edits_artifact(
                run_id,
                audit_entries,
                patches=[p.to_dict() for p in applied_patches],
            )
            
            # Store ref in quality_refs
            if "human_edits" not in state.quality_refs:
                state.quality_refs["human_edits"] = {}
            state.quality_refs["human_edits"]["audit_ref"] = audit_ref
            
        except Exception as e:
            logger.error(f"{node_name}: Failed to store audit artifact: {e}")
    
    # =================================================================
    # 6. Update State with Bounded Summary
    # =================================================================
    
    if applied_patches:
        summary = HumanEditSummary.from_patches(
            applied_patches,
            needs_static_recheck=True,
        )
        
        # Store summary (bounded, not full patches)
        state.plan["human_edit_summary"] = summary.to_dict()
        
        # Increment budget
        state.plan[EDIT_BUDGET_KEY] = budget_used + 1
        
        # Flag that static analysis should re-run
        state.plan["needs_static_recheck"] = True
        
        logger.info(
            f"{node_name}: Applied {len(applied_patches)} patch(es), "
            f"budget: {budget_used + 1}/{MAX_HUMAN_EDIT_BUDGET}"
        )
    
    # Add validation warnings to state
    if validation_errors:
        state.warnings.append(
            f"Human edit validation issues: {'; '.join(validation_errors[:3])}"
            + (f" (+{len(validation_errors) - 3} more)" if len(validation_errors) > 3 else "")
        )
    
    _mark_completed(state, node_name)
    
    duration = time.time() - start_time
    logger.info(f"{node_name}: Completed in {duration:.2f}s")
    
    return state


# =============================================================================
# Helper Functions
# =============================================================================

def _get_pending_patches(state: WorkflowState) -> List[HumanEditPatch]:
    """
    Get patches from the review decision.
    
    The sandbox review decision with action="apply_human_edits" should
    include the patches to apply in the decision payload.
    
    Also validates the decision schema version.
    """
    decisions = getattr(state, 'review_decisions', None) or {}
    sandbox_decision = decisions.get("sandbox", {})
    
    # Check for apply_human_edits action
    action = sandbox_decision.get("action")
    if action != "apply_human_edits":
        return []
    
    # Validate decision schema
    schema_errors = validate_sandbox_decision(sandbox_decision)
    if schema_errors:
        logger.error(
            f"Invalid sandbox decision schema (v{DECISION_SCHEMA_VERSION}): "
            f"{'; '.join(schema_errors)}"
        )
        return []
    
    # Get patches from decision
    patches_data = sandbox_decision.get("patches", [])
    
    if not patches_data:
        logger.warning("apply_human_edits action but no patches in decision")
        return []
    
    patches: List[HumanEditPatch] = []
    
    for patch_dict in patches_data:
        try:
            patch = HumanEditPatch.from_dict(patch_dict)
            patches.append(patch)
        except (EditValidationError, KeyError, TypeError) as e:
            logger.warning(f"Invalid patch in decision: {e}")
    
    return patches


def _mark_completed(state: WorkflowState, node_name: str) -> None:
    """Mark node as completed."""
    if node_name not in state.completed_steps:
        state.completed_steps.append(node_name)


# =============================================================================
# Graph Integration Functions
# =============================================================================

def check_needs_human_edit_recheck(state: WorkflowState) -> str:
    """
    Route after human edits applied.
    
    Always routes to static_analysis_gate for re-check since
    edits may have introduced issues.
    
    Returns:
        "recheck" - always (edits need validation)
    """
    # Human edits always need recheck
    return "recheck"


def check_human_edit_budget(state: WorkflowState) -> bool:
    """
    Check if human edit budget is available.
    
    Returns:
        True if budget available, False if exhausted
    """
    budget_used = state.plan.get(EDIT_BUDGET_KEY, 0)
    return budget_used < MAX_HUMAN_EDIT_BUDGET


def is_human_edit_escalated(state: WorkflowState) -> bool:
    """
    Check if human edits were escalated due to budget.
    
    Returns:
        True if escalated (budget exhausted)
    """
    return state.plan.get("human_edit_escalated", False)
