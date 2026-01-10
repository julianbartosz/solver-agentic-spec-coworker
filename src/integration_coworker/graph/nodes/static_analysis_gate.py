"""
Static analysis gate node.

PR #8: Static Analysis Gate

Runs static analysis checks on generated code artifacts before sandbox execution.
Stores results via PR #7 quality artifacts plumbing.

CRITICAL INVARIANTS:
- No auto-fix, no retries, no routing loops (signals only)
- No new interrupts (doesn't change interrupt ordering)
- Results stored as refs + bounded summaries only
- Built-in checks always run (syntax, imports)
- Optional tools discovered, not hardcoded

Per ADR-HITL-ENHANCEMENT-v2:
- Insert AFTER generate_code_and_tests, BEFORE sandbox
- Store in quality_refs["static"] with bounded summary
- No checkpoint size inflation

Per P3a Feedback Learning:
- Record lint results via safe_record_lint_result for KG confidence updates
- Single choke point: all lint results flow through this node
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.static_checks import run_all_checks
from integration_coworker.graph.quality_artifacts import (
    store_static_analysis_artifact,
    build_static_analysis_summary,
    check_no_blobs_in_quality_refs,
    validate_quality_refs_size,
)
from integration_coworker.graph.quality_models import StaticAnalysisResult

# P3a: Feedback hooks for lint results (single choke point)
from integration_coworker.feedback.hooks import (
    are_hooks_enabled,
    safe_record_lint_result,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Maximum size for quality_refs to prevent checkpoint bloat
MAX_QUALITY_REFS_BYTES = 8192  # 8KB


# =============================================================================
# Static Analysis Gate Node
# =============================================================================

def static_analysis_gate(state: WorkflowState) -> Dict[str, Any]:
    """
    Run static analysis on generated code artifacts.
    
    This node:
    1. Extracts code from code_artifacts
    2. Runs enabled static checks (built-ins + discovered tools)
    3. Stores full results as artifacts
    4. Updates quality_refs with bounded summary
    
    Does NOT:
    - Auto-fix issues (future PR)
    - Retry generation (future PR)
    - Change routing (signals only)
    - Add interrupts
    
    Args:
        state: Current workflow state with code_artifacts
        
    Returns:
        State update dict with quality_refs["static"]
    """
    run_id = state.run_id or "unknown"
    
    logger.info(f"[PR#8] static_analysis_gate: Starting for run={run_id}")
    
    # Extract code files from artifacts
    files = _extract_code_files(state)
    
    if not files:
        logger.info("[PR#8] No code files to analyze")
        return _build_empty_result(run_id, state)
    
    logger.debug(f"[PR#8] Analyzing {len(files)} files")
    
    # Get repo root for tool config detection
    repo_root = _get_repo_root(state)
    
    # Run all enabled checks
    result = run_all_checks(
        files=files,
        repo_root=repo_root,
        # Future: explicit_enable/disable from state.options
    )
    
    logger.info(
        f"[PR#8] Static analysis complete: passed={result.passed}, "
        f"blocking={result.blocking_count}, warnings={result.warning_count}"
    )
    
    # P3a: Record lint result for feedback learning (single choke point)
    # All static analysis flows through here, enabling KG confidence updates
    if are_hooks_enabled():
        template_key = _get_template_key(state)
        # Extract error/warning messages for feedback
        errors = [i.message for i in result.issues if i.severity == "error"][:10]
        warnings = [i.message for i in result.issues if i.severity == "warning"][:10]
        safe_record_lint_result(
            run_id=run_id,
            template_key=template_key,
            success=result.passed,
            warnings=warnings if warnings else None,
            errors=errors if errors else None,
        )
        logger.debug(f"[P3a] Recorded lint feedback for {template_key}")
    
    # Store result as artifact and get refs + summary
    static_refs = store_static_analysis_artifact(run_id, result)
    
    # Update quality_refs in state
    quality_refs = dict(state.quality_refs) if state.quality_refs else {}
    quality_refs["static"] = static_refs
    
    # Validate no blobs in refs
    violations = check_no_blobs_in_quality_refs(quality_refs)
    if violations:
        logger.warning(f"[PR#8] Quality refs blob violations: {violations}")
    
    # Validate size constraint
    if not validate_quality_refs_size(quality_refs, MAX_QUALITY_REFS_BYTES):
        logger.warning(
            f"[PR#8] quality_refs exceeds {MAX_QUALITY_REFS_BYTES} bytes, "
            "may cause checkpoint bloat"
        )
    
    return {
        "quality_refs": quality_refs,
        # Record that static analysis ran
        "completed_steps": state.completed_steps + ["static_analysis_gate"],
    }


def _extract_code_files(state: WorkflowState) -> Dict[str, str]:
    """
    Extract Python files from code artifacts.
    
    Args:
        state: Workflow state with code_artifacts
        
    Returns:
        Dict of {relative_path: code_content}
    """
    files = {}
    
    for artifact in state.code_artifacts:
        # CodeArtifact has file_path and content
        file_path = getattr(artifact, 'file_path', None) or getattr(artifact, 'path', None)
        content = getattr(artifact, 'content', None) or getattr(artifact, 'code', None)
        
        if file_path and content:
            # Normalize path
            rel_path = str(file_path)
            if rel_path.startswith('/'):
                rel_path = rel_path.lstrip('/')
            
            # Only analyze Python files
            if rel_path.endswith('.py'):
                files[rel_path] = content
    
    return files


def _get_repo_root(state: WorkflowState) -> Optional[Path]:
    """
    Get repository root from state for config detection.
    
    Args:
        state: Workflow state
        
    Returns:
        Path to repo root, or None if not available
    """
    if state.repo_root:
        return Path(state.repo_root) if isinstance(state.repo_root, str) else state.repo_root
    
    # Try to get from plan
    if state.plan.get("repo_root"):
        return Path(state.plan["repo_root"])
    
    return None


def _build_empty_result(run_id: str, state: WorkflowState) -> Dict[str, Any]:
    """
    Build result when no code files to analyze.
    
    Args:
        run_id: Current run ID
        state: Workflow state
        
    Returns:
        State update dict with empty static analysis result
    """
    empty_result = StaticAnalysisResult(
        passed=True,
        issues=[],
        blocking_count=0,
        warning_count=0,
        tool_versions={},
    )
    
    static_refs = store_static_analysis_artifact(run_id, empty_result)
    
    quality_refs = dict(state.quality_refs) if state.quality_refs else {}
    quality_refs["static"] = static_refs
    
    return {
        "quality_refs": quality_refs,
        "completed_steps": state.completed_steps + ["static_analysis_gate"],
    }


def _get_template_key(state: WorkflowState) -> str:
    """
    Get the template key from state for feedback recording.
    
    P3a: Required for lint feedback to associate results with templates.
    Used by KG confidence update system.
    
    Args:
        state: Workflow state
        
    Returns:
        Template key string, e.g., "workflow.stripe.create_charge"
    """
    # Try to get from workflow template (Bug #59: uses 'code' not 'key')
    if state.workflow_template and state.workflow_template.code:
        return state.workflow_template.code
    
    # Fallback to constructed key from provider + task
    provider = state.provider_code or "unknown"
    task_slug = "task"
    if state.integration_task:
        task_slug = state.integration_task.task_slug or "task"
    
    return f"workflow.{provider}.{task_slug}"
