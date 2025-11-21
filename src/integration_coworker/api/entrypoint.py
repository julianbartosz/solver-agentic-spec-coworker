"""
Public API entrypoint for the integration coworker.

File: api/entrypoint.py (per Section 4.4 of design spec)
"""
from pathlib import Path
from typing import Iterable, Optional

from integration_coworker.api.types import IntegrationOptions, IntegrationResult
from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.runtime import run_workflow


def design_and_generate_integration(
    spec_refs: Iterable[str],
    task_description: str,
    provider_code: Optional[str] = None,
    repo_root: Optional[Path] = None,
    repo_profile: Optional['RepoProfile'] = None,  # Forward reference
    options: Optional[IntegrationOptions] = None,
) -> IntegrationResult:
    """
    Public API for a single integration run.
    
    Per Section 4.4 and Appendix C of the design spec.
    
    Args:
        spec_refs: List of spec URLs or file paths (v1: exactly one).
        task_description: Natural-language description of the integration task.
        provider_code: Optional override; may also be set via options.override_provider_code.
        repo_root: Optional filesystem root of a target repo for wiring.
        repo_profile: Optional explicit RepoProfile; if None and repo_root is set,
                      attach_repo_context is responsible for detecting a profile.
        options: IntegrationOptions controlling repo integration and dry-run behavior.
    
    Returns:
        IntegrationResult with run_id, task, code_artifacts, repo_changes, and report.
    """
    state = WorkflowState(
        source_refs=[],  # v1: reserved for future use
        spec_refs=list(spec_refs),
        task_description=task_description,
        provider_code=provider_code,
        repo_root=repo_root,
        repo_profile=repo_profile,
        options=options or IntegrationOptions(),
    )

    final_state = run_workflow(state)

    return IntegrationResult(
        run_id=final_state.run_id or "",
        task=final_state.integration_task,
        code_artifacts=final_state.code_artifacts,
        repo_changes=final_state.repo_changes,
        report_markdown=final_state.report_markdown or "",
    )
