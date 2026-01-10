"""
Tests for graph node lifecycle logging (GRAPH_TRACE.jsonl).

These tests verify the Log Schema v1.0 for graph events, including:
- graph.node.start/end/skip/error events
- graph.workflow.start/end/error events
- GRAPH_TRACE.jsonl file writing
- NODE_FAILURE_BUNDLE.<node>.json generation
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integration_coworker.graph.runtime import (
    _log_graph_event,
    _write_graph_trace_line,
    _write_node_failure_bundle,
    _generate_span_id,
    _generate_trace_id,
    _sha256_prefix,
    _get_state_size,
    _get_non_empty_keys,
    set_graph_trace_context,
    clear_graph_trace_context,
    graph_trace_context,
    _graph_run_id,
    _current_node_name,
    _trace_id,
    _span_id,
    _artifacts_dir,
)
from integration_coworker.graph.state import WorkflowState


class TestGraphTraceIds:
    """Test W3C-compatible trace ID generation."""
    
    def test_generate_span_id_format(self):
        """Span ID should be 16 hex characters."""
        span_id = _generate_span_id()
        assert len(span_id) == 16
        assert all(c in "0123456789abcdef" for c in span_id)
    
    def test_generate_trace_id_format(self):
        """Trace ID should be 32 hex characters."""
        trace_id = _generate_trace_id()
        assert len(trace_id) == 32
        assert all(c in "0123456789abcdef" for c in trace_id)
    
    def test_span_ids_are_unique(self):
        """Each call should generate a unique span ID."""
        span_ids = [_generate_span_id() for _ in range(100)]
        assert len(set(span_ids)) == 100
    
    def test_trace_ids_are_unique(self):
        """Each call should generate a unique trace ID."""
        trace_ids = [_generate_trace_id() for _ in range(100)]
        assert len(set(trace_ids)) == 100


class TestSha256Prefix:
    """Test SHA256 prefix computation."""
    
    def test_string_input(self):
        """Should compute SHA256 prefix for string input."""
        result = _sha256_prefix("hello world")
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)
    
    def test_bytes_input(self):
        """Should compute SHA256 prefix for bytes input."""
        result = _sha256_prefix(b"hello world")
        assert len(result) == 16
    
    def test_custom_length(self):
        """Should support custom prefix length."""
        result = _sha256_prefix("test", length=8)
        assert len(result) == 8
    
    def test_consistent_output(self):
        """Same input should produce same output."""
        result1 = _sha256_prefix("test data")
        result2 = _sha256_prefix("test data")
        assert result1 == result2


class TestStateInspection:
    """Test state size and key inspection utilities."""
    
    def test_get_state_size_none(self):
        """Should return 0 for None state."""
        assert _get_state_size(None) == 0
    
    def test_get_non_empty_keys_none(self):
        """Should return empty list for None state."""
        assert _get_non_empty_keys(None) == []
    
    def test_get_non_empty_keys_with_state(self):
        """Should return list of non-empty state keys."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=["spec.yaml"],
            task_description="Test task",
            provider_code="TEST",
        )
        keys = _get_non_empty_keys(state)
        assert "task_description" in keys
        assert "provider_code" in keys
        # Empty lists should be excluded
        assert "endpoints" not in keys or len(state.endpoints) > 0


class TestGraphTraceContext:
    """Test graph trace context management."""
    
    def test_set_and_clear_context(self):
        """Should set and clear context variables."""
        tokens = set_graph_trace_context(
            run_id="test-run-123",
            artifacts_dir="/tmp/test",
            trace_id="abcd1234",
        )
        
        assert _graph_run_id.get() == "test-run-123"
        assert _trace_id.get() == "abcd1234"
        assert _artifacts_dir.get() == "/tmp/test"
        
        clear_graph_trace_context(tokens)
        
        # Context should be cleared
        assert _graph_run_id.get() is None
        assert _artifacts_dir.get() is None
    
    def test_context_manager(self):
        """Should work as context manager."""
        with graph_trace_context("run-456", "/tmp/artifacts"):
            assert _graph_run_id.get() == "run-456"
            assert _artifacts_dir.get() == "/tmp/artifacts"
        
        # Should be cleaned up after context exits
        assert _graph_run_id.get() is None
    
    def test_generates_trace_id_if_not_provided(self):
        """Should generate trace ID if not provided."""
        tokens = set_graph_trace_context(run_id="test-run")
        trace_id = _trace_id.get()
        
        assert trace_id is not None
        assert len(trace_id) == 32
        
        clear_graph_trace_context(tokens)


class TestGraphEventLogging:
    """Test graph event logging function."""
    
    def test_log_event_writes_to_trace_file(self):
        """Should write event to GRAPH_TRACE.jsonl."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with graph_trace_context("test-run", tmpdir, trace_id="test-trace-123"):
                # Set node context via the proper API
                _current_node_name.set("test_node")
                _span_id.set("span123")
                
                _log_graph_event(
                    "graph.node.start",
                    run_id="test-run",
                    duration_ms=100.5,
                )
                
                # Clear node context
                _current_node_name.set(None)
                _span_id.set(None)
            
            # Check trace file was written
            trace_file = Path(tmpdir) / "GRAPH_TRACE.jsonl"
            assert trace_file.exists()
            
            content = trace_file.read_text()
            assert "graph.node.start" in content
            assert "test-run" in content
            
            # Parse and verify structure
            event = json.loads(content.strip().split("\n")[0])
            assert event["event"] == "graph.node.start"
            assert event["run_id"] == "test-run"
            assert event["duration_ms"] == 100.5


class TestGraphTraceFile:
    """Test GRAPH_TRACE.jsonl file writing."""
    
    def test_write_trace_line_creates_file(self):
        """Should create GRAPH_TRACE.jsonl and append lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with graph_trace_context("run-123", tmpdir):
                _write_graph_trace_line('{"event":"test"}')
                _write_graph_trace_line('{"event":"test2"}')
            
            trace_file = Path(tmpdir) / "GRAPH_TRACE.jsonl"
            assert trace_file.exists()
            
            lines = trace_file.read_text().strip().split("\n")
            assert len(lines) == 2
            assert json.loads(lines[0])["event"] == "test"
            assert json.loads(lines[1])["event"] == "test2"
    
    def test_write_trace_line_no_artifacts_dir(self):
        """Should silently skip if no artifacts_dir set."""
        # Clear context
        clear_graph_trace_context(set_graph_trace_context("test"))
        _write_graph_trace_line('{"event":"test"}')  # Should not raise


class TestNodeFailureBundle:
    """Test NODE_FAILURE_BUNDLE generation."""
    
    def test_writes_failure_bundle(self):
        """Should write NODE_FAILURE_BUNDLE.<node>.json on error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with graph_trace_context("run-456", tmpdir):
                error = ValueError("Test error message")
                bundle_path = _write_node_failure_bundle(
                    node_name="test_node",
                    run_id="run-456",
                    error=error,
                )
            
            assert bundle_path is not None
            assert bundle_path.exists()
            assert bundle_path.name == "NODE_FAILURE_BUNDLE.test_node.json"
            
            bundle = json.loads(bundle_path.read_text())
            assert bundle["node_name"] == "test_node"
            assert bundle["run_id"] == "run-456"
            assert bundle["error"]["type"] == "ValueError"
            assert "Test error message" in bundle["error"]["message"]
            assert "stack_hash" in bundle["error"]
            assert "environment" in bundle
    
    def test_failure_bundle_with_state(self):
        """Should include state snapshot in failure bundle."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=["spec.yaml"],
            task_description="Test task",
            run_id="run-789",
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with graph_trace_context("run-789", tmpdir):
                error = RuntimeError("State error")
                bundle_path = _write_node_failure_bundle(
                    node_name="failing_node",
                    run_id="run-789",
                    error=error,
                    state=state,
                )
            
            bundle = json.loads(bundle_path.read_text())
            assert "state_snapshot" in bundle
            assert "non_empty_keys" in bundle["state_snapshot"]
            assert "estimated_size_bytes" in bundle["state_snapshot"]
    
    def test_failure_bundle_no_artifacts_dir(self):
        """Should return None if no artifacts_dir set."""
        # Ensure no artifacts dir is set
        clear_graph_trace_context(set_graph_trace_context("test"))
        
        error = ValueError("Test")
        result = _write_node_failure_bundle("test", "run", error)
        assert result is None


class TestLogSchemaV1:
    """Verify Log Schema v1.0 compliance for graph events."""
    
    def test_required_fields_present(self):
        """All required fields must be present in logged events."""
        logged_events = []
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with graph_trace_context("run-schema-test", tmpdir, trace_id="trace-abc"):
                _log_graph_event(
                    "graph.node.start",
                    run_id="run-schema-test",
                )
            
            trace_file = Path(tmpdir) / "GRAPH_TRACE.jsonl"
            if trace_file.exists():
                for line in trace_file.read_text().strip().split("\n"):
                    logged_events.append(json.loads(line))
        
        if logged_events:
            event = logged_events[0]
            # Required fields per Log Schema v1.0
            required_fields = ["ts", "level", "event", "run_id", "graph_run_id", 
                              "node_name", "trace_id", "span_id", "pid"]
            for field in required_fields:
                assert field in event, f"Required field '{field}' missing from event"
    
    def test_event_taxonomy(self):
        """Events should use fixed taxonomy names."""
        valid_events = {
            "graph.node.start",
            "graph.node.end", 
            "graph.node.skip",
            "graph.node.error",
            "graph.node.timeout",
            "graph.node.checkpoint",
            "graph.workflow.start",
            "graph.workflow.end",
            "graph.workflow.error",
        }
        
        # Test that we can log each event type without error
        with tempfile.TemporaryDirectory() as tmpdir:
            with graph_trace_context("run-tax", tmpdir):
                for event_name in valid_events:
                    _log_graph_event(event_name, run_id="run-tax")
            
            trace_file = Path(tmpdir) / "GRAPH_TRACE.jsonl"
            lines = trace_file.read_text().strip().split("\n")
            logged_events = {json.loads(line)["event"] for line in lines}
            
            assert logged_events == valid_events
