"""
CLI entrypoint for the integration coworker.

Thin wrapper over design_and_generate_integration per design spec Section 4.4.

Supports:
- Multiple --spec-ref options for multi-spec runs
- Demo mode with pre-configured examples
- Postgres persistence (set DATABASE_URL) or SQLite fallback (USE_SQLITE=true)
- Real LLM calls (set OPENAI_API_KEY) or mock mode (USE_MOCK_LLM=true)
- Verbose mode for debugging (--verbose or -v)
"""
import typer
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

# Load .env file if present (for local development)
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not installed, rely on environment variables

from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions
from integration_coworker.config import get_settings, reset_settings

# Configure logging
_logger = logging.getLogger("integration_coworker")


def _setup_logging(verbose: bool = False) -> None:
    """
    Configure logging based on verbose flag.
    
    Args:
        verbose: If True, enable DEBUG level logging with detailed format.
                 If False, only WARNING and above.
    """
    level = logging.DEBUG if verbose else logging.WARNING

    # Create handler with appropriate format
    handler = logging.StreamHandler(sys.stderr)
    if verbose:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S"
        )
    else:
        formatter = logging.Formatter("%(levelname)s: %(message)s")

    handler.setFormatter(formatter)

    # Configure root logger for integration_coworker
    root_logger = logging.getLogger("integration_coworker")
    root_logger.handlers = []
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Also configure langchain/langgraph loggers in verbose mode
    if verbose:
        for logger_name in ["langchain", "langgraph", "openai", "httpx"]:
            lg = logging.getLogger(logger_name)
            lg.setLevel(logging.INFO)
            lg.addHandler(handler)


# =============================================================================
# Auto-Resume Helper Functions (V2)
# =============================================================================

def _try_resume_run(
    run_id: str,
    json_output: bool,
    verbose: bool,
) -> Optional[str]:
    """
    Try to resume a specific run by ID.
    
    Returns run_id if checkpoint exists and is resumable, None otherwise.
    """
    try:
        from integration_coworker.persistence.checkpoints import load_checkpoint, get_completed_nodes
        
        checkpoint = load_checkpoint(run_id)
        if checkpoint:
            completed = get_completed_nodes(run_id)
            if completed and verbose:
                _logger.debug(f"Found checkpoint for run {run_id} with {len(completed)} completed nodes")
            return run_id
        else:
            if not json_output:
                typer.echo(f"⚠ No checkpoint found for run {run_id}", err=True)
            return None
    except Exception as e:
        if verbose:
            _logger.debug(f"Could not check checkpoint for {run_id}: {e}")
        return None


def _try_auto_resume(
    provider_code: Optional[str],
    spec_refs: List[str],
    json_output: bool,
    verbose: bool,
) -> Optional[str]:
    """
    Try to auto-detect an interrupted run to resume.
    
    Looks for the most recent incomplete run that matches:
    - Same provider_code (if provided)
    - Same spec_refs
    
    Returns run_id if found, None otherwise.
    """
    try:
        from integration_coworker.persistence.db import get_connection, get_engine_type
        from integration_coworker.persistence.checkpoints import get_completed_nodes
        from integration_coworker.graph.runtime import WORKFLOW_NODE_ORDER
        
        engine = get_engine_type()
        
        # Find recent incomplete runs
        # Bug #61 fix: Query checkpoints directly for provider_code (run_status.task_id may be NULL)
        # Checkpoints store provider_code in state_json which is always populated
        if engine == "postgres":
            from integration_coworker.persistence.postgres import get_connection as pg_get_connection
            
            with pg_get_connection() as conn:
                with conn.cursor() as cur:
                    # Find runs that are incomplete (status = 'running' or no persist_run_outcome)
                    # Use checkpoints to get provider_code since run_status.task_id may be NULL
                    if provider_code:
                        cur.execute("""
                            SELECT DISTINCT rs.run_id, rs.started_at
                            FROM integration_gold.run_status rs
                            JOIN integration_gold.run_checkpoints rc_plan 
                                ON rs.run_id = rc_plan.run_id AND rc_plan.node_name = 'plan_run'
                            WHERE rs.status = 'running'
                            AND rc_plan.state_json->>'provider_code' = %s
                            AND NOT EXISTS (
                                SELECT 1 FROM integration_gold.run_checkpoints rc
                                WHERE rc.run_id = rs.run_id
                                AND rc.node_name = 'persist_run_outcome'
                            )
                            ORDER BY rs.started_at DESC
                            LIMIT 5
                        """, (provider_code,))
                    else:
                        cur.execute("""
                            SELECT rs.run_id, rs.started_at
                            FROM integration_gold.run_status rs
                            WHERE rs.status = 'running'
                            AND NOT EXISTS (
                                SELECT 1 FROM integration_gold.run_checkpoints rc
                                WHERE rc.run_id = rs.run_id
                                AND rc.node_name = 'persist_run_outcome'
                            )
                            ORDER BY rs.started_at DESC
                            LIMIT 5
                        """)
                    
                    rows = cur.fetchall()
                    
                    if rows:
                        # Return the most recent incomplete run
                        run_id = rows[0][0]
                        if verbose:
                            _logger.debug(f"Found {len(rows)} incomplete run(s), most recent: {run_id}")
                        if not json_output:
                            typer.echo(f"🔍 Found interrupted run: {run_id}")
                        return run_id
        else:
            # SQLite fallback - Bug #61 fix: Query checkpoints for provider_code
            conn = get_connection()
            cur = conn.cursor()
            
            if provider_code:
                cur.execute("""
                    SELECT DISTINCT rs.run_id, rs.started_at FROM run_status rs
                    JOIN run_checkpoints rc_plan ON rs.run_id = rc_plan.run_id
                    WHERE rs.status = 'running'
                    AND rc_plan.node_name = 'plan_run'
                    AND json_extract(rc_plan.state_json, '$.provider_code') = ?
                    AND rs.run_id NOT IN (
                        SELECT run_id FROM run_checkpoints
                        WHERE node_name = 'persist_run_outcome'
                    )
                    ORDER BY rs.started_at DESC
                    LIMIT 5
                """, (provider_code,))
            else:
                cur.execute("""
                    SELECT rs.run_id, rs.started_at FROM run_status rs
                    WHERE rs.status = 'running'
                    AND rs.run_id NOT IN (
                        SELECT run_id FROM run_checkpoints
                        WHERE node_name = 'persist_run_outcome'
                    )
                    ORDER BY rs.started_at DESC
                    LIMIT 5
                """)
            
            rows = cur.fetchall()
            conn.close()
            
            if rows:
                run_id = rows[0][0]
                if verbose:
                    _logger.debug(f"Found {len(rows)} incomplete run(s), most recent: {run_id}")
                if not json_output:
                    typer.echo(f"🔍 Found interrupted run: {run_id}")
                return run_id
        
        if verbose:
            _logger.debug("No incomplete runs found for auto-resume")
        return None
        
    except Exception as e:
        if verbose:
            _logger.debug(f"Auto-resume check failed: {e}")
        return None


def _handle_run_result(result, options, json_output: bool):
    """
    Handle the result of a run (normal or resumed).
    
    Extracted to avoid code duplication between normal and resumed runs.
    """
    # Check for errors in the result
    has_errors = False
    if result.report_markdown and "## Errors" in result.report_markdown:
        error_section = result.report_markdown.split("## Errors", 1)[1].split("##", 1)[0]
        if error_section.strip() and not error_section.strip().startswith("*(none)*"):
            has_errors = True

    if json_output:
        output = {
            "run_id": result.run_id,
            "provider_code": result.task.provider_code if result.task else None,
            "task_slug": result.task.task_slug if result.task else None,
            "code_artifacts": len(result.code_artifacts),
            "files_created": len([c for c in (result.repo_changes.changes if result.repo_changes else []) if c.change_type == "create"]),
            "files_updated": len([c for c in (result.repo_changes.changes if result.repo_changes else []) if c.change_type == "update"]),
            "dry_run": options.dry_run,
            "status": "completed_with_errors" if has_errors else "completed",
            "report_markdown": result.report_markdown
        }
        typer.echo(json.dumps(output, indent=2))
    else:
        status_icon = "⚠" if has_errors else "✓"
        typer.echo(f"\n{status_icon} Run completed: {result.run_id}")
        if result.task:
            typer.echo(f"  Provider: {result.task.provider_code}")
            typer.echo(f"  Task: {result.task.task_slug}")
        typer.echo(f"  Code artifacts: {len(result.code_artifacts)}")
        if result.repo_changes:
            files_created = len([c for c in result.repo_changes.changes if c.change_type == "create"])
            files_updated = len([c for c in result.repo_changes.changes if c.change_type == "update"])
            typer.echo(f"  Files created: {files_created}")
            typer.echo(f"  Files updated: {files_updated}")
            typer.echo(f"  Dry run: {'Yes' if options.dry_run else 'No'}")

        if has_errors:
            typer.echo("\n⚠ Completed with errors - check report for details")

        if result.report_markdown:
            typer.echo("\n--- Full Report ---\n")
            typer.echo(result.report_markdown)

    if has_errors:
        raise typer.Exit(code=1)


app = typer.Typer(
    name="integration-coworker",
    help="Agentic API Integration Designer & Code Generator",
    add_completion=False,
)


@app.command("run")
def run_integration(
    spec_ref: List[str] = typer.Option(
        ...,
        "--spec-ref",
        "-s",
        help="URL or path to an API specification. Can be specified multiple times for multi-spec runs.",
    ),
    task: str = typer.Option(..., "--task", "-t", help="Description of the integration task"),
    repo_root: Optional[Path] = typer.Option(None, "--repo-root", "-r", help="Path to the target repository"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Run without persisting to database"),
    provider_code: Optional[str] = typer.Option(None, "--provider", "-p", help="Override provider code"),
    policy_mode: str = typer.Option(
        "inline",
        "--policy-mode",
        "-m",
        help=(
            "Code generation style:\n"
            "  'inline' (default) - FULLY standalone code (~200 LOC), only needs httpx. "
            "No external dependencies beyond stdlib + httpx.\n"
            "  'runtime' - Thin clients (~30 LOC) using integration-coworker-runtime package. "
            "Must pip install integration-coworker-runtime in target project."
        ),
    ),
    standalone: bool = typer.Option(
        False,
        "--standalone",
        help="Alias for --policy-mode inline. Generate fully self-contained code with no runtime dependencies.",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="V1.1: Disable spec caching - always re-fetch and re-parse specs even if unchanged",
    ),
    strict_codegen: bool = typer.Option(
        False,
        "--strict-codegen",
        help="V1.1: Enable strict code generation mode - apply auto-formatting and fail on validation errors",
    ),
    constrained_codegen: bool = typer.Option(
        False,
        "--constrained-codegen",
        help="V2.2: Enable constrained code generation - inject paths/fixtures from spec rather than LLM generation (reduces hallucination)",
    ),
    auto_resume: bool = typer.Option(
        False,
        "--auto-resume",
        help="V2: Automatically resume from checkpoint if a previous run with same provider was interrupted",
    ),
    resume_run_id: Optional[str] = typer.Option(
        None,
        "--resume",
        help="V2: Resume a specific interrupted run by its run_id",
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose debug logging"),
):
    """
    Run the integration design and code generation workflow.
    
    Examples:
        # Single spec
        integration-coworker run --spec-ref https://api.example.com/openapi.yaml --task "Create checkout session"
        
        # Multiple specs
        integration-coworker run -s ./payments.yaml -s ./customers.yaml --task "Create payment with customer"
        
        # With repo integration
        integration-coworker run -s ./api.yaml -t "Add payment flow" -r ./my-project
        
        # With runtime-based code generation (V2.1)
        integration-coworker run -s ./api.yaml -t "Create payment" --policy-mode runtime
        
        # With verbose debug logging
        integration-coworker run -s ./api.yaml -t "Create payment" --verbose
        
        # Auto-resume if interrupted (V2)
        integration-coworker run -s ./api.yaml -t "Create payment" --auto-resume
        
        # Resume a specific run by ID (V2)
        integration-coworker run -s ./api.yaml -t "Create payment" --resume run-abc123
    """
    # Setup logging based on verbose flag
    _setup_logging(verbose)

    # V4 Observability: Show LangSmith trace URL early for real-time monitoring
    langsmith_enabled = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
    langsmith_project = os.getenv("LANGCHAIN_PROJECT", "default")
    if langsmith_enabled and not json_output:
        typer.echo(f"🔗 LangSmith: https://smith.langchain.com (project: {langsmith_project})")
        typer.echo("   Trace will appear once run starts...")
        typer.echo("")

    if verbose:
        _logger.debug(f"Starting integration run with {len(spec_ref)} spec(s)")
        _logger.debug(f"Task: {task}")
        _logger.debug(f"Dry run: {dry_run}")
        _logger.debug(f"Policy mode: {policy_mode}")
        _logger.debug(f"Standalone flag: {standalone}")
        _logger.debug(f"No cache: {no_cache}")
        _logger.debug(f"Strict codegen: {strict_codegen}")
        _logger.debug(f"Constrained codegen: {constrained_codegen}")

    # Handle --standalone as alias for --policy-mode inline
    if standalone:
        policy_mode = "inline"
        if verbose:
            _logger.debug("Standalone mode enabled - using inline policy mode")

    # Validate policy_mode
    if policy_mode not in ("inline", "runtime"):
        typer.echo(f"✗ Invalid --policy-mode: {policy_mode}. Must be 'inline' or 'runtime'.", err=True)
        raise typer.Exit(code=1)

    options = IntegrationOptions(
        dry_run=dry_run,
        repo_integration_enabled=repo_root is not None,
        override_provider_code=provider_code,
        policy_mode=policy_mode,  # V2.1 (GAP-02)
        no_cache=no_cache,  # V1.1 (FT-001)
        strict_codegen=strict_codegen,  # V1.1 (FT-008)
        constrained_codegen=constrained_codegen,  # V2.2 (Dynamic Capability Fix #7)
    )

    # V2: Auto-resume logic - check for interrupted runs
    resuming_from = None
    if resume_run_id:
        # Explicit resume request
        resuming_from = _try_resume_run(resume_run_id, json_output, verbose)
    elif auto_resume and not dry_run:
        # Auto-detect interrupted runs for this provider
        resuming_from = _try_auto_resume(provider_code, spec_ref, json_output, verbose)
    
    if resuming_from:
        # Resume from checkpoint
        try:
            from integration_coworker.persistence.checkpoints import load_checkpoint, get_completed_nodes
            from integration_coworker.graph.runtime import run_from_node, WORKFLOW_NODE_ORDER
            from integration_coworker.graph.state import WorkflowState
            from integration_coworker.api.types import IntegrationResult
            
            checkpoint_state = load_checkpoint(resuming_from)
            if checkpoint_state:
                completed = get_completed_nodes(resuming_from)
                
                if not json_output:
                    typer.echo(f"📂 Resuming run {resuming_from}")
                    typer.echo(f"   Completed nodes: {len(completed)}")
                    if completed:
                        typer.echo(f"   Last checkpoint: {completed[-1]}")
                    typer.echo("")
                
                # Find next node to run
                next_node = None
                for node in WORKFLOW_NODE_ORDER:
                    if node not in completed:
                        next_node = node
                        break
                
                if next_node:
                    if verbose:
                        _logger.debug(f"Resuming from node: {next_node}")
                    
                    # Update checkpoint state with new options if needed
                    checkpoint_state.options = options
                    
                    # Run from the next node with thread_id for LangGraph checkpointing
                    # Bug #61 Fix: Pass run_id as thread_id for checkpoint isolation
                    final_state = run_from_node(
                        checkpoint_state, 
                        next_node,
                        thread_id=resuming_from  # Use run_id as thread for checkpoint lookup
                    )
                    
                    # Build result from final state
                    result = IntegrationResult(
                        run_id=final_state.run_id or resuming_from,
                        task=final_state.integration_task,
                        code_artifacts=final_state.code_artifacts,
                        repo_changes=final_state.repo_changes,
                        report_markdown=final_state.report_markdown or "",
                        persisted_ids=final_state.persisted_ids,
                        endpoints=final_state.endpoints,
                        schemas=final_state.schemas,
                        entities=final_state.entities,
                        workflow_nodes=final_state.workflow_nodes,
                        workflow_edges=final_state.workflow_edges,
                        endpoint_bindings=final_state.endpoint_bindings,
                        policies=final_state.policies,
                        cache_hit=final_state.cache_hit,
                        spec_documents=final_state.spec_documents,
                        doc_chunks=final_state.doc_chunks,
                        plan=final_state.plan,
                        errors=final_state.errors,
                        completed_steps=final_state.completed_steps,
                        provider_code=final_state.provider_code,
                    )
                    
                    # Skip to result handling
                    return _handle_run_result(result, options, json_output)
                else:
                    if not json_output:
                        typer.echo(f"✓ Run {resuming_from} already completed - starting fresh run")
                        typer.echo("")
        except Exception as e:
            if not json_output:
                typer.echo(f"⚠ Could not resume run {resuming_from}: {e}", err=True)
                typer.echo("   Starting fresh run instead...", err=True)
                typer.echo("")
            if verbose:
                _logger.exception(f"Resume failed: {e}")

    try:
        result = design_and_generate_integration(
            spec_refs=list(spec_ref),  # Convert typer List to regular list
            task_description=task,
            provider_code=provider_code,
            repo_root=repo_root,
            options=options
        )
    except Exception as e:
        if json_output:
            error_output = {
                "error": str(e),
                "status": "failed"
            }
            typer.echo(json.dumps(error_output), err=True)
        else:
            typer.echo(f"✗ Error: {str(e)}", err=True)
        raise typer.Exit(code=1)

    # Use shared result handling (same as resumed runs)
    _handle_run_result(result, options, json_output)


@app.command("demo")
def run_demo(
    dry_run: bool = typer.Option(True, "--dry-run/--persist", help="Run in dry-run mode (default: true)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose debug logging"),
):
    """
    Run a demo integration with the built-in mock payments spec.
    
    This demonstrates the full workflow without requiring external API specs.
    Uses the mock_payments_openapi.yaml fixture bundled with the package.
    
    Examples:
        # Dry run demo (default)
        integration-coworker demo
        
        # Demo with database persistence
        integration-coworker demo --persist
        
        # With verbose logging
        integration-coworker demo --verbose
    """
    # Find the mock spec in fixtures - try multiple locations
    possible_paths = [
        # From src/integration_coworker/cli.py -> tests/fixtures/
        Path(__file__).parent.parent.parent / "tests" / "fixtures" / "mock_payments_openapi.yaml",
        # If running from project root
        Path.cwd() / "tests" / "fixtures" / "mock_payments_openapi.yaml",
        # If installed as package (relative to module)
        Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml",
    ]

    mock_spec = None
    for p in possible_paths:
        if p.exists():
            mock_spec = p.resolve()
            break

    if not mock_spec:
        typer.echo("✗ Error: Demo spec not found. Run from project root or check installation.", err=True)
        typer.echo(f"   Looked in: {[str(p) for p in possible_paths]}", err=True)
        raise typer.Exit(code=1)

    # V4 Observability: Show LangSmith info for demo
    langsmith_enabled = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
    langsmith_project = os.getenv("LANGCHAIN_PROJECT", "default")

    typer.echo("🚀 Running demo with mock payments API...")
    typer.echo(f"   Spec: {mock_spec}")
    typer.echo(f"   Mode: {'dry-run' if dry_run else 'persist to database'}")
    if langsmith_enabled:
        typer.echo(f"   🔗 LangSmith: https://smith.langchain.com (project: {langsmith_project})")
    typer.echo("")

    # Run the integration
    run_integration(
        spec_ref=[str(mock_spec.resolve())],
        task="Create a checkout session for payment processing",
        repo_root=None,
        dry_run=dry_run,
        provider_code=None,
        json_output=json_output,
        verbose=verbose,
    )


@app.command("demo-v1")
def demo_v1(
    openapi: Optional[Path] = typer.Option(None, "--openapi", "-o", help="Path to OpenAPI spec (default: mock_payments)"),
    repo: Optional[Path] = typer.Option(None, "--repo", "-r", help="Path to target repository for file writes"),
    task: Optional[str] = typer.Option(None, "--task", "-t", help="Task description (default: 'Create checkout session')"),
    dry_run: bool = typer.Option(True, "--dry-run/--persist", help="Dry run mode (default: true)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose debug logging"),
):
    """
    Golden Path demo - shows everything working end-to-end.
    
    This command demonstrates the full V1 capabilities:
    - Spec ingestion and parsing
    - Silver API model extraction
    - Task understanding and KG alignment
    - Code generation with policies
    - Repo integration (optional)
    - Per-node timing table
    - Artifact location summary
    
    Examples:
        # Quick demo with built-in spec
        integration-coworker demo-v1
        
        # Demo with your own OpenAPI spec
        integration-coworker demo-v1 --openapi ./api.yaml --task "Create order"
        
        # Demo with repo integration
        integration-coworker demo-v1 --repo ./my-project --persist
        
        # Demo with HTML spec (experimental)
        integration-coworker demo-v1 --openapi ./api-docs.html
    """
    import time
    from datetime import datetime
    
    _setup_logging(verbose)
    
    # Find spec
    if openapi and openapi.exists():
        spec_path = openapi.resolve()
    else:
        # Use default mock spec
        possible_paths = [
            Path(__file__).parent.parent.parent / "tests" / "fixtures" / "mock_payments_openapi.yaml",
            Path.cwd() / "tests" / "fixtures" / "mock_payments_openapi.yaml",
        ]
        spec_path = None
        for p in possible_paths:
            if p.exists():
                spec_path = p.resolve()
                break
        
        if not spec_path:
            typer.echo("✗ Error: No spec found. Use --openapi to specify one.", err=True)
            raise typer.Exit(code=1)
    
    task_desc = task or "Create checkout session"
    
    # Header
    typer.echo("")
    typer.echo("╔══════════════════════════════════════════════════════════════╗")
    typer.echo("║       Integration Co-Worker V1 - Golden Path Demo            ║")
    typer.echo("╚══════════════════════════════════════════════════════════════╝")
    typer.echo("")
    typer.echo(f"  📄 Spec:    {spec_path}")
    typer.echo(f"  📝 Task:    {task_desc}")
    if repo:
        typer.echo(f"  📁 Repo:    {repo}")
    typer.echo(f"  🔧 Mode:    {'Dry Run' if dry_run else 'Persist'}")
    typer.echo(f"  ⏱️  Started: {datetime.now().strftime('%H:%M:%S')}")
    typer.echo("")
    typer.echo("─" * 66)
    
    start_time = time.time()
    
    # Run integration
    options = IntegrationOptions(
        dry_run=dry_run,
        repo_integration_enabled=repo is not None,
    )
    
    try:
        result = design_and_generate_integration(
            spec_refs=[str(spec_path)],
            task_description=task_desc,
            repo_root=str(repo) if repo else None,
            options=options,
        )
    except Exception as e:
        typer.echo(f"\n✗ Error: {e}", err=True)
        raise typer.Exit(code=1)
    
    elapsed = time.time() - start_time
    
    # Results Summary
    typer.echo("\n📊 RESULTS SUMMARY")
    typer.echo("─" * 66)
    typer.echo(f"  Run ID:        {result.run_id}")
    typer.echo(f"  Provider:      {result.task.provider_code if result.task else 'N/A'}")
    typer.echo(f"  Task Slug:     {result.task.task_slug if result.task else 'N/A'}")
    typer.echo(f"  Total Time:    {elapsed:.2f}s")
    typer.echo("")
    
    # Silver Model
    typer.echo("  📦 Silver API Model:")
    typer.echo(f"      Endpoints:      {len(result.endpoints)}")
    typer.echo(f"      Schemas:        {len(result.schemas)}")
    typer.echo(f"      Entities:       {len(result.entities)}")
    if result.spec_documents:
        typer.echo(f"      Spec Documents: {len(result.spec_documents)}")
    typer.echo("")
    
    # Gold Model
    typer.echo("  🏆 Gold Integration Model:")
    typer.echo(f"      Workflow Nodes: {len(result.workflow_nodes)}")
    typer.echo(f"      Workflow Edges: {len(result.workflow_edges)}")
    typer.echo(f"      Bindings:       {len(result.endpoint_bindings)}")
    typer.echo("")
    
    # Code Artifacts
    typer.echo("  📝 Generated Artifacts:")
    for artifact in result.code_artifacts:
        size_kb = len(artifact.content) / 1024
        typer.echo(f"      [{artifact.artifact_type:6}] {artifact.rel_path} ({size_kb:.1f}KB)")
    typer.echo("")
    
    # Repo Changes (if applicable)
    if result.repo_changes and result.repo_changes.changes:
        typer.echo("  📁 Repo Changes:")
        for change in result.repo_changes.changes:
            icon = "+" if change.change_type == "create" else "~"
            typer.echo(f"      {icon} {change.rel_path}")
        typer.echo("")
    
    # Node Timings (extracted from report)
    if result.report_markdown and "Node Timings" in result.report_markdown:
        typer.echo("  ⏱️  Node Execution Times:")
        typer.echo("  ┌─────────────────────────────────┬───────────┐")
        typer.echo("  │ Node                            │ Time (ms) │")
        typer.echo("  ├─────────────────────────────────┼───────────┤")
        
        # Parse timings from report
        lines = result.report_markdown.split("\n")
        in_timing_section = False
        for line in lines:
            if "Node Timings" in line:
                in_timing_section = True
                continue
            if in_timing_section:
                if line.startswith("- **"):
                    # Parse "- **node_name**: 1.23 ms"
                    parts = line.split("**")
                    if len(parts) >= 3:
                        node_name = parts[1].strip()
                        time_part = parts[2].replace(":", "").strip()
                        typer.echo(f"  │ {node_name:<31} │ {time_part:>9} │")
                elif line.startswith("---") or line.startswith("#"):
                    break
        
        typer.echo("  └─────────────────────────────────┴───────────┘")
        typer.echo("")
    
    # LangSmith Link (if enabled)
    langsmith_enabled = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
    if langsmith_enabled:
        project = os.getenv("LANGCHAIN_PROJECT", "default")
        typer.echo("  🔗 LangSmith:")
        typer.echo(f"      Project: {project}")
        typer.echo(f"      URL: https://smith.langchain.com/o/default/projects/{project}")
        typer.echo("")
    
    # Completed Steps
    typer.echo("  ✅ Completed Steps:")
    steps_per_line = 4
    steps = result.completed_steps
    for i in range(0, len(steps), steps_per_line):
        chunk = steps[i:i+steps_per_line]
        typer.echo(f"      {', '.join(chunk)}")
    typer.echo("")
    
    # Errors/Warnings
    if result.errors:
        critical = [e for e in result.errors if "Warning:" not in e]
        warnings = [e for e in result.errors if "Warning:" in e]
        if critical:
            typer.echo("  ❌ Errors:")
            for e in critical[:3]:
                typer.echo(f"      • {e[:60]}")
        if warnings:
            typer.echo("  ⚠️  Warnings:")
            for w in warnings[:3]:
                typer.echo(f"      • {w[:60]}")
        typer.echo("")
    
    # Footer
    typer.echo("─" * 66)
    status_icon = "✅" if not result.errors or all("Warning:" in e for e in result.errors) else "⚠️"
    typer.echo(f"{status_icon} Demo completed in {elapsed:.2f}s")
    typer.echo("")
    
    # Next steps hint
    typer.echo("📚 Next Steps:")
    typer.echo("   • Run with --persist to save to database")
    typer.echo("   • Run with --repo ./path to write files")
    typer.echo("   • Run 'integration-coworker kg-dump' to inspect KG")
    typer.echo("   • Run 'integration-coworker health' to check system status")
    typer.echo("")


@app.command("status")
def show_status():
    """
    Show the current configuration and database status.
    
    Displays:
    - Database configuration (Postgres vs SQLite)
    - pgvector extension status (Postgres only)
    - LLM configuration (real vs mock)
    - Embedding configuration
    """
    # Reset settings to pick up fresh environment
    reset_settings()
    settings = get_settings()
    warnings = settings.validate()

    typer.echo("Integration Co-Worker Status")
    typer.echo("=" * 40)

    # Database
    typer.echo("\n📦 Database:")
    if settings.database.use_sqlite:
        typer.echo("   Engine: SQLite (test mode)")
        typer.echo(f"   Path: {settings.database.sqlite_path}")
    else:
        # Mask password in URL for display
        display_url = settings.database.url
        if "@" in display_url and ":" in display_url.split("@")[0]:
            parts = display_url.split("@")
            user_pass = parts[0].split("://")[1]
            if ":" in user_pass:
                user = user_pass.split(":")[0]
                display_url = f"postgresql://{user}:***@{parts[1]}"
        typer.echo("   Engine: PostgreSQL + pgvector")
        typer.echo(f"   URL: {display_url}")

        # Check Postgres connection and pgvector
        try:
            from integration_coworker.persistence.postgres import check_connection, check_pgvector
            if check_connection():
                typer.echo("   Connection: ✓ Connected")
                if check_pgvector():
                    typer.echo("   pgvector: ✓ Available")
                else:
                    typer.echo("   pgvector: ⚠ Not installed (run CREATE EXTENSION vector)")
            else:
                typer.echo("   Connection: ✗ Failed to connect")
        except ImportError:
            typer.echo("   Connection: ⚠ psycopg not installed")
            typer.echo("   Install: pip install 'psycopg[binary]' psycopg_pool")
        except Exception as e:
            typer.echo(f"   Connection: ✗ Error: {str(e)[:50]}")

    # LLM
    typer.echo("\n🤖 LLM:")
    if settings.llm.use_mock:
        typer.echo("   Mode: Mock (USE_MOCK_LLM=true)")
    elif settings.llm.api_key:
        typer.echo(f"   Mode: Real ({settings.llm.default_model})")
        typer.echo(f"   API Key: {settings.llm.api_key[:8]}...")
        if settings.llm.base_url:
            typer.echo(f"   Base URL: {settings.llm.base_url}")
    else:
        typer.echo("   Mode: ⚠ Not configured (set OPENAI_API_KEY)")

    # Embeddings
    typer.echo("\n📊 Embeddings:")
    typer.echo(f"   Model: {settings.embedding.model}")
    typer.echo(f"   Dimensions: {settings.embedding.dimensions}")
    typer.echo(f"   Batch Size: {settings.embedding.batch_size}")

    # Warnings
    if warnings:
        typer.echo("\n⚠ Warnings:")
        for w in warnings:
            typer.echo(f"   - {w}")
    else:
        typer.echo("\n✓ All systems configured for production use")

    typer.echo("")


@app.command("resume")
def resume_run_cmd(
    run_id: str = typer.Argument(..., help="Run ID to resume (from previous interrupted run)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
):
    """
    Resume an interrupted run from its last checkpoint.
    
    Use this command to continue a run that was interrupted (e.g., due to
    network issues, process crash, or manual cancellation). The run will
    resume from the last successfully completed node.
    
    To find incomplete runs, use 'integration-coworker status' or check
    the run_status table in the database.
    
    Examples:
        # Resume a specific run
        integration-coworker resume run_abc123
        
        # Resume with JSON output (for CI/CD)
        integration-coworker resume run_abc123 --json
        
        # Resume with verbose logging
        integration-coworker resume run_abc123 --verbose
    """
    _setup_logging(verbose)
    
    if verbose:
        _logger.debug(f"Attempting to resume run: {run_id}")
    
    # Check if checkpoint exists
    resumable = _try_resume_run(run_id, json_output, verbose)
    if not resumable:
        if json_output:
            typer.echo(json.dumps({"error": f"No checkpoint found for run {run_id}"}))
        else:
            typer.echo(f"✗ No checkpoint found for run: {run_id}", err=True)
            typer.echo("  The run may have completed or never started.", err=True)
            typer.echo("  Use 'integration-coworker status' to check run history.", err=True)
        raise typer.Exit(code=1)
    
    try:
        from integration_coworker.api.recovery import resume_run
        
        if not json_output:
            typer.echo(f"🔄 Resuming run: {run_id}")
            typer.echo("")
        
        result = resume_run(run_id)
        
        # Handle result using existing helper
        _handle_run_result(result, None, json_output)
        
        # Determine exit code based on errors
        has_errors = bool(result.errors) if hasattr(result, 'errors') else False
        if has_errors:
            raise typer.Exit(code=1)
            
    except ValueError as e:
        if json_output:
            typer.echo(json.dumps({"error": str(e)}))
        else:
            typer.echo(f"✗ Resume failed: {e}", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        if verbose:
            _logger.exception("Resume failed with unexpected error")
        if json_output:
            typer.echo(json.dumps({"error": str(e)}))
        else:
            typer.echo(f"✗ Resume failed: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("init-db")
def init_database(
    seed_kg: bool = typer.Option(True, "--seed-kg/--no-seed-kg", help="Seed Knowledge Graph with curated templates"),
):
    """
    Initialize the database schema and seed the Knowledge Graph.
    
    Creates all required tables for spec_silver, integration_gold, and repo_meta.
    Optionally seeds the Knowledge Graph with curated workflow templates (KG-002).
    Safe to run multiple times (uses CREATE IF NOT EXISTS).
    
    Requires:
    - For Postgres: DATABASE_URL set and Postgres running
    - For SQLite: USE_SQLITE=true
    
    Examples:
        # Initialize Postgres with KG seeding
        DATABASE_URL="postgresql://user:pass@localhost/db" integration-coworker init-db
        
        # Initialize without KG seeding
        integration-coworker init-db --no-seed-kg
        
        # Initialize SQLite (for testing)
        USE_SQLITE=true integration-coworker init-db
    """
    from integration_coworker.persistence.db import init_schema, get_engine_type
    from integration_coworker.persistence.seed_kg import seed_knowledge_graph

    # Reset settings to pick up fresh environment
    reset_settings()
    settings = get_settings()
    engine = get_engine_type()

    typer.echo("Database Schema Initialization")
    typer.echo("=" * 40)

    if engine == "sqlite":
        typer.echo("Mode: SQLite")
        typer.echo(f"Path: {settings.database.sqlite_path}")
    else:
        display_url = settings.database.url
        if "@" in display_url and ":" in display_url.split("@")[0]:
            parts = display_url.split("@")
            user_pass = parts[0].split("://")[1]
            if ":" in user_pass:
                user = user_pass.split(":")[0]
                display_url = f"postgresql://{user}:***@{parts[1]}"
        typer.echo("Mode: PostgreSQL + pgvector")
        typer.echo(f"URL: {display_url}")

    typer.echo("\nInitializing schema...")

    try:
        init_schema()
        typer.echo("✓ Schema initialized successfully!")

        # For Postgres, check pgvector
        if engine == "postgres":
            try:
                from integration_coworker.persistence.postgres import check_pgvector
                if check_pgvector():
                    typer.echo("✓ pgvector extension is available")
                else:
                    typer.echo("⚠ pgvector not found. Run: CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception as e:
                typer.echo(f"⚠ Could not check pgvector: {e}")

        # KG-002: Seed Knowledge Graph with curated templates
        if seed_kg:
            typer.echo("\nSeeding Knowledge Graph...")
            try:
                added, skipped = seed_knowledge_graph()
                if added > 0:
                    typer.echo(f"✓ Seeded {added} workflow templates")
                elif skipped > 0:
                    typer.echo(f"✓ Knowledge Graph already seeded ({skipped} templates exist)")
                else:
                    typer.echo("✓ Knowledge Graph is ready")
            except Exception as e:
                typer.echo(f"⚠ Could not seed Knowledge Graph: {e}")

    except RuntimeError as e:
        typer.echo(f"✗ Error: {e}", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"✗ Unexpected error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("health")
def health_check(
    check_llm: bool = typer.Option(False, "--check-llm", help="Test LLM connectivity with actual API calls"),
    perf_summary: bool = typer.Option(False, "--perf-summary", help="Include performance benchmark results"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed checks"),
):
    """
    Run health checks on all system components.
    
    Validates:
    - Database connection (Postgres or SQLite)
    - pgvector extension (Postgres only)
    - LLM configuration (API keys, archetypes)
    - LangSmith tracing configuration
    - Required Python packages
    
    Use --check-llm to make actual API calls to verify connectivity.
    Use --perf-summary to run quick performance benchmarks.
    
    Returns exit code 0 if healthy, 1 if any critical check fails.
    
    Examples:
        # Quick health check
        integration-coworker health
        
        # Test LLM connectivity with API calls
        integration-coworker health --check-llm
        
        # Include performance benchmarks
        integration-coworker health --perf-summary
        
        # Detailed output
        integration-coworker health --verbose
        
        # JSON output for scripting
        integration-coworker health --json
    """
    import time

    _setup_logging(verbose)
    reset_settings()
    settings = get_settings()

    checks = {
        "database": {"status": "unknown", "message": ""},
        "pgvector": {"status": "unknown", "message": ""},
        "llm": {"status": "unknown", "message": ""},
        "langsmith": {"status": "unknown", "message": ""},
        "archetypes": {"status": "unknown", "message": ""},
        "packages": {"status": "unknown", "message": ""},
    }

    # 1. Database check
    if settings.database.use_sqlite:
        checks["database"]["status"] = "ok"
        checks["database"]["message"] = f"SQLite at {settings.database.sqlite_path}"
        checks["pgvector"]["status"] = "skip"
        checks["pgvector"]["message"] = "Not applicable for SQLite"
    else:
        try:
            from integration_coworker.persistence.postgres import check_connection, check_pgvector
            if check_connection():
                checks["database"]["status"] = "ok"
                checks["database"]["message"] = "PostgreSQL connection successful"

                if check_pgvector():
                    checks["pgvector"]["status"] = "ok"
                    checks["pgvector"]["message"] = "pgvector extension available"
                else:
                    checks["pgvector"]["status"] = "warning"
                    checks["pgvector"]["message"] = "pgvector not installed (run CREATE EXTENSION vector)"
            else:
                checks["database"]["status"] = "error"
                checks["database"]["message"] = "Failed to connect to PostgreSQL"
                checks["pgvector"]["status"] = "skip"
                checks["pgvector"]["message"] = "Skipped (database not connected)"
        except ImportError:
            checks["database"]["status"] = "error"
            checks["database"]["message"] = "psycopg not installed"
            checks["pgvector"]["status"] = "skip"
            checks["pgvector"]["message"] = "Skipped (psycopg not installed)"
        except Exception as e:
            checks["database"]["status"] = "error"
            checks["database"]["message"] = f"Error: {str(e)[:100]}"
            checks["pgvector"]["status"] = "skip"
            checks["pgvector"]["message"] = "Skipped (database error)"

    # 2. LLM check (API keys)
    openai_key = os.getenv("OPENAI_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    if settings.llm.use_mock:
        checks["llm"]["status"] = "ok"
        checks["llm"]["message"] = "Mock LLM mode (USE_MOCK_LLM=true)"
    elif openai_key and anthropic_key:
        checks["llm"]["status"] = "ok"
        checks["llm"]["message"] = "Both OpenAI and Anthropic API keys configured"
    elif openai_key:
        checks["llm"]["status"] = "warning"
        checks["llm"]["message"] = "Only OpenAI configured (ANTHROPIC_API_KEY missing)"
    elif anthropic_key:
        checks["llm"]["status"] = "warning"
        checks["llm"]["message"] = "Only Anthropic configured (OPENAI_API_KEY missing)"
    else:
        checks["llm"]["status"] = "error"
        checks["llm"]["message"] = "No LLM API keys set"

    # 3. LangSmith check
    tracing_enabled = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
    langchain_key = os.getenv("LANGCHAIN_API_KEY", "") or os.getenv("LANGSMITH_API_KEY", "")

    if tracing_enabled and langchain_key:
        checks["langsmith"]["status"] = "ok"
        checks["langsmith"]["message"] = f"Enabled, project: {os.getenv('LANGCHAIN_PROJECT', 'default')}"
    elif tracing_enabled:
        checks["langsmith"]["status"] = "warning"
        checks["langsmith"]["message"] = "LANGCHAIN_TRACING_V2=true but no API key"
    else:
        checks["langsmith"]["status"] = "skip"
        checks["langsmith"]["message"] = "Tracing disabled (LANGCHAIN_TRACING_V2 not true)"

    # 4. Archetypes check
    try:
        from integration_coworker.config import list_available_archetypes, load_archetype, reset_archetype_cache

        reset_archetype_cache()
        archetypes = list_available_archetypes()

        # Check for provider/model mismatches
        mismatches = []
        for node_name in archetypes:
            arch = load_archetype(node_name)
            model_cfg = arch.get("model", {})
            provider = model_cfg.get("provider", "openai")
            model_name = model_cfg.get("name", "")

            if provider == "anthropic" and model_name.startswith("gpt-"):
                mismatches.append(f"{node_name}: anthropic+gpt-*")
            elif provider == "openai" and model_name.startswith("claude-"):
                mismatches.append(f"{node_name}: openai+claude-*")

        if mismatches:
            checks["archetypes"]["status"] = "error"
            checks["archetypes"]["message"] = f"Provider/model mismatch: {', '.join(mismatches[:2])}"
        else:
            checks["archetypes"]["status"] = "ok"
            checks["archetypes"]["message"] = f"{len(archetypes)} archetypes, all valid"
    except Exception as e:
        checks["archetypes"]["status"] = "error"
        checks["archetypes"]["message"] = f"Error loading: {str(e)[:50]}"

    # 5. Package check (including langchain-anthropic)
    required_packages = [
        ("typer", "typer"),
        ("yaml", "pyyaml"),
        ("langgraph", "langgraph"),
        ("openai", "openai"),
        ("langchain_anthropic", "langchain-anthropic"),
    ]
    missing = []
    for import_name, package_name in required_packages:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(package_name)

    if missing:
        checks["packages"]["status"] = "error"
        checks["packages"]["message"] = f"Missing: {', '.join(missing)}"
    else:
        checks["packages"]["status"] = "ok"
        checks["packages"]["message"] = f"All {len(required_packages)} required packages installed"

    # 6. Optional: Test LLM connectivity with actual API calls
    if check_llm:
        llm_connectivity = {}

        # Test OpenAI
        if openai_key:
            try:
                from langchain_openai import ChatOpenAI
                llm = ChatOpenAI(model="gpt-4o-mini", max_tokens=10)
                llm.invoke("Say 'OK'")
                llm_connectivity["openai"] = "ok"
            except Exception as e:
                llm_connectivity["openai"] = f"error: {str(e)[:40]}"
        else:
            llm_connectivity["openai"] = "skipped (no key)"

        # Test Anthropic
        if anthropic_key:
            try:
                from langchain_anthropic import ChatAnthropic
                llm = ChatAnthropic(model="claude-sonnet-4-5-20250929", max_tokens=10)
                llm.invoke("Say 'OK'")
                llm_connectivity["anthropic"] = "ok"
            except ImportError:
                llm_connectivity["anthropic"] = "error: langchain-anthropic not installed"
            except Exception as e:
                llm_connectivity["anthropic"] = f"error: {str(e)[:40]}"
        else:
            llm_connectivity["anthropic"] = "skipped (no key)"

        checks["llm_connectivity"] = {
            "status": "ok" if all(v == "ok" for v in llm_connectivity.values() if not v.startswith("skipped")) else "error",
            "message": f"OpenAI: {llm_connectivity.get('openai', 'n/a')}, Anthropic: {llm_connectivity.get('anthropic', 'n/a')}",
        }

    # 7. Performance Summary (optional)
    perf_results = {}
    if perf_summary:
        import random
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions as PerfOptions
        
        # Test state creation
        start = time.perf_counter()
        for _ in range(100):
            WorkflowState(
                run_id="perf-test",
                source_refs=["test.yaml"],
                spec_refs=["test.yaml"],
                task_description="Test",
                options=PerfOptions(),
                completed_steps=[],
            )
        state_time = (time.perf_counter() - start) * 10  # ms per creation
        perf_results["state_creation_ms"] = round(state_time, 2)
        
        # Test cosine similarity
        try:
            from integration_coworker.retrieval.semantic_search import cosine_similarity
            vec_a = [random.random() for _ in range(1536)]
            vec_b = [random.random() for _ in range(1536)]
            
            start = time.perf_counter()
            for _ in range(100):
                cosine_similarity(vec_a, vec_b)
            sim_time = (time.perf_counter() - start) * 10  # ms per computation
            perf_results["cosine_similarity_ms"] = round(sim_time, 2)
        except ImportError:
            perf_results["cosine_similarity_ms"] = "n/a"
        
        # Test YAML parsing
        try:
            import yaml
            sample_yaml = """
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0.0"
paths:
  /items:
    get:
      operationId: listItems
      responses:
        "200":
          description: Success
"""
            start = time.perf_counter()
            for _ in range(50):
                yaml.safe_load(sample_yaml)
            yaml_time = (time.perf_counter() - start) * 20  # ms per parse
            perf_results["yaml_parse_ms"] = round(yaml_time, 2)
        except ImportError:
            perf_results["yaml_parse_ms"] = "n/a"
        
        # Determine perf status
        all_fast = all(
            isinstance(v, float) and v < 10.0 
            for v in perf_results.values() 
            if isinstance(v, (int, float))
        )
        checks["performance"] = {
            "status": "ok" if all_fast else "warning",
            "message": ", ".join(f"{k}={v}" for k, v in perf_results.items()),
            "details": perf_results,
        }

    # Determine overall status
    has_errors = any(c["status"] == "error" for c in checks.values())
    has_warnings = any(c["status"] == "warning" for c in checks.values())

    if json_output:
        output = {
            "healthy": not has_errors,
            "checks": checks,
            "summary": "unhealthy" if has_errors else ("degraded" if has_warnings else "healthy"),
        }
        if perf_summary and perf_results:
            output["performance"] = perf_results
        typer.echo(json.dumps(output, indent=2))
    else:
        typer.echo("Integration Co-Worker Health Check")
        typer.echo("=" * 40)

        status_icons = {"ok": "✓", "warning": "⚠", "error": "✗", "skip": "○", "unknown": "?"}

        for check_name, result in checks.items():
            icon = status_icons.get(result["status"], "?")
            typer.echo(f"\n{icon} {check_name.replace('_', ' ').title()}")
            if verbose or result["status"] in ("error", "warning"):
                typer.echo(f"   {result['message']}")

        typer.echo("")
        if has_errors:
            typer.echo("✗ Health check FAILED - resolve errors before running")
        elif has_warnings:
            typer.echo("⚠ Health check passed with warnings")
        else:
            typer.echo("✓ All health checks passed")
        typer.echo("")

    if has_errors:
        raise typer.Exit(code=1)


# Keep backward compatibility with old main() command
@app.command("main", hidden=True)
def main(
    spec_ref: str = typer.Option(..., help="URL or path to the API specification"),
    task: str = typer.Option(..., help="Description of the integration task"),
    repo_root: Optional[Path] = typer.Option(None, help="Path to the target repository"),
    dry_run: bool = typer.Option(False, help="Run without persisting changes"),
    provider_code: Optional[str] = typer.Option(None, help="Override provider code"),
    json_output: bool = typer.Option(False, help="Output as JSON"),
):
    """Legacy command - use 'run' instead."""
    run_integration(
        spec_ref=[spec_ref],
        task=task,
        repo_root=repo_root,
        dry_run=dry_run,
        provider_code=provider_code,
        json_output=json_output,
    )


@app.command("kg-dump")
def kg_dump(
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="Filter by provider code"),
    node_type: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by node type (provider, workflow_template, endpoint, entity, task)"),
    show_edges: bool = typer.Option(False, "--edges", "-e", help="Also show edges between nodes"),
    show_steps: bool = typer.Option(False, "--steps", help="Also show workflow steps for templates"),
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum number of nodes to show"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """
    Dump Knowledge Graph contents for debugging and demo.
    
    Shows nodes, edges, and workflow templates stored in the KG.
    Useful for verifying that persist_kg_learning is populating the graph.
    
    Examples:
        # Show all nodes for a provider
        integration-coworker kg-dump --provider mock_payments
        
        # Show only workflow templates
        integration-coworker kg-dump --type workflow_template
        
        # Show templates with their steps
        integration-coworker kg-dump --type workflow_template --steps
        
        # Show nodes and edges
        integration-coworker kg-dump --provider mock_payments --edges
    """
    from integration_coworker.persistence.db import get_connection, get_engine_type

    reset_settings()
    engine = get_engine_type()

    try:
        conn = get_connection()
        cur = conn.cursor()

        # Build query for nodes
        where_clauses = []
        params = []

        if provider:
            where_clauses.append("provider_code = ?")
            params.append(provider)

        if node_type:
            where_clauses.append("node_type = ?")
            params.append(node_type)

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        # Query nodes
        node_query = f"""
            SELECT id, node_type, provider_code, key, name, description, usage_count
            FROM kg_nodes
            {where_sql}
            ORDER BY node_type, provider_code, key
            LIMIT ?
        """
        params.append(limit)

        cur.execute(node_query, params)
        nodes = cur.fetchall()

        if json_output:
            # JSON output mode
            output = {
                "engine": engine,
                "filter": {"provider": provider, "node_type": node_type},
                "nodes": [],
                "edges": [],
                "workflow_steps": [],
            }

            for node in nodes:
                node_id, n_type, n_provider, n_key, n_name, n_desc, usage = node
                output["nodes"].append({
                    "id": node_id,
                    "type": n_type,
                    "provider_code": n_provider,
                    "key": n_key,
                    "name": n_name,
                    "description": n_desc,
                    "usage_count": usage,
                })

            if show_edges and nodes:
                node_ids = [n[0] for n in nodes]
                placeholders = ",".join("?" * len(node_ids))
                edge_query = f"""
                    SELECT e.id, e.src_node_id, e.dst_node_id, e.relation_type, e.weight,
                           src.key as src_key, dst.key as dst_key
                    FROM kg_edges e
                    JOIN kg_nodes src ON e.src_node_id = src.id
                    JOIN kg_nodes dst ON e.dst_node_id = dst.id
                    WHERE e.src_node_id IN ({placeholders}) OR e.dst_node_id IN ({placeholders})
                """
                cur.execute(edge_query, node_ids + node_ids)
                edges = cur.fetchall()

                for edge in edges:
                    e_id, src_id, dst_id, rel_type, weight, src_key, dst_key = edge
                    output["edges"].append({
                        "id": e_id,
                        "src_node_id": src_id,
                        "dst_node_id": dst_id,
                        "relation_type": rel_type,
                        "weight": weight,
                        "src_key": src_key,
                        "dst_key": dst_key,
                    })

            if show_steps:
                template_ids = [n[0] for n in nodes if n[1] == "workflow_template"]
                if template_ids:
                    placeholders = ",".join("?" * len(template_ids))
                    steps_query = f"""
                        SELECT s.id, s.template_node_id, s.step_key, s.step_type, s.position, s.label
                        FROM kg_workflow_steps s
                        WHERE s.template_node_id IN ({placeholders})
                        ORDER BY s.template_node_id, s.position
                    """
                    cur.execute(steps_query, template_ids)
                    steps = cur.fetchall()

                    for step in steps:
                        s_id, tmpl_id, s_key, s_type, pos, label = step
                        output["workflow_steps"].append({
                            "id": s_id,
                            "template_node_id": tmpl_id,
                            "step_key": s_key,
                            "step_type": s_type,
                            "position": pos,
                            "label": label,
                        })

            typer.echo(json.dumps(output, indent=2))

        else:
            # Human-readable output
            typer.echo("Knowledge Graph Contents")
            typer.echo("=" * 60)
            typer.echo(f"Engine: {engine}")
            if provider:
                typer.echo(f"Provider filter: {provider}")
            if node_type:
                typer.echo(f"Type filter: {node_type}")
            typer.echo("")

            if not nodes:
                typer.echo("No nodes found. Run a successful integration to populate the KG.")
                typer.echo("")
                typer.echo("Hint: Run with --persist (not --dry-run) to write to KG:")
                typer.echo("  integration-coworker demo --persist")
                return

            typer.echo(f"📊 Nodes ({len(nodes)} found):")
            typer.echo("-" * 60)

            current_type = None
            for node in nodes:
                node_id, n_type, n_provider, n_key, n_name, n_desc, usage = node

                if n_type != current_type:
                    current_type = n_type
                    typer.echo(f"\n  [{n_type.upper()}]")

                usage_str = f" (used {usage}x)" if usage and usage > 0 else ""
                typer.echo(f"    • {n_key}{usage_str}")
                if n_name and n_name != n_key:
                    typer.echo(f"      Name: {n_name}")
                if n_desc:
                    desc_short = n_desc[:60] + "..." if len(n_desc) > 60 else n_desc
                    typer.echo(f"      Desc: {desc_short}")

            if show_edges and nodes:
                typer.echo("")
                typer.echo("🔗 Edges:")
                typer.echo("-" * 60)

                node_ids = [n[0] for n in nodes]
                placeholders = ",".join("?" * len(node_ids))
                edge_query = f"""
                    SELECT e.relation_type, src.key as src_key, dst.key as dst_key
                    FROM kg_edges e
                    JOIN kg_nodes src ON e.src_node_id = src.id
                    JOIN kg_nodes dst ON e.dst_node_id = dst.id
                    WHERE e.src_node_id IN ({placeholders}) OR e.dst_node_id IN ({placeholders})
                    LIMIT 30
                """
                cur.execute(edge_query, node_ids + node_ids)
                edges = cur.fetchall()

                if edges:
                    for edge in edges:
                        rel_type, src_key, dst_key = edge
                        typer.echo(f"    {src_key} --[{rel_type}]--> {dst_key}")
                else:
                    typer.echo("    No edges found.")

            if show_steps:
                template_ids = [n[0] for n in nodes if n[1] == "workflow_template"]
                if template_ids:
                    typer.echo("")
                    typer.echo("📋 Workflow Steps:")
                    typer.echo("-" * 60)

                    placeholders = ",".join("?" * len(template_ids))
                    steps_query = f"""
                        SELECT n.key, s.step_key, s.step_type, s.position, s.label
                        FROM kg_workflow_steps s
                        JOIN kg_nodes n ON s.template_node_id = n.id
                        WHERE s.template_node_id IN ({placeholders})
                        ORDER BY n.key, s.position
                    """
                    cur.execute(steps_query, template_ids)
                    steps = cur.fetchall()

                    current_template = None
                    for step in steps:
                        tmpl_key, s_key, s_type, pos, label = step

                        if tmpl_key != current_template:
                            current_template = tmpl_key
                            typer.echo(f"\n  Template: {tmpl_key}")

                        label_str = f" ({label})" if label else ""
                        typer.echo(f"    {pos}. [{s_type}] {s_key}{label_str}")

            typer.echo("")

    except Exception as e:
        typer.echo(f"✗ Error querying KG: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("ui")
def launch_ui(
    port: int = typer.Option(8501, "--port", "-p", help="Port to run Streamlit on"),
    host: str = typer.Option("localhost", "--host", "-h", help="Host to bind to"),
    browser: bool = typer.Option(True, "--browser/--no-browser", help="Open browser automatically"),
):
    """
    Launch the Streamlit web UI for interactive integration design.
    
    Provides:
    - Multi-panel interface (Inputs / Run View / Artifacts / Logs)
    - Real-time run status and progress tracking
    - Error capture with interactive recovery (Retry/Skip/Restart)
    - Artifact browser with code highlighting
    
    Requires: pip install 'integration-coworker[ui]'
    
    Examples:
        # Launch UI with default settings
        integration-coworker ui
        
        # Launch on a specific port
        integration-coworker ui --port 8080
        
        # Launch without opening browser
        integration-coworker ui --no-browser
        
        # Bind to all interfaces (for Docker/remote)
        integration-coworker ui --host 0.0.0.0
    """
    import subprocess
    import sys
    
    # Check if streamlit is installed
    try:
        import streamlit
    except ImportError:
        typer.echo("✗ Error: Streamlit not installed.", err=True)
        typer.echo("  Install with: pip install 'integration-coworker[ui]'", err=True)
        raise typer.Exit(code=1)
    
    # Find the streamlit app module
    ui_module = Path(__file__).parent / "ui" / "streamlit_app.py"
    
    if not ui_module.exists():
        typer.echo(f"✗ Error: UI module not found at {ui_module}", err=True)
        raise typer.Exit(code=1)
    
    typer.echo("🚀 Launching Integration Co-Worker UI...")
    typer.echo(f"   URL: http://{host}:{port}")
    typer.echo("")
    typer.echo("   Press Ctrl+C to stop the server")
    typer.echo("")
    
    # Build streamlit command
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(ui_module),
        "--server.port", str(port),
        "--server.address", host,
    ]
    
    if not browser:
        cmd.extend(["--server.headless", "true"])
    
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        typer.echo(f"✗ Error launching UI: {e}", err=True)
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        typer.echo("\n\n👋 UI server stopped.")


@app.command("kg-query")
def kg_query(
    entity: Optional[str] = typer.Option(None, "--entity", "-e", help="Find tasks related to this entity"),
    endpoint: Optional[str] = typer.Option(None, "--endpoint", help="Find tasks using this endpoint"),
    pattern: Optional[str] = typer.Option(None, "--pattern", "-p", help="Find tasks implementing this pattern"),
    provider: Optional[str] = typer.Option(None, "--provider", help="Optional: filter to specific provider"),
    max_depth: int = typer.Option(3, "--depth", "-d", help="Maximum graph traversal depth"),
    limit: int = typer.Option(10, "--limit", "-l", help="Maximum results to return"),
    show_path: bool = typer.Option(False, "--path", help="Show traversal path to each result"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """
    Query the Knowledge Graph using graph traversal (BFS/DFS).
    
    Finds related tasks by traversing graph relationships, NOT by
    semantic/embedding similarity. Uses the actual graph structure.
    
    You can query by:
    - Entity name (e.g., "Payment", "Customer")
    - Endpoint path (e.g., "/checkout/sessions")
    - Pattern key (e.g., "pattern.crud_create")
    
    Works without --provider flag - discovers across all providers.
    
    Examples:
        # Find tasks related to Payment entity
        integration-coworker kg-query --entity Payment
        
        # Find tasks using a specific endpoint
        integration-coworker kg-query --endpoint /checkout/sessions
        
        # Find tasks implementing CRUD create pattern
        integration-coworker kg-query --pattern pattern.crud_create
        
        # Filter to specific provider
        integration-coworker kg-query --entity Payment --provider stripe
        
        # Show traversal paths
        integration-coworker kg-query --entity Payment --path
    """
    from integration_coworker.kg import (
        find_related_tasks_via_graph,
        get_kg_node_count,
    )

    reset_settings()

    # Require at least one query parameter
    if not entity and not endpoint and not pattern:
        typer.echo("✗ Error: Specify at least one of --entity, --endpoint, or --pattern", err=True)
        raise typer.Exit(code=1)

    try:
        if json_output:
            output = {
                "query": {
                    "entity": entity,
                    "endpoint": endpoint,
                    "pattern": pattern,
                    "provider": provider,
                    "max_depth": max_depth,
                },
                "results": [],
                "kg_stats": {},
            }
        else:
            typer.echo("Knowledge Graph Query (BFS/DFS Traversal)")
            typer.echo("=" * 50)
            if entity:
                typer.echo(f"Entity: {entity}")
            if endpoint:
                typer.echo(f"Endpoint: {endpoint}")
            if pattern:
                typer.echo(f"Pattern: {pattern}")
            if provider:
                typer.echo(f"Provider filter: {provider}")
            typer.echo(f"Max depth: {max_depth}")
            typer.echo("")

        # Get KG stats
        kg_stats = get_kg_node_count()

        if json_output:
            output["kg_stats"] = kg_stats
        else:
            if kg_stats:
                typer.echo(f"📊 KG contains: {sum(kg_stats.values())} nodes")
                typer.echo(f"   Types: {', '.join(f'{t}={c}' for t, c in kg_stats.items())}")
            else:
                typer.echo("⚠ KG is empty. Run an integration with --persist first.")
                return
            typer.echo("")

        # Run the query
        matches = find_related_tasks_via_graph(
            entity_name=entity,
            endpoint_path=endpoint,
            pattern_key=pattern,
            provider_code=provider,
            max_depth=max_depth,
            top_k=limit,
        )

        if json_output:
            for match in matches:
                output["results"].append({
                    "task_key": match.task_key,
                    "task_description": match.task_description,
                    "provider_code": match.provider_code,
                    "graph_distance": match.graph_distance,
                    "path": match.path if show_path else None,
                    "relation_types": match.relation_types if show_path else None,
                    "associated_templates": match.associated_templates,
                })
            typer.echo(json.dumps(output, indent=2))

        else:
            if not matches:
                typer.echo("No related tasks found.")
                typer.echo("")
                typer.echo("Hints:")
                typer.echo("  • Check that your entity/endpoint/pattern exists in the KG")
                typer.echo("  • Try increasing --depth for deeper traversal")
                typer.echo("  • Run 'integration-coworker kg-dump' to see what's in the KG")
            else:
                typer.echo(f"🔍 Found {len(matches)} related tasks:")
                typer.echo("-" * 50)

                for i, match in enumerate(matches, 1):
                    distance_str = f"({match.graph_distance} hop{'s' if match.graph_distance > 1 else ''})"
                    typer.echo(f"\n  {i}. {match.task_key} {distance_str}")
                    if match.provider_code:
                        typer.echo(f"     Provider: {match.provider_code}")
                    if match.task_description:
                        desc_short = match.task_description[:60] + "..." if len(match.task_description) > 60 else match.task_description
                        typer.echo(f"     Description: {desc_short}")
                    if match.associated_templates:
                        typer.echo(f"     Templates: {', '.join(match.associated_templates[:3])}")

                    if show_path and match.path:
                        typer.echo(f"     Path: {' → '.join(match.path)}")
                        if match.relation_types:
                            typer.echo(f"     Via: {' → '.join(match.relation_types)}")

                typer.echo("")

                # Show cross-provider summary if querying by pattern
                if pattern:
                    typer.echo("📋 Cross-Provider Summary:")
                    providers = {}
                    for m in matches:
                        p = m.provider_code or "unknown"
                        providers[p] = providers.get(p, 0) + 1
                    for p, count in sorted(providers.items(), key=lambda x: -x[1]):
                        typer.echo(f"   • {p}: {count} task(s)")
                    typer.echo("")

    except Exception as e:
        typer.echo(f"✗ Error querying KG: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("feedback")
def record_feedback(
    run_id: str = typer.Argument(..., help="Run ID to provide feedback for"),
    score: Optional[float] = typer.Option(None, "--score", "-s", help="Numeric score (0.0 to 1.0)"),
    thumbs_up: bool = typer.Option(False, "--thumbs-up", "--up", help="Positive feedback (score=1.0)"),
    thumbs_down: bool = typer.Option(False, "--thumbs-down", "--down", help="Negative feedback (score=0.0)"),
    comment: Optional[str] = typer.Option(None, "--comment", "-c", help="Optional feedback comment"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """
    Record human feedback for a completed run.
    
    Feedback is stored locally and synced to LangSmith (if configured).
    The feedback is used to update Knowledge Graph confidence scores.
    
    Use one of: --score, --thumbs-up, or --thumbs-down.
    
    Examples:
        # Positive feedback with thumbs up
        integration-coworker feedback run-abc123 --thumbs-up
        
        # Negative feedback with thumbs down
        integration-coworker feedback run-abc123 --thumbs-down
        
        # Numeric score with comment
        integration-coworker feedback run-abc123 --score 0.8 --comment "Generated code worked well"
    """
    from integration_coworker.feedback.langsmith_sync import create_feedback
    from integration_coworker.domain.models import FeedbackSource

    # Validate score options
    options_count = sum([score is not None, thumbs_up, thumbs_down])
    if options_count == 0:
        typer.echo("✗ Error: Specify one of --score, --thumbs-up, or --thumbs-down", err=True)
        raise typer.Exit(code=1)
    if options_count > 1:
        typer.echo("✗ Error: Specify only one of --score, --thumbs-up, or --thumbs-down", err=True)
        raise typer.Exit(code=1)

    # Determine final score
    if thumbs_up:
        final_score = 1.0
        feedback_type = "thumbs_up"
    elif thumbs_down:
        final_score = 0.0
        feedback_type = "thumbs_down"
    else:
        if score < 0.0 or score > 1.0:
            typer.echo("✗ Error: Score must be between 0.0 and 1.0", err=True)
            raise typer.Exit(code=1)
        final_score = score
        feedback_type = "score"

    try:
        record = create_feedback(
            run_id=run_id,
            score=final_score,
            feedback_type=feedback_type,
            source=FeedbackSource.HUMAN,
            comment=comment,
        )

        if json_output:
            output = {
                "status": "created",
                "feedback_id": record.id,
                "run_id": run_id,
                "score": final_score,
                "feedback_type": record.feedback_type.value,
                "source": record.source.value,
                "langsmith_synced": record.langsmith_id is not None,
            }
            typer.echo(json.dumps(output, indent=2))
        else:
            typer.echo(f"✓ Feedback recorded for run {run_id}")
            typer.echo(f"   Score: {final_score}")
            typer.echo(f"   Type: {feedback_type}")
            if record.langsmith_id:
                typer.echo(f"   LangSmith: synced ({record.langsmith_id})")
            else:
                typer.echo("   LangSmith: not synced (tracing disabled or error)")
            if comment:
                typer.echo(f"   Comment: {comment}")

    except Exception as e:
        if json_output:
            typer.echo(json.dumps({"error": str(e), "status": "failed"}), err=True)
        else:
            typer.echo(f"✗ Error recording feedback: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("feedback-sync")
def sync_feedback(
    days: int = typer.Option(7, "--days", "-d", help="Sync feedback from the last N days"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="Filter by provider code"),
    update_confidence: bool = typer.Option(True, "--update-confidence/--no-update-confidence", help="Update KG confidence scores after sync"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed sync progress"),
):
    """
    Sync feedback from LangSmith to local database.
    
    Fetches human feedback from LangSmith for recent runs and stores it
    locally. Optionally updates Knowledge Graph confidence scores based
    on aggregated feedback.
    
    Requires LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY set.
    
    Examples:
        # Sync last 7 days of feedback
        integration-coworker feedback-sync
        
        # Sync last 30 days
        integration-coworker feedback-sync --days 30
        
        # Sync without updating confidence scores
        integration-coworker feedback-sync --no-update-confidence
        
        # Filter to specific provider
        integration-coworker feedback-sync --provider stripe
    """
    from integration_coworker.feedback.langsmith_sync import sync_langsmith_feedback
    from integration_coworker.feedback.confidence import update_all_confidences

    _setup_logging(verbose)

    # Check LangSmith configuration
    tracing_enabled = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
    langchain_key = os.getenv("LANGCHAIN_API_KEY", "") or os.getenv("LANGSMITH_API_KEY", "")

    if not tracing_enabled:
        typer.echo("✗ Error: LangSmith tracing not enabled", err=True)
        typer.echo("   Set LANGCHAIN_TRACING_V2=true to enable", err=True)
        raise typer.Exit(code=1)

    if not langchain_key:
        typer.echo("✗ Error: LangSmith API key not set", err=True)
        typer.echo("   Set LANGCHAIN_API_KEY or LANGSMITH_API_KEY", err=True)
        raise typer.Exit(code=1)

    try:
        if not json_output:
            typer.echo(f"🔄 Syncing feedback from LangSmith (last {days} days)...")
            if provider:
                typer.echo(f"   Provider filter: {provider}")

        # Sync feedback from LangSmith
        synced_count, error_count = sync_langsmith_feedback(
            days_back=days,
            provider_filter=provider,
        )

        if not json_output:
            if synced_count > 0:
                typer.echo(f"   ✓ Synced {synced_count} feedback record(s)")
            else:
                typer.echo("   No new feedback to sync")
            if error_count > 0:
                typer.echo(f"   ⚠ {error_count} error(s) during sync")

        # Optionally update confidence scores
        nodes_updated = 0
        if update_confidence and synced_count > 0:
            if not json_output:
                typer.echo("\n📊 Updating KG confidence scores...")
            
            nodes_updated = update_all_confidences(provider_filter=provider)
            
            if not json_output:
                if nodes_updated > 0:
                    typer.echo(f"   ✓ Updated {nodes_updated} node(s)")
                else:
                    typer.echo("   No confidence updates needed")

        if json_output:
            output = {
                "status": "completed",
                "synced_count": synced_count,
                "error_count": error_count,
                "nodes_updated": nodes_updated if update_confidence else None,
            }
            typer.echo(json.dumps(output, indent=2))
        else:
            typer.echo("")
            typer.echo("✓ Feedback sync completed")

    except Exception as e:
        if json_output:
            typer.echo(json.dumps({"error": str(e), "status": "failed"}), err=True)
        else:
            typer.echo(f"✗ Error syncing feedback: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("kg-confidence")
def show_kg_confidence(
    template_key: Optional[str] = typer.Argument(None, help="Specific template key to show confidence for"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="Filter by provider code"),
    threshold: Optional[float] = typer.Option(None, "--threshold", "-t", help="Only show templates with confidence below threshold"),
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum number of templates to show"),
    history: bool = typer.Option(False, "--history", "-h", help="Show confidence history for template"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """
    Show Knowledge Graph confidence scores for workflow templates.
    
    Displays the learned confidence scores based on aggregated feedback.
    Use this to identify high-performing vs low-performing templates.
    
    Examples:
        # Show all template confidence scores
        integration-coworker kg-confidence
        
        # Show confidence for specific template
        integration-coworker kg-confidence template.stripe.create_payment
        
        # Show templates with low confidence
        integration-coworker kg-confidence --threshold 0.5
        
        # Show confidence history for a template
        integration-coworker kg-confidence template.stripe.create_payment --history
        
        # Filter by provider
        integration-coworker kg-confidence --provider stripe
    """
    from integration_coworker.feedback.confidence import get_confidence_for_template
    from integration_coworker.persistence.db import get_connection, get_engine_type

    reset_settings()
    engine = get_engine_type()

    try:
        conn = get_connection()
        cur = conn.cursor()

        if template_key:
            # Single template mode
            confidence = get_confidence_for_template(template_key)
            
            if json_output:
                output = {
                    "template_key": template_key,
                    "confidence_score": confidence,
                    "history": [],
                }
                
                if history:
                    # Get confidence history
                    if engine == "postgres":
                        cur.execute("""
                            SELECT created_at, old_confidence, new_confidence, feedback_count, reason
                            FROM kg.confidence_history
                            WHERE node_key = %s
                            ORDER BY created_at DESC
                            LIMIT 20
                        """, (template_key,))
                    else:
                        cur.execute("""
                            SELECT created_at, old_confidence, new_confidence, feedback_count, reason
                            FROM kg_confidence_history
                            WHERE node_key = ?
                            ORDER BY created_at DESC
                            LIMIT 20
                        """, (template_key,))
                    
                    for row in cur.fetchall():
                        output["history"].append({
                            "timestamp": str(row[0]),
                            "old_score": row[1],
                            "new_score": row[2],
                            "feedback_count": row[3],
                            "trigger": row[4],
                        })
                
                typer.echo(json.dumps(output, indent=2))
            else:
                typer.echo(f"Template: {template_key}")
                typer.echo(f"Confidence: {confidence:.3f}")
                
                if history:
                    typer.echo("\n📈 Confidence History:")
                    typer.echo("-" * 60)
                    
                    if engine == "postgres":
                        cur.execute("""
                            SELECT created_at, old_confidence, new_confidence, feedback_count, reason
                            FROM kg.confidence_history
                            WHERE node_key = %s
                            ORDER BY created_at DESC
                            LIMIT 10
                        """, (template_key,))
                    else:
                        cur.execute("""
                            SELECT created_at, old_confidence, new_confidence, feedback_count, reason
                            FROM kg_confidence_history
                            WHERE node_key = ?
                            ORDER BY created_at DESC
                            LIMIT 10
                        """, (template_key,))
                    
                    rows = cur.fetchall()
                    if rows:
                        for row in rows:
                            ts, old_s, new_s, fc, trigger = row
                            delta = new_s - old_s if old_s else 0
                            delta_str = f"+{delta:.3f}" if delta >= 0 else f"{delta:.3f}"
                            typer.echo(f"  {ts}: {old_s:.3f} → {new_s:.3f} ({delta_str}) [{trigger}]")
                    else:
                        typer.echo("  No history recorded yet")
        else:
            # List mode - show all templates
            where_clauses = ["node_type = ?"]
            params = ["workflow_template"]

            if provider:
                where_clauses.append("provider_code = ?")
                params.append(provider)

            where_sql = "WHERE " + " AND ".join(where_clauses)

            # Query templates with confidence scores
            if engine == "postgres":
                query = f"""
                    SELECT key, provider_code, name, confidence_score, usage_count
                    FROM kg.nodes
                    {where_sql.replace('?', '%s')}
                    ORDER BY confidence_score ASC, usage_count DESC
                    LIMIT %s
                """
            else:
                query = f"""
                    SELECT key, provider_code, name, confidence_score, usage_count
                    FROM kg_nodes
                    {where_sql}
                    ORDER BY confidence_score ASC, usage_count DESC
                    LIMIT ?
                """
            
            params.append(limit)
            cur.execute(query, params)
            templates = cur.fetchall()

            # Apply threshold filter in Python (for flexibility)
            if threshold is not None:
                templates = [t for t in templates if t[3] and t[3] < threshold]

            if json_output:
                output = {
                    "filter": {"provider": provider, "threshold": threshold},
                    "templates": [],
                }
                for t in templates:
                    key, prov, name, conf, usage = t
                    output["templates"].append({
                        "key": key,
                        "provider_code": prov,
                        "name": name,
                        "confidence_score": conf,
                        "usage_count": usage,
                    })
                typer.echo(json.dumps(output, indent=2))
            else:
                typer.echo("Knowledge Graph Confidence Scores")
                typer.echo("=" * 60)
                if provider:
                    typer.echo(f"Provider filter: {provider}")
                if threshold:
                    typer.echo(f"Threshold filter: < {threshold}")
                typer.echo("")

                if not templates:
                    typer.echo("No workflow templates found.")
                    typer.echo("")
                    typer.echo("Hint: Run 'integration-coworker kg-dump --type workflow_template'")
                    return

                typer.echo(f"{'Template Key':<40} {'Confidence':>10} {'Usage':>7}")
                typer.echo("-" * 60)

                for t in templates:
                    key, prov, name, conf, usage = t
                    conf_str = f"{conf:.3f}" if conf else "1.000"
                    usage_str = str(usage) if usage else "0"
                    
                    # Color coding hint (for terminals that support it)
                    if conf and conf < 0.5:
                        indicator = "⚠"
                    elif conf and conf > 0.8:
                        indicator = "✓"
                    else:
                        indicator = " "
                    
                    typer.echo(f"{indicator} {key:<38} {conf_str:>10} {usage_str:>7}")

                typer.echo("")
                typer.echo("Legend: ⚠ = low confidence (<0.5), ✓ = high confidence (>0.8)")

    except Exception as e:
        if json_output:
            typer.echo(json.dumps({"error": str(e), "status": "failed"}), err=True)
        else:
            typer.echo(f"✗ Error querying confidence: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("cache-stats")
def cache_stats(
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """
    Show LLM response cache statistics (Plan 7).
    
    Displays cache hit rate, total requests, and connection status.
    Requires Redis to be running (docker compose up redis).
    
    Examples:
        # Show cache stats
        integration-coworker cache-stats
        
        # JSON output for scripting
        integration-coworker cache-stats --json
    """
    from integration_coworker.llm.cache import get_llm_cache
    
    cache = get_llm_cache()
    
    # Check if cache is available
    is_available = cache.is_available()
    stats = cache.get_stats()
    stats_dict = stats.to_dict()
    
    if json_output:
        output = {
            "enabled": cache.enabled,
            "available": is_available,
            "redis_url": cache.redis_url,
            "ttl_seconds": cache.ttl,
            **stats_dict,
        }
        typer.echo(json.dumps(output, indent=2))
    else:
        typer.echo("LLM Response Cache Statistics")
        typer.echo("=" * 40)
        typer.echo("")
        
        if not cache.enabled:
            typer.echo("⚠ Cache is DISABLED")
            typer.echo("   Set LLM_CACHE_ENABLED=true to enable")
            return
        
        if not is_available:
            typer.echo("✗ Redis not available")
            typer.echo(f"   URL: {cache.redis_url}")
            typer.echo("")
            typer.echo("   To start Redis:")
            typer.echo("     docker compose up -d redis")
            return
        
        typer.echo(f"✓ Cache ENABLED and connected")
        typer.echo(f"   Redis URL: {cache.redis_url}")
        typer.echo(f"   TTL: {cache.ttl}s ({cache.ttl // 3600}h)")
        typer.echo("")
        typer.echo("📊 Statistics:")
        typer.echo(f"   Hits:     {stats_dict['hits']}")
        typer.echo(f"   Misses:   {stats_dict['misses']}")
        typer.echo(f"   Hit Rate: {stats_dict['hit_rate']}")
        typer.echo(f"   Total:    {stats_dict['total_requests']} requests")
        typer.echo("")


@app.command("cache-clear")
def cache_clear(
    pattern: Optional[str] = typer.Option(None, "--pattern", "-p", help="Only clear keys matching pattern (e.g., 'llm:openai:*')"),
    reset_stats: bool = typer.Option(False, "--reset-stats", "-r", help="Also reset cache statistics"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """
    Clear the LLM response cache (Plan 7).
    
    Clears cached LLM responses from Redis. Use --pattern to selectively
    clear entries (e.g., for a specific provider or model).
    
    Examples:
        # Clear all cached responses
        integration-coworker cache-clear
        
        # Clear only OpenAI responses
        integration-coworker cache-clear --pattern "llm:openai:*"
        
        # Clear and reset statistics
        integration-coworker cache-clear --reset-stats
        
        # Force clear without confirmation
        integration-coworker cache-clear --force
    """
    from integration_coworker.llm.cache import get_llm_cache
    
    cache = get_llm_cache()
    
    # Check if cache is available
    if not cache.is_available():
        if json_output:
            typer.echo(json.dumps({"error": "Redis not available", "status": "failed"}))
        else:
            typer.echo("✗ Redis not available", err=True)
            typer.echo(f"   URL: {cache.redis_url}", err=True)
        raise typer.Exit(code=1)
    
    # Confirmation prompt
    if not force and not json_output:
        pattern_msg = f" matching '{pattern}'" if pattern else ""
        confirm = typer.confirm(f"Clear all cached LLM responses{pattern_msg}?")
        if not confirm:
            typer.echo("Aborted.")
            raise typer.Exit(code=0)
    
    # Clear cache
    deleted = cache.clear(pattern=pattern)
    
    # Reset stats if requested
    stats_reset = False
    if reset_stats:
        stats_reset = cache.reset_stats()
    
    if json_output:
        output = {
            "status": "success",
            "deleted_keys": deleted,
            "pattern": pattern,
            "stats_reset": stats_reset,
        }
        typer.echo(json.dumps(output, indent=2))
    else:
        typer.echo(f"✓ Cleared {deleted} cached entries")
        if pattern:
            typer.echo(f"   Pattern: {pattern}")
        if stats_reset:
            typer.echo("✓ Statistics reset")


if __name__ == "__main__":
    app()
