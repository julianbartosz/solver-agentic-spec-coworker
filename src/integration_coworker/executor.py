"""
Shared ThreadPoolExecutor for production use.

H-3: Provides a module-level shared executor with proper lifecycle management.

Why a shared executor?
---------------------
Creating ThreadPoolExecutor per-call has overhead:
- Thread pool creation/teardown
- No control over total thread count
- No graceful shutdown on process termination

This module provides:
- Lazy initialization (executor created on first use)
- Configurable via environment variables
- Automatic cleanup registration with ShutdownManager and atexit
- Thread-safe singleton pattern

Usage
-----
For async code (PREFERRED):
    # asyncio.to_thread uses the default executor and is simpler
    result = await asyncio.to_thread(sync_function, arg1, arg2)

For sync code needing executor:
    from integration_coworker.executor import get_executor, run_sync_in_executor
    
    # Get the shared executor
    executor = get_executor()
    future = executor.submit(sync_function, arg)
    result = future.result()
    
    # Or use the convenience wrapper
    result = run_sync_in_executor(sync_function, arg)

Environment Variables
--------------------
- EXECUTOR_MAX_WORKERS: Max threads (default: min(32, cpu_count + 4))
- EXECUTOR_THREAD_NAME_PREFIX: Thread name prefix (default: "SharedExecutor")
"""

import atexit
import concurrent.futures
import logging
import os
import threading
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

# Type variable for generic return type
T = TypeVar("T")

# Module-level state
_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_cleanup_registered = False


def get_executor_config() -> dict:
    """
    Get executor configuration from environment variables.
    
    Returns:
        Dictionary with max_workers and thread_name_prefix
    """
    # Default max_workers matches Python's ThreadPoolExecutor default
    # min(32, os.cpu_count() + 4) - good for I/O bound tasks
    cpu_count = os.cpu_count() or 1
    default_max_workers = min(32, cpu_count + 4)
    
    max_workers_str = os.environ.get("EXECUTOR_MAX_WORKERS", "")
    if max_workers_str:
        try:
            max_workers = int(max_workers_str)
            if max_workers < 1:
                logger.warning(
                    f"EXECUTOR_MAX_WORKERS={max_workers} invalid, using default={default_max_workers}"
                )
                max_workers = default_max_workers
        except ValueError:
            logger.warning(
                f"EXECUTOR_MAX_WORKERS={max_workers_str!r} not an integer, using default={default_max_workers}"
            )
            max_workers = default_max_workers
    else:
        max_workers = default_max_workers
    
    thread_name_prefix = os.environ.get("EXECUTOR_THREAD_NAME_PREFIX", "SharedExecutor")
    
    return {
        "max_workers": max_workers,
        "thread_name_prefix": thread_name_prefix,
    }


def get_executor() -> concurrent.futures.ThreadPoolExecutor:
    """
    Get or create the shared ThreadPoolExecutor.
    
    The executor is created lazily on first use and reused for all subsequent calls.
    Cleanup is automatically registered with ShutdownManager and atexit.
    
    Returns:
        Shared ThreadPoolExecutor instance
    """
    global _executor, _cleanup_registered
    
    if _executor is None:
        with _executor_lock:
            # Double-check locking pattern
            if _executor is None:
                config = get_executor_config()
                _executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=config["max_workers"],
                    thread_name_prefix=config["thread_name_prefix"],
                )
                logger.debug(
                    f"Created shared executor: max_workers={config['max_workers']}, "
                    f"prefix={config['thread_name_prefix']}"
                )
                
                # Register cleanup handlers
                if not _cleanup_registered:
                    _cleanup_registered = True
                    
                    # Always register atexit as fallback
                    atexit.register(shutdown_executor)
                    logger.debug("Registered executor cleanup with atexit")
                    
                    # Try to register with ShutdownManager for graceful async shutdown
                    try:
                        from integration_coworker.shutdown import get_shutdown_manager
                        manager = get_shutdown_manager()
                        manager.register_cleanup(shutdown_executor)
                        logger.debug("Registered executor cleanup with ShutdownManager")
                    except ImportError:
                        pass  # ShutdownManager not available
                    except Exception as e:
                        logger.debug(f"Could not register with ShutdownManager: {e}")
    
    return _executor


def shutdown_executor(wait: bool = True, cancel_futures: bool = False) -> None:
    """
    Shutdown the shared executor.
    
    Safe to call multiple times. After shutdown, get_executor() will create
    a new executor.
    
    Args:
        wait: If True, wait for pending futures to complete (default True)
        cancel_futures: If True, cancel pending futures (default False)
    """
    global _executor, _cleanup_registered
    
    with _executor_lock:
        if _executor is not None:
            try:
                _executor.shutdown(wait=wait, cancel_futures=cancel_futures)
                logger.debug("Shared executor shutdown complete")
            except Exception as e:
                logger.debug(f"Executor shutdown error (safe to ignore): {e}")
            _executor = None
        # Reset registration flag so re-creation will register again
        _cleanup_registered = False


def run_sync_in_executor(
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """
    Run a synchronous function in the shared executor.
    
    This is a convenience wrapper for simple use cases. For async code,
    prefer asyncio.to_thread() which uses the default executor.
    
    Args:
        func: Synchronous function to run
        *args: Positional arguments for func
        **kwargs: Keyword arguments for func
        
    Returns:
        Result of func(*args, **kwargs)
        
    Example:
        result = run_sync_in_executor(requests.get, "https://example.com")
    """
    executor = get_executor()
    if kwargs:
        # ThreadPoolExecutor.submit doesn't support kwargs directly
        future = executor.submit(lambda: func(*args, **kwargs))
    else:
        future = executor.submit(func, *args)
    return future.result()


async def run_in_executor_async(
    func: Callable[..., T],
    *args: Any,
) -> T:
    """
    Run a synchronous function in the shared executor from async context.
    
    PREFER asyncio.to_thread() for Python 3.9+ as it's simpler and uses
    the default executor:
        result = await asyncio.to_thread(func, arg1, arg2)
    
    Use this function when you need to use the shared executor specifically
    (e.g., for consistent thread pool sizing).
    
    Args:
        func: Synchronous function to run
        *args: Positional arguments for func
        
    Returns:
        Result of func(*args)
    """
    import asyncio
    
    loop = asyncio.get_running_loop()
    executor = get_executor()
    return await loop.run_in_executor(executor, func, *args)
