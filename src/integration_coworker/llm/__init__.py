"""
LLM Client Module

Centralized LLM client utilities for the integration coworker.

Per design doc Section 4.3, this module:
- Provides a unified interface for LLM calls
- Supports OpenAI/Azure OpenAI APIs
- Falls back to mock responses when USE_MOCK_LLM=true or no API key
- Integrates with the config module for settings
- Uses LangChain for automatic LangSmith tracing

Usage:
    from integration_coworker.llm import get_llm_client, call_llm
    
    # Get a client for a specific task type
    client = get_llm_client("codegen")
    response = client.complete("Generate a function that...")
    
    # Or use the simplified helper
    response = call_llm("Generate a function that...", task_type="codegen")

For structured data, use TOON format instead of JSON for 30-40% token savings:
    from integration_coworker.llm import call_llm
    from integration_coworker.llm.toon import from_toon
    
    response_text = call_llm(toon_prompt, task_type="planning")
    response = from_toon(response_text)
"""

from .client import (
    LLMClient,
    get_llm_client,
    get_llm_client_for_node,
    call_llm,
    call_llm_json,  # Deprecated: use call_llm + TOON instead
    is_mock_llm_mode,
    MockLLMClient,
    set_run_context,
    get_run_context,
    clear_run_context,
)

__all__ = [
    "LLMClient",
    "get_llm_client",
    "get_llm_client_for_node",
    "call_llm",
    "call_llm_json",  # Deprecated
    "is_mock_llm_mode",
    "MockLLMClient",
    "set_run_context",
    "get_run_context",
    "clear_run_context",
]
