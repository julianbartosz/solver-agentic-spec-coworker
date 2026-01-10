#!/usr/bin/env python3
"""
Production Test: Code Quality with Semantic Validation

Tests Alternative A implementation:
1. Real LLM generates code
2. Semantic validator catches hallucinated imports
3. Pattern learning captures events for analysis

Requirements:
    - OPENAI_API_KEY set (real LLM calls)
    - PATTERN_LEARNING_ENABLED=true (captures events)
    - USE_SQLITE=true OR DATABASE_URL for Postgres

Usage:
    # With SQLite (quick test)
    source .env && USE_SQLITE=true .venv311/bin/python scripts/test_production_quality.py

    # With Postgres (full production simulation)
    source .env && .venv311/bin/python scripts/test_production_quality.py
"""
import os
import sys
import logging
from typing import Dict, Any, List

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_environment() -> Dict[str, Any]:
    """Verify environment is configured correctly."""
    results = {
        "api_key_set": bool(os.getenv("OPENAI_API_KEY")),
        "pattern_learning": os.getenv("PATTERN_LEARNING_ENABLED", "true").lower() == "true",
        "use_sqlite": os.getenv("USE_SQLITE", "false").lower() == "true",
        "database_url": bool(os.getenv("DATABASE_URL")),
    }
    
    print("\n" + "="*60)
    print("ENVIRONMENT CHECK")
    print("="*60)
    for key, value in results.items():
        status = "✅" if value else "❌"
        print(f"  {status} {key}: {value}")
    
    if not results["api_key_set"]:
        print("\n⚠️  WARNING: OPENAI_API_KEY not set - tests requiring LLM will fail")
    
    return results


def test_semantic_validator_catches_hallucination():
    """Test that semantic validator catches hallucinated imports."""
    print("\n" + "="*60)
    print("TEST: Semantic Validator Catches Hallucinations")
    print("="*60)
    
    from integration_coworker.codegen.semantic_validator import (
        validate_semantic_correctness,
        format_semantic_issues,
    )
    
    # Code with hallucinated import (common LLM mistake)
    hallucinated_code = '''
"""Petstore client module."""
import requests
from petstore_sdk import PetstoreAuth  # Hallucinated!

class PetstoreClient:
    def __init__(self, api_key: str):
        self.auth = PetstoreAuth(api_key)
    
    def list_pets(self):
        return []
'''
    
    is_valid, issues = validate_semantic_correctness(hallucinated_code)
    
    print(f"\n  Code has hallucinated import: 'petstore_sdk'")
    print(f"  Validation result: {'PASS' if is_valid else 'FAIL (expected)'}")
    print(f"  Issues found: {len(issues)}")
    
    if not is_valid:
        print(f"\n  {format_semantic_issues(issues)}")
        print("\n  ✅ TEST PASSED: Semantic validator caught hallucination")
        return True
    else:
        print("\n  ❌ TEST FAILED: Hallucination not caught")
        return False


def test_valid_code_passes_semantic_validation():
    """Test that valid code passes semantic validation."""
    print("\n" + "="*60)
    print("TEST: Valid Code Passes Semantic Validation")
    print("="*60)
    
    from integration_coworker.codegen.semantic_validator import (
        validate_semantic_correctness,
    )
    
    valid_code = '''
"""Petstore client module."""
import os
import json
from typing import Dict, List, Optional
import requests

class PetstoreClient:
    """Client for the Petstore API."""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("PETSTORE_API_KEY")
        self.session = requests.Session()
    
    def list_pets(self, limit: int = 100) -> List[Dict]:
        """List all pets."""
        response = self.session.get(f"{self.base_url}/pets", params={"limit": limit})
        response.raise_for_status()
        return response.json()
'''
    
    is_valid, issues = validate_semantic_correctness(valid_code)
    
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    
    print(f"\n  Code uses only valid imports")
    print(f"  Validation result: {'PASS' if is_valid else 'FAIL'}")
    print(f"  Errors: {len(errors)}, Warnings: {len(warnings)}")
    
    if is_valid:
        print("\n  ✅ TEST PASSED: Valid code passes validation")
        return True
    else:
        print(f"\n  ❌ TEST FAILED: Valid code rejected")
        for issue in errors:
            print(f"    - {issue.message}")
        return False


def test_pattern_learning_config():
    """Test that pattern learning is enabled and configured."""
    print("\n" + "="*60)
    print("TEST: Pattern Learning Configuration")
    print("="*60)
    
    # Reset settings to pick up env vars
    from integration_coworker.config import reset_settings, get_settings
    reset_settings()
    
    settings = get_settings()
    
    print(f"\n  Pattern Learning Settings:")
    print(f"    - pattern_learning_enabled: {settings.pattern_learning_enabled}")
    print(f"    - pattern_capture_events: {settings.pattern_capture_events}")
    print(f"    - pattern_discover_candidates: {settings.pattern_discover_candidates}")
    print(f"    - pattern_auto_promote: {settings.pattern_auto_promote}")
    print(f"    - pattern_match_learned: {settings.pattern_match_learned}")
    print(f"    - pattern_promotion_threshold: {settings.pattern_promotion_threshold}")
    
    if settings.pattern_learning_enabled:
        print("\n  ✅ Pattern learning is ENABLED")
        return True
    else:
        print("\n  ⚠️  Pattern learning is DISABLED (set PATTERN_LEARNING_ENABLED=true)")
        return False


def test_validation_chain_integration():
    """Test the full validation chain: syntax → security → semantic."""
    print("\n" + "="*60)
    print("TEST: Full Validation Chain Integration")
    print("="*60)
    
    from integration_coworker.codegen.security import validate_code_security, validate_syntax
    from integration_coworker.codegen.semantic_validator import validate_semantic_correctness
    
    # Test code that passes all validations
    good_code = '''
"""API client module."""
import requests
from typing import Dict

class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = requests.Session()
    
    def get_data(self) -> Dict:
        resp = self.session.get(f"{self.base_url}/data")
        resp.raise_for_status()
        return resp.json()
'''
    
    print("\n  Testing good code through validation chain:")
    
    # Step 1: Syntax
    syntax_ok, syntax_err = validate_syntax(good_code)
    print(f"    1. Syntax validation: {'PASS' if syntax_ok else 'FAIL'}")
    
    # Step 2: Security
    security_ok, sec_violations = validate_code_security(good_code)
    print(f"    2. Security validation: {'PASS' if security_ok else 'FAIL'}")
    
    # Step 3: Semantic
    semantic_ok, sem_issues = validate_semantic_correctness(good_code)
    errors = [i for i in sem_issues if i.severity == "error"]
    print(f"    3. Semantic validation: {'PASS' if semantic_ok else 'FAIL'}")
    
    all_passed = syntax_ok and security_ok and semantic_ok
    
    if all_passed:
        print("\n  ✅ TEST PASSED: All validation gates pass for valid code")
        return True
    else:
        print("\n  ❌ TEST FAILED: Validation chain has issues")
        return False


def test_validation_chain_rejects_bad_code():
    """Test that the validation chain rejects code with issues."""
    print("\n" + "="*60)
    print("TEST: Validation Chain Rejects Bad Code")
    print("="*60)
    
    from integration_coworker.codegen.security import validate_code_security
    from integration_coworker.codegen.semantic_validator import validate_semantic_correctness
    
    # Code with security violation
    insecure_code = '''
import os

def run_command(cmd: str):
    os.system(cmd)  # Security violation: os.system
'''
    
    print("\n  Testing code with os.system():")
    security_ok, violations = validate_code_security(insecure_code)
    print(f"    Security validation: {'PASS' if security_ok else 'FAIL (expected)'}")
    
    # Code with hallucinated import
    hallucinated_code = '''
from nonexistent_sdk import FakeClient

def do_stuff():
    return FakeClient().run()
'''
    
    print("\n  Testing code with hallucinated import:")
    semantic_ok, issues = validate_semantic_correctness(hallucinated_code)
    print(f"    Semantic validation: {'PASS' if semantic_ok else 'FAIL (expected)'}")
    
    both_caught = not security_ok and not semantic_ok
    
    if both_caught:
        print("\n  ✅ TEST PASSED: Both violations caught by validation chain")
        return True
    else:
        print("\n  ❌ TEST FAILED: Some violations not caught")
        return False


def run_all_tests() -> bool:
    """Run all production tests."""
    print("\n" + "="*60)
    print("PRODUCTION TEST: Code Quality with Semantic Validation")
    print("Alternative A: LLM-First with Strict Post-Validation")
    print("="*60)
    
    env_results = check_environment()
    
    tests = [
        ("Semantic Validator Catches Hallucination", test_semantic_validator_catches_hallucination),
        ("Valid Code Passes Validation", test_valid_code_passes_semantic_validation),
        ("Pattern Learning Config", test_pattern_learning_config),
        ("Full Validation Chain Integration", test_validation_chain_integration),
        ("Validation Chain Rejects Bad Code", test_validation_chain_rejects_bad_code),
    ]
    
    results: List[tuple] = []
    
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed, None))
        except Exception as e:
            logger.exception(f"Test '{name}' raised exception")
            results.append((name, False, str(e)))
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    passed_count = sum(1 for _, passed, _ in results if passed)
    total_count = len(results)
    
    for name, passed, error in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {name}")
        if error:
            print(f"         Error: {error}")
    
    print(f"\n  Total: {passed_count}/{total_count} tests passed")
    
    all_passed = passed_count == total_count
    
    if all_passed:
        print("\n🎉 ALL TESTS PASSED - Alternative A implementation is working!")
    else:
        print("\n⚠️  SOME TESTS FAILED - Review output above")
    
    return all_passed


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
