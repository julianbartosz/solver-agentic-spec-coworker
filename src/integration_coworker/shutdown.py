"""
Graceful shutdown handling for long-running production deployment.

Production Readiness v4 - P0-4:
Platform-aware signal handling with proper async cleanup.

V24-003: Added atexit handler for emergency cleanup.
When the process exits (including SIGKILL after timeout -k), the atexit
handler runs synchronous cleanup callbacks to ensure resources are released.

Platform constraints:
- Unix: loop.add_signal_handler() (requires main thread)
- Windows: signal.signal() + call_soon_threadsafe (NotImplementedError for loop handlers)

Thread constraint:
- Signal handlers must be registered from main thread (Python limitation)
"""

import asyncio
import atexit
import logging
import signal
import sys
import threading
from contextlib import asynccontextmanager
from typing import Callable, List, Optional, Set

logger = logging.getLogger(__name__)


class ShutdownManager:
    """
    Manages graceful shutdown for async workflows.
    
    Production Readiness v4 - P0-4:
    Coordinates shutdown across:
    - Signal handlers (SIGTERM, SIGINT)
    - Async checkpointer cleanup
    - Running task cancellation
    - Database connection closure
    
    Thread Safety:
    - shutdown_event is thread-safe (asyncio.Event)
    - Cleanup callbacks executed in event loop thread
    
    Usage:
        manager = ShutdownManager()
        await manager.setup()
        
        # Register cleanup callbacks
        manager.register_cleanup(async_cleanup_func)
        
        # Check shutdown in loops
        while not manager.is_shutdown_requested():
            await do_work()
        
        # Or await shutdown
        await manager.wait_for_shutdown()
    """
    
    def __init__(self):
        self._shutdown_event: Optional[asyncio.Event] = None
        self._cleanup_callbacks: List[Callable] = []
        self._running_tasks: Set[asyncio.Task] = set()
        self._setup_complete = False
        self._is_main_thread = threading.current_thread() is threading.main_thread()
        self._original_handlers: dict = {}
    
    @property
    def shutdown_event(self) -> asyncio.Event:
        """Get the shutdown event, creating if needed."""
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()
        return self._shutdown_event
    
    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested (non-blocking)."""
        if self._shutdown_event is None:
            return False
        return self._shutdown_event.is_set()
    
    async def wait_for_shutdown(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for shutdown signal.
        
        Args:
            timeout: Max seconds to wait (None = forever)
            
        Returns:
            True if shutdown was signaled, False if timeout
        """
        try:
            await asyncio.wait_for(self.shutdown_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
    
    def request_shutdown(self) -> None:
        """Request graceful shutdown. Thread-safe."""
        if self._shutdown_event is not None and not self._shutdown_event.is_set():
            logger.info("Shutdown requested")
            # Thread-safe: Event.set() is safe from any thread
            self._shutdown_event.set()
    
    def register_cleanup(self, callback: Callable) -> None:
        """
        Register an async cleanup callback to run during shutdown.
        
        Args:
            callback: Async function to call during cleanup
        """
        self._cleanup_callbacks.append(callback)
    
    def track_task(self, task: asyncio.Task) -> None:
        """Track a task for cancellation during shutdown."""
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)
    
    async def setup(self) -> None:
        """
        Setup signal handlers for graceful shutdown.
        
        Platform constraints:
        - Unix: loop.add_signal_handler() (requires main thread)
        - Windows: signal.signal() + call_soon_threadsafe
        
        Thread constraint:
        - Must be called from main thread for signal handling
        """
        if self._setup_complete:
            return
        
        # Ensure event exists
        _ = self.shutdown_event
        
        loop = asyncio.get_running_loop()
        
        if sys.platform != "win32":
            # Unix: use loop signal handlers
            await self._setup_unix_handlers(loop)
        else:
            # Windows: use signal.signal with call_soon_threadsafe
            self._setup_windows_handlers(loop)
        
        self._setup_complete = True
        logger.debug(f"Shutdown manager setup complete (main_thread={self._is_main_thread})")
    
    async def _setup_unix_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """Setup Unix signal handlers using loop.add_signal_handler()."""
        def _signal_handler():
            self.request_shutdown()
        
        signals_to_handle = (signal.SIGTERM, signal.SIGINT)
        
        for sig in signals_to_handle:
            try:
                loop.add_signal_handler(sig, _signal_handler)
                self._original_handlers[sig] = None  # Mark as registered
                logger.debug(f"Registered {sig.name} handler")
            except ValueError as e:
                # "signal only works in main thread" - common in pytest/subprocess
                logger.warning(
                    f"Cannot register {sig.name} handler (not main thread?): {e}"
                )
            except RuntimeError as e:
                # Closed event loop or other issues
                logger.warning(f"Cannot register {sig.name} handler: {e}")
    
    def _setup_windows_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """Setup Windows signal handlers using signal.signal()."""
        def _win_handler(signum, frame):
            # Thread-safe: call_soon_threadsafe schedules in the event loop
            loop.call_soon_threadsafe(self.request_shutdown)
        
        try:
            self._original_handlers[signal.SIGTERM] = signal.signal(
                signal.SIGTERM, _win_handler
            )
            self._original_handlers[signal.SIGINT] = signal.signal(
                signal.SIGINT, _win_handler
            )
            logger.debug("Windows signal handlers registered")
        except (ValueError, OSError) as e:
            logger.warning(f"Cannot register Windows signal handlers: {e}")
    
    async def cleanup(self, timeout: float = 5.0) -> None:
        """
        Run cleanup callbacks and cancel tracked tasks.
        
        Args:
            timeout: Max seconds to wait for cleanup (default 5s)
        """
        logger.info("Starting graceful shutdown cleanup")
        
        # Cancel tracked tasks
        if self._running_tasks:
            logger.debug(f"Cancelling {len(self._running_tasks)} tracked tasks")
            for task in list(self._running_tasks):
                if not task.done():
                    task.cancel()
            
            # Wait for cancellation with timeout
            if self._running_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*self._running_tasks, return_exceptions=True),
                        timeout=timeout / 2,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Some tasks did not cancel in time")
        
        # Run cleanup callbacks
        for callback in self._cleanup_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await asyncio.wait_for(callback(), timeout=timeout / 4)
                else:
                    callback()
            except asyncio.TimeoutError:
                logger.warning(f"Cleanup callback {callback.__name__} timed out")
            except Exception as e:
                logger.warning(f"Cleanup callback {callback.__name__} failed: {e}")
        
        # Restore original signal handlers (Windows only)
        self._restore_handlers()
        
        logger.info("Graceful shutdown cleanup complete")
    
    def _restore_handlers(self) -> None:
        """Restore original signal handlers."""
        if sys.platform == "win32":
            for sig, original in self._original_handlers.items():
                if original is not None:
                    try:
                        signal.signal(sig, original)
                    except (ValueError, OSError):
                        pass


# Global singleton for shared use
_shutdown_manager: Optional[ShutdownManager] = None


def get_shutdown_manager() -> ShutdownManager:
    """Get the global shutdown manager singleton."""
    global _shutdown_manager
    if _shutdown_manager is None:
        _shutdown_manager = ShutdownManager()
    return _shutdown_manager


def reset_shutdown_manager() -> None:
    """Reset the global shutdown manager (for testing)."""
    global _shutdown_manager
    if _shutdown_manager is not None:
        # Clear state but don't run cleanup
        _shutdown_manager._shutdown_event = None
        _shutdown_manager._cleanup_callbacks.clear()
        _shutdown_manager._running_tasks.clear()
        _shutdown_manager._setup_complete = False
    _shutdown_manager = None


@asynccontextmanager
async def shutdown_context(cleanup_timeout: float = 5.0):
    """
    Async context manager for graceful shutdown handling.
    
    Production Readiness v4 - P0-4:
    Wraps workflow execution with proper shutdown handling.
    
    Usage:
        async with shutdown_context() as manager:
            # Register cleanup
            manager.register_cleanup(cleanup_db)
            
            # Run workflow
            await run_workflow(state)
    """
    manager = get_shutdown_manager()
    await manager.setup()
    
    try:
        yield manager
    finally:
        await manager.cleanup(timeout=cleanup_timeout)


async def setup_shutdown_handlers() -> ShutdownManager:
    """
    Setup signal handlers for graceful shutdown (convenience function).
    
    Returns the shutdown manager for checking shutdown status.
    """
    manager = get_shutdown_manager()
    await manager.setup()
    return manager


def is_shutdown_requested() -> bool:
    """Check if shutdown has been requested (convenience function)."""
    manager = get_shutdown_manager()
    return manager.is_shutdown_requested()


async def graceful_shutdown(timeout: float = 5.0) -> None:
    """Run graceful shutdown cleanup (convenience function)."""
    manager = get_shutdown_manager()
    await manager.cleanup(timeout=timeout)


# =============================================================================
# V24-003: atexit cleanup for emergency shutdown
# =============================================================================

_atexit_registered = False
_atexit_cleanup_ran = False


def _atexit_cleanup_sync():
    """
    Emergency synchronous cleanup on process exit.
    
    V24-003: Called by atexit when Python exits, including after SIGKILL
    from `timeout -k`. Runs synchronous cleanup callbacks only.
    
    Note: Async callbacks cannot be reliably run in atexit handlers
    because the event loop may be closed or in an undefined state.
    """
    global _atexit_cleanup_ran
    if _atexit_cleanup_ran:
        return  # Already ran
    _atexit_cleanup_ran = True
    
    global _shutdown_manager
    if _shutdown_manager is None:
        return
    
    logger.debug("V24-003: atexit cleanup triggered")
    
    # Run only synchronous callbacks
    for callback in _shutdown_manager._cleanup_callbacks:
        try:
            if not asyncio.iscoroutinefunction(callback):
                callback()
        except Exception as e:
            # Log but don't re-raise in atexit
            try:
                logger.warning(f"V24-003: atexit cleanup callback failed: {e}")
            except Exception:
                pass  # Logging may fail during shutdown
    
    logger.info("V24-003: atexit cleanup completed")


def register_atexit_cleanup():
    """
    Register the atexit cleanup handler.
    
    Call this early in application startup to ensure cleanup runs
    even on forced termination (SIGKILL after timeout -k).
    
    Safe to call multiple times - only registers once.
    """
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(_atexit_cleanup_sync)
        _atexit_registered = True
        logger.debug("V24-003: atexit cleanup handler registered")


# Auto-register on module import
register_atexit_cleanup()
