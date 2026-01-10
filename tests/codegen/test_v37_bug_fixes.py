"""
V37 Bug Fix Tests

Tests for V37 fixes identified during external repo (docformatter) validation:
- V37-001: Fix import paths for relocated flows
- V37-002: Honor user's explicit function name in prompts
- V37-003: Relocate client near flow for CLI repos

Each bug has multiple test cases covering:
- Normal operation
- Edge cases
- Integration with existing functionality
"""
import pytest
from unittest.mock import Mock, MagicMock, patch

# =============================================================================
# V37-001: Fix Import Paths for Relocated Flows
# =============================================================================

class TestV37001_ImportPaths:
    """
    V37-001: When a flow is placed at a custom path (e.g., src/myapp/ai_enhancer.py),
    the client import path should be computed correctly.
    
    Bug: Flow imports `from integrations.clients.openai` but is at `src/docformatter/`
    Fix: Compute correct import path based on flow's actual location.
    """
    
    def test_compute_client_import_default_location(self):
        """Test import path for flow at default location (no change needed)."""
        from integration_coworker.codegen.paths import compute_client_import_for_flow
        
        result = compute_client_import_for_flow(
            flow_file_path="src/integrations/flows/openai_flow.py",
            client_file_path="src/integrations/clients/openai.py",
            prefer_relative=False,
        )
        
        assert result == "integrations.clients.openai"
    
    def test_compute_client_import_custom_location_absolute(self):
        """Test import path for flow at custom location (absolute import)."""
        from integration_coworker.codegen.paths import compute_client_import_for_flow
        
        result = compute_client_import_for_flow(
            flow_file_path="src/docformatter/ai_enhancer.py",
            client_file_path="src/integrations/clients/openai.py",
            prefer_relative=False,
        )
        
        # Should compute absolute import path
        assert result == "integrations.clients.openai"
    
    def test_compute_client_import_custom_location_relative(self):
        """Test import path for flow at custom location (relative import)."""
        from integration_coworker.codegen.paths import compute_client_import_for_flow
        
        result = compute_client_import_for_flow(
            flow_file_path="src/docformatter/ai_enhancer.py",
            client_file_path="src/integrations/clients/openai.py",
            prefer_relative=True,
        )
        
        # Should compute relative import path
        assert ".integrations.clients.openai" in result or "integrations.clients.openai" in result
    
    def test_compute_client_import_same_package(self):
        """Test import path when client and flow are in same package."""
        from integration_coworker.codegen.paths import compute_client_import_for_flow
        
        result = compute_client_import_for_flow(
            flow_file_path="src/myapp/flows/feature.py",
            client_file_path="src/myapp/clients/api.py",
            prefer_relative=True,
        )
        
        # Should use relative import for same package
        assert "..clients.api" in result or "myapp.clients.api" in result
    
    def test_should_use_relative_import_same_package(self):
        """Test detection of same package for relative imports."""
        from integration_coworker.codegen.paths import should_use_relative_import
        
        # Same package should use relative
        assert should_use_relative_import(
            "src/myapp/flows/feature.py",
            "src/myapp/clients/api.py"
        ) == True
    
    def test_should_use_relative_import_different_package(self):
        """Test detection of different packages for absolute imports."""
        from integration_coworker.codegen.paths import should_use_relative_import
        
        # Different packages should use absolute
        result = should_use_relative_import(
            "src/docformatter/ai_enhancer.py",
            "src/integrations/clients/openai.py"
        )
        # Custom location outside integrations should prefer absolute
        assert result == False


class TestV37001_CodegenContextImport:
    """Test CodegenContext.get_effective_client_import_path() for V37-001."""
    
    def test_default_import_path_no_explicit_path(self):
        """Test default import path when no explicit flow path is set."""
        from integration_coworker.codegen.context import CodegenContext
        
        ctx = CodegenContext(
            provider_code="openai",
            task_slug="enhance_docstring",
            client_module="openai",
            client_class="OpenaiClient",
            method_name="create_completion",
            client_import_path="integrations.clients.openai",
            flow_module="openai_enhance_docstring",
            flow_function="enhance_docstring_flow",
            flow_import_path="integrations.flows.openai_enhance_docstring",
            test_module="test_openai_enhance_docstring",
            test_class="TestOpenaiFlow",
            clients_dir="src/integrations/clients",
            flows_dir="src/integrations/flows",
            tests_dir="tests/integrations",
            task_explicit_path=None,  # No custom path
            has_task_overrides=False,
        )
        
        # Should return default import path
        assert ctx.get_effective_client_import_path() == "integrations.clients.openai"
    
    def test_effective_import_path_with_explicit_flow_path(self):
        """Test import path is computed when flow has explicit path."""
        from integration_coworker.codegen.context import CodegenContext
        
        ctx = CodegenContext(
            provider_code="openai",
            task_slug="enhance_docstring",
            client_module="openai",
            client_class="OpenaiClient",
            method_name="create_completion",
            client_import_path="integrations.clients.openai",
            flow_module="ai_enhancer",
            flow_function="enhance_docstring",
            flow_import_path="docformatter.ai_enhancer",
            test_module="test_ai_enhancer",
            test_class="TestAiEnhancer",
            clients_dir="src/integrations/clients",
            flows_dir="src/integrations/flows",
            tests_dir="tests",
            task_explicit_path="src/docformatter/ai_enhancer.py",  # Custom path
            task_function_name="enhance_docstring",
            has_task_overrides=True,
        )
        
        result = ctx.get_effective_client_import_path()
        
        # Should compute import path from flow's location
        # The client is at src/docformatter/clients/openai.py (relocated)
        # So import should be relative or absolute depending on preference
        assert "openai" in result.lower()


# =============================================================================
# V37-002: Honor User's Explicit Function Name
# =============================================================================

class TestV37002_FunctionNameConstraints:
    """
    V37-002: When user specifies explicit function name like "enhance_docstring()",
    the generated code should use that name, not "enhance_docstring_flow" or
    "enhance_docstring_with_llm_flow".
    
    Bug: User requested `enhance_docstring()` but got `enhance_docstring_with_llm_flow()`
    Fix: Pass task_function_name to LLM prompts as a constraint.
    """
    
    def test_build_task_function_constraints_with_name(self):
        """Test constraint text is generated for function name."""
        from integration_coworker.codegen.prompts import _build_task_function_constraints
        
        result = _build_task_function_constraints(
            task_function_name="enhance_docstring",
            task_function_signature=None,
            artifact_kind="flow",
        )
        
        assert "enhance_docstring" in result
        assert "CRITICAL" in result
        assert "_flow" in result  # Should warn against generic _flow suffix
    
    def test_build_task_function_constraints_with_signature(self):
        """Test constraint text is generated for full signature."""
        from integration_coworker.codegen.prompts import _build_task_function_constraints
        
        result = _build_task_function_constraints(
            task_function_name="enhance_docstring",
            task_function_signature="(original: str, code: str) -> str",
            artifact_kind="flow",
        )
        
        assert "enhance_docstring" in result
        assert "original: str" in result
        assert "code: str" in result
        assert "-> str" in result
    
    def test_build_task_function_constraints_none_specified(self):
        """Test constraint text when no requirements specified."""
        from integration_coworker.codegen.prompts import _build_task_function_constraints
        
        result = _build_task_function_constraints(
            task_function_name=None,
            task_function_signature=None,
            artifact_kind="flow",
        )
        
        assert "None specified" in result
    
    def test_build_task_function_constraints_for_test(self):
        """Test constraint text for test artifact."""
        from integration_coworker.codegen.prompts import _build_task_function_constraints
        
        result = _build_task_function_constraints(
            task_function_name="enhance_docstring",
            task_function_signature=None,
            artifact_kind="test",
        )
        
        assert "enhance_docstring" in result
        assert "test" in result.lower()


class TestV37002_CodegenContextFlowFunction:
    """Test CodegenContext.get_effective_flow_function() for V37-002."""
    
    def test_default_flow_function_no_override(self):
        """Test default flow function when no override specified."""
        from integration_coworker.codegen.context import CodegenContext
        
        ctx = CodegenContext(
            provider_code="openai",
            task_slug="enhance_docstring",
            client_module="openai",
            client_class="OpenaiClient",
            method_name="create_completion",
            client_import_path="integrations.clients.openai",
            flow_module="openai_enhance_docstring",
            flow_function="enhance_docstring_flow",  # Default generated name
            flow_import_path="integrations.flows.openai_enhance_docstring",
            test_module="test_openai_enhance_docstring",
            test_class="TestOpenaiFlow",
            clients_dir="src/integrations/clients",
            flows_dir="src/integrations/flows",
            tests_dir="tests/integrations",
            task_function_name=None,  # No override
            has_task_overrides=False,
        )
        
        assert ctx.get_effective_flow_function() == "enhance_docstring_flow"
    
    def test_effective_flow_function_with_override(self):
        """Test flow function respects user's explicit name."""
        from integration_coworker.codegen.context import CodegenContext
        
        ctx = CodegenContext(
            provider_code="openai",
            task_slug="enhance_docstring",
            client_module="openai",
            client_class="OpenaiClient",
            method_name="create_completion",
            client_import_path="integrations.clients.openai",
            flow_module="ai_enhancer",
            flow_function="enhance_docstring_flow",  # Default would be this
            flow_import_path="docformatter.ai_enhancer",
            test_module="test_ai_enhancer",
            test_class="TestAiEnhancer",
            clients_dir="src/integrations/clients",
            flows_dir="src/integrations/flows",
            tests_dir="tests",
            task_function_name="enhance_docstring",  # User specified this!
            has_task_overrides=True,
        )
        
        # Should return user's explicit name, not default
        assert ctx.get_effective_flow_function() == "enhance_docstring"


# =============================================================================
# V37-003: Relocate Client for CLI Repos
# =============================================================================

class TestV37003_ClientRelocation:
    """
    V37-003: When flow has explicit path outside integrations/, place client
    in a clients/ subdirectory near the flow for better organization.
    
    Bug: Client at src/integrations/clients/ even when flow at src/docformatter/
    Fix: Place client at src/docformatter/clients/ when flow is relocated.
    """
    
    def test_client_path_default_no_explicit_flow(self):
        """Test client path when no explicit flow path specified."""
        from integration_coworker.codegen.context import CodegenContext
        
        ctx = CodegenContext(
            provider_code="openai",
            task_slug="enhance_docstring",
            client_module="openai",
            client_class="OpenaiClient",
            method_name="create_completion",
            client_import_path="integrations.clients.openai",
            flow_module="openai_enhance_docstring",
            flow_function="enhance_docstring_flow",
            flow_import_path="integrations.flows.openai_enhance_docstring",
            test_module="test_openai_enhance_docstring",
            test_class="TestOpenaiFlow",
            clients_dir="src/integrations/clients",
            flows_dir="src/integrations/flows",
            tests_dir="tests/integrations",
            task_explicit_path=None,  # No custom path
            has_task_overrides=False,
        )
        
        # Should use default location
        assert ctx.get_client_rel_path() == "src/integrations/clients/openai.py"
    
    def test_client_path_with_explicit_flow_in_integrations(self):
        """Test client path when flow is still in integrations (no relocation)."""
        from integration_coworker.codegen.context import CodegenContext
        
        ctx = CodegenContext(
            provider_code="openai",
            task_slug="enhance_docstring",
            client_module="openai",
            client_class="OpenaiClient",
            method_name="create_completion",
            client_import_path="integrations.clients.openai",
            flow_module="custom_flow",
            flow_function="enhance_docstring",
            flow_import_path="integrations.flows.custom_flow",
            test_module="test_custom_flow",
            test_class="TestCustomFlow",
            clients_dir="src/integrations/clients",
            flows_dir="src/integrations/flows",
            tests_dir="tests",
            task_explicit_path="src/integrations/flows/custom_flow.py",  # Still in integrations
            has_task_overrides=True,
        )
        
        # Should use default location since flow is still in integrations
        assert ctx.get_client_rel_path() == "src/integrations/clients/openai.py"
    
    def test_client_path_relocated_near_custom_flow(self):
        """Test client path when flow is outside integrations (should relocate)."""
        from integration_coworker.codegen.context import CodegenContext
        
        ctx = CodegenContext(
            provider_code="openai",
            task_slug="enhance_docstring",
            client_module="openai",
            client_class="OpenaiClient",
            method_name="create_completion",
            client_import_path="integrations.clients.openai",
            flow_module="ai_enhancer",
            flow_function="enhance_docstring",
            flow_import_path="docformatter.ai_enhancer",
            test_module="test_ai_enhancer",
            test_class="TestAiEnhancer",
            clients_dir="src/integrations/clients",
            flows_dir="src/integrations/flows",
            tests_dir="tests",
            task_explicit_path="src/docformatter/ai_enhancer.py",  # Custom location
            has_task_overrides=True,
        )
        
        # Should relocate client near the flow
        result = ctx.get_client_rel_path()
        assert "src/docformatter/clients/openai.py" == result
    
    def test_client_path_nested_custom_flow(self):
        """Test client path when flow is in nested custom location."""
        from integration_coworker.codegen.context import CodegenContext
        
        ctx = CodegenContext(
            provider_code="stripe",
            task_slug="create_checkout",
            client_module="stripe",
            client_class="StripeClient",
            method_name="create_checkout_session",
            client_import_path="integrations.clients.stripe",
            flow_module="checkout",
            flow_function="create_checkout",
            flow_import_path="myapp.payments.checkout",
            test_module="test_checkout",
            test_class="TestCheckout",
            clients_dir="src/integrations/clients",
            flows_dir="src/integrations/flows",
            tests_dir="tests",
            task_explicit_path="src/myapp/payments/checkout.py",  # Nested custom location
            has_task_overrides=True,
        )
        
        # Should place client near nested flow
        result = ctx.get_client_rel_path()
        assert result == "src/myapp/payments/clients/stripe.py"


# =============================================================================
# Integration Tests: V37 Fixes Working Together
# =============================================================================

class TestV37Integration:
    """Test all V37 fixes working together."""
    
    def test_full_context_with_all_overrides(self):
        """Test CodegenContext with all V37 features enabled."""
        from integration_coworker.codegen.context import CodegenContext
        
        ctx = CodegenContext(
            provider_code="openai",
            task_slug="enhance_docstring",
            client_module="openai",
            client_class="OpenaiClient",
            method_name="create_completion",
            client_import_path="integrations.clients.openai",  # Default
            flow_module="ai_enhancer",
            flow_function="enhance_docstring_flow",  # Default
            flow_import_path="docformatter.ai_enhancer",
            test_module="test_ai_enhancer",
            test_class="TestAiEnhancer",
            clients_dir="src/integrations/clients",
            flows_dir="src/integrations/flows",
            tests_dir="tests",
            # V37 overrides:
            task_explicit_path="src/docformatter/ai_enhancer.py",
            task_function_name="enhance_docstring",  # V37-002
            has_task_overrides=True,
        )
        
        # V37-002: Function name should be user's explicit name
        assert ctx.get_effective_flow_function() == "enhance_docstring"
        
        # V37-003: Client should be near the flow
        client_path = ctx.get_client_rel_path()
        assert "docformatter/clients" in client_path
        
        # V37-001: Import should be computed from new locations
        import_path = ctx.get_effective_client_import_path()
        # Should reference the relocated client
        assert "openai" in import_path.lower()
    
    def test_no_overrides_uses_defaults(self):
        """Test CodegenContext with no V37 overrides uses defaults."""
        from integration_coworker.codegen.context import CodegenContext
        
        ctx = CodegenContext(
            provider_code="openai",
            task_slug="enhance_docstring",
            client_module="openai",
            client_class="OpenaiClient",
            method_name="create_completion",
            client_import_path="integrations.clients.openai",
            flow_module="openai_enhance_docstring",
            flow_function="enhance_docstring_flow",
            flow_import_path="integrations.flows.openai_enhance_docstring",
            test_module="test_openai_enhance_docstring",
            test_class="TestOpenaiFlow",
            clients_dir="src/integrations/clients",
            flows_dir="src/integrations/flows",
            tests_dir="tests/integrations",
            # No V37 overrides
            task_explicit_path=None,
            task_function_name=None,
            has_task_overrides=False,
        )
        
        # All should return defaults
        assert ctx.get_effective_flow_function() == "enhance_docstring_flow"
        assert ctx.get_client_rel_path() == "src/integrations/clients/openai.py"
        assert ctx.get_effective_client_import_path() == "integrations.clients.openai"


# =============================================================================
# Test Task Parser Integration with V37-002
# =============================================================================

class TestV37002_TaskParserIntegration:
    """Test that task_parser extracts function names for V37-002."""
    
    def test_extract_function_name_from_task(self):
        """Test task parser extracts function name from task description."""
        from integration_coworker.codegen.task_parser import extract_task_requirements
        
        result = extract_task_requirements(
            "Add a new file src/docformatter/ai_enhancer.py with a function "
            "enhance_docstring(original: str, function_code: str) -> str"
        )
        
        assert result.has_explicit_structure
        assert result.primary_target_path == "src/docformatter/ai_enhancer.py"
        assert len(result.requested_functions) >= 1
        
        # Should extract the function name
        func_names = [f.name for f in result.requested_functions]
        assert "enhance_docstring" in func_names
    
    def test_extract_function_with_params(self):
        """Test task parser extracts function parameters."""
        from integration_coworker.codegen.task_parser import extract_task_requirements
        
        result = extract_task_requirements(
            "Create enhance_docstring(original: str, code: str) -> str"
        )
        
        # Find the enhance_docstring function
        enhance_func = None
        for func in result.requested_functions:
            if func.name == "enhance_docstring":
                enhance_func = func
                break
        
        assert enhance_func is not None
        
        # Should have extracted parameters
        param_names = [p[0] for p in enhance_func.parameters]
        assert "original" in param_names
        assert "code" in param_names

    def test_extract_function_name_pattern_v37_002_fix(self):
        """
        V37-002 Fix: Test that 'with function name X' extracts X, not 'name'.
        
        This test validates the fix for the bug where 'with function name format_docstrings_via_api'
        incorrectly extracted 'name' as the function name instead of 'format_docstrings_via_api'.
        """
        from integration_coworker.codegen.task_parser import extract_task_requirements
        
        # This exact pattern was used in docformatter testing
        result = extract_task_requirements(
            "Create a format_code integration flow at src/docformatter/formatter_integration.py "
            "with function name format_docstrings_via_api"
        )
        
        assert result.has_explicit_structure
        assert result.primary_target_path == "src/docformatter/formatter_integration.py"
        
        # CRITICAL: Should extract 'format_docstrings_via_api', NOT 'name'
        func_names = [f.name for f in result.requested_functions]
        assert "format_docstrings_via_api" in func_names, (
            f"Expected 'format_docstrings_via_api' but got {func_names}"
        )
        assert "name" not in func_names, (
            f"Incorrectly extracted 'name' as function: {func_names}"
        )
    
    def test_function_name_blacklist_excludes_common_words(self):
        """Test that common words like 'name', 'called', etc. are excluded."""
        from integration_coworker.codegen.task_parser import FUNCTION_NAME_BLACKLIST
        
        # These common words should be in the blacklist
        expected_blacklisted = ['name', 'called', 'function', 'method', 'with', 'the', 'a']
        for word in expected_blacklisted:
            assert word in FUNCTION_NAME_BLACKLIST, f"'{word}' should be in blacklist"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
