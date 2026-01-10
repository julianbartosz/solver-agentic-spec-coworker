#!/usr/bin/env python3
"""
Production Integration Script

Integrates ANY OpenAPI spec into a real repository with full validation.
NO MOCKS. NO FALLBACKS. Real LLM generation with strict validation.

Usage:
    # With a URL
    python scripts/production_integration.py \
        --spec "https://petstore3.swagger.io/api/v3/openapi.json" \
        --task "Create a pet and list all pets"

    # With a local file
    python scripts/production_integration.py \
        --spec "specs/twilio_messaging_v1.json" \
        --task "Send an SMS message"

    # Dry run first
    python scripts/production_integration.py \
        --spec "specs/stripe_api.json" \
        --task "Create a payment intent" \
        --dry-run

Environment Variables Required:
    OPENAI_API_KEY      - For LLM code generation (required)
    DATABASE_URL        - Postgres connection OR set USE_SQLITE=true
    
Optional:
    ANTHROPIC_API_KEY   - To use Claude instead (set LLM_PROVIDER=anthropic)
    LANGCHAIN_TRACING_V2=true - Enable LangSmith tracing
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime

# Ensure we can import from src
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# Load environment
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", override=True)


def check_environment():
    """Verify all required environment variables are set."""
    errors = []
    warnings = []
    
    # Check LLM API key
    openai_key = os.getenv("OPENAI_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    llm_provider = os.getenv("LLM_PROVIDER", "openai")
    
    if llm_provider == "openai" and not openai_key:
        errors.append("OPENAI_API_KEY not set (required for LLM generation)")
    elif llm_provider == "anthropic" and not anthropic_key:
        errors.append("ANTHROPIC_API_KEY not set (LLM_PROVIDER=anthropic)")
    
    # Check for mock mode (we want to ensure it's OFF)
    if os.getenv("USE_MOCK_LLM", "").lower() == "true":
        errors.append("USE_MOCK_LLM=true is set - disable it for production")
    
    # Check database
    db_url = os.getenv("DATABASE_URL")
    use_sqlite = os.getenv("USE_SQLITE", "").lower() == "true"
    
    if not db_url and not use_sqlite:
        warnings.append("DATABASE_URL not set and USE_SQLITE not true - will use SQLite fallback")
    
    # Check LangSmith (optional but recommended)
    if os.getenv("LANGCHAIN_TRACING_V2", "").lower() != "true":
        warnings.append("LangSmith tracing disabled (set LANGCHAIN_TRACING_V2=true for observability)")
    
    return errors, warnings


def validate_spec_path(spec_ref: str) -> bool:
    """Check if spec path/URL is accessible."""
    if spec_ref.startswith(("http://", "https://")):
        # URL - we'll let the workflow validate it
        return True
    
    # Local file
    spec_path = Path(spec_ref)
    if not spec_path.is_absolute():
        spec_path = PROJECT_ROOT / spec_ref
    
    if not spec_path.exists():
        print(f"❌ Spec file not found: {spec_path}")
        return False
    
    return True


def validate_repo(repo_root: str, dry_run: bool = False, auto_confirm: bool = False) -> bool:
    """Check if target repo exists and is a Python project."""
    repo_path = Path(repo_root)
    
    if not repo_path.exists():
        print(f"❌ Repository not found: {repo_path}")
        return False
    
    # Check for Python project indicators
    python_indicators = [
        repo_path / "pyproject.toml",
        repo_path / "requirements.txt",
        repo_path / "setup.py",
        repo_path / "setup.cfg",
    ]
    
    is_python = any(p.exists() for p in python_indicators)
    if not is_python:
        print(f"⚠️  Warning: {repo_path} doesn't look like a Python project")
        print("   No pyproject.toml, requirements.txt, or setup.py found")
        if dry_run or auto_confirm:
            print(f"   [{'Dry-run' if dry_run else 'Auto-confirm'} mode: continuing anyway]")
        else:
            response = input("   Continue anyway? [y/N]: ")
            if response.lower() != 'y':
                return False
    
    # Check for config file
    config_file = repo_path / ".integration-coworker.yaml"
    if config_file.exists():
        print(f"✅ Found config file: {config_file}")
    else:
        print(f"ℹ️  No .integration-coworker.yaml - will use detected/default paths")
    
    return True


async def run_production_integration(
    spec_ref: str,
    task_description: str,
    repo_root: str,
    provider_code: str,
    dry_run: bool = False,
    strict: bool = True,
):
    """
    Run the full production integration workflow.
    
    This uses the actual workflow with:
    - Real LLM calls (no mocks)
    - Full syntax validation (tree-sitter)
    - Security validation (Python AST)
    - Content policy validation (no hallucinated paths)
    - Real file writes to the target repo
    """
    from integration_coworker.graph.runtime import _run_workflow_async
    from integration_coworker.graph.state import WorkflowState
    from integration_coworker.domain.models import SourceRef
    from integration_coworker.api.types import IntegrationOptions
    from integration_coworker.codegen.syntax_validator import validate_syntax, is_tree_sitter_available
    
    print("\n" + "=" * 70)
    print("🚀 PRODUCTION INTEGRATION")
    print("=" * 70)
    print(f"Spec:        {spec_ref}")
    print(f"Task:        {task_description}")
    print(f"Provider:    {provider_code}")
    print(f"Target Repo: {repo_root}")
    print(f"Dry Run:     {dry_run}")
    print(f"Strict Mode: {strict}")
    print(f"Tree-sitter: {'✅ Available' if is_tree_sitter_available() else '⚠️ Not installed'}")
    print("=" * 70)
    
    # Build options - PRODUCTION settings
    options = IntegrationOptions(
        dry_run=dry_run,
        strict_codegen=strict,           # Fail on validation errors
        constrained_codegen=True,         # Prevent path hallucination
        policy_mode="inline",             # Full inline code (no runtime deps)
    )
    
    # Create source ref
    source_ref = SourceRef.from_ref(spec_ref, provider_code)
    
    # Build initial state
    state = WorkflowState(
        source_refs=[source_ref],
        spec_refs=[spec_ref],
        task_description=task_description,
        provider_code=provider_code,
        repo_root=repo_root,
        options=options,
    )
    
    # Force repo integration
    state.plan["use_repo"] = True
    
    print("\n🔄 Starting workflow...\n")
    start_time = datetime.now()
    
    try:
        # Run the full async workflow
        result = await _run_workflow_async(state, use_checkpointer=True)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        
        print("\n" + "=" * 70)
        print("📊 RESULTS")
        print("=" * 70)
        
        # Basic info
        print(f"\n✅ Run ID: {result.run_id}")
        print(f"⏱️  Elapsed: {elapsed:.1f}s")
        print(f"📋 Completed Steps: {len(result.completed_steps)}/{len(result.completed_steps)}")
        
        # Show completed steps
        print("\n📝 Workflow Steps:")
        for i, step in enumerate(result.completed_steps, 1):
            print(f"   {i:2d}. {step}")
        
        # Errors
        if result.errors:
            print(f"\n⚠️  Errors ({len(result.errors)}):")
            for error in result.errors:
                print(f"   ❌ {error}")
        
        # Endpoints discovered
        print(f"\n🔗 Endpoints Discovered: {len(result.endpoints)}")
        for ep in result.endpoints[:10]:
            auth = "🔒" if ep.auth_required else "🔓"
            print(f"   {auth} {ep.method:6s} {ep.path}")
        if len(result.endpoints) > 10:
            print(f"   ... and {len(result.endpoints) - 10} more")
        
        # Schemas
        print(f"\n📦 Schemas Extracted: {len(result.schemas)}")
        
        # Policies
        if result.policies:
            print(f"\n🛡️  Policies Inferred ({len(result.policies)}):")
            for policy in result.policies:
                ptype = policy.policy_type.value if hasattr(policy.policy_type, 'value') else str(policy.policy_type)
                print(f"   - {ptype}")
        
        # Code artifacts - THE MAIN OUTPUT
        print(f"\n📄 Code Artifacts ({len(result.code_artifacts)}):")
        all_valid = True
        
        for artifact in result.code_artifacts:
            # Validate syntax
            validation = validate_syntax(artifact.content, artifact.language)
            status = "✅" if validation.is_valid else "❌"
            all_valid = all_valid and validation.is_valid
            
            lines = len(artifact.content.splitlines()) if artifact.content else 0
            
            print(f"\n   {status} [{artifact.artifact_type.upper()}] {artifact.rel_path}")
            print(f"      Language: {artifact.language}")
            print(f"      Lines: {lines}")
            print(f"      Validation: {validation.method}")
            
            if not validation.is_valid:
                print(f"      ❌ Error: {validation.error_message}")
                if validation.error_line:
                    print(f"         Line {validation.error_line}")
        
        # Files written
        applied_changes = result.plan.get("applied_changes", []) if result.plan else []
        if applied_changes:
            print(f"\n📁 Files {'Would Be' if dry_run else ''} Written ({len(applied_changes)}):")
            for change in applied_changes:
                action = change.get("action", "unknown").upper()
                path = change.get("path", "unknown")
                print(f"   [{action}] {path}")
        
        # Final validation summary
        print("\n" + "=" * 70)
        if result.errors:
            print("⚠️  COMPLETED WITH ERRORS")
            print("   Review errors above and re-run if needed")
        elif not all_valid:
            print("❌ SYNTAX VALIDATION FAILED")
            print("   Generated code has syntax errors - check artifacts above")
        elif dry_run:
            print("✅ DRY RUN SUCCESSFUL")
            print(f"   Run without --dry-run to write files to {repo_root}")
        else:
            print("✅ INTEGRATION COMPLETE")
            print(f"\n   Files written to: {repo_root}")
            print("\n   Next steps:")
            print(f"   1. cd {repo_root}")
            print(f"   2. Review generated files")
            print(f"   3. Run tests: pytest tests/")
            print(f"   4. Set API credentials in environment")
        print("=" * 70)
        
        return result
        
    except Exception as e:
        elapsed = (datetime.now() - start_time).total_seconds()
        print("\n" + "=" * 70)
        print(f"❌ WORKFLOW FAILED after {elapsed:.1f}s")
        print("=" * 70)
        print(f"\nError: {type(e).__name__}: {e}")
        
        import traceback
        print("\nFull traceback:")
        traceback.print_exc()
        
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Production Integration - Generate validated Python code from any OpenAPI spec",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Petstore API
    python scripts/production_integration.py \\
        --spec "https://petstore3.swagger.io/api/v3/openapi.json" \\
        --task "Create a new pet and list all available pets"

    # Local Twilio spec
    python scripts/production_integration.py \\
        --spec "specs/twilio_messaging_v1.json" \\
        --task "Send an SMS notification message"

    # Stripe with custom provider name
    python scripts/production_integration.py \\
        --spec "specs/stripe_api.json" \\
        --task "Create a payment intent for checkout" \\
        --provider stripe_checkout

    # Dry run first (preview only)
    python scripts/production_integration.py \\
        --spec "specs/openai_api.yaml" \\
        --task "Create a chat completion" \\
        --dry-run
        """
    )
    
    parser.add_argument(
        "--spec", "-s",
        required=True,
        help="OpenAPI spec URL or local file path"
    )
    
    parser.add_argument(
        "--task", "-t",
        required=True,
        help="Task description (what integration should do)"
    )
    
    parser.add_argument(
        "--provider", "-p",
        default=None,
        help="Provider code (default: derived from spec filename)"
    )
    
    parser.add_argument(
        "--repo-root", "-r",
        default="/Users/julianbartosz/git/schoolwork/UPlant-testing-solver-agentic-spec-coworker",
        help="Target repository root (default: UPlant testing repo)"
    )
    
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Preview changes without writing files"
    )
    
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Disable strict mode (allow fallback to templates on validation failure)"
    )
    
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Auto-confirm all prompts (use with caution)"
    )
    
    args = parser.parse_args()
    
    print("\n" + "=" * 70)
    print("🔧 PRODUCTION INTEGRATION SCRIPT")
    print("=" * 70)
    
    # Environment check
    print("\n📋 Environment Check:")
    errors, warnings = check_environment()
    
    for warning in warnings:
        print(f"   ⚠️  {warning}")
    
    for error in errors:
        print(f"   ❌ {error}")
    
    if errors:
        print("\n❌ Environment check failed. Fix the errors above and retry.")
        sys.exit(1)
    
    print("   ✅ Environment OK")
    
    # Validate spec
    print("\n📋 Spec Validation:")
    if not validate_spec_path(args.spec):
        sys.exit(1)
    print(f"   ✅ Spec accessible: {args.spec}")
    
    # Validate repo
    print("\n📋 Repository Validation:")
    if not validate_repo(args.repo_root, dry_run=args.dry_run, auto_confirm=args.yes):
        sys.exit(1)
    print(f"   ✅ Repository valid: {args.repo_root}")
    
    # Derive provider code if not specified
    provider_code = args.provider
    if not provider_code:
        # Extract from spec filename/URL
        spec_name = Path(args.spec).stem
        # Clean up common suffixes
        for suffix in ["_api", "_openapi", "-api", "-openapi", "_v1", "_v2", "_v3"]:
            if spec_name.lower().endswith(suffix):
                spec_name = spec_name[:-len(suffix)]
        provider_code = spec_name.lower().replace("-", "_").replace(" ", "_")
    
    print(f"\n   Provider code: {provider_code}")
    
    # Run the integration
    result = asyncio.run(run_production_integration(
        spec_ref=args.spec,
        task_description=args.task,
        repo_root=args.repo_root,
        provider_code=provider_code,
        dry_run=args.dry_run,
        strict=not args.no_strict,
    ))
    
    sys.exit(0 if result and not result.errors else 1)


if __name__ == "__main__":
    main()
