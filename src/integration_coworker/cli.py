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


if __name__ == "__main__":
    app()
