"""
build_report node - Generate a human-readable markdown report.

Uses structured template, with optional LLM enhancement for executive summary.

V1.1 FT-014: Enhanced Reporting
  - Added Mermaid workflow diagrams showing node flow and API call paths
  - Diagrams render in GitHub, GitLab, and markdown viewers with Mermaid support
"""
import logging
from integration_coworker.graph.state import WorkflowState
from integration_coworker.llm import call_llm_for_node, call_llm_async_for_node

logger = logging.getLogger(__name__)


def _generate_workflow_mermaid(state: WorkflowState) -> str:
    """
    V1.1 FT-014: Generate a Mermaid flowchart from workflow nodes and edges.
    
    Returns a Mermaid diagram string that can be embedded in markdown.
    The diagram shows:
    - Workflow nodes with their types (different shapes)
    - Edges showing the flow between nodes
    - Special styling for api_call nodes
    
    Returns:
        Mermaid flowchart code block
    """
    if not state.workflow_nodes:
        return ""
    
    lines = ["```mermaid", "flowchart TD"]
    
    # Node type to Mermaid shape mapping
    node_shapes = {
        "start": "([{label}])",       # Stadium shape for start
        "end": "([{label}])",         # Stadium shape for end
        "api_call": "[/{label}/]",    # Parallelogram for API calls
        "validation": "{{{label}}}",  # Rhombus/Diamond for validation
        "transform": "[{label}]",     # Rectangle for transform
        "pagination": "[{label}]",    # Rectangle for pagination
    }
    
    # Generate node definitions
    for node in state.workflow_nodes:
        node_key = node.node_key
        label = node.config.get("label", node_key) if node.config else node_key
        node_type = node.node_type
        
        # Sanitize label for Mermaid (escape quotes and special chars)
        label = label.replace('"', "'").replace("[", "(").replace("]", ")")
        
        # Get shape or default to rectangle
        shape_template = node_shapes.get(node_type, "[{label}]")
        shape = shape_template.format(label=label)
        
        lines.append(f"    {node_key}{shape}")
    
    # Generate edges
    if state.workflow_edges:
        for edge in state.workflow_edges:
            from_key = edge.from_node_key
            to_key = edge.to_node_key
            
            if edge.condition:
                # Edge with label
                lines.append(f"    {from_key} -->|{edge.condition}| {to_key}")
            else:
                lines.append(f"    {from_key} --> {to_key}")
    else:
        # If no edges defined, create linear flow based on position
        sorted_nodes = sorted(state.workflow_nodes, key=lambda n: n.position)
        for i in range(len(sorted_nodes) - 1):
            lines.append(f"    {sorted_nodes[i].node_key} --> {sorted_nodes[i + 1].node_key}")
    
    # Add styling for different node types
    lines.append("")
    lines.append("    %% Styling")
    
    # Collect nodes by type for styling
    api_call_nodes = [n.node_key for n in state.workflow_nodes if n.node_type == "api_call"]
    start_end_nodes = [n.node_key for n in state.workflow_nodes if n.node_type in ("start", "end")]
    validation_nodes = [n.node_key for n in state.workflow_nodes if n.node_type == "validation"]
    
    if api_call_nodes:
        lines.append(f"    style {' '.join(api_call_nodes)} fill:#e1f5fe,stroke:#0288d1")
    if start_end_nodes:
        lines.append(f"    style {' '.join(start_end_nodes)} fill:#e8f5e9,stroke:#388e3c")
    if validation_nodes:
        lines.append(f"    style {' '.join(validation_nodes)} fill:#fff3e0,stroke:#f57c00")
    
    lines.append("```")
    
    return "\n".join(lines)


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


def _add_what_i_did_section(lines: list, state: WorkflowState) -> None:
    """
    V4 Observability: Add plain-English "What I Did" summary.
    
    Explains each major step in user-friendly language, derived from state.
    """
    lines.append("## What the Coworker Did")
    lines.append("")
    
    step_num = 1
    
    # 1. Spec reading
    if state.spec_documents:
        spec_names = [doc.uri.split("/")[-1] if "/" in doc.uri else doc.uri for doc in state.spec_documents[:3]]
        spec_list = ", ".join(f"`{name}`" for name in spec_names)
        lines.append(f"**{step_num}. Read your API spec** ({spec_list})")
        
        # Details
        details = []
        if state.endpoints:
            details.append(f"Found {len(state.endpoints)} endpoints")
        if state.schemas:
            schema_names = [s.name for s in state.schemas[:3]]
            details.append(f"{len(state.schemas)} data models ({', '.join(schema_names)}{'...' if len(state.schemas) > 3 else ''})")
        if state.openapi_spec:
            spec_version = state.openapi_spec.get("openapi", state.openapi_spec.get("swagger", "unknown"))
            details.append(f"Identified as OpenAPI {spec_version}")
        
        for detail in details:
            lines.append(f"   - {detail}")
        lines.append("")
        step_num += 1
    
    # 2. Task understanding
    if state.integration_task:
        task = state.integration_task
        lines.append(f"**{step_num}. Understood your task** (\"{state.task_description[:50]}{'...' if len(state.task_description) > 50 else ''}\")")
        
        if task.task_slug:
            # Extract action from task_slug (e.g., "create_checkout_session" -> "CREATE")
            action = task.task_slug.split("_")[0].upper() if "_" in task.task_slug else "PROCESS"
            lines.append(f"   - Matched to pattern: **{action}** operation")
        
        if state.endpoint_bindings:
            primary_binding = state.endpoint_bindings[0]
            # Look up endpoint path from endpoints list (EndpointBinding only has endpoint_id)
            endpoint_display = None
            for ep in state.endpoints:
                # Endpoint uses 'id' field, EndpointBinding uses 'endpoint_id'
                ep_id = getattr(ep, 'id', None) or getattr(ep, 'endpoint_id', None)
                if ep_id and str(ep_id) == str(primary_binding.endpoint_id):
                    endpoint_display = f"{ep.method} {ep.path}"
                    break
            if endpoint_display:
                lines.append(f"   - Primary endpoint: `{endpoint_display}`")
            elif primary_binding.endpoint_id:
                lines.append(f"   - Primary endpoint ID: `{primary_binding.endpoint_id}`")
        
        lines.append("")
        step_num += 1
    
    # 3. Workflow design
    if state.workflow_nodes:
        lines.append(f"**{step_num}. Designed the integration**")
        
        # Describe workflow
        node_types = [n.node_type for n in state.workflow_nodes]
        workflow_desc = " → ".join([n.node_type for n in sorted(state.workflow_nodes, key=lambda x: x.position)][:5])
        lines.append(f"   - Created {len(state.workflow_nodes)}-step workflow: {workflow_desc}")
        
        # Policies
        if state.policies:
            policy_types = list(set(
                p.policy_type.value if hasattr(p.policy_type, 'value') else str(p.policy_type) 
                for p in state.policies
            ))
            lines.append(f"   - Applied {len(state.policies)} policies: {', '.join(policy_types[:3])}")
        
        lines.append("")
        step_num += 1
    
    # 4. Code generation
    if state.code_artifacts:
        lines.append(f"**{step_num}. Generated code**")
        
        for artifact in state.code_artifacts:
            # Describe each artifact type
            if artifact.artifact_type == "client":
                class_match = artifact.content.find("class ")
                if class_match >= 0:
                    class_line = artifact.content[class_match:class_match+50].split("\n")[0]
                    class_name = class_line.replace("class ", "").split("(")[0].split(":")[0].strip()
                    lines.append(f"   - Client class: `{class_name}` (handles auth, retries)")
                else:
                    lines.append(f"   - Client: `{artifact.rel_path}`")
            elif artifact.artifact_type == "workflow":
                lines.append(f"   - Workflow function: `{artifact.rel_path.split('/')[-1].replace('.py', '')}`")
            elif artifact.artifact_type == "test":
                # Count test functions
                test_count = artifact.content.count("def test_")
                lines.append(f"   - Unit tests: {test_count} test cases covering success + error paths")
        
        lines.append("")
        step_num += 1
    
    # 5. Repo changes (if any)
    if state.repo_changes and state.repo_changes.changes:
        lines.append(f"**{step_num}. Updated your repository**")
        created = state.repo_changes.files_created()
        updated = state.repo_changes.files_updated()
        if created:
            lines.append(f"   - Created {len(created)} new file(s)")
        if updated:
            lines.append(f"   - Updated {len(updated)} existing file(s)")
        lines.append("")
        step_num += 1
    
    # Show if nothing was done (error case)
    if step_num == 1:
        lines.append("*No significant steps completed - check errors below.*")
        lines.append("")


async def build_report(state: WorkflowState) -> WorkflowState:
    """
    Reads: All state fields
    Writes: report_markdown
    
    Generates structured report, with optional LLM-enhanced executive summary.
    
    V3.1: Async implementation for concurrent execution.
    """
    lines = []

    lines.append("# Integration Co-Worker Report")
    lines.append("")

    # Header
    lines.append(f"**Run ID**: `{state.run_id or 'N/A'}`")
    lines.append(f"**Provider**: `{state.provider_code or 'unknown'}`")
    lines.append(f"**Task**: {state.task_description or 'N/A'}")
    lines.append("")

    # V2.1: Degraded mode warning (Section 13.7)
    if state.degraded_mode:
        lines.append("## ⚠️ Degraded Mode Warning")
        lines.append("")
        lines.append("> **This run completed in degraded mode.** Some features may be limited or use fallback behavior.")
        lines.append("")
        if state.degraded_reason:
            lines.append(f"**Reason**: {state.degraded_reason}")
        lines.append("")
        lines.append("**Impact**:")
        lines.append("- Task understanding may be less accurate (heuristic fallback)")
        lines.append("- Workflow planning may use simpler patterns")
        lines.append("- Generated code may require additional review")
        lines.append("")
        if state.llm_fallbacks:
            lines.append("**Fallbacks Used**:")
            for fallback in state.llm_fallbacks:
                node = fallback.get("node", "unknown")
                reason = fallback.get("reason", "LLM unavailable")
                lines.append(f"- `{node}`: {reason}")
            lines.append("")
        # Add a horizontal rule to separate from normal content
        lines.append("---")
        lines.append("")

    # V2.1: Also warn about skipped nodes
    if hasattr(state, 'skipped_nodes') and state.skipped_nodes:
        lines.append("## ⚠️ Skipped Nodes")
        lines.append("")
        lines.append("> Some workflow nodes were skipped due to upstream failures.")
        lines.append("")
        for node in state.skipped_nodes:
            lines.append(f"- `{node}`")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Try to generate LLM executive summary
    try:
        structured_report = _build_structured_metrics(state)
        summary_prompt = _build_summary_prompt(structured_report, state)
        summary = await call_llm_async_for_node("build_report", summary_prompt)

        # Check if we got a real summary (not mock placeholder)
        if summary and len(summary) > 20 and not summary.startswith("Mock response"):
            lines.append("## Executive Summary")
            lines.append(summary.strip())
            lines.append("")
            logger.info("Generated LLM executive summary")
    except Exception as e:
        logger.debug(f"Skipping LLM summary: {e}")

    # V4 Observability: "What I Did" section - plain English summary
    _add_what_i_did_section(lines, state)

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

    # V1.1 FT-014: Add Mermaid workflow diagram
    mermaid_diagram = _generate_workflow_mermaid(state)
    if mermaid_diagram:
        lines.append("### Workflow Diagram")
        lines.append("")
        lines.append(mermaid_diagram)
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
                "config_file": "✅ Loaded from `.integration-coworker.yaml` config file",
                "llm_inference": "🤖 LLM-generated config (saved to `.integration-coworker.yaml`)",
                "archetype": "Used archetype defaults (high confidence)",
                "archetype+heuristic": "Archetype with heuristic refinement",
                "heuristic": "Inferred via heuristics (no archetype match)",
                "heuristic_fallback": "⚠️ Heuristic fallback (low confidence detection)",
                "llm": "LLM-assisted refinement",
                "cached": "Loaded from cache",
            }.get(rp.profile_source, rp.profile_source)
            lines.append(f"**Profile Source**: {source_desc}")

            # Prompt user to review LLM-generated config
            if rp.profile_source == "llm_inference":
                lines.append("")
                lines.append("> 📝 **Action Required**: The LLM generated a `.integration-coworker.yaml` config file in your repo root. "
                           "Please review this file to ensure the detected layout and conventions match your project structure. "
                           "You can edit it to customize where integration code is placed.")

        # Add low confidence warning
        if rp.detection_confidence is not None and rp.detection_confidence < 0.6:
            lines.append("")
            lines.append("> ⚠️ **Low Detection Confidence**: Layout inference may be unreliable. "
                        "Consider providing an explicit `repo_profile` to ensure correct file placement.")

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

    # Errors - V4 Observability: Enriched error display with suggestions
    if state.errors:
        lines.append("## Errors")
        lines.append("")
        for i, error in enumerate(state.errors, 1):
            if hasattr(error, 'severity') and hasattr(error, 'message'):
                lines.append(f"{i}. [{error.severity}] {error.phase}: {error.message}")
            else:
                lines.append(f"{i}. {error}")
        lines.append("")

    # V4 Observability: Warnings section (non-fatal issues)
    if state.warnings:
        lines.append("## Warnings")
        lines.append("*Non-fatal issues encountered during the run:*")
        lines.append("")
        for warning in state.warnings:
            lines.append(f"- ⚠️ {warning}")
        lines.append("")

    # Completion
    lines.append("## Completed Steps")
    for step in state.completed_steps:
        lines.append(f"- {step}")
    lines.append("")

    # V4 Observability: LLM Token Usage
    if state.llm_token_usage and state.llm_token_usage.get("total_tokens", 0) > 0:
        lines.append("## LLM Usage")
        prompt_tokens = state.llm_token_usage.get("prompt_tokens", 0)
        completion_tokens = state.llm_token_usage.get("completion_tokens", 0)
        total_tokens = state.llm_token_usage.get("total_tokens", 0)
        
        lines.append(f"- **Prompt tokens**: {prompt_tokens:,}")
        lines.append(f"- **Completion tokens**: {completion_tokens:,}")
        lines.append(f"- **Total tokens**: {total_tokens:,}")
        
        # Estimate cost (rough approximation for GPT-4)
        # GPT-4: ~$0.03/1K prompt, ~$0.06/1K completion
        # GPT-4o-mini: ~$0.00015/1K prompt, ~$0.0006/1K completion
        estimated_cost_gpt4 = (prompt_tokens * 0.03 + completion_tokens * 0.06) / 1000
        estimated_cost_mini = (prompt_tokens * 0.00015 + completion_tokens * 0.0006) / 1000
        lines.append(f"- **Estimated cost**: ${estimated_cost_mini:.4f} (gpt-4o-mini) to ${estimated_cost_gpt4:.4f} (gpt-4)")
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

    # V4 Observability: LLM Fallbacks section (even if not in degraded mode)
    if state.llm_fallbacks and not state.degraded_mode:
        # Only show if not already shown in degraded mode section
        lines.append("## LLM Fallbacks")
        lines.append("*Some LLM calls used fallback behavior:*")
        lines.append("")
        for fallback in state.llm_fallbacks:
            node = fallback.get("node", "unknown")
            reason = fallback.get("reason", "LLM unavailable")
            fallback_type = fallback.get("type", "heuristic")
            lines.append(f"- **{node}**: {reason} → used `{fallback_type}` fallback")
        lines.append("")

    # V4 Observability: Run Journey (derived from completed_steps + errors + timings)
    # Bug #82 Fix: Do NOT add persist_run_outcome to completed_steps here!
    # The timed_node wrapper checks completed_steps to skip already-run nodes,
    # so pre-adding persist_run_outcome causes it to be skipped entirely.
    # Only add build_report (which is completing now) for accurate progress display.
    state.completed_steps.append("build_report")
    # Note: persist_run_outcome will add itself after it actually runs
    
    # For visualization purposes, we calculate completed + pending separately
    visualization_completed = list(state.completed_steps) + ["persist_run_outcome"]
    
    lines.append("## Run Journey")
    lines.append("")
    _add_journey_visualization(lines, state, include_pending=["persist_run_outcome"])
    lines.append("")

    lines.append("---")
    lines.append("*Generated by Integration Co-Worker*")

    state.report_markdown = "\n".join(lines)
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


# V4 Observability: Journey visualization helpers

# Mapping of node names to user-friendly labels and emojis
# Note: Only includes nodes that are actually part of the workflow graph edges.
# persist_results is a legacy node not connected to the graph.
_NODE_JOURNEY_INFO = {
    "plan_run": ("🎯", "Plan Run", "Initialized run configuration"),
    "ingest_spec": ("📄", "Ingest Spec", "Loaded and chunked API specification"),
    "detect_and_parse_spec": ("🔍", "Parse Spec", "Identified spec format and parsed structure"),
    "build_silver_api_model": ("📊", "Build API Model", "Extracted endpoints, schemas, entities"),
    "embed_spec_chunks": ("🧬", "Embed Chunks", "Generated embeddings for semantic search"),
    "persist_silver_checkpoint": ("💾", "Save Silver", "Persisted Silver model to database"),
    "understand_task": ("🧠", "Understand Task", "Analyzed task intent with LLM"),
    "align_task_with_kg": ("🔗", "Align with KG", "Matched task to knowledge graph patterns"),
    "plan_integration_flow": ("📋", "Plan Flow", "Designed integration workflow"),
    "attach_policies_and_patterns": ("🛡️", "Apply Policies", "Attached retry, timeout, error handling"),
    "attach_repo_context": ("📁", "Repo Context", "Analyzed target repository structure"),
    "generate_code_and_tests": ("⚡", "Generate Code", "Created client, workflow, and tests"),
    "analyze_repo_layout": ("🗂️", "Analyze Layout", "Determined file placement strategy"),
    "apply_repo_integration_changes": ("✏️", "Apply Changes", "Wrote files to repository"),
    "validate_integration_design": ("✅", "Validate", "Verified generated code"),
    "persist_gold_checkpoint": ("💾", "Save Gold", "Persisted Gold model to database"),
    "persist_kg_learning": ("🧠", "Save Learning", "Updated knowledge graph"),
    "build_report": ("�", "Build Report", "Generated this report"),
    "persist_run_outcome": ("📊", "Save Outcome", "Recorded run status"),
}


def _add_journey_visualization(lines: list, state: WorkflowState, include_pending: list = None) -> None:
    """
    V4 Observability: Add visual journey representation.
    
    Shows progress through workflow with status indicators:
    - ✅ Completed successfully
    - ⚠️ Completed with warnings
    - ❌ Failed
    - ⏭️ Skipped
    
    Args:
        lines: Output lines to append to
        state: Workflow state
        include_pending: Nodes to treat as "will complete" for visualization (Bug #82 fix)
    """
    # Bug #82 Fix: Create a combined set for visualization that includes pending nodes
    # but don't modify state.completed_steps directly
    completed = set(state.completed_steps)
    if include_pending:
        completed.update(include_pending)
    
    errors_in_nodes = set()
    
    # Try to identify which nodes had errors
    for error in state.errors:
        # Errors often mention node names
        for node_name in _NODE_JOURNEY_INFO.keys():
            if node_name in error.lower() or node_name.replace("_", " ") in error.lower():
                errors_in_nodes.add(node_name)
    
    # Nodes with fallbacks
    fallback_nodes = set()
    for fb in state.llm_fallbacks:
        fallback_nodes.add(fb.get("node", ""))
    
    # Build the journey line with emojis
    journey_emojis = []
    for node_name in _NODE_JOURNEY_INFO.keys():
        emoji, label, _ = _NODE_JOURNEY_INFO[node_name]
        if node_name in completed:
            if node_name in errors_in_nodes:
                journey_emojis.append(f"❌")
            elif node_name in fallback_nodes:
                journey_emojis.append(f"⚠️")
            else:
                journey_emojis.append(f"✅")
        elif node_name in errors_in_nodes:
            journey_emojis.append(f"❌")
        else:
            journey_emojis.append(f"⬜")  # Not yet run
    
    # Progress bar
    total_nodes = len(_NODE_JOURNEY_INFO)
    completed_count = len([n for n in _NODE_JOURNEY_INFO.keys() if n in completed])
    progress_pct = int((completed_count / total_nodes) * 100) if total_nodes > 0 else 0
    
    filled = int(progress_pct / 5)  # 20 chars total
    progress_bar = "█" * filled + "░" * (20 - filled)
    
    # Status summary
    error_count = len(state.errors)
    fallback_count = len(state.llm_fallbacks)
    
    if error_count > 0:
        status_text = f"❌ Completed with {error_count} error(s)"
    elif fallback_count > 0:
        status_text = f"⚠️ Completed with {fallback_count} fallback(s)"
    elif completed_count == total_nodes:
        status_text = "✅ All steps completed successfully"
    else:
        status_text = f"🔄 {completed_count}/{total_nodes} steps completed"
    
    lines.append(f"**Progress**: {progress_bar} {progress_pct}%")
    lines.append(f"**Status**: {status_text}")
    lines.append("")
    
    # Visual journey (compact)
    lines.append("```")
    lines.append(" → ".join(journey_emojis[:6]))  # First 6 nodes
    if len(journey_emojis) > 6:
        lines.append(" → ".join(journey_emojis[6:12]))  # Middle nodes
    if len(journey_emojis) > 12:
        lines.append(" → ".join(journey_emojis[12:]))  # Remaining nodes
    lines.append("```")
    lines.append("")
    
    # Legend
    lines.append("*Legend: ✅ Success | ⚠️ Fallback | ❌ Error | ⬜ Not run*")
