"""Unit tests for progress emitter module."""

import pytest
import threading
import time
from unittest.mock import MagicMock, patch

from integration_coworker.progress.emitter import (
    ProgressEmitter,
    get_progress_emitter,
    set_progress_emitter,
)
from integration_coworker.progress.events import ProgressEvent


class TestProgressEmitterBasics:
    """Basic emitter functionality tests."""

    def test_register_callback(self):
        """Register a callback successfully."""
        emitter = ProgressEmitter()
        callback = MagicMock()

        result = emitter.register(callback)

        assert result is True
        assert emitter.callback_count == 1

    def test_register_same_callback_twice(self):
        """Registering same callback twice returns False."""
        emitter = ProgressEmitter()
        callback = MagicMock()

        assert emitter.register(callback) is True
        assert emitter.register(callback) is False
        assert emitter.callback_count == 1

    def test_register_max_callbacks(self):
        """Registration fails when max_callbacks reached."""
        emitter = ProgressEmitter(max_callbacks=3)

        for i in range(3):
            assert emitter.register(MagicMock()) is True

        # 4th registration should fail
        assert emitter.register(MagicMock()) is False
        assert emitter.callback_count == 3

    def test_unregister_callback(self):
        """Unregister a registered callback."""
        emitter = ProgressEmitter()
        callback = MagicMock()

        emitter.register(callback)
        result = emitter.unregister(callback)

        assert result is True
        assert emitter.callback_count == 0

    def test_unregister_unknown_callback(self):
        """Unregistering unknown callback returns False."""
        emitter = ProgressEmitter()
        callback = MagicMock()

        result = emitter.unregister(callback)

        assert result is False

    def test_clear_callbacks(self):
        """Clear all callbacks."""
        emitter = ProgressEmitter()

        for _ in range(5):
            emitter.register(MagicMock())

        count = emitter.clear()

        assert count == 5
        assert emitter.callback_count == 0


class TestProgressEmitterEmit:
    """Tests for event emission."""

    def test_emit_calls_callback(self):
        """Emit calls registered callback with event."""
        emitter = ProgressEmitter()
        callback = MagicMock()
        emitter.register(callback)

        event = ProgressEvent.create(
            run_id="test-123",
            event_type="workflow.start",
        )
        emitter.emit(event)

        callback.assert_called_once_with(event)

    def test_emit_calls_multiple_callbacks(self):
        """Emit calls all registered callbacks."""
        emitter = ProgressEmitter()
        callbacks = [MagicMock() for _ in range(3)]
        for cb in callbacks:
            emitter.register(cb)

        event = ProgressEvent.create(
            run_id="test-123",
            event_type="workflow.start",
        )
        emitter.emit(event)

        for cb in callbacks:
            cb.assert_called_once_with(event)

    def test_emit_without_callbacks_succeeds(self):
        """Emit with no callbacks doesn't fail."""
        emitter = ProgressEmitter()
        event = ProgressEvent.create(
            run_id="test-123",
            event_type="workflow.start",
        )

        # Should not raise
        emitter.emit(event)
        assert emitter.events_emitted == 1

    def test_emit_counts_events(self):
        """Emitter tracks event count."""
        emitter = ProgressEmitter()

        for i in range(5):
            event = ProgressEvent.create(
                run_id="test",
                event_type="node.start",
                node_index=i,
            )
            emitter.emit(event)

        assert emitter.events_emitted == 5


class TestProgressEmitterErrorIsolation:
    """Tests for callback error isolation."""

    def test_failing_callback_doesnt_stop_others(self):
        """One failing callback doesn't prevent others from running."""
        emitter = ProgressEmitter()

        failing_callback = MagicMock(side_effect=ValueError("Test error"))
        success_callback = MagicMock()

        emitter.register(failing_callback)
        emitter.register(success_callback)

        event = ProgressEvent.create(
            run_id="test-123",
            event_type="workflow.start",
        )

        # Should not raise
        emitter.emit(event)

        # Both should have been called
        failing_callback.assert_called_once()
        success_callback.assert_called_once()

        # Failure should be tracked
        assert emitter.callbacks_failed == 1

    def test_slow_callback_logged(self):
        """Slow callbacks are tracked."""
        emitter = ProgressEmitter(callback_timeout_ms=10)

        def slow_callback(event):
            time.sleep(0.05)  # 50ms, exceeds 10ms threshold

        emitter.register(slow_callback)

        event = ProgressEvent.create(
            run_id="test",
            event_type="workflow.start",
        )
        emitter.emit(event)

        assert emitter._callbacks_slow >= 1


class TestProgressEmitterThreadSafety:
    """Tests for thread-safe operation."""

    def test_concurrent_registration(self):
        """Concurrent callback registration is safe."""
        emitter = ProgressEmitter(max_callbacks=1000)
        results = []

        def register_callback():
            cb = MagicMock()
            result = emitter.register(cb)
            results.append(result)

        threads = [threading.Thread(target=register_callback) for _ in range(100)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All registrations should succeed
        assert all(results)
        assert emitter.callback_count == 100

    def test_concurrent_emit(self):
        """Concurrent event emission is safe."""
        emitter = ProgressEmitter()
        received = []
        lock = threading.Lock()

        def collecting_callback(event):
            with lock:
                received.append(event)

        emitter.register(collecting_callback)

        def emit_events():
            for i in range(10):
                event = ProgressEvent.create(
                    run_id=f"thread-{threading.current_thread().name}",
                    event_type="node.start",
                    node_index=i,
                )
                emitter.emit(event)

        threads = [threading.Thread(target=emit_events) for _ in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All events should be received
        assert len(received) == 50
        assert emitter.events_emitted == 50


class TestProgressEmitterContextVar:
    """Tests for context variable management."""

    def test_get_progress_emitter_default_none(self):
        """Default context returns None."""
        # Clear any existing
        set_progress_emitter(None)
        assert get_progress_emitter() is None

    def test_set_and_get_progress_emitter(self):
        """Set and retrieve emitter from context."""
        emitter = ProgressEmitter()
        set_progress_emitter(emitter)

        assert get_progress_emitter() is emitter

        # Cleanup
        set_progress_emitter(None)

    def test_context_manager(self):
        """Emitter context manager sets/clears context var."""
        emitter = ProgressEmitter()

        with emitter:
            assert get_progress_emitter() is emitter

        assert get_progress_emitter() is None

    def test_context_manager_clears_callbacks(self):
        """Exiting context manager clears callbacks."""
        emitter = ProgressEmitter()
        emitter.register(MagicMock())

        with emitter:
            assert emitter.callback_count == 1

        # After exit, callbacks should be cleared
        assert emitter.callback_count == 0


class TestProgressEmitterGetCallbacks:
    """Tests for get_callbacks method."""

    def test_get_callbacks_returns_copy(self):
        """get_callbacks returns a copy, not the internal set."""
        emitter = ProgressEmitter()
        cb1 = MagicMock()
        cb2 = MagicMock()
        emitter.register(cb1)
        emitter.register(cb2)

        callbacks = emitter.get_callbacks()

        # Should have both
        assert len(callbacks) == 2
        assert cb1 in callbacks
        assert cb2 in callbacks

        # Modifying returned list shouldn't affect emitter
        callbacks.clear()
        assert emitter.callback_count == 2
