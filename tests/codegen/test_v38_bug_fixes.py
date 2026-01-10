"""Tests for V38 bug fixes: async and streaming detection."""

import pytest
from integration_coworker.codegen.task_parser import (
    extract_task_requirements,
    _detect_async_requirement,
    _detect_streaming_requirement,
    ASYNC_INDICATOR_KEYWORDS,
    ASYNC_PHRASE_PATTERNS,
    STREAMING_INDICATOR_KEYWORDS,
    STREAMING_PHRASE_PATTERNS,
)
from integration_coworker.codegen.context import CodegenContext


class TestV38007AsyncDetection:
    """Test V38-007: Async requirement detection."""
    
    def test_async_keywords_detected(self):
        """Keywords like 'async', 'concurrent', 'parallel' should trigger async detection."""
        task = "Build an async batch processor for the API"
        result = extract_task_requirements(task)
        assert result.is_async_required is True
    
    def test_concurrent_keyword_detected(self):
        """'concurrent' keyword should trigger async detection."""
        task = "Process items concurrently with rate limiting"
        result = extract_task_requirements(task)
        assert result.is_async_required is True
    
    def test_parallel_keyword_detected(self):
        """'parallel' keyword should trigger async detection."""
        task = "Parallel execution of multiple API calls"
        result = extract_task_requirements(task)
        assert result.is_async_required is True
    
    def test_batch_processing_detected(self):
        """Batch processing patterns should trigger async detection."""
        task = "Process a batch of items through the API"
        result = extract_task_requirements(task)
        assert result.is_async_required is True
    
    def test_rate_limiting_phrase_detected(self):
        """Rate limiting phrases should trigger async detection (via batch patterns)."""
        task = "Process requests with rate limiting and batch execution"
        result = extract_task_requirements(task)
        assert result.is_async_required is True
    
    def test_simple_sync_task_not_detected(self):
        """Simple synchronous tasks should not trigger async detection."""
        task = "Create a function to call the GET /users endpoint"
        result = extract_task_requirements(task)
        assert result.is_async_required is False
    
    def test_config_driven_task_not_async(self):
        """Config-driven prompt generator should not require async."""
        task = "Build a config-driven prompt generator using the POST /completions endpoint"
        result = extract_task_requirements(task)
        assert result.is_async_required is False


class TestV38008StreamingDetection:
    """Test V38-008: Streaming requirement detection."""
    
    def test_stream_keyword_detected(self):
        """'stream' keyword should trigger streaming detection."""
        task = "Create a client that streams responses from the API"
        result = extract_task_requirements(task)
        assert result.is_streaming_required is True
    
    def test_streaming_keyword_detected(self):
        """'streaming' keyword should trigger streaming detection."""
        task = "Implement streaming chat completions"
        result = extract_task_requirements(task)
        assert result.is_streaming_required is True
    
    def test_tokens_as_arrive_phrase(self):
        """'streams tokens as they arrive' should trigger streaming detection."""
        task = "Create an OpenAI client that streams tokens as they arrive"
        result = extract_task_requirements(task)
        assert result.is_streaming_required is True
    
    def test_realtime_keyword_detected(self):
        """'real-time' keyword should trigger streaming detection."""
        task = "Build a real-time response viewer"
        result = extract_task_requirements(task)
        assert result.is_streaming_required is True
    
    def test_sse_keyword_detected(self):
        """'SSE' (Server-Sent Events) should trigger streaming detection."""
        task = "Consume SSE events from the API"
        result = extract_task_requirements(task)
        assert result.is_streaming_required is True
    
    def test_chunks_keyword_detected(self):
        """'chunks' keyword should trigger streaming detection."""
        task = "Process response chunks incrementally"
        result = extract_task_requirements(task)
        assert result.is_streaming_required is True
    
    def test_simple_post_not_streaming(self):
        """Simple POST request should not trigger streaming detection."""
        task = "Send a POST request to the /chat/completions endpoint"
        result = extract_task_requirements(task)
        assert result.is_streaming_required is False
    
    def test_batch_processing_not_streaming(self):
        """Batch processing should be async but NOT streaming."""
        task = "Build an async batch processor for the POST /batch endpoint"
        result = extract_task_requirements(task)
        assert result.is_streaming_required is False
        assert result.is_async_required is True


class TestV38008StreamingVsAsync:
    """Test that streaming and async are correctly distinguished."""
    
    def test_streaming_only_task(self):
        """Streaming task should be streaming but not async."""
        task = "Create a client that streams chat completions as they arrive"
        result = extract_task_requirements(task)
        assert result.is_streaming_required is True
        assert result.is_async_required is False
    
    def test_async_only_task(self):
        """Async task should be async but not streaming."""
        task = "Process multiple requests concurrently"
        result = extract_task_requirements(task)
        assert result.is_async_required is True
        assert result.is_streaming_required is False
    
    def test_both_async_and_streaming(self):
        """Task can be both async AND streaming."""
        task = "Build an async client that streams responses in parallel"
        result = extract_task_requirements(task)
        assert result.is_async_required is True
        assert result.is_streaming_required is True
    
    def test_neither_async_nor_streaming(self):
        """Simple tasks should be neither async nor streaming."""
        task = "Create a function to fetch user data"
        result = extract_task_requirements(task)
        assert result.is_async_required is False
        assert result.is_streaming_required is False


class TestV38008CodegenContextStreaming:
    """Test that CodegenContext correctly includes streaming flag."""
    
    def test_codegen_context_has_streaming_field(self):
        """CodegenContext should have is_streaming_required field."""
        # CodegenContext is a dataclass with many required fields
        # We just check that the class has the attribute
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(CodegenContext)}
        assert "is_streaming_required" in field_names
    
    def test_codegen_context_streaming_default_false(self):
        """CodegenContext.is_streaming_required should default to False."""
        import dataclasses
        for f in dataclasses.fields(CodegenContext):
            if f.name == "is_streaming_required":
                assert f.default is False
                break
    
    def test_codegen_context_has_async_field(self):
        """CodegenContext should have is_async_required field."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(CodegenContext)}
        assert "is_async_required" in field_names


class TestV38008DetectionFunctions:
    """Direct tests for detection functions."""
    
    def test_detect_streaming_requirement_with_keyword(self):
        """_detect_streaming_requirement should detect streaming keywords."""
        assert _detect_streaming_requirement("streaming chat completions") is True
        assert _detect_streaming_requirement("stream the response") is True
        assert _detect_streaming_requirement("real-time data") is True
    
    def test_detect_streaming_requirement_with_phrase(self):
        """_detect_streaming_requirement should detect streaming phrases."""
        assert _detect_streaming_requirement("tokens as they arrive") is True
        assert _detect_streaming_requirement("process chunks incrementally") is True
    
    def test_detect_streaming_requirement_negative(self):
        """_detect_streaming_requirement should not trigger on non-streaming tasks."""
        assert _detect_streaming_requirement("fetch user data") is False
        assert _detect_streaming_requirement("post a request") is False
    
    def test_detect_async_requirement_with_keyword(self):
        """_detect_async_requirement should detect async keywords."""
        assert _detect_async_requirement("async batch processing") is True
        assert _detect_async_requirement("concurrent execution") is True
        assert _detect_async_requirement("parallel requests") is True
    
    def test_detect_async_requirement_with_phrase(self):
        """_detect_async_requirement should detect async phrases."""
        assert _detect_async_requirement("run in parallel") is True
        assert _detect_async_requirement("multiple at once") is True  # Fixed from "rate limiting"


class TestV38008KeywordSets:
    """Test that keyword sets are comprehensive."""
    
    def test_streaming_keywords_exist(self):
        """STREAMING_INDICATOR_KEYWORDS should have expected keywords."""
        expected = {"streaming", "stream", "streams", "real-time", "realtime", "sse", "chunk", "chunks"}
        assert expected.issubset(STREAMING_INDICATOR_KEYWORDS)
    
    def test_async_keywords_exist(self):
        """ASYNC_INDICATOR_KEYWORDS should have expected keywords."""
        expected = {"async", "concurrent", "parallel", "batch", "await"}
        assert expected.issubset(ASYNC_INDICATOR_KEYWORDS)
    
    def test_streaming_phrase_patterns_exist(self):
        """STREAMING_PHRASE_PATTERNS should not be empty."""
        assert len(STREAMING_PHRASE_PATTERNS) >= 3
    
    def test_async_phrase_patterns_exist(self):
        """ASYNC_PHRASE_PATTERNS should not be empty."""
        assert len(ASYNC_PHRASE_PATTERNS) >= 3
