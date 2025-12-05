"""
Tests for multi-provider LLM client support.

Tests OpenAI, Anthropic, Google Gemini, and mock LLM clients via LangChain.
"""
import os
import pytest
from unittest.mock import Mock, patch, MagicMock

from integration_coworker.llm.client import (
    get_llm_client,
    get_llm_client_for_archetype,
    get_llm_client_for_node,
    reset_client_cache,
    MockLLMClient,
    OpenAILLMClient,
    AnthropicLLMClient,
    GoogleLLMClient,
    LLMProvider,
    _try_create_client_for_provider,
)
from integration_coworker.config import (
    reset_settings,
    reset_archetype_cache,
    load_archetype,
    get_archetype_model_config,
)

# Mark all tests in this module as not requiring the database
pytestmark = pytest.mark.no_db


@pytest.fixture(autouse=True)
def reset_state():
    """Reset caches before each test."""
    reset_client_cache()
    reset_settings()
    reset_archetype_cache()
    yield
    reset_client_cache()
    reset_settings()
    reset_archetype_cache()


class TestMockLLMClient:
    """Tests for MockLLMClient."""
    
    def test_complete_returns_string(self):
        """Test that complete returns a string."""
        client = MockLLMClient(task_type="test")
        result = client.complete("test prompt")
        
        assert isinstance(result, str)
        assert len(result) > 0
    
    def test_complete_json_returns_dict(self):
        """Test that complete_json returns a dict."""
        client = MockLLMClient(task_type="test")
        result = client.complete_json("test prompt with workflow plan")
        
        assert isinstance(result, dict)
    
    def test_task_understanding_response(self):
        """Test mock response for task understanding."""
        client = MockLLMClient(task_type="planning")
        result = client.complete_json("Analyze this integration task: create checkout session")
        
        assert "task_slug" in result
        assert "create_checkout_session" in result.get("task_slug", "")
    
    def test_workflow_planning_response(self):
        """Test mock response for workflow planning."""
        client = MockLLMClient(task_type="planning")
        result = client.complete_json("Plan a workflow for this task")
        
        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) > 0


class TestOpenAILLMClient:
    """Tests for OpenAILLMClient."""
    
    def test_initialization(self):
        """Test client initialization."""
        client = OpenAILLMClient(
            api_key="test-key",
            model="gpt-4",
            task_type="test",
        )
        
        assert client.api_key == "test-key"
        assert client.model == "gpt-4"
        assert client.task_type == "test"
    
    def test_default_temperature(self):
        """Test default temperature setting."""
        client = OpenAILLMClient(
            api_key="test-key",
            default_temperature=0.5,
        )
        
        assert client.default_temperature == 0.5
    
    @patch("integration_coworker.llm.client.OpenAILLMClient._get_llm")
    def test_complete_calls_langchain(self, mock_get_llm):
        """Test that complete calls LangChain."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "test response"
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm
        
        client = OpenAILLMClient(api_key="test-key")
        result = client.complete("test prompt")
        
        assert result == "test response"
        mock_llm.invoke.assert_called_once()


class TestAnthropicLLMClient:
    """Tests for AnthropicLLMClient."""
    
    def test_initialization(self):
        """Test client initialization."""
        client = AnthropicLLMClient(
            api_key="test-key",
            model="claude-3-sonnet-20240229",
            task_type="test",
        )
        
        assert client.api_key == "test-key"
        assert client.model == "claude-3-sonnet-20240229"
        assert client.task_type == "test"
    
    def test_default_model(self):
        """Test default model is Claude Sonnet 4."""
        client = AnthropicLLMClient(api_key="test-key")
        
        assert "claude-sonnet-4" in client.model
    
    @patch("integration_coworker.llm.client.AnthropicLLMClient._get_llm")
    def test_complete_calls_langchain(self, mock_get_llm):
        """Test that complete calls LangChain."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "anthropic response"
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm
        
        client = AnthropicLLMClient(api_key="test-key")
        result = client.complete("test prompt")
        
        assert result == "anthropic response"
        mock_llm.invoke.assert_called_once()
    
    def test_metadata_includes_anthropic_provider(self):
        """Test that metadata includes anthropic as provider."""
        client = AnthropicLLMClient(api_key="test-key", task_type="codegen")
        metadata = client._build_metadata()
        
        assert metadata.get("provider") == "anthropic"
        assert metadata.get("task_type") == "codegen"


class TestGetLLMClient:
    """Tests for get_llm_client factory function."""
    
    def test_mock_mode_returns_mock_client(self, monkeypatch):
        """Test that USE_MOCK_LLM returns MockLLMClient."""
        monkeypatch.setenv("USE_MOCK_LLM", "true")
        reset_client_cache()
        reset_settings()
        
        client = get_llm_client("test")
        
        assert isinstance(client, MockLLMClient)
    
    def test_openai_with_api_key(self, monkeypatch):
        """Test that OpenAI client is returned with API key."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_settings()
        
        client = get_llm_client("test", provider="openai")
        
        assert isinstance(client, OpenAILLMClient)
    
    def test_anthropic_with_api_key(self, monkeypatch):
        """Test that Anthropic client is returned with API key."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_settings()
        
        client = get_llm_client("test", provider="anthropic")
        
        assert isinstance(client, AnthropicLLMClient)
    
    def test_missing_api_key_falls_back_to_mock(self, monkeypatch):
        """Test fallback to mock when API key is missing."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_settings()
        
        client = get_llm_client("test", strict=False)
        
        assert isinstance(client, MockLLMClient)
    
    def test_strict_mode_raises_without_api_key(self, monkeypatch):
        """Test that strict mode raises error without API key."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_settings()
        
        with pytest.raises(RuntimeError) as exc_info:
            get_llm_client("test", strict=True, provider="openai")
        
        assert "OPENAI_API_KEY" in str(exc_info.value)
    
    def test_strict_mode_raises_for_anthropic_without_key(self, monkeypatch):
        """Test that strict mode raises when Anthropic is requested and no providers are available."""
        # Clear ALL provider keys to test strict mode behavior
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_settings()
        
        # With fallback chain, strict mode now raises when NO providers are available
        with pytest.raises(RuntimeError) as exc_info:
            get_llm_client("test", strict=True, provider="anthropic")
        
        # Error message should mention both providers or the fallback failure
        error_msg = str(exc_info.value)
        assert "API" in error_msg or "key" in error_msg.lower()
    
    def test_anthropic_fallback_to_openai(self, monkeypatch):
        """Test that Anthropic falls back to OpenAI when Anthropic key is missing."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_settings()
        
        # Should fall back to OpenAI instead of failing
        client = get_llm_client("test", strict=True, provider="anthropic")
        
        assert isinstance(client, OpenAILLMClient)
    
    def test_client_caching(self, monkeypatch):
        """Test that clients are cached by task_type and provider."""
        monkeypatch.setenv("USE_MOCK_LLM", "true")
        reset_client_cache()
        reset_settings()
        
        client1 = get_llm_client("planning")
        client2 = get_llm_client("planning")
        client3 = get_llm_client("codegen")
        
        assert client1 is client2  # Same cache key
        assert client1 is not client3  # Different cache key


class TestGetLLMClientForArchetype:
    """Tests for get_llm_client_for_archetype function."""
    
    def test_uses_archetype_provider(self, monkeypatch):
        """Test that archetype provider is used."""
        monkeypatch.setenv("USE_MOCK_LLM", "true")
        reset_client_cache()
        
        archetype_config = {
            "name": "test_node",
            "role": "planning",
            "model": {
                "provider": "anthropic",
                "name": "claude-3-sonnet",
            }
        }
        
        client = get_llm_client_for_archetype(archetype_config)
        
        assert isinstance(client, MockLLMClient)  # Mock because USE_MOCK_LLM=true
    
    def test_extracts_role_as_task_type(self, monkeypatch):
        """Test that role is used as task_type."""
        monkeypatch.setenv("USE_MOCK_LLM", "true")
        reset_client_cache()
        
        archetype_config = {
            "role": "extraction",
            "model": {"provider": "openai"}
        }
        
        client = get_llm_client_for_archetype(archetype_config)
        
        assert client.task_type == "extraction"


class TestProviderSelection:
    """Tests for provider selection logic."""
    
    def test_explicit_provider_overrides_config(self, monkeypatch):
        """Test that explicit provider arg overrides config."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_settings()
        
        # Even though default might be OpenAI, explicit provider=anthropic should work
        client = get_llm_client("test", provider="anthropic")
        
        assert isinstance(client, AnthropicLLMClient)
    
    def test_mock_provider_explicit(self, monkeypatch):
        """Test explicit mock provider."""
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_settings()
        
        client = get_llm_client("test", provider="mock")
        
        assert isinstance(client, MockLLMClient)


class TestGoogleGeminiClient:
    """Tests for Google Gemini LLM client."""
    
    def test_initialization(self):
        """Test GoogleLLMClient initialization."""
        client = GoogleLLMClient(
            api_key="test-google-key",
            model="gemini-3-pro",
            task_type="test",
        )
        
        assert client.api_key == "test-google-key"
        assert client.model == "gemini-3-pro"
        assert client.task_type == "test"
    
    def test_default_model(self):
        """Test default model is Gemini 3 Pro."""
        client = GoogleLLMClient(api_key="test-key")
        
        assert client.model == "gemini-3-pro"
    
    def test_metadata_includes_google_provider(self):
        """Test that metadata includes google as provider."""
        client = GoogleLLMClient(api_key="test-key", task_type="repo_analysis")
        metadata = client._build_metadata()
        
        assert metadata.get("provider") == "google"
        assert metadata.get("task_type") == "repo_analysis"
    
    @patch("integration_coworker.llm.client.GoogleLLMClient._get_llm")
    def test_complete_calls_langchain(self, mock_get_llm):
        """Test that complete calls LangChain."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "gemini response"
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm
        
        client = GoogleLLMClient(api_key="test-key")
        result = client.complete("test prompt")
        
        assert result == "gemini response"
        mock_llm.invoke.assert_called_once()


class TestGoogleProviderFallback:
    """Tests for Google provider in fallback chain."""
    
    def test_google_client_creation_with_key(self, monkeypatch):
        """Test that Google client can be created with API key."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        
        client = _try_create_client_for_provider(
            provider="google",
            model_name="gemini-2.0-flash",
            temperature=0.3,
            max_tokens=2000,
            task_type="test",
        )
        
        assert isinstance(client, GoogleLLMClient)
        assert client.model == "gemini-2.0-flash"
        assert client.api_key == "test-google-key"
    
    def test_google_client_default_model(self, monkeypatch):
        """Test that Google client uses default model when not specified."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        
        client = _try_create_client_for_provider(
            provider="google",
            model_name=None,
            temperature=0.3,
            max_tokens=2000,
            task_type="test",
        )
        
        assert isinstance(client, GoogleLLMClient)
        assert client.model == "gemini-3-pro"  # Default
    
    def test_google_in_fallback_chain(self, monkeypatch):
        """Test that Google is included in fallback chain."""
        # Only have Google key
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_archetype_cache()
        
        archetype = load_archetype("understand_task")
        client = get_llm_client_for_archetype(archetype)
        
        # Should fall back to Google when OpenAI/Anthropic unavailable
        assert isinstance(client, GoogleLLMClient)
    
    def test_google_returns_none_without_key(self, monkeypatch):
        """Test that Google provider returns None without API key."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        reset_client_cache()
        
        client = _try_create_client_for_provider(
            provider="google",
            model_name="gemini-1.5-pro",
            temperature=0.3,
            max_tokens=2000,
            task_type="test",
        )
        
        assert client is None


class TestProviderLiteralType:
    """Tests for LLMProvider type literal."""
    
    def test_provider_literal_includes_google(self):
        """Test that LLMProvider includes google."""
        import typing
        
        # Get the literal values
        if hasattr(typing, 'get_args'):
            args = typing.get_args(LLMProvider)
        else:
            args = LLMProvider.__args__
        
        assert "google" in args
        assert "openai" in args
        assert "anthropic" in args
        assert "mock" in args


class TestNodeModelAssignment:
    """Tests that each node type gets the correct model/provider from archetypes."""

    def test_planning_nodes_use_openai_gpt51(self, monkeypatch):
        """Test that planning nodes are configured for GPT-5.1."""
        # Clear env overrides
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        reset_archetype_cache()
        
        planning_nodes = [
            "understand_task",
            "plan_integration_flow",
            "plan_run",
            "align_task_with_kg",
            "attach_policies_and_patterns",
        ]
        
        for node_name in planning_nodes:
            config = get_archetype_model_config(node_name)
            assert config.get("provider") == "openai", f"{node_name} should use openai"
            assert "gpt-5.1" in config.get("name", ""), f"{node_name} should use gpt-5.1"

    def test_codegen_nodes_use_anthropic_claude_opus_45(self, monkeypatch):
        """Test that code generation nodes are configured for Claude Opus 4."""
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        reset_archetype_cache()
        
        config = get_archetype_model_config("generate_code_and_tests")
        
        assert config.get("provider") == "anthropic"
        assert "claude" in config.get("name", "").lower()
        assert "sonnet" in config.get("name", "").lower()
        # Model name is claude-sonnet-4-20250514
        assert "sonnet-4" in config.get("name", "").lower()

    def test_extraction_nodes_use_gpt4o_mini(self, monkeypatch):
        """Test that extraction nodes use smaller, faster models."""
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        reset_archetype_cache()
        
        config = get_archetype_model_config("build_silver_api_model")
        
        assert config.get("provider") == "openai"
        assert "mini" in config.get("name", "").lower()


class TestClientForNode:
    """Tests for get_llm_client_for_node convenience function."""

    def test_get_client_for_planning_node(self, monkeypatch):
        """Test getting client for a planning node."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_archetype_cache()
        
        client = get_llm_client_for_node("understand_task")
        
        assert isinstance(client, OpenAILLMClient)
        assert "gpt-5.1" in client.model

    def test_get_client_for_codegen_node(self, monkeypatch):
        """Test getting client for a code generation node."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_archetype_cache()
        
        client = get_llm_client_for_node("generate_code_and_tests")
        
        assert isinstance(client, AnthropicLLMClient)
        assert "sonnet" in client.model.lower()
        # Model name is claude-sonnet-4-20250514
        assert "sonnet-4" in client.model.lower()


class TestModelTemperature:
    """Tests for temperature configuration by role."""

    def test_planning_nodes_low_temperature(self, monkeypatch):
        """Test that planning nodes have low temperature for consistency."""
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_archetype_cache()
        
        planning_nodes = ["understand_task", "plan_integration_flow"]
        
        for node_name in planning_nodes:
            config = get_archetype_model_config(node_name)
            assert config.get("temperature", 1.0) <= 0.3, f"{node_name} should have low temperature"

    def test_codegen_very_low_temperature(self, monkeypatch):
        """Test that code generation has very low temperature."""
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_archetype_cache()
        
        config = get_archetype_model_config("generate_code_and_tests")
        assert config.get("temperature", 1.0) <= 0.2

    def test_extraction_zero_temperature(self, monkeypatch):
        """Test that extraction uses zero temperature for determinism."""
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_archetype_cache()
        
        config = get_archetype_model_config("build_silver_api_model")
        assert config.get("temperature", 1.0) == 0.0
