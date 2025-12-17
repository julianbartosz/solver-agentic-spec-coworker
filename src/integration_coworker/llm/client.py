"""
LLM Client Implementation

Provides unified LLM client for integration coworker nodes.
Uses LangChain for automatic LangSmith tracing.
Supports OpenAI, Anthropic, Google Gemini, and mock fallback for testing.

Supported Providers:
- OpenAI: gpt-4o, gpt-4o-mini, o1, o1-mini, etc.
- Anthropic: claude-sonnet-4-5, claude-3-5-sonnet, etc.
- Google: gemini-2.5-flash, gemini-2.5-pro, gemini-2.0-flash, etc. (large context windows)

Model Configuration:
Models are configured per-node via archetype YAML files in config/archetypes/.
This allows swapping providers/models without code changes.

LLM Modes (LLM-003):
- REAL: Call OpenAI/Anthropic/Google APIs
- MOCK: Return deterministic static strings
- RECORD: Call Real APIs, save request/response to disk
- REPLAY: Read from disk, fail if missing

LangSmith Integration:
- All LLM calls are automatically traced when LANGCHAIN_TRACING_V2=true
- Traces include metadata: task_type, model, provider, temperature, run_id
- Parent-child relationships are maintained via run context

v2 Security (SEC-001):
- All system prompts are hardened with safety preamble via harden_system_prompt()
- Protects against prompt injection attacks in user-provided content

Redis Cache (Plan 7):
- LLM responses are cached in Redis when LLM_CACHE_ENABLED=true
- Cache key: llm:<provider>:<model>:<task_type>:<sha256(prompt)>
- Default TTL: 24 hours (configurable via LLM_CACHE_TTL)
- Graceful degradation if Redis unavailable

Bug #24 Fix: Added automatic retry with exponential backoff for rate limits (429)
and transient API errors. Retries up to 3 times with 1-10 second waits.
"""
import hashlib
import json
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Literal, Callable, TypeVar

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from integration_coworker.config import get_settings
from integration_coworker.config.llm_mode import LLMMode, get_llm_mode
from integration_coworker.llm.safety import harden_system_prompt
from integration_coworker.llm.cache import get_llm_cache

logger = logging.getLogger(__name__)

# Type variable for retry wrapper
T = TypeVar('T')

# Supported LLM providers
LLMProvider = Literal["openai", "anthropic", "google", "mock"]


def _is_retryable_error(exc: Exception) -> bool:
    """
    Determine if an exception is retryable (rate limit, transient error).
    
    Bug #24 Fix: Check for rate limit (429), server errors (5xx),
    and transient network issues.
    """
    error_str = str(exc).lower()
    
    # Rate limit errors (429)
    if "429" in error_str or "rate_limit" in error_str or "rate limit" in error_str:
        return True
    
    # Credit/quota errors should NOT be retried (400, not transient)
    if "credit" in error_str or "balance" in error_str or "quota" in error_str:
        return False
    
    # Server errors (5xx) are transient
    if any(code in error_str for code in ["500", "502", "503", "504"]):
        return True
    
    # Connection/timeout errors
    if any(term in error_str for term in ["timeout", "connection", "network"]):
        return True
    
    return False


def with_retry(fn: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to add retry logic with exponential backoff for LLM calls.
    
    Bug #24 Fix: Retries on rate limits (429), server errors (5xx),
    and transient network issues. Maximum 3 attempts with 1-10 second waits.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs) -> T:
        attempts = 0
        max_attempts = 3
        
        while attempts < max_attempts:
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                attempts += 1
                if attempts >= max_attempts or not _is_retryable_error(e):
                    raise
                
                # Calculate backoff: 2^attempt seconds, capped at 10
                wait_time = min(2 ** attempts, 10)
                logger.warning(
                    f"LLM call failed with retryable error (attempt {attempts}/{max_attempts}), "
                    f"retrying in {wait_time}s: {e}"
                )
                import time
                time.sleep(wait_time)
        
        # Should not reach here, but just in case
        raise RuntimeError("Retry logic error")
    
    return wrapper


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


# =============================================================================
# V4 Observability: Token Usage Tracking
# =============================================================================

# Aggregate token usage for current run
_token_usage: ContextVar[Dict[str, int]] = ContextVar(
    "token_usage",
    default=None
)


def init_token_usage() -> None:
    """Initialize token usage tracking for a run."""
    _token_usage.set({
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    })


def get_token_usage() -> Dict[str, int]:
    """Get aggregated token usage for the current run."""
    usage = _token_usage.get()
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return usage.copy()


def _track_token_usage(response) -> None:
    """
    Extract and aggregate token usage from LangChain response.
    
    LangChain AIMessage includes response_metadata with token counts.
    """
    usage = _token_usage.get()
    if usage is None:
        return  # Not tracking
    
    try:
        # Try response_metadata (newer LangChain)
        if hasattr(response, 'response_metadata') and response.response_metadata:
            token_info = response.response_metadata.get('token_usage', {})
            if not token_info:
                # OpenAI format
                token_info = response.response_metadata.get('usage', {})
            
            usage["prompt_tokens"] += token_info.get("prompt_tokens", 0)
            usage["completion_tokens"] += token_info.get("completion_tokens", 0)
            usage["total_tokens"] += token_info.get("total_tokens", 0)
        
        # Try usage_metadata (alternative)
        elif hasattr(response, 'usage_metadata') and response.usage_metadata:
            usage["prompt_tokens"] += getattr(response.usage_metadata, 'input_tokens', 0)
            usage["completion_tokens"] += getattr(response.usage_metadata, 'output_tokens', 0)
            usage["total_tokens"] += getattr(response.usage_metadata, 'total_tokens', 0)
            
    except Exception as e:
        logger.debug(f"Could not extract token usage: {e}")


# =============================================================================
# Record/Replay Support (LLM-003)
# =============================================================================

# Default directory for recorded interactions
_REPLAY_DIR = Path(__file__).parent.parent.parent.parent / ".llm_recordings"


def _get_interaction_key(prompt: str, system_prompt: Optional[str], model: str) -> str:
    """Generate a deterministic key for a prompt/model combination."""
    content = f"{model}|{system_prompt or ''}|{prompt}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _get_recording_path(key: str) -> Path:
    """Get the file path for a recorded interaction."""
    return _REPLAY_DIR / f"{key}.json"


def _save_interaction(
    prompt: str,
    system_prompt: Optional[str],
    model: str,
    response: str,
) -> None:
    """Save an LLM interaction to disk for later replay."""
    _REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    key = _get_interaction_key(prompt, system_prompt, model)
    path = _get_recording_path(key)
    
    data = {
        "model": model,
        "system_prompt": system_prompt,
        "prompt": prompt,
        "response": response,
    }
    
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    
    logger.debug(f"Recorded LLM interaction to {path}")


def _load_interaction(
    prompt: str,
    system_prompt: Optional[str],
    model: str,
) -> Optional[str]:
    """Load a previously recorded LLM interaction."""
    key = _get_interaction_key(prompt, system_prompt, model)
    path = _get_recording_path(key)
    
    if not path.exists():
        return None
    
    try:
        with open(path) as f:
            data = json.load(f)
        logger.debug(f"Replayed LLM interaction from {path}")
        return data.get("response")
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Failed to load recording {path}: {e}")
        return None


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

        # Task understanding - return structured response (check FIRST, before workflow)
        # V1.2: Made more specific to catch task analysis prompts that also mention "plan"
        if any(marker in prompt_lower for marker in [
            "analyze this integration task",
            "task_slug",
            "integration task",
            "understand",
            "target_operations",
        ]):
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

        # Workflow planning - return structured flow (nodes/edges)
        # Only match explicit workflow structure requests
        if ("workflow" in prompt_lower and "nodes" in prompt_lower) or "flow" in prompt_lower or "design a workflow" in prompt_lower:
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

        # V1.2: Fallback for unmatched prompts - include task_slug if it looks like
        # a task analysis prompt to prevent "missing task_slug" errors
        if "task" in prompt_lower or "api" in prompt_lower or "integration" in prompt_lower:
            return {
                "task_slug": "mock_fallback_task",
                "input_entities": [],
                "output_entities": [],
                "constraints": {},
                "target_operations": [],
                "mock": True,
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

    async def complete_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Async version of complete - returns same mock response."""
        return self.complete(prompt, system_prompt, temperature, max_tokens)

    async def complete_json_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Async version of complete_json - returns same mock response."""
        return self.complete_json(prompt, system_prompt, temperature, max_tokens)


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
        v2: System prompts are hardened with safety preamble (SEC-001).
        LLM-003: Supports RECORD/REPLAY modes for regression testing.
        Plan 7: Checks Redis cache before making API call.
        """
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError:
            raise ImportError(
                "langchain-core package required for LLM calls. "
                "Install with: pip install langchain-core"
            )

        # v2: Harden system prompt (SEC-001)
        hardened_system = harden_system_prompt(system_prompt)

        # LLM-003: Check for REPLAY mode first
        mode = get_llm_mode()
        if mode.should_replay:
            cached = _load_interaction(prompt, hardened_system, self.model)
            if cached is not None:
                return cached
            raise RuntimeError(
                f"REPLAY mode: No recorded interaction found for prompt. "
                f"Run with LLM_MODE=record first to capture interactions."
            )

        # Plan 7: Check Redis cache before API call
        cache = get_llm_cache()
        cached_response = cache.get("openai", self.model, self.task_type, prompt, hardened_system)
        if cached_response is not None:
            logger.debug(f"Cache hit for OpenAI {self.model}, task_type={self.task_type}")
            return cached_response

        llm = self._get_llm(temperature=temperature, max_tokens=max_tokens)

        messages = []
        messages.append(SystemMessage(content=hardened_system))
        messages.append(HumanMessage(content=prompt))

        metadata = self._build_metadata()

        def _invoke_llm():
            """Inner function for retry wrapper."""
            return llm.invoke(
                messages,
                config={
                    "metadata": metadata,
                    "tags": [f"task:{self.task_type}", f"model:{self.model}"],
                }
            )

        try:
            # Bug #24 Fix: Wrap LLM call with retry for rate limits and transient errors
            response = with_retry(_invoke_llm)()

            result = response.content or ""
            
            # V4 Observability: Track token usage from response metadata
            _track_token_usage(response)
            
            # LLM-003: Save interaction if in RECORD mode
            if mode.should_record:
                _save_interaction(prompt, hardened_system, self.model, result)

            # Plan 7: Cache the result
            cache.set("openai", self.model, self.task_type, prompt, hardened_system, result)

            return result

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
    Supports Claude models (Opus 4.5, Sonnet 4.5, etc.).
    """

    api_key: str
    model: str = "claude-sonnet-4-5-20250929"
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
        v2: System prompts are hardened with safety preamble (SEC-001).
        LLM-003: Supports RECORD/REPLAY modes for regression testing.
        Plan 7: Checks Redis cache before making API call.
        """
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError:
            raise ImportError(
                "langchain-core package required for LLM calls. "
                "Install with: pip install langchain-core"
            )

        # v2: Harden system prompt (SEC-001)
        hardened_system = harden_system_prompt(system_prompt)

        # LLM-003: Check for REPLAY mode first
        mode = get_llm_mode()
        if mode.should_replay:
            cached = _load_interaction(prompt, hardened_system, self.model)
            if cached is not None:
                return cached
            raise RuntimeError(
                f"REPLAY mode: No recorded interaction found for prompt. "
                f"Run with LLM_MODE=record first to capture interactions."
            )

        # Plan 7: Check Redis cache before API call
        cache = get_llm_cache()
        cached_response = cache.get("anthropic", self.model, self.task_type, prompt, hardened_system)
        if cached_response is not None:
            logger.debug(f"Cache hit for Anthropic {self.model}, task_type={self.task_type}")
            return cached_response

        llm = self._get_llm(temperature=temperature, max_tokens=max_tokens)

        messages = []
        messages.append(SystemMessage(content=hardened_system))
        messages.append(HumanMessage(content=prompt))

        metadata = self._build_metadata()

        def _invoke_llm():
            """Inner function for retry wrapper."""
            return llm.invoke(
                messages,
                config={
                    "metadata": metadata,
                    "tags": [f"task:{self.task_type}", f"model:{self.model}", "provider:anthropic"],
                }
            )

        try:
            # Bug #24 Fix: Wrap LLM call with retry for rate limits and transient errors
            response = with_retry(_invoke_llm)()

            result = response.content or ""
            
            # LLM-003: Save interaction if in RECORD mode
            if mode.should_record:
                _save_interaction(prompt, hardened_system, self.model, result)

            # Plan 7: Cache the result
            cache.set("anthropic", self.model, self.task_type, prompt, hardened_system, result)

            return result

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


@dataclass
class GoogleLLMClient:
    """
    Google Gemini LLM client with LangSmith tracing.
    
    Uses LangChain's ChatGoogleGenerativeAI for automatic LangSmith integration.
    Supports Gemini models (gemini-2.5-flash, gemini-2.5-pro, gemini-2.0-flash, etc.).
    
    Gemini models offer large context windows, making them ideal for 
    repo analysis and tasks requiring extensive context.
    """

    api_key: str
    model: str = "gemini-2.5-flash"
    default_temperature: float = 0.7
    default_max_tokens: int = 2000
    task_type: str = "default"

    _llm: Optional[Any] = field(default=None, repr=False)

    def _get_llm(self, temperature: Optional[float] = None, max_tokens: Optional[int] = None):
        """Get or create the LangChain ChatGoogleGenerativeAI instance."""
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError(
                "langchain-google-genai package required for Google Gemini LLM calls. "
                "Install with: pip install langchain-google-genai"
            )

        kwargs = {
            "google_api_key": self.api_key,
            "model": self.model,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "max_output_tokens": max_tokens or self.default_max_tokens,
        }

        return ChatGoogleGenerativeAI(**kwargs)

    def _build_metadata(self) -> Dict[str, Any]:
        """Build metadata for LangSmith tracing."""
        run_id, provider_code = get_run_context()
        metadata = {
            "task_type": self.task_type,
            "model": self.model,
            "provider": "google",
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
        Generate a completion using LangChain ChatGoogleGenerativeAI.
        
        Automatically traced in LangSmith when LANGCHAIN_TRACING_V2=true.
        v2: System prompts are hardened with safety preamble (SEC-001).
        LLM-003: Supports RECORD/REPLAY modes for regression testing.
        Plan 7: Checks Redis cache before making API call.
        """
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError:
            raise ImportError(
                "langchain-core package required for LLM calls. "
                "Install with: pip install langchain-core"
            )

        # v2: Harden system prompt (SEC-001)
        hardened_system = harden_system_prompt(system_prompt)

        # LLM-003: Check for REPLAY mode first
        mode = get_llm_mode()
        if mode.should_replay:
            cached = _load_interaction(prompt, hardened_system, self.model)
            if cached is not None:
                return cached
            raise RuntimeError(
                f"REPLAY mode: No recorded interaction found for prompt. "
                f"Run with LLM_MODE=record first to capture interactions."
            )

        # Plan 7: Check Redis cache before API call
        cache = get_llm_cache()
        cached_response = cache.get("google", self.model, self.task_type, prompt, hardened_system)
        if cached_response is not None:
            logger.debug(f"Cache hit for Google {self.model}, task_type={self.task_type}")
            return cached_response

        llm = self._get_llm(temperature=temperature, max_tokens=max_tokens)

        messages = []
        messages.append(SystemMessage(content=hardened_system))
        messages.append(HumanMessage(content=prompt))

        metadata = self._build_metadata()

        def _invoke_llm():
            """Inner function for retry wrapper."""
            return llm.invoke(
                messages,
                config={
                    "metadata": metadata,
                    "tags": [f"task:{self.task_type}", f"model:{self.model}", "provider:google"],
                }
            )

        try:
            # Bug #24 Fix: Wrap LLM call with retry for rate limits and transient errors
            response = with_retry(_invoke_llm)()

            result = response.content or ""
            
            # V4 Observability: Track token usage from response metadata
            _track_token_usage(response)
            
            # LLM-003: Save interaction if in RECORD mode
            if mode.should_record:
                _save_interaction(prompt, hardened_system, self.model, result)

            # Plan 7: Cache the result
            cache.set("google", self.model, self.task_type, prompt, hardened_system, result)

            return result

        except Exception as e:
            logger.error(f"Google Gemini API call failed: {e}")
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
            logger.warning(f"Failed to parse Google Gemini response as JSON: {e}")
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
    
    .. deprecated::
        Use :func:`get_llm_client_for_node` instead, which uses archetype YAML configs.
        This function is kept for backwards compatibility but no longer reads from models.yaml.
    
    Uses LLMMode from config to determine behavior (LLM-003):
    - REAL: Use real API clients
    - MOCK: Use MockLLMClient
    - RECORD: Use real API clients (recording handled in complete())
    - REPLAY: Use real API clients (replay handled in complete())
    
    Provider Fallback Chain (when primary provider API key is missing):
    1. Try the configured/requested provider
    2. Try OpenAI (if not already tried)
    3. Try Anthropic (if not already tried)
    4. Fall back to MockLLMClient
    
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
    # Get the current LLM mode (LLM-003)
    mode = get_llm_mode()
    
    # Cache key includes provider and mode to allow different clients
    cache_key = f"{task_type}:{provider or 'default'}:{mode.value}"
    if cache_key in _client_cache:
        return _client_cache[cache_key]

    # DEPRECATED: Previously called get_llm_config(task_type) to load from models.yaml.
    # Now uses defaults - prefer get_llm_client_for_node() for new code.
    config: Dict[str, Any] = {}

    # Determine provider from explicit arg, config, or default
    effective_provider = provider or config.get("provider", "openai")

    # LLM-003: Check mode instead of use_mock boolean
    # MOCK mode or mock provider → MockLLMClient
    # RECORD/REPLAY modes still need a real client (they wrap the calls)
    use_mock = mode.is_mock or effective_provider == "mock"

    if use_mock:
        logger.info(f"Using mock LLM client for task_type={task_type} (LLM_MODE={mode.value})")
        client = MockLLMClient(task_type=task_type)
        _client_cache[cache_key] = client
        return client

    # Build provider fallback chain based on configured provider
    if effective_provider == "anthropic":
        fallback_chain = ["anthropic", "openai"]
    else:
        fallback_chain = ["openai", "anthropic"]

    # Try each provider in order
    client: Optional[LLMClient] = None
    for try_provider in fallback_chain:
        client = _try_create_client_for_provider_from_config(
            try_provider,
            config=config,
            task_type=task_type,
            is_fallback=(try_provider != effective_provider),
        )
        if client is not None:
            break

    # All providers failed - use mock or raise
    if client is None:
        if strict:
            raise RuntimeError(
                "No LLM API keys configured.\n"
                "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY in your environment or .env file.\n"
                "For testing without API keys, set USE_MOCK_LLM=true."
            )
        
        logger.warning(
            f"No LLM API keys available for task_type={task_type}, using mock client. "
            "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY for real LLM calls."
        )
        client = MockLLMClient(task_type=task_type)

    _client_cache[cache_key] = client
    return client


def _try_create_client_for_provider_from_config(
    provider: str,
    config: Dict[str, Any],
    task_type: str,
    is_fallback: bool = False,
) -> Optional[LLMClient]:
    """
    Try to create an LLM client for a specific provider using config dict.
    
    Returns None if the provider's API key is not configured.
    """
    temperature = config.get("temperature", 0.7)
    max_tokens = config.get("max_tokens", 2000)
    
    if provider == "anthropic":
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            if is_fallback:
                logger.debug(f"Anthropic fallback skipped: ANTHROPIC_API_KEY not set")
            return None
        
        model = config.get("model", "claude-3-sonnet-20240229") if not is_fallback else "claude-3-sonnet-20240229"
        if is_fallback:
            logger.info(
                f"Primary provider unavailable, falling back to Anthropic for task_type={task_type}, "
                f"model={model}"
            )
        else:
            logger.info(f"Using LangChain ChatAnthropic client for task_type={task_type}, model={model}")
        
        return AnthropicLLMClient(
            api_key=anthropic_key,
            model=model,
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )
    
    elif provider == "openai":
        openai_key = config.get("api_key", "") or os.getenv("OPENAI_API_KEY", "")
        if not openai_key:
            if is_fallback:
                logger.debug(f"OpenAI fallback skipped: OPENAI_API_KEY not set")
            return None
        
        model = config.get("model", "gpt-4o") if not is_fallback else "gpt-4o"
        if is_fallback:
            logger.info(
                f"Primary provider unavailable, falling back to OpenAI for task_type={task_type}, "
                f"model={model}"
            )
        else:
            logger.info(f"Using LangChain ChatOpenAI client for task_type={task_type}, model={model}")
        
        return OpenAILLMClient(
            api_key=openai_key,
            model=model,
            base_url=config.get("base_url"),
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )
    
    elif provider == "google":
        google_key = os.getenv("GOOGLE_API_KEY", "")
        if not google_key:
            if is_fallback:
                logger.debug(f"Google fallback skipped: GOOGLE_API_KEY not set")
            return None
        
        model = config.get("model", "gemini-2.5-flash") if not is_fallback else "gemini-2.5-flash"
        if is_fallback:
            logger.info(
                f"Primary provider unavailable, falling back to Google Gemini for task_type={task_type}, "
                f"model={model}"
            )
        else:
            logger.info(f"Using LangChain ChatGoogleGenerativeAI client for task_type={task_type}, model={model}")
        
        return GoogleLLMClient(
            api_key=google_key,
            model=model,
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )
    
    return None


def get_llm_client_for_archetype(archetype_config: Dict[str, Any], strict: bool = False) -> LLMClient:
    """
    Get an LLM client based on archetype configuration.
    
    This is the primary entry point when using YAML archetype files.
    Reads provider and model settings from the archetype's model section.
    
    Provider Fallback Chain (when primary provider API key is missing):
    1. Try the configured provider (from archetype)
    2. Try OpenAI (if not already tried)
    3. Try Anthropic (if not already tried)
    4. Fall back to MockLLMClient
    
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

    # Check for explicit mock mode
    use_mock = os.getenv("USE_MOCK_LLM", "").lower() == "true" or provider == "mock"

    if use_mock:
        logger.info(f"Using mock LLM client for archetype task_type={task_type} (USE_MOCK_LLM=true)")
        return MockLLMClient(task_type=task_type)

    # Build provider fallback chain based on configured provider
    # Google is included for large-context tasks, but falls back to others if key not set
    if provider == "anthropic":
        fallback_chain = ["anthropic", "openai", "google"]
    elif provider == "google":
        fallback_chain = ["google", "openai", "anthropic"]
    else:
        fallback_chain = ["openai", "anthropic", "google"]

    # Try each provider in order
    for try_provider in fallback_chain:
        client = _try_create_client_for_provider(
            try_provider,
            model_name=model_name if try_provider == provider else None,  # Use archetype model only for primary
            temperature=temperature,
            max_tokens=max_tokens,
            task_type=task_type,
            is_fallback=(try_provider != provider),
        )
        if client is not None:
            return client

    # All providers failed - use mock or raise
    if strict:
        raise RuntimeError(
            "No LLM API keys configured.\n"
            "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY in your environment or .env file.\n"
            "For testing without API keys, set USE_MOCK_LLM=true."
        )
    
    logger.warning(
        f"No LLM API keys available for task_type={task_type}, using mock client. "
        "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY for real LLM calls."
    )
    return MockLLMClient(task_type=task_type)


def _try_create_client_for_provider(
    provider: str,
    model_name: Optional[str],
    temperature: float,
    max_tokens: int,
    task_type: str,
    is_fallback: bool = False,
) -> Optional[LLMClient]:
    """
    Try to create an LLM client for a specific provider.
    
    Returns None if the provider's API key is not configured.
    
    Args:
        provider: Provider name ("openai" or "anthropic")
        model_name: Optional model name override
        temperature: Temperature setting
        max_tokens: Max tokens setting
        task_type: Task type for logging
        is_fallback: Whether this is a fallback attempt (affects logging)
        
    Returns:
        LLMClient if successful, None if API key missing
    """
    if provider == "anthropic":
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            if is_fallback:
                logger.debug(f"Anthropic fallback skipped: ANTHROPIC_API_KEY not set")
            return None
        
        effective_model = model_name or "claude-sonnet-4-5-20250929"
        if is_fallback:
            logger.info(
                f"Primary provider unavailable, falling back to Anthropic for task_type={task_type}, "
                f"model={effective_model}"
            )
        else:
            logger.info(
                f"Using LangChain ChatAnthropic client for archetype task_type={task_type}, "
                f"model={effective_model}"
            )
        return AnthropicLLMClient(
            api_key=anthropic_key,
            model=effective_model,
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )
    
    elif provider == "openai":
        openai_key = os.getenv("OPENAI_API_KEY", "")
        if not openai_key:
            if is_fallback:
                logger.debug(f"OpenAI fallback skipped: OPENAI_API_KEY not set")
            return None
        
        effective_model = model_name or "gpt-4o"
        if is_fallback:
            logger.info(
                f"Primary provider unavailable, falling back to OpenAI for task_type={task_type}, "
                f"model={effective_model}"
            )
        else:
            logger.info(
                f"Using LangChain ChatOpenAI client for archetype task_type={task_type}, "
                f"model={effective_model}"
            )
        return OpenAILLMClient(
            api_key=openai_key,
            model=effective_model,
            base_url=os.getenv("LLM_BASE_URL"),
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )
    
    elif provider == "google":
        google_key = os.getenv("GOOGLE_API_KEY", "")
        if not google_key:
            if is_fallback:
                logger.debug(f"Google fallback skipped: GOOGLE_API_KEY not set")
            return None
        
        effective_model = model_name or "gemini-2.5-flash"
        if is_fallback:
            logger.info(
                f"Primary provider unavailable, falling back to Google Gemini for task_type={task_type}, "
                f"model={effective_model}"
            )
        else:
            logger.info(
                f"Using LangChain ChatGoogleGenerativeAI client for archetype task_type={task_type}, "
                f"model={effective_model}"
            )
        return GoogleLLMClient(
            api_key=google_key,
            model=effective_model,
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )
    
    else:
        logger.warning(f"Unknown provider '{provider}', skipping")
        return None


def get_llm_client_for_node(node_name: str, strict: bool = False) -> LLMClient:
    """
    Get an LLM client configured for a specific LangGraph node.
    
    .. deprecated:: 3.1
        Use :func:`get_async_llm_client_for_node` from `async_client` module instead.
        This function will be removed in a future version.
    
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
    import warnings
    warnings.warn(
        "get_llm_client_for_node is deprecated, use get_async_llm_client_for_node instead. "
        "This function will be removed in a future version.",
        DeprecationWarning,
        stacklevel=2,
    )
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
    - Neither OPENAI_API_KEY nor ANTHROPIC_API_KEY is set
    
    Useful for runtime warnings about mock mode in non-testing contexts.
    """
    use_mock = os.getenv("USE_MOCK_LLM", "").lower() == "true"
    has_openai_key = bool(os.getenv("OPENAI_API_KEY", ""))
    has_anthropic_key = bool(os.getenv("ANTHROPIC_API_KEY", ""))

    return use_mock or (not has_openai_key and not has_anthropic_key)


def call_llm_for_node(
    node_name: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Make an LLM call using archetype configuration for a specific node.
    
    .. deprecated:: 3.1
        Use :func:`call_llm_async_for_node` from `async_client` module instead.
        This function now wraps the async version for backward compatibility.
    
    This is the recommended way to call LLMs from nodes. It loads the archetype
    YAML for the given node name and uses its model configuration.
    
    Bug #90 Fix: Properly handles being called from within an async context
    (e.g., from LangGraph nodes) by using a thread pool executor instead of
    asyncio.run() which cannot be called from a running event loop.
    
    Args:
        node_name: Name of the LangGraph node (e.g., "understand_task", "build_report")
        prompt: The user prompt
        system_prompt: Optional system prompt (overrides archetype default if provided)
        temperature: Optional temperature override (uses archetype default if not provided)
        max_tokens: Optional max tokens override (uses archetype default if not provided)
    
    Returns:
        The LLM response text
        
    Example:
        response = call_llm_for_node(
            "understand_task",
            prompt="Parse this task: Create a checkout session",
        )
    """
    import warnings
    import asyncio
    import concurrent.futures
    
    warnings.warn(
        "call_llm_for_node is deprecated, use call_llm_async_for_node instead. "
        "This function now wraps the async version for backward compatibility.",
        DeprecationWarning,
        stacklevel=2,
    )
    from integration_coworker.llm.async_client import call_llm_async_for_node
    
    async def _call_async():
        return await call_llm_async_for_node(
            node_name=node_name,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    
    # Bug #90 Fix: Check if we're already in an async context
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    
    if loop is not None:
        # We're inside an async context - use thread pool to run a new event loop
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, _call_async())
            return future.result()
    else:
        # No event loop - create one directly
        return asyncio.run(_call_async())
