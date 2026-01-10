"""
Exception Handling Utilities (Production Hardening H-6)

Provides safe exception handling patterns for production use.

⚠️  CRITICAL RULES FOR LONG-RUNNING SERVICES:

1. NEVER swallow these exceptions:
   - KeyboardInterrupt: User-initiated shutdown
   - SystemExit: Programmatic shutdown
   - asyncio.CancelledError: Task cancellation (MUST re-raise in async code)

2. PREFER specific exception types over bare `except Exception:`
   - File operations: (OSError, IOError, PermissionError)
   - JSON parsing: (json.JSONDecodeError, ValueError)
   - YAML parsing: (yaml.YAMLError,)
   - HTTP operations: (httpx.HTTPError, httpx.TimeoutException)
   - Database: (psycopg2.Error, sqlite3.Error)

3. ALWAYS LOG before swallowing:
   - At minimum use logger.debug() for expected failures
   - Use logger.warning() for unexpected but recoverable failures
   - Use logger.error() for failures that affect functionality

This module provides helpers to enforce these patterns.

TEST MODE DETECTION:
--------------------
Use `is_test_mode()` to detect test execution. This checks:
1. INTEGRATION_COWORKER_TESTING=1 (explicit, preferred)
2. PYTEST_CURRENT_TEST (auto-set by pytest as fallback)

Production code should use this for test-only behaviors like:
- Resetting global state (Settings cache, semaphores)
- Suppressing deprecation warnings
- Enabling test-only code paths
"""

import asyncio
import functools
import logging
import os
from typing import Callable, Tuple, Type, TypeVar, Union

logger = logging.getLogger(__name__)

# Type variable for generic function wrapping
T = TypeVar("T")

# ============================================================================
# TEST MODE DETECTION
# ============================================================================

# Environment variable for explicit test mode (preferred)
TEST_MODE_ENV_VAR = "INTEGRATION_COWORKER_TESTING"

# Fallback: pytest auto-sets this
PYTEST_ENV_VAR = "PYTEST_CURRENT_TEST"


def is_test_mode() -> bool:
    """
    Detect if code is running in test context.
    
    Checks (in order of preference):
    1. INTEGRATION_COWORKER_TESTING=1 (explicit, recommended for CI/fixtures)
    2. PYTEST_CURRENT_TEST (auto-set by pytest during test runs)
    
    Returns:
        True if running in test context, False otherwise
        
    Usage:
        if is_test_mode():
            # Reset caches, suppress warnings, etc.
            reset_settings()
            
    Note:
        Prefer setting INTEGRATION_COWORKER_TESTING=1 explicitly in your
        test fixtures or CI environment rather than relying on pytest
        auto-detection. This makes test behavior explicit and debuggable.
    """
    # Check explicit env var first (truthy values)
    explicit = os.environ.get(TEST_MODE_ENV_VAR, "").lower()
    if explicit in ("1", "true", "yes"):
        return True
    
    # Fallback to pytest auto-detection
    if os.environ.get(PYTEST_ENV_VAR):
        return True
    
    return False


def require_test_mode(operation: str = "this operation") -> None:
    """
    Assert that code is running in test mode.
    
    Call this at the start of test-only functions to prevent
    accidental production invocation.
    
    Args:
        operation: Description for error message
        
    Raises:
        RuntimeError: If not in test mode
    """
    if not is_test_mode():
        raise RuntimeError(
            f"{operation} is only allowed in test mode. "
            f"Set {TEST_MODE_ENV_VAR}=1 to enable."
        )


# ============================================================================
# FATAL EXCEPTION HANDLING
# ============================================================================

# Exceptions that must NEVER be swallowed
FATAL_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    KeyboardInterrupt,
    SystemExit,
)

# In async contexts, CancelledError must also not be swallowed
ASYNC_FATAL_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    KeyboardInterrupt,
    SystemExit,
    asyncio.CancelledError,
)


def reraise_fatal(exc: BaseException) -> bool:
    """
    Check if an exception should be re-raised (never swallowed).
    
    Args:
        exc: The exception to check
        
    Returns:
        True if the exception is fatal and must be re-raised
    """
    return isinstance(exc, FATAL_EXCEPTIONS)


def reraise_async_fatal(exc: BaseException) -> bool:
    """
    Check if an exception should be re-raised in async context.
    
    This is stricter than reraise_fatal - also includes CancelledError.
    
    Args:
        exc: The exception to check
        
    Returns:
        True if the exception is fatal and must be re-raised
    """
    return isinstance(exc, ASYNC_FATAL_EXCEPTIONS)


def safe_cleanup(func: Callable[[], None], context: str = "cleanup") -> None:
    """
    Execute a cleanup function, suppressing non-fatal errors.
    
    Use this for cleanup operations where failures should not propagate,
    but fatal errors (KeyboardInterrupt, SystemExit) are still re-raised.
    
    Args:
        func: Cleanup function to execute
        context: Description for logging
        
    Example:
        def cleanup_resources():
            connection.close()
        
        safe_cleanup(cleanup_resources, "database connection")
    """
    try:
        func()
    except BaseException as e:
        if reraise_fatal(e):
            raise
        logger.debug(f"Error during {context}: {e}")


async def async_safe_cleanup(
    func: Callable[[], Union[None, "asyncio.coroutine"]],
    context: str = "cleanup"
) -> None:
    """
    Execute an async cleanup function, suppressing non-fatal errors.
    
    Use this for async cleanup operations where failures should not propagate,
    but fatal errors (KeyboardInterrupt, SystemExit, CancelledError) are re-raised.
    
    Args:
        func: Cleanup function to execute (sync or async)
        context: Description for logging
    """
    try:
        result = func()
        if asyncio.iscoroutine(result):
            await result
    except BaseException as e:
        if reraise_async_fatal(e):
            raise
        logger.debug(f"Error during async {context}: {e}")


# Common exception tuples for specific use cases
FILE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    OSError,
    IOError,
    PermissionError,
    FileNotFoundError,
    IsADirectoryError,
    NotADirectoryError,
)

PARSE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    ValueError,  # Includes JSONDecodeError
    TypeError,
    KeyError,
)

# Add YAML exceptions if available
try:
    import yaml
    YAML_EXCEPTIONS: Tuple[Type[Exception], ...] = (yaml.YAMLError,)
except ImportError:
    YAML_EXCEPTIONS: Tuple[Type[Exception], ...] = ()


def expect_exceptions(
    *exception_types: Type[Exception],
    default: T = None,
    log_level: int = logging.DEBUG,
    context: str = "",
) -> Callable:
    """
    Decorator to handle expected exceptions with proper logging.
    
    Unlike bare `except Exception:`, this:
    1. Never swallows KeyboardInterrupt, SystemExit
    2. Only catches the specified exception types
    3. Logs the exception before returning default
    
    Args:
        *exception_types: Exception types to catch
        default: Value to return on exception
        log_level: Logging level for caught exceptions
        context: Context string for log message
        
    Example:
        @expect_exceptions(OSError, FileNotFoundError, default={})
        def load_config_file(path: str) -> dict:
            with open(path) as f:
                return json.load(f)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except BaseException as e:
                # Always re-raise fatal exceptions
                if reraise_fatal(e):
                    raise
                # Only catch specified types
                if not isinstance(e, exception_types):
                    raise
                # Log and return default
                ctx = f" ({context})" if context else ""
                logger.log(log_level, f"{func.__name__}{ctx}: {type(e).__name__}: {e}")
                return default
        return wrapper
    return decorator


def async_expect_exceptions(
    *exception_types: Type[Exception],
    default: T = None,
    log_level: int = logging.DEBUG,
    context: str = "",
) -> Callable:
    """
    Async version of expect_exceptions decorator.
    
    Also properly handles asyncio.CancelledError (re-raises it).
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            try:
                return await func(*args, **kwargs)
            except BaseException as e:
                # Always re-raise fatal exceptions (including CancelledError)
                if reraise_async_fatal(e):
                    raise
                # Only catch specified types
                if not isinstance(e, exception_types):
                    raise
                # Log and return default
                ctx = f" ({context})" if context else ""
                logger.log(log_level, f"{func.__name__}{ctx}: {type(e).__name__}: {e}")
                return default
        return wrapper
    return decorator
