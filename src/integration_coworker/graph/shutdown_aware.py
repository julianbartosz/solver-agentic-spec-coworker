"""
Shutdown-aware utilities for LangGraph nodes.

V24-002: Production-ready shutdown handling for graph execution.

This module provides utilities that enable interruptible graph execution:
1. shutdown_check_point() - Explicit checkpoint for shutdown detection
2. interruptible_llm_call() - Wrapper that checks shutdown before/after LLM calls
3. ShutdownAwareLoop - Context manager for loops with periodic shutdown checks

Problem Statement (BUG-V24-002):
Once `app.ainvoke()` starts, the LangGraph execution runs to completion unless
explicitly interrupted. If a node blocks on an LLM call or I/O for hours,
the shutdown signal is never checked.

Solution:
Insert explicit shutdown checkpoints within long-running operations:
- Before each LLM call
- After each LLM call returns
- Within loops processing many items
- Between major processing phases

Usage:
    from integration_coworker.graph.shutdown_aware import (
        shutdown_check_point,
        interruptible_llm_call,
        ShutdownAwareLoop,
    )
    
    # Simple checkpoint
    shutdown_check_point("before LLM call")
    
    # Interruptible LLM call
    response = await interruptible_llm_call(
        llm_func=client.complete_async,
        prompt=prompt,
        context="generating code"
    )
    
    # Loop with periodic checks
    async with ShutdownAwareLoop(items, check_every=10) as loop:
        for item in loop:
            await process(item)
"""
import asyncio
import atexit
import logging
import time
from contextlib import asynccontextmanager, contextmanager
from functools import wraps
from typing import Any, Callable, Iterator, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')


class ShutdownInterruptError(Exception):
    """Raised when shutdown is detected during operation."""
    
    def __init__(self, context: str = "operation"):
        self.context = context
        super().__init__(f"Shutdown requested during {context}")


def shutdown_check_point(context: str = "checkpoint") -> None:
    """
    Check if shutdown has been requested, raise if so.
    
    Insert this at key points in long-running operations:
    - Before expensive LLM calls
    - After LLM calls return
    - Between processing phases
    - In loops processing many items
    
    Args:
        context: Description of what operation was interrupted (for logging)
    
    Raises:
        ShutdownInterruptError: If shutdown was requested
    """
    try:
        from integration_coworker.shutdown import is_shutdown_requested
        if is_shutdown_requested():
            logger.warning(f"V24-002: Shutdown detected at checkpoint: {context}")
            raise ShutdownInterruptError(context)
    except ImportError:
        # Shutdown module not available - continue without check
        pass


async def interruptible_llm_call(
    llm_func: Callable[..., Any],
    *args,
    context: str = "LLM call",
    timeout: Optional[float] = 300.0,  # 5 minute default timeout
    **kwargs
) -> Any:
    """
    Execute an LLM call with shutdown checking before and after.
    
    This wrapper ensures that:
    1. Shutdown is checked before starting the LLM call
    2. The LLM call has a timeout to prevent infinite blocking
    3. Shutdown is checked after the call returns
    
    Args:
        llm_func: Async LLM function to call
        *args: Positional arguments for llm_func
        context: Description for logging
        timeout: Max seconds to wait for LLM response (None = no timeout)
        **kwargs: Keyword arguments for llm_func
    
    Returns:
        The result of llm_func
        
    Raises:
        ShutdownInterruptError: If shutdown requested before/after call
        asyncio.TimeoutError: If LLM call exceeds timeout
    """
    # Check before call
    shutdown_check_point(f"before {context}")
    
    start_time = time.perf_counter()
    
    try:
        if timeout is not None:
            result = await asyncio.wait_for(
                llm_func(*args, **kwargs),
                timeout=timeout
            )
        else:
            result = await llm_func(*args, **kwargs)
            
        elapsed = time.perf_counter() - start_time
        logger.debug(f"V24-002: {context} completed in {elapsed:.2f}s")
        
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start_time
        logger.error(f"V24-002: {context} timed out after {elapsed:.2f}s")
        raise
    
    # Check after call
    shutdown_check_point(f"after {context}")
    
    return result


class ShutdownAwareLoop:
    """
    Iterator wrapper that checks for shutdown periodically.
    
    Use this when processing many items to ensure shutdown signals
    are detected promptly.
    
    Usage:
        items = [...]
        with ShutdownAwareLoop(items, check_every=10) as loop:
            for item in loop:
                process(item)
    
    Args:
        iterable: Items to iterate over
        check_every: Check shutdown after this many iterations (default 10)
        context: Description for logging
    """
    
    def __init__(
        self,
        iterable,
        check_every: int = 10,
        context: str = "loop iteration",
    ):
        self.iterable = iterable
        self.check_every = check_every
        self.context = context
        self._count = 0
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass
    
    def __iter__(self):
        for item in self.iterable:
            self._count += 1
            if self._count % self.check_every == 0:
                shutdown_check_point(f"{self.context} (iteration {self._count})")
            yield item


@asynccontextmanager
async def shutdown_aware_section(context: str = "section"):
    """
    Async context manager that checks shutdown on entry and exit.
    
    Usage:
        async with shutdown_aware_section("code generation"):
            response = await llm.generate(...)
            process(response)
    """
    shutdown_check_point(f"entering {context}")
    try:
        yield
    finally:
        # Don't raise on exit - just log if shutdown was requested
        try:
            from integration_coworker.shutdown import is_shutdown_requested
            if is_shutdown_requested():
                logger.info(f"V24-002: Shutdown pending after {context}")
        except ImportError:
            pass


def with_shutdown_check(context: str = "operation"):
    """
    Decorator that adds shutdown checks before/after a function.
    
    Works with both sync and async functions.
    
    Usage:
        @with_shutdown_check("endpoint extraction")
        def extract_endpoints(spec):
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs) -> T:
                shutdown_check_point(f"before {context}")
                result = await func(*args, **kwargs)
                shutdown_check_point(f"after {context}")
                return result
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs) -> T:
                shutdown_check_point(f"before {context}")
                result = func(*args, **kwargs)
                shutdown_check_point(f"after {context}")
                return result
            return sync_wrapper
    return decorator


# =============================================================================
# V24-003: atexit cleanup for forced terminations
# =============================================================================

_atexit_registered = False


def _atexit_cleanup():
    """
    Emergency cleanup called on Python exit.
    
    V24-003: Ensures ShutdownManager cleanup runs even on forced termination.
    This handles cases where the process exits without going through the
    normal cleanup path (e.g., SIGKILL after timeout -k).
    """
    logger.debug("V24-003: atexit cleanup triggered")
    try:
        from integration_coworker.shutdown import get_shutdown_manager
        manager = get_shutdown_manager()
        
        # Only run cleanup if it wasn't already run
        if manager._shutdown_event is not None:
            # Use synchronous cleanup path for atexit
            # Can't run async code in atexit handlers reliably
            for callback in manager._cleanup_callbacks:
                try:
                    if not asyncio.iscoroutinefunction(callback):
                        callback()
                except Exception as e:
                    logger.warning(f"V24-003: atexit cleanup callback failed: {e}")
            
            logger.info("V24-003: atexit cleanup completed")
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"V24-003: atexit cleanup error: {e}")


def register_atexit_cleanup():
    """
    Register atexit cleanup handler for emergency shutdown.
    
    Call this early in the application lifecycle to ensure cleanup
    runs even on forced termination.
    """
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(_atexit_cleanup)
        _atexit_registered = True
        logger.debug("V24-003: atexit cleanup handler registered")


# Auto-register on import
register_atexit_cleanup()
