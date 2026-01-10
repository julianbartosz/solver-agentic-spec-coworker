"""
Tests for V44 fixes: Pre-validation sanitization for LLM-generated code.

V44-001: Fix concatenated statements
V44-002: Line-based import sanitizer
"""
import pytest


class TestV44ConcatenatedStatementFix:
    """Tests for _fix_concatenated_statements (V44-001)."""
    
    def test_concatenated_from_imports(self):
        """Test splitting concatenated 'from X import' statements."""
        from integration_coworker.codegen.security import _fix_concatenated_statements
        
        code = "from integration_framework.core.client import X from integration_framework.core.exceptions import Y"
        result = _fix_concatenated_statements(code)
        
        lines = result.split('\n')
        assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}"
        assert "from integration_framework.core.client import X" in lines[0]
        assert "from integration_framework.core.exceptions import Y" in lines[1]
    
    def test_concatenated_import_statements(self):
        """Test splitting concatenated 'import X' statements."""
        from integration_coworker.codegen.security import _fix_concatenated_statements
        
        code = "import os import sys"
        result = _fix_concatenated_statements(code)
        
        lines = result.split('\n')
        assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}"
        assert "import os" in lines[0]
        assert "import sys" in lines[1]
    
    def test_class_with_method_on_same_line(self):
        """Test splitting class definition with method on same line."""
        from integration_coworker.codegen.security import _fix_concatenated_statements
        
        code = "class MyClass: def __init__(self):"
        result = _fix_concatenated_statements(code)
        
        lines = result.split('\n')
        assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}"
        assert "class MyClass:" in lines[0]
        assert "def __init__(self):" in lines[1]
    
    def test_normal_import_unchanged(self):
        """Test that normal single-line import is unchanged."""
        from integration_coworker.codegen.security import _fix_concatenated_statements
        
        code = "from typing import List, Dict, Optional"
        result = _fix_concatenated_statements(code)
        
        assert result == code, "Single import should be unchanged"


class TestV44ImportSanitizer:
    """Tests for sanitize_hallucinated_imports_line_based (V44-002)."""
    
    def test_strips_integration_framework_imports(self):
        """Test stripping integration_framework imports."""
        from integration_coworker.codegen.import_fixer import sanitize_hallucinated_imports_line_based
        
        code = """import csv
from integration_framework.core.client import IntegrationHttpClient
from integration_framework.core.exceptions import IntegrationError

class MyClient:
    pass
"""
        result, fixes = sanitize_hallucinated_imports_line_based(code, strip_instead_of_redirect=True)
        
        assert "integration_framework" not in result
        assert len(fixes) == 2, f"Expected 2 fixes, got {len(fixes)}"
        assert "import csv" in result
        assert "class MyClient:" in result
    
    def test_strips_integration_coworker_runtime_imports(self):
        """Test stripping integration_coworker.runtime imports."""
        from integration_coworker.codegen.import_fixer import sanitize_hallucinated_imports_line_based
        
        code = """from integration_coworker.runtime.client import SomeClient

class MyClient:
    pass
"""
        result, fixes = sanitize_hallucinated_imports_line_based(code, strip_instead_of_redirect=True)
        
        assert "integration_coworker.runtime" not in result
        assert len(fixes) == 1, f"Expected 1 fix, got {len(fixes)}"
    
    def test_preserves_valid_imports(self):
        """Test that valid imports are preserved."""
        from integration_coworker.codegen.import_fixer import sanitize_hallucinated_imports_line_based
        
        code = """import csv
from typing import List, Dict
from dataclasses import dataclass

class MyClass:
    pass
"""
        result, fixes = sanitize_hallucinated_imports_line_based(code, strip_instead_of_redirect=True)
        
        assert result.strip() == code.strip()
        assert len(fixes) == 0, f"Expected 0 fixes, got {len(fixes)}"


class TestPreAstSanitize:
    """Tests for pre_ast_sanitize_code (combined V44 fixes)."""
    
    def test_full_sanitization_flow(self):
        """Test the complete pre-AST sanitization flow."""
        from integration_coworker.codegen.import_fixer import pre_ast_sanitize_code
        import ast
        
        # Broken code with both issues: concatenated imports AND hallucinated packages
        broken_code = '''"""Module docstring."""
import csv
from typing import List, Dict

from integration_framework.core.client import IntegrationHttpClient from integration_framework.core.exceptions import IntegrationError


class CsvImportClient:
    def __init__(self):
        pass
'''
        
        fixed, fixes = pre_ast_sanitize_code(broken_code)
        
        # Should have stripped hallucinated imports and split concatenated statements
        assert "integration_framework" not in fixed
        assert len(fixes) > 0, "Should have applied fixes"
        
        # Code should now be parseable
        try:
            ast.parse(fixed)
        except SyntaxError as e:
            pytest.fail(f"Fixed code should be parseable, but got: {e}")
    
    def test_works_on_already_valid_code(self):
        """Test that valid code passes through unchanged."""
        from integration_coworker.codegen.import_fixer import pre_ast_sanitize_code
        
        valid_code = '''"""Module docstring."""
import csv
from typing import List, Dict

class MyClient:
    def __init__(self):
        pass
'''
        
        fixed, fixes = pre_ast_sanitize_code(valid_code)
        
        assert fixed.strip() == valid_code.strip()
        assert len(fixes) == 0, "No fixes should be needed for valid code"
    
    def test_ordering_concatenated_then_sanitize(self):
        """
        Test that concatenated statements are split BEFORE import sanitization.
        
        This is critical - if we sanitize first, the whole concatenated line gets
        stripped as one unit. If we split first, each import line can be individually
        processed.
        
        V44-004: Ordering fix - concatenated statements MUST be split before sanitization.
        """
        from integration_coworker.codegen.import_fixer import pre_ast_sanitize_code
        import ast
        
        # Concatenated hallucinated imports on one line
        # Without proper ordering, this would strip the entire line and lose info
        broken_code = 'from integration_framework.core.client import IntegrationHttpClient from integration_framework.core.exceptions import IntegrationError'
        
        fixed, fixes = pre_ast_sanitize_code(broken_code)
        
        # Should have 3 fixes: 1 for concatenated split, 2 for import sanitization
        assert len(fixes) == 3, f"Expected 3 fixes (1 concat split + 2 import strips), got {len(fixes)}"
        
        # First fix should be the concatenated statement split (V44-001)
        assert "V44-001" in fixes[0].reason, f"First fix should be V44-001, got: {fixes[0].reason}"
        
        # Remaining fixes should be import sanitization (V44-002)
        assert "V44-002" in fixes[1].reason, f"Second fix should be V44-002, got: {fixes[1].reason}"
        assert "V44-002" in fixes[2].reason, f"Third fix should be V44-002, got: {fixes[2].reason}"
        
        # Code should now be empty or just whitespace (all imports stripped)
        assert "integration_framework" not in fixed
        
        # If there were valid imports mixed in, they should be preserved
        mixed_code = '''import csv
from integration_framework.core.client import IntegrationHttpClient from integration_framework.core.exceptions import IntegrationError
from typing import List
'''
        fixed_mixed, fixes_mixed = pre_ast_sanitize_code(mixed_code)
        
        assert "import csv" in fixed_mixed
        assert "from typing import List" in fixed_mixed
        assert "integration_framework" not in fixed_mixed
        
        # Should be parseable
        try:
            ast.parse(fixed_mixed)
        except SyntaxError as e:
            pytest.fail(f"Fixed mixed code should be parseable, but got: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
