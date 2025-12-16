#!/usr/bin/env python3
"""Comprehensive Production Test Suite"""

import os
import sys
import time
import tempfile
import traceback
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path
import json

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@dataclass
class Bug:
    id: int
    title: str
    severity: str
    feature: str
    description: str
    test_name: str
    stack_trace: Optional[str] = None


@dataclass
class TestResult:
    name: str
    passed: bool
    duration: float
    error: Optional[str] = None
    bug: Optional[Bug] = None


class ProductionTestSuite:
    def __init__(self):
        self.results: List[TestResult] = []
        self.all_bugs: List[Bug] = []
        self._bug_counter = 0
        
    def _next_bug_id(self) -> int:
        self._bug_counter += 1
        return self._bug_counter
        
    def _record_bug(self, title, severity, feature, description, test_name, stack_trace=None):
        bug = Bug(
            id=self._next_bug_id(),
            title=title,
            severity=severity,
            feature=feature,
            description=description,
            test_name=test_name,
            stack_trace=stack_trace,
        )
        self.all_bugs.append(bug)
        return bug
        
    def run_test(self, name, test_func, feature):
        print(f"\n  [{feature}] {name}...", end=" ", flush=True)
        start = time.time()
        
        try:
            test_func()
            duration = time.time() - start
            print(f"PASS ({duration:.2f}s)")
            self.results.append(TestResult(name=name, passed=True, duration=duration))
        except AssertionError as e:
            duration = time.time() - start
            stack = traceback.format_exc()
            print(f"FAIL - ASSERTION")
            bug = self._record_bug(f"Assertion in {name}", "high", feature, str(e), name, stack)
            self.results.append(TestResult(name=name, passed=False, duration=duration, error=str(e), bug=bug))
        except Exception as e:
            duration = time.time() - start
            stack = traceback.format_exc()
            print(f"FAIL - {type(e).__name__}")
            bug = self._record_bug(f"Exception in {name}", "critical", feature, str(e), name, stack)
            self.results.append(TestResult(name=name, passed=False, duration=duration, error=str(e), bug=bug))

    def test_database(self):
        print("\n" + "=" * 70)
        print("SECTION 1: Database Tests")
        print("=" * 70)
        
        def test_connection():
            from integration_coworker.persistence.db import get_engine
            from sqlalchemy import text
            engine = get_engine()
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                assert result.fetchone()[0] == 1
        self.run_test("db_connection", test_connection, "database")
        
        def test_tables():
            from integration_coworker.persistence.db import get_engine, ensure_tables
            engine = get_engine()
            ensure_tables(engine)
        self.run_test("ensure_tables", test_tables, "database")

    def test_llm(self):
        print("\n" + "=" * 70)
        print("SECTION 2: LLM Tests")
        print("=" * 70)
        
        def test_call():
            from integration_coworker.llm.dispatch import call_llm_for_node
            result = call_llm_for_node("test", "Return OK", "You are helpful.")
            assert result is not None
            assert len(result) > 0
        self.run_test("llm_call", test_call, "llm")

    def test_spec_detection(self):
        print("\n" + "=" * 70)
        print("SECTION 3: Spec Detection Tests")
        print("=" * 70)
        
        def test_openapi():
            from integration_coworker.graph.nodes.detect_and_parse_spec import _detect_spec_format
            spec = '{"openapi": "3.0.0"}'
            assert _detect_spec_format(spec) == "openapi"
        self.run_test("openapi_detection", test_openapi, "spec")
        
        def test_graphql():
            from integration_coworker.graph.nodes.detect_and_parse_spec import _is_graphql_content
            schema = "type Query { user(id: ID!): User }"
            assert _is_graphql_content(schema) is True
        self.run_test("graphql_detection", test_graphql, "spec")
        
        def test_asyncapi():
            from integration_coworker.graph.nodes.detect_and_parse_spec import _is_asyncapi_content
            spec = "asyncapi: 2.0.0\ninfo:\n  title: Test"
            assert _is_asyncapi_content(spec) is True
        self.run_test("asyncapi_detection", test_asyncapi, "spec")

    def test_codegen(self):
        print("\n" + "=" * 70)
        print("SECTION 4: Code Generation Tests")
        print("=" * 70)
        
        def test_python():
            from integration_coworker.graph.nodes.generate_code_and_tests import generate_client_code_for_language
            endpoint = {"path": "/users", "method": "GET", "summary": "Get users", "parameters": []}
            result = generate_client_code_for_language([endpoint], "python")
            assert result is not None
            assert "def " in result or "class " in result
        self.run_test("python_codegen", test_python, "codegen")
        
        def test_typescript():
            from integration_coworker.graph.nodes.generate_code_and_tests import generate_client_code_for_language
            endpoint = {"path": "/users", "method": "GET", "summary": "Get users", "parameters": []}
            result = generate_client_code_for_language([endpoint], "typescript")
            assert result is not None
        self.run_test("typescript_codegen", test_typescript, "codegen")

    def test_repo_detection(self):
        print("\n" + "=" * 70)
        print("SECTION 5: Repo Detection Tests")
        print("=" * 70)
        
        def test_python_repo():
            from integration_coworker.graph.nodes.analyze_repo_layout import detect_repo_language_and_structure
            with tempfile.TemporaryDirectory() as tmpdir:
                Path(tmpdir, "setup.py").write_text("# setup")
                Path(tmpdir, "requirements.txt").write_text("requests")
                result = detect_repo_language_and_structure(tmpdir)
                assert result is not None
        self.run_test("python_repo", test_python_repo, "repo")
        
        def test_ts_repo():
            from integration_coworker.graph.nodes.analyze_repo_layout import detect_repo_language_and_structure
            with tempfile.TemporaryDirectory() as tmpdir:
                Path(tmpdir, "package.json").write_text('{"name": "test"}')
                Path(tmpdir, "tsconfig.json").write_text('{}')
                result = detect_repo_language_and_structure(tmpdir)
                assert result is not None
        self.run_test("typescript_repo", test_ts_repo, "repo")

    def test_sandbox(self):
        print("\n" + "=" * 70)
        print("SECTION 6: Test Execution Sandbox Tests")
        print("=" * 70)
        
        def test_disabled():
            from integration_coworker.runtime.test_execution import is_test_execution_enabled
            old = os.environ.pop("ENABLE_TEST_EXECUTION", None)
            try:
                assert is_test_execution_enabled() is False
            finally:
                if old: os.environ["ENABLE_TEST_EXECUTION"] = old
        self.run_test("sandbox_disabled", test_disabled, "sandbox")
        
        def test_enabled():
            from integration_coworker.runtime.test_execution import is_test_execution_enabled
            old = os.environ.get("ENABLE_TEST_EXECUTION")
            try:
                os.environ["ENABLE_TEST_EXECUTION"] = "true"
                assert is_test_execution_enabled() is True
            finally:
                if old: os.environ["ENABLE_TEST_EXECUTION"] = old
                else: os.environ.pop("ENABLE_TEST_EXECUTION", None)
        self.run_test("sandbox_enabled", test_enabled, "sandbox")

    def test_security(self):
        print("\n" + "=" * 70)
        print("SECTION 7: Security Validation Tests")
        print("=" * 70)
        
        def test_dangerous():
            from integration_coworker.graph.nodes.validate_integration_design import _check_security_patterns
            code = "import os\nos.system('rm -rf /')"
            issues = _check_security_patterns(code, "python")
            assert len(issues) > 0
        self.run_test("dangerous_patterns", test_dangerous, "security")

    def test_e2e(self):
        print("\n" + "=" * 70)
        print("SECTION 8: End-to-End Tests")
        print("=" * 70)
        
        def test_minimal_e2e():
            from integration_coworker.public_api import design_and_generate_integration
            spec = {"openapi": "3.0.0", "info": {"title": "Test", "version": "1.0.0"},
                    "paths": {"/users": {"get": {"summary": "Get users", "responses": {"200": {"description": "OK"}}}}}}
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(spec, f)
                spec_path = f.name
            try:
                result = design_and_generate_integration(spec_refs=[spec_path], task_description="Create client")
                assert result is not None
            finally:
                os.unlink(spec_path)
        self.run_test("minimal_e2e", test_minimal_e2e, "e2e")

    def run_all(self):
        print("\n" + "=" * 70)
        print("COMPREHENSIVE PRODUCTION TEST SUITE")
        print("=" * 70)
        print(f"LLM_MODE: {os.environ.get('LLM_MODE', 'mock')}")
        
        start = time.time()
        self.test_database()
        self.test_llm()
        self.test_spec_detection()
        self.test_codegen()
        self.test_repo_detection()
        self.test_sandbox()
        self.test_security()
        self.test_e2e()
        
        total = time.time() - start
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed
        
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Total: {len(self.results)} | Passed: {passed} | Failed: {failed} | Time: {total:.2f}s")
        
        if self.all_bugs:
            print(f"\nBUGS FOUND: {len(self.all_bugs)}")
            for bug in self.all_bugs:
                print(f"\n  #{bug.id}: {bug.title}")
                print(f"    Severity: {bug.severity}, Feature: {bug.feature}")
                print(f"    {bug.description[:200]}")
                if bug.stack_trace:
                    lines = bug.stack_trace.split('\n')[-5:]
                    print("    Stack:\n      " + "\n      ".join(lines))
        
        return len(self.all_bugs)


if __name__ == "__main__":
    suite = ProductionTestSuite()
    bugs = suite.run_all()
    sys.exit(1 if bugs > 0 else 0)
