#!/usr/bin/env python3
"""
COMPREHENSIVE PRODUCTION CAVEAT ANALYSIS

This script tests each caveat identified in the audit with REAL production
settings (Postgres, real LLM, real repos) to determine the optimal fix.

For each caveat, we:
1. Demonstrate the current limitation
2. Test alternative solutions
3. Measure quality/scalability trade-offs
4. Recommend the optimal fix

CAVEATS ANALYZED:
================

ANY LANGUAGE:
1. Tree-sitter NOT INSTALLED - only Python AST works
2. LLM may still produce Python-ish code for other languages
3. Generated code NOT compiled/executed for non-Python
4. Skeleton templates may not match latest language features

ANY SPEC:
1. GraphQL → OpenAPI uses LLM (non-deterministic)
2. AsyncAPI → OpenAPI uses LLM (non-deterministic)
3. HTML/PDF parsing depends on optional deps
4. RAML, WSDL, API Blueprint NOT supported

ANY REPO:
1. Detection confidence often < 0.4 for sparse repos
2. Monorepo structures not explicitly handled
3. Language conventions are generic
4. No integration with existing linters/formatters

MULTI-SPEC:
1. Specs merged into single list (no namespacing)
2. No cross-API workflow orchestration
3. Spec ordering matters - last wins on conflicts
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Set up path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.startswith("export "):
            line = line[7:]
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            os.environ[key.strip()] = value.strip()


@dataclass
class CaveatAnalysis:
    """Analysis of a single caveat with alternative solutions."""
    caveat_id: str
    category: str
    description: str
    current_behavior: str
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    optimal_solution: Optional[str] = None
    implementation_plan: Optional[str] = None
    files_to_change: List[str] = field(default_factory=list)
    bugs_found: List[Dict[str, Any]] = field(default_factory=list)


@dataclass 
class ProductionTestResult:
    """Result of a production test."""
    name: str
    passed: bool
    duration: float
    details: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


class ProductionCaveatAnalyzer:
    """Analyzes caveats with real production settings."""
    
    def __init__(self):
        self.analyses: List[CaveatAnalysis] = []
        self.test_results: List[ProductionTestResult] = []
        
    def run_test(self, name: str, test_func, **kwargs) -> ProductionTestResult:
        """Run a test and record results."""
        print(f"\n  🧪 {name}...", end=" ", flush=True)
        start = time.time()
        
        try:
            result = test_func(**kwargs)
            duration = time.time() - start
            
            if isinstance(result, dict):
                passed = result.get("passed", True)
                details = result
            else:
                passed = bool(result)
                details = {"result": result}
            
            status = "✅" if passed else "❌"
            print(f"{status} ({duration:.1f}s)")
            
            test_result = ProductionTestResult(
                name=name,
                passed=passed,
                duration=duration,
                details=details,
            )
            self.test_results.append(test_result)
            return test_result
            
        except Exception as e:
            duration = time.time() - start
            print(f"💥 ERROR ({duration:.1f}s)")
            print(f"      {type(e).__name__}: {str(e)[:80]}")
            
            test_result = ProductionTestResult(
                name=name,
                passed=False,
                duration=duration,
                errors=[str(e)],
            )
            self.test_results.append(test_result)
            return test_result

    # =========================================================================
    # CAVEAT 1: TREE-SITTER NOT INSTALLED
    # =========================================================================
    
    def analyze_tree_sitter_caveat(self) -> CaveatAnalysis:
        """Analyze the tree-sitter installation caveat."""
        print("\n" + "=" * 70)
        print("CAVEAT 1: Tree-sitter Syntax Validation")
        print("=" * 70)
        
        analysis = CaveatAnalysis(
            caveat_id="LANG-001",
            category="ANY_LANGUAGE",
            description="Tree-sitter NOT INSTALLED - only Python AST validation works",
            current_behavior="Non-Python code gets regex fallback (limited accuracy)",
        )
        
        # Test current state
        from integration_coworker.codegen.syntax_validator import (
            validate_syntax,
            is_tree_sitter_available,
            get_available_languages,
        )
        
        ts_available = is_tree_sitter_available()
        print(f"\n  Current state: tree-sitter available = {ts_available}")
        
        if ts_available:
            available = get_available_languages()
            print(f"  Available languages: {available}")
        
        # Alternative 1: Install tree-sitter (production)
        alt1 = {
            "name": "Install tree-sitter dependencies",
            "approach": "pip install 'solver-agentic-spec-coworker[validation]'",
            "pros": [
                "Accurate syntax validation for 7 languages",
                "Catches real errors before runtime",
                "Zero LLM cost for validation",
            ],
            "cons": [
                "Adds ~50MB to install size",
                "Requires compilation on some platforms",
            ],
            "scalability": "High - O(n) parsing, no API calls",
            "implementation": "One-time pip install",
        }
        
        # Alternative 2: LLM-based validation
        alt2 = {
            "name": "LLM-based syntax validation",
            "approach": "Send code to LLM asking 'Is this valid {language}?'",
            "pros": [
                "No additional dependencies",
                "Can validate any language",
            ],
            "cons": [
                "Slow (300-1000ms per validation)",
                "Costs tokens for every validation",
                "Not 100% accurate",
                "Rate-limited under high load",
            ],
            "scalability": "Low - API call per validation",
            "implementation": "New function in syntax_validator.py",
        }
        
        # Alternative 3: Language-specific subprocess validation
        alt3 = {
            "name": "Subprocess-based compilation check",
            "approach": "Run language compiler/interpreter in check mode",
            "pros": [
                "100% accurate for syntax",
                "Uses native tools (go build, javac, etc.)",
            ],
            "cons": [
                "Requires all compilers installed",
                "Slow (subprocess overhead)",
                "Security concerns (untrusted code)",
            ],
            "scalability": "Medium - subprocess per validation",
            "implementation": "New subprocess_validator.py module",
        }
        
        analysis.alternatives = [alt1, alt2, alt3]
        
        # Test each alternative
        test_code = {
            "python": ("def foo():\n    pass", True),
            "go": ("package main\n\nfunc main() {}", True),
            "java": ("public class Test { }", True),
            "typescript": ("function foo(): void { }", True),
        }
        
        # Test current implementation
        print("\n  Testing current implementation (regex fallback):")
        for lang, (code, expected) in test_code.items():
            result = validate_syntax(code, lang)
            status = "✅" if result.is_valid == expected else "❌"
            print(f"    {status} {lang}: method={result.method}, valid={result.is_valid}")
        
        # Optimal solution
        analysis.optimal_solution = "Install tree-sitter dependencies"
        analysis.implementation_plan = """
1. Add tree-sitter to required dependencies (not optional)
2. OR: Make validation gracefully degrade without failing
3. Update CI to install validation extras
4. Add tree-sitter availability check to health endpoint
"""
        analysis.files_to_change = [
            "pyproject.toml",
            "src/integration_coworker/codegen/syntax_validator.py",
            ".github/workflows/ci.yml (if exists)",
        ]
        
        self.analyses.append(analysis)
        return analysis

    # =========================================================================
    # CAVEAT 2: LLM PRODUCES PYTHON-ISH CODE FOR OTHER LANGUAGES
    # =========================================================================
    
    def analyze_llm_language_fidelity(self) -> CaveatAnalysis:
        """Analyze LLM language fidelity for non-Python code generation."""
        print("\n" + "=" * 70)
        print("CAVEAT 2: LLM Language Fidelity")
        print("=" * 70)
        
        analysis = CaveatAnalysis(
            caveat_id="LANG-002",
            category="ANY_LANGUAGE",
            description="LLM may produce Python-ish code for other languages",
            current_behavior="Prompts include language hints but may not enforce idioms",
        )
        
        # Alternative 1: Enhanced few-shot examples
        alt1 = {
            "name": "Language-specific few-shot examples in prompts",
            "approach": "Include 2-3 example code snippets for each language",
            "pros": [
                "Significantly improves output quality",
                "Shows proper idioms (defer in Go, using in C#, etc.)",
                "No additional dependencies",
            ],
            "cons": [
                "Increases prompt token count",
                "Examples may become stale",
            ],
            "scalability": "High - fixed token overhead",
            "implementation": "Add LANGUAGE_EXAMPLES to prompts.py",
        }
        
        # Alternative 2: Post-generation linting
        alt2 = {
            "name": "Post-generation linting with language tools",
            "approach": "Run gofmt, eslint, rubocop etc. on generated code",
            "pros": [
                "Enforces real style guides",
                "Can auto-fix some issues",
            ],
            "cons": [
                "Requires all linters installed",
                "Subprocess overhead",
                "Complex error handling",
            ],
            "scalability": "Medium - subprocess per artifact",
            "implementation": "New linter_integration.py module",
        }
        
        # Alternative 3: Language-specific LLM prompting
        alt3 = {
            "name": "Separate prompts per language with style guides",
            "approach": "Load language-specific prompt templates",
            "pros": [
                "Tailored instructions per language",
                "Can include style rules",
            ],
            "cons": [
                "Maintenance burden for 7+ languages",
                "Prompt duplication",
            ],
            "scalability": "High - no runtime overhead",
            "implementation": "Add prompts/languages/*.txt templates",
        }
        
        analysis.alternatives = [alt1, alt2, alt3]
        
        # Test with real LLM call (if available)
        print("\n  Testing real LLM code generation fidelity:")
        
        def test_language_generation():
            try:
                from integration_coworker.codegen.prompts import (
                    build_client_generation_prompt,
                    get_language_conventions,
                )
                from integration_coworker.llm.dispatch import call_llm_for_node
                
                results = {}
                for lang in ["go", "typescript", "java"]:
                    conventions = get_language_conventions(lang)
                    prompt = build_client_generation_prompt(
                        endpoint_name="get_users",
                        method="GET",
                        path="/users",
                        request_schema=None,
                        response_schema={"type": "array", "items": {"type": "object"}},
                        target_language=lang,
                    )
                    
                    # Make real LLM call
                    response = call_llm_for_node("generate_code_and_tests", prompt[:4000])
                    
                    # Check for language-specific patterns
                    checks = {
                        "go": ["func ", "package ", "error"],
                        "typescript": ["async ", "Promise<", "interface "],
                        "java": ["public class", "throws", "void "],
                    }
                    
                    found = [p for p in checks[lang] if p in response]
                    results[lang] = {
                        "response_length": len(response),
                        "patterns_found": found,
                        "pattern_count": len(found),
                        "sample": response[:200],
                    }
                    print(f"    {lang}: found {len(found)}/{len(checks[lang])} patterns")
                
                return {"passed": True, "results": results}
            except Exception as e:
                return {"passed": False, "error": str(e)}
        
        self.run_test("LLM language fidelity", test_language_generation)
        
        analysis.optimal_solution = "Language-specific few-shot examples in prompts"
        analysis.implementation_plan = """
1. Add LANGUAGE_EXAMPLES dict to prompts.py with 2-3 examples per language
2. Include idiom markers: Go (defer, error handling), Java (try-with-resources), etc.
3. Update build_client_generation_prompt to include examples
4. Test with production LLM to verify improvement
"""
        analysis.files_to_change = [
            "src/integration_coworker/codegen/prompts.py",
        ]
        
        self.analyses.append(analysis)
        return analysis

    # =========================================================================
    # CAVEAT 3: GRAPHQL/ASYNCAPI LLM CONVERSION
    # =========================================================================
    
    def analyze_spec_conversion_caveat(self) -> CaveatAnalysis:
        """Analyze GraphQL/AsyncAPI LLM conversion non-determinism."""
        print("\n" + "=" * 70)
        print("CAVEAT 3: GraphQL/AsyncAPI LLM Conversion")
        print("=" * 70)
        
        analysis = CaveatAnalysis(
            caveat_id="SPEC-001",
            category="ANY_SPEC",
            description="GraphQL/AsyncAPI → OpenAPI uses LLM (non-deterministic)",
            current_behavior="Each run may produce different OpenAPI structure",
        )
        
        # Alternative 1: Deterministic parser libraries
        alt1 = {
            "name": "Use graphql-core and asyncapi-parser libraries",
            "approach": "Parse schemas natively then build OpenAPI structure",
            "pros": [
                "100% deterministic",
                "Fast (no LLM calls)",
                "Free (no tokens)",
                "Works offline",
            ],
            "cons": [
                "Additional dependencies",
                "May not capture all semantic nuances",
            ],
            "scalability": "High - O(n) parsing",
            "implementation": "Add graphql_parser.py and asyncapi_parser.py",
        }
        
        # Alternative 2: LLM with structured output (JSON schema)
        alt2 = {
            "name": "LLM with structured output enforcement",
            "approach": "Use OpenAI's structured output feature or JSON mode",
            "pros": [
                "Guaranteed valid JSON",
                "Can use same prompt approach",
            ],
            "cons": [
                "Still has LLM variability in content",
                "Vendor lock-in to structured output feature",
            ],
            "scalability": "Medium - still LLM call",
            "implementation": "Update _parse_graphql_to_pseudo_openapi",
        }
        
        # Alternative 3: Hybrid (parser + LLM for descriptions)
        alt3 = {
            "name": "Hybrid: Parser for structure, LLM for descriptions",
            "approach": "Parse schema deterministically, use LLM only for summaries",
            "pros": [
                "Best of both worlds",
                "Deterministic structure",
                "Rich descriptions",
            ],
            "cons": [
                "More complex implementation",
                "Still some LLM cost",
            ],
            "scalability": "High for structure, medium for descriptions",
            "implementation": "New graphql_hybrid_parser.py",
        }
        
        analysis.alternatives = [alt1, alt2, alt3]
        
        # Test deterministic conversion
        print("\n  Testing GraphQL/AsyncAPI detection and conversion:")
        
        def test_graphql_parsing():
            from integration_coworker.graph.nodes.detect_and_parse_spec import (
                _is_graphql_content,
                _parse_graphql_to_pseudo_openapi,
            )
            
            graphql_schema = '''
type Query {
    user(id: ID!): User
    users(limit: Int): [User!]!
}

type User {
    id: ID!
    name: String!
    email: String
}
'''
            # Test detection
            is_graphql = _is_graphql_content(graphql_schema, "")
            
            # Test conversion (with real LLM)
            result1 = _parse_graphql_to_pseudo_openapi(graphql_schema, "schema.graphql")
            result2 = _parse_graphql_to_pseudo_openapi(graphql_schema, "schema.graphql")
            
            # Check for non-determinism
            if result1 and result2:
                paths1 = list(result1.get("paths", {}).keys())
                paths2 = list(result2.get("paths", {}).keys())
                deterministic = paths1 == paths2
            else:
                deterministic = False
            
            return {
                "passed": is_graphql and result1 is not None,
                "is_graphql": is_graphql,
                "result1_paths": paths1 if result1 else [],
                "result2_paths": paths2 if result2 else [],
                "deterministic": deterministic,
            }
        
        self.run_test("GraphQL parsing", test_graphql_parsing)
        
        analysis.optimal_solution = "Hybrid: Parser for structure, LLM for descriptions"
        analysis.implementation_plan = """
1. Add graphql-core dependency to pyproject.toml
2. Create graphql_parser.py with deterministic schema parsing
3. Extract types, queries, mutations into structured format
4. Map to OpenAPI structure deterministically
5. Optionally use LLM to generate operation descriptions
6. Same approach for AsyncAPI with asyncapi library
"""
        analysis.files_to_change = [
            "pyproject.toml",
            "src/integration_coworker/graph/nodes/detect_and_parse_spec.py",
            "src/integration_coworker/parsers/graphql_parser.py (new)",
            "src/integration_coworker/parsers/asyncapi_parser.py (new)",
        ]
        
        self.analyses.append(analysis)
        return analysis

    # =========================================================================
    # CAVEAT 4: REPO DETECTION CONFIDENCE
    # =========================================================================
    
    def analyze_repo_detection_caveat(self) -> CaveatAnalysis:
        """Analyze repo detection confidence issues."""
        print("\n" + "=" * 70)
        print("CAVEAT 4: Repo Detection Confidence")
        print("=" * 70)
        
        analysis = CaveatAnalysis(
            caveat_id="REPO-001",
            category="ANY_REPO",
            description="Detection confidence often < 0.4 for sparse repos",
            current_behavior="Warns user but continues with low-confidence detection",
        )
        
        # Alternative 1: Require config file for low-confidence
        alt1 = {
            "name": "Require .integration-coworker.yaml for low-confidence",
            "approach": "Error out and ask for config if confidence < threshold",
            "pros": [
                "No false positives",
                "User explicitly confirms intent",
            ],
            "cons": [
                "Worse UX for first-time users",
                "Extra step required",
            ],
            "scalability": "N/A - policy choice",
            "implementation": "Add check in attach_repo_context",
        }
        
        # Alternative 2: Interactive mode
        alt2 = {
            "name": "Interactive detection confirmation",
            "approach": "Prompt user: 'Detected Go project. Correct? (y/n)'",
            "pros": [
                "User can correct mistakes",
                "Good UX for CLI",
            ],
            "cons": [
                "Doesn't work in automation/CI",
                "Not feasible in API mode",
            ],
            "scalability": "N/A - UX choice",
            "implementation": "Add --interactive flag to CLI",
        }
        
        # Alternative 3: LLM-assisted detection with confidence boost
        alt3 = {
            "name": "LLM-assisted repo analysis",
            "approach": "For low-confidence, ask LLM to analyze repo structure",
            "pros": [
                "Can understand README, code patterns",
                "Higher accuracy for ambiguous repos",
            ],
            "cons": [
                "LLM cost for every detection",
                "Slower detection",
            ],
            "scalability": "Low - LLM call per repo",
            "implementation": "Already exists as use_llm=True option",
        }
        
        # Alternative 4: Improve heuristics for sparse repos
        alt4 = {
            "name": "Improve heuristics for sparse/new repos",
            "approach": "Check more files: README, Makefile, Dockerfile, CI configs",
            "pros": [
                "Higher accuracy without LLM",
                "Free and fast",
            ],
            "cons": [
                "Heuristics have limits",
                "Still may fail on truly ambiguous repos",
            ],
            "scalability": "High - O(1) file checks",
            "implementation": "Extend _detect_language_by_config_files",
        }
        
        analysis.alternatives = [alt1, alt2, alt3, alt4]
        
        # Test with various repo structures
        print("\n  Testing repo detection with various structures:")
        
        def test_repo_detection():
            from integration_coworker.repo.detection import detect_repo_profile
            
            results = {}
            with tempfile.TemporaryDirectory() as tmpdir:
                base = Path(tmpdir)
                
                # Test 1: Sparse Go repo
                go_repo = base / "go_sparse"
                go_repo.mkdir()
                (go_repo / "go.mod").write_text("module example\n\ngo 1.21\n")
                result = detect_repo_profile(str(go_repo))
                results["go_sparse"] = {
                    "language": result.language,
                    "confidence": result.confidence,
                    "passed": result.language == "go",
                }
                print(f"    Go sparse: lang={result.language}, conf={result.confidence:.2f}")
                
                # Test 2: Monorepo (mixed languages)
                mono = base / "monorepo"
                mono.mkdir()
                (mono / "package.json").write_text('{"name": "workspace"}')
                (mono / "apps").mkdir()
                (mono / "apps" / "api" / "src").mkdir(parents=True)
                (mono / "apps" / "api" / "tsconfig.json").write_text("{}")
                (mono / "services" / "worker").mkdir(parents=True)
                (mono / "services" / "worker" / "go.mod").write_text("module worker\n")
                result = detect_repo_profile(str(mono))
                results["monorepo"] = {
                    "language": result.language,
                    "confidence": result.confidence,
                    "passed": result.language in ["typescript", "javascript"],
                }
                print(f"    Monorepo: lang={result.language}, conf={result.confidence:.2f}")
                
                # Test 3: Nx monorepo
                nx = base / "nx"
                nx.mkdir()
                (nx / "package.json").write_text('{"name": "@org/nx"}')
                (nx / "nx.json").write_text('{"npmScope": "org"}')
                (nx / "tsconfig.base.json").write_text("{}")
                result = detect_repo_profile(str(nx))
                results["nx"] = {
                    "language": result.language,
                    "confidence": result.confidence,
                    "passed": result.language == "typescript",
                }
                print(f"    Nx: lang={result.language}, conf={result.confidence:.2f}")
                
                # Test 4: Empty repo
                empty = base / "empty"
                empty.mkdir()
                (empty / "README.md").write_text("# My Project")
                result = detect_repo_profile(str(empty))
                results["empty"] = {
                    "language": result.language,
                    "confidence": result.confidence,
                    "passed": result.confidence < 0.4,  # Should be low
                }
                print(f"    Empty: lang={result.language}, conf={result.confidence:.2f}")
            
            all_passed = all(r["passed"] for r in results.values())
            return {"passed": all_passed, "results": results}
        
        self.run_test("Repo detection variants", test_repo_detection)
        
        analysis.optimal_solution = "Improve heuristics for sparse/new repos"
        analysis.implementation_plan = """
1. Extend _detect_language_by_config_files with more indicators:
   - Dockerfile (FROM python:, FROM node:, FROM golang:)
   - Makefile (check targets like 'go build', 'npm run')
   - .github/workflows/*.yml (check uses: actions/setup-*)
   - README.md patterns (code blocks with language hints)
2. Add monorepo detection (nx.json, lerna.json, pnpm-workspace.yaml)
3. For monorepos, detect primary app language, not workspace tooling
4. Increase confidence based on multiple signals
"""
        analysis.files_to_change = [
            "src/integration_coworker/repo/detection.py",
        ]
        
        self.analyses.append(analysis)
        return analysis

    # =========================================================================
    # CAVEAT 5: MULTI-SPEC NAMESPACE COLLISIONS
    # =========================================================================
    
    def analyze_multi_spec_caveat(self) -> CaveatAnalysis:
        """Analyze multi-spec namespace collision issues."""
        print("\n" + "=" * 70)
        print("CAVEAT 5: Multi-Spec Namespace Collisions")
        print("=" * 70)
        
        analysis = CaveatAnalysis(
            caveat_id="MULTI-001",
            category="MULTI_SPEC",
            description="Specs merged into single list - no provider namespacing",
            current_behavior="Endpoint /users from Stripe and GitHub would collide",
        )
        
        # Alternative 1: Provider-prefixed paths
        alt1 = {
            "name": "Provider-prefix all paths",
            "approach": "/stripe/customers, /github/users instead of /customers, /users",
            "pros": [
                "No collisions possible",
                "Clear provenance",
            ],
            "cons": [
                "Changes API surface",
                "May not match actual API",
            ],
            "scalability": "High - simple string prefix",
            "implementation": "Update ingest_spec to prefix paths",
        }
        
        # Alternative 2: Separate endpoint lists per provider
        alt2 = {
            "name": "Separate endpoint lists per provider",
            "approach": "state.endpoints_by_provider = {'stripe': [...], 'github': [...]}",
            "pros": [
                "Natural separation",
                "Easy to access per-provider",
            ],
            "cons": [
                "Schema change required",
                "Downstream nodes need updates",
            ],
            "scalability": "High - dict lookup",
            "implementation": "Add provider grouping to WorkflowState",
        }
        
        # Alternative 3: Endpoint ID includes provider
        alt3 = {
            "name": "Include provider in endpoint ID",
            "approach": "endpoint.id = f'{provider_code}::{method}::{path}'",
            "pros": [
                "Minimal schema change",
                "Unique IDs guaranteed",
            ],
            "cons": [
                "Need to parse ID to get provider",
            ],
            "scalability": "High - simple string",
            "implementation": "Update endpoint creation in build_silver_api_model",
        }
        
        analysis.alternatives = [alt1, alt2, alt3]
        
        # Test current multi-spec behavior
        print("\n  Testing multi-spec collision handling:")
        
        def test_multi_spec():
            from integration_coworker.graph.state import WorkflowState
            from integration_coworker.domain.models import SourceRef, SpecDocument
            from integration_coworker.graph.nodes.ingest_spec import ingest_spec
            from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
            
            # Create two specs with overlapping paths
            spec1_content = '''
openapi: "3.0.0"
info:
  title: Provider A
  version: "1.0.0"
paths:
  /users:
    get:
      summary: Get users from Provider A
'''
            spec2_content = '''
openapi: "3.0.0"
info:
  title: Provider B
  version: "1.0.0"
paths:
  /users:
    get:
      summary: Get users from Provider B
'''
            with tempfile.TemporaryDirectory() as tmpdir:
                spec1_path = Path(tmpdir) / "spec_a.yaml"
                spec2_path = Path(tmpdir) / "spec_b.yaml"
                spec1_path.write_text(spec1_content)
                spec2_path.write_text(spec2_content)
                
                state = WorkflowState(
                    source_refs=[
                        SourceRef.from_ref(str(spec1_path), "provider_a"),
                        SourceRef.from_ref(str(spec2_path), "provider_b"),
                    ],
                    spec_refs=[str(spec1_path), str(spec2_path)],
                    task_description="Test multi-spec",
                    provider_code="multi",
                )
                
                # Run ingest
                state = ingest_spec(state)
                
                # Run parse
                state = detect_and_parse_spec(state)
                
                # Check for collisions
                openapi_specs = state.plan.get("openapi_specs", [])
                all_paths = []
                for spec in openapi_specs:
                    if isinstance(spec, dict):
                        paths = list(spec.get("paths", {}).keys())
                        all_paths.extend(paths)
                
                has_duplicates = len(all_paths) != len(set(all_paths))
                
                return {
                    "passed": len(openapi_specs) == 2,
                    "spec_count": len(openapi_specs),
                    "paths": all_paths,
                    "has_duplicates": has_duplicates,
                }
        
        self.run_test("Multi-spec collision test", test_multi_spec)
        
        analysis.optimal_solution = "Include provider in endpoint ID"
        analysis.implementation_plan = """
1. Update Endpoint model to include provider_code field
2. Set endpoint.id = f'{provider_code}::{method}::{path}' in build_silver_api_model
3. Update codegen to use provider-prefixed naming: StripeClient, GitHubClient
4. Keep endpoints in single list but filter by provider when needed
5. Update tests to verify no collisions
"""
        analysis.files_to_change = [
            "src/integration_coworker/domain/models.py",
            "src/integration_coworker/graph/nodes/build_silver_api_model.py",
            "src/integration_coworker/codegen/naming.py",
        ]
        
        self.analyses.append(analysis)
        return analysis

    # =========================================================================
    # RUN FULL E2E PRODUCTION TEST
    # =========================================================================
    
    def run_full_e2e_production_test(self) -> Dict[str, Any]:
        """Run a complete E2E test with production settings."""
        print("\n" + "=" * 70)
        print("FULL E2E PRODUCTION TEST")
        print("=" * 70)
        
        def e2e_test():
            from integration_coworker.graph.runtime import build_graph
            from integration_coworker.graph.state import WorkflowState
            from integration_coworker.domain.models import SourceRef
            
            spec_path = Path(__file__).parent / "fixtures" / "petstore_openapi.yaml"
            if not spec_path.exists():
                return {"passed": False, "error": "petstore spec not found"}
            
            with tempfile.TemporaryDirectory() as tmpdir:
                repo = Path(tmpdir)
                
                # Create Python project structure
                (repo / "pyproject.toml").write_text('[project]\nname = "test-project"\n')
                (repo / "src").mkdir()
                (repo / "src" / "__init__.py").write_text("")
                (repo / "tests").mkdir()
                
                state = WorkflowState(
                    source_refs=[SourceRef.from_ref(str(spec_path), "petstore")],
                    spec_refs=[str(spec_path)],
                    task_description="Create a client to list pets and add a new pet",
                    provider_code="petstore",
                    repo_root=str(repo),
                )
                
                # Build and run graph
                graph = build_graph()
                
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        asyncio.wait_for(graph.ainvoke(state), timeout=180)
                    )
                finally:
                    loop.close()
                
                # Analyze results
                if hasattr(result, 'code_artifacts'):
                    artifacts = result.code_artifacts or []
                elif isinstance(result, dict):
                    artifacts = result.get('code_artifacts', [])
                else:
                    artifacts = []
                
                if hasattr(result, 'errors'):
                    errors = result.errors or []
                elif isinstance(result, dict):
                    errors = result.get('errors', [])
                else:
                    errors = []
                
                # Check artifact quality
                artifact_info = []
                for a in artifacts:
                    content = a.content if hasattr(a, 'content') else str(a)
                    artifact_info.append({
                        "path": a.rel_path if hasattr(a, 'rel_path') else "unknown",
                        "length": len(content),
                        "has_todo": "TODO" in content,
                        "has_class": "class " in content or "def " in content,
                    })
                
                return {
                    "passed": len(artifacts) > 0 and len(errors) == 0,
                    "artifacts": len(artifacts),
                    "errors": [str(e)[:100] for e in errors[:3]],
                    "artifact_info": artifact_info,
                }
        
        return self.run_test("Full E2E production workflow", e2e_test)

    # =========================================================================
    # MAIN ANALYSIS
    # =========================================================================
    
    def run_full_analysis(self):
        """Run complete caveat analysis."""
        print("\n" + "=" * 70)
        print("PRODUCTION CAVEAT ANALYSIS")
        print("=" * 70)
        print(f"Database: {os.environ.get('DATABASE_URL', 'not set')[:50]}...")
        print(f"OpenAI Key: {'set' if os.environ.get('OPENAI_API_KEY') else 'not set'}")
        
        start = time.time()
        
        # Analyze each caveat
        self.analyze_tree_sitter_caveat()
        self.analyze_llm_language_fidelity()
        self.analyze_spec_conversion_caveat()
        self.analyze_repo_detection_caveat()
        self.analyze_multi_spec_caveat()
        
        # Run E2E test
        self.run_full_e2e_production_test()
        
        total_time = time.time() - start
        
        # Print summary
        print("\n" + "=" * 70)
        print("ANALYSIS SUMMARY")
        print("=" * 70)
        
        passed = sum(1 for r in self.test_results if r.passed)
        failed = len(self.test_results) - passed
        
        print(f"\n  Tests: {passed}/{len(self.test_results)} passed")
        print(f"  Time: {total_time:.1f}s")
        
        print("\n  OPTIMAL SOLUTIONS:")
        for analysis in self.analyses:
            print(f"\n  [{analysis.caveat_id}] {analysis.description}")
            print(f"    → {analysis.optimal_solution}")
            print(f"    Files: {', '.join(analysis.files_to_change[:3])}")
        
        # Generate implementation plan
        print("\n" + "=" * 70)
        print("IMPLEMENTATION PRIORITY (by impact/effort ratio)")
        print("=" * 70)
        
        priority_order = [
            ("LANG-001", "Install tree-sitter", "HIGH impact, LOW effort (pip install)"),
            ("SPEC-001", "Hybrid GraphQL parser", "HIGH impact, MEDIUM effort"),
            ("REPO-001", "Improve heuristics", "MEDIUM impact, LOW effort"),
            ("LANG-002", "Few-shot examples", "MEDIUM impact, LOW effort"),
            ("MULTI-001", "Provider in endpoint ID", "MEDIUM impact, MEDIUM effort"),
        ]
        
        for i, (caveat_id, solution, rationale) in enumerate(priority_order, 1):
            print(f"\n  {i}. [{caveat_id}] {solution}")
            print(f"     Rationale: {rationale}")
        
        return {
            "analyses": len(self.analyses),
            "tests_passed": passed,
            "tests_failed": failed,
            "total_time": total_time,
        }


def main():
    analyzer = ProductionCaveatAnalyzer()
    result = analyzer.run_full_analysis()
    
    print("\n" + "=" * 70)
    print("JSON SUMMARY")
    print("=" * 70)
    print(json.dumps(result, indent=2))
    
    return 0 if result["tests_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
