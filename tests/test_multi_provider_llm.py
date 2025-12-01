"""
Tests for multi-provider LLM client support.

Tests OpenAI, Anthropic, and mock LLM clients via LangChain.
"""
import os
import pytest
from unittest.mock import Mock, patch, MagicMock

from integration_coworker.llm.client import (
    get_llm_client,
    get_llm_client_for_archetype,
    reset_client_cache,
    MockLLMClient,
    OpenAILLMClient,
    AnthropicLLMClient,
    LLMProvider,
)
from integration_coworker.config import reset_settings, reset_archetype_cache

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
        """Test default model is Claude 3 Sonnet."""
        client = AnthropicLLMClient(api_key="test-key")
        
        assert "claude-3-sonnet" in client.model
    
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
        """Test that strict mode raises for Anthropic without key."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("USE_MOCK_LLM", raising=False)
        reset_client_cache()
        reset_settings()
        
        with pytest.raises(RuntimeError) as exc_info:
            get_llm_client("test", strict=True, provider="anthropic")
        
        assert "ANTHROPIC_API_KEY" in str(exc_info.value)
    
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
