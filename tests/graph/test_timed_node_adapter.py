"""Tests for timed_node call adapter, HITL invariants, and finally block cleanup.

This module tests the refactored 2-wrapper design:
- Call adapter handles positional and kwarg config/runtime injection
- HITL-exempt nodes cannot be wrapped with timed_node
- Async wrapper resumes HITL clock on all exit paths (success, error, timeout, cancel)
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from integration_coworker.graph.runtime import (
    timed_node,
    _build_call_adapter,
    _is_tracing_enabled,
)
from integration_coworker.graph.bounds import HITL_EXEMPT_NODES


# =============================================================================
# Test fixtures
# =============================================================================

@dataclass
class MockWorkflowState:
    """Minimal mock state for testing."""
    run_id: str = "test-run"
    node_timings: Dict[str, float] = field(default_factory=dict)
    completed_steps: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    options: Optional[Any] = None


# =============================================================================
# TestCallAdapter: signature-aware arg/kwarg handling
# =============================================================================

class TestCallAdapter:
    """Test _build_call_adapter handles LangGraph's config/runtime injection."""
    
    def test_drops_config_kwarg_when_fn_doesnt_accept(self):
        """Config kwarg is dropped if fn only accepts state."""
        def fn(state):
            return state
        adapter = _build_call_adapter(fn)
        result = adapter((), {'config': MagicMock()})
        assert 'config' not in result
    
    def test_passes_config_kwarg_when_fn_accepts(self):
        """Config kwarg is passed if fn signature includes config."""
        def fn(state, config):
            return state
        adapter = _build_call_adapter(fn)
        mock_config = MagicMock()
        result = adapter((), {'config': mock_config})
        assert result['config'] is mock_config
    
    def test_handles_positional_config(self):
        """Positional config arg is passed if fn accepts it."""
        def fn(state, config):
            return state
        adapter = _build_call_adapter(fn)
        mock_config = MagicMock()
        result = adapter((mock_config,), {})
        assert result['config'] is mock_config
    
    def test_handles_positional_config_and_runtime(self):
        """Both positional args passed if fn accepts them."""
        def fn(state, config, runtime):
            return state
        adapter = _build_call_adapter(fn)
        mock_config = MagicMock()
        mock_runtime = MagicMock()
        result = adapter((mock_config, mock_runtime), {})
        assert result['config'] is mock_config
        assert result['runtime'] is mock_runtime
    
    def test_drops_positional_config_when_fn_doesnt_accept(self):
        """Positional config dropped if fn only accepts state."""
        def fn(state):
            return state
        adapter = _build_call_adapter(fn)
        result = adapter((MagicMock(),), {})
        assert 'config' not in result
    
    def test_kwargs_override_positional(self):
        """Explicit kwargs take precedence over positional args."""
        def fn(state, config):
            return state
        adapter = _build_call_adapter(fn)
        positional_config = MagicMock(name="positional")
        kwarg_config = MagicMock(name="kwarg")
        result = adapter((positional_config,), {'config': kwarg_config})
        # Kwargs should override positional
        assert result['config'] is kwarg_config
    
    def test_raises_on_too_many_positional_args(self):
        """More than 2 extra positional args raises TypeError."""
        def fn(state, config, runtime):
            return state
        adapter = _build_call_adapter(fn)
        with pytest.raises(TypeError, match="received 3 extra positional args"):
            adapter((1, 2, 3), {})
    
    def test_raises_on_unexpected_kwarg(self):
        """Unknown kwargs raise TypeError if fn has no **kwargs."""
        def fn(state):
            return state
        adapter = _build_call_adapter(fn)
        with pytest.raises(TypeError, match="unexpected keyword argument 'foo'"):
            adapter((), {'foo': 'bar'})
    
    def test_passes_all_kwargs_when_fn_has_var_keyword(self):
        """All kwargs passed if fn has **kwargs."""
        def fn(state, **kwargs):
            return state
        adapter = _build_call_adapter(fn)
        result = adapter((), {'config': 1, 'runtime': 2, 'custom': 3})
        assert result == {'config': 1, 'runtime': 2, 'custom': 3}
    
    def test_drops_runtime_when_fn_doesnt_accept(self):
        """Runtime kwarg is dropped if fn doesn't accept it."""
        def fn(state, config):
            return state
        adapter = _build_call_adapter(fn)
        result = adapter((), {'config': 'c', 'runtime': 'r'})
        assert result == {'config': 'c'}
        assert 'runtime' not in result

    def test_kwargs_override_positional_both_supplied(self):
        """When both positional and kwarg config/runtime supplied, kwargs win.
        
        This validates precedence for LangGraph interface compatibility.
        """
        def fn(state, config, runtime):
            return state
        adapter = _build_call_adapter(fn)
        
        # Supply config and runtime both positionally AND as kwargs
        positional_config = MagicMock(name="positional_config")
        positional_runtime = MagicMock(name="positional_runtime")
        kwarg_config = MagicMock(name="kwarg_config")
        kwarg_runtime = MagicMock(name="kwarg_runtime")
        
        result = adapter(
            (positional_config, positional_runtime),
            {'config': kwarg_config, 'runtime': kwarg_runtime}
        )
        
        # Kwargs must override positional args
        assert result['config'] is kwarg_config
        assert result['runtime'] is kwarg_runtime
        assert result['config'] is not positional_config
        assert result['runtime'] is not positional_runtime

    def test_var_kwargs_receives_config_and_runtime(self):
        """Fn with **kwargs receives both config and runtime even if not explicitly named.
        
        This ensures forward compatibility with LangGraph's documented node interface.
        """
        captured = {}
        def fn(state, **kwargs):
            captured.update(kwargs)
            return state
        
        adapter = _build_call_adapter(fn)
        mock_config = MagicMock(name="config")
        mock_runtime = MagicMock(name="runtime")
        
        # Pass config and runtime via kwargs
        result = adapter((), {'config': mock_config, 'runtime': mock_runtime})
        
        # Both should be in the result kwargs
        assert result['config'] is mock_config
        assert result['runtime'] is mock_runtime
        
        # Also verify via positional args
        result2 = adapter((mock_config, mock_runtime), {})
        assert result2['config'] is mock_config
        assert result2['runtime'] is mock_runtime


# =============================================================================
# TestHITLInvariant: wrap-time enforcement
# =============================================================================

class TestHITLInvariant:
    """Test that HITL-exempt nodes cannot be wrapped with timed_node."""
    
    def test_wrapping_hitl_exempt_node_raises(self):
        """Wrapping code_review_gate raises ValueError."""
        def code_review_gate(state):
            return state
        with pytest.raises(ValueError, match="HITL-exempt node must not be wrapped"):
            timed_node(code_review_gate)
    
    def test_hitl_exempt_nodes_set_contains_code_review_gate(self):
        """Verify HITL_EXEMPT_NODES contains expected nodes."""
        assert "code_review_gate" in HITL_EXEMPT_NODES
        assert "sandbox_review_gate" in HITL_EXEMPT_NODES
    
    def test_wrapping_normal_node_succeeds(self):
        """Non-exempt nodes can be wrapped."""
        def normal_node(state):
            return state
        wrapped = timed_node(normal_node)
        assert wrapped is not None
        assert callable(wrapped)


# =============================================================================
# TestAsyncWrapperFinallyBlock: HITL clock cleanup on all exit paths
# =============================================================================

class TestAsyncWrapperFinallyBlock:
    """Test that HITL clock resumes on all exit paths in async wrapper.
    
    Since HITL-exempt nodes cannot be wrapped, we monkeypatch _is_hitl_exempt
    to return True for a non-exempt test node to exercise the finally block.
    
    Note: The _emit_* functions are local to timed_node, so we patch the 
    module-level functions they call (_log_graph_event, etc.) instead.
    """
    
    @pytest.mark.asyncio
    async def test_hitl_resumes_on_success(self):
        """HITL clock resumes after successful execution."""
        async def async_node(state):
            return state
        
        with patch('integration_coworker.graph.runtime._is_hitl_exempt', return_value=True), \
             patch('integration_coworker.graph.runtime._pause_wall_clock_for_hitl') as mock_pause, \
             patch('integration_coworker.graph.runtime._resume_wall_clock_for_hitl') as mock_resume, \
             patch('integration_coworker.graph.runtime._check_run_budget'), \
             patch('integration_coworker.graph.runtime._get_bounded_timeout', return_value=None), \
             patch('integration_coworker.graph.runtime._log_graph_event'), \
             patch('integration_coworker.graph.runtime._get_state_size', return_value=100), \
             patch('integration_coworker.graph.runtime._get_non_empty_keys', return_value=['run_id']), \
             patch('integration_coworker.graph.runtime._generate_span_id', return_value='test-span'), \
             patch('integration_coworker.graph.runtime._current_node_name'), \
             patch('integration_coworker.graph.runtime._span_id'), \
             patch('integration_coworker.graph.runtime._current_attempt'), \
             patch('integration_coworker.graph.runtime.validate_node_result', side_effect=lambda r, n: r), \
             patch('integration_coworker.graph.runtime.normalize_node_result', side_effect=lambda s, r, n: (r, {})):
            
            wrapped = timed_node(async_node)
            state = MockWorkflowState()
            
            await wrapped(state)
            
            mock_pause.assert_called_once_with('async_node')
            mock_resume.assert_called_once_with('async_node')
    
    @pytest.mark.asyncio
    async def test_hitl_resumes_on_exception(self):
        """HITL clock resumes even when node raises exception."""
        async def async_node(state):
            raise ValueError("test error")
        
        with patch('integration_coworker.graph.runtime._is_hitl_exempt', return_value=True), \
             patch('integration_coworker.graph.runtime._pause_wall_clock_for_hitl') as mock_pause, \
             patch('integration_coworker.graph.runtime._resume_wall_clock_for_hitl') as mock_resume, \
             patch('integration_coworker.graph.runtime._check_run_budget'), \
             patch('integration_coworker.graph.runtime._get_bounded_timeout', return_value=None), \
             patch('integration_coworker.graph.runtime._log_graph_event'), \
             patch('integration_coworker.graph.runtime._get_state_size', return_value=100), \
             patch('integration_coworker.graph.runtime._get_non_empty_keys', return_value=['run_id']), \
             patch('integration_coworker.graph.runtime._generate_span_id', return_value='test-span'), \
             patch('integration_coworker.graph.runtime._current_node_name'), \
             patch('integration_coworker.graph.runtime._span_id'), \
             patch('integration_coworker.graph.runtime._current_attempt'), \
             patch('integration_coworker.graph.runtime._write_node_failure_bundle'), \
             patch('integration_coworker.graph.runtime.classify_error'):
            
            wrapped = timed_node(async_node)
            state = MockWorkflowState()
            
            with pytest.raises(ValueError, match="test error"):
                await wrapped(state)
            
            mock_pause.assert_called_once_with('async_node')
            mock_resume.assert_called_once_with('async_node')
    
    @pytest.mark.asyncio
    async def test_hitl_resumes_on_timeout(self):
        """HITL clock resumes on timeout."""
        async def async_node(state):
            await asyncio.sleep(10)  # Will timeout
            return state
        
        with patch('integration_coworker.graph.runtime._is_hitl_exempt', return_value=True), \
             patch('integration_coworker.graph.runtime._pause_wall_clock_for_hitl') as mock_pause, \
             patch('integration_coworker.graph.runtime._resume_wall_clock_for_hitl') as mock_resume, \
             patch('integration_coworker.graph.runtime._check_run_budget'), \
             patch('integration_coworker.graph.runtime._get_bounded_timeout', return_value=0.01), \
             patch('integration_coworker.graph.runtime._log_graph_event'), \
             patch('integration_coworker.graph.runtime._record_timeout_error'), \
             patch('integration_coworker.graph.runtime._get_state_size', return_value=100), \
             patch('integration_coworker.graph.runtime._get_non_empty_keys', return_value=['run_id']), \
             patch('integration_coworker.graph.runtime._generate_span_id', return_value='test-span'), \
             patch('integration_coworker.graph.runtime._current_node_name'), \
             patch('integration_coworker.graph.runtime._span_id'), \
             patch('integration_coworker.graph.runtime._current_attempt'):
            
            from integration_coworker.graph.runtime import NodeTimeoutError
            
            wrapped = timed_node(async_node)
            state = MockWorkflowState()
            
            with pytest.raises(NodeTimeoutError):
                await wrapped(state)
            
            mock_pause.assert_called_once_with('async_node')
            mock_resume.assert_called_once_with('async_node')
    
    @pytest.mark.asyncio
    async def test_hitl_resumes_on_cancellation(self):
        """HITL clock resumes on asyncio.CancelledError."""
        cancel_event = asyncio.Event()
        
        async def async_node(state):
            cancel_event.set()
            await asyncio.sleep(10)  # Will be cancelled
            return state
        
        with patch('integration_coworker.graph.runtime._is_hitl_exempt', return_value=True), \
             patch('integration_coworker.graph.runtime._pause_wall_clock_for_hitl') as mock_pause, \
             patch('integration_coworker.graph.runtime._resume_wall_clock_for_hitl') as mock_resume, \
             patch('integration_coworker.graph.runtime._check_run_budget'), \
             patch('integration_coworker.graph.runtime._get_bounded_timeout', return_value=None), \
             patch('integration_coworker.graph.runtime._log_graph_event'), \
             patch('integration_coworker.graph.runtime._get_state_size', return_value=100), \
             patch('integration_coworker.graph.runtime._get_non_empty_keys', return_value=['run_id']), \
             patch('integration_coworker.graph.runtime._generate_span_id', return_value='test-span'), \
             patch('integration_coworker.graph.runtime._current_node_name'), \
             patch('integration_coworker.graph.runtime._span_id'), \
             patch('integration_coworker.graph.runtime._current_attempt'):
            
            wrapped = timed_node(async_node)
            state = MockWorkflowState()
            
            # Create task and cancel it
            task = asyncio.create_task(wrapped(state))
            await cancel_event.wait()  # Wait until node starts
            await asyncio.sleep(0.01)  # Small delay to ensure we're in the sleep
            task.cancel()
            
            with pytest.raises(asyncio.CancelledError):
                await task
            
            mock_pause.assert_called_once_with('async_node')
            mock_resume.assert_called_once_with('async_node')


# =============================================================================
# TestTracingEnablement: environment variable checks
# =============================================================================

class TestTracingEnablement:
    """Test _is_tracing_enabled() respects LangSmith docs.
    
    Per LangSmith docs, LANGSMITH_TRACING=true is REQUIRED for tracing.
    LANGCHAIN_TRACING_V2 alone is NOT sufficient - only legacy compat hint.
    """
    
    def test_langsmith_tracing_true_enables(self):
        """LANGSMITH_TRACING=true enables tracing."""
        with patch.dict('os.environ', {'LANGSMITH_TRACING': 'true'}, clear=False), \
             patch('integration_coworker.utils.trace_sanitizer.is_tracing_healthy', return_value=True):
            assert _is_tracing_enabled() is True
    
    def test_langchain_tracing_v2_alone_does_not_enable(self):
        """LANGCHAIN_TRACING_V2=true alone does NOT enable tracing.
        
        This is stricter than before - prevents accidental tracing activation
        in tests/CI where only LANGCHAIN_TRACING_V2 might be set.
        """
        env = {'LANGCHAIN_TRACING_V2': 'true'}
        # Explicitly exclude LANGSMITH_TRACING
        with patch.dict('os.environ', env, clear=True), \
             patch('integration_coworker.utils.trace_sanitizer.is_tracing_healthy', return_value=True):
            assert _is_tracing_enabled() is False
    
    def test_tracing_disabled_when_not_healthy(self):
        """Tracing disabled if is_tracing_healthy returns False."""
        with patch.dict('os.environ', {'LANGSMITH_TRACING': 'true'}, clear=False), \
             patch('integration_coworker.utils.trace_sanitizer.is_tracing_healthy', return_value=False):
            assert _is_tracing_enabled() is False
    
    def test_tracing_disabled_when_env_not_set(self):
        """Tracing disabled if neither env var is set."""
        with patch.dict('os.environ', {}, clear=True):
            assert _is_tracing_enabled() is False
    
    def test_langsmith_tracing_required_even_with_langchain_v2(self):
        """Both LANGSMITH_TRACING and LANGCHAIN_TRACING_V2 set, but LANGSMITH not true."""
        env = {'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'true'}
        with patch.dict('os.environ', env, clear=True):
            assert _is_tracing_enabled() is False


# =============================================================================
# TestSyncWrapperTracing: ls.trace context manager usage
# =============================================================================

class TestSyncWrapperTracing:
    """Test sync wrapper uses ls.trace context manager correctly."""
    
    def test_sync_wrapper_calls_fn_with_adapted_kwargs(self):
        """Sync wrapper passes adapted kwargs to fn."""
        received_kwargs = {}
        
        def sync_node(state, config=None):
            received_kwargs['config'] = config
            return state
        
        with patch('integration_coworker.graph.runtime._is_tracing_enabled', return_value=False), \
             patch('integration_coworker.graph.runtime._check_run_budget'), \
             patch('integration_coworker.graph.runtime._log_graph_event'), \
             patch('integration_coworker.graph.runtime._get_state_size', return_value=100), \
             patch('integration_coworker.graph.runtime._get_non_empty_keys', return_value=['run_id']), \
             patch('integration_coworker.graph.runtime._generate_span_id', return_value='test-span'), \
             patch('integration_coworker.graph.runtime._current_node_name'), \
             patch('integration_coworker.graph.runtime._span_id'), \
             patch('integration_coworker.graph.runtime._current_attempt'), \
             patch('integration_coworker.graph.runtime.validate_node_result', side_effect=lambda r, n: r), \
             patch('integration_coworker.graph.runtime.normalize_node_result', side_effect=lambda s, r, n: (r, {})):
            
            wrapped = timed_node(sync_node)
            state = MockWorkflowState()
            mock_config = MagicMock()
            
            wrapped(state, config=mock_config)
            
            assert received_kwargs['config'] is mock_config
    
    def test_sync_wrapper_handles_exception(self):
        """Sync wrapper emits error event on exception."""
        def sync_node(state):
            raise RuntimeError("sync error")
        
        with patch('integration_coworker.graph.runtime._is_tracing_enabled', return_value=False), \
             patch('integration_coworker.graph.runtime._check_run_budget'), \
             patch('integration_coworker.graph.runtime._log_graph_event') as mock_log, \
             patch('integration_coworker.graph.runtime._get_state_size', return_value=100), \
             patch('integration_coworker.graph.runtime._get_non_empty_keys', return_value=['run_id']), \
             patch('integration_coworker.graph.runtime._generate_span_id', return_value='test-span'), \
             patch('integration_coworker.graph.runtime._current_node_name'), \
             patch('integration_coworker.graph.runtime._span_id'), \
             patch('integration_coworker.graph.runtime._current_attempt'), \
             patch('integration_coworker.graph.runtime._write_node_failure_bundle'), \
             patch('integration_coworker.graph.runtime.classify_error'):
            
            wrapped = timed_node(sync_node)
            state = MockWorkflowState()
            
            with pytest.raises(RuntimeError, match="sync error"):
                wrapped(state)
            
            # Should have logged at least start and error events
            assert mock_log.call_count >= 2
