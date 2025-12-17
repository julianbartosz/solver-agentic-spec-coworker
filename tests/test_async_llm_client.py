"""
Tests for the async LLM client module (Bug #35 fix).

Verifies that the async LLM client:
1. Can be instantiated with proper configuration
2. Has correct protocol/interface
3. Caching and factory functions work
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio

from integration_coworker.llm.async_client import (
    AsyncOpenAILLMClient,
    AsyncAnthropicLLMClient,
    get_async_llm_client,
    run_concurrent_llm_calls,
    reset_async_client_cache,
    AsyncLLMClient,
)


class TestAsyncLLMClientInterface:
    """Test the async client interface and instantiation."""
    
    def test_openai_client_instantiation(self):
        """Test that OpenAI async client can be instantiated."""
        client = AsyncOpenAILLMClient(
            api_key="test-key",
            model="gpt-4o-mini",
            task_type="test",
        )
        assert client.api_key == "test-key"
        assert client.model == "gpt-4o-mini"
        assert client.task_type == "test"
    
    def test_openai_client_has_complete_async(self):
        """Test that OpenAI client has complete_async method."""
        client = AsyncOpenAILLMClient(
            api_key="test-key",
            model="gpt-4o-mini",
        )
        assert hasattr(client, 'complete_async')
        assert asyncio.iscoroutinefunction(client.complete_async)
    
    def test_openai_client_has_complete_json_async(self):
        """Test that OpenAI client has complete_json_async method."""
        client = AsyncOpenAILLMClient(
            api_key="test-key",
            model="gpt-4o-mini",
        )
        assert hasattr(client, 'complete_json_async')
        assert asyncio.iscoroutinefunction(client.complete_json_async)
    
    def test_openai_client_has_sync_wrapper(self):
        """Test that OpenAI client has sync complete wrapper."""
        client = AsyncOpenAILLMClient(
            api_key="test-key",
            model="gpt-4o-mini",
        )
        assert hasattr(client, 'complete')
        # complete() is a sync wrapper, not a coroutine
        assert not asyncio.iscoroutinefunction(client.complete)
    
    def test_anthropic_client_instantiation(self):
        """Test that Anthropic async client can be instantiated."""
        client = AsyncAnthropicLLMClient(
            api_key="test-key",
            model="claude-3-5-sonnet-20241022",
            task_type="test",
        )
        assert client.api_key == "test-key"
        assert client.model == "claude-3-5-sonnet-20241022"
        assert client.task_type == "test"


class TestAsyncClientFactory:
    """Test the async client factory function."""
    
    def setup_method(self):
        """Reset client cache before each test."""
        reset_async_client_cache()
    
    @patch.dict('os.environ', {
        'OPENAI_API_KEY': 'test-openai-key',
        'USE_MOCK_LLM': 'false',
    })
    def test_get_async_llm_client_returns_client(self):
        """Test that factory returns a client."""
        client = get_async_llm_client("codegen")
        assert client is not None
    
    @patch.dict('os.environ', {
        'OPENAI_API_KEY': 'test-openai-key',
        'USE_MOCK_LLM': 'false',
    })
    def test_get_async_llm_client_caching(self):
        """Test that factory caches clients by task type."""
        reset_async_client_cache()
        client1 = get_async_llm_client("codegen")
        client2 = get_async_llm_client("codegen")
        assert client1 is client2  # Same instance
    
    @patch.dict('os.environ', {
        'OPENAI_API_KEY': 'test-openai-key',
        'USE_MOCK_LLM': 'false',
    })
    def test_get_async_llm_client_different_types(self):
        """Test that different task types get different clients."""
        reset_async_client_cache()
        client1 = get_async_llm_client("codegen")
        client2 = get_async_llm_client("planning")
        # May be same or different depending on config, but should not error
        assert client1 is not None
        assert client2 is not None
    
    @patch.dict('os.environ', {'USE_MOCK_LLM': 'true'})
    def test_mock_mode_returns_mock_client(self):
        """Test that mock mode returns a mock client."""
        reset_async_client_cache()
        client = get_async_llm_client("test")
        # Should work even in mock mode
        assert client is not None


class TestResetCache:
    """Test cache reset functionality."""
    
    @patch.dict('os.environ', {
        'OPENAI_API_KEY': 'test-key',
        'USE_MOCK_LLM': 'false',
    })
    def test_reset_clears_cache(self):
        """Test that reset_async_client_cache clears cached clients."""
        client1 = get_async_llm_client("codegen")
        reset_async_client_cache()
        client2 = get_async_llm_client("codegen")
        # After reset, should be a new instance
        assert client1 is not client2


class TestConcurrentLLMCalls:
    """Test the concurrent LLM call utility."""
    
    def test_run_concurrent_llm_calls_interface(self):
        """Test that run_concurrent_llm_calls is a coroutine function."""
        assert asyncio.iscoroutinefunction(run_concurrent_llm_calls)
    
    @pytest.mark.asyncio
    @patch.dict('os.environ', {'USE_MOCK_LLM': 'true'})
    async def test_run_concurrent_empty_list(self):
        """Test concurrent calls with empty list."""
        results = await run_concurrent_llm_calls([])
        assert results == []
    
    @pytest.mark.asyncio
    @patch('integration_coworker.llm.async_client.call_llm_async_for_node')
    async def test_run_concurrent_multiple_calls(self, mock_call_llm):
        """Test that concurrent calls invoke the async LLM function correctly."""
        # Create a mock async function
        mock_call_llm.return_value = "Mock response"
        
        calls = [
            ("Prompt 1", "codegen", None),
            ("Prompt 2", "codegen", None),
            ("Prompt 3", "codegen", None),
        ]
        
        results = await run_concurrent_llm_calls(calls)
        
        assert len(results) == 3
        assert all(r == "Mock response" for r in results)
        assert mock_call_llm.call_count == 3


@pytest.mark.asyncio
@patch.dict('os.environ', {'USE_MOCK_LLM': 'true'})
async def test_call_llm_async_smoke():
    from integration_coworker.llm.async_client import call_llm_async

    result = await call_llm_async("hello async")

    assert isinstance(result, str)


class TestAsyncClientDefaults:
    """Test default values and configuration."""
    
    def test_openai_default_temperature(self):
        """Test OpenAI client has correct default temperature."""
        client = AsyncOpenAILLMClient(api_key="test")
        assert client.default_temperature == 0.7
    
    def test_openai_default_max_tokens(self):
        """Test OpenAI client has correct default max_tokens."""
        client = AsyncOpenAILLMClient(api_key="test")
        assert client.default_max_tokens == 2000
    
    def test_anthropic_default_temperature(self):
        """Test Anthropic client has correct default temperature."""
        client = AsyncAnthropicLLMClient(api_key="test")
        assert client.default_temperature == 0.7
    
    def test_anthropic_default_max_tokens(self):
        """Test Anthropic client has correct default max_tokens."""
        client = AsyncAnthropicLLMClient(api_key="test")
        assert client.default_max_tokens == 2000
