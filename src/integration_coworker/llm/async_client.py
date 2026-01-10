"""
Async LLM Client Implementation

Provides async versions of LLM clients for true concurrency in parallel integration runs.
Unlike semaphore-based approaches, async clients allow non-blocking I/O while waiting
for LLM API responses.

Features:
- AsyncOpenAILLMClient: Async version of OpenAI client using aiohttp
- AsyncAnthropicLLMClient: Async version of Anthropic client
- AsyncGoogleLLMClient: Async version of Google Gemini client
- Backward-compatible with sync clients (can run async clients in sync context)
- Full LangSmith tracing support
- RECORD/REPLAY mode support
- Parallelization V2: Semaphore-based concurrency control via acquire_llm_slot()

Recommended Usage (archetype-based):
    # Async context - uses archetype YAML for node-specific config
    async def my_async_function():
        response = await call_llm_async_for_node(
            "understand_task",
            "Parse this task..."
        )

    # Concurrent calls with archetypes
    results = await asyncio.gather(
        call_llm_async_for_node("understand_task", prompt1),
        call_llm_async_for_node("generate_code_and_tests", prompt2),
    )

Bug #35 Fix: Enables true concurrent LLM calls without blocking.
Parallelization V2: Adds concurrency limiting to prevent rate limit bursts.
"""
import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Literal, Callable, TypeVar, Union

from integration_coworker.config.llm_mode import get_llm_mode
from integration_coworker.llm.safety import harden_system_prompt
from integration_coworker.llm.client import (
    get_run_context,
    _get_interaction_key,
    _save_interaction,
    _load_interaction,
    _hash_api_key,
    _track_token_usage,
    MockLLMClient,
    get_llm_client_for_node,
)
from integration_coworker.llm.exceptions import (
    LLMError,
    LLMAuthError,
    LLMRateLimitError,
    LLMTransientError,
    classify_llm_exception,
)
from integration_coworker.llm.concurrency import acquire_llm_slot
from integration_coworker.llm.circuit_breaker import (
    get_circuit_breaker,
    CircuitOpenError,
    make_circuit_key,
)

logger = logging.getLogger(__name__)

# Type variable for return types
T = TypeVar('T')


class AsyncLLMClient(Protocol):
    """Protocol for async LLM clients."""

    async def complete_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate a completion asynchronously."""
        ...

    async def complete_json_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate a completion and parse as JSON asynchronously."""
        ...

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Sync wrapper for complete_async."""
        ...


# V22-011: Configurable per-call timeout to prevent long hangs
# Default 120s is reasonable for code generation (OpenAI default is 600s)
LLM_CALL_TIMEOUT_SECONDS = float(os.environ.get("IC_LLM_CALL_TIMEOUT", "120"))


def _check_shutdown_before_llm():
    """
    V24-002: Check if shutdown has been requested before making LLM call.
    
    Raises RuntimeError if shutdown is requested, which the retry loop
    will propagate to abort the operation.
    """
    try:
        from integration_coworker.shutdown import is_shutdown_requested
        if is_shutdown_requested():
            raise RuntimeError("Shutdown requested - aborting LLM call")
    except ImportError:
        pass  # Shutdown module not available


async def _retry_async(
    fn: Callable[..., T],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    circuit_key: Optional[str] = None,
    per_call_timeout: Optional[float] = None,
    *args,
    **kwargs,
) -> T:
    """
    Async retry wrapper with exponential backoff, concurrency control, and circuit breaker.
    
    Production Readiness v4: Auth errors (LLMAuthError) are FATAL and never retried.
    Retries on rate limits (429), server errors (5xx), and transient issues.
    
    V22-011: Added per_call_timeout to prevent indefinite hangs.
    Default timeout from IC_LLM_CALL_TIMEOUT env var (default 120s).
    
    Parallelization V2: Uses semaphore to limit concurrent LLM API requests.
    The semaphore is acquired before each attempt and released after.
    This prevents rate limit bursts when running parallel workflows.
    
    Circuit Breaker (C-2): Failures are counted at the logical request boundary
    (after all retries exhausted). If circuit is open, raises CircuitOpenError
    immediately without attempting the request.
    
    Args:
        fn: Async function to call
        max_attempts: Maximum retry attempts
        base_delay: Base delay for exponential backoff
        circuit_key: Circuit breaker key (e.g., "openai:gpt-4o"). If None, circuit breaker is disabled.
        per_call_timeout: Timeout in seconds for each individual call (default: LLM_CALL_TIMEOUT_SECONDS)
        *args, **kwargs: Arguments to pass to fn
    
    Returns:
        Result of fn
    
    Raises:
        CircuitOpenError: If circuit breaker is open (fail-fast)
        LLMAuthError: If authentication fails (never retried)
        LLMError: If all retries exhausted
        asyncio.TimeoutError: If per_call_timeout exceeded (treated as retryable)
    """
    # V22-011: Use per-call timeout (configurable via env var)
    timeout = per_call_timeout if per_call_timeout is not None else LLM_CALL_TIMEOUT_SECONDS
    
    # Circuit breaker check - fail fast if open
    circuit_breaker = get_circuit_breaker() if circuit_key else None
    if circuit_breaker and circuit_key:
        circuit_breaker.can_execute(circuit_key)  # Raises CircuitOpenError if open
    
    attempts = 0
    last_exception = None
    
    while attempts < max_attempts:
        # V24-002: Check for shutdown before each attempt
        _check_shutdown_before_llm()
        
        try:
            # Parallelization V2: Acquire concurrency slot before making request
            # This limits concurrent LLM calls across all async operations
            async with acquire_llm_slot():
                # V22-011: Wrap with per-call timeout to prevent indefinite hangs
                result = await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
            
            # Success - record with circuit breaker
            if circuit_breaker and circuit_key:
                circuit_breaker.record_success(circuit_key)
            
            return result
        except LLMAuthError:
            # Auth errors are FATAL - never retry, re-raise immediately
            # Don't record as circuit failure - this is a config issue, not service failure
            raise
        except CircuitOpenError:
            # Circuit breaker open - re-raise (shouldn't happen after initial check, but be safe)
            raise
        except asyncio.TimeoutError as e:
            # V22-011: Per-call timeout - treat as retryable transient error
            # This prevents indefinite hangs on slow LLM responses
            attempts += 1
            last_exception = e
            logger.warning(
                f"LLM call timed out after {timeout}s (attempt {attempts}/{max_attempts})"
            )
            if attempts >= max_attempts:
                if circuit_breaker and circuit_key:
                    circuit_breaker.record_failure(circuit_key)
                raise LLMTransientError(
                    message=f"LLM call timed out after {timeout}s ({max_attempts} attempts)",
                    provider="unknown",
                    is_retryable=False,  # No more retries available
                ) from e
            # Retry with backoff
            wait_time = min(base_delay * (2 ** attempts), 10.0)
            await asyncio.sleep(wait_time)
            continue
        except RuntimeError as e:
            # Parallelization V2: Shutdown requested - abort immediately
            if "Shutdown requested" in str(e):
                logger.info("LLM call aborted due to shutdown request")
                raise
            # Other RuntimeErrors should go through normal retry logic
            attempts += 1
            last_exception = e
            # Don't sleep/retry for RuntimeErrors - re-raise after updating attempt count
            break
        except Exception as e:
            attempts += 1
            last_exception = e
            
            # Use typed exception classification
            if isinstance(e, LLMRateLimitError) or isinstance(e, LLMTransientError):
                is_retryable = True
            elif isinstance(e, LLMError):
                is_retryable = False
            else:
                # For untyped exceptions, check string patterns
                error_str = str(e).lower()
                
                # Auth errors - NEVER retry
                if any(pattern in error_str for pattern in [
                    "401", "403", "unauthorized", "authentication",
                    "invalid api key", "invalid_api_key", "permission denied",
                ]):
                    # Convert to typed exception and raise
                    raise classify_llm_exception(e) from e
                
                # Check if retryable
                is_retryable = any(term in error_str for term in [
                    "429", "rate_limit", "rate limit",
                    "500", "502", "503", "504",
                    "timeout", "connection", "network",
                ])
                
                # Don't retry credit/quota errors
                if any(term in error_str for term in ["credit", "balance", "quota"]):
                    is_retryable = False
            
            if attempts >= max_attempts or not is_retryable:
                # All retries exhausted or non-retryable - record circuit failure
                if circuit_breaker and circuit_key:
                    circuit_breaker.record_failure(circuit_key)
                
                # Convert to typed exception before raising
                if not isinstance(e, LLMError):
                    raise classify_llm_exception(e) from e
                raise
            
            wait_time = min(base_delay * (2 ** attempts), 10.0)
            logger.warning(
                f"Async LLM call failed (attempt {attempts}/{max_attempts}), "
                f"retrying in {wait_time:.1f}s: {e}"
            )
            await asyncio.sleep(wait_time)
    
    # Record circuit failure if we fell through the loop
    if circuit_breaker and circuit_key and last_exception:
        circuit_breaker.record_failure(circuit_key)
    
    if last_exception:
        if not isinstance(last_exception, LLMError):
            raise classify_llm_exception(last_exception) from last_exception
        raise last_exception
    
    raise RuntimeError("Retry logic error - should not reach here")


@dataclass
class AsyncOpenAILLMClient:
    """
    Async OpenAI LLM client with LangSmith tracing.
    
    Uses LangChain's async interface for non-blocking LLM calls.
    Provides both async methods and sync wrappers.
    """

    api_key: str
    model: str = "gpt-4o"
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
                "langchain-openai package required for async LLM calls. "
                "Install with: pip install langchain-openai"
            )

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
            "async": True,
        }
        if run_id:
            metadata["run_id"] = run_id
        if provider_code:
            metadata["provider_code"] = provider_code
        return metadata

    async def complete_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Generate a completion asynchronously using LangChain's ainvoke.
        
        Automatically traced in LangSmith when LANGCHAIN_TRACING_V2=true.
        
        Async Migration: Now includes Redis cache integration (Plan 7).
        """
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError:
            raise ImportError(
                "langchain-core package required for LLM calls. "
                "Install with: pip install langchain-core"
            )
        
        from integration_coworker.llm.cache import get_llm_cache

        # Harden system prompt
        hardened_system = harden_system_prompt(system_prompt)

        # Check for REPLAY mode
        mode = get_llm_mode()
        if mode.should_replay:
            cached = _load_interaction(prompt, hardened_system, self.model)
            if cached is not None:
                return cached
            raise RuntimeError(
                f"REPLAY mode: No recorded interaction found for prompt."
            )

        # Check Redis cache first (Async Migration: cache integration)
        # Item E Security: Compute api_key_hash for tenant isolation in cache
        cache = get_llm_cache()
        api_key_hash = _hash_api_key(self.api_key) if cache.is_enabled() else None
        if cache.is_enabled():
            cached_response = cache.get(
                prompt=prompt,
                system_prompt=hardened_system,
                model=self.model,
                provider="openai",
                task_type=self.task_type,
                api_key_hash=api_key_hash,
            )
            if cached_response is not None:
                logger.debug(f"[Async OpenAI] Cache hit for task_type={self.task_type}")
                return cached_response

        llm = self._get_llm(temperature=temperature, max_tokens=max_tokens)

        messages = [
            SystemMessage(content=hardened_system),
            HumanMessage(content=prompt),
        ]

        metadata = self._build_metadata()

        async def _invoke():
            return await llm.ainvoke(
                messages,
                config={
                    "metadata": metadata,
                    "tags": [f"task:{self.task_type}", f"model:{self.model}", "async"],
                }
            )

        # Retry wrapper with circuit breaker (include base_url for endpoint-specific circuits)
        circuit_key = make_circuit_key("openai", self.model, self.base_url)
        response = await _retry_async(_invoke, circuit_key=circuit_key)

        result = response.content or ""
        
        # Track token usage
        _track_token_usage(response)
        
        # Bug #V22-MEM: Explicitly delete response object to free HTTP buffer memory
        del response
        
        # Save interaction if in RECORD mode
        if mode.should_record:
            _save_interaction(prompt, hardened_system, self.model, result)

        # Store in Redis cache (Async Migration: cache integration, Item E: with tenant isolation)
        if cache.is_enabled():
            cache.set(
                prompt=prompt,
                system_prompt=hardened_system,
                model=self.model,
                provider="openai",
                task_type=self.task_type,
                response=result,
                api_key_hash=api_key_hash,
            )

        return result

    async def complete_json_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate a completion and parse as JSON asynchronously."""
        json_system = (system_prompt or "") + "\n\nRespond only with valid JSON, no markdown formatting."

        response = await self.complete_async(
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
            logger.warning(f"Failed to parse LLM response as JSON: {e}")
            return {"error": "Failed to parse response", "raw": response[:500]}

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Sync wrapper for complete_async.
        
        Runs the async method in an event loop. Use complete_async directly
        in async contexts for better performance.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        
        if loop is not None:
            # Already in async context - create a new task
            # This can cause issues with nested loops, so we use run_in_executor
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self.complete_async(prompt, system_prompt, temperature, max_tokens)
                )
                return future.result()
        else:
            # No event loop - create one
            return asyncio.run(
                self.complete_async(prompt, system_prompt, temperature, max_tokens)
            )

    def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Sync wrapper for complete_json_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        
        if loop is not None:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self.complete_json_async(prompt, system_prompt, temperature, max_tokens)
                )
                return future.result()
        else:
            return asyncio.run(
                self.complete_json_async(prompt, system_prompt, temperature, max_tokens)
            )


@dataclass
class AsyncAnthropicLLMClient:
    """
    Async Anthropic Claude LLM client with LangSmith tracing.
    
    Uses LangChain's async interface for non-blocking LLM calls.
    """

    api_key: str
    model: str = "claude-sonnet-4-5-20250929"
    default_temperature: float = 0.7
    default_max_tokens: int = 2000
    task_type: str = "default"

    def _get_llm(self, temperature: Optional[float] = None, max_tokens: Optional[int] = None):
        """Get or create the LangChain ChatAnthropic instance."""
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError(
                "langchain-anthropic package required for Anthropic LLM calls. "
                "Install with: pip install langchain-anthropic"
            )

        return ChatAnthropic(
            api_key=self.api_key,
            model=self.model,
            temperature=temperature if temperature is not None else self.default_temperature,
            max_tokens=max_tokens or self.default_max_tokens,
        )

    def _build_metadata(self) -> Dict[str, Any]:
        """Build metadata for LangSmith tracing."""
        run_id, provider_code = get_run_context()
        metadata = {
            "task_type": self.task_type,
            "model": self.model,
            "provider": "anthropic",
            "async": True,
        }
        if run_id:
            metadata["run_id"] = run_id
        if provider_code:
            metadata["provider_code"] = provider_code
        return metadata

    async def complete_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Generate a completion asynchronously.
        
        Async Migration: Now includes Redis cache integration (Plan 7).
        """
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError:
            raise ImportError("langchain-core package required for LLM calls.")
        
        from integration_coworker.llm.cache import get_llm_cache

        hardened_system = harden_system_prompt(system_prompt)

        mode = get_llm_mode()
        if mode.should_replay:
            cached = _load_interaction(prompt, hardened_system, self.model)
            if cached is not None:
                return cached
            raise RuntimeError("REPLAY mode: No recorded interaction found.")

        # Check Redis cache first (Async Migration: cache integration)
        # Item E Security: Compute api_key_hash for tenant isolation in cache
        cache = get_llm_cache()
        api_key_hash = _hash_api_key(self.api_key) if cache.is_enabled() else None
        if cache.is_enabled():
            cached_response = cache.get(
                prompt=prompt,
                system_prompt=hardened_system,
                model=self.model,
                provider="anthropic",
                task_type=self.task_type,
                api_key_hash=api_key_hash,
            )
            if cached_response is not None:
                logger.debug(f"[Async Anthropic] Cache hit for task_type={self.task_type}")
                return cached_response

        llm = self._get_llm(temperature=temperature, max_tokens=max_tokens)

        messages = [
            SystemMessage(content=hardened_system),
            HumanMessage(content=prompt),
        ]

        metadata = self._build_metadata()

        async def _invoke():
            return await llm.ainvoke(
                messages,
                config={
                    "metadata": metadata,
                    "tags": [f"task:{self.task_type}", f"model:{self.model}", "async", "anthropic"],
                }
            )

        # Retry wrapper with circuit breaker (Anthropic doesn't support custom base_url)
        circuit_key = make_circuit_key("anthropic", self.model)
        response = await _retry_async(_invoke, circuit_key=circuit_key)

        result = response.content or ""
        
        # Bug #V22-MEM: Explicitly delete response object to free HTTP buffer memory
        del response
        
        if mode.should_record:
            _save_interaction(prompt, hardened_system, self.model, result)

        # Store in Redis cache (Async Migration: cache integration, Item E: with tenant isolation)
        if cache.is_enabled():
            cache.set(
                prompt=prompt,
                system_prompt=hardened_system,
                model=self.model,
                provider="anthropic",
                task_type=self.task_type,
                response=result,
                api_key_hash=api_key_hash,
            )

        return result

    async def complete_json_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate a completion and parse as JSON asynchronously."""
        json_system = (system_prompt or "") + "\n\nRespond only with valid JSON, no markdown formatting."

        response = await self.complete_async(
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
            return {"error": "Failed to parse response", "raw": response[:500]}

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Sync wrapper for complete_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        
        if loop is not None:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self.complete_async(prompt, system_prompt, temperature, max_tokens)
                )
                return future.result()
        else:
            return asyncio.run(
                self.complete_async(prompt, system_prompt, temperature, max_tokens)
            )

    def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Sync wrapper for complete_json_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        
        if loop is not None:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self.complete_json_async(prompt, system_prompt, temperature, max_tokens)
                )
                return future.result()
        else:
            return asyncio.run(
                self.complete_json_async(prompt, system_prompt, temperature, max_tokens)
            )


@dataclass
class AsyncGoogleLLMClient:
    """
    Async Google Gemini LLM client with LangSmith tracing.
    
    Uses LangChain's async interface for non-blocking LLM calls.
    """

    api_key: str
    model: str = "gemini-2.5-flash"
    default_temperature: float = 0.7
    default_max_tokens: int = 2000
    task_type: str = "default"

    def _get_llm(self, temperature: Optional[float] = None, max_tokens: Optional[int] = None):
        """Get or create the LangChain ChatGoogleGenerativeAI instance."""
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError(
                "langchain-google-genai package required for Google Gemini LLM calls. "
                "Install with: pip install langchain-google-genai"
            )

        return ChatGoogleGenerativeAI(
            google_api_key=self.api_key,
            model=self.model,
            temperature=temperature if temperature is not None else self.default_temperature,
            max_output_tokens=max_tokens or self.default_max_tokens,
        )

    def _build_metadata(self) -> Dict[str, Any]:
        """Build metadata for LangSmith tracing."""
        run_id, provider_code = get_run_context()
        metadata = {
            "task_type": self.task_type,
            "model": self.model,
            "provider": "google",
            "async": True,
        }
        if run_id:
            metadata["run_id"] = run_id
        if provider_code:
            metadata["provider_code"] = provider_code
        return metadata

    async def complete_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Generate a completion asynchronously.
        
        Async Migration: Now includes Redis cache integration (Plan 7).
        """
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError:
            raise ImportError("langchain-core package required for LLM calls.")
        
        from integration_coworker.llm.cache import get_llm_cache

        hardened_system = harden_system_prompt(system_prompt)

        mode = get_llm_mode()
        if mode.should_replay:
            cached = _load_interaction(prompt, hardened_system, self.model)
            if cached is not None:
                return cached
            raise RuntimeError("REPLAY mode: No recorded interaction found.")

        # Check Redis cache first (Async Migration: cache integration)
        # Item E Security: Compute api_key_hash for tenant isolation in cache
        cache = get_llm_cache()
        api_key_hash = _hash_api_key(self.api_key) if cache.is_enabled() else None
        if cache.is_enabled():
            cached_response = cache.get(
                prompt=prompt,
                system_prompt=hardened_system,
                model=self.model,
                provider="google",
                task_type=self.task_type,
                api_key_hash=api_key_hash,
            )
            if cached_response is not None:
                logger.debug(f"[Async Google] Cache hit for task_type={self.task_type}")
                return cached_response

        llm = self._get_llm(temperature=temperature, max_tokens=max_tokens)

        messages = [
            SystemMessage(content=hardened_system),
            HumanMessage(content=prompt),
        ]

        metadata = self._build_metadata()

        async def _invoke():
            return await llm.ainvoke(
                messages,
                config={
                    "metadata": metadata,
                    "tags": [f"task:{self.task_type}", f"model:{self.model}", "async", "google"],
                }
            )

        # Retry wrapper with circuit breaker (Google doesn't support custom base_url)
        circuit_key = make_circuit_key("google", self.model)
        response = await _retry_async(_invoke, circuit_key=circuit_key)

        result = response.content or ""
        
        _track_token_usage(response)
        
        # Bug #V22-MEM: Explicitly delete response object to free HTTP buffer memory
        del response
        
        if mode.should_record:
            _save_interaction(prompt, hardened_system, self.model, result)

        # Store in Redis cache (Async Migration: cache integration, Item E: with tenant isolation)
        if cache.is_enabled():
            cache.set(
                prompt=prompt,
                system_prompt=hardened_system,
                model=self.model,
                provider="google",
                task_type=self.task_type,
                response=result,
                api_key_hash=api_key_hash,
            )

        return result

    async def complete_json_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate a completion and parse as JSON asynchronously."""
        json_system = (system_prompt or "") + "\n\nRespond only with valid JSON, no markdown formatting."

        response = await self.complete_async(
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
            return {"error": "Failed to parse response", "raw": response[:500]}

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Sync wrapper for complete_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        
        if loop is not None:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self.complete_async(prompt, system_prompt, temperature, max_tokens)
                )
                return future.result()
        else:
            return asyncio.run(
                self.complete_async(prompt, system_prompt, temperature, max_tokens)
            )

    def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Sync wrapper for complete_json_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        
        if loop is not None:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self.complete_json_async(prompt, system_prompt, temperature, max_tokens)
                )
                return future.result()
        else:
            return asyncio.run(
                self.complete_json_async(prompt, system_prompt, temperature, max_tokens)
            )


# Cache for async client instances
_async_client_cache: Dict[str, AsyncLLMClient] = {}


def get_async_llm_client(
    task_type: str = "default",
    provider: Optional[str] = None,
    strict: bool = False,
) -> AsyncLLMClient:
    """
    Get an async LLM client for the specified task type.
    
    Returns an AsyncLLMClient that supports both async and sync operations.
    Use complete_async() in async contexts, or complete() as a sync wrapper.
    
    Note: Prefer call_llm_async_for_node() for node-specific configuration.
    This function uses default settings.
    
    Args:
        task_type: Type of task (for cache key only)
        provider: Optional provider override ("openai", "anthropic", "google")
        strict: If True, raise error when API key is missing
        
    Returns:
        AsyncLLMClient instance
    """
    from integration_coworker.config import get_settings
    
    mode = get_llm_mode()
    cache_key = f"async:{task_type}:{provider or 'default'}:{mode.value}"
    
    if cache_key in _async_client_cache:
        return _async_client_cache[cache_key]

    # Use Settings for default configuration
    settings = get_settings()
    config = {
        "model": settings.llm.default_model,
        "temperature": 0.7,
        "max_tokens": 2000,
        "api_key": settings.llm.api_key,
        "use_mock": settings.llm.use_mock,
    }
    if settings.llm.base_url:
        config["base_url"] = settings.llm.base_url
    
    effective_provider = provider or "openai"

    # Mock mode
    if mode.is_mock or effective_provider == "mock":
        logger.info(f"Using mock LLM client for async task_type={task_type}")
        # MockLLMClient is sync-only but compatible with our interface
        client = MockLLMClient(task_type=task_type)
        _async_client_cache[cache_key] = client
        return client

    # Provider fallback chain
    if effective_provider == "anthropic":
        fallback_chain = ["anthropic", "openai", "google"]
    elif effective_provider == "google":
        fallback_chain = ["google", "openai", "anthropic"]
    else:
        fallback_chain = ["openai", "anthropic", "google"]

    for try_provider in fallback_chain:
        client = _try_create_async_client(
            try_provider,
            config=config,
            task_type=task_type,
            is_fallback=(try_provider != effective_provider),
        )
        if client is not None:
            _async_client_cache[cache_key] = client
            return client

    if strict:
        raise RuntimeError(
            "No LLM API keys configured for async client.\n"
            "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY."
        )
    
    logger.warning(f"No async LLM API keys available, using mock client")
    client = MockLLMClient(task_type=task_type)
    _async_client_cache[cache_key] = client
    return client


def _try_create_async_client(
    provider: str,
    config: Dict[str, Any],
    task_type: str,
    is_fallback: bool = False,
) -> Optional[AsyncLLMClient]:
    """Try to create an async LLM client for a specific provider."""
    temperature = config.get("temperature", 0.7)
    max_tokens = config.get("max_tokens", 2000)
    
    if provider == "openai":
        api_key = config.get("api_key", "") or os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return None
        
        model = config.get("model", "gpt-4o") if not is_fallback else "gpt-4o"
        if is_fallback:
            logger.info(f"Async: Falling back to OpenAI for task_type={task_type}")
        else:
            logger.info(f"Async: Using OpenAI for task_type={task_type}, model={model}")
        
        return AsyncOpenAILLMClient(
            api_key=api_key,
            model=model,
            base_url=config.get("base_url"),
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )
    
    elif provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        
        model = config.get("model", "claude-sonnet-4-5-20250929") if not is_fallback else "claude-sonnet-4-5-20250929"
        if is_fallback:
            logger.info(f"Async: Falling back to Anthropic for task_type={task_type}")
        else:
            logger.info(f"Async: Using Anthropic for task_type={task_type}, model={model}")
        
        return AsyncAnthropicLLMClient(
            api_key=api_key,
            model=model,
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )
    
    elif provider == "google":
        api_key = os.getenv("GOOGLE_API_KEY", "")
        if not api_key:
            return None
        
        model = config.get("model", "gemini-2.5-flash") if not is_fallback else "gemini-2.5-flash"
        if is_fallback:
            logger.info(f"Async: Falling back to Google Gemini for task_type={task_type}")
        else:
            logger.info(f"Async: Using Google Gemini for task_type={task_type}, model={model}")
        
        return AsyncGoogleLLMClient(
            api_key=api_key,
            model=model,
            default_temperature=temperature,
            default_max_tokens=max_tokens,
            task_type=task_type,
        )
    
    return None


def reset_async_client_cache() -> None:
    """Reset the async client cache (for testing)."""
    global _async_client_cache
    _async_client_cache = {}


def get_async_llm_client_for_node(node_name: str, strict: bool = False) -> AsyncLLMClient:
    """
    Get an async LLM client configured for a specific LangGraph node.
    
    This is the recommended async entry point for nodes. It loads the archetype
    YAML for the given node name and returns a properly configured async client.
    
    Async Migration: This is the preferred way to get an async LLM client.
    
    Args:
        node_name: Name of the LangGraph node (e.g., "understand_task", "generate_code_and_tests")
        strict: If True, raise error when API key is missing
        
    Returns:
        AsyncLLMClient configured according to the node's archetype
        
    Example:
        client = get_async_llm_client_for_node("understand_task")
        response = await client.complete_async(prompt)
    """
    from integration_coworker.config import load_archetype
    
    archetype = load_archetype(node_name)
    return _get_async_client_for_archetype(archetype, strict=strict)


def _get_async_client_for_archetype(
    archetype_config: Dict[str, Any],
    strict: bool = False,
) -> AsyncLLMClient:
    """
    Create async client from archetype config.
    
    Async Migration: Mirrors get_llm_client_for_archetype() but returns async client.
    """
    mode = get_llm_mode()
    
    # Extract config from archetype
    model_config = archetype_config.get("model", {})
    provider = model_config.get("provider", "openai")
    model = model_config.get("name", "gpt-4o")
    temperature = model_config.get("temperature", 0.7)
    max_tokens = model_config.get("max_tokens", 2000)
    task_type = archetype_config.get("name", "default")
    
    # Check for mock mode
    if mode.is_mock or provider == "mock":
        logger.info(f"Using mock LLM client for async node={task_type}")
        return MockLLMClient(task_type=task_type)
    
    # Try to create provider-specific client
    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            logger.debug(f"Creating async OpenAI client for node={task_type}, model={model}")
            return AsyncOpenAILLMClient(
                api_key=api_key,
                model=model,
                base_url=model_config.get("base_url"),
                default_temperature=temperature,
                default_max_tokens=max_tokens,
                task_type=task_type,
            )
    
    elif provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if api_key:
            logger.debug(f"Creating async Anthropic client for node={task_type}, model={model}")
            return AsyncAnthropicLLMClient(
                api_key=api_key,
                model=model,
                default_temperature=temperature,
                default_max_tokens=max_tokens,
                task_type=task_type,
            )
    
    elif provider == "google":
        api_key = os.getenv("GOOGLE_API_KEY", "")
        if api_key:
            logger.debug(f"Creating async Google client for node={task_type}, model={model}")
            return AsyncGoogleLLMClient(
                api_key=api_key,
                model=model,
                default_temperature=temperature,
                default_max_tokens=max_tokens,
                task_type=task_type,
            )
    
    # Fallback: use get_async_llm_client with fallback chain
    logger.debug(f"No API key for {provider}, using fallback chain for node={task_type}")
    return get_async_llm_client(task_type=task_type, provider=provider, strict=strict)


async def call_llm_async_for_node(
    node_name: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Async LLM call using archetype configuration.
    
    This is the recommended async LLM call function. It loads configuration
    from config/archetypes/{node_name}.archetype.yaml and uses the appropriate
    model, temperature, and max_tokens.
    
    Async Migration: Now uses get_async_llm_client_for_node() directly.
    
    Args:
        node_name: Name of the workflow node (e.g., "understand_task", "generate_code_and_tests")
        prompt: The user prompt
        system_prompt: Optional system prompt (overrides archetype default if provided)
        temperature: Optional temperature override
        max_tokens: Optional max tokens override
    
    Returns:
        The LLM response text
    
    Example:
        response = await call_llm_async_for_node(
            "understand_task",
            "Parse this task description..."
        )
    """
    # Async Migration: Use native async client directly
    client = get_async_llm_client_for_node(node_name)
    
    # All async clients now support complete_async
    if hasattr(client, 'complete_async'):
        return await client.complete_async(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    else:
        # Fallback for MockLLMClient (sync-only)
        import concurrent.futures
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(
                executor,
                lambda: client.complete(prompt, system_prompt, temperature, max_tokens)
            )


async def run_concurrent_llm_calls(
    prompts: list[tuple[str, str, Optional[str]]],  # (prompt, node_name, system_prompt)
) -> list[str]:
    """
    Run multiple LLM calls concurrently.
    
    This is the primary benefit of async clients - multiple LLM calls
    can be awaited simultaneously without blocking.
    
    Args:
        prompts: List of (prompt, node_name, system_prompt) tuples
        
    Returns:
        List of responses in the same order as prompts
        
    Example:
        results = await run_concurrent_llm_calls([
            ("Generate client code", "generate_code_and_tests", None),
            ("Generate flow code", "generate_code_and_tests", None),
            ("Generate test code", "generate_code_and_tests", None),
        ])
    """
    async def _call_one(prompt: str, node_name: str, system_prompt: Optional[str]) -> str:
        return await call_llm_async_for_node(node_name, prompt, system_prompt=system_prompt)
    
    tasks = [
        _call_one(prompt, node_name, system_prompt)
        for prompt, node_name, system_prompt in prompts
    ]
    
    return await asyncio.gather(*tasks)


async def call_llm_async(
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    task_type: str = "default",
) -> str:
    """
    Backward-compatible async LLM entrypoint.

    Thin wrapper around get_async_llm_client() for parity with the sync
    call_llm_for_node surface. Kept minimal for tests expecting this export.
    """

    client = get_async_llm_client(task_type=task_type)

    if hasattr(client, "complete_async"):
        return await client.complete_async(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # Fallback for mock/sync clients
    import concurrent.futures

    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor() as executor:
        return await loop.run_in_executor(
            executor,
            lambda: client.complete(prompt, system_prompt, temperature, max_tokens),
        )
