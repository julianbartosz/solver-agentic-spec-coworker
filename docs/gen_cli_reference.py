#!/usr/bin/env python3
"""Generate CLI reference documentation from --help output.

This script generates markdown documentation for the CLI by running
each command with --help and capturing the output.

Usage:
    python docs/gen_cli_reference.py
"""
import subprocess
import sys
from pathlib import Path


def generate_cli_docs() -> None:
    """Generate CLI reference markdown from typer --help."""
    output_path = Path("docs/user-guide/cli-reference.md")
    
    # Core commands to document
    commands = [
        ("run", "Run an integration workflow"),
        ("demo", "Run a demonstration workflow"),
        ("status", "Check system status"),
        ("health", "Run health checks"),
        ("init-db", "Initialize the database"),
        ("resume", "Resume an interrupted run"),
        ("kg-dump", "Export knowledge graph"),
        ("kg-query", "Query knowledge graph"),
        ("kg-confidence", "Show template confidence"),
        ("feedback", "Submit run feedback"),
        ("feedback-sync", "Sync feedback to database"),
        ("cache-stats", "Show cache statistics"),
        ("cache-clear", "Clear the LLM cache"),
        ("ui", "Launch web interface"),
    ]
    
    content = [
        "# CLI Reference\n\n",
        "> Auto-generated from `integration-coworker --help`.\n\n",
    ]
    
    # Main help
    try:
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        content.append("## Main Command\n\n```\n")
        content.append(result.stdout or "No output available")
        content.append("```\n\n")
    except Exception as e:
        content.append(f"## Main Command\n\nError generating help: {e}\n\n")
    
    # Each subcommand
    for cmd, description in commands:
        content.append(f"## `{cmd}`\n\n")
        content.append(f"{description}.\n\n")
        
        try:
            result = subprocess.run(
                [sys.executable, "-m", "integration_coworker.cli", cmd, "--help"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            content.append("```\n")
            content.append(result.stdout or f"No help available for {cmd}")
            content.append("```\n\n")
        except subprocess.TimeoutExpired:
            content.append(f"```\nTimeout getting help for {cmd}\n```\n\n")
        except Exception as e:
            content.append(f"```\nError: {e}\n```\n\n")
    
    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(content))
    print(f"Generated {output_path}")


def main() -> None:
    """Main entry point."""
    generate_cli_docs()


if __name__ == "__main__":
    main()
