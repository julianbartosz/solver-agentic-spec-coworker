"""
Integration tests for V2.2 Dynamic Capability Fixes.

Tests the 7 fixes from DYNAMIC_CAPABILITY_FIXES_PLAN.md:
1. Constrained Path Generation (Fix #1)
2. Test Fixture Injection (Fix #2)
3. Unified Semantic Index (Fix #3)
4. Hybrid Policy Inference (Fix #4)
5. Remove Archetype Detection (Fix #5)
6. Async LLM Batcher (Fix #6)
7. constrained_codegen Option (Fix #7)
"""
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import asyncio

from integration_coworker.api.types import IntegrationOptions
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import Endpoint, Policy


# ==============================================================================
# Fix #7: constrained_codegen Option Tests
# ==============================================================================

class TestConstrainedCodegenOption:
    """Tests for IntegrationOptions.constrained_codegen field."""
    
    def test_constrained_codegen_defaults_to_false(self):
        """constrained_codegen should default to False."""
        options = IntegrationOptions()
        assert options.constrained_codegen is False
    
    def test_constrained_codegen_can_be_enabled(self):
        """constrained_codegen should be settable to True."""
        options = IntegrationOptions(constrained_codegen=True)
        assert options.constrained_codegen is True
    
    def test_constrained_codegen_in_options_dict(self):
        """constrained_codegen should be serializable."""
        options = IntegrationOptions(constrained_codegen=True)
        # Verify it's a valid field by accessing it
        assert hasattr(options, 'constrained_codegen')


# ==============================================================================
# Fix #1: Constrained Path Generation Tests
# ==============================================================================

class TestConstrainedPathGeneration:
    """Tests for spec-injected constrained path generation."""
    
    def test_build_constrained_body_prompt_exists(self):
        """build_constrained_body_prompt should be importable."""
        from integration_coworker.codegen.prompts import build_constrained_body_prompt
        assert callable(build_constrained_body_prompt)
    
    def test_build_constrained_body_prompt_includes_exact_path(self):
        """Prompt should include the exact path from spec."""
        from integration_coworker.codegen.prompts import build_constrained_body_prompt
        
        prompt = build_constrained_body_prompt(
            endpoint_path="/v1/messages",
            http_method="POST",
            base_url="https://api.twilio.com",
            provider_code="twilio",
            client_class="TwilioClient",
            method_name="send_message",
            summary="Send a message",
            request_schema={"type": "object", "properties": {"to": {"type": "string"}}},
            response_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        )
        
        # Path should appear in prompt
        assert "/v1/messages" in prompt
        # Method should appear
        assert "POST" in prompt
        # Should instruct to use exact path
        assert "path" in prompt.lower()
    
    def test_constrained_generation_in_generate_code_and_tests(self):
        """_generate_constrained_client_code should exist."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_constrained_client_code
        )
        assert callable(_generate_constrained_client_code)


# ==============================================================================
# Fix #2: Test Fixture Injection Tests
# ==============================================================================

class TestTestFixtureInjection:
    """Tests for pytest fixture skeleton injection."""
    
    def test_fixture_skeleton_constant_exists(self):
        """TEST_FIXTURE_SKELETON should be defined."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            TEST_FIXTURE_SKELETON
        )
        assert TEST_FIXTURE_SKELETON is not None
        assert len(TEST_FIXTURE_SKELETON) > 0
    
    def test_fixture_skeleton_uses_environ_get(self):
        """Fixture skeleton should use os.environ.get for credentials."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            TEST_FIXTURE_SKELETON
        )
        
        # Should use os.environ.get pattern, not hardcoded creds
        assert "os.environ.get" in TEST_FIXTURE_SKELETON
        assert "API_KEY" in TEST_FIXTURE_SKELETON.upper()
    
    def test_fixture_skeleton_has_pytest_fixture(self):
        """Fixture skeleton should define pytest fixtures."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            TEST_FIXTURE_SKELETON
        )
        
        assert "@pytest.fixture" in TEST_FIXTURE_SKELETON
    
    def test_constrained_test_code_generator_exists(self):
        """_generate_constrained_test_code should exist."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _generate_constrained_test_code
        )
        assert callable(_generate_constrained_test_code)


# ==============================================================================
# Fix #3: Unified Semantic Index Tests
# ==============================================================================

class TestUnifiedSemanticIndex:
    """Tests for unified semantic search index."""
    
    def test_unified_index_class_exists(self):
        """UnifiedSemanticIndex class should be importable."""
        from integration_coworker.retrieval.unified_index import UnifiedSemanticIndex
        assert UnifiedSemanticIndex is not None
    
    def test_unified_index_search_method(self):
        """UnifiedSemanticIndex should have a search method."""
        from integration_coworker.retrieval.unified_index import UnifiedSemanticIndex
        
        index = UnifiedSemanticIndex()
        assert hasattr(index, 'search')
        assert callable(index.search)
    
    def test_unified_index_has_weights(self):
        """UnifiedSemanticIndex should support configurable weights."""
        from integration_coworker.retrieval.unified_index import UnifiedSemanticIndex
        
        index = UnifiedSemanticIndex(chunk_weight=0.7, template_weight=0.3)
        assert index.chunk_weight == 0.7
        assert index.template_weight == 0.3
    
    def test_unified_search_result_types(self):
        """UnifiedSearchResult and ResultType should be importable."""
        from integration_coworker.retrieval.unified_index import (
            UnifiedSearchResult,
            ResultType,
        )
        
        assert UnifiedSearchResult is not None
        assert ResultType.SPEC_CHUNK is not None
        assert ResultType.KG_TEMPLATE is not None


# ==============================================================================
# Fix #4: Hybrid Policy Inference Tests
# ==============================================================================

class TestHybridPolicyInference:
    """Tests for hybrid spec + LLM policy inference."""
    
    def test_infer_policies_from_spec_exists(self):
        """_infer_policies_from_spec should be importable."""
        from integration_coworker.graph.nodes.attach_policies_and_patterns import (
            _infer_policies_from_spec
        )
        assert callable(_infer_policies_from_spec)
    
    def test_augment_with_llm_analysis_exists(self):
        """_augment_with_llm_analysis should be importable."""
        from integration_coworker.graph.nodes.attach_policies_and_patterns import (
            _augment_with_llm_analysis
        )
        assert callable(_augment_with_llm_analysis)
    
    def test_extract_security_schemes_exists(self):
        """_extract_security_schemes should be importable."""
        from integration_coworker.graph.nodes.attach_policies_and_patterns import (
            _extract_security_schemes
        )
        assert callable(_extract_security_schemes)
    
    def test_infer_auth_policy_config_exists(self):
        """_infer_auth_policy_config should be importable."""
        from integration_coworker.graph.nodes.attach_policies_and_patterns import (
            _infer_auth_policy_config
        )
        assert callable(_infer_auth_policy_config)


# ==============================================================================
# Fix #5: Remove Archetype Detection Tests
# ==============================================================================

class TestRemoveArchetypeDetection:
    """Tests for simplified 2-tier profile resolution."""
    
    def test_detect_python_framework_exists(self):
        """_detect_python_framework should be importable."""
        from integration_coworker.graph.nodes.attach_repo_context import (
            _detect_python_framework
        )
        assert callable(_detect_python_framework)
    
    def test_detect_python_framework_finds_fastapi(self, tmp_path):
        """Should detect FastAPI from pyproject.toml."""
        from integration_coworker.graph.nodes.attach_repo_context import (
            _detect_python_framework
        )
        
        # Create pyproject.toml with FastAPI dependency
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('''
[project]
name = "test"
dependencies = ["fastapi>=0.100.0", "uvicorn"]
''')
        
        framework = _detect_python_framework(tmp_path)
        assert framework == "fastapi"
    
    def test_detect_python_framework_finds_flask(self, tmp_path):
        """Should detect Flask from requirements.txt."""
        from integration_coworker.graph.nodes.attach_repo_context import (
            _detect_python_framework
        )
        
        # Create requirements.txt with Flask
        requirements = tmp_path / "requirements.txt"
        requirements.write_text("flask>=2.0.0\ngunicorn\n")
        
        framework = _detect_python_framework(tmp_path)
        assert framework == "flask"
    
    def test_detect_python_framework_returns_generic(self, tmp_path):
        """Should return 'generic' when no framework detected."""
        from integration_coworker.graph.nodes.attach_repo_context import (
            _detect_python_framework
        )
        
        # Create minimal pyproject.toml without framework
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('''
[project]
name = "test"
dependencies = ["requests"]
''')
        
        framework = _detect_python_framework(tmp_path)
        assert framework == "generic"
    
    def test_convention_inference_sets_profile_source(self, tmp_path):
        """Profile should have profile_source='convention_inference'."""
        from integration_coworker.graph.nodes.attach_repo_context import (
            _infer_profile_from_conventions
        )
        
        # Create Python project
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "test"\n')
        
        profile = _infer_profile_from_conventions(tmp_path)
        
        assert profile is not None
        assert profile.profile_source == "convention_inference"


# ==============================================================================
# Fix #6: Async LLM Batcher Tests
# ==============================================================================

class TestAsyncLLMBatcher:
    """Tests for async LLM request batching."""
    
    def test_async_batcher_class_exists(self):
        """AsyncLLMBatcher class should be importable."""
        from integration_coworker.llm.async_batcher import AsyncLLMBatcher
        assert AsyncLLMBatcher is not None
    
    def test_batch_request_dataclass_exists(self):
        """BatchRequest dataclass should be importable."""
        from integration_coworker.llm.async_batcher import BatchRequest
        assert BatchRequest is not None
    
    def test_get_global_batcher_function_exists(self):
        """get_global_batcher function should be importable."""
        from integration_coworker.llm.async_batcher import get_global_batcher
        assert callable(get_global_batcher)
    
    def test_batcher_has_submit_method(self):
        """AsyncLLMBatcher should have submit method."""
        from integration_coworker.llm.async_batcher import AsyncLLMBatcher
        
        batcher = AsyncLLMBatcher()
        assert hasattr(batcher, 'submit')
        assert callable(batcher.submit)
    
    def test_batcher_has_lifecycle_methods(self):
        """AsyncLLMBatcher should have start/stop methods."""
        from integration_coworker.llm.async_batcher import AsyncLLMBatcher
        
        batcher = AsyncLLMBatcher()
        assert hasattr(batcher, 'start')
        assert hasattr(batcher, 'stop')
    
    def test_batch_metrics_exists(self):
        """BatchMetrics should be importable."""
        from integration_coworker.llm.async_batcher import BatchMetrics
        
        metrics = BatchMetrics()
        assert hasattr(metrics, 'total_requests')
        assert hasattr(metrics, 'completed_requests')
    
    def test_global_batcher_returns_singleton(self):
        """get_global_batcher should return same instance."""
        from integration_coworker.llm.async_batcher import get_global_batcher
        
        batcher1 = get_global_batcher()
        batcher2 = get_global_batcher()
        
        assert batcher1 is batcher2


# ==============================================================================
# Integration Tests
# ==============================================================================

class TestConstrainedModeE2E:
    """End-to-end tests for constrained codegen mode."""
    
    @pytest.fixture
    def mock_spec_path(self):
        """Path to mock payments spec."""
        return Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
    
    def test_options_passed_through_to_state(self, tmp_path, mock_spec_path):
        """constrained_codegen option should be passed through workflow."""
        from integration_coworker.graph.state import WorkflowState
        
        state = WorkflowState(
            source_refs=[str(mock_spec_path)],
            spec_refs=[str(mock_spec_path)],
            task_description="Create checkout session",
            provider_code="test_provider",
            options=IntegrationOptions(constrained_codegen=True),
        )
        
        assert state.options.constrained_codegen is True


class TestProfileResolutionE2E:
    """End-to-end tests for profile resolution."""
    
    def test_profile_resolution_order(self, tmp_path):
        """Profile resolution should follow config -> LLM -> convention order."""
        from integration_coworker.graph.nodes.attach_repo_context import (
            _get_profile_config_first
        )
        
        # Create a minimal Python project
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('''
[project]
name = "test-project"
dependencies = ["requests"]
''')
        
        # Without config file, should use convention inference
        profile = _get_profile_config_first(str(tmp_path), use_llm_fallback=False)
        
        assert profile is not None
        assert profile.profile_source in ("convention_inference", "default")
    
    def test_config_file_takes_priority(self, tmp_path):
        """Config file should take priority over other methods."""
        from integration_coworker.graph.nodes.attach_repo_context import (
            _get_profile_config_first
        )
        import yaml
        
        # Create config file with proper schema
        config = {
            "version": "1.0",
            "profile": {
                "name": "my-custom-project",
                "framework": "custom",
                "language": "python",
            },
            "layout": {
                "integrations_root": "custom/integrations",
                "tests_root": "custom/tests",
            },
        }
        
        config_file = tmp_path / ".integration-coworker.yaml"
        config_file.write_text(yaml.dump(config))
        
        profile = _get_profile_config_first(str(tmp_path), use_llm_fallback=False)
        
        assert profile is not None
        assert profile.profile_source == "config_file"
        assert profile.integrations_root == "custom/integrations"
