"""
CLI entrypoint for the integration coworker.

Thin wrapper over design_and_generate_integration per design spec Section 4.4.

Supports:
- Multiple --spec-ref options for multi-spec runs
- Demo mode with pre-configured examples
- Postgres persistence (set DATABASE_URL) or SQLite fallback (USE_SQLITE=true)
- Real LLM calls (set OPENAI_API_KEY) or mock mode (USE_MOCK_LLM=true)
"""
import typer
import json
import os
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
    """
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
            typer.echo(f"\n--- Full Report ---\n")
            typer.echo(result.report_markdown)
    
    # Exit with non-zero code if there were errors
    if has_errors:
        raise typer.Exit(code=1)


@app.command("demo")
def run_demo(
    dry_run: bool = typer.Option(True, "--dry-run/--persist", help="Run in dry-run mode (default: true)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
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
    )


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
        typer.echo(f"   Engine: SQLite (test mode)")
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
        typer.echo(f"   Engine: PostgreSQL + pgvector")
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
        typer.echo(f"Mode: SQLite")
        typer.echo(f"Path: {settings.database.sqlite_path}")
    else:
        display_url = settings.database.url
        if "@" in display_url and ":" in display_url.split("@")[0]:
            parts = display_url.split("@")
            user_pass = parts[0].split("://")[1]
            if ":" in user_pass:
                user = user_pass.split(":")[0]
                display_url = f"postgresql://{user}:***@{parts[1]}"
        typer.echo(f"Mode: PostgreSQL + pgvector")
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


if __name__ == "__main__":
    app()
