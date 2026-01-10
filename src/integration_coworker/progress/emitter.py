"""Progress event emitter with thread-safe callback management.

This module provides the ProgressEmitter class that manages progress event
callbacks with:

1. Thread-safe registration/unregistration
2. Error isolation (one failing callback doesn't affect others)
3. Context variable for workflow-scoped access
4. Bounded queue for backpressure (optional)
"""

from contextvars import ContextVar
from typing import Protocol, Callable, Optional, List, Set
import threading
import logging
import time
import os

from integration_coworker.progress.events import ProgressEvent

logger = logging.getLogger(__name__)


class ProgressCallback(Protocol):
    """Protocol defining the callback signature for progress events.

    Callbacks receive a single ProgressEvent and should return quickly.
    Long-running callbacks will be logged as warnings.
    """

    def __call__(self, event: ProgressEvent) -> None:
        """Handle a progress event.

        Args:
            event: The progress event to handle.

        Note:
            Implementations should not raise exceptions. If they do,
            the exception will be logged but not propagated.
        """
        ...


# Type alias for callback functions (same signature as Protocol)
ProgressCallbackFn = Callable[[ProgressEvent], None]

# Context variable for workflow-scoped emitter access
_progress_emitter_var: ContextVar[Optional["ProgressEmitter"]] = ContextVar(
    "progress_emitter", default=None
)


def get_progress_emitter() -> Optional["ProgressEmitter"]:
    """Get the current workflow's progress emitter.

    Returns:
        The ProgressEmitter for the current context, or None if not set.

    Example:
        >>> emitter = get_progress_emitter()
        >>> if emitter:
        ...     emitter.emit(event)
    """
    return _progress_emitter_var.get()


def set_progress_emitter(emitter: Optional["ProgressEmitter"]) -> None:
    """Set the progress emitter for the current workflow context.

    Args:
        emitter: The ProgressEmitter to use, or None to clear.

    Note:
        This should be called at workflow start and cleared at workflow end.
    """
    _progress_emitter_var.set(emitter)


class ProgressEmitter:
    """Thread-safe progress event emitter with callback management.

    The emitter maintains a set of registered callbacks and dispatches
    progress events to all of them. Callback failures are isolated and
    logged but do not affect other callbacks or the workflow.

    Attributes:
        callback_timeout_ms: Maximum time (ms) to wait for a callback.
        max_callbacks: Maximum number of registered callbacks.

    Example:
        >>> emitter = ProgressEmitter()
        >>> events_received = []
        >>> emitter.register(events_received.append)
        >>> emitter.emit(ProgressEvent.create(
        ...     run_id="test-123",
        ...     event_type="workflow.start",
        ... ))
        >>> len(events_received)
        1
    """

    def __init__(
        self,
        callback_timeout_ms: Optional[float] = None,
        max_callbacks: int = 100,
    ) -> None:
        """Initialize the progress emitter.

        Args:
            callback_timeout_ms: Maximum time to wait for each callback.
                Defaults to PROGRESS_CALLBACK_TIMEOUT_MS env var or 100ms.
            max_callbacks: Maximum number of callbacks to register.
        """
        self._callbacks: Set[ProgressCallbackFn] = set()
        self._lock = threading.RLock()  # Reentrant for nested calls

        # Configuration with env var fallback
        self._callback_timeout_ms = callback_timeout_ms or float(
            os.getenv("PROGRESS_CALLBACK_TIMEOUT_MS", "100")
        )
        self._max_callbacks = max_callbacks

        # Metrics
        self._events_emitted = 0
        self._callbacks_failed = 0
        self._callbacks_slow = 0

    @property
    def callback_count(self) -> int:
        """Number of currently registered callbacks."""
        with self._lock:
            return len(self._callbacks)

    @property
    def events_emitted(self) -> int:
        """Total number of events emitted."""
        return self._events_emitted

    @property
    def callbacks_failed(self) -> int:
        """Total number of callback failures."""
        return self._callbacks_failed

    def register(self, callback: ProgressCallbackFn) -> bool:
        """Register a callback to receive progress events.

        Args:
            callback: Function to call with each ProgressEvent.

        Returns:
            True if registered successfully, False if already registered
            or max_callbacks limit reached.

        Example:
            >>> emitter = ProgressEmitter()
            >>> emitter.register(print)
            True
            >>> emitter.register(print)  # Already registered
            False
        """
        with self._lock:
            if callback in self._callbacks:
                logger.debug("Callback already registered: %s", callback)
                return False

            if len(self._callbacks) >= self._max_callbacks:
                logger.warning(
                    "Max callbacks (%d) reached, rejecting registration",
                    self._max_callbacks,
                )
                return False

            self._callbacks.add(callback)
            logger.debug(
                "Registered progress callback: %s (total: %d)",
                callback,
                len(self._callbacks),
            )
            return True

    def unregister(self, callback: ProgressCallbackFn) -> bool:
        """Unregister a callback.

        Args:
            callback: The callback to remove.

        Returns:
            True if removed, False if not found.
        """
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.discard(callback)
                logger.debug("Unregistered progress callback: %s", callback)
                return True
            return False

    def clear(self) -> int:
        """Remove all registered callbacks.

        Returns:
            Number of callbacks that were removed.
        """
        with self._lock:
            count = len(self._callbacks)
            self._callbacks.clear()
            logger.debug("Cleared %d progress callbacks", count)
            return count

    def emit(self, event: ProgressEvent) -> None:
        """Emit a progress event to all registered callbacks.

        This method is thread-safe and isolates callback failures.
        Slow callbacks (exceeding callback_timeout_ms) are logged as warnings.

        Args:
            event: The progress event to emit.

        Note:
            Exceptions in callbacks are caught, logged, and do not propagate.
        """
        with self._lock:
            callbacks = list(self._callbacks)

        if not callbacks:
            self._events_emitted += 1
            return

        self._events_emitted += 1

        for callback in callbacks:
            self._invoke_callback(callback, event)

    def _invoke_callback(
        self, callback: ProgressCallbackFn, event: ProgressEvent
    ) -> None:
        """Invoke a single callback with error isolation and timing.

        Args:
            callback: The callback to invoke.
            event: The event to pass to the callback.
        """
        start_time = time.monotonic()
        try:
            callback(event)
        except Exception as e:
            self._callbacks_failed += 1
            logger.warning(
                "Progress callback %s failed for event %s: %s",
                callback,
                event.event_type,
                e,
                exc_info=True,
            )
        finally:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            if elapsed_ms > self._callback_timeout_ms:
                self._callbacks_slow += 1
                logger.warning(
                    "Progress callback %s took %.1fms (threshold: %.1fms)",
                    callback,
                    elapsed_ms,
                    self._callback_timeout_ms,
                )

    def get_callbacks(self) -> List[ProgressCallbackFn]:
        """Get a copy of the current callback list.

        Returns:
            List of registered callbacks (copy, not reference).
        """
        with self._lock:
            return list(self._callbacks)

    def __enter__(self) -> "ProgressEmitter":
        """Context manager entry - sets this emitter in context var."""
        set_progress_emitter(self)
        return self

    def __exit__(self, *args) -> None:
        """Context manager exit - clears context var and callbacks."""
        set_progress_emitter(None)
        self.clear()
