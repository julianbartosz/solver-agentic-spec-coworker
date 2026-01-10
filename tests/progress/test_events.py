"""Unit tests for progress event module."""

import pytest
from datetime import datetime, timezone
import json

from integration_coworker.progress.events import (
    ProgressEvent,
    ProgressEventType,
    PROGRESS_EVENT_TYPES,
)


class TestProgressEventCreation:
    """Tests for ProgressEvent.create() factory method."""

    def test_create_workflow_start_event(self):
        """Create a workflow.start event with all defaults."""
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="workflow.start",
            total_nodes=10,
        )

        assert event.run_id == "test-run-123"
        assert event.event_type == "workflow.start"
        assert event.total_nodes == 10
        assert event.progress_pct == 0.0
        assert event.node_name is None
        assert event.node_index is None
        assert event.message == "Workflow started"
        assert event.error is None

    def test_create_workflow_end_event(self):
        """Create a workflow.end event."""
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="workflow.end",
            total_nodes=10,
            duration_ms=5000.0,
        )

        assert event.event_type == "workflow.end"
        assert event.duration_ms == 5000.0
        assert event.message == "Workflow completed"

    def test_create_workflow_error_event(self):
        """Create a workflow.error event with error message."""
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="workflow.error",
            error="Connection timeout",
        )

        assert event.event_type == "workflow.error"
        assert event.error == "Connection timeout"
        assert event.message == "Workflow failed"

    def test_create_node_start_event(self):
        """Create a node.start event with progress calculation."""
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="node.start",
            node_name="fetch_spec",
            node_index=2,
            total_nodes=10,
        )

        assert event.node_name == "fetch_spec"
        assert event.node_index == 2
        # Progress at start of node 2 (0-indexed) = 2/10 = 0.2
        assert event.progress_pct == pytest.approx(0.2)
        assert event.message == "Node 'fetch_spec' start"

    def test_create_node_end_event(self):
        """Create a node.end event with progress calculation."""
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="node.end",
            node_name="fetch_spec",
            node_index=2,
            total_nodes=10,
            duration_ms=1500.0,
        )

        # Progress at end of node 2 (0-indexed) = 3/10 = 0.3
        assert event.progress_pct == pytest.approx(0.3)
        assert event.duration_ms == 1500.0
        assert event.message == "Node 'fetch_spec' end"

    def test_create_node_skip_event(self):
        """Create a node.skip event."""
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="node.skip",
            node_name="optional_step",
            node_index=5,
            total_nodes=10,
        )

        assert event.event_type == "node.skip"
        assert event.message == "Node 'optional_step' skip"

    def test_create_node_error_event(self):
        """Create a node.error event."""
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="node.error",
            node_name="api_call",
            node_index=3,
            total_nodes=10,
            error="Rate limit exceeded",
        )

        assert event.event_type == "node.error"
        assert event.error == "Rate limit exceeded"
        assert event.message == "Node 'api_call' error"

    def test_create_node_timeout_event(self):
        """Create a node.timeout event."""
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="node.timeout",
            node_name="slow_operation",
            node_index=7,
            total_nodes=10,
        )

        assert event.event_type == "node.timeout"
        assert event.message == "Node 'slow_operation' timeout"

    def test_create_with_metadata(self):
        """Create event with custom metadata."""
        metadata = {"retry_count": 3, "source": "cache"}
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="node.end",
            node_name="fetch",
            node_index=1,
            total_nodes=5,
            metadata=metadata,
        )

        assert event.metadata == metadata

    def test_create_with_custom_message(self):
        """Create event with custom message override."""
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="node.start",
            node_name="analysis",
            message="Analyzing OpenAPI spec v3.0",
        )

        assert event.message == "Analyzing OpenAPI spec v3.0"

    def test_timestamp_is_iso_format(self):
        """Verify timestamp is ISO 8601 format."""
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="workflow.start",
        )

        # Should parse as ISO format
        parsed = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
        assert parsed is not None

    def test_progress_clamped_to_bounds(self):
        """Verify progress is clamped to [0.0, 1.0]."""
        # This shouldn't happen in practice, but ensure bounds
        event = ProgressEvent.create(
            run_id="test-run-123",
            event_type="node.end",
            node_index=100,  # Out of bounds
            total_nodes=10,
        )

        assert event.progress_pct == 1.0  # Clamped to max


class TestProgressEventValidation:
    """Tests for event validation."""

    def test_invalid_event_type_raises(self):
        """Invalid event_type should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid event_type"):
            ProgressEvent(
                run_id="test",
                event_type="invalid.type",  # type: ignore
                timestamp="2025-01-01T00:00:00Z",
            )

    def test_all_event_types_valid(self):
        """All PROGRESS_EVENT_TYPES should be valid."""
        for event_type in PROGRESS_EVENT_TYPES:
            event = ProgressEvent.create(
                run_id="test",
                event_type=event_type,  # type: ignore
            )
            assert event.event_type == event_type


class TestProgressEventSerialization:
    """Tests for event serialization."""

    def test_to_dict_excludes_none(self):
        """to_dict() should exclude None values."""
        event = ProgressEvent.create(
            run_id="test-123",
            event_type="workflow.start",
        )
        d = event.to_dict()

        assert "run_id" in d
        assert "event_type" in d
        assert "timestamp" in d
        assert "node_name" not in d  # None excluded
        assert "error" not in d  # None excluded

    def test_to_json_compact(self):
        """to_json() without indent produces compact output."""
        event = ProgressEvent.create(
            run_id="test",
            event_type="workflow.start",
        )
        json_str = event.to_json()

        # Should be single line (no newlines except possibly at end)
        assert "\n" not in json_str.strip()
        # Should be valid JSON
        parsed = json.loads(json_str)
        assert parsed["run_id"] == "test"

    def test_to_json_indented(self):
        """to_json(indent=2) produces formatted output."""
        event = ProgressEvent.create(
            run_id="test",
            event_type="workflow.start",
        )
        json_str = event.to_json(indent=2)

        # Should have newlines
        assert "\n" in json_str

    def test_from_dict_roundtrip(self):
        """Event should roundtrip through dict."""
        original = ProgressEvent.create(
            run_id="test-123",
            event_type="node.start",
            node_name="fetch",
            node_index=2,
            total_nodes=10,
            metadata={"key": "value"},
        )

        d = original.to_dict()
        restored = ProgressEvent.from_dict(d)

        assert restored.run_id == original.run_id
        assert restored.event_type == original.event_type
        assert restored.node_name == original.node_name
        assert restored.metadata == original.metadata

    def test_from_json_roundtrip(self):
        """Event should roundtrip through JSON."""
        original = ProgressEvent.create(
            run_id="test-456",
            event_type="workflow.end",
            duration_ms=10000.0,
        )

        json_str = original.to_json()
        restored = ProgressEvent.from_json(json_str)

        assert restored.run_id == original.run_id
        assert restored.event_type == original.event_type
        assert restored.duration_ms == original.duration_ms


class TestProgressEventImmutability:
    """Tests for event immutability."""

    def test_event_is_frozen(self):
        """ProgressEvent should be immutable (frozen dataclass)."""
        event = ProgressEvent.create(
            run_id="test",
            event_type="workflow.start",
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            event.run_id = "modified"  # type: ignore

    def test_event_is_hashable(self):
        """Immutable events should be hashable (for sets)."""
        event1 = ProgressEvent(
            run_id="test",
            event_type="workflow.start",
            timestamp="2025-01-01T00:00:00Z",
        )
        event2 = ProgressEvent(
            run_id="test",
            event_type="workflow.start",
            timestamp="2025-01-01T00:00:00Z",
        )

        # Same values should hash the same
        assert hash(event1) == hash(event2)

        # Can be added to set
        s = {event1, event2}
        assert len(s) == 1
