"""Unit tests for progress callbacks module."""

import pytest
import tempfile
import json
from pathlib import Path
from io import StringIO
from unittest.mock import MagicMock, patch

from integration_coworker.progress.callbacks import (
    FileProgressCallback,
    CollectingCallback,
    create_default_callbacks,
)
from integration_coworker.progress.events import ProgressEvent


class TestFileProgressCallback:
    """Tests for FileProgressCallback."""

    def test_write_event_to_file(self):
        """Event is written as JSON line."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            path = Path(f.name)

        try:
            callback = FileProgressCallback(path=path)
            event = ProgressEvent.create(
                run_id="test-123",
                event_type="workflow.start",
                total_nodes=5,
            )

            callback(event)
            callback.close()

            # Read and verify
            with open(path) as f:
                lines = f.readlines()

            assert len(lines) == 1
            parsed = json.loads(lines[0])
            assert parsed["run_id"] == "test-123"
            assert parsed["event_type"] == "workflow.start"
        finally:
            path.unlink(missing_ok=True)

    def test_write_multiple_events(self):
        """Multiple events written as separate lines."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            path = Path(f.name)

        try:
            callback = FileProgressCallback(path=path)

            for i in range(3):
                event = ProgressEvent.create(
                    run_id="test",
                    event_type="node.start",
                    node_index=i,
                    total_nodes=3,
                )
                callback(event)

            callback.close()

            with open(path) as f:
                lines = f.readlines()

            assert len(lines) == 3
        finally:
            path.unlink(missing_ok=True)

    def test_write_to_file_handle(self):
        """Write to provided file handle."""
        buffer = StringIO()
        callback = FileProgressCallback(file=buffer, flush=False)

        event = ProgressEvent.create(
            run_id="test",
            event_type="workflow.start",
        )
        callback(event)

        content = buffer.getvalue()
        assert "workflow.start" in content

    def test_context_manager(self):
        """File is closed after context manager exit."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            path = Path(f.name)

        try:
            with FileProgressCallback(path=path) as callback:
                event = ProgressEvent.create(
                    run_id="test",
                    event_type="workflow.start",
                )
                callback(event)

            # File should be closed, verify we can read it
            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 1
        finally:
            path.unlink(missing_ok=True)

    def test_requires_path_or_file(self):
        """ValueError if neither path nor file provided."""
        with pytest.raises(ValueError, match="Either path or file"):
            FileProgressCallback()


class TestCollectingCallback:
    """Tests for CollectingCallback."""

    def test_collect_events(self):
        """Events are collected in list."""
        callback = CollectingCallback()

        for i in range(3):
            event = ProgressEvent.create(
                run_id="test",
                event_type="node.start",
                node_index=i,
            )
            callback(event)

        assert len(callback.events) == 3

    def test_events_returns_copy(self):
        """events property returns a copy."""
        callback = CollectingCallback()
        event = ProgressEvent.create(run_id="test", event_type="workflow.start")
        callback(event)

        events = callback.events
        events.clear()

        assert len(callback.events) == 1  # Original unchanged

    def test_clear_events(self):
        """Clear removes all events."""
        callback = CollectingCallback()
        for i in range(5):
            event = ProgressEvent.create(run_id="test", event_type="node.start")
            callback(event)

        callback.clear()

        assert len(callback.events) == 0

    def test_max_events_limit(self):
        """Old events dropped when max exceeded."""
        callback = CollectingCallback(max_events=5)

        for i in range(10):
            event = ProgressEvent.create(
                run_id=f"run-{i}",
                event_type="node.start",
            )
            callback(event)

        events = callback.events
        assert len(events) == 5
        # Should have the last 5 events (indices 5-9)
        run_ids = [e.run_id for e in events]
        assert run_ids == ["run-5", "run-6", "run-7", "run-8", "run-9"]

    def test_get_by_type(self):
        """Filter events by type."""
        callback = CollectingCallback()

        callback(ProgressEvent.create(run_id="test", event_type="workflow.start"))
        callback(ProgressEvent.create(run_id="test", event_type="node.start", node_name="a"))
        callback(ProgressEvent.create(run_id="test", event_type="node.end", node_name="a"))
        callback(ProgressEvent.create(run_id="test", event_type="node.start", node_name="b"))
        callback(ProgressEvent.create(run_id="test", event_type="workflow.end"))

        starts = callback.get_by_type("node.start")
        assert len(starts) == 2

        ends = callback.get_by_type("workflow.end")
        assert len(ends) == 1

    def test_get_by_node(self):
        """Filter events by node name."""
        callback = CollectingCallback()

        callback(ProgressEvent.create(run_id="test", event_type="workflow.start"))
        callback(ProgressEvent.create(run_id="test", event_type="node.start", node_name="fetch"))
        callback(ProgressEvent.create(run_id="test", event_type="node.end", node_name="fetch"))
        callback(ProgressEvent.create(run_id="test", event_type="node.start", node_name="parse"))
        callback(ProgressEvent.create(run_id="test", event_type="node.end", node_name="parse"))

        fetch_events = callback.get_by_node("fetch")
        assert len(fetch_events) == 2

        parse_events = callback.get_by_node("parse")
        assert len(parse_events) == 2


class TestCreateDefaultCallbacks:
    """Tests for create_default_callbacks factory."""

    def test_no_rich_no_file(self):
        """Returns empty list when both disabled."""
        callbacks = create_default_callbacks(
            enable_rich=False,
            enable_file=False,
        )

        assert callbacks == []

    def test_file_callback_created(self):
        """File callback created when enabled with path."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as f:
            path = Path(f.name)

        try:
            callbacks = create_default_callbacks(
                enable_rich=False,
                enable_file=True,
                file_path=path,
            )

            assert len(callbacks) == 1
            assert isinstance(callbacks[0], FileProgressCallback)

            # Cleanup
            callbacks[0].close()
        finally:
            path.unlink(missing_ok=True)

    def test_file_not_created_without_path(self):
        """File callback not created if path not provided."""
        callbacks = create_default_callbacks(
            enable_rich=False,
            enable_file=True,
            file_path=None,  # No path
        )

        assert callbacks == []

    @patch("integration_coworker.progress.callbacks.sys.stderr")
    @patch("integration_coworker.progress.callbacks.RICH_AVAILABLE", True)
    def test_rich_not_created_non_tty(self, mock_stderr):
        """Rich callback not created if not TTY."""
        mock_stderr.isatty.return_value = False

        callbacks = create_default_callbacks(
            enable_rich=True,
            enable_file=False,
        )

        # Rich should be skipped when not TTY
        assert callbacks == []
