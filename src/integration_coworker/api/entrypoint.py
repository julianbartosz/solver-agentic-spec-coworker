"""
Public API entrypoint for the integration coworker.

File: api/entrypoint.py (per Section 4.4 of design spec)
"""
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Union, TYPE_CHECKING

from integration_coworker.api.types import IntegrationOptions, IntegrationResult
from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.runtime import run_workflow

if TYPE_CHECKING:
    from integration_coworker.repo.models import RepoProfile


def design_and_generate_integration(
    spec_refs: Iterable[str],
    task_description: str,
    provider_code: Optional[str] = None,
    repo_root: Optional[Path] = None,
    repo_profile: Optional['RepoProfile'] = None,
    options: Optional[Union[IntegrationOptions, dict]] = None,
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
    # Normalize options: convert dict to IntegrationOptions if needed
    if options is None:
        normalized_options = IntegrationOptions()
    elif isinstance(options, dict):
        # Filter to only known fields to avoid unexpected keyword arguments
        known_fields = {f.name for f in IntegrationOptions.__dataclass_fields__.values()}
        filtered_options = {k: v for k, v in options.items() if k in known_fields}
        normalized_options = IntegrationOptions(**filtered_options)
    else:
        normalized_options = options

    state = WorkflowState(
        source_refs=[],  # v1: reserved for future use
        spec_refs=list(spec_refs),
        task_description=task_description,
        provider_code=provider_code,
        repo_root=repo_root,
        repo_profile=repo_profile,
        options=normalized_options,
    )

    # Bug #68 Fix: Generate unique run_id BEFORE workflow starts
    # This ensures LangGraph checkpointing uses unique thread_id per run
    # instead of falling back to "default" which causes checkpoint collisions
    state.run_id = f"run_{uuid.uuid4().hex[:8]}_{int(datetime.now().timestamp())}"

    final_state = run_workflow(state)

    return IntegrationResult(
        run_id=final_state.run_id or "",
        task=final_state.integration_task,
        code_artifacts=final_state.code_artifacts,
        repo_changes=final_state.repo_changes,
        report_markdown=final_state.report_markdown or "",
        # M4: Include persistence and Silver/Gold artifacts
        persisted_ids=final_state.persisted_ids,
        endpoints=final_state.endpoints,
        schemas=final_state.schemas,
        entities=final_state.entities,
        workflow_nodes=final_state.workflow_nodes,
        workflow_edges=final_state.workflow_edges,
        endpoint_bindings=final_state.endpoint_bindings,
        # V2.1: Expose inferred policies (Section 13.6)
        policies=final_state.policies,
        # V1.1 (FT-001): Spec caching status
        cache_hit=final_state.cache_hit,
        # Phase 4: Multi-spec and diagnostics
        spec_documents=final_state.spec_documents,
        doc_chunks=final_state.doc_chunks,
        plan=final_state.plan,
        errors=final_state.errors,
        completed_steps=final_state.completed_steps,
        provider_code=final_state.provider_code,
    )
