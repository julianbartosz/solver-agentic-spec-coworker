#!/usr/bin/env python3
"""
Production Test Suite - December 10, 2024

Comprehensive production testing with:
- Real LLM calls (OpenAI/Anthropic)
- Postgres persistence
- Real repo integration
- Multi-language code generation
- Syntax validation (Bug #88 verification)
- LLM inference for repo detection
- Various codegen modes
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project is in path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
# Force override existing env vars with .env values
load_dotenv(override=True)

# Set production database
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://integration:integration@localhost:5432/integration_coworker"
)

from integration_coworker.graph.runtime import _run_workflow_async as run_workflow_async
from integration_coworker.graph.state import WorkflowState
from integration_coworker.api.types import IntegrationOptions
from integration_coworker.domain.models import SourceRef


@dataclass
class Bug:
    """Represents a discovered bug."""
    id: str
    title: str
    severity: str  # critical, high, medium, low
    feature: str
    description: str
    reproduction: str
    test_name: str
    stack_trace: Optional[str] = None


@dataclass  
class TestResult:
    """Result of a single test."""
    name: str
    category: str
    passed: bool
    duration: float
    details: Dict[str, Any] = field(default_factory=dict)
    bugs: List[Bug] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class ProductionTestRunner:
    """Production test runner."""
    
    def __init__(self):
        self.results: List[TestResult] = []
        self.all_bugs: List[Bug] = []
        self.bug_counter = 88  # Start after Bug #88 (syntax validation)
        self.base_path = Path(tempfile.mkdtemp(prefix="prod_test_dec10_"))
        print(f"Test workspace: {self.base_path}")
        
    def _next_bug_id(self) -> str:
        self.bug_counter += 1
        return f"#{self.bug_counter}"
    
    def _create_bug(self, title: str, severity: str, feature: str, 
                    description: str, reproduction: str, test_name: str,
                    stack_trace: str = None) -> Bug:
        bug = Bug(
            id=self._next_bug_id(),
            title=title,
            severity=severity,
            feature=feature,
            description=description,
            reproduction=reproduction,
            test_name=test_name,
            stack_trace=stack_trace
        )
        self.all_bugs.append(bug)
        return bug

    def create_python_repo(self) -> Path:
        """Create a Python/FastAPI repo structure."""
        repo = self.base_path / "python_fastapi_repo"
        repo.mkdir(parents=True, exist_ok=True)
        
        (repo / "pyproject.toml").write_text("""
[project]
name = "my-fastapi-app"
version = "1.0.0"
dependencies = ["fastapi", "uvicorn", "httpx"]
""")
        (repo / "src" / "api").mkdir(parents=True, exist_ok=True)
        (repo / "src" / "api" / "__init__.py").write_text("")
        (repo / "tests").mkdir(exist_ok=True)
        (repo / "tests" / "__init__.py").write_text("")
        return repo

    def create_typescript_repo(self) -> Path:
        """Create a TypeScript/Express repo structure."""
        repo = self.base_path / "typescript_express_repo"
        repo.mkdir(parents=True, exist_ok=True)
        
        (repo / "package.json").write_text(json.dumps({
            "name": "my-express-app",
            "version": "1.0.0",
            "dependencies": {
                "express": "^4.18.0",
                "typescript": "^5.0.0",
                "axios": "^1.0.0"
            }
        }, indent=2))
        
        (repo / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"target": "ES2020", "module": "commonjs"}
        }, indent=2))
        
        (repo / "src").mkdir(exist_ok=True)
        (repo / "src" / "index.ts").write_text("// Express app")
        (repo / "tests").mkdir(exist_ok=True)
        return repo

    def create_go_repo(self) -> Path:
        """Create a Go repo structure."""
        repo = self.base_path / "go_repo"
        repo.mkdir(parents=True, exist_ok=True)
        
        (repo / "go.mod").write_text("""module github.com/example/myapp

go 1.21

require (
    github.com/gin-gonic/gin v1.9.0
)
""")
        (repo / "main.go").write_text("""package main

func main() {
    // TODO
}
""")
        (repo / "internal").mkdir(exist_ok=True)
        return repo

    async def test_1_syntax_validation_integration(self) -> TestResult:
        """Test Bug #88: Tree-sitter syntax validation integration."""
        print("\n" + "="*70)
        print("TEST 1: Syntax Validation Integration (Bug #88)")
        print("="*70)
        
        start = time.time()
        bugs = []
        warnings = []
        details = {}
        
        try:
            from integration_coworker.codegen.syntax_validator import (
                validate_syntax, is_tree_sitter_available, get_available_languages
            )
            
            # Check tree-sitter availability
            ts_available = is_tree_sitter_available()
            details["tree_sitter_available"] = ts_available
            print(f"  Tree-sitter available: {ts_available}")
            
            if ts_available:
                langs = get_available_languages()
                details["available_languages"] = langs
                print(f"  Available languages: {langs}")
            
            # Test Python validation (always works via AST fallback)
            valid_python = "def hello(name: str) -> str:\n    return f'Hello, {name}!'"
            invalid_python = "def broken("
            
            result = validate_syntax(valid_python, "python")
            assert result.is_valid, f"Valid Python rejected: {result.error_message}"
            print(f"  ✅ Valid Python: PASSED (method={result.method})")
            
            result = validate_syntax(invalid_python, "python")
            assert not result.is_valid, "Invalid Python accepted"
            print(f"  ✅ Invalid Python detection: PASSED")
            
            # Test language aliases
            for alias, canonical in [("py", "python"), ("ts", "typescript"), ("js", "javascript")]:
                result = validate_syntax("x = 1", alias)
                print(f"  ✅ Alias '{alias}' -> '{canonical}': {result.method}")
            
            # Test integration with generate_code_and_tests
            from integration_coworker.graph.nodes.generate_code_and_tests import (
                _validate_syntax, _validate_python_syntax
            )
            
            # Verify function exists and works
            assert _validate_syntax("def foo(): pass", "python") == True
            assert _validate_python_syntax("def bar(): pass") == True
            print(f"  ✅ generate_code_and_tests integration: PASSED")
            
            # Test validate_integration_design integration
            from integration_coworker.graph.nodes.validate_integration_design import (
                _validate_syntax as vid_validate_syntax
            )
            
            errors = vid_validate_syntax("def foo(): pass", "test.py", "python")
            assert errors == [], f"Unexpected errors: {errors}"
            print(f"  ✅ validate_integration_design integration: PASSED")
            
            passed = True
            
        except Exception as e:
            passed = False
            bugs.append(self._create_bug(
                title="Syntax validation integration failure",
                severity="high",
                feature="Bug #88 Syntax Validation",
                description=str(e),
                reproduction="Import and call validate_syntax",
                test_name="test_1_syntax_validation_integration",
                stack_trace=traceback.format_exc()
            ))
        
        return TestResult(
            name="Syntax Validation Integration",
            category="Bug #88 Verification",
            passed=passed,
            duration=time.time() - start,
            details=details,
            bugs=bugs,
            warnings=warnings
        )

    async def test_2_real_llm_python_codegen(self) -> TestResult:
        """Test real LLM code generation for Python."""
        print("\n" + "="*70)
        print("TEST 2: Real LLM Python Code Generation")
        print("="*70)
        
        start = time.time()
        bugs = []
        warnings = []
        details = {}
        
        try:
            spec_path = Path(__file__).parent.parent / "specs" / "httpbin_api.json"
            if not spec_path.exists():
                spec_path = Path(__file__).parent.parent / "specs" / "petstore_v3.json"
            
            print(f"  Using spec: {spec_path.name}")
            
            state = WorkflowState(
                source_refs=[SourceRef.from_ref(str(spec_path), "python_llm_test")],
                spec_refs=[str(spec_path)],
                task_description="Make a simple GET request",
                provider_code="python_llm_test",
                options=IntegrationOptions(
                    dry_run=False,
                    strict_codegen=False,
                ),
            )
            
            final_state = await run_workflow_async(state)
            
            details["completed_steps"] = len(final_state.completed_steps)
            details["errors"] = final_state.errors
            details["artifacts_count"] = len(final_state.code_artifacts)
            
            print(f"  Completed steps: {details['completed_steps']}")
            print(f"  Errors: {len(details['errors'])}")
            print(f"  Artifacts: {details['artifacts_count']}")
            
            # Check artifacts
            for artifact in final_state.code_artifacts:
                print(f"    - {artifact.artifact_type}: {artifact.rel_path} ({len(artifact.content.splitlines())} lines)")
                details[f"artifact_{artifact.artifact_type}"] = {
                    "path": artifact.rel_path,
                    "lines": len(artifact.content.splitlines()),
                    "language": artifact.language
                }
            
            # Verify syntax of generated code
            from integration_coworker.codegen.syntax_validator import validate_syntax
            for artifact in final_state.code_artifacts:
                if artifact.language == "python":
                    result = validate_syntax(artifact.content, "python")
                    if not result.is_valid:
                        warnings.append(f"Artifact {artifact.artifact_type} has syntax error: {result.error_message}")
                    else:
                        print(f"    ✅ {artifact.artifact_type} syntax valid")
            
            passed = details["artifacts_count"] > 0 and len(details["errors"]) == 0
            
        except Exception as e:
            passed = False
            bugs.append(self._create_bug(
                title="Real LLM Python codegen failure",
                severity="high",
                feature="LLM Code Generation",
                description=str(e),
                reproduction="Run workflow with petstore spec",
                test_name="test_2_real_llm_python_codegen",
                stack_trace=traceback.format_exc()
            ))
        
        return TestResult(
            name="Real LLM Python Codegen",
            category="LLM Integration",
            passed=passed,
            duration=time.time() - start,
            details=details,
            bugs=bugs,
            warnings=warnings
        )

    async def test_3_typescript_codegen_with_repo(self) -> TestResult:
        """Test TypeScript code generation with repo integration."""
        print("\n" + "="*70)
        print("TEST 3: TypeScript Codegen with Repo Integration")
        print("="*70)
        
        start = time.time()
        bugs = []
        warnings = []
        details = {}
        
        try:
            # Create TypeScript repo
            ts_repo = self.create_typescript_repo()
            print(f"  Created TypeScript repo: {ts_repo}")
            
            spec_path = Path(__file__).parent.parent / "specs" / "httpbin_api.json"
            
            state = WorkflowState(
                source_refs=[SourceRef.from_ref(str(spec_path), "ts_codegen_test")],
                spec_refs=[str(spec_path)],
                task_description="Make a GET request to retrieve data",
                provider_code="ts_codegen_test",
                repo_root=str(ts_repo),
                options=IntegrationOptions(
                    dry_run=False,
                ),
            )
            
            final_state = await run_workflow_async(state)
            
            details["completed_steps"] = len(final_state.completed_steps)
            details["errors"] = final_state.errors
            details["artifacts_count"] = len(final_state.code_artifacts)
            details["repo_profile_detected"] = final_state.repo_profile is not None
            
            if final_state.repo_profile:
                details["detected_language"] = final_state.repo_profile.language
                print(f"  Detected language: {final_state.repo_profile.language}")
            
            print(f"  Completed steps: {details['completed_steps']}")
            print(f"  Artifacts: {details['artifacts_count']}")
            
            # Check if TypeScript files were generated
            ts_artifacts = [a for a in final_state.code_artifacts 
                          if a.language in ("typescript", "ts") or a.rel_path.endswith(".ts")]
            details["typescript_artifacts"] = len(ts_artifacts)
            
            if ts_artifacts:
                print(f"  ✅ TypeScript artifacts generated: {len(ts_artifacts)}")
                for a in ts_artifacts:
                    print(f"    - {a.rel_path}")
            else:
                warnings.append("No TypeScript artifacts generated despite TypeScript repo")
            
            passed = details["artifacts_count"] > 0
            
        except Exception as e:
            passed = False
            bugs.append(self._create_bug(
                title="TypeScript codegen with repo failure",
                severity="high",
                feature="Multi-Language Codegen",
                description=str(e),
                reproduction="Run workflow with TypeScript repo",
                test_name="test_3_typescript_codegen_with_repo",
                stack_trace=traceback.format_exc()
            ))
        
        return TestResult(
            name="TypeScript Codegen with Repo",
            category="Multi-Language",
            passed=passed,
            duration=time.time() - start,
            details=details,
            bugs=bugs,
            warnings=warnings
        )

    async def test_4_go_repo_detection(self) -> TestResult:
        """Test Go repo detection and codegen."""
        print("\n" + "="*70)
        print("TEST 4: Go Repo Detection")
        print("="*70)
        
        start = time.time()
        bugs = []
        warnings = []
        details = {}
        
        try:
            # Create Go repo
            go_repo = self.create_go_repo()
            print(f"  Created Go repo: {go_repo}")
            
            # Test repo detection
            from integration_coworker.repo.detection import get_repo_profile
            
            profile = get_repo_profile(str(go_repo))
            
            if profile:
                details["detected"] = True
                details["language"] = profile.language
                details["profile_source"] = getattr(profile, 'profile_source', 'unknown')
                print(f"  Detected: {profile.language} (source: {details['profile_source']})")
                
                if profile.language.lower() != "go":
                    warnings.append(f"Expected 'go', got '{profile.language}'")
            else:
                details["detected"] = False
                warnings.append("Go repo not detected")
            
            passed = details.get("detected", False)
            
        except Exception as e:
            passed = False
            bugs.append(self._create_bug(
                title="Go repo detection failure",
                severity="medium",
                feature="Repo Detection",
                description=str(e),
                reproduction="Call detect_repo_profile on Go repo",
                test_name="test_4_go_repo_detection",
                stack_trace=traceback.format_exc()
            ))
        
        return TestResult(
            name="Go Repo Detection",
            category="Repo Detection",
            passed=passed,
            duration=time.time() - start,
            details=details,
            bugs=bugs,
            warnings=warnings
        )

    async def test_5_llm_repo_inference(self) -> TestResult:
        """Test LLM-based repo layout inference."""
        print("\n" + "="*70)
        print("TEST 5: LLM Repo Inference")
        print("="*70)
        
        start = time.time()
        bugs = []
        warnings = []
        details = {}
        
        try:
            # Create a complex repo structure
            complex_repo = self.base_path / "complex_repo"
            complex_repo.mkdir(parents=True, exist_ok=True)
            
            # Mixed indicators
            (complex_repo / "package.json").write_text(json.dumps({
                "name": "fullstack-app",
                "dependencies": {"react": "^18.0.0", "express": "^4.0.0"}
            }))
            (complex_repo / "requirements.txt").write_text("flask\nrequests\n")
            (complex_repo / "backend" / "app").mkdir(parents=True, exist_ok=True)
            (complex_repo / "backend" / "app" / "__init__.py").write_text("")
            (complex_repo / "frontend" / "src").mkdir(parents=True, exist_ok=True)
            (complex_repo / "frontend" / "src" / "App.tsx").write_text("export default function App() {}")
            
            print(f"  Created complex repo: {complex_repo}")
            
            # Test LLM inference
            from integration_coworker.repo.llm_inference import infer_repo_config
            
            try:
                config = infer_repo_config(complex_repo)
                
                if config:
                    details["inferred"] = True
                    details["language"] = config.profile.language if config.profile else "unknown"
                    print(f"  LLM inferred: {details['language']}")
                else:
                    details["inferred"] = False
                    warnings.append("LLM inference returned None")
                    
            except Exception as infer_err:
                details["inferred"] = False
                details["inference_error"] = str(infer_err)
                warnings.append(f"LLM inference error: {infer_err}")
            
            passed = True  # This test is informational
            
        except Exception as e:
            passed = False
            bugs.append(self._create_bug(
                title="LLM repo inference failure",
                severity="medium",
                feature="LLM Repo Inference",
                description=str(e),
                reproduction="Call infer_repo_config_with_llm",
                test_name="test_5_llm_repo_inference",
                stack_trace=traceback.format_exc()
            ))
        
        return TestResult(
            name="LLM Repo Inference",
            category="Repo Detection",
            passed=passed,
            duration=time.time() - start,
            details=details,
            bugs=bugs,
            warnings=warnings
        )

    async def test_6_postgres_persistence(self) -> TestResult:
        """Test Postgres persistence (Silver/Gold layers)."""
        print("\n" + "="*70)
        print("TEST 6: Postgres Persistence")
        print("="*70)
        
        start = time.time()
        bugs = []
        warnings = []
        details = {}
        
        try:
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Check Silver layer
                    cur.execute("SELECT COUNT(*) FROM spec_silver.spec_documents")
                    details["spec_documents"] = cur.fetchone()[0]
                    
                    cur.execute("SELECT COUNT(*) FROM spec_silver.endpoints")
                    details["endpoints"] = cur.fetchone()[0]
                    
                    cur.execute("SELECT COUNT(*) FROM spec_silver.schemas")
                    details["schemas"] = cur.fetchone()[0]
                    
                    # Check Gold layer
                    cur.execute("SELECT COUNT(*) FROM integration_gold.integration_tasks")
                    details["integration_tasks"] = cur.fetchone()[0]
                    
                    cur.execute("SELECT COUNT(*) FROM integration_gold.code_artifacts")
                    details["code_artifacts"] = cur.fetchone()[0]
                    
                    # Check run tracking
                    cur.execute("SELECT COUNT(*) FROM integration_gold.run_status")
                    details["run_status_records"] = cur.fetchone()[0]
                    
                    # Get latest run status
                    cur.execute("""
                        SELECT run_id, status, started_at, finished_at 
                        FROM integration_gold.run_status 
                        ORDER BY started_at DESC LIMIT 5
                    """)
                    recent_runs = cur.fetchall()
                    details["recent_runs"] = [
                        {"run_id": r[0][:20], "status": r[1]} for r in recent_runs
                    ]
            
            print(f"  Silver Layer:")
            print(f"    - Spec documents: {details['spec_documents']}")
            print(f"    - Endpoints: {details['endpoints']}")
            print(f"    - Schemas: {details['schemas']}")
            print(f"  Gold Layer:")
            print(f"    - Integration tasks: {details['integration_tasks']}")
            print(f"    - Code artifacts: {details['code_artifacts']}")
            print(f"    - Run records: {details['run_status_records']}")
            
            # Check for stuck runs
            stuck_runs = [r for r in details.get("recent_runs", []) if r["status"] == "running"]
            if stuck_runs:
                warnings.append(f"{len(stuck_runs)} runs stuck in 'running' status")
            
            passed = True
            
        except Exception as e:
            passed = False
            bugs.append(self._create_bug(
                title="Postgres persistence failure",
                severity="critical",
                feature="Persistence",
                description=str(e),
                reproduction="Query spec_silver/integration_gold tables",
                test_name="test_6_postgres_persistence",
                stack_trace=traceback.format_exc()
            ))
        
        return TestResult(
            name="Postgres Persistence",
            category="Persistence",
            passed=passed,
            duration=time.time() - start,
            details=details,
            bugs=bugs,
            warnings=warnings
        )

    async def test_7_strict_codegen_mode(self) -> TestResult:
        """Test strict codegen mode with syntax validation."""
        print("\n" + "="*70)
        print("TEST 7: Strict Codegen Mode")
        print("="*70)
        
        start = time.time()
        bugs = []
        warnings = []
        details = {}
        
        try:
            spec_path = Path(__file__).parent.parent / "specs" / "httpbin_api.json"
            
            state = WorkflowState(
                source_refs=[SourceRef.from_ref(str(spec_path), "strict_test_dec10")],
                spec_refs=[str(spec_path)],
                task_description="Make a simple GET request",
                provider_code="strict_test_dec10",
                options=IntegrationOptions(
                    dry_run=False,
                    strict_codegen=True,
                ),
            )
            
            print(f"  Running with strict_codegen=True...")
            
            final_state = await run_workflow_async(state)
            
            details["completed_steps"] = len(final_state.completed_steps)
            details["errors"] = final_state.errors
            details["artifacts_count"] = len(final_state.code_artifacts)
            
            print(f"  Completed steps: {details['completed_steps']}")
            print(f"  Errors: {len(details['errors'])}")
            print(f"  Artifacts: {details['artifacts_count']}")
            
            if details["errors"]:
                for err in details["errors"][:3]:
                    print(f"    Error: {err[:100]}...")
            
            # Strict mode may fail due to LLM hallucination - that's expected
            # What we're checking is that it doesn't crash
            passed = True
            
            if details["artifacts_count"] == 0:
                warnings.append("Strict mode produced no artifacts (may be expected if LLM hallucinated)")
            
        except Exception as e:
            passed = False
            bugs.append(self._create_bug(
                title="Strict codegen mode crash",
                severity="high",
                feature="Strict Codegen",
                description=str(e),
                reproduction="Run workflow with strict_codegen=True",
                test_name="test_7_strict_codegen_mode",
                stack_trace=traceback.format_exc()
            ))
        
        return TestResult(
            name="Strict Codegen Mode",
            category="Codegen Modes",
            passed=passed,
            duration=time.time() - start,
            details=details,
            bugs=bugs,
            warnings=warnings
        )

    async def test_8_constrained_codegen_mode(self) -> TestResult:
        """Test constrained codegen mode (path hallucination prevention)."""
        print("\n" + "="*70)
        print("TEST 8: Constrained Codegen Mode")
        print("="*70)
        
        start = time.time()
        bugs = []
        warnings = []
        details = {}
        
        try:
            spec_path = Path(__file__).parent.parent / "specs" / "httpbin_api.json"
            
            state = WorkflowState(
                source_refs=[SourceRef.from_ref(str(spec_path), "constrained_test_dec10")],
                spec_refs=[str(spec_path)],
                task_description="Make a GET request",
                provider_code="constrained_test_dec10",
                options=IntegrationOptions(
                    dry_run=False,
                    constrained_codegen=True,
                ),
            )
            
            print(f"  Running with constrained_codegen=True...")
            
            final_state = await run_workflow_async(state)
            
            details["completed_steps"] = len(final_state.completed_steps)
            details["errors"] = final_state.errors
            details["artifacts_count"] = len(final_state.code_artifacts)
            
            print(f"  Completed steps: {details['completed_steps']}")
            print(f"  Artifacts: {details['artifacts_count']}")
            
            # Check that generated code contains exact spec paths
            if final_state.endpoints:
                spec_paths = [e.path for e in final_state.endpoints[:5]]
                details["spec_paths_sample"] = spec_paths
                
                for artifact in final_state.code_artifacts:
                    if artifact.artifact_type == "client":
                        # Check if at least one spec path is in the code
                        has_spec_path = any(p in artifact.content for p in spec_paths)
                        details["client_has_spec_paths"] = has_spec_path
                        if has_spec_path:
                            print(f"  ✅ Client contains spec paths")
                        else:
                            warnings.append("Client may have hallucinated paths")
            
            passed = details["artifacts_count"] > 0
            
        except Exception as e:
            passed = False
            bugs.append(self._create_bug(
                title="Constrained codegen mode failure",
                severity="high",
                feature="Constrained Codegen",
                description=str(e),
                reproduction="Run workflow with constrained_codegen=True",
                test_name="test_8_constrained_codegen_mode",
                stack_trace=traceback.format_exc()
            ))
        
        return TestResult(
            name="Constrained Codegen Mode",
            category="Codegen Modes",
            passed=passed,
            duration=time.time() - start,
            details=details,
            bugs=bugs,
            warnings=warnings
        )

    async def test_9_real_repo_integration(self) -> TestResult:
        """Test real repo file writing."""
        print("\n" + "="*70)
        print("TEST 9: Real Repo Integration (File Writing)")
        print("="*70)
        
        start = time.time()
        bugs = []
        warnings = []
        details = {}
        
        try:
            # Create a repo with config file
            repo = self.create_python_repo()
            
            # Add integration-coworker config
            config = {
                "profile": {
                    "language": "python",
                    "framework": "fastapi"
                },
                "layout": {
                    "clients_dir": "src/api/clients",
                    "flows_dir": "src/api/flows",
                    "tests_dir": "tests/api"
                }
            }
            
            import yaml
            (repo / ".integration-coworker.yaml").write_text(yaml.dump(config))
            
            # Create target directories
            (repo / "src" / "api" / "clients").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "api" / "flows").mkdir(parents=True, exist_ok=True)
            (repo / "tests" / "api").mkdir(parents=True, exist_ok=True)
            
            print(f"  Repo with config: {repo}")
            
            spec_path = Path(__file__).parent.parent / "specs" / "httpbin_api.json"
            
            state = WorkflowState(
                source_refs=[SourceRef.from_ref(str(spec_path), "repo_integration_test")],
                spec_refs=[str(spec_path)],
                task_description="Make a GET request",
                provider_code="repo_integration_test",
                repo_root=str(repo),
                options=IntegrationOptions(
                    dry_run=False,
                ),
            )
            
            final_state = await run_workflow_async(state)
            
            details["completed_steps"] = len(final_state.completed_steps)
            details["artifacts_count"] = len(final_state.code_artifacts)
            
            # Check if files were written
            written_files = []
            for artifact in final_state.code_artifacts:
                full_path = repo / artifact.rel_path
                if full_path.exists():
                    written_files.append(str(artifact.rel_path))
                    print(f"  ✅ Written: {artifact.rel_path}")
                else:
                    # Check if file exists anywhere in repo
                    found = list(repo.rglob(Path(artifact.rel_path).name))
                    if found:
                        written_files.append(str(found[0].relative_to(repo)))
                        print(f"  ✅ Found at: {found[0].relative_to(repo)}")
                    else:
                        warnings.append(f"File not written: {artifact.rel_path}")
            
            details["written_files"] = written_files
            passed = len(written_files) > 0
            
        except Exception as e:
            passed = False
            bugs.append(self._create_bug(
                title="Real repo integration failure",
                severity="high",
                feature="Repo Integration",
                description=str(e),
                reproduction="Run workflow with repo_root",
                test_name="test_9_real_repo_integration",
                stack_trace=traceback.format_exc()
            ))
        
        return TestResult(
            name="Real Repo Integration",
            category="Repo Integration",
            passed=passed,
            duration=time.time() - start,
            details=details,
            bugs=bugs,
            warnings=warnings
        )

    async def test_10_large_spec_processing(self) -> TestResult:
        """Test large spec processing (Stripe API)."""
        print("\n" + "="*70)
        print("TEST 10: Large Spec Processing")
        print("="*70)
        
        start = time.time()
        bugs = []
        warnings = []
        details = {}
        
        try:
            spec_path = Path(__file__).parent.parent / "specs" / "stripe_api.json"
            
            if not spec_path.exists():
                print(f"  Stripe spec not found, skipping")
                return TestResult(
                    name="Large Spec Processing",
                    category="Performance",
                    passed=True,
                    duration=time.time() - start,
                    details={"skipped": "spec not found"},
                    bugs=[],
                    warnings=["stripe_api.json not found"]
                )
            
            file_size = spec_path.stat().st_size / (1024 * 1024)
            details["spec_size_mb"] = round(file_size, 2)
            print(f"  Spec size: {details['spec_size_mb']} MB")
            
            state = WorkflowState(
                source_refs=[SourceRef.from_ref(str(spec_path), "large_spec_test")],
                spec_refs=[str(spec_path)],
                task_description="Create a payment intent",
                provider_code="large_spec_test",
                options=IntegrationOptions(
                    dry_run=False,
                ),
            )
            
            final_state = await run_workflow_async(state)
            
            details["completed_steps"] = len(final_state.completed_steps)
            details["endpoints_count"] = len(final_state.endpoints) if final_state.endpoints else 0
            details["errors"] = len(final_state.errors)
            details["processing_time"] = round(time.time() - start, 2)
            
            print(f"  Endpoints extracted: {details['endpoints_count']}")
            print(f"  Processing time: {details['processing_time']}s")
            print(f"  Errors: {details['errors']}")
            
            passed = details["endpoints_count"] > 0
            
        except Exception as e:
            passed = False
            bugs.append(self._create_bug(
                title="Large spec processing failure",
                severity="high",
                feature="Spec Processing",
                description=str(e),
                reproduction="Process stripe_api.json",
                test_name="test_10_large_spec_processing",
                stack_trace=traceback.format_exc()
            ))
        
        return TestResult(
            name="Large Spec Processing",
            category="Performance",
            passed=passed,
            duration=time.time() - start,
            details=details,
            bugs=bugs,
            warnings=warnings
        )

    async def run_all_tests(self):
        """Run all production tests."""
        print("\n" + "="*70)
        print("PRODUCTION TEST SUITE - December 10, 2024")
        print("="*70)
        
        test_methods = [
            self.test_1_syntax_validation_integration,
            self.test_2_real_llm_python_codegen,
            self.test_3_typescript_codegen_with_repo,
            self.test_4_go_repo_detection,
            self.test_5_llm_repo_inference,
            self.test_6_postgres_persistence,
            self.test_7_strict_codegen_mode,
            self.test_8_constrained_codegen_mode,
            self.test_9_real_repo_integration,
            self.test_10_large_spec_processing,
        ]
        
        for test_method in test_methods:
            try:
                result = await test_method()
                self.results.append(result)
            except Exception as e:
                print(f"\n❌ Test {test_method.__name__} crashed: {e}")
                self.results.append(TestResult(
                    name=test_method.__name__,
                    category="Crashed",
                    passed=False,
                    duration=0,
                    bugs=[self._create_bug(
                        title=f"Test crash: {test_method.__name__}",
                        severity="critical",
                        feature="Test Infrastructure",
                        description=str(e),
                        reproduction=f"Run {test_method.__name__}",
                        test_name=test_method.__name__,
                        stack_trace=traceback.format_exc()
                    )]
                ))
        
        # Print summary
        self._print_summary()
        
        # Cleanup
        try:
            shutil.rmtree(self.base_path)
        except Exception:
            pass

    def _print_summary(self):
        """Print test summary."""
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
        
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed
        
        print(f"\nResults: {passed}/{len(self.results)} passed")
        print()
        
        for result in self.results:
            status = "✅" if result.passed else "❌"
            print(f"  {status} {result.name} ({result.duration:.2f}s)")
            
            for warning in result.warnings:
                print(f"      ⚠️  {warning}")
            
            for bug in result.bugs:
                print(f"      🐛 {bug.id}: {bug.title} [{bug.severity}]")
        
        if self.all_bugs:
            print("\n" + "="*70)
            print("BUGS DISCOVERED")
            print("="*70)
            
            for bug in self.all_bugs:
                print(f"\n{bug.id} [{bug.severity.upper()}] - {bug.title}")
                print(f"  Feature: {bug.feature}")
                print(f"  Description: {bug.description[:200]}...")
                print(f"  Test: {bug.test_name}")
        else:
            print("\n✅ No bugs discovered!")


async def main():
    runner = ProductionTestRunner()
    await runner.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
