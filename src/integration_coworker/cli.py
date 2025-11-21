"""
CLI entrypoint for the integration coworker.

Thin wrapper over design_and_generate_integration per design spec Section 4.4.
"""
import typer
import json
from pathlib import Path
from typing import Optional

from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions

app = typer.Typer()


@app.command()
def main(
    spec_ref: str = typer.Option(..., help="URL or path to the API specification"),
    task: str = typer.Option(..., help="Description of the integration task"),
    repo_root: Optional[Path] = typer.Option(None, help="Path to the target repository"),
    dry_run: bool = typer.Option(False, help="Run without persisting changes"),
    provider_code: Optional[str] = typer.Option(None, help="Override provider code"),
    json_output: bool = typer.Option(False, help="Output as JSON"),
):
    """
    Agentic API Integration Designer & Code Generator CLI.
    
    Per design spec Section 4.4, this is a thin wrapper that:
    - Reads inputs (spec refs, repo_root, options)
    - Invokes design_and_generate_integration once per run
    - Writes generated artifacts, diffs, or reports to disk or stdout
    """
    options = IntegrationOptions(
        dry_run=dry_run,
        repo_integration_enabled=repo_root is not None,
        override_provider_code=provider_code
    )

    try:
        result = design_and_generate_integration(
            spec_refs=[spec_ref],
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


if __name__ == "__main__":
    app()
