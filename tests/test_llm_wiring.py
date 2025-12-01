"""
LLM Wiring Tests

Validates that:
1. Archetypes load with correct provider/model combinations
2. get_llm_client_for_archetype returns correct client types
3. LLM_MODEL env var is provider-aware (doesn't cross-pollute)
4. USE_MOCK_LLM properly switches to mock client
5. All archetype YAMLs have valid provider/model pairs
"""
import os
import pytest
from pathlib import Path


class TestArchetypeLoading:
    """Test that archetypes load with correct provider/model combinations."""

    def test_key_nodes_have_correct_provider_and_model(self, monkeypatch):
        """Verify each key node archetype has expected provider and model prefix."""
        # Clear env overrides to test base config
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        
        from integration_coworker.config import load_archetype, reset_archetype_cache
        
        reset_archetype_cache()
        
        expected_configs = [
            ("understand_task", {"provider": "anthropic", "model_prefix": "claude-sonnet-4"}),
            ("plan_integration_flow", {"provider": "anthropic", "model_prefix": "claude-sonnet-4"}),
            ("generate_code_and_tests", {"provider": "anthropic", "model_prefix": "claude-sonnet-4"}),
            # build_report uses base archetype (openai)
        ]
        
        for node, expected in expected_configs:
            archetype = load_archetype(node)
            model_config = archetype.get("model", {})
            
            assert model_config.get("provider") == expected["provider"], \
                f"{node}: expected provider={expected['provider']}, got {model_config.get('provider')}"
            assert model_config.get("name", "").startswith(expected["model_prefix"]), \
                f"{node}: expected model starting with {expected['model_prefix']}, got {model_config.get('name')}"

    def test_openai_archetypes_use_openai_models(self, monkeypatch):
        """Verify OpenAI-provider archetypes use OpenAI models."""
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        
        from integration_coworker.config import load_archetype, reset_archetype_cache
        
        reset_archetype_cache()
        
        # Base archetype defaults to OpenAI
        archetype = load_archetype("_base")
        model_config = archetype.get("model", {})
        
        assert model_config.get("provider") == "openai", \
            f"_base should use openai provider, got {model_config.get('provider')}"
        # gpt-4 is the default in base
        model_name = model_config.get("name", "")
        assert model_name.startswith("gpt-") or model_name == "", \
            f"_base OpenAI archetype should use gpt-* model, got {model_name}"


class TestLLMClientWiring:
    """Test that get_llm_client_for_archetype returns correct client types."""

    def test_anthropic_archetype_returns_anthropic_client(self, monkeypatch):
        """Anthropic archetypes should return AnthropicLLMClient."""
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        
        from integration_coworker.config import load_archetype, reset_archetype_cache
        from integration_coworker.llm.client import get_llm_client_for_archetype, AnthropicLLMClient, reset_client_cache
        
        reset_archetype_cache()
        reset_client_cache()
        
        archetype = load_archetype("understand_task")
        client = get_llm_client_for_archetype(archetype)
        
        assert isinstance(client, AnthropicLLMClient), \
            f"Expected AnthropicLLMClient for understand_task, got {type(client).__name__}"

    def test_openai_archetype_returns_openai_client(self, monkeypatch):
        """OpenAI archetypes should return OpenAILLMClient."""
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        
        from integration_coworker.config import load_archetype, reset_archetype_cache
        from integration_coworker.llm.client import get_llm_client_for_archetype, OpenAILLMClient, reset_client_cache
        
        reset_archetype_cache()
        reset_client_cache()
        
        # _base uses OpenAI
        archetype = load_archetype("_base")
        client = get_llm_client_for_archetype(archetype)
        
        assert isinstance(client, OpenAILLMClient), \
            f"Expected OpenAILLMClient for _base, got {type(client).__name__}"


class TestEnvOverrides:
    """Test that LLM_MODEL is provider-aware and USE_MOCK_LLM works."""

    def test_llm_model_env_only_affects_matching_provider(self, monkeypatch):
        """LLM_MODEL=gpt-4o-mini should only affect OpenAI archetypes, not Anthropic."""
        monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        
        from integration_coworker.config import load_archetype, reset_archetype_cache
        
        reset_archetype_cache()
        
        # Anthropic archetype should NOT pick up gpt-4o-mini
        anthropic_archetype = load_archetype("understand_task")
        anthropic_model = anthropic_archetype.get("model", {}).get("name", "")
        assert not anthropic_model.startswith("gpt-"), \
            f"Anthropic archetype should not use gpt-* model, got {anthropic_model}"
        assert anthropic_model.startswith("claude-"), \
            f"Anthropic archetype should keep claude-* model, got {anthropic_model}"
        
        # OpenAI archetype SHOULD pick up gpt-4o-mini
        openai_archetype = load_archetype("_base")
        openai_model = openai_archetype.get("model", {}).get("name", "")
        assert openai_model == "gpt-4o-mini", \
            f"OpenAI archetype should use gpt-4o-mini from env, got {openai_model}"

    def test_use_mock_llm_returns_mock_client(self, monkeypatch):
        """USE_MOCK_LLM=true should return MockLLMClient for any provider."""
        monkeypatch.setenv("USE_MOCK_LLM", "true")
        
        from integration_coworker.config import load_archetype, reset_archetype_cache
        from integration_coworker.llm.client import get_llm_client_for_archetype, MockLLMClient, reset_client_cache
        
        reset_archetype_cache()
        reset_client_cache()
        
        # Even Anthropic archetype should return MockLLMClient
        archetype = load_archetype("understand_task")
        client = get_llm_client_for_archetype(archetype)
        
        assert isinstance(client, MockLLMClient), \
            f"Expected MockLLMClient when USE_MOCK_LLM=true, got {type(client).__name__}"

    def test_anthropic_model_override_only_affects_anthropic(self, monkeypatch):
        """LLM_MODEL=claude-* should only affect Anthropic archetypes."""
        monkeypatch.setenv("LLM_MODEL", "claude-3-haiku-20240307")
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        
        from integration_coworker.config import load_archetype, reset_archetype_cache
        
        reset_archetype_cache()
        
        # Anthropic archetype SHOULD pick up claude-3-haiku
        anthropic_archetype = load_archetype("understand_task")
        anthropic_model = anthropic_archetype.get("model", {}).get("name", "")
        assert anthropic_model == "claude-3-haiku-20240307", \
            f"Anthropic archetype should use claude-3-haiku from env, got {anthropic_model}"
        
        # OpenAI archetype should NOT pick up claude-*
        openai_archetype = load_archetype("_base")
        openai_model = openai_archetype.get("model", {}).get("name", "")
        assert not openai_model.startswith("claude-"), \
            f"OpenAI archetype should not use claude-* model, got {openai_model}"


class TestArchetypeConfigurationInvariants:
    """Test that all archetype YAMLs have valid provider/model pairs."""

    def test_all_archetypes_have_valid_provider_model_pairs(self, monkeypatch):
        """Every archetype YAML should have matching provider and model prefix."""
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        
        from integration_coworker.config import load_archetype, reset_archetype_cache, list_available_archetypes
        
        reset_archetype_cache()
        
        archetypes = list_available_archetypes()
        assert len(archetypes) > 0, "No archetypes found"
        
        for node_name in archetypes:
            archetype = load_archetype(node_name)
            model_config = archetype.get("model", {})
            provider = model_config.get("provider", "openai")
            model_name = model_config.get("name", "")
            
            if provider == "anthropic":
                assert model_name.startswith("claude-"), \
                    f"Archetype {node_name}: Anthropic provider should use claude-* model, got {model_name}"
                assert not model_name.startswith("gpt-"), \
                    f"Archetype {node_name}: Anthropic provider has gpt-* model, which is wrong"
            elif provider == "openai":
                assert not model_name.startswith("claude-"), \
                    f"Archetype {node_name}: OpenAI provider should not use claude-* model, got {model_name}"
            else:
                # Mock provider is fine
                assert provider in ("openai", "anthropic", "mock"), \
                    f"Archetype {node_name}: Unknown provider {provider}"

    def test_archetype_files_exist_for_key_nodes(self):
        """Key nodes should have archetype files."""
        archetypes_dir = Path(__file__).parent.parent / "src" / "integration_coworker" / "config" / "archetypes"
        
        expected_files = [
            "_base.archetype.yaml",
            "understand_task.archetype.yaml",
            "plan_integration_flow.archetype.yaml",
            "generate_code_and_tests.archetype.yaml",
        ]
        
        for filename in expected_files:
            filepath = archetypes_dir / filename
            assert filepath.exists(), f"Expected archetype file {filename} not found at {filepath}"


class TestMissingDependencies:
    """Test behavior when optional dependencies are missing."""

    def test_missing_api_key_falls_back_to_mock(self, monkeypatch):
        """Missing API key should fall back to mock client (non-strict mode)."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        
        from integration_coworker.config import load_archetype, reset_archetype_cache
        from integration_coworker.llm.client import get_llm_client_for_archetype, MockLLMClient, reset_client_cache
        
        reset_archetype_cache()
        reset_client_cache()
        
        archetype = load_archetype("understand_task")
        # Non-strict mode should fall back to mock
        client = get_llm_client_for_archetype(archetype, strict=False)
        
        assert isinstance(client, MockLLMClient), \
            f"Expected MockLLMClient when API key missing, got {type(client).__name__}"

    def test_missing_api_key_raises_in_strict_mode(self, monkeypatch):
        """Missing API key should raise in strict mode."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        
        from integration_coworker.config import load_archetype, reset_archetype_cache
        from integration_coworker.llm.client import get_llm_client_for_archetype, reset_client_cache
        
        reset_archetype_cache()
        reset_client_cache()
        
        archetype = load_archetype("understand_task")
        
        with pytest.raises(RuntimeError) as exc_info:
            get_llm_client_for_archetype(archetype, strict=True)
        
        assert "API_KEY" in str(exc_info.value)
