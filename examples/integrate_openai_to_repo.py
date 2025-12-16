#!/usr/bin/env python3
"""
Example: Integrate OpenAI API into a Python Repository

This script demonstrates how to:
1. Call the integration coworker with an OpenAPI spec
2. Get validated, tested Python code
3. Write it to your repo with proper structure

Prerequisites:
- OPENAI_API_KEY in environment (for LLM calls)
- DATABASE_URL for Postgres OR USE_SQLITE=true for SQLite
- Target repo must be a Python project (has pyproject.toml or requirements.txt)

Usage:
    python examples/integrate_openai_to_repo.py --repo-root /path/to/your/project
    python examples/integrate_openai_to_repo.py --repo-root /path/to/your/project --dry-run
"""
import argparse
import os
import sys
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Integrate OpenAI API into your Python repo")
    parser.add_argument("--repo-root", type=str, required=True, help="Path to your Python project")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    parser.add_argument("--task", type=str, default="Create a chat completion client with streaming support",
                        help="Task description for the integration")
    parser.add_argument("--spec-url", type=str, 
                        default="https://raw.githubusercontent.com/openai/openai-openapi/master/openapi.yaml",
                        help="OpenAPI spec URL")
    args = parser.parse_args()

    # Validate environment
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ Error: OPENAI_API_KEY not set")
        print("   Set it in .env or environment variables")
        sys.exit(1)

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        print(f"❌ Error: Repo root does not exist: {repo_root}")
        sys.exit(1)

    # Check it looks like a Python project
    is_python_project = (
        (repo_root / "pyproject.toml").exists() or
        (repo_root / "requirements.txt").exists() or
        (repo_root / "setup.py").exists()
    )
    if not is_python_project:
        print(f"⚠️  Warning: {repo_root} doesn't look like a Python project")
        print("   Expected pyproject.toml, requirements.txt, or setup.py")
        response = input("Continue anyway? [y/N]: ")
        if response.lower() != 'y':
            sys.exit(1)

    print("=" * 60)
    print("🔧 Integration Coworker - OpenAI Integration")
    print("=" * 60)
    print(f"Spec:      {args.spec_url}")
    print(f"Task:      {args.task}")
    print(f"Repo:      {repo_root}")
    print(f"Dry Run:   {args.dry_run}")
    print("=" * 60)

    # Import the API
    from integration_coworker.api.entrypoint import design_and_generate_integration
    from integration_coworker.api.types import IntegrationOptions

    # Configure options
    options = IntegrationOptions(
        dry_run=args.dry_run,
        strict_codegen=True,  # Enable strict validation
        constrained_codegen=True,  # Prevent path hallucination
    )

    print("\n🚀 Starting integration workflow...")
    print("   This will:")
    print("   1. Fetch and parse the OpenAPI spec")
    print("   2. Understand your task")
    print("   3. Generate client code, workflow code, and tests")
    print("   4. Validate syntax and security")
    print("   5. Write files to your repo (unless --dry-run)")
    print()

    # Run the integration
    result = design_and_generate_integration(
        spec_refs=[args.spec_url],
        task_description=args.task,
        provider_code="openai",
        repo_root=repo_root,
        options=options,
    )

    # Report results
    print("\n" + "=" * 60)
    print("📊 RESULTS")
    print("=" * 60)

    print(f"\n✅ Run ID: {result.run_id}")
    print(f"✅ Provider: {result.provider_code}")
    print(f"✅ Completed Steps: {len(result.completed_steps)}")

    if result.errors:
        print(f"\n⚠️  Errors ({len(result.errors)}):")
        for error in result.errors:
            print(f"   - {error}")

    # Show generated code artifacts
    print(f"\n📦 Code Artifacts ({len(result.code_artifacts)}):")
    for artifact in result.code_artifacts:
        status = "✅" if artifact.content else "❌"
        print(f"   {status} [{artifact.artifact_type}] {artifact.rel_path}")
        print(f"      Language: {artifact.language}")
        print(f"      Lines: {len(artifact.content.splitlines()) if artifact.content else 0}")

    # Show endpoints discovered
    print(f"\n🔗 Endpoints Discovered: {len(result.endpoints)}")
    for ep in result.endpoints[:5]:  # Show first 5
        print(f"   - {ep.method} {ep.path}")
    if len(result.endpoints) > 5:
        print(f"   ... and {len(result.endpoints) - 5} more")

    # Show policies inferred
    if result.policies:
        print(f"\n🛡️  Policies Inferred ({len(result.policies)}):")
        for policy in result.policies:
            ptype = policy.policy_type.value if hasattr(policy.policy_type, 'value') else str(policy.policy_type)
            print(f"   - {ptype}: {policy.config}")

    # Show what would be/was written
    if args.dry_run:
        print("\n📝 DRY RUN - Files that WOULD be written:")
    else:
        print("\n📝 Files written to repo:")

    applied_changes = result.plan.get("applied_changes", []) if result.plan else []
    for change in applied_changes:
        action = change.get("action", "unknown")
        path = change.get("path", "unknown")
        print(f"   [{action.upper()}] {path}")

    # Show full report
    if result.report_markdown:
        print("\n" + "=" * 60)
        print("📄 FULL REPORT")
        print("=" * 60)
        print(result.report_markdown[:2000])  # First 2000 chars
        if len(result.report_markdown) > 2000:
            print(f"\n... (report truncated, {len(result.report_markdown)} chars total)")

    # Final status
    print("\n" + "=" * 60)
    if result.errors:
        print("⚠️  COMPLETED WITH WARNINGS")
    elif args.dry_run:
        print("✅ DRY RUN COMPLETE - Run without --dry-run to write files")
    else:
        print("✅ INTEGRATION COMPLETE")
        print(f"\nNext steps:")
        print(f"  1. Review generated files in {repo_root}")
        print(f"  2. Run the generated tests: pytest {repo_root}/tests/integrations/")
        print(f"  3. Set OPENAI_API_KEY in your project's environment")
    print("=" * 60)

    return 0 if not result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
