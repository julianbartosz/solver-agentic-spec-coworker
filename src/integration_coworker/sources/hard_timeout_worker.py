"""
Process-based hard timeout worker for file parsing.

This module provides TRUE hard timeout enforcement by running parsing
in a subprocess that can be terminated with SIGTERM/SIGKILL.

Unlike cooperative timeout checks (which require code to call check()),
this can stop:
- Stuck I/O operations
- C-extension processing (e.g., openpyxl)
- Regex catastrophic backtracking
- Any code that doesn't yield to Python

Usage:
    from integration_coworker.sources.hard_timeout_worker import (
        run_with_hard_timeout,
        HardTimeoutConfig,
    )
    
    # Run parsing with hard timeout
    result = run_with_hard_timeout(
        func=parse_function,
        args=(content, uri),
        timeout_seconds=30.0,
    )
    
    # Result is the return value, or raises TimeoutError

Architecture:
    1. Main process pickles the function and args
    2. Subprocess is spawned to execute
    3. Main process waits with timeout
    4. On timeout: SIGTERM → wait grace → SIGKILL
    5. Result is unpickled from subprocess queue

Configuration via environment:
    FILE_HARD_TIMEOUT_MODE=process|cooperative|off
    FILE_HARD_TIMEOUT_SECONDS=30
    FILE_HARD_TIMEOUT_GRACE_SECONDS=5
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import pickle
import signal
import traceback
from dataclasses import dataclass, field
from multiprocessing import Process, Queue
from typing import Any, Callable, Optional, Tuple, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Exceptions
# =============================================================================


class HardTimeoutError(Exception):
    """Raised when a subprocess is killed due to hard timeout."""
    
    def __init__(
        self,
        timeout_seconds: float,
        grace_seconds: float,
        was_killed: bool = False,
    ):
        self.timeout_seconds = timeout_seconds
        self.grace_seconds = grace_seconds
        self.was_killed = was_killed
        
        kill_info = " (SIGKILL after grace period)" if was_killed else ""
        super().__init__(
            f"Hard timeout after {timeout_seconds}s{kill_info}"
        )


class WorkerError(Exception):
    """Raised when the worker subprocess encountered an error."""
    
    def __init__(self, original_error: str, traceback_str: str = ""):
        self.original_error = original_error
        self.traceback_str = traceback_str
        super().__init__(f"Worker error: {original_error}")


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class HardTimeoutConfig:
    """
    Configuration for hard timeout behavior.
    
    Attributes:
        mode: "process" (subprocess), "cooperative" (check-based), or "off"
        timeout_seconds: Maximum time before termination
        grace_seconds: Time after SIGTERM before SIGKILL
        use_spawn: Use "spawn" instead of "fork" (safer for some libs)
    """
    mode: str = "process"  # process, cooperative, off
    timeout_seconds: float = 30.0
    grace_seconds: float = 5.0
    use_spawn: bool = True  # spawn is safer on macOS
    
    @classmethod
    def from_env(cls) -> "HardTimeoutConfig":
        """Load configuration from environment variables."""
        return cls(
            mode=os.getenv("FILE_HARD_TIMEOUT_MODE", "process"),
            timeout_seconds=float(os.getenv("FILE_HARD_TIMEOUT_SECONDS", "30")),
            grace_seconds=float(os.getenv("FILE_HARD_TIMEOUT_GRACE_SECONDS", "5")),
            use_spawn=os.getenv("FILE_HARD_TIMEOUT_USE_SPAWN", "true").lower() == "true",
        )
    
    @property
    def is_enabled(self) -> bool:
        """Check if hard timeout is enabled."""
        return self.mode == "process"


# =============================================================================
# Worker Function (runs in subprocess)
# =============================================================================


def _worker_target(
    result_queue: "Queue[Tuple[bool, Any]]",
    func_bytes: bytes,
    args_bytes: bytes,
    kwargs_bytes: bytes,
) -> None:
    """
    Worker function that runs in subprocess.
    
    Unpickles function and args, calls function, pickles result.
    
    Args:
        result_queue: Queue to put (success, result_or_error) tuple
        func_bytes: Pickled function
        args_bytes: Pickled args tuple
        kwargs_bytes: Pickled kwargs dict
    """
    try:
        # Unpickle inputs
        func = pickle.loads(func_bytes)
        args = pickle.loads(args_bytes)
        kwargs = pickle.loads(kwargs_bytes)
        
        # Call the function
        result = func(*args, **kwargs)
        
        # Put success result
        result_queue.put((True, result))
        
    except Exception as e:
        # Put error info (can't pickle all exceptions, so stringify)
        error_info = {
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }
        result_queue.put((False, error_info))


# =============================================================================
# Main Function
# =============================================================================


def run_with_hard_timeout(
    func: Callable[..., T],
    args: Tuple[Any, ...] = (),
    kwargs: Optional[dict] = None,
    timeout_seconds: Optional[float] = None,
    grace_seconds: Optional[float] = None,
    config: Optional[HardTimeoutConfig] = None,
) -> T:
    """
    Run a function with hard timeout enforcement via subprocess.
    
    This function spawns a subprocess to run `func(*args, **kwargs)`.
    If the subprocess doesn't complete within timeout_seconds, it is
    terminated with SIGTERM. If it doesn't exit within grace_seconds
    after SIGTERM, it is killed with SIGKILL.
    
    Args:
        func: Function to call (must be picklable)
        args: Positional arguments for func
        kwargs: Keyword arguments for func
        timeout_seconds: Override config timeout (seconds)
        grace_seconds: Override config grace period (seconds)
        config: Configuration (defaults to from_env())
        
    Returns:
        Result of func(*args, **kwargs)
        
    Raises:
        HardTimeoutError: If subprocess didn't complete in time
        WorkerError: If subprocess raised an exception
        TypeError: If func or args can't be pickled
        
    Example:
        def slow_parse(content: bytes) -> dict:
            # This might hang on malformed data
            return parse_somehow(content)
        
        try:
            result = run_with_hard_timeout(
                slow_parse,
                args=(large_content,),
                timeout_seconds=30.0,
            )
        except HardTimeoutError:
            logger.error("Parsing timed out!")
    """
    config = config or HardTimeoutConfig.from_env()
    kwargs = kwargs or {}
    
    # Use explicit values or config defaults
    timeout = timeout_seconds if timeout_seconds is not None else config.timeout_seconds
    grace = grace_seconds if grace_seconds is not None else config.grace_seconds
    
    # If hard timeout is disabled, just call directly
    if not config.is_enabled:
        return func(*args, **kwargs)
    
    logger.debug(
        "hard_timeout.starting",
        extra={
            "func": func.__name__,
            "timeout_seconds": timeout,
            "grace_seconds": grace,
        }
    )
    
    # Pickle inputs
    try:
        func_bytes = pickle.dumps(func)
        args_bytes = pickle.dumps(args)
        kwargs_bytes = pickle.dumps(kwargs)
    except (pickle.PicklingError, TypeError) as e:
        raise TypeError(f"Cannot pickle function or arguments: {e}") from e
    
    # Create result queue and process
    # Use "spawn" context for safety (fork can cause issues with threads)
    ctx = multiprocessing.get_context("spawn" if config.use_spawn else "fork")
    result_queue: "Queue[Tuple[bool, Any]]" = ctx.Queue()
    
    process = ctx.Process(
        target=_worker_target,
        args=(result_queue, func_bytes, args_bytes, kwargs_bytes),
    )
    
    process.start()
    
    try:
        # Wait for result with timeout
        process.join(timeout=timeout)
        
        if process.is_alive():
            # Timeout - try graceful termination first
            logger.warning(
                "hard_timeout.terminating",
                extra={
                    "func": func.__name__,
                    "timeout_seconds": timeout,
                    "pid": process.pid,
                }
            )
            
            process.terminate()  # SIGTERM
            process.join(timeout=grace)
            
            was_killed = False
            if process.is_alive():
                # Still running after grace period - force kill
                logger.warning(
                    "hard_timeout.killing",
                    extra={
                        "func": func.__name__,
                        "grace_seconds": grace,
                        "pid": process.pid,
                    }
                )
                process.kill()  # SIGKILL
                process.join(timeout=1.0)  # Final wait
                was_killed = True
            
            raise HardTimeoutError(
                timeout_seconds=timeout,
                grace_seconds=grace,
                was_killed=was_killed,
            )
        
        # Process completed - get result
        if result_queue.empty():
            raise WorkerError("Worker exited without returning result")
        
        success, result = result_queue.get_nowait()
        
        if success:
            logger.debug(
                "hard_timeout.completed",
                extra={"func": func.__name__}
            )
            return result
        else:
            # Worker raised an exception
            error_info = result
            raise WorkerError(
                original_error=f"{error_info['type']}: {error_info['message']}",
                traceback_str=error_info.get('traceback', ''),
            )
            
    finally:
        # Ensure process is cleaned up
        if process.is_alive():
            process.kill()
            process.join(timeout=1.0)


# =============================================================================
# Convenience Wrappers
# =============================================================================


def with_hard_timeout(
    timeout_seconds: float = 30.0,
    grace_seconds: float = 5.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to wrap a function with hard timeout.
    
    Usage:
        @with_hard_timeout(timeout_seconds=30)
        def slow_parse(content: bytes) -> dict:
            return parse_somehow(content)
    
    Note: Decorated function will run in subprocess every time.
    This adds overhead, so use for expensive operations only.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return run_with_hard_timeout(
                func,
                args=args,
                kwargs=kwargs,
                timeout_seconds=timeout_seconds,
                grace_seconds=grace_seconds,
            )
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


# =============================================================================
# Exports
# =============================================================================


__all__ = [
    # Main function
    "run_with_hard_timeout",
    # Decorator
    "with_hard_timeout",
    # Configuration
    "HardTimeoutConfig",
    # Exceptions
    "HardTimeoutError",
    "WorkerError",
]
