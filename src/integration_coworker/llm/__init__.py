"""
LLM Client Module

Centralized LLM client utilities for the integration coworker.

Per design doc Section 4.3, this module:
- Provides a unified interface for LLM calls
- Supports OpenAI, Anthropic, and Google Gemini APIs
- Falls back to mock responses when USE_MOCK_LLM=true or no API key
- Uses archetype YAML files for per-node model configuration
- Integrates with LangChain for automatic LangSmith tracing

RECOMMENDED: Async API (V3.1+)
===============================
All LLM-calling graph nodes now use async functions for better concurrency.
Use the async API for new code:

    from integration_coworker.llm import call_llm_async_for_node, get_async_llm_client_for_node
    
    async def my_async_node(state):
        response = await call_llm_async_for_node("understand_task", "Parse this task...")
        return state
    
    # Concurrent calls with archetypes
    import asyncio
    results = await asyncio.gather(
        call_llm_async_for_node("understand_task", prompt1),
        call_llm_async_for_node("generate_code_and_tests", prompt2),
    )

DEPRECATED: Sync API
====================
The synchronous functions (call_llm_for_node, get_llm_client_for_node) are deprecated
and will emit warnings. They now wrap the async versions internally.

    # DEPRECATED - will emit DeprecationWarning
    from integration_coworker.llm import call_llm_for_node
    response = call_llm_for_node("understand_task", "Parse this task...")

For structured data, use TOON format instead of JSON for 30-40% token savings:
    from integration_coworker.llm import call_llm_async_for_node
    from integration_coworker.llm.toon import from_toon
    
    response_text = await call_llm_async_for_node("plan_integration_flow", toon_prompt)
    response = from_toon(response_text)

Archetypes are defined in config/archetypes/*.archetype.yaml and specify:
- Model provider and name (e.g., openai/gpt-4o, anthropic/claude-sonnet-4-5)
- Temperature and max_tokens
- Prompting strategy
- Retrieval settings
"""

from .client import (
    LLMClient,
    get_llm_client,
    get_llm_client_for_node,
    call_llm_for_node,
    is_mock_llm_mode,
    MockLLMClient,
    set_run_context,
    get_run_context,
    clear_run_context,
)

from .async_client import (
    AsyncLLMClient,
    AsyncOpenAILLMClient,
    AsyncAnthropicLLMClient,
    AsyncGoogleLLMClient,
    get_async_llm_client,
    get_async_llm_client_for_node,
    call_llm_async_for_node,
    run_concurrent_llm_calls,
    reset_async_client_cache,
)

from .exceptions import (
    LLMError,
    LLMAuthError,
    LLMRateLimitError,
    LLMTransientError,
    LLMContentFilterError,
    LLMContextLengthError,
    LLMBudgetExceededError,
    classify_llm_exception,
)

from .client import (
    get_llm_call_count,
    init_token_usage,
    get_token_usage,
)

__all__ = [
    # Sync clients
    "LLMClient",
    "get_llm_client",
    "get_llm_client_for_node",
    "call_llm_for_node",
    "is_mock_llm_mode",
    "MockLLMClient",
    "set_run_context",
    "get_run_context",
    "clear_run_context",
    # Budget/observability (V4 Production Safety)
    "get_llm_call_count",
    "init_token_usage",
    "get_token_usage",
    # Async clients (Bug #35 fix)
    "AsyncLLMClient",
    "AsyncOpenAILLMClient",
    "AsyncAnthropicLLMClient",
    "AsyncGoogleLLMClient",
    "get_async_llm_client",
    "get_async_llm_client_for_node",
    "call_llm_async_for_node",
    "run_concurrent_llm_calls",
    "reset_async_client_cache",
    # Exceptions (Production Readiness v4)
    "LLMError",
    "LLMAuthError",
    "LLMRateLimitError",
    "LLMTransientError",
    "LLMContentFilterError",
    "LLMContextLengthError",
    "LLMBudgetExceededError",
    "classify_llm_exception",
]
