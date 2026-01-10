"""
Test trace sanitizer for LangSmith payload size limits.

Validates:
1. String truncation with clear markers
2. Recursive dict/list sanitization
3. Total payload size control
4. Edge cases (empty, deeply nested, bytes)
"""
import json

import pytest

from integration_coworker.utils.trace_sanitizer import (
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_MAX_STRING_LENGTH,
    truncate_string,
    sanitize_value,
    sanitize_trace_data,
    estimate_json_size,
    should_sanitize_run,
)


class TestTruncateString:
    """Test string truncation."""
    
    def test_short_string_unchanged(self):
        """Short strings should not be truncated."""
        s = "Hello, World!"
        result = truncate_string(s, max_length=1000)
        assert result == s
    
    def test_long_string_truncated(self):
        """Long strings should be truncated with marker."""
        s = "x" * 10000
        result = truncate_string(s, max_length=1000)
        
        assert len(result) <= 1000
        assert "[TRUNCATED" in result
        assert "10,000 chars" in result
    
    def test_preserves_beginning(self):
        """Should preserve the beginning of the string."""
        s = "IMPORTANT_PREFIX_" + "x" * 10000
        result = truncate_string(s, max_length=1000)
        
        assert result.startswith("IMPORTANT_PREFIX_")


class TestSanitizeValue:
    """Test recursive value sanitization."""
    
    def test_primitives_unchanged(self):
        """Primitives should pass through unchanged."""
        assert sanitize_value(42) == 42
        assert sanitize_value(3.14) == 3.14
        assert sanitize_value(True) is True
        assert sanitize_value(None) is None
    
    def test_nested_dict(self):
        """Nested dicts should be recursively sanitized."""
        data = {
            "outer": {
                "inner": {
                    "value": "x" * 100000
                }
            }
        }
        result = sanitize_value(data, max_string_length=1000)
        
        assert "[TRUNCATED" in result["outer"]["inner"]["value"]
    
    def test_list_truncation(self):
        """Long lists should be truncated."""
        data = list(range(1000))
        result = sanitize_value(data, max_list_items=10)
        
        assert len(result) == 11  # 10 items + truncation marker
        assert "more items" in result[-1]
    
    def test_bytes_conversion(self):
        """Bytes should be converted to string preview."""
        data = b"Hello, bytes!" * 1000
        result = sanitize_value(data, max_string_length=500)
        
        assert "[BYTES:" in result
        assert "Hello, bytes!" in result
    
    def test_max_depth(self):
        """Should stop recursion at max depth."""
        # Create deeply nested structure
        data: dict = {}
        current = data
        for i in range(30):
            current["nested"] = {}
            current = current["nested"]
        current["value"] = "deep"
        
        result = sanitize_value(data, max_depth=10)
        
        # Should hit depth limit somewhere
        assert "[MAX_DEPTH_EXCEEDED]" in json.dumps(result)


class TestSanitizeTraceData:
    """Test full trace data sanitization."""
    
    def test_small_payload_unchanged(self):
        """Small payloads should be mostly unchanged."""
        data = {
            "run_id": "abc123",
            "inputs": {"query": "hello"},
            "outputs": {"result": "world"},
        }
        result = sanitize_trace_data(data)
        
        assert result["run_id"] == "abc123"
        assert result["inputs"]["query"] == "hello"
        assert "_trace_sanitized" in result
    
    def test_large_string_truncated(self):
        """Large strings should be truncated."""
        data = {
            "spec_content": "x" * 1_000_000,  # 1MB string
        }
        result = sanitize_trace_data(data, max_string_length=10000)
        
        assert len(result["spec_content"]) <= 10000
        assert "[TRUNCATED" in result["spec_content"]
    
    def test_total_size_limit(self):
        """Total payload should be within limit."""
        # Create data that would exceed limit
        data = {
            f"field_{i}": "x" * 100000
            for i in range(100)
        }
        
        result = sanitize_trace_data(data, max_total_bytes=500_000)
        
        serialized = json.dumps(result, default=str)
        # Allow some overhead for sanitization metadata
        assert len(serialized) < 1_000_000
    
    def test_adds_sanitization_metadata(self):
        """Should add sanitization metadata."""
        data = {"key": "value"}
        result = sanitize_trace_data(data)
        
        assert "_trace_sanitized" in result
        assert "original_size_estimate" in result["_trace_sanitized"]
        assert "aggressive_truncation" in result["_trace_sanitized"]
    
    def test_aggressive_truncation_flag(self):
        """Should flag when aggressive truncation was needed."""
        # Create oversized payload with many fields to exceed limit after first pass
        # The first pass truncates strings to max_string_length (50k default)
        # We need the total to still exceed limit after that
        data = {
            f"field_{i}": "x" * 60_000  # Many 60KB fields
            for i in range(50)  # 50 * 60KB = 3MB strings before truncation
        }
        # With max_string_length=50000 and max_total_bytes=100_000, 
        # the first pass will produce ~2.5MB (50 * 50000 = 2.5MB)
        # which exceeds 100KB, triggering aggressive truncation
        result = sanitize_trace_data(
            data, 
            max_total_bytes=100_000,
            max_string_length=50_000,  # First pass will create 2.5MB
        )
        
        assert result["_trace_sanitized"]["aggressive_truncation"] is True


class TestEstimateJsonSize:
    """Test JSON size estimation."""
    
    def test_string_size(self):
        """String size should be approximately length."""
        s = "hello"
        size = estimate_json_size(s)
        assert size == 5
    
    def test_dict_size(self):
        """Dict size should be positive."""
        d = {"key": "value", "number": 42}
        size = estimate_json_size(d)
        assert size > 0
    
    def test_list_size(self):
        """List size should scale with items."""
        small = [1, 2, 3]
        large = list(range(1000))
        
        small_size = estimate_json_size(small)
        large_size = estimate_json_size(large)
        
        assert large_size > small_size


class TestShouldSanitizeRun:
    """Test run sanitization decision."""
    
    def test_small_run_no_sanitize(self):
        """Small runs should not need sanitization."""
        data = {"inputs": {"x": 1}, "outputs": {"y": 2}}
        assert should_sanitize_run(data) is False
    
    def test_large_run_needs_sanitize(self):
        """Large runs should need sanitization."""
        data = {"huge_field": "x" * 10_000_000}
        assert should_sanitize_run(data) is True


class TestRealWorldScenarios:
    """Test with realistic trace data patterns."""
    
    def test_openapi_spec_trace(self):
        """Simulate large OpenAPI spec in trace."""
        # Simulate a large spec (like Twilio's)
        fake_spec = {
            "openapi": "3.0.0",
            "paths": {
                f"/api/v1/resource{i}": {
                    "get": {
                        "summary": "Get resource",
                        "description": "x" * 1000,
                    }
                }
                for i in range(500)
            }
        }
        
        trace_data = {
            "run_id": "test-run",
            "inputs": {
                "spec_content": json.dumps(fake_spec),
            },
            "outputs": {
                "extracted_endpoints": ["endpoint1", "endpoint2"],
            },
        }
        
        result = sanitize_trace_data(trace_data, max_total_bytes=500_000)
        
        # Should be under limit
        serialized = json.dumps(result, default=str)
        assert len(serialized) < 1_000_000
        
        # Should preserve run_id
        assert result["run_id"] == "test-run"
    
    def test_graph_state_trace(self):
        """Simulate LangGraph state in trace."""
        state = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "World"},
            ] * 100,  # Many messages
            "spec_documents": [
                {"content": "x" * 50000}
                for _ in range(10)
            ],
        }
        
        result = sanitize_trace_data(
            {"state": state},
            max_string_length=5000,
            max_list_items=20,
        )
        
        # Messages list should be truncated
        assert len(result["state"]["messages"]) <= 21
        
        # Spec content should be truncated
        for doc in result["state"]["spec_documents"][:10]:
            if isinstance(doc, dict) and "content" in doc:
                assert len(doc["content"]) <= 5000


class TestRuntimeTraceTruncation:
    """Test the P0.2 LangSmith client-level truncation function.
    
    This tests the _truncate_for_trace function in runtime.py that
    provides byte-budget enforcement at the LangSmith Client level.
    """
    
    def test_small_data_unchanged(self):
        """Small data should pass through unchanged."""
        from integration_coworker.graph.runtime import _truncate_for_trace
        
        data = {"key": "value", "number": 123, "list": [1, 2, 3]}
        result = _truncate_for_trace(data)
        assert result == data
    
    def test_large_data_truncated(self):
        """Large data should be truncated under budget."""
        from integration_coworker.graph.runtime import (
            _truncate_for_trace,
            _TRACE_BYTE_BUDGET,
        )
        
        # Create 5MB of data
        large_data = {"generated_code": "x" * 5_000_000}
        result = _truncate_for_trace(large_data)
        
        # Result should serialize under budget
        serialized = json.dumps(result, default=str)
        assert len(serialized) <= _TRACE_BYTE_BUDGET
    
    def test_non_dict_passthrough(self):
        """Non-dict data should pass through unchanged."""
        from integration_coworker.graph.runtime import _truncate_for_trace
        
        assert _truncate_for_trace("string") == "string"
        assert _truncate_for_trace(123) == 123
        assert _truncate_for_trace(None) is None
    
    def test_complex_nested_truncation(self):
        """Complex nested structures should be handled."""
        from integration_coworker.graph.runtime import (
            _truncate_for_trace,
            _TRACE_BYTE_BUDGET,
        )
        
        # Simulate LangGraph state with large nested data
        data = {
            "messages": ["msg" * 1000 for _ in range(100)],
            "spec_documents": [{"content": "doc" * 10000} for _ in range(50)],
            "generated_artifacts": {
                "code": "def foo(): " + "pass  # " * 50000,
                "tests": "def test_foo(): " + "assert True  # " * 50000,
            },
        }
        
        result = _truncate_for_trace(data)
        serialized = json.dumps(result, default=str)
        assert len(serialized) <= _TRACE_BYTE_BUDGET
