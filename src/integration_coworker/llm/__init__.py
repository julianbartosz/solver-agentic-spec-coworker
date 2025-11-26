"""
LLM Client Module

Centralized LLM client utilities for the integration coworker.

Per design doc Section 4.3, this module:
- Provides a unified interface for LLM calls
- Supports OpenAI/Azure OpenAI APIs
- Falls back to mock responses when USE_MOCK_LLM=true or no API key
- Integrates with the config module for settings

Usage:
    from integration_coworker.llm import get_llm_client, call_llm
    
    # Get a client for a specific task type
    client = get_llm_client("codegen")
    response = client.complete("Generate a function that...")
    
    # Or use the simplified helper
    response = call_llm("Generate a function that...", task_type="codegen")
"""

from .client import (
    LLMClient,
    get_llm_client,
    call_llm,
    call_llm_json,
    is_mock_llm_mode,
    MockLLMClient,
)

__all__ = [
    "LLMClient",
    "get_llm_client", 
    "call_llm",
    "call_llm_json",
    "is_mock_llm_mode",
    "MockLLMClient",
]
