"""
LLM Client Implementation

Provides unified LLM client for integration coworker nodes.
Supports real OpenAI calls and mock fallback for testing.
"""
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from integration_coworker.config import get_llm_config, get_settings

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    """Protocol for LLM clients."""
    
    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate a completion for the given prompt."""
        ...
    
    def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate a completion and parse as JSON."""
        ...


@dataclass
class MockLLMClient:
    """
    Mock LLM client for testing.
    
    Returns deterministic responses based on prompt keywords.
    Used when USE_MOCK_LLM=true or OPENAI_API_KEY not set.
    """
    
    task_type: str = "default"
    
    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Return a mock response based on prompt keywords."""
        prompt_lower = prompt.lower()
        
        # Task understanding / extraction
        if "extract" in prompt_lower or "understand" in prompt_lower:
            return self._mock_extraction_response(prompt)
        
        # Code generation
        if "generate" in prompt_lower and ("code" in prompt_lower or "function" in prompt_lower):
            return self._mock_code_response(prompt)
        
        # Planning / flow design
        if "plan" in prompt_lower or "workflow" in prompt_lower or "flow" in prompt_lower:
            return self._mock_planning_response(prompt)
        
        # Default response
        return f"Mock response for: {prompt[:100]}..."
    
    def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Return a mock JSON response."""
        prompt_lower = prompt.lower()
        
        # Task understanding - return structured response
        if "task" in prompt_lower and ("understand" in prompt_lower or "analyze" in prompt_lower or "integration task" in prompt_lower):
            # Extract task_slug from prompt keywords
            task_slug = "mock_task"
            if "checkout" in prompt_lower and "session" in prompt_lower:
                task_slug = "create_checkout_session"
            elif "payment" in prompt_lower:
                task_slug = "process_payment"
            elif "customer" in prompt_lower:
                task_slug = "manage_customer"
            
            return {
                "task_slug": task_slug,
                "input_entities": [],
                "output_entities": ["CheckoutSession"] if "checkout" in prompt_lower else [],
                "constraints": {
                    "idempotency_required": "create" in prompt_lower,
                    "requires_webhooks": "webhook" in prompt_lower,
                },
                "target_operations": [],
            }
        
        # Workflow planning - return structured flow
        if "workflow" in prompt_lower or "flow" in prompt_lower or "plan" in prompt_lower:
            return {
                "nodes": [
                    {"node_key": "start", "node_type": "start", "label": "Start", "position": 0},
                    {"node_key": "validate_input", "node_type": "validation", "label": "Validate Input", "position": 1},
                    {"node_key": "call_api", "node_type": "api_call", "label": "API Call", "position": 2},
                    {"node_key": "transform_response", "node_type": "transform", "label": "Transform", "position": 3},
                    {"node_key": "end", "node_type": "end", "label": "End", "position": 4},
                ],
                "edges": [
                    {"from_node_key": "start", "to_node_key": "validate_input"},
                    {"from_node_key": "validate_input", "to_node_key": "call_api"},
                    {"from_node_key": "call_api", "to_node_key": "transform_response"},
                    {"from_node_key": "transform_response", "to_node_key": "end"},
                ],
            }
        
        # Code generation - return mock code structure
        if "code" in prompt_lower or "generate" in prompt_lower:
            return {
                "code": "# Mock generated code\ndef mock_function():\n    pass",
                "language": "python",
                "dependencies": [],
            }
        
        return {"mock": True, "prompt_preview": prompt[:100]}
    
    def _mock_extraction_response(self, prompt: str) -> str:
        """Mock response for extraction tasks."""
        return json.dumps({
            "task_type": "api_integration",
            "entities": ["CheckoutSession", "Payment"],
            "operations": ["createCheckoutSession"],
        })
    
    def _mock_code_response(self, prompt: str) -> str:
        """Mock response for code generation."""
        return '''def mock_function():
    """Mock generated function."""
    return {"status": "success"}
'''
    
    def _mock_planning_response(self, prompt: str) -> str:
        """Mock response for planning tasks."""
        return json.dumps({
            "workflow_steps": [
                "validate_input",
                "call_api",
                "transform_response",
                "return_result",
            ],
        })


@dataclass
class OpenAILLMClient:
    """
    Real OpenAI LLM client.
    
    Uses the OpenAI API (or Azure OpenAI) for completions.
    """
    
    api_key: str
    model: str = "gpt-4"
    base_url: Optional[str] = None
    default_temperature: float = 0.7
    default_max_tokens: int = 2000
    
    _client: Optional[Any] = None
    
    def _get_client(self):
        """Lazy-initialize the OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI
                
                kwargs = {"api_key": self.api_key}
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                
                self._client = OpenAI(**kwargs)
            except ImportError:
                raise ImportError("openai package required for real LLM calls. Install with: pip install openai")
        
        return self._client
    
    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate a completion using OpenAI API."""
        client = self._get_client()
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature or self.default_temperature,
                max_tokens=max_tokens or self.default_max_tokens,
            )
            
            return response.choices[0].message.content or ""
            
        except Exception as e:
            logger.error(f"OpenAI API call failed: {e}")
            raise
    
    def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate a completion and parse as JSON."""
        # Add JSON instruction to system prompt
        json_system = (system_prompt or "") + "\n\nRespond only with valid JSON, no markdown formatting."
        
        response = self.complete(
            prompt=prompt,
            system_prompt=json_system.strip(),
            temperature=temperature or 0.3,  # Lower temp for structured output
            max_tokens=max_tokens,
        )
        
        # Try to extract JSON from response
        content = response.strip()
        
        # Remove markdown code blocks if present
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first and last lines (code block markers)
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"Raw response: {response}")
            # Return a structured error
            return {"error": "Failed to parse response", "raw": response[:500]}


# Cache for client instances
_client_cache: Dict[str, LLMClient] = {}


def get_llm_client(task_type: str = "default", strict: bool = False) -> LLMClient:
    """
    Get an LLM client for the specified task type.
    
    Uses config from get_llm_config() and respects USE_MOCK_LLM setting.
    
    Args:
        task_type: Type of task (e.g., "planning", "extraction", "codegen")
        strict: If True, raise an error when API key is missing instead of falling back to mock.
                Use this for production/demo runs where real LLM is required.
    
    Returns:
        LLMClient instance (real OpenAI or mock)
        
    Raises:
        RuntimeError: If strict=True and OPENAI_API_KEY is not set
    """
    if task_type in _client_cache:
        return _client_cache[task_type]
    
    config = get_llm_config(task_type)
    settings = get_settings()
    
    # Check if mock mode is explicitly requested
    use_mock = config.get("use_mock", False)
    has_api_key = bool(config.get("api_key"))
    
    if use_mock:
        logger.info(f"Using mock LLM client for task_type={task_type} (USE_MOCK_LLM=true)")
        client = MockLLMClient(task_type=task_type)
    elif has_api_key:
        logger.info(f"Using OpenAI LLM client for task_type={task_type}, model={config.get('model')}")
        client = OpenAILLMClient(
            api_key=config["api_key"],
            model=config.get("model", "gpt-4"),
            base_url=config.get("base_url"),
            default_temperature=config.get("temperature", 0.7),
            default_max_tokens=config.get("max_tokens", 2000),
        )
    elif strict:
        raise RuntimeError(
            "OPENAI_API_KEY is not set and USE_MOCK_LLM is not 'true'.\n"
            "For real LLM calls, set OPENAI_API_KEY in your environment or .env file.\n"
            "For testing without API key, set USE_MOCK_LLM=true."
        )
    else:
        # Fallback to mock with a warning
        logger.warning(
            f"No OPENAI_API_KEY set for task_type={task_type}, using mock client. "
            "Set OPENAI_API_KEY for real LLM calls or USE_MOCK_LLM=true to suppress this warning."
        )
        client = MockLLMClient(task_type=task_type)
    
    _client_cache[task_type] = client
    return client


def reset_client_cache() -> None:
    """Reset the client cache (for testing)."""
    global _client_cache
    _client_cache = {}


def is_mock_llm_mode() -> bool:
    """
    Check if we're running in mock LLM mode.
    
    Returns True if:
    - USE_MOCK_LLM is explicitly set to 'true', OR
    - OPENAI_API_KEY is not set (implicit fallback to mock)
    
    Useful for runtime warnings about mock mode in non-testing contexts.
    """
    config = get_llm_config("default")
    use_mock = config.get("use_mock", False)
    has_api_key = bool(config.get("api_key"))
    
    return use_mock or not has_api_key


def call_llm(
    prompt: str,
    task_type: str = "default",
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Convenience function to make an LLM call.
    
    Args:
        prompt: The user prompt
        task_type: Type of task for config lookup
        system_prompt: Optional system prompt
        temperature: Optional temperature override
        max_tokens: Optional max tokens override
    
    Returns:
        The LLM response text
    """
    client = get_llm_client(task_type)
    return client.complete(
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def call_llm_json(
    prompt: str,
    task_type: str = "default",
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Convenience function to make an LLM call and parse JSON response.
    
    Args:
        prompt: The user prompt
        task_type: Type of task for config lookup
        system_prompt: Optional system prompt
        temperature: Optional temperature override
        max_tokens: Optional max tokens override
    
    Returns:
        Parsed JSON dictionary
    """
    client = get_llm_client(task_type)
    return client.complete_json(
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )
