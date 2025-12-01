"""
build_report node - Generate a human-readable markdown report.

Uses structured template, with optional LLM enhancement for executive summary.
"""
import logging
from integration_coworker.graph.state import WorkflowState
from integration_coworker.llm import call_llm

logger = logging.getLogger(__name__)


def _build_summary_prompt(structured_report: str, state: WorkflowState) -> str:
    """Build prompt for LLM to generate executive summary."""
    return f"""Generate a brief executive summary (2-3 sentences) for this integration report.

TASK: {state.task_description or "API integration"}
PROVIDER: {state.provider_code or "unknown"}

KEY METRICS:
- Endpoints discovered: {len(state.endpoints)}
- Workflow nodes: {len(state.workflow_nodes)}
- Code artifacts generated: {len(state.code_artifacts)}
- Errors: {len(state.errors)}

Generate a concise summary highlighting what was accomplished and any important notes.
"""


def build_report(state: WorkflowState) -> WorkflowState:
    """
    Reads: All state fields
    Writes: report_markdown
    
    Generates structured report, with optional LLM-enhanced executive summary.
    """
    lines = []

    lines.append("# Integration Co-Worker Report")
    lines.append("")

    # Header
    lines.append(f"**Run ID**: `{state.run_id or 'N/A'}`")
    lines.append(f"**Provider**: `{state.provider_code or 'unknown'}`")
    lines.append(f"**Task**: {state.task_description or 'N/A'}")
    lines.append("")

    # Try to generate LLM executive summary
    try:
        structured_report = _build_structured_metrics(state)
        summary_prompt = _build_summary_prompt(structured_report, state)
        summary = call_llm(summary_prompt, task_type="report")

        # Check if we got a real summary (not mock placeholder)
        if summary and len(summary) > 20 and not summary.startswith("Mock response"):
            lines.append("## Executive Summary")
            lines.append(summary.strip())
            lines.append("")
            logger.info("Generated LLM executive summary")
    except Exception as e:
        logger.debug(f"Skipping LLM summary: {e}")

    # Spec ingestion
    lines.append("## Spec Ingestion")
    lines.append(f"- Spec documents: {len(state.spec_documents)}")
    lines.append(f"- Chunks: {len(state.doc_chunks)}")
    lines.append("")

    # Silver model
    lines.append("## Silver API Model")
    lines.append(f"- Endpoints: {len(state.endpoints)}")
    if state.endpoints:
        lines.append("### Endpoints:")
        for ep in state.endpoints[:5]:  # Show first 5
            lines.append(f"  - `{ep.method} {ep.path}` ({ep.operation_id})")
        if len(state.endpoints) > 5:
            lines.append(f"  - ... and {len(state.endpoints) - 5} more")
    lines.append(f"- Schemas: {len(state.schemas)}")
    lines.append(f"- Entities: {len(state.entities)}")
    lines.append(f"- Relationships: {len(state.relationships)}")
    lines.append("")

    # Gold workflow
    lines.append("## Integration Workflow")
    if state.integration_task:
        lines.append(f"**Task Slug**: `{state.integration_task.task_slug}`")

    # Template selection (M5 WS2-T3: Show template source)
    template_source = state.plan.get("template_source", "unknown") if state.plan else "unknown"
    candidate_templates = state.plan.get("candidate_templates", []) if state.plan else []

    lines.append("")
    lines.append("### Template Selection")
    lines.append(f"**Source**: `{template_source}`")

    if template_source == "inferred":
        lines.append("- Workflow inferred from spec structure (HTTP method → pattern)")
    elif template_source == "kg":
        lines.append("- Template matched from Knowledge Graph")
    elif template_source == "legacy":
        lines.append("- Legacy hardcoded template (deprecated)")
    elif template_source == "pattern":
        lines.append("- Cross-provider pattern matched from KG")

    if candidate_templates:
        lines.append(f"- Candidate templates considered: {len(candidate_templates)}")
        for tmpl in candidate_templates[:3]:  # Show first 3
            tmpl_id = tmpl.get("template_id", "unknown")
            tmpl_name = tmpl.get("name", "")
            lines.append(f"  - `{tmpl_id}`: {tmpl_name}")
    else:
        lines.append("- No templates matched (using dynamic inference)")

    lines.append("")
    lines.append(f"- Workflow nodes: {len(state.workflow_nodes)}")
    if state.workflow_nodes:
        lines.append("### Workflow Steps:")
        for node in state.workflow_nodes:
            label = node.config.get("label", node.node_key) if node.config else node.node_key
            description = node.config.get("description", "") if node.config else ""
            lines.append(f"  {node.position + 1}. **{label}** ({node.node_type}): {description}")
    lines.append(f"- Workflow edges: {len(state.workflow_edges)}")
    lines.append(f"- Endpoint bindings: {len(state.endpoint_bindings)}")
    lines.append("")

    # Policies
    lines.append("## Policies")
    lines.append(f"- Total policies: {len(state.policies)}")
    if state.policies:
        policy_counts = {}
        for policy in state.policies:
            pt = policy.policy_type.value if hasattr(policy.policy_type, 'value') else str(policy.policy_type)
            policy_counts[pt] = policy_counts.get(pt, 0) + 1
        for policy_type, count in policy_counts.items():
            lines.append(f"  - {policy_type}: {count}")
    lines.append("")

    # Code generation
    lines.append("## Generated Code")
    lines.append(f"- Code artifacts: {len(state.code_artifacts)}")
    if state.code_artifacts:
        lines.append("### Artifacts:")
        for artifact in state.code_artifacts:
            lines.append(f"  - `{artifact.rel_path}` ({artifact.artifact_type}, {artifact.language})")
    lines.append("")

    # Repo integration
    if state.repo_changes:
        lines.append("## Repository Changes")
        created = state.repo_changes.files_created()
        updated = state.repo_changes.files_updated()
        lines.append(f"- Files to create: {len(created)}")
        for change in created:
            lines.append(f"  - `{change.rel_path}`")
        lines.append(f"- Files to update: {len(updated)}")
        for change in updated:
            lines.append(f"  - `{change.rel_path}`")
        lines.append("")

    # Repo Profile Detection (M6: Two-layer pipeline)
    if state.repo_profile:
        lines.append("## Repository Profile")
        rp = state.repo_profile
        lines.append(f"**Profile Name**: `{rp.name}`")

        if rp.framework:
            lines.append(f"**Framework**: `{rp.framework}`")
        if rp.archetype:
            lines.append(f"**Detected Archetype**: `{rp.archetype}`")
        lines.append(f"**Language**: `{rp.language}`")

        # Detection metadata
        if rp.detection_confidence is not None:
            confidence_pct = rp.detection_confidence * 100
            confidence_level = "High" if rp.detection_confidence >= 0.8 else "Medium" if rp.detection_confidence >= 0.5 else "Low"
            lines.append(f"**Detection Confidence**: {confidence_pct:.0f}% ({confidence_level})")

        if rp.profile_source:
            source_desc = {
                "archetype": "Used archetype defaults (high confidence)",
                "archetype+heuristic": "Archetype with heuristic refinement",
                "heuristic": "Inferred via heuristics (no archetype match)",
                "heuristic_fallback": "⚠️ Heuristic fallback (low confidence detection)",
                "llm": "LLM-assisted refinement",
                "cached": "Loaded from cache",
            }.get(rp.profile_source, rp.profile_source)
            lines.append(f"**Profile Source**: {source_desc}")

        # Add low confidence warning
        if rp.detection_confidence is not None and rp.detection_confidence < 0.6:
            lines.append("")
            lines.append("> ⚠️ **Low Detection Confidence**: Layout inference may be unreliable. "
                        "Consider providing an explicit `repo_profile` to ensure correct file placement.")
            lines.append(f"**Profile Source**: {source_desc}")

        if rp.detection_evidence:
            lines.append("")
            lines.append("### Detection Evidence")
            for evidence in rp.detection_evidence[:5]:
                lines.append(f"- {evidence}")
            if len(rp.detection_evidence) > 5:
                lines.append(f"- ... and {len(rp.detection_evidence) - 5} more")

        lines.append("")
        lines.append("### Layout Configuration")
        lines.append(f"- Integrations root: `{rp.integrations_root}`")
        lines.append(f"- Tests root: `{rp.tests_root}`")

        if rp.layout_hints:
            if rp.layout_hints.get("clients_dir"):
                lines.append(f"- Clients dir: `{rp.layout_hints['clients_dir']}`")
            if rp.layout_hints.get("flows_dir"):
                lines.append(f"- Flows/Services dir: `{rp.layout_hints['flows_dir']}`")
            if rp.layout_hints.get("llm_reasoning"):
                lines.append(f"- LLM reasoning: {rp.layout_hints['llm_reasoning']}")

        if rp.integration_hooks:
            lines.append("")
            lines.append("### Integration Hooks")
            if rp.integration_hooks.get("router_file"):
                lines.append(f"- Router file: `{rp.integration_hooks['router_file']}`")
            if rp.integration_hooks.get("settings_file"):
                lines.append(f"- Settings file: `{rp.integration_hooks['settings_file']}`")

        lines.append("")

    # Persistence
    if state.persisted_ids:
        lines.append("## Persistence")
        status = state.persisted_ids.get("run_status", "unknown")
        lines.append(f"**Status**: {status}")
        if "would_persist" in state.persisted_ids:
            lines.append("*(Dry run - no actual persistence)*")
        lines.append("")

    # Errors
    if state.errors:
        lines.append("## Errors")
        for error in state.errors:
            lines.append(f"- {error}")
        lines.append("")

    # Completion
    lines.append("## Completed Steps")
    for step in state.completed_steps:
        lines.append(f"- {step}")
    lines.append("")

    # Node Timings (for observability - shows non-LLM nodes do work)
    if state.node_timings:
        lines.append("## Node Timings (Internal)")
        lines.append("*Shows execution time for non-LLM nodes that may appear as \"0.00s\" in LangSmith.*")
        lines.append("")
        # Sort by execution order (roughly by completion in timings dict)
        for name, ms in state.node_timings.items():
            lines.append(f"- **{name}**: {ms:.2f} ms")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by Integration Co-Worker*")

    state.report_markdown = "\n".join(lines)
    state.completed_steps.append("build_report")
    return state


def _build_structured_metrics(state: WorkflowState) -> str:
    """Build a structured metrics string for LLM context."""
    return f"""
Endpoints: {len(state.endpoints)}
Schemas: {len(state.schemas)}
Entities: {len(state.entities)}
Workflow Nodes: {len(state.workflow_nodes)}
Code Artifacts: {len(state.code_artifacts)}
Errors: {len(state.errors)}
"""
