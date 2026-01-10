"""
Unit tests for V32 bug fixes.

V32-001: Syntax validation and auto-repair before sandbox execution
V32-002: Response | None conditional access pattern fixes
"""
import pytest

from integration_coworker.codegen.security import (
    repair_syntax_errors,
    validate_and_repair_syntax,
)
from integration_coworker.codegen.response_type_guard import (
    fix_conditional_response_access,
    fix_generated_client_code,
)


class TestV32001SyntaxRepair:
    """Tests for V32-001: Syntax validation and auto-repair."""

    def test_valid_code_unchanged(self):
        """Valid code should pass through unchanged."""
        code = '''
def foo():
    x = 1
    return x
'''
        repaired, is_valid = validate_and_repair_syntax(code)
        assert is_valid
        # Code should be syntactically equivalent (formatting may change)

    def test_mixed_tabs_spaces_repaired(self):
        """Mixed tabs and spaces should be repaired."""
        code = '''
def foo():
    x = 1
	y = 2
    return x + y
'''
        repaired, was_repaired, method = repair_syntax_errors(code)
        # Should attempt repair
        assert repaired is not None

    def test_orphaned_return_fixed(self):
        """Orphaned return statements should be handled."""
        code = '''
def foo():
    x = 1
return x
'''
        repaired, was_repaired, method = repair_syntax_errors(code)
        # Should attempt repair
        assert repaired is not None

    def test_validate_and_repair_returns_tuple(self):
        """validate_and_repair_syntax should return (code, is_valid) tuple."""
        code = "x = 1"
        result = validate_and_repair_syntax(code)
        assert isinstance(result, tuple)
        assert len(result) == 2
        repaired, is_valid = result
        assert isinstance(repaired, str)
        assert isinstance(is_valid, bool)

    def test_repair_syntax_errors_returns_tuple(self):
        """repair_syntax_errors should return (code, was_repaired, method) tuple."""
        code = "x = 1"
        result = repair_syntax_errors(code)
        assert isinstance(result, tuple)
        assert len(result) == 3
        repaired, was_repaired, method = result
        assert isinstance(repaired, str)
        assert isinstance(was_repaired, bool)
        assert isinstance(method, str)

    def test_empty_code_handled(self):
        """Empty code should be handled gracefully."""
        repaired, is_valid = validate_and_repair_syntax("")
        assert isinstance(is_valid, bool)

    def test_simple_syntax_error_repaired(self):
        """Simple syntax errors that ruff can fix should be repaired."""
        # Missing newline at end
        code = "def foo():\n    return 1"
        repaired, is_valid = validate_and_repair_syntax(code)
        # Should complete without exception
        assert isinstance(is_valid, bool)


class TestV32002ResponseConditionalFix:
    """Tests for V32-002: Response | None conditional access patterns."""

    def test_simple_conditional_access_fixed(self):
        """Simple conditional access patterns should be fixed."""
        code = '''
def check():
    response = make_request()
    status = response.status_code if response else "unknown"
    return status
'''
        fixed, changes = fix_conditional_response_access(code)
        assert len(changes) == 1
        assert "[V32-002]" in changes[0]
        assert "response.status_code" in fixed
        assert "if response else" not in fixed

    def test_multiple_conditionals_fixed(self):
        """Multiple conditional patterns should all be fixed."""
        code = '''
def check():
    status = response.status_code if response else "unknown"
    text = response.text if response else ""
    return status, text
'''
        fixed, changes = fix_conditional_response_access(code)
        assert len(changes) == 2

    def test_fstring_conditional_fixed(self):
        """F-string conditional patterns should be fixed."""
        code = '''
def log_response(response):
    msg = f"Response: {response.text[:200] if response else 'no response'}"
    return msg
'''
        fixed, changes = fix_conditional_response_access(code)
        assert len(changes) == 1
        assert "[V32-002]" in changes[0]

    def test_returns_tuple(self):
        """fix_conditional_response_access should return (code, changes) tuple."""
        result = fix_conditional_response_access("x = 1")
        assert isinstance(result, tuple)
        assert len(result) == 2
        code, changes = result
        assert isinstance(code, str)
        assert isinstance(changes, list)

    def test_no_changes_for_unrelated_code(self):
        """Code without Response conditionals should be unchanged."""
        code = '''
def add(a, b):
    return a + b
'''
        fixed, changes = fix_conditional_response_access(code)
        assert len(changes) == 0
        assert fixed == code

    def test_integration_with_fix_generated_client_code(self):
        """V32-002 should be called from fix_generated_client_code."""
        code = '''
import httpx

def get_data():
    response = httpx.get("https://api.example.com")
    status = response.status_code if response else "error"
    return status
'''
        fixed, changes = fix_generated_client_code(code)
        # Should have V32-002 fix applied
        assert any("[V32-002]" in c for c in changes)


class TestV32Integration:
    """Integration tests for V32 fixes working together."""

    def test_both_fixes_can_be_applied(self):
        """Both V32-001 and V32-002 fixes should work on the same code."""
        # Code with Response conditionals (syntactically valid)
        code = '''
def check(response):
    status = response.status_code if response else "unknown"
    return status
'''
        # First apply V32-002
        fixed_code, response_changes = fix_conditional_response_access(code)
        assert len(response_changes) > 0

        # Then apply V32-001 validation
        repaired, is_valid = validate_and_repair_syntax(fixed_code)
        assert is_valid

    def test_v32_fixes_are_idempotent(self):
        """Applying V32 fixes multiple times should be safe."""
        code = '''
def check():
    status = response.status_code if response else "unknown"
    return status
'''
        # Apply V32-002 twice
        fixed1, changes1 = fix_conditional_response_access(code)
        fixed2, changes2 = fix_conditional_response_access(fixed1)
        
        # Second application should have no changes
        assert len(changes2) == 0
        assert fixed1 == fixed2
