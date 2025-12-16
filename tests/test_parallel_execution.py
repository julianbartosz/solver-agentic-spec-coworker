"""
Tests for Parallel Node Execution (Plan 8).

Tests the parallel workflow execution support where independent nodes
(embed_spec_chunks and understand_task) can run concurrently.
"""

import os
import pytest
import time
from unittest.mock import patch, MagicMock


# =============================================================================
# Test Configuration
# =============================================================================

class TestParallelConfiguration:
    """Test parallel execution configuration."""
    
    def test_is_parallel_enabled_default_false(self):
        """Test that parallel is disabled by default."""
        from integration_coworker.graph.parallel import is_parallel_enabled
        
        # Clear env var
        with patch.dict(os.environ, {}, clear=True):
            assert is_parallel_enabled() is False
    
    def test_is_parallel_enabled_true(self):
        """Test enabling parallel execution via env var."""
        from integration_coworker.graph.parallel import is_parallel_enabled
        
        with patch.dict(os.environ, {"PARALLEL_WORKFLOW": "true"}):
            assert is_parallel_enabled() is True
    
    def test_is_parallel_enabled_various_values(self):
        """Test various truthy values for PARALLEL_WORKFLOW."""
        from integration_coworker.graph.parallel import is_parallel_enabled
        
        for value in ["true", "1", "yes", "on", "TRUE", "True"]:
            with patch.dict(os.environ, {"PARALLEL_WORKFLOW": value}):
                assert is_parallel_enabled() is True, f"Failed for value: {value}"
        
        for value in ["false", "0", "no", "off", "", "random"]:
            with patch.dict(os.environ, {"PARALLEL_WORKFLOW": value}):
                assert is_parallel_enabled() is False, f"Failed for value: {value}"
    
    def test_get_parallel_timeout_default(self):
        """Test default parallel timeout."""
        from integration_coworker.graph.parallel import get_parallel_timeout
        
        with patch.dict(os.environ, {}, clear=True):
            assert get_parallel_timeout() == 300  # 5 minutes
    
    def test_get_parallel_timeout_custom(self):
        """Test custom parallel timeout."""
        from integration_coworker.graph.parallel import get_parallel_timeout
        
        with patch.dict(os.environ, {"PARALLEL_TIMEOUT": "600"}):
            assert get_parallel_timeout() == 600


# =============================================================================
# Test Sync Node
# =============================================================================

class TestSyncNode:
    """Test the sync_embed_task node."""
    
    def test_sync_embed_task_adds_to_completed_steps(self):
        """Test that sync node adds itself to completed_steps."""
        from integration_coworker.graph.parallel import sync_embed_task
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        state = WorkflowState(
            source_refs=["test.yaml"],
            spec_refs=["test.yaml"],
            task_description="Test task",
            options=IntegrationOptions(),
            completed_steps=["build_silver_api_model", "embed_spec_chunks", "understand_task"],
        )
        
        result = sync_embed_task(state)
        
        assert "sync_embed_task" in result.completed_steps
    
    def test_sync_embed_task_records_timing(self):
        """Test that sync node records timing."""
        from integration_coworker.graph.parallel import sync_embed_task
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        state = WorkflowState(
            source_refs=["test.yaml"],
            spec_refs=["test.yaml"],
            task_description="Test task",
            options=IntegrationOptions(),
            completed_steps=[],
            node_timings={},
        )
        
        result = sync_embed_task(state)
        
        assert "sync_embed_task" in result.node_timings
        assert result.node_timings["sync_embed_task"] >= 0


# =============================================================================
# Test Parallel Branch Configuration
# =============================================================================

class TestParallelBranchConfig:
    """Test parallel branch configuration."""
    
    def test_get_parallel_branches_after_silver(self):
        """Test getting parallel branches after build_silver_api_model."""
        from integration_coworker.graph.parallel import get_parallel_branches
        
        branches = get_parallel_branches("build_silver_api_model")
        
        assert "embed_spec_chunks" in branches
        assert "understand_task" in branches
        assert len(branches) == 2
    
    def test_get_parallel_branches_unknown_node(self):
        """Test getting parallel branches for unknown node returns empty."""
        from integration_coworker.graph.parallel import get_parallel_branches
        
        branches = get_parallel_branches("unknown_node")
        
        assert branches == []
    
    def test_get_sync_node_after_silver(self):
        """Test getting sync node after build_silver_api_model."""
        from integration_coworker.graph.parallel import get_sync_node
        
        sync_node = get_sync_node("build_silver_api_model")
        
        assert sync_node == "sync_embed_task"
    
    def test_get_sync_node_unknown(self):
        """Test getting sync node for unknown node returns None."""
        from integration_coworker.graph.parallel import get_sync_node
        
        sync_node = get_sync_node("unknown_node")
        
        assert sync_node is None


# =============================================================================
# Test Parallel Execution Utilities
# =============================================================================

class TestParallelExecution:
    """Test parallel execution utilities."""
    
    def test_run_nodes_parallel_empty_list(self):
        """Test running with empty node list returns state unchanged."""
        from integration_coworker.graph.parallel import run_nodes_parallel
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        state = WorkflowState(
            source_refs=["test.yaml"],
            spec_refs=["test.yaml"],
            task_description="Test task",
            options=IntegrationOptions(),
            completed_steps=[],
        )
        
        result = run_nodes_parallel(state, [])
        
        assert result is state
    
    def test_run_nodes_parallel_single_node(self):
        """Test running a single node in parallel."""
        from integration_coworker.graph.parallel import run_nodes_parallel
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        state = WorkflowState(
            source_refs=["test.yaml"],
            spec_refs=["test.yaml"],
            task_description="Test task",
            options=IntegrationOptions(),
            completed_steps=[],
        )
        
        def mock_node(s):
            s.completed_steps.append("mock_node")
            return s
        
        result = run_nodes_parallel(state, [("mock_node", mock_node)])
        
        assert "mock_node" in result.completed_steps
    
    def test_run_nodes_parallel_multiple_nodes(self):
        """Test running multiple nodes in parallel."""
        from integration_coworker.graph.parallel import run_nodes_parallel
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        state = WorkflowState(
            source_refs=["test.yaml"],
            spec_refs=["test.yaml"],
            task_description="Test task",
            options=IntegrationOptions(),
            completed_steps=[],
        )
        
        def node1(s):
            new_state = WorkflowState(**s.__dict__)
            new_state.completed_steps = s.completed_steps.copy()
            new_state.completed_steps.append("node1")
            return new_state
        
        def node2(s):
            new_state = WorkflowState(**s.__dict__)
            new_state.completed_steps = s.completed_steps.copy()
            new_state.completed_steps.append("node2")
            return new_state
        
        result = run_nodes_parallel(state, [("node1", node1), ("node2", node2)])
        
        assert "node1" in result.completed_steps
        assert "node2" in result.completed_steps
    
    def test_run_nodes_parallel_error_handling(self):
        """Test that errors in parallel nodes are propagated."""
        from integration_coworker.graph.parallel import run_nodes_parallel
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        state = WorkflowState(
            source_refs=["test.yaml"],
            spec_refs=["test.yaml"],
            task_description="Test task",
            options=IntegrationOptions(),
            completed_steps=[],
        )
        
        def failing_node(s):
            raise ValueError("Test error")
        
        with pytest.raises(RuntimeError) as exc_info:
            run_nodes_parallel(state, [("failing_node", failing_node)])
        
        assert "Parallel execution failed" in str(exc_info.value)


# =============================================================================
# Test Parallel Metrics
# =============================================================================

class TestParallelMetrics:
    """Test parallel execution metrics."""
    
    def test_parallel_execution_metrics_basic(self):
        """Test basic metrics recording."""
        from integration_coworker.graph.parallel import ParallelExecutionMetrics
        
        metrics = ParallelExecutionMetrics()
        
        metrics.record_branch("embed_spec_chunks", 100.0)
        metrics.record_branch("understand_task", 150.0)
        metrics.record_total_time(150.0)  # Max of the two
        
        assert metrics.parallel_branches == ["embed_spec_chunks", "understand_task"]
        assert metrics.branch_timings == {"embed_spec_chunks": 100.0, "understand_task": 150.0}
        assert metrics.total_parallel_time == 150.0
        assert metrics.sequential_equivalent_time == 250.0
        assert metrics.speedup == pytest.approx(1.67, rel=0.01)
    
    def test_parallel_metrics_to_dict(self):
        """Test metrics conversion to dict."""
        from integration_coworker.graph.parallel import ParallelExecutionMetrics
        
        metrics = ParallelExecutionMetrics()
        metrics.record_branch("test_node", 100.0)
        metrics.record_total_time(100.0)
        
        result = metrics.to_dict()
        
        assert "parallel_branches" in result
        assert "branch_timings_ms" in result
        assert "total_parallel_time_ms" in result
        assert "speedup" in result
    
    def test_get_parallel_metrics_from_state(self):
        """Test extracting metrics from workflow state."""
        from integration_coworker.graph.parallel import get_parallel_metrics_from_state
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        state = WorkflowState(
            source_refs=["test.yaml"],
            spec_refs=["test.yaml"],
            task_description="Test task",
            options=IntegrationOptions(),
            completed_steps=[],
            node_timings={
                "embed_spec_chunks": 100.0,
                "understand_task": 150.0,
            },
        )
        
        metrics = get_parallel_metrics_from_state(state)
        
        assert metrics is not None
        assert len(metrics.parallel_branches) == 2
        assert metrics.speedup > 1.0
    
    def test_get_parallel_metrics_no_parallel_nodes(self):
        """Test extracting metrics when no parallel nodes ran."""
        from integration_coworker.graph.parallel import get_parallel_metrics_from_state
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        state = WorkflowState(
            source_refs=["test.yaml"],
            spec_refs=["test.yaml"],
            task_description="Test task",
            options=IntegrationOptions(),
            completed_steps=[],
            node_timings={
                "plan_run": 10.0,
                "ingest_spec": 20.0,
            },
        )
        
        metrics = get_parallel_metrics_from_state(state)
        
        assert metrics is None


# =============================================================================
# Test Graph Building
# =============================================================================

class TestGraphBuilding:
    """Test parallel graph building."""
    
    def test_build_parallel_graph_creates_sync_node(self):
        """Test that build_parallel_graph includes sync node."""
        from integration_coworker.graph.runtime import build_parallel_graph
        
        # Build the graph
        graph = build_parallel_graph(checkpointer=None)
        
        # The graph should be compiled successfully
        assert graph is not None
    
    def test_run_workflow_uses_parallel_when_enabled(self):
        """Test that run_workflow uses parallel graph when enabled."""
        from integration_coworker.graph.runtime import run_workflow, build_parallel_graph, build_graph
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        with patch.dict(os.environ, {"PARALLEL_WORKFLOW": "true"}):
            with patch('integration_coworker.graph.runtime.build_parallel_graph') as mock_parallel:
                with patch('integration_coworker.graph.runtime.build_graph') as mock_seq:
                    # Mock the graph compilation
                    mock_app = MagicMock()
                    mock_app.invoke.return_value = {
                        'source_refs': [],
                        'spec_refs': [],
                        'task_description': 'test',
                        'options': IntegrationOptions(),
                        'completed_steps': [],
                    }
                    mock_parallel.return_value = mock_app
                    
                    state = WorkflowState(
                        source_refs=["test.yaml"],
                        spec_refs=["test.yaml"],
                        task_description="Test task",
                        options=IntegrationOptions(),
                        completed_steps=[],
                    )
                    
                    with patch('integration_coworker.graph.runtime.get_checkpointer', return_value=None):
                        try:
                            run_workflow(state, use_checkpointer=False)
                        except Exception:
                            pass  # We just want to check which graph was built
                    
                    # Parallel graph should have been called
                    mock_parallel.assert_called_once()
    
    def test_run_workflow_uses_sequential_when_disabled(self):
        """Test that run_workflow uses sequential graph when parallel disabled."""
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        with patch.dict(os.environ, {"PARALLEL_WORKFLOW": "false"}):
            with patch('integration_coworker.graph.runtime.build_parallel_graph') as mock_parallel:
                with patch('integration_coworker.graph.runtime.build_graph') as mock_seq:
                    mock_app = MagicMock()
                    mock_app.invoke.return_value = {
                        'source_refs': [],
                        'spec_refs': [],
                        'task_description': 'test',
                        'options': IntegrationOptions(),
                        'completed_steps': [],
                    }
                    mock_seq.return_value = mock_app
                    
                    state = WorkflowState(
                        source_refs=["test.yaml"],
                        spec_refs=["test.yaml"],
                        task_description="Test task",
                        options=IntegrationOptions(),
                        completed_steps=[],
                    )
                    
                    with patch('integration_coworker.graph.runtime.get_checkpointer', return_value=None):
                        from integration_coworker.graph.runtime import run_workflow
                        try:
                            run_workflow(state, use_checkpointer=False)
                        except Exception:
                            pass
                    
                    # Sequential graph should have been called
                    mock_seq.assert_called_once()


# =============================================================================
# Integration Tests
# =============================================================================

@pytest.mark.slow
class TestParallelIntegration:
    """Integration tests for parallel execution."""
    
    def test_parallel_execution_faster_than_sequential(self):
        """Test that parallel execution is faster than sequential (simulated)."""
        import time
        from integration_coworker.graph.parallel import run_nodes_parallel
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        state = WorkflowState(
            source_refs=["test.yaml"],
            spec_refs=["test.yaml"],
            task_description="Test task",
            options=IntegrationOptions(),
            completed_steps=[],
            node_timings={},
        )
        
        def slow_node1(s):
            time.sleep(0.1)  # 100ms
            new_s = WorkflowState(**s.__dict__)
            new_s.completed_steps = s.completed_steps.copy() + ["node1"]
            new_s.node_timings = {"node1": 100.0}
            return new_s
        
        def slow_node2(s):
            time.sleep(0.1)  # 100ms
            new_s = WorkflowState(**s.__dict__)
            new_s.completed_steps = s.completed_steps.copy() + ["node2"]
            new_s.node_timings = {"node2": 100.0}
            return new_s
        
        # Time parallel execution
        start_parallel = time.perf_counter()
        run_nodes_parallel(state, [("node1", slow_node1), ("node2", slow_node2)])
        parallel_time = time.perf_counter() - start_parallel
        
        # Time sequential execution
        start_seq = time.perf_counter()
        slow_node1(state)
        slow_node2(state)
        seq_time = time.perf_counter() - start_seq
        
        # Parallel should be significantly faster (at least 30% faster)
        assert parallel_time < seq_time * 0.8, f"Parallel: {parallel_time:.3f}s, Sequential: {seq_time:.3f}s"
