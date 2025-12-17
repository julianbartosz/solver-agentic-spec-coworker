"""Security tests for safe expression evaluation.

Verifies that dangerous expressions are blocked while safe expressions work.

Per docs/BUCKET_2_NO_INTERPRETATION_PLAN.md Step 1.4
"""

import pytest

# Skip all tests if simpleeval is not installed
pytest.importorskip("simpleeval")

from integration_coworker.codegen.safe_eval import (
    safe_eval_cross_field,
    SafeExpressionEvaluator,
    CrossFieldEvaluationError,
)


class TestSafeEvalSecurity:
    """Tests that unsafe expressions are blocked."""
    
    def test_blocks_import(self):
        """Should block __import__ attempts."""
        with pytest.raises(CrossFieldEvaluationError):
            safe_eval_cross_field("__import__('os')", {})
    
    def test_blocks_attribute_traversal(self):
        """Should block object attribute access."""
        with pytest.raises(CrossFieldEvaluationError):
            safe_eval_cross_field(
                "().__class__.__bases__[0].__subclasses__()",
                {}
            )
    
    def test_blocks_open(self):
        """Should block open() file access."""
        with pytest.raises(CrossFieldEvaluationError):
            safe_eval_cross_field("open('/etc/passwd').read()", {})
    
    def test_blocks_exec(self):
        """Should block exec() attempts."""
        with pytest.raises(CrossFieldEvaluationError):
            safe_eval_cross_field("exec('import os')", {})
    
    def test_blocks_eval(self):
        """Should block nested eval() attempts."""
        with pytest.raises(CrossFieldEvaluationError):
            safe_eval_cross_field("eval('1+1')", {})
    
    def test_blocks_compile(self):
        """Should block compile() attempts."""
        with pytest.raises(CrossFieldEvaluationError):
            safe_eval_cross_field("compile('1', '', 'eval')", {})
    
    def test_blocks_getattr(self):
        """Should block getattr() attempts."""
        with pytest.raises(CrossFieldEvaluationError):
            safe_eval_cross_field("getattr((), '__class__')", {})
    
    def test_blocks_arbitrary_attribute_access(self):
        """Should block attribute access even on allowed objects."""
        with pytest.raises(CrossFieldEvaluationError):
            safe_eval_cross_field("'test'.__class__", {})
    
    def test_blocks_dunder_access(self):
        """Should block access to dunder attributes."""
        with pytest.raises(CrossFieldEvaluationError):
            safe_eval_cross_field("[].__class__.__mro__", {})


class TestSafeEvalAllowedOperations:
    """Tests that safe expressions work correctly."""
    
    def test_allows_comparison(self):
        """Should allow simple comparisons."""
        assert safe_eval_cross_field("amount > 100", {"amount": 150}) is True
        assert safe_eval_cross_field("amount > 100", {"amount": 50}) is False
    
    def test_allows_equality(self):
        """Should allow equality comparisons."""
        assert safe_eval_cross_field("status == 'active'", {"status": "active"}) is True
        assert safe_eval_cross_field("status != 'active'", {"status": "pending"}) is True
    
    def test_allows_arithmetic(self):
        """Should allow arithmetic operations."""
        result = safe_eval_cross_field(
            "total == qty * price",
            {"total": 100, "qty": 10, "price": 10}
        )
        assert result is True
        
        result = safe_eval_cross_field(
            "balance + deposit",
            {"balance": 100, "deposit": 50}
        )
        assert result == 150
    
    def test_allows_boolean_logic(self):
        """Should allow boolean and/or/not."""
        assert safe_eval_cross_field(
            "active and amount > 0",
            {"active": True, "amount": 10}
        ) is True
        
        assert safe_eval_cross_field(
            "active or fallback",
            {"active": False, "fallback": True}
        ) is True
        
        assert safe_eval_cross_field(
            "not disabled",
            {"disabled": False}
        ) is True
    
    def test_allows_membership(self):
        """Should allow 'in' operator."""
        assert safe_eval_cross_field(
            "status in ['A', 'B', 'C']",
            {"status": "B"}
        ) is True
        
        assert safe_eval_cross_field(
            "status not in ['X', 'Y', 'Z']",
            {"status": "A"}
        ) is True
    
    def test_allows_safe_functions(self):
        """Should allow whitelisted functions."""
        assert safe_eval_cross_field("len(name) > 0", {"name": "test"}) is True
        assert safe_eval_cross_field("abs(balance)", {"balance": -50}) == 50
        assert safe_eval_cross_field("max(a, b)", {"a": 1, "b": 2}) == 2
        assert safe_eval_cross_field("min(a, b)", {"a": 1, "b": 2}) == 1
        assert safe_eval_cross_field("round(value, 2)", {"value": 3.14159}) == 3.14
    
    def test_allows_string_operations(self):
        """Should allow string comparisons."""
        assert safe_eval_cross_field(
            "prefix == 'USD'",
            {"prefix": "USD"}
        ) is True
    
    def test_allows_none_comparison(self):
        """Should handle None comparisons."""
        assert safe_eval_cross_field(
            "value == None",
            {"value": None}
        ) is True
        
        assert safe_eval_cross_field(
            "value != None",
            {"value": "test"}
        ) is True
    
    def test_allows_nested_arithmetic(self):
        """Should allow nested arithmetic expressions."""
        assert safe_eval_cross_field(
            "(qty * price) + (qty * price * tax_rate)",
            {"qty": 10, "price": 5, "tax_rate": 0.1}
        ) == 55.0


class TestCrossFieldValidationIntegration:
    """Integration tests with realistic validation scenarios."""
    
    def test_date_comparison_as_ordinals(self):
        """Should validate date comparisons using ordinal integers."""
        result = safe_eval_cross_field(
            "end_date > start_date",
            {"start_date": 20231201, "end_date": 20231231}
        )
        assert result is True
    
    def test_sum_validation(self):
        """Should validate that fields sum correctly."""
        result = safe_eval_cross_field(
            "subtotal + tax + shipping == total",
            {"subtotal": 100, "tax": 8, "shipping": 5, "total": 113}
        )
        assert result is True
        
        result = safe_eval_cross_field(
            "subtotal + tax + shipping == total",
            {"subtotal": 100, "tax": 8, "shipping": 5, "total": 999}  # Wrong total
        )
        assert result is False
    
    def test_conditional_required(self):
        """Should validate conditional requirements."""
        # If status is 'approved', amount must be > 0
        result = safe_eval_cross_field(
            "status != 'approved' or amount > 0",
            {"status": "approved", "amount": 100}
        )
        assert result is True
        
        # Should fail if status is approved but amount is 0
        result = safe_eval_cross_field(
            "status != 'approved' or amount > 0",
            {"status": "approved", "amount": 0}
        )
        assert result is False
        
        # Should pass if status is not approved (amount doesn't matter)
        result = safe_eval_cross_field(
            "status != 'approved' or amount > 0",
            {"status": "pending", "amount": 0}
        )
        assert result is True
    
    def test_percentage_validation(self):
        """Should validate percentage calculations."""
        result = safe_eval_cross_field(
            "abs(actual_pct - expected_pct) < 0.01",
            {"actual_pct": 0.335, "expected_pct": 0.33}
        )
        assert result is True
    
    def test_balance_validation(self):
        """Should validate credit/debit balancing."""
        result = safe_eval_cross_field(
            "credits == debits",
            {"credits": 1000, "debits": 1000}
        )
        assert result is True


class TestEdgeCases:
    """Edge case tests for robustness."""
    
    def test_empty_expression(self):
        """Should handle empty or True expression."""
        assert safe_eval_cross_field("True", {}) is True
    
    def test_numeric_strings(self):
        """Should handle numeric operations on actual numbers."""
        # Note: won't auto-convert strings
        assert safe_eval_cross_field(
            "int(value) > 10",
            {"value": "42"}
        ) is True
    
    def test_missing_field_raises(self):
        """Should raise error for missing fields."""
        with pytest.raises(CrossFieldEvaluationError) as excinfo:
            safe_eval_cross_field("missing_field > 0", {})
        assert "Unknown variable" in str(excinfo.value) or "missing_field" in str(excinfo.value)
    
    def test_type_error_in_comparison(self):
        """Should raise error on type mismatches."""
        with pytest.raises(CrossFieldEvaluationError):
            # Can't compare string and int in Python
            safe_eval_cross_field("name > 10", {"name": "test"})
    
    def test_syntax_error_raises(self):
        """Should raise error on syntax errors."""
        with pytest.raises(CrossFieldEvaluationError):
            safe_eval_cross_field("amount > ", {"amount": 100})


class TestEvaluatorInstantiation:
    """Tests for SafeExpressionEvaluator class."""
    
    def test_custom_functions(self):
        """Should support custom whitelisted functions."""
        def double(x):
            return x * 2
        
        evaluator = SafeExpressionEvaluator(extra_functions={"double": double})
        result = evaluator.evaluate("double(value)", {"value": 21})
        assert result == 42
    
    def test_reuse_across_evaluations(self):
        """Should be reusable across multiple evaluations."""
        evaluator = SafeExpressionEvaluator()
        
        assert evaluator.evaluate("a > b", {"a": 5, "b": 3}) is True
        assert evaluator.evaluate("x + y", {"x": 10, "y": 20}) == 30
        assert evaluator.evaluate("status in allowed", {"status": "A", "allowed": ["A", "B"]}) is True
