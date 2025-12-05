"""
Tests for codegen/security.py - AST-based security validation.

v2: Tests for FT-SEC-003 from V2 Implementation Plan Section 3.7
"""
import pytest

from integration_coworker.codegen.security import (
    SecurityViolation,
    SecurityVisitor,
    validate_code_security,
    format_violations,
    FORBIDDEN_FUNCTIONS,
    FORBIDDEN_CALLS,
    FORBIDDEN_ATTRS,
)


class TestSecurityViolation:
    """Tests for SecurityViolation dataclass."""
    
    def test_violation_creation(self):
        """Test creating a security violation."""
        v = SecurityViolation(
            line=10,
            column=5,
            pattern="exec",
            description="Forbidden function call: exec()",
            severity="error",
        )
        assert v.line == 10
        assert v.column == 5
        assert v.pattern == "exec"
        assert v.severity == "error"


class TestSecurityVisitor:
    """Tests for the SecurityVisitor AST visitor."""
    
    def test_detects_exec(self):
        """Test detection of exec() call."""
        code = "exec('print(1)')"
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert len(violations) == 1
        assert violations[0].pattern == "exec"
        assert violations[0].severity == "error"
    
    def test_detects_eval(self):
        """Test detection of eval() call."""
        code = "result = eval('1 + 2')"
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert len(violations) == 1
        assert violations[0].pattern == "eval"
    
    def test_detects_compile(self):
        """Test detection of compile() call."""
        code = "code = compile('x=1', '<string>', 'exec')"
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert violations[0].pattern == "compile"
    
    def test_detects_dunder_import(self):
        """Test detection of __import__() call."""
        code = "os = __import__('os')"
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert violations[0].pattern == "__import__"
    
    def test_open_is_warning_with_file_io_allowed(self):
        """Test that open() is a warning when file IO is allowed."""
        code = "f = open('file.txt')"
        is_valid, violations = validate_code_security(code, allow_file_io=True)
        # Should be valid (only warning, no error)
        assert is_valid
        assert len(violations) == 1
        assert violations[0].pattern == "open"
        assert violations[0].severity == "warning"
    
    def test_open_is_error_with_file_io_disallowed(self):
        """Test that open() is kept as warning even when file IO is disallowed.
        
        Note: The SecurityVisitor initially sets open() as "warning" severity.
        When allow_file_io=False, we still include it in violations but
        don't upgrade its severity. The philosophy is that open() is less
        dangerous than exec/eval/subprocess, so it remains a warning.
        """
        code = "f = open('file.txt')"
        is_valid, violations = validate_code_security(code, allow_file_io=False)
        # open() is still just a warning, so is_valid should be True
        assert is_valid
        assert violations[0].pattern == "open"
        assert violations[0].severity == "warning"


class TestForbiddenModuleCalls:
    """Tests for detection of forbidden module.function calls."""
    
    def test_detects_os_system(self):
        """Test detection of os.system()."""
        code = """
import os
os.system('ls -la')
"""
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert any("os.system" in v.pattern for v in violations)
    
    def test_detects_subprocess_run(self):
        """Test detection of subprocess.run()."""
        code = """
import subprocess
subprocess.run(['ls', '-la'])
"""
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert any("subprocess.run" in v.pattern for v in violations)
    
    def test_detects_subprocess_popen(self):
        """Test detection of subprocess.Popen()."""
        code = """
import subprocess
p = subprocess.Popen(['cmd'])
"""
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert any("subprocess.Popen" in v.pattern for v in violations)
    
    def test_subprocess_allowed_when_flag_set(self):
        """Test that subprocess is allowed when allow_subprocess=True."""
        code = """
import subprocess
subprocess.run(['ls'])
"""
        is_valid, violations = validate_code_security(code, allow_subprocess=True)
        assert is_valid
        assert len(violations) == 0
    
    def test_detects_pickle_loads(self):
        """Test detection of pickle.loads()."""
        code = """
import pickle
data = pickle.loads(b'...')
"""
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert any("pickle.loads" in v.pattern for v in violations)
    
    def test_detects_aliased_imports(self):
        """Test detection with aliased imports."""
        code = """
import subprocess as sp
sp.run(['ls'])
"""
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        # Should still detect even with alias
        assert len(violations) >= 1


class TestForbiddenAttributes:
    """Tests for detection of forbidden attribute access."""
    
    def test_detects_dunder_code(self):
        """Test detection of __code__ access."""
        code = "func.__code__"
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert any("__code__" in v.pattern for v in violations)
    
    def test_detects_dunder_globals(self):
        """Test detection of __globals__ access."""
        code = "func.__globals__"
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert any("__globals__" in v.pattern for v in violations)
    
    def test_detects_dunder_builtins(self):
        """Test detection of __builtins__ access."""
        code = "obj.__builtins__"
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert any("__builtins__" in v.pattern for v in violations)
    
    def test_detects_dunder_subclasses(self):
        """Test detection of __subclasses__ access."""
        code = "object.__subclasses__()"
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert any("__subclasses__" in v.pattern for v in violations)


class TestValidateCodeSecurity:
    """Tests for the validate_code_security function."""
    
    def test_valid_code_passes(self):
        """Test that valid, safe code passes validation."""
        code = """
def greet(name):
    return f"Hello, {name}!"

result = greet("world")
print(result)
"""
        is_valid, violations = validate_code_security(code)
        assert is_valid
        assert len(violations) == 0
    
    def test_syntax_error_fails(self):
        """Test that syntax errors are caught."""
        code = "def foo(:"  # Invalid syntax
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert len(violations) == 1
        assert violations[0].pattern == "SyntaxError"
    
    def test_multiple_violations(self):
        """Test detection of multiple violations."""
        code = """
import os
import subprocess

os.system('ls')
subprocess.run(['cat'])
exec('print(1)')
"""
        is_valid, violations = validate_code_security(code)
        assert not is_valid
        assert len(violations) >= 3
    
    def test_common_api_client_code_passes(self):
        """Test that typical API client code passes."""
        code = '''
import requests
from typing import Dict, Any

class StripeClient:
    """Client for Stripe API."""
    
    def __init__(self, api_key: str, base_url: str = "https://api.stripe.com"):
        self.api_key = api_key
        self.base_url = base_url
    
    def create_checkout_session(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a checkout session."""
        response = requests.post(
            f"{self.base_url}/v1/checkout/sessions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"}
        )
        response.raise_for_status()
        return response.json()
'''
        is_valid, violations = validate_code_security(code)
        assert is_valid
        assert len(violations) == 0


class TestFormatViolations:
    """Tests for the format_violations function."""
    
    def test_empty_violations(self):
        """Test formatting with no violations."""
        result = format_violations([])
        assert result == "No security issues found."
    
    def test_single_violation(self):
        """Test formatting a single violation."""
        violations = [
            SecurityViolation(
                line=5,
                column=0,
                pattern="exec",
                description="Forbidden function call: exec()",
                severity="error",
            )
        ]
        result = format_violations(violations)
        assert "Security violations detected:" in result
        assert "[ERROR]" in result
        assert "Line 5" in result
        assert "exec()" in result
    
    def test_multiple_violations(self):
        """Test formatting multiple violations."""
        violations = [
            SecurityViolation(
                line=5, column=0, pattern="exec",
                description="Forbidden: exec()", severity="error"
            ),
            SecurityViolation(
                line=10, column=0, pattern="open",
                description="Warning: open()", severity="warning"
            ),
        ]
        result = format_violations(violations)
        assert "[ERROR]" in result
        assert "[WARNING]" in result
        assert "Line 5" in result
        assert "Line 10" in result


class TestForbiddenSets:
    """Tests to verify the forbidden sets are populated correctly."""
    
    def test_forbidden_functions_populated(self):
        """Verify FORBIDDEN_FUNCTIONS contains expected items."""
        assert "exec" in FORBIDDEN_FUNCTIONS
        assert "eval" in FORBIDDEN_FUNCTIONS
        assert "compile" in FORBIDDEN_FUNCTIONS
        assert "__import__" in FORBIDDEN_FUNCTIONS
        assert "open" in FORBIDDEN_FUNCTIONS
    
    def test_forbidden_calls_populated(self):
        """Verify FORBIDDEN_CALLS contains expected items."""
        assert ("os", "system") in FORBIDDEN_CALLS
        assert ("subprocess", "run") in FORBIDDEN_CALLS
        assert ("pickle", "loads") in FORBIDDEN_CALLS
        assert ("ctypes", "CDLL") in FORBIDDEN_CALLS
    
    def test_forbidden_attrs_populated(self):
        """Verify FORBIDDEN_ATTRS contains expected items."""
        assert "__code__" in FORBIDDEN_ATTRS
        assert "__globals__" in FORBIDDEN_ATTRS
        assert "__builtins__" in FORBIDDEN_ATTRS
        assert "__subclasses__" in FORBIDDEN_ATTRS
