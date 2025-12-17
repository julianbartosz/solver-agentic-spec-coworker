#!/usr/bin/env python3
"""
Production Test: Multi-Language Code Generation

Tests the coworker's ability to generate code for different target languages
using real LLM calls, Postgres persistence, and actual repo structures.

Bug #70 Feature Tests:
1. Python repo - Should generate Python code (baseline)
2. TypeScript/Express repo - Should generate TypeScript code  
3. Go repo - Should generate Go code
4. No repo profile - Should default to Python
5. Cross-provider consistency (OpenAI vs Anthropic)

Run with: python scripts/test_multi_language_production.py
"""
import os
import sys
import json
import time
import tempfile
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

# Set correct DATABASE_URL before importing coworker modules
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://integration:integration@localhost:5432/integration_coworker"
)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from integration_coworker.api.entrypoint import run_workflow
from integration_coworker.graph.state import WorkflowState, IntegrationOptions
from integration_coworker.repo.models import RepoProfile


@dataclass
class TestResult:
    """Result of a single test case."""
    name: str
    passed: bool
    duration: float
    artifacts: List[Dict[str, Any]]
    errors: List[str]
    bugs_found: List[str]
    details: Dict[str, Any]


def create_python_repo(base_path: Path) -> Path:
    """Create a mock Python/FastAPI repo structure."""
    repo = base_path / "python_fastapi_repo"
    repo.mkdir(parents=True, exist_ok=True)
    
    # pyproject.toml
    (repo / "pyproject.toml").write_text("""
[project]
name = "my-fastapi-app"
version = "1.0.0"
dependencies = ["fastapi", "uvicorn", "httpx"]
""")
    
    # src structure
    (repo / "src" / "api").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "api" / "__init__.py").write_text("")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "__init__.py").write_text("")
    
    return repo


def create_typescript_repo(base_path: Path) -> Path:
    """Create a mock TypeScript/Express repo structure."""
    repo = base_path / "typescript_express_repo"
    repo.mkdir(parents=True, exist_ok=True)
    
    # package.json
    (repo / "package.json").write_text(json.dumps({
        "name": "my-express-app",
        "version": "1.0.0",
        "dependencies": {
            "express": "^4.18.0",
            "typescript": "^5.0.0",
            "axios": "^1.0.0"
        },
        "devDependencies": {
            "@types/express": "^4.17.0",
            "jest": "^29.0.0",
            "@types/jest": "^29.0.0"
        }
    }, indent=2))
    
    # tsconfig.json
    (repo / "tsconfig.json").write_text(json.dumps({
        "compilerOptions": {
            "target": "ES2020",
            "module": "commonjs",
            "outDir": "./dist",
            "rootDir": "./src",
            "strict": True
        }
    }, indent=2))
    
    # src structure
    (repo / "src" / "routes").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "services").mkdir(parents=True, exist_ok=True)
    (repo / "tests").mkdir(exist_ok=True)
    
    return repo


def create_go_repo(base_path: Path) -> Path:
    """Create a mock Go repo structure."""
    repo = base_path / "go_repo"
    repo.mkdir(parents=True, exist_ok=True)
    
    # go.mod
    (repo / "go.mod").write_text("""
module github.com/example/myapp

go 1.21

require (
    github.com/gin-gonic/gin v1.9.0
)
""")
    
    # cmd/api structure
    (repo / "cmd" / "api").mkdir(parents=True, exist_ok=True)
    (repo / "internal" / "handlers").mkdir(parents=True, exist_ok=True)
    (repo / "internal" / "services").mkdir(parents=True, exist_ok=True)
    (repo / "pkg" / "client").mkdir(parents=True, exist_ok=True)
    
    return repo


def create_config_with_language(repo: Path, language: str) -> None:
    """Create .integration-coworker.yaml with explicit language setting.
    
    Must match IntegrationCoworkerConfig schema which requires:
    - profile: {name, language, framework?}
    - layout: {integrations_root, tests_root}
    """
    # Map language to file extension for conventions
    ext = ".ts" if language in ("typescript", "javascript") else ".py"
    if language == "go":
        ext = ".go"
    
    config = f"""# Integration Coworker Configuration
version: "1.0"
profile:
  name: "test-{language}-repo"
  language: "{language if language in ('python', 'typescript', 'javascript') else 'python'}"
  framework: "generic"
layout:
  integrations_root: "src/integrations"
  tests_root: "tests/integrations"
conventions:
  client_module: "clients/{{provider}}{ext}"
  flow_module: "flows/{{provider}}_{{task}}{ext}"
  test_module: "test_{{provider}}_{{task}}{ext}"
metadata:
  generated_by: "test-script"
  detection_method: "user_provided"
"""
    (repo / ".integration-coworker.yaml").write_text(config)


def run_integration_test(
    name: str,
    spec_ref: str,
    task: str,
    provider: str,
    repo_root: Optional[Path] = None,
    llm_provider: str = "openai",
) -> TestResult:
    """Run a single integration test with production settings."""
    start = time.time()
    bugs_found = []
    details = {}
    
    try:
        # Set LLM provider
        os.environ["LLM_PROVIDER"] = llm_provider
        
        # Create state with production options
        state = WorkflowState(
            source_refs=[],
            spec_refs=[spec_ref],
            task_description=task,
            provider_code=provider,
            repo_root=str(repo_root) if repo_root else None,
            options=IntegrationOptions(
                dry_run=False,  # Real persistence
                policy_mode="inline",
                no_cache=False,  # Use caching
            ),
        )
        
        # Run the workflow
        result = run_workflow(state)
        
        # Analyze results
        artifacts = []
        for artifact in result.code_artifacts:
            artifacts.append({
                "type": artifact.artifact_type,
                "language": artifact.language,
                "path": artifact.rel_path,
                "content_preview": artifact.content[:200] if artifact.content else "",
                "content_length": len(artifact.content) if artifact.content else 0,
            })
            
            # Check for language mismatches
            if repo_root:
                # Read the config if exists
                config_file = repo_root / ".integration-coworker.yaml"
                if config_file.exists():
                    import yaml
                    with open(config_file) as f:
                        config = yaml.safe_load(f)
                    expected_lang = config.get("language", "python")
                    if artifact.language != expected_lang:
                        bugs_found.append(
                            f"BUG #71: Language mismatch - expected '{expected_lang}', "
                            f"got '{artifact.language}' for {artifact.artifact_type}"
                        )
        
        # Check for errors
        errors = result.errors or []
        
        # Additional checks
        details["completed_steps"] = result.completed_steps
        details["repo_profile"] = str(result.repo_profile) if result.repo_profile else None
        details["target_language"] = result.repo_profile.language if result.repo_profile else "python"
        
        # Check if code content matches expected language
        for artifact in result.code_artifacts:
            content = artifact.content or ""
            if artifact.language == "python":
                if "def " not in content and "class " not in content:
                    bugs_found.append(
                        f"BUG #72: Python artifact missing def/class: {artifact.artifact_type}"
                    )
            elif artifact.language == "typescript":
                # TypeScript should have export, interface, or function keywords
                if not any(kw in content for kw in ["export ", "interface ", "function ", "const ", "class "]):
                    bugs_found.append(
                        f"BUG #73: TypeScript artifact missing TS keywords: {artifact.artifact_type}"
                    )
            elif artifact.language == "go":
                if "func " not in content and "package " not in content:
                    bugs_found.append(
                        f"BUG #74: Go artifact missing func/package: {artifact.artifact_type}"
                    )
        
        duration = time.time() - start
        return TestResult(
            name=name,
            passed=len(errors) == 0 and len(bugs_found) == 0,
            duration=duration,
            artifacts=artifacts,
            errors=errors,
            bugs_found=bugs_found,
            details=details,
        )
        
    except Exception as e:
        duration = time.time() - start
        import traceback
        return TestResult(
            name=name,
            passed=False,
            duration=duration,
            artifacts=[],
            errors=[str(e), traceback.format_exc()],
            bugs_found=[f"BUG: Exception during test - {type(e).__name__}: {e}"],
            details=details,
        )


def main():
    """Run all production tests."""
    print("=" * 80)
    print("PRODUCTION TEST: Multi-Language Code Generation")
    print("=" * 80)
    print()
    
    results: List[TestResult] = []
    all_bugs: List[str] = []
    
    # Create temp directory for test repos
    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = Path(tmpdir)
        
        # Test 1: Python repo (baseline)
        print("\n[TEST 1] Python/FastAPI Repo (baseline)")
        print("-" * 40)
        python_repo = create_python_repo(base_path)
        create_config_with_language(python_repo, "python")
        
        result = run_integration_test(
            name="Python FastAPI Repo",
            spec_ref="specs/twilio_messaging_v1.json",
            task="Send an SMS message",
            provider="python_test",
            repo_root=python_repo,
        )
        results.append(result)
        all_bugs.extend(result.bugs_found)
        print(f"  ✓ Passed: {result.passed}")
        print(f"  Duration: {result.duration:.2f}s")
        print(f"  Artifacts: {len(result.artifacts)}")
        for a in result.artifacts:
            print(f"    - {a['type']}: {a['language']} ({a['content_length']} chars)")
        if result.bugs_found:
            print(f"  BUGS: {result.bugs_found}")
        
        # Test 2: TypeScript repo
        print("\n[TEST 2] TypeScript/Express Repo")
        print("-" * 40)
        ts_repo = create_typescript_repo(base_path)
        create_config_with_language(ts_repo, "typescript")
        
        result = run_integration_test(
            name="TypeScript Express Repo",
            spec_ref="specs/twilio_messaging_v1.json",
            task="Send an SMS message",
            provider="typescript_test",
            repo_root=ts_repo,
        )
        results.append(result)
        all_bugs.extend(result.bugs_found)
        print(f"  ✓ Passed: {result.passed}")
        print(f"  Duration: {result.duration:.2f}s")
        print(f"  Artifacts: {len(result.artifacts)}")
        for a in result.artifacts:
            print(f"    - {a['type']}: {a['language']} ({a['content_length']} chars)")
            # Show preview for TypeScript
            if a['language'] == 'typescript':
                print(f"      Preview: {a['content_preview'][:100]}...")
        if result.bugs_found:
            print(f"  BUGS: {result.bugs_found}")
        
        # Test 3: Go repo
        print("\n[TEST 3] Go Repo")
        print("-" * 40)
        go_repo = create_go_repo(base_path)
        create_config_with_language(go_repo, "go")
        
        result = run_integration_test(
            name="Go Repo",
            spec_ref="specs/twilio_messaging_v1.json",
            task="Send an SMS message",
            provider="go_test",
            repo_root=go_repo,
        )
        results.append(result)
        all_bugs.extend(result.bugs_found)
        print(f"  ✓ Passed: {result.passed}")
        print(f"  Duration: {result.duration:.2f}s")
        print(f"  Artifacts: {len(result.artifacts)}")
        for a in result.artifacts:
            print(f"    - {a['type']}: {a['language']} ({a['content_length']} chars)")
        if result.bugs_found:
            print(f"  BUGS: {result.bugs_found}")
        
        # Test 4: No repo (should default to Python)
        print("\n[TEST 4] No Repo Root (Python default)")
        print("-" * 40)
        
        result = run_integration_test(
            name="No Repo (Python default)",
            spec_ref="specs/twilio_messaging_v1.json",
            task="Send an SMS message",
            provider="no_repo_test",
            repo_root=None,
        )
        results.append(result)
        all_bugs.extend(result.bugs_found)
        print(f"  ✓ Passed: {result.passed}")
        print(f"  Duration: {result.duration:.2f}s")
        print(f"  Artifacts: {len(result.artifacts)}")
        for a in result.artifacts:
            print(f"    - {a['type']}: {a['language']} ({a['content_length']} chars)")
        if result.bugs_found:
            print(f"  BUGS: {result.bugs_found}")
        
        # Test 5: Cross-provider consistency (if Anthropic key available)
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_key:
            print("\n[TEST 5] Cross-Provider Consistency (Anthropic)")
            print("-" * 40)
            
            # Re-run Python test with Anthropic
            result = run_integration_test(
                name="Python Repo (Anthropic)",
                spec_ref="specs/twilio_messaging_v1.json",
                task="Send an SMS message",
                provider="anthropic_test",
                repo_root=python_repo,
                llm_provider="anthropic",
            )
            results.append(result)
            all_bugs.extend(result.bugs_found)
            print(f"  ✓ Passed: {result.passed}")
            print(f"  Duration: {result.duration:.2f}s")
        else:
            print("\n[TEST 5] SKIPPED (no ANTHROPIC_API_KEY)")
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\nTests: {passed}/{total} passed")
    print(f"Total Duration: {sum(r.duration for r in results):.2f}s")
    
    if all_bugs:
        print(f"\nBUGS FOUND ({len(all_bugs)}):")
        for bug in set(all_bugs):  # Deduplicate
            print(f"  - {bug}")
    else:
        print("\nNo bugs found! ✓")
    
    # Detailed failures
    failed = [r for r in results if not r.passed]
    if failed:
        print(f"\nFAILED TESTS ({len(failed)}):")
        for r in failed:
            print(f"\n  {r.name}:")
            if r.errors:
                for e in r.errors[:3]:  # First 3 errors
                    print(f"    ERROR: {e[:200]}")
            if r.bugs_found:
                for b in r.bugs_found:
                    print(f"    BUG: {b}")
    
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
