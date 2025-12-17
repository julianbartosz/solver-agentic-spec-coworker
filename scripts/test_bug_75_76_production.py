#!/usr/bin/env python3
"""
Production Test: Bug #75 & #76 - Multi-Language Code Generation

This test validates the optimal fixes for:
- Bug #75: Template fallback returns wrong language when LLM fails
- Bug #76: ProfileConfig.language limited to 3 languages

Tests use:
- Real LLM calls (OpenAI/Anthropic)
- Postgres persistence
- LLM inference for repo profiling
- Real repo structures
- Production codegen modes

Run with: source .env && python scripts/test_bug_75_76_production.py
"""
import os
import sys
import json
import time
import tempfile
import traceback
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Load .env before importing coworker modules
load_dotenv()

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from integration_coworker.api.entrypoint import run_workflow
from integration_coworker.graph.state import WorkflowState, IntegrationOptions
from integration_coworker.repo.config_schema import (
    IntegrationCoworkerConfig,
    ProfileConfig,
    LayoutConfig,
    ConventionsConfig,
    MetadataConfig,
)
from integration_coworker.codegen.prompts import LANGUAGE_CONVENTIONS, get_language_conventions


@dataclass
class TestResult:
    """Result of a single test case."""
    name: str
    passed: bool
    duration: float
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    bugs_found: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


def create_language_repo(base_path: Path, language: str) -> Path:
    """Create a mock repo structure for a specific language."""
    repo = base_path / f"{language}_repo"
    repo.mkdir(parents=True, exist_ok=True)
    
    if language == "python":
        (repo / "pyproject.toml").write_text("""
[project]
name = "test-python-app"
version = "1.0.0"
dependencies = ["fastapi", "httpx"]
""")
        (repo / "src" / "api").mkdir(parents=True, exist_ok=True)
        (repo / "tests").mkdir(exist_ok=True)
    
    elif language == "typescript":
        (repo / "package.json").write_text(json.dumps({
            "name": "test-ts-app",
            "version": "1.0.0",
            "dependencies": {"express": "^4.18.0", "axios": "^1.0.0"},
            "devDependencies": {"typescript": "^5.0.0", "@types/node": "^20.0.0"}
        }, indent=2))
        (repo / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"target": "ES2020", "module": "ESNext", "strict": True}
        }, indent=2))
        (repo / "src").mkdir(parents=True, exist_ok=True)
        (repo / "tests").mkdir(exist_ok=True)
    
    elif language == "go":
        (repo / "go.mod").write_text("""module github.com/example/testapp

go 1.21

require (
    github.com/gin-gonic/gin v1.9.0
)
""")
        (repo / "cmd" / "api").mkdir(parents=True, exist_ok=True)
        (repo / "internal" / "handlers").mkdir(parents=True, exist_ok=True)
        (repo / "pkg").mkdir(exist_ok=True)
    
    elif language == "java":
        (repo / "pom.xml").write_text("""<?xml version="1.0" encoding="UTF-8"?>
<project>
    <groupId>com.example</groupId>
    <artifactId>test-java-app</artifactId>
    <version>1.0.0</version>
</project>
""")
        (repo / "src" / "main" / "java" / "com" / "example").mkdir(parents=True, exist_ok=True)
        (repo / "src" / "test" / "java").mkdir(parents=True, exist_ok=True)
    
    elif language == "ruby":
        (repo / "Gemfile").write_text("""source 'https://rubygems.org'

gem 'sinatra'
gem 'faraday'
""")
        (repo / "lib").mkdir(exist_ok=True)
        (repo / "spec").mkdir(exist_ok=True)
    
    return repo


def create_valid_config(repo: Path, language: str) -> None:
    """Create a valid .integration-coworker.yaml config file."""
    # Get conventions for this language
    conventions = get_language_conventions(language)
    ext = conventions.get("file_extension", ".py")
    
    # Bug #76 is now FIXED - we can use any language from LANGUAGE_CONVENTIONS
    # Previously: schema_lang = language if language in ("python", "typescript", "javascript") else "python"
    schema_lang = language  # All languages now accepted!
    
    config = IntegrationCoworkerConfig(
        version="1.0",
        profile=ProfileConfig(
            name=f"test-{language}-repo",
            language=schema_lang,  # Current schema limitation
            framework=None,
        ),
        layout=LayoutConfig(
            integrations_root="src/integrations",
            tests_root="tests/integrations",
        ),
        conventions=ConventionsConfig(
            client_module=f"clients/{{provider}}{ext}",
            flow_module=f"flows/{{provider}}_{{task}}{ext}",
            test_module=f"test_{{provider}}_{{task}}{ext}",
        ),
        metadata=MetadataConfig(
            generated_by="test-script",
            detection_method="user_provided",
        ),
    )
    
    # Write as YAML
    config_path = repo / ".integration-coworker.yaml"
    config_dict = config.model_dump(mode="json", exclude_none=True)
    
    import yaml
    with open(config_path, "w") as f:
        yaml.dump(config_dict, f, default_flow_style=False)


def analyze_code_language(code: str) -> Dict[str, Any]:
    """Analyze code to detect its actual language."""
    indicators = {
        "python": {
            "patterns": ["def ", "class ", "import ", "from ", '"""', "if __name__"],
            "anti_patterns": ["function ", "const ", "let ", "package "],
        },
        "typescript": {
            "patterns": ["interface ", "export ", ": string", ": number", "async function", "=> {"],
            "anti_patterns": ["def ", '"""', "package "],
        },
        "javascript": {
            "patterns": ["function ", "const ", "let ", "require(", "module.exports"],
            "anti_patterns": ["def ", '"""', "interface ", ": string"],
        },
        "go": {
            "patterns": ["package ", "func ", "import (", "type ", "struct {", "if err != nil"],
            "anti_patterns": ["def ", "class ", "function "],
        },
        "java": {
            "patterns": ["public class ", "private ", "public void", "import java.", "@Override"],
            "anti_patterns": ["def ", "function ", "package main"],
        },
        "ruby": {
            "patterns": ["def ", "end", "require ", "class ", "module "],
            "anti_patterns": ["package ", "func ", "public class"],
        },
    }
    
    scores = {}
    for lang, patterns in indicators.items():
        score = sum(1 for p in patterns["patterns"] if p in code)
        penalty = sum(1 for p in patterns["anti_patterns"] if p in code)
        scores[lang] = max(0, score - penalty)
    
    detected = max(scores, key=scores.get) if any(scores.values()) else "unknown"
    confidence = scores.get(detected, 0) / max(len(indicators.get(detected, {}).get("patterns", [1])), 1)
    
    return {
        "detected_language": detected,
        "confidence": min(confidence, 1.0),
        "scores": scores,
        "has_python_docstrings": '"""' in code,
        "has_jsdoc": "/**" in code and "*/" in code,
    }


def run_integration_test(
    name: str,
    spec_ref: str,
    task: str,
    provider: str,
    expected_language: str,
    repo_root: Optional[Path] = None,
    use_llm_inference: bool = False,
    strict_codegen: bool = False,
    dry_run: bool = False,
) -> TestResult:
    """Run a single integration test with production settings."""
    start = time.time()
    bugs_found = []
    details = {"expected_language": expected_language}
    artifacts = []
    errors = []
    
    try:
        # Create state with production options
        state = WorkflowState(
            source_refs=[],
            spec_refs=[spec_ref],
            task_description=task,
            provider_code=provider,
            repo_root=str(repo_root) if repo_root else None,
            options=IntegrationOptions(
                dry_run=dry_run,
                policy_mode="inline",
                no_cache=True,  # Force fresh generation
                strict_codegen=strict_codegen,
            ),
        )
        
        # Run the workflow
        result = run_workflow(state)
        
        # Analyze each artifact
        for artifact in result.code_artifacts:
            content = artifact.content or ""
            analysis = analyze_code_language(content)
            
            artifact_info = {
                "type": artifact.artifact_type,
                "declared_language": artifact.language,
                "detected_language": analysis["detected_language"],
                "detection_confidence": analysis["confidence"],
                "content_length": len(content),
                "has_python_docstrings": analysis["has_python_docstrings"],
                "has_jsdoc": analysis["has_jsdoc"],
                "content_preview": content[:300] if content else "",
            }
            artifacts.append(artifact_info)
            
            # Bug #75 check: Language mismatch
            if artifact.language != analysis["detected_language"]:
                if analysis["confidence"] > 0.5:  # High confidence mismatch
                    bugs_found.append(
                        f"BUG #75: Language mismatch in {artifact.artifact_type} - "
                        f"declared '{artifact.language}', detected '{analysis['detected_language']}' "
                        f"(confidence: {analysis['confidence']:.2f})"
                    )
            
            # Additional check: Python docstrings in non-Python
            if expected_language != "python" and analysis["has_python_docstrings"]:
                bugs_found.append(
                    f"BUG #75 variant: Python-style docstrings in {expected_language} "
                    f"artifact ({artifact.artifact_type})"
                )
        
        # Check for errors
        errors = result.errors or []
        details["completed_steps"] = result.completed_steps
        details["repo_profile_language"] = (
            result.repo_profile.language if result.repo_profile else None
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
        return TestResult(
            name=name,
            passed=False,
            duration=duration,
            artifacts=artifacts,
            errors=[str(e), traceback.format_exc()[:500]],
            bugs_found=[f"EXCEPTION: {type(e).__name__}: {e}"],
            details=details,
        )


def test_bug_76_language_literals():
    """Test Bug #76: ProfileConfig.language literal limitations."""
    print("\n" + "=" * 60)
    print("BUG #76 TEST: Language Literal Limitations")
    print("=" * 60)
    
    results = []
    
    # Test all languages in LANGUAGE_CONVENTIONS
    for language in LANGUAGE_CONVENTIONS.keys():
        print(f"\n  Testing ProfileConfig with language='{language}'...")
        
        try:
            # This will fail for languages not in the Literal type
            profile = ProfileConfig(
                name=f"test-{language}",
                language=language,
            )
            print(f"    ✅ ProfileConfig accepts '{language}'")
            results.append({"language": language, "accepted": True, "error": None})
        except Exception as e:
            error_msg = str(e)[:100]
            print(f"    ❌ ProfileConfig REJECTS '{language}': {error_msg}")
            results.append({"language": language, "accepted": False, "error": error_msg})
    
    # Summary
    accepted = [r for r in results if r["accepted"]]
    rejected = [r for r in results if not r["accepted"]]
    
    print(f"\n  Summary: {len(accepted)}/{len(results)} languages accepted")
    print(f"  Accepted: {[r['language'] for r in accepted]}")
    print(f"  Rejected: {[r['language'] for r in rejected]}")
    
    if rejected:
        print(f"\n  BUG #76 CONFIRMED: {len(rejected)} languages in LANGUAGE_CONVENTIONS")
        print(f"  are not accepted by ProfileConfig.language Literal type.")
    
    return results


def main():
    """Run all production tests for Bug #75 and #76."""
    print("=" * 70)
    print("PRODUCTION TEST: Bug #75 & #76 - Multi-Language Code Generation")
    print("=" * 70)
    
    # Check environment
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    db_url = os.environ.get("DATABASE_URL", "")
    
    print(f"\nEnvironment:")
    print(f"  OPENAI_API_KEY: {'✅ Set' if openai_key.startswith('sk-') else '❌ Missing'}")
    print(f"  DATABASE_URL: {'✅ Set' if 'postgres' in db_url else '❌ Missing'}")
    
    if not openai_key.startswith("sk-"):
        print("\n❌ OPENAI_API_KEY not set. Run: source .env")
        return 1
    
    # Test Bug #76 first (doesn't need LLM)
    bug76_results = test_bug_76_language_literals()
    
    # Production tests for Bug #75
    print("\n" + "=" * 60)
    print("BUG #75 TESTS: Template Fallback Language")
    print("=" * 60)
    
    all_results: List[TestResult] = []
    all_bugs: List[str] = []
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = Path(tmpdir)
        
        # Test languages that ARE in Literal (should work)
        for language in ["python", "typescript"]:
            print(f"\n[TEST] {language.upper()} Repo")
            print("-" * 40)
            
            repo = create_language_repo(base_path, language)
            create_valid_config(repo, language)
            
            result = run_integration_test(
                name=f"{language.capitalize()} Repo",
                spec_ref="specs/twilio_messaging_v1.json",
                task="Send an SMS message",
                provider=f"{language}_test",
                expected_language=language,
                repo_root=repo,
            )
            all_results.append(result)
            all_bugs.extend(result.bugs_found)
            
            print(f"  Passed: {result.passed}")
            print(f"  Duration: {result.duration:.2f}s")
            print(f"  Artifacts: {len(result.artifacts)}")
            for a in result.artifacts:
                print(f"    - {a['type']}: declared={a['declared_language']}, "
                      f"detected={a['detected_language']} (conf={a['detection_confidence']:.2f})")
            if result.bugs_found:
                for bug in result.bugs_found:
                    print(f"  ❌ {bug}")
        
        # Test LLM Inference mode (no config file)
        print(f"\n[TEST] LLM INFERENCE MODE (No Config)")
        print("-" * 40)
        
        # Create a TS repo without config file
        ts_repo_no_config = create_language_repo(base_path, "typescript")
        # Don't create config - let LLM infer
        
        result = run_integration_test(
            name="TypeScript Repo (LLM Inference)",
            spec_ref="specs/twilio_messaging_v1.json",
            task="Send an SMS message",
            provider="llm_inference_test",
            expected_language="typescript",
            repo_root=ts_repo_no_config,
            use_llm_inference=True,
        )
        all_results.append(result)
        all_bugs.extend(result.bugs_found)
        
        print(f"  Passed: {result.passed}")
        print(f"  Duration: {result.duration:.2f}s")
        print(f"  Inferred Language: {result.details.get('repo_profile_language', 'N/A')}")
        for a in result.artifacts:
            print(f"    - {a['type']}: declared={a['declared_language']}, "
                  f"detected={a['detected_language']}")
        if result.bugs_found:
            for bug in result.bugs_found:
                print(f"  ❌ {bug}")
        
        # Test Go repo (language NOT in Literal - triggers Bug #76)
        print(f"\n[TEST] GO REPO (Now Supported - Bug #76 Fixed)")
        print("-" * 40)
        
        go_repo = create_language_repo(base_path, "go")
        # Bug #76 is fixed! We can now create a valid config with language: go
        create_valid_config(go_repo, "go")
        
        result = run_integration_test(
            name="Go Repo (Now Supported)",
            spec_ref="specs/twilio_messaging_v1.json",
            task="Send an SMS message",
            provider="go_test",
            expected_language="go",
            repo_root=go_repo,
            use_llm_inference=False,  # Use the config file we created
        )
        all_results.append(result)
        all_bugs.extend(result.bugs_found)
        
        print(f"  Passed: {result.passed}")
        print(f"  Duration: {result.duration:.2f}s")
        print(f"  Inferred/Forced Language: {result.details.get('repo_profile_language', 'N/A')}")
        for a in result.artifacts:
            print(f"    - {a['type']}: declared={a['declared_language']}, "
                  f"detected={a['detected_language']}")
        if result.bugs_found:
            for bug in result.bugs_found:
                print(f"  ❌ {bug}")
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for r in all_results if r.passed)
    total = len(all_results)
    print(f"\nTests: {passed}/{total} passed")
    print(f"Total Duration: {sum(r.duration for r in all_results):.2f}s")
    
    # Bug #76 summary
    bug76_rejected = [r for r in bug76_results if not r["accepted"]]
    if bug76_rejected:
        print(f"\nBUG #76: {len(bug76_rejected)} languages rejected by schema:")
        for r in bug76_rejected:
            print(f"  - {r['language']}")
    
    # Bug #75 summary
    if all_bugs:
        unique_bugs = list(set(all_bugs))
        print(f"\nBUG #75 & Related ({len(unique_bugs)} unique):")
        for bug in unique_bugs:
            print(f"  - {bug}")
    else:
        print("\n✅ No Bug #75 issues detected!")
    
    # Recommendations
    print("\n" + "=" * 70)
    print("RECOMMENDATIONS")
    print("=" * 70)
    
    print("""
Bug #75 Fix (Template Fallback):
  OPTIMAL: Option G - Language-aware skeleton fallback
  - Keep minimal skeleton templates per language (just imports + signature)
  - LLM fills in the body, fallback provides syntactically correct structure
  - Scales to any language in LANGUAGE_CONVENTIONS

Bug #76 Fix (Language Literals):
  OPTIMAL: Option D - Validated string from LANGUAGE_CONVENTIONS
  - Change ProfileConfig.language from Literal to str
  - Add @field_validator that checks against LANGUAGE_CONVENTIONS.keys()
  - Single source of truth, infinitely extensible
""")
    
    return 0 if all(r.passed for r in all_results) else 1


if __name__ == "__main__":
    sys.exit(main())
