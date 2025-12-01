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
        
        # With verbose debug logging
        integration-coworker run -s ./api.yaml -t "Create payment" --verbose
    """
    # Setup logging based on verbose flag
    _setup_logging(verbose)

    if verbose:
        _logger.debug(f"Starting integration run with {len(spec_ref)} spec(s)")
        _logger.debug(f"Task: {task}")
        _logger.debug(f"Dry run: {dry_run}")

    options = IntegrationOptions(
        dry_run=dry_run,
        repo_integration_enabled=repo_root is not None,
        override_provider_code=provider_code
    )

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

    # Check for errors in the result
    has_errors = False
    if result.report_markdown and "## Errors" in result.report_markdown:
        # Parse report to check if there are actual errors
        error_section = result.report_markdown.split("## Errors", 1)[1].split("##", 1)[0]
        if error_section.strip() and not error_section.strip().startswith("*(none)*"):
            has_errors = True

    if json_output:
        # Output only valid JSON to stdout
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
        # Human-readable output
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

    # Exit with non-zero code if there were errors
    if has_errors:
        raise typer.Exit(code=1)


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

    typer.echo("🚀 Running demo with mock payments API...")
    typer.echo(f"   Spec: {mock_spec}")
    typer.echo(f"   Mode: {'dry-run' if dry_run else 'persist to database'}")
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


@app.command("init-db")
def init_database():
    """
    Initialize the database schema.
    
    Creates all required tables for spec_silver, integration_gold, and repo_meta.
    Safe to run multiple times (uses CREATE IF NOT EXISTS).
    
    Requires:
    - For Postgres: DATABASE_URL set and Postgres running
    - For SQLite: USE_SQLITE=true
    
    Examples:
        # Initialize Postgres
        DATABASE_URL="postgresql://user:pass@localhost/db" integration-coworker init-db
        
        # Initialize SQLite (for testing)
        USE_SQLITE=true integration-coworker init-db
    """
    from integration_coworker.persistence.db import init_schema, get_engine_type

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
                llm = ChatAnthropic(model="claude-sonnet-4-20250514", max_tokens=10)
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


if __name__ == "__main__":
    app()
