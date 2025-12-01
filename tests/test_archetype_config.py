"""
Tests for archetype configuration loading.

Per design doc Section 5.5 - Agent and node configuration is stored in YAML 
archetype files that follow an Anthropic-style pattern.
"""
import os
import pytest
from pathlib import Path

from integration_coworker.config import (
    load_archetype,
    get_archetype_model_config,
    get_archetype_prompt_config,
    get_archetype_retrieval_config,
    get_archetype_parallelism_config,
    list_available_archetypes,
    reset_archetype_cache,
    reset_settings,
)

# Mark all tests in this module as not requiring the database
pytestmark = pytest.mark.no_db


@pytest.fixture(autouse=True)
def reset_caches():
    """Reset caches before each test."""
    reset_archetype_cache()
    reset_settings()
    yield
    reset_archetype_cache()
    reset_settings()


class TestArchetypeLoading:
    """Tests for archetype file loading."""
    
    def test_list_available_archetypes(self):
        """Test listing available archetype files."""
        archetypes = list_available_archetypes()
        assert isinstance(archetypes, list)
        # Should have at least the archetypes we created
        assert "understand_task" in archetypes
        assert "generate_code_and_tests" in archetypes
        assert "plan_integration_flow" in archetypes
    
    def test_load_base_archetype_inheritance(self):
        """Test that archetypes inherit from _base."""
        archetype = load_archetype("understand_task")
        
        # Should have base defaults
        assert "version" in archetype.get("metadata", {}) or "model" in archetype
        
        # Should have node-specific overrides
        assert archetype.get("name") == "understand_task"
        assert archetype.get("role") == "planning"
    
    def test_load_nonexistent_archetype_uses_base(self):
        """Test that loading a nonexistent archetype returns base config."""
        archetype = load_archetype("nonexistent_node")
        
        # Should have base defaults
        assert "model" in archetype
        # Should not have node-specific fields
        assert archetype.get("name") is None
    
    def test_archetype_model_section(self):
        """Test that archetype has model configuration."""
        archetype = load_archetype("understand_task")
        
        model = archetype.get("model", {})
        assert "provider" in model
        assert "name" in model
        assert "temperature" in model
        assert "max_tokens" in model
    
    def test_archetype_caching(self):
        """Test that archetypes are cached after first load."""
        archetype1 = load_archetype("understand_task")
        archetype2 = load_archetype("understand_task")
        
        # Should be the exact same object (cached)
        assert archetype1 is archetype2


class TestArchetypeModelConfig:
    """Tests for model configuration extraction."""
    
    def test_get_model_config_openai(self, monkeypatch):
        """Test getting model config for OpenAI provider."""
        # Ensure no env overrides
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        reset_archetype_cache()
        
        config = get_archetype_model_config("build_silver_api_model")
        
        assert config.get("provider") == "openai"
        assert "gpt" in config.get("name", "").lower()
        assert "temperature" in config
    
    def test_get_model_config_anthropic(self, monkeypatch):
        """Test getting model config for Anthropic provider."""
        # Ensure no env overrides
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        reset_archetype_cache()
        
        config = get_archetype_model_config("understand_task")
        
        # understand_task is configured to use Anthropic
        assert config.get("provider") == "anthropic"
        assert "claude" in config.get("name", "").lower()
    
    def test_model_config_env_override(self, monkeypatch):
        """Test that environment variables override model config."""
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        monkeypatch.setenv("LLM_PROVIDER", "mock")
        monkeypatch.setenv("LLM_MODEL", "test-model")
        reset_archetype_cache()
        
        archetype = load_archetype("understand_task")
        model = archetype.get("model", {})
        
        assert model.get("provider") == "mock"
        assert model.get("name") == "test-model"
    
    def test_mock_mode_override(self, monkeypatch):
        """Test that USE_MOCK_LLM forces mock provider."""
        monkeypatch.setenv("USE_MOCK_LLM", "true")
        reset_archetype_cache()
        
        config = get_archetype_model_config("understand_task")
        
        assert config.get("use_mock") is True


class TestArchetypePromptConfig:
    """Tests for prompting configuration."""
    
    def test_planning_node_uses_chain_of_thought(self):
        """Test that planning nodes use chain_of_thought strategy."""
        config = get_archetype_prompt_config("understand_task")
        
        assert config.get("strategy") == "chain_of_thought"
    
    def test_extraction_node_uses_extraction_strategy(self):
        """Test that extraction nodes use extraction strategy."""
        config = get_archetype_prompt_config("build_silver_api_model")
        
        assert config.get("strategy") == "extraction"
    
    def test_codegen_node_uses_generation_strategy(self):
        """Test that codegen nodes use generation strategy."""
        config = get_archetype_prompt_config("generate_code_and_tests")
        
        assert config.get("strategy") == "generation"
    
    def test_prompt_has_system_template(self):
        """Test that archetypes have system prompt templates."""
        config = get_archetype_prompt_config("understand_task")
        
        assert "system_template" in config
        assert len(config["system_template"]) > 50  # Non-trivial template


class TestArchetypeRetrievalConfig:
    """Tests for retrieval configuration."""
    
    def test_retrieval_has_required_fields(self):
        """Test that retrieval config has required fields."""
        config = get_archetype_retrieval_config("understand_task")
        
        assert "top_k" in config
        assert "graph_radius" in config
        assert "token_budget" in config
    
    def test_planning_nodes_have_kg_sources(self):
        """Test that planning nodes include KG sources."""
        config = get_archetype_retrieval_config("align_task_with_kg")
        
        sources = config.get("sources", [])
        assert any("kg" in s for s in sources)
    
    def test_retrieval_values_in_expected_range(self):
        """Test that retrieval values are within expected ranges."""
        config = get_archetype_retrieval_config("understand_task")
        
        assert 1 <= config.get("top_k", 0) <= 20
        assert 0 <= config.get("graph_radius", -1) <= 5
        assert 1000 <= config.get("token_budget", 0) <= 10000


class TestArchetypeParallelismConfig:
    """Tests for parallelism configuration."""
    
    def test_planning_nodes_support_sampling(self):
        """Test that planning nodes support multiple samples."""
        config = get_archetype_parallelism_config("plan_integration_flow")
        
        assert config.get("num_samples", 0) >= 1
    
    def test_codegen_supports_candidates(self):
        """Test that codegen supports multiple candidates."""
        config = get_archetype_parallelism_config("generate_code_and_tests")
        
        assert config.get("num_candidates", 0) >= 1
    
    def test_parallelism_has_limits(self):
        """Test that parallelism has reasonable limits."""
        config = get_archetype_parallelism_config("understand_task")
        
        max_parallel = config.get("max_parallel_samples", 1)
        assert max_parallel <= 5  # Reasonable limit


class TestArchetypeSchemas:
    """Tests for input/output schema definitions."""
    
    def test_archetype_has_input_schema(self):
        """Test that archetypes define input schemas."""
        archetype = load_archetype("understand_task")
        
        assert "input_schema" in archetype
        input_schema = archetype["input_schema"]
        assert "task_description" in input_schema
    
    def test_archetype_has_output_schema(self):
        """Test that archetypes define output schemas."""
        archetype = load_archetype("understand_task")
        
        assert "output_schema" in archetype
        output_schema = archetype["output_schema"]
        assert "task_slug" in output_schema
    
    def test_codegen_output_includes_artifacts(self):
        """Test that codegen output schema includes code artifacts."""
        archetype = load_archetype("generate_code_and_tests")
        
        output_schema = archetype.get("output_schema", {})
        assert "artifacts" in output_schema


class TestProviderDiversity:
    """Tests for multi-provider configuration."""
    
    def test_archetypes_use_different_providers(self, monkeypatch):
        """Test that archetypes use different providers as designed."""
        # Ensure no env overrides
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        reset_archetype_cache()
        
        providers = {}
        
        for node_name in ["understand_task", "build_silver_api_model", "generate_code_and_tests"]:
            config = get_archetype_model_config(node_name)
            providers[node_name] = config.get("provider")
        
        # Should have at least two different providers
        unique_providers = set(providers.values())
        assert len(unique_providers) >= 2, f"Expected multiple providers, got: {providers}"
    
    def test_extraction_uses_smaller_model(self, monkeypatch):
        """Test that extraction uses smaller/faster model."""
        # Ensure no env overrides
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        reset_archetype_cache()
        
        extraction_config = get_archetype_model_config("build_silver_api_model")
        planning_config = get_archetype_model_config("plan_integration_flow")
        
        # Extraction should use mini/smaller model
        extraction_model = extraction_config.get("name", "")
        planning_model = planning_config.get("name", "")
        
        # Either extraction uses "mini" in name, or different from planning
        is_smaller = "mini" in extraction_model.lower() or extraction_model != planning_model
        assert is_smaller, f"Expected smaller model for extraction, got: {extraction_model}"
