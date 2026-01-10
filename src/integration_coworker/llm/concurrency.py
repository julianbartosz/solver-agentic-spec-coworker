"""
LLM Concurrency Limiter (Parallelization V2)

Provides global semaphore-based concurrency control for LLM API requests.
Prevents rate limit bursts when running parallel workflows.

Features:
- asyncio.Semaphore-based limiting
- Configurable via Settings system (profiles/env vars)
- Metrics tracking for observability
- Graceful shutdown integration
- Timeout handling with configurable limits

Configuration:
    Settings (config/__init__.py):
        settings.llm.max_concurrent: Maximum concurrent LLM requests (default: 5)
        settings.llm.acquire_timeout_s: Timeout for acquisition in seconds (default: 30)
    
    Environment Variables (override settings):
        LLM_MAX_CONCURRENT: Maximum concurrent LLM requests
        LLM_ACQUIRE_TIMEOUT: Timeout for semaphore acquisition in seconds

Usage:
    from integration_coworker.llm.concurrency import acquire_llm_slot

    # Context manager (recommended)
    async with acquire_llm_slot():
        response = await llm.ainvoke(messages)

    # Check metrics
    from integration_coworker.llm.concurrency import get_concurrency_metrics
    metrics = get_concurrency_metrics()
    print(f"Peak concurrent: {metrics['peak_active']}")

Design Notes (Parallelization V2):
- Single global semaphore is sufficient since we typically use one LLM provider at a time
- Per-provider semaphores would add complexity without significant benefit
- The semaphore wraps at the _retry_async() level, not inside individual client methods,
  ensuring consistent behavior across all providers (OpenAI, Anthropic, Google)
"""

import asyncio
import logging
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Dict, Optional

from integration_coworker.shutdown import is_shutdown_requested

logger = logging.getLogger(__name__)

# Configuration defaults (used if Settings unavailable)
DEFAULT_MAX_CONCURRENT = 5
DEFAULT_ACQUIRE_TIMEOUT = 30.0

# Global state - per-event-loop semaphores to avoid cross-loop issues
# WeakKeyDictionary allows event loops to be garbage collected when done
_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)
_config: Optional["ConcurrencyConfig"] = None


@dataclass
class ConcurrencyConfig:
    """
    Configuration for LLM concurrency limiting.
    
    Attributes:
        max_concurrent: Maximum number of concurrent LLM requests
        acquire_timeout: Timeout in seconds for acquiring a slot
        total_acquired: Total number of successful slot acquisitions
        total_timeouts: Total number of acquisition timeouts
        current_active: Current number of active requests
        peak_active: Peak number of concurrent requests seen
    """
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT
    
    # Metrics (not included in repr to avoid noise)
    total_acquired: int = field(default=0, repr=False)
    total_timeouts: int = field(default=0, repr=False)
    current_active: int = field(default=0, repr=False)
    peak_active: int = field(default=0, repr=False)


def _load_config() -> ConcurrencyConfig:
    """
    Load configuration from Settings system.
    
    Priority:
    1. Settings.llm.max_concurrent / Settings.llm.acquire_timeout_s
    2. Environment variables (already read by Settings)
    3. Hardcoded defaults
    
    This ensures config respects the profiles/settings pattern.
    """
    # Import here to avoid circular imports
    from integration_coworker.config import get_settings
    
    try:
        settings = get_settings()
        max_concurrent = settings.llm.max_concurrent
        acquire_timeout = settings.llm.acquire_timeout_s
        logger.debug(
            f"Loaded concurrency config from Settings: "
            f"max_concurrent={max_concurrent}, acquire_timeout={acquire_timeout}"
        )
    except Exception as e:
        # Fall back to defaults if Settings unavailable
        logger.warning(f"Failed to load Settings, using defaults: {e}")
        max_concurrent = DEFAULT_MAX_CONCURRENT
        acquire_timeout = DEFAULT_ACQUIRE_TIMEOUT
    
    # Validate
    if max_concurrent < 1:
        logger.warning(f"max_concurrent={max_concurrent} is invalid, using default {DEFAULT_MAX_CONCURRENT}")
        max_concurrent = DEFAULT_MAX_CONCURRENT
    
    if acquire_timeout <= 0:
        logger.warning(f"acquire_timeout={acquire_timeout} is invalid, using default {DEFAULT_ACQUIRE_TIMEOUT}")
        acquire_timeout = DEFAULT_ACQUIRE_TIMEOUT
    
    return ConcurrencyConfig(
        max_concurrent=max_concurrent,
        acquire_timeout=acquire_timeout,
    )


def get_concurrency_config() -> ConcurrencyConfig:
    """
    Get the global concurrency configuration.
    
    Configuration is loaded once from environment variables and cached.
    Use reset_llm_semaphore() to reload configuration.
    
    Returns:
        ConcurrencyConfig instance
    """
    global _config
    if _config is None:
        _config = _load_config()
    return _config


def get_llm_semaphore() -> asyncio.Semaphore:
    """
    Get the LLM semaphore for the current event loop.
    
    Creates the semaphore lazily on first access per event loop.
    Uses WeakKeyDictionary to avoid holding references to closed loops.
    The semaphore limit is controlled by LLM_MAX_CONCURRENT env var.
    
    Returns:
        asyncio.Semaphore with configured limit for the current event loop
    
    Raises:
        RuntimeError: If called from sync context without a running event loop.
            This function should only be called from within an async context.
    """
    global _semaphores
    # Always require a running event loop - semaphores are bound to their loop
    loop = asyncio.get_running_loop()
    
    if loop not in _semaphores:
        config = get_concurrency_config()
        _semaphores[loop] = asyncio.Semaphore(config.max_concurrent)
        logger.info(f"Initialized LLM semaphore for loop {id(loop)} with max_concurrent={config.max_concurrent}")
    return _semaphores[loop]


def reset_llm_semaphore() -> None:
    """
    Reset the semaphores and configuration.
    
    This function is designed for TEST USE ONLY. Production code should never
    call this function as it clears global state that may be in use.
    
    In test context (detected via is_test_mode()), this also resets the
    Settings cache to allow tests to set fresh environment variables.
    
    Warning: Calling this while requests are in-flight may cause
    unexpected behavior. Only call when idle.
    
    Test mode is detected via:
    - INTEGRATION_COWORKER_TESTING=1 (explicit, preferred)
    - PYTEST_CURRENT_TEST (auto-set by pytest, fallback)
    """
    from integration_coworker.utils.exceptions import is_test_mode
    
    global _semaphores, _config
    _semaphores.clear()
    _config = None
    
    # TEST-ONLY BEHAVIOR: Reset Settings cache to allow fresh env var reads.
    # This guard prevents accidental Settings cache corruption in production.
    if is_test_mode():
        from integration_coworker.config import reset_settings
        reset_settings()
        logger.debug("Reset LLM semaphore, configuration, and Settings cache (test context)")
    else:
        logger.debug("Reset LLM semaphore and configuration (production context, Settings cache preserved)")


@asynccontextmanager
async def acquire_llm_slot(timeout: Optional[float] = None):
    """
    Async context manager to acquire an LLM concurrency slot.
    
    This is the recommended way to throttle LLM requests.
    Automatically handles timeout, shutdown, and cancellation.
    
    Cancellation Safety:
    - If cancelled while waiting for semaphore: no slot held, no release needed
    - If cancelled after acquire: slot is released in finally block
    - Metrics are updated atomically with acquire/release
    
    Args:
        timeout: Override the default acquire timeout (seconds).
                 If None, uses LLM_ACQUIRE_TIMEOUT.
        
    Raises:
        asyncio.TimeoutError: If slot not acquired within timeout
        RuntimeError: If shutdown is requested while waiting
        asyncio.CancelledError: If task is cancelled (slot properly released)
        
    Usage:
        async with acquire_llm_slot():
            response = await llm.ainvoke(messages)
            
    Example with custom timeout:
        async with acquire_llm_slot(timeout=5.0):
            response = await llm.ainvoke(messages)
    """
    config = get_concurrency_config()
    semaphore = get_llm_semaphore()
    effective_timeout = timeout if timeout is not None else config.acquire_timeout
    
    # Check shutdown before waiting
    if is_shutdown_requested():
        raise RuntimeError("Shutdown requested, aborting LLM slot acquisition")
    
    # Use explicit boolean to track acquisition state for cancellation safety
    acquired: bool = False
    
    try:
        # Wait for semaphore with timeout
        try:
            await asyncio.wait_for(
                semaphore.acquire(),
                timeout=effective_timeout,
            )
            # Mark acquired IMMEDIATELY after successful acquire
            acquired = True
        except asyncio.TimeoutError:
            # Timeout: slot NOT acquired, increment timeout counter
            config.total_timeouts += 1
            logger.warning(
                f"LLM slot acquisition timed out after {effective_timeout}s "
                f"(current_active={config.current_active}, max={config.max_concurrent})"
            )
            raise asyncio.TimeoutError(
                f"Failed to acquire LLM slot within {effective_timeout}s. "
                f"Current active: {config.current_active}/{config.max_concurrent}. "
                f"Consider increasing LLM_MAX_CONCURRENT or LLM_ACQUIRE_TIMEOUT."
            )
        except asyncio.CancelledError:
            # Cancellation during wait: slot NOT acquired
            logger.debug("LLM slot acquisition cancelled while waiting")
            raise
        
        # Track metrics AFTER acquire confirmed
        config.total_acquired += 1
        config.current_active += 1
        config.peak_active = max(config.peak_active, config.current_active)
        
        # Log at limit
        if config.current_active >= config.max_concurrent:
            logger.debug(
                f"LLM concurrency at limit: {config.current_active}/{config.max_concurrent} "
                f"(peak={config.peak_active})"
            )
        
        yield
        
    except asyncio.CancelledError:
        # Re-raise cancellation - finally block will handle cleanup
        logger.debug(f"LLM slot holder cancelled (acquired={acquired})")
        raise
    finally:
        # Release ONLY if we successfully acquired
        if acquired:
            semaphore.release()
            config.current_active -= 1


def get_concurrency_metrics() -> dict:
    """
    Get concurrency metrics for observability.
    
    Returns:
        Dictionary with:
        - max_concurrent: Configured maximum concurrent requests
        - acquire_timeout: Configured acquisition timeout
        - total_acquired: Total successful slot acquisitions
        - total_timeouts: Total acquisition timeouts
        - current_active: Current active requests
        - peak_active: Peak concurrent requests seen
        - utilization: Current utilization percentage (current/max * 100)
    """
    config = get_concurrency_config()
    utilization = (config.current_active / config.max_concurrent * 100) if config.max_concurrent > 0 else 0.0
    
    return {
        "max_concurrent": config.max_concurrent,
        "acquire_timeout": config.acquire_timeout,
        "total_acquired": config.total_acquired,
        "total_timeouts": config.total_timeouts,
        "current_active": config.current_active,
        "peak_active": config.peak_active,
        "utilization": round(utilization, 1),
    }


def log_concurrency_status() -> None:
    """
    Log current concurrency status.
    
    Useful for debugging and observability.
    """
    metrics = get_concurrency_metrics()
    logger.info(
        f"LLM Concurrency: active={metrics['current_active']}/{metrics['max_concurrent']} "
        f"(peak={metrics['peak_active']}, acquired={metrics['total_acquired']}, "
        f"timeouts={metrics['total_timeouts']}, utilization={metrics['utilization']}%)"
    )


# =============================================================================
# Optional: Decorator for wrapping async functions
# =============================================================================

def with_llm_concurrency(timeout: Optional[float] = None):
    """
    Decorator to wrap an async function with concurrency control.
    
    Args:
        timeout: Optional timeout override for slot acquisition
        
    Usage:
        @with_llm_concurrency(timeout=10.0)
        async def my_llm_call(prompt: str) -> str:
            return await llm.ainvoke(prompt)
    """
    def decorator(fn):
        async def wrapper(*args, **kwargs):
            async with acquire_llm_slot(timeout=timeout):
                return await fn(*args, **kwargs)
        return wrapper
    return decorator
