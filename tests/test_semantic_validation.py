"""
Tests for Semantic Validation.

Part of Section 4: Execution - Code Quality Improvements.

Tests the semantic validation layer that validates:
- Import resolution
- Class signature matching
- Function existence
- Docstring presence
"""
import pytest

from integration_coworker.codegen.semantic_validator import (
    SemanticIssue,
    validate_imports,
    validate_class_signature,
    validate_function_exists,
    validate_docstrings,
    validate_semantic_correctness,
    format_semantic_issues,
)


class TestValidateImports:
    """Tests for import validation."""
    
    def test_stdlib_imports_pass(self):
        """Standard library imports should pass validation."""
        code = """
import os
import sys
import json
from pathlib import Path
from typing import Dict, List, Optional
"""
        issues = validate_imports(code)
        assert len(issues) == 0, f"Unexpected issues: {issues}"
    
    def test_known_third_party_imports_pass(self):
        """Known third-party imports should pass validation."""
        code = """
import requests
import pydantic
from openai import OpenAI
from fastapi import FastAPI
"""
        issues = validate_imports(code)
        assert len(issues) == 0, f"Unexpected issues: {issues}"
    
    def test_unknown_module_fails(self):
        """Unknown modules should be flagged as errors."""
        code = """
import nonexistent_module_xyz
from another_fake_package import something
"""
        issues = validate_imports(code)
        
        # Should have 2 issues (one per unknown import)
        assert len(issues) == 2
        assert all(i.issue_type == "invalid_import" for i in issues)
        assert all(i.severity == "error" for i in issues)
    
    def test_local_package_imports_pass(self):
        """Imports from integration_coworker should pass."""
        code = """
from integration_coworker.codegen import CodegenContext
from integration_coworker.persistence import db
"""
        issues = validate_imports(code)
        assert len(issues) == 0
    
    def test_syntax_error_returns_empty(self):
        """Code with syntax errors should return empty list (syntax checked elsewhere)."""
        code = "import this is not valid"
        issues = validate_imports(code)
        assert len(issues) == 0


class TestValidateClassSignature:
    """Tests for class signature validation."""
    
    def test_class_with_all_methods_passes(self):
        """Class with all expected methods should pass."""
        code = """
class PetstoreClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
    
    def list_pets(self):
        pass
    
    def get_pet(self, pet_id: int):
        pass
"""
        issues = validate_class_signature(
            code, 
            "PetstoreClient", 
            ["list_pets", "get_pet"]
        )
        assert len(issues) == 0
    
    def test_missing_method_fails(self):
        """Missing required method should be flagged."""
        code = """
class PetstoreClient:
    def __init__(self):
        pass
    
    def list_pets(self):
        pass
"""
        issues = validate_class_signature(
            code,
            "PetstoreClient",
            ["list_pets", "get_pet", "create_pet"]
        )
        
        # Should flag get_pet and create_pet as missing
        assert len(issues) == 2
        assert all(i.issue_type == "missing_method" for i in issues)
        
        missing_methods = {i.context["method"] for i in issues}
        assert missing_methods == {"get_pet", "create_pet"}
    
    def test_class_not_found_fails(self):
        """Missing class should be flagged."""
        code = """
class SomeOtherClass:
    pass
"""
        issues = validate_class_signature(
            code,
            "PetstoreClient",
            ["list_pets"]
        )
        
        assert len(issues) == 1
        assert issues[0].issue_type == "missing_class"
    
    def test_async_methods_detected(self):
        """Async methods should be detected."""
        code = """
class AsyncClient:
    async def fetch_data(self):
        pass
"""
        issues = validate_class_signature(
            code,
            "AsyncClient",
            ["fetch_data"]
        )
        assert len(issues) == 0


class TestValidateFunctionExists:
    """Tests for function existence validation."""
    
    def test_function_exists_passes(self):
        """Existing function should pass."""
        code = """
def run_petstore_flow(client: PetstoreClient, config: dict):
    return client.list_pets()
"""
        issues = validate_function_exists(code, "run_petstore_flow")
        assert len(issues) == 0
    
    def test_missing_function_fails(self):
        """Missing function should be flagged."""
        code = """
def some_other_function():
    pass
"""
        issues = validate_function_exists(code, "run_petstore_flow")
        
        assert len(issues) == 1
        assert issues[0].issue_type == "missing_function"
    
    def test_async_function_detected(self):
        """Async functions should be detected."""
        code = """
async def async_flow():
    pass
"""
        issues = validate_function_exists(code, "async_flow")
        assert len(issues) == 0
    
    def test_min_params_check(self):
        """Function parameter count should be validated."""
        code = """
def my_function(a):
    pass
"""
        issues = validate_function_exists(code, "my_function", min_params=3)
        
        assert len(issues) == 1
        assert issues[0].issue_type == "signature_mismatch"
        assert issues[0].severity == "warning"


class TestValidateDocstrings:
    """Tests for docstring validation."""
    
    def test_fully_documented_passes(self):
        """Fully documented code should pass."""
        code = '''
"""Module docstring."""

class MyClass:
    """Class docstring."""
    
    def my_method(self):
        """Method docstring."""
        pass

def my_function():
    """Function docstring."""
    pass
'''
        issues = validate_docstrings(code, require_module=True)
        assert len(issues) == 0
    
    def test_missing_module_docstring(self):
        """Missing module docstring should be flagged."""
        code = """
class MyClass:
    \"\"\"Class docstring.\"\"\"
    pass
"""
        issues = validate_docstrings(code, require_module=True)
        
        assert len(issues) == 1
        assert issues[0].issue_type == "missing_docstring"
        assert issues[0].context["scope"] == "module"
    
    def test_private_functions_ignored(self):
        """Private functions (leading underscore) should not require docstrings."""
        code = '''
"""Module."""

def _private_helper():
    pass

class _PrivateClass:
    pass
'''
        issues = validate_docstrings(code)
        assert len(issues) == 0


class TestValidateSemanticCorrectness:
    """Tests for comprehensive semantic validation."""
    
    def test_valid_code_passes(self):
        """Valid code should pass all checks."""
        code = """
import json
import os
from typing import Dict

class PetstoreClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
    
    def list_pets(self) -> Dict:
        return {}

def run_flow():
    client = PetstoreClient("http://localhost")
    return client.list_pets()
"""
        is_valid, issues = validate_semantic_correctness(code)
        
        assert is_valid is True
        # No errors (may have warnings)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0
    
    def test_bad_import_fails(self):
        """Invalid imports should fail validation."""
        code = """
import nonexistent_xyz
from fake_package import nothing
"""
        is_valid, issues = validate_semantic_correctness(code)
        
        assert is_valid is False
        assert len(issues) >= 2
    
    def test_with_codegen_context(self):
        """Validation with CodegenContext should check expected artifacts."""
        from integration_coworker.codegen.context import CodegenContext
        
        code = """
import requests

class PetstoreClient:
    def list_pets(self):
        return []

def run_petstore_flow():
    return PetstoreClient().list_pets()
"""
        context = CodegenContext(
            provider_code="petstore",
            task_slug="list_pets",
            client_module="petstore",
            client_class="PetstoreClient",
            method_name="list_pets",
            client_import_path="integrations.clients.petstore",
            flow_module="petstore_flow",
            flow_function="run_petstore_flow",
            flow_import_path="integrations.flows.petstore_flow",
            test_module="test_petstore",
            test_class="TestPetstoreFlow",
            clients_dir="src/integrations/clients",
            flows_dir="src/integrations/flows",
            tests_dir="tests/integrations",
        )
        
        is_valid, issues = validate_semantic_correctness(code, context=context)
        
        # Should pass - has expected class and method
        assert is_valid is True


class TestFormatSemanticIssues:
    """Tests for issue formatting."""
    
    def test_format_empty_issues(self):
        """Empty issue list should produce friendly message."""
        result = format_semantic_issues([])
        assert "No semantic issues" in result
    
    def test_format_with_issues(self):
        """Issues should be formatted with severity and line numbers."""
        issues = [
            SemanticIssue(
                line=10,
                issue_type="invalid_import",
                message="Module 'xyz' cannot be resolved",
                severity="error",
            ),
            SemanticIssue(
                line=5,
                issue_type="missing_docstring",
                message="Function 'foo' missing docstring",
                severity="warning",
            ),
        ]
        
        result = format_semantic_issues(issues)
        
        assert "ERROR" in result
        assert "WARNING" in result
        assert "Line 10" in result
        assert "Line 5" in result


class TestIntegrationWithRealCode:
    """Integration tests with realistic generated code patterns."""
    
    def test_typical_client_code_passes(self):
        """Typical LLM-generated client code should pass validation."""
        code = '''
"""
Petstore API Client

Auto-generated client for the Petstore API.
"""
import os
import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

import requests


@dataclass
class Pet:
    """Represents a pet in the store."""
    id: int
    name: str
    tag: Optional[str] = None


class PetstoreClient:
    """Client for the Petstore API."""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        """Initialize the client."""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("PETSTORE_API_KEY")
        self.session = requests.Session()
        if self.api_key:
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"
    
    def list_pets(self, limit: int = 100) -> List[Pet]:
        """List all pets in the store."""
        response = self.session.get(f"{self.base_url}/pets", params={"limit": limit})
        response.raise_for_status()
        return [Pet(**p) for p in response.json()]
    
    def get_pet(self, pet_id: int) -> Pet:
        """Get a specific pet by ID."""
        response = self.session.get(f"{self.base_url}/pets/{pet_id}")
        response.raise_for_status()
        return Pet(**response.json())
'''
        is_valid, issues = validate_semantic_correctness(
            code, 
            check_imports=True,
            check_docstrings=True
        )
        
        # Should pass with no errors
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0, f"Unexpected errors: {errors}"
        assert is_valid is True
    
    def test_hallucinated_import_caught(self):
        """Code with hallucinated imports should be caught."""
        code = '''
"""Client module."""
import requests
from petstore_sdk import PetstoreAuth  # Hallucinated!
from nonexistent_lib import magic_function  # Also hallucinated

class Client:
    pass
'''
        is_valid, issues = validate_semantic_correctness(code)
        
        assert is_valid is False
        
        # Should catch both hallucinated imports
        import_errors = [i for i in issues if i.issue_type == "invalid_import"]
        assert len(import_errors) == 2
        
        bad_modules = {i.context["module"] for i in import_errors}
        assert "petstore_sdk" in bad_modules
        assert "nonexistent_lib" in bad_modules
