"""
Tests for code_test_validator module (V25-001/V25-002 Fix)

Tests the cross-validation system that ensures generated tests match
actual flow code behavior.
"""
import pytest
from integration_coworker.codegen.code_test_validator import (
    CodeBehaviorExtractor,
    TestAssertionAnalyzer,
    ValidationBehavior,
    ParameterValidation,
    cross_validate_code_and_tests,
    extract_code_behaviors,
    extract_test_assertions,
    validate_and_fix_tests,
    build_repair_prompt_with_context,
)


class TestCodeBehaviorExtractor:
    """Tests for extracting validation behavior from code."""
    
    def test_extracts_raises_on_none_pattern(self):
        """Test detection of 'if param is None: raise ValueError'."""
        code = '''
def my_flow(api_key: str, payload: dict):
    if api_key is None:
        raise ValueError("api_key is required")
    if payload is None:
        raise ValueError("payload is required")
    return {"success": True}
'''
        behaviors = extract_code_behaviors(code)
        
        assert len(behaviors) == 1
        flow = behaviors[0]
        assert flow.function_name == "my_flow"
        
        # Check we found both validations
        validations = {pv.param_name: pv for pv in flow.parameter_validations}
        
        assert "api_key" in validations
        assert validations["api_key"].behavior == ValidationBehavior.RAISES_ON_NONE
        assert "api_key is required" in validations["api_key"].error_message
        
        assert "payload" in validations
        assert validations["payload"].behavior == ValidationBehavior.RAISES_ON_NONE
    
    def test_extracts_converts_none_to_default_pattern(self):
        """Test detection of 'if param is None: param = {}'."""
        code = '''
def my_flow(api_key: str, payload: dict):
    if api_key is None:
        raise ValueError("api_key is required")
    if payload is None:
        payload = {}  # Convert to empty dict
    return {"success": True, "data": payload}
'''
        behaviors = extract_code_behaviors(code)
        
        assert len(behaviors) == 1
        flow = behaviors[0]
        
        validations = {pv.param_name: pv for pv in flow.parameter_validations}
        
        # api_key should raise
        assert validations["api_key"].behavior == ValidationBehavior.RAISES_ON_NONE
        
        # payload should convert to default
        assert validations["payload"].behavior == ValidationBehavior.CONVERTS_NONE_TO_DEFAULT
        assert validations["payload"].default_value == "{}"
    
    def test_extracts_empty_string_check(self):
        """Test detection of 'if not param: raise ValueError'."""
        code = '''
def my_flow(api_key: str):
    if not api_key:
        raise ValueError("api_key is required")
    return {"success": True}
'''
        behaviors = extract_code_behaviors(code)
        
        assert len(behaviors) == 1
        flow = behaviors[0]
        
        validations = {pv.param_name: pv for pv in flow.parameter_validations}
        assert "api_key" in validations
        assert validations["api_key"].behavior == ValidationBehavior.RAISES_ON_EMPTY
    
    def test_detects_client_method_calls(self):
        """Test detection of client.method() calls."""
        code = '''
def list_products_flow(api_key: str, payload: dict):
    client = StripeClient(api_key=api_key)
    result = client.list_products(payload=payload)
    return {"success": True, "data": result}
'''
        behaviors = extract_code_behaviors(code)
        
        assert len(behaviors) == 1
        flow = behaviors[0]
        assert flow.calls_external_client is True
        assert flow.client_method_called == "list_products"
    
    def test_handles_optional_type_hint(self):
        """Test detection of Optional type hints."""
        code = '''
from typing import Optional, Dict, Any

def my_flow(
    api_key: str,
    payload: Optional[Dict[str, Any]] = None,
):
    return {"success": True}
'''
        behaviors = extract_code_behaviors(code)
        
        assert len(behaviors) == 1
        flow = behaviors[0]
        
        # Should detect Optional parameter
        validations = {pv.param_name: pv for pv in flow.parameter_validations}
        assert "payload" in validations
        assert validations["payload"].behavior in (
            ValidationBehavior.OPTIONAL_PARAM,
            ValidationBehavior.ACCEPTS_NONE,
        )
    
    def test_handles_syntax_error(self):
        """Test graceful handling of code with syntax errors."""
        code = "def broken( x:"  # Syntax error
        
        behaviors = extract_code_behaviors(code)
        assert behaviors == []


class TestTestAssertionAnalyzer:
    """Tests for extracting assertions from test code."""
    
    def test_extracts_pytest_raises(self):
        """Test extraction of pytest.raises assertions."""
        test_code = '''
import pytest
from integrations.flows.my_flow import my_flow

class TestMyFlow:
    def test_flow_missing_api_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            my_flow(api_key=None, payload={})
    
    def test_flow_missing_payload(self):
        with pytest.raises(ValueError, match="payload is required"):
            my_flow(api_key="test", payload=None)
'''
        assertions = extract_test_assertions(test_code)
        
        assert len(assertions) >= 2
        
        # Find the api_key test
        api_key_test = next(
            (a for a in assertions if a.param_tested == "api_key"),
            None
        )
        assert api_key_test is not None
        assert api_key_test.assertion_type == "raises"
        assert api_key_test.expected_exception == "ValueError"
        assert api_key_test.param_value == "None"
        
        # Find the payload test
        payload_test = next(
            (a for a in assertions if a.param_tested == "payload"),
            None
        )
        assert payload_test is not None
        assert payload_test.assertion_type == "raises"
    
    def test_handles_syntax_error(self):
        """Test graceful handling of test code with syntax errors."""
        code = "def broken( x:"
        
        assertions = extract_test_assertions(code)
        assert assertions == []


class TestCrossValidation:
    """Tests for cross-validating code and tests."""
    
    def test_valid_code_and_tests(self):
        """Test that matching code and tests pass validation."""
        flow_code = '''
def my_flow(api_key: str, payload: dict):
    if api_key is None:
        raise ValueError("api_key is required")
    if payload is None:
        raise ValueError("payload is required")
    return {"success": True}
'''
        test_code = '''
import pytest
from integrations.flows.my_flow import my_flow

class TestMyFlow:
    def test_flow_missing_api_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            my_flow(api_key=None, payload={})
    
    def test_flow_missing_payload(self):
        with pytest.raises(ValueError, match="payload is required"):
            my_flow(api_key="test", payload=None)
'''
        result = cross_validate_code_and_tests(
            flow_code=flow_code,
            test_code=test_code,
            flow_function_name="my_flow",
        )
        
        assert result.is_valid is True
        assert result.error_count == 0
    
    def test_detects_mismatch_converts_vs_raises(self):
        """Test detection of mismatch: code converts None but test expects raise."""
        flow_code = '''
def my_flow(api_key: str, payload: dict):
    if api_key is None:
        raise ValueError("api_key is required")
    if payload is None:
        payload = {}  # CODE CONVERTS TO DEFAULT, DOESN'T RAISE!
    return {"success": True}
'''
        test_code = '''
import pytest
from integrations.flows.my_flow import my_flow

class TestMyFlow:
    def test_flow_missing_payload(self):
        # TEST EXPECTS RAISE, BUT CODE DOESN'T RAISE!
        with pytest.raises(ValueError, match="payload is required"):
            my_flow(api_key="test", payload=None)
'''
        result = cross_validate_code_and_tests(
            flow_code=flow_code,
            test_code=test_code,
            flow_function_name="my_flow",
        )
        
        assert result.is_valid is False
        assert result.error_count == 1
        
        mismatch = result.mismatches[0]
        assert mismatch.severity == "error"
        assert "payload" in mismatch.description.lower()
        assert "converts none" in mismatch.description.lower() or "does not" in mismatch.description.lower()


class TestValidateAndFixTests:
    """Tests for the main fix function."""
    
    def test_fixes_mismatched_test(self):
        """Test that validate_and_fix_tests modifies mismatched tests."""
        flow_code = '''
def list_products_flow(api_key: str, payload: dict):
    if not api_key:
        raise ValueError("api_key is required")
    if payload is None:
        payload = {}  # Silently convert, don't raise
    return {"success": True}
'''
        test_code = '''
import pytest
from integrations.flows.my_flow import list_products_flow

class TestListProductsFlow:
    def test_flow_missing_payload(self):
        with pytest.raises(ValueError, match="payload is required"):
            list_products_flow(api_key="test", payload=None)
'''
        fixed_code, was_modified, changes = validate_and_fix_tests(
            flow_code=flow_code,
            test_code=test_code,
            flow_function_name="list_products_flow",
        )
        
        # Should detect the mismatch
        assert was_modified is True or len(changes) > 0 or fixed_code != test_code
    
    def test_no_changes_for_valid_tests(self):
        """Test that valid tests are not modified."""
        flow_code = '''
def my_flow(api_key: str, payload: dict):
    if api_key is None:
        raise ValueError("api_key is required")
    return {"success": True}
'''
        test_code = '''
import pytest

class TestMyFlow:
    def test_flow_success(self):
        result = my_flow(api_key="test", payload={})
        assert result["success"] is True
'''
        fixed_code, was_modified, changes = validate_and_fix_tests(
            flow_code=flow_code,
            test_code=test_code,
            flow_function_name="my_flow",
        )
        
        # Should not modify valid test
        assert was_modified is False
        assert changes == []


class TestBuildRepairPromptWithContext:
    """Tests for the context-aware repair prompt builder."""
    
    def test_includes_flow_code_in_prompt(self):
        """Test that prompt includes flow code."""
        flow_code = '''
def my_flow(api_key: str, payload: dict):
    if payload is None:
        payload = {}
    return {"success": True}
'''
        test_code = "def test_example(): pass"
        errors = [{"error_type": "ValueError", "error_detail": "test", "test_name": "test_x"}]
        
        prompt = build_repair_prompt_with_context(
            test_code=test_code,
            flow_code=flow_code,
            pytest_errors=errors,
        )
        
        assert "my_flow" in prompt
        assert "payload is None" in prompt
        assert "payload = {}" in prompt
    
    def test_includes_behavior_summary(self):
        """Test that prompt includes behavior summary."""
        flow_code = '''
def my_flow(api_key: str, payload: dict):
    if payload is None:
        payload = {}  # Convert to empty dict
    return {"success": True}
'''
        test_code = "def test_example(): pass"
        errors = [{"error_type": "ValueError", "error_detail": "test", "test_name": "test_x"}]
        
        prompt = build_repair_prompt_with_context(
            test_code=test_code,
            flow_code=flow_code,
            pytest_errors=errors,
        )
        
        # Should mention that payload converts None to default
        assert "CONVERTS" in prompt or "default" in prompt.lower()


class TestRealWorldScenario:
    """Tests based on the actual V25-001 bug scenario."""
    
    def test_stripe_api_cache_test_scenario(self):
        """
        Test the exact scenario from V25-001:
        - Flow converts payload=None to {}
        - Test expects ValueError for payload=None
        """
        # This is similar to the actual generated flow code
        flow_code = '''
def list_available_products_flow(
    api_key: str,
    payload: dict,
    **kwargs,
) -> dict:
    """Execute the list available products workflow."""
    # Step 1: Validate input
    if not api_key:
        raise ValueError("api_key is required")
    if payload is None:
        payload = {}  # V25-001 BUG: This converts instead of raising!
    
    # Step 2: Call the API
    client = StripeApiCacheTestClient(api_key=api_key)
    response = client.list_products(payload=payload)
    
    return {"success": True, "data": response}
'''
        
        # This is similar to the actual generated test code
        test_code = '''
import pytest
from unittest.mock import patch
from integrations.flows.stripe_api_cache_test_list_available_products import (
    list_available_products_flow
)

class TestStripeApiCacheTestFlow:
    def test_list_available_products_flow_missing_payload(self):
        """Test flow raises error when payload is missing."""
        with patch(
            'integrations.flows.stripe_api_cache_test_list_available_products.'
            'StripeApiCacheTestClient'
        ) as MockClient:
            # V25-001 BUG: Test expects ValueError but code doesn't raise!
            with pytest.raises(ValueError, match="payload is required"):
                list_available_products_flow(
                    api_key="test_api_key",
                    payload=None,
                )
'''
        
        # Cross-validate should detect the mismatch
        result = cross_validate_code_and_tests(
            flow_code=flow_code,
            test_code=test_code,
            flow_function_name="list_available_products_flow",
        )
        
        assert result.is_valid is False
        assert result.error_count >= 1
        
        # Should specifically mention the payload mismatch
        mismatch = result.mismatches[0]
        assert "payload" in mismatch.description.lower()
        assert mismatch.test_assertion.param_tested == "payload"
        assert mismatch.code_behavior.behavior == ValidationBehavior.CONVERTS_NONE_TO_DEFAULT


class TestEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_empty_flow_code(self):
        """Test handling of empty flow code."""
        result = cross_validate_code_and_tests(
            flow_code="",
            test_code="def test_x(): pass",
        )
        # Should not crash, just return valid (nothing to validate)
        assert result.is_valid is True
    
    def test_empty_test_code(self):
        """Test handling of empty test code."""
        result = cross_validate_code_and_tests(
            flow_code="def my_flow(): pass",
            test_code="",
        )
        assert result.is_valid is True
    
    def test_no_flow_function_found(self):
        """Test when no flow function is found in code."""
        code = '''
class SomeClass:
    def some_method(self):
        pass
'''
        result = cross_validate_code_and_tests(
            flow_code=code,
            test_code="def test_x(): pass",
        )
        # Should not crash, returns valid (nothing to validate)
        assert result.is_valid is True
    
    def test_comparison_with_equality_operator(self):
        """Test detection of `if param == None` pattern."""
        code = '''
def my_flow(api_key: str, payload: dict):
    if payload == None:  # Using == instead of is
        raise ValueError("payload is required")
    return {"success": True}
'''
        behaviors = extract_code_behaviors(code)
        
        assert len(behaviors) == 1
        validations = {pv.param_name: pv for pv in behaviors[0].parameter_validations}
        assert "payload" in validations
        assert validations["payload"].behavior == ValidationBehavior.RAISES_ON_NONE
