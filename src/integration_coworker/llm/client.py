"""
LLM Client Implementation

Provides unified LLM client for integration coworker nodes.
Uses LangChain for automatic LangSmith tracing.
Supports OpenAI, Anthropic, and mock fallback for testing.

Supported Providers:
- OpenAI: gpt-4, gpt-4o-mini, gpt-3.5-turbo, etc.
- Anthropic: claude-3-opus, claude-3-sonnet, claude-3-haiku, etc.

LangSmith Integration:
- All LLM calls are automatically traced when LANGCHAIN_TRACING_V2=true
- Traces include metadata: task_type, model, provider, temperature, run_id
- Parent-child relationships are maintained via run context
"""
import json
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Literal

from integration_coworker.config import get_llm_config, get_settings

logger = logging.getLogger(__name__)

# Supported LLM providers
LLMProvider = Literal["openai", "anthropic", "mock"]

# Context variable for current run_id (set by workflow runtime)
_current_run_id: ContextVar[Optional[str]] = ContextVar("current_run_id", default=None)
_current_provider: ContextVar[Optional[str]] = ContextVar("current_provider", default=None)


def set_run_context(run_id: str, provider_code: Optional[str] = None) -> None:
    """Set the current run context for LangSmith tracing."""
    _current_run_id.set(run_id)
    _current_provider.set(provider_code)


def get_run_context() -> tuple[Optional[str], Optional[str]]:
    """Get the current run context (run_id, provider_code)."""
    return _current_run_id.get(), _current_provider.get()


def clear_run_context() -> None:
    """Clear the run context."""
    _current_run_id.set(None)
    _current_provider.set(None)


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
    Real OpenAI LLM client with LangSmith tracing.
    
    Uses LangChain's ChatOpenAI for automatic LangSmith integration.
    All LLM calls are traced with metadata including task_type, model,
    run_id, and provider_code for easy debugging and analysis.
    """

    api_key: str
    model: str = "gpt-4"
    base_url: Optional[str] = None
    default_temperature: float = 0.7
    default_max_tokens: int = 2000
    task_type: str = "default"

    _llm: Optional[Any] = field(default=None, repr=False)

    def _get_llm(self, temperature: Optional[float] = None, max_tokens: Optional[int] = None):
        """Get or create the LangChain ChatOpenAI instance."""
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError(
                "langchain-openai package required for LLM calls with LangSmith tracing. "
                "Install with: pip install langchain-openai"
            )

        # Create a new instance with the requested parameters
        # (LangChain handles caching internally)
        kwargs = {
            "api_key": self.api_key,
            "model": self.model,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "max_tokens": max_tokens or self.default_max_tokens,
        }

        if self.base_url:
            kwargs["base_url"] = self.base_url

        return ChatOpenAI(**kwargs)

    def _build_metadata(self) -> Dict[str, Any]:
        """Build metadata for LangSmith tracing."""
        run_id, provider_code = get_run_context()
        metadata = {
            "task_type": self.task_type,
            "model": self.model,
        }
        if run_id:
            metadata["run_id"] = run_id
        if provider_code:
            metadata["provider_code"] = provider_code
        return metadata

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Generate a completion using LangChain ChatOpenAI.
        
        Automatically traced in LangSmith when LANGCHAIN_TRACING_V2=true.
        """
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError:
            raise ImportError(
                "langchain-core package required for LLM calls. "
                "Install with: pip install langchain-core"
            )

        llm = self._get_llm(temperature=temperature, max_tokens=max_tokens)

        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        metadata = self._build_metadata()

        try:
            # LangChain automatically handles LangSmith tracing
            response = llm.invoke(
                messages,
                config={
                    "metadata": metadata,
                    "tags": [f"task:{self.task_type}", f"model:{self.model}"],
                }
            )

            return response.content or ""

        except Exception as e:
            logger.error(f"LLM API call failed: {e}")
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
            temperature=temperature if temperature is not None else 0.3,  # Lower temp for structured output
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


@dataclass
class AnthropicLLMClient:
    """
    Anthropic Claude LLM client with LangSmith tracing.
    
    Uses LangChain's ChatAnthropic for automatic LangSmith integration.
    Supports Claude 3 models (Opus, Sonnet, Haiku).
    """

    api_key: str
    model: str = "claude-3-sonnet-20240229"
    default_temperature: float = 0.7
    default_max_tokens: int = 2000
    task_type: str = "default"

    _llm: Optional[Any] = field(default=None, repr=False)

    def _get_llm(self, temperature: Optional[float] = None, max_tokens: Optional[int] = None):
        """Get or create the LangChain ChatAnthropic instance."""
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError(
                "langchain-anthropic package required for Anthropic LLM calls. "
                "Install with: pip install langchain-anthropic"
            )

        kwargs = {
            "api_key": self.api_key,
            "model": self.model,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "max_tokens": max_tokens or self.default_max_tokens,
        }

        return ChatAnthropic(**kwargs)

    def _build_metadata(self) -> Dict[str, Any]:
        """Build metadata for LangSmith tracing."""
        run_id, provider_code = get_run_context()
        metadata = {
            "task_type": self.task_type,
            "model": self.model,
            "provider": "anthropic",
        }
        if run_id:
            metadata["run_id"] = run_id
        if provider_code:
            metadata["provider_code"] = provider_code
        return metadata

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Generate a completion using LangChain ChatAnthropic.
        
        Automatically traced in LangSmith when LANGCHAIN_TRACING_V2=true.
        """
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError:
            raise ImportError(
                "langchain-core package required for LLM calls. "
                "Install with: pip install langchain-core"
            )

        llm = self._get_llm(temperature=temperature, max_tokens=max_tokens)

        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        metadata = self._build_metadata()

        try:
            response = llm.invoke(
                messages,
                config={
                    "metadata": metadata,
                    "tags": [f"task:{self.task_type}", f"model:{self.model}", "provider:anthropic"],
                }
            )

            return response.content or ""

        except Exception as e:
            logger.error(f"Anthropic API call failed: {e}")
            raise

    def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate a completion and parse as JSON."""
        json_system = (system_prompt or "") + "\n\nRespond only with valid JSON, no markdown formatting."

        response = self.complete(
            prompt=prompt,
            system_prompt=json_system.strip(),
            temperature=temperature if temperature is not None else 0.3,
            max_tokens=max_tokens,
        )

        content = response.strip()

        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Anthropic response as JSON: {e}")
            logger.debug(f"Raw response: {response}")
            return {"error": "Failed to parse response", "raw": response[:500]}


# Cache for client instances
_client_cache: Dict[str, LLMClient] = {}


def get_llm_client(
    task_type: str = "default",
    strict: bool = False,
    provider: Optional[LLMProvider] = None,
) -> LLMClient:
    """
    Get an LLM client for the specified task type.
    
    Uses config from get_llm_config() and respects USE_MOCK_LLM setting.
    Supports multiple providers: OpenAI, Anthropic, and mock.
    
    Args:
        task_type: Type of task (e.g., "planning", "extraction", "codegen")
        strict: If True, raise an error when API key is missing instead of falling back to mock.
                Use this for production/demo runs where real LLM is required.
        provider: Optional provider override. If not specified, uses config or defaults to OpenAI.
                  Values: "openai", "anthropic", "mock"
    
    Returns:
        LLMClient instance (OpenAI, Anthropic, or mock)
        
    Raises:
        RuntimeError: If strict=True and required API key is not set
    """
    # Cache key includes provider to allow different clients per task/provider combo
    cache_key = f"{task_type}:{provider or 'default'}"
    if cache_key in _client_cache:
        return _client_cache[cache_key]

    config = get_llm_config(task_type)
    settings = get_settings()

    # Determine provider from explicit arg, config, or default
    effective_provider = provider or config.get("provider", "openai")

    # Check if mock mode is explicitly requested
    use_mock = config.get("use_mock", False) or effective_provider == "mock"

    if use_mock:
        logger.info(f"Using mock LLM client for task_type={task_type} (USE_MOCK_LLM=true)")
        client = MockLLMClient(task_type=task_type)
    elif effective_provider == "anthropic":
        # Anthropic provider
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            if strict:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set and provider=anthropic was requested.\n"
                    "Set ANTHROPIC_API_KEY in your environment or .env file."
                )
            else:
                logger.warning(
                    f"No ANTHROPIC_API_KEY set for task_type={task_type}, falling back to mock. "
                    "Set ANTHROPIC_API_KEY for real Anthropic calls."
                )
                client = MockLLMClient(task_type=task_type)
        else:
            logger.info(f"Using LangChain ChatAnthropic client for task_type={task_type}, model={config.get('model', 'claude-3-sonnet-20240229')}")
            client = AnthropicLLMClient(
                api_key=anthropic_key,
                model=config.get("model", "claude-3-sonnet-20240229"),
                default_temperature=config.get("temperature", 0.7),
                default_max_tokens=config.get("max_tokens", 2000),
                task_type=task_type,
            )
    else:
        # OpenAI provider (default)
        openai_key = config.get("api_key", "")
        if openai_key:
            logger.info(f"Using LangChain ChatOpenAI client for task_type={task_type}, model={config.get('model')}")
            client = OpenAILLMClient(
                api_key=openai_key,
                model=config.get("model", "gpt-4"),
                base_url=config.get("base_url"),
                default_temperature=config.get("temperature", 0.7),
                default_max_tokens=config.get("max_tokens", 2000),
                task_type=task_type,
            )
        elif strict:
            raise RuntimeError(
                "OPENAI_API_KEY is not set and USE_MOCK_LLM is not 'true'.\n"
                "For real LLM calls, set OPENAI_API_KEY in your environment or .env file.\n"
                "For testing without API key, set USE_MOCK_LLM=true."
            )
        else:
            logger.warning(
                f"No OPENAI_API_KEY set for task_type={task_type}, using mock client. "
                "Set OPENAI_API_KEY for real LLM calls or USE_MOCK_LLM=true to suppress this warning."
            )
            client = MockLLMClient(task_type=task_type)

    _client_cache[cache_key] = client
    return client


def get_llm_client_for_archetype(archetype_config: Dict[str, Any], strict: bool = False) -> LLMClient:
    """
    Get an LLM client based on archetype configuration.
    
    This is the primary entry point when using YAML archetype files.
    Reads provider and model settings from the archetype's model section.
    
    Args:
        archetype_config: Loaded archetype configuration dict
        strict: If True, raise error when API key is missing
        
    Returns:
        LLMClient configured according to the archetype
    """
    model_config = archetype_config.get("model", {})
    provider = model_config.get("provider", "openai")
    model_name = model_config.get("name")  # e.g., "claude-3-sonnet-20240229" or "gpt-4o-mini"
    temperature = model_config.get("temperature", 0.7)
    max_tokens = model_config.get("max_tokens", 2000)
    task_type = archetype_config.get("role", "default")

    # Check for mock mode
    use_mock = os.getenv("USE_MOCK_LLM", "").lower() == "true" or provider == "mock"

    if use_mock:
        logger.info(f"Using mock LLM client for archetype task_type={task_type} (USE_MOCK_LLM=true)")
        return MockLLMClient(task_type=task_type)

    if provider == "anthropic":
        # Anthropic provider - use archetype model config
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            if strict:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set and provider=anthropic was requested.\n"
                    "Set ANTHROPIC_API_KEY in your environment or .env file."
                )
            else:
                logger.warning(
                    f"No ANTHROPIC_API_KEY set for archetype task_type={task_type}, falling back to mock. "
                    "Set ANTHROPIC_API_KEY for real Anthropic calls."
                )
                return MockLLMClient(task_type=task_type)

        effective_model = model_name or "claude-3-sonnet-20240229"
        logger.info(f"Using LangChain ChatAnthropic client for archetype task_type={task_type}, model={effective_model}")
        return AnthropicLLMClient(
            api_key=anthropic_key,
            model=effective_model,
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )
    else:
        # OpenAI provider (default) - use archetype model config
        openai_key = os.getenv("OPENAI_API_KEY", "")
        if not openai_key:
            if strict:
                raise RuntimeError(
                    "OPENAI_API_KEY is not set.\n"
                    "For real LLM calls, set OPENAI_API_KEY in your environment or .env file.\n"
                    "For testing without API key, set USE_MOCK_LLM=true."
                )
            else:
                logger.warning(
                    f"No OPENAI_API_KEY set for archetype task_type={task_type}, using mock client."
                )
                return MockLLMClient(task_type=task_type)

        effective_model = model_name or "gpt-4"
        logger.info(f"Using LangChain ChatOpenAI client for archetype task_type={task_type}, model={effective_model}")
        return OpenAILLMClient(
            api_key=openai_key,
            model=effective_model,
            base_url=os.getenv("LLM_BASE_URL"),
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )


def get_llm_client_for_node(node_name: str, strict: bool = False) -> LLMClient:
    """
    Get an LLM client configured for a specific LangGraph node.
    
    This is the recommended entry point for nodes. It loads the archetype
    YAML for the given node name and returns a properly configured client.
    
    Args:
        node_name: Name of the LangGraph node (e.g., "understand_task", "generate_code_and_tests")
        strict: If True, raise error when API key is missing
        
    Returns:
        LLMClient configured according to the node's archetype
        
    Example:
        client = get_llm_client_for_node("understand_task")
        response = client.complete(prompt)
    """
    from integration_coworker.config import load_archetype

    archetype = load_archetype(node_name)
    return get_llm_client_for_archetype(archetype, strict=strict)


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
    
    .. deprecated:: 1.0.0
        Use :func:`call_llm` with TOON format instead for 30-40% token savings.
        Import ``from_toon`` from ``integration_coworker.llm.toon`` to parse responses.
        
        Example migration::
        
            # Before (deprecated):
            response = call_llm_json(prompt, task_type="planning")
            
            # After (recommended):
            from integration_coworker.llm.toon import from_toon
            response_text = call_llm(toon_prompt, task_type="planning")
            response = from_toon(response_text)
    
    Args:
        prompt: The user prompt
        task_type: Type of task for config lookup
        system_prompt: Optional system prompt
        temperature: Optional temperature override
        max_tokens: Optional max tokens override
    
    Returns:
        Parsed JSON dictionary
    """
    import warnings
    warnings.warn(
        "call_llm_json is deprecated. Use call_llm with TOON format instead "
        "for 30-40% token savings. See integration_coworker.llm.toon module.",
        DeprecationWarning,
        stacklevel=2,
    )
    client = get_llm_client(task_type)
    return client.complete_json(
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )
