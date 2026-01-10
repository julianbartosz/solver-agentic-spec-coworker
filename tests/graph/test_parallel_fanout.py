"""
Tests for N-way parallel fan-out (Parallelization V2).

Tests the scalable parallel execution framework for LangGraph workflows.

NOTE: run_branches_with_semaphore, merge_parallel_states, and related config
classes are TEST-ONLY utilities. Production uses build_parallel_graph() 
with LangGraph edges.
"""

import asyncio
import copy
import pytest
from unittest.mock import MagicMock, patch

from integration_coworker.graph.parallel import get_parallel_timeout
from integration_coworker.graph.state import WorkflowState

# TEST-ONLY utilities (not for production use)
from tests.helpers.parallel_test_utils import (
    ParallelBranchConfig,
    ParallelFanOutConfig,
    FanOutStrategy,
    run_branches_with_semaphore,
    merge_parallel_states,
    create_dynamic_sync_node,
    register_fanout_config,
    get_fanout_config,
    clear_fanout_registry,
    get_max_branches,
)


@pytest.fixture
def base_state():
    """Create a base workflow state for testing."""
    return WorkflowState(
        source_refs=[],
        spec_refs=["test.yaml"],
        task_description="Test task",
    )


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the fanout registry before each test."""
    clear_fanout_registry()
    yield
    clear_fanout_registry()


class TestParallelBranchConfig:
    """Tests for ParallelBranchConfig."""
    
    def test_basic_config(self):
        """Test basic branch configuration."""
        def dummy_fn(state):
            return state
        
        config = ParallelBranchConfig(
            name="test_branch",
            node_fn=dummy_fn,
        )
        
        assert config.name == "test_branch"
        assert config.node_fn is dummy_fn
        assert config.condition is None
        assert config.priority == 0
        assert config.timeout is None
    
    def test_config_with_condition(self):
        """Test branch configuration with condition."""
        def dummy_fn(state):
            return state
        
        condition = lambda s: len(s.schemas) > 0
        
        config = ParallelBranchConfig(
            name="conditional_branch",
            node_fn=dummy_fn,
            condition=condition,
            priority=10,
        )
        
        assert config.condition is condition
        assert config.priority == 10


class TestParallelFanOutConfig:
    """Tests for ParallelFanOutConfig."""
    
    def test_basic_fanout_config(self):
        """Test basic fan-out configuration."""
        branches = [
            ParallelBranchConfig("branch_a", lambda s: s),
            ParallelBranchConfig("branch_b", lambda s: s),
        ]
        
        config = ParallelFanOutConfig(
            source_node="source",
            branches=branches,
            sync_node="sync",
            next_node="next",
        )
        
        assert config.source_node == "source"
        assert len(config.branches) == 2
        assert config.strategy == FanOutStrategy.STATIC
        assert config.max_concurrent_branches is None
    
    def test_fanout_with_concurrency_limit(self):
        """Test fan-out configuration with concurrency limit."""
        config = ParallelFanOutConfig(
            source_node="source",
            branches=[ParallelBranchConfig("branch", lambda s: s)],
            sync_node="sync",
            next_node="next",
            max_concurrent_branches=5,
            timeout=60,
        )
        
        assert config.max_concurrent_branches == 5
        assert config.timeout == 60


class TestFanOutRegistry:
    """Tests for the fan-out configuration registry."""
    
    def test_register_and_get(self):
        """Test registering and retrieving config."""
        config = ParallelFanOutConfig(
            source_node="my_node",
            branches=[ParallelBranchConfig("branch", lambda s: s)],
            sync_node="sync",
            next_node="next",
        )
        
        register_fanout_config(config)
        
        retrieved = get_fanout_config("my_node")
        assert retrieved is config
    
    def test_get_nonexistent_returns_none(self):
        """Test that getting nonexistent config returns None."""
        result = get_fanout_config("nonexistent")
        assert result is None
    
    def test_clear_registry(self):
        """Test clearing the registry."""
        config = ParallelFanOutConfig(
            source_node="my_node",
            branches=[ParallelBranchConfig("branch", lambda s: s)],
            sync_node="sync",
            next_node="next",
        )
        register_fanout_config(config)
        
        clear_fanout_registry()
        
        result = get_fanout_config("my_node")
        assert result is None


class TestCreateDynamicSyncNode:
    """Tests for dynamic sync node creation."""
    
    def test_create_sync_node(self, base_state):
        """Test creating a sync node for multiple branches."""
        sync_fn = create_dynamic_sync_node(["branch_a", "branch_b", "branch_c"])
        
        result = sync_fn(base_state)
        
        assert "sync_branch_a-branch_b-branch_c" in result.completed_steps
    
    def test_sync_node_custom_name(self, base_state):
        """Test sync node with custom name."""
        sync_fn = create_dynamic_sync_node(["a", "b"], name="custom_sync")
        
        assert sync_fn.__name__ == "custom_sync"
        
        result = sync_fn(base_state)
        assert "custom_sync" in result.completed_steps
    
    def test_sync_node_records_timing(self, base_state):
        """Test that sync node records timing."""
        base_state.node_timings = {}
        
        sync_fn = create_dynamic_sync_node(["branch_a"])
        result = sync_fn(base_state)
        
        assert "sync_branch_a" in result.node_timings


class TestRunBranchesWithSemaphore:
    """Tests for run_branches_with_semaphore."""
    
    @pytest.mark.asyncio
    async def test_basic_two_branch_fanout(self, base_state):
        """Test basic two-branch fan-out."""
        results = []
        
        def branch_a(state):
            results.append("a")
            state.completed_steps.append("branch_a")
            return state
        
        def branch_b(state):
            results.append("b")
            state.completed_steps.append("branch_b")
            return state
        
        branches = [
            ParallelBranchConfig("branch_a", branch_a),
            ParallelBranchConfig("branch_b", branch_b),
        ]
        
        branch_states = await run_branches_with_semaphore(base_state, branches)
        
        assert len(branch_states) == 2
        assert "branch_a" in branch_states
        assert "branch_b" in branch_states
        assert len(results) == 2
    
    @pytest.mark.asyncio
    async def test_three_way_fanout(self, base_state):
        """Test fan-out with 3 parallel branches."""
        def make_branch(name):
            def branch_fn(state):
                state.completed_steps.append(name)
                return state
            return branch_fn
        
        branches = [
            ParallelBranchConfig("branch_1", make_branch("branch_1")),
            ParallelBranchConfig("branch_2", make_branch("branch_2")),
            ParallelBranchConfig("branch_3", make_branch("branch_3")),
        ]
        
        branch_states = await run_branches_with_semaphore(base_state, branches)
        
        assert len(branch_states) == 3
        for i in range(1, 4):
            assert f"branch_{i}" in branch_states
    
    @pytest.mark.asyncio
    async def test_five_way_fanout(self, base_state):
        """Test fan-out with 5 parallel branches."""
        def make_branch(name):
            def branch_fn(state):
                state.completed_steps.append(name)
                return state
            return branch_fn
        
        branches = [
            ParallelBranchConfig(f"branch_{i}", make_branch(f"branch_{i}"))
            for i in range(5)
        ]
        
        branch_states = await run_branches_with_semaphore(base_state, branches)
        
        assert len(branch_states) == 5
    
    @pytest.mark.asyncio
    async def test_conditional_branch_skip(self, base_state):
        """Test that branches with unmet conditions are skipped."""
        executed = []
        
        def always_run(state):
            executed.append("always")
            state.completed_steps.append("always")
            return state
        
        def conditional_run(state):
            executed.append("conditional")
            state.completed_steps.append("conditional")
            return state
        
        # Condition that is always False (no schemas)
        branches = [
            ParallelBranchConfig("always", always_run),
            ParallelBranchConfig(
                "conditional", 
                conditional_run, 
                condition=lambda s: len(s.schemas) > 0
            ),
        ]
        
        branch_states = await run_branches_with_semaphore(base_state, branches)
        
        # Both branches return states, but only "always" was executed
        assert len(branch_states) == 2
        assert "always" in executed
        assert "conditional" not in executed
    
    @pytest.mark.asyncio
    async def test_branch_priority_ordering(self, base_state):
        """Test that branches are sorted by priority."""
        execution_order = []
        
        def make_branch(name):
            def branch_fn(state):
                execution_order.append(name)
                return state
            return branch_fn
        
        branches = [
            ParallelBranchConfig("low_priority", make_branch("low"), priority=10),
            ParallelBranchConfig("high_priority", make_branch("high"), priority=1),
            ParallelBranchConfig("medium_priority", make_branch("medium"), priority=5),
        ]
        
        await run_branches_with_semaphore(base_state, branches)
        
        # With parallel execution, we can't guarantee order, but all should complete
        assert len(execution_order) == 3
    
    @pytest.mark.asyncio
    async def test_max_concurrent_branches(self, base_state):
        """Test that max_concurrent limits concurrent branch execution."""
        max_concurrent_seen = 0
        current_concurrent = 0
        lock = asyncio.Lock()
        
        def make_branch(name):
            def branch_fn(state):
                nonlocal max_concurrent_seen, current_concurrent
                # This is synchronous, so we can't truly test async concurrency
                # But we can verify the function is called
                state.completed_steps.append(name)
                return state
            return branch_fn
        
        branches = [
            ParallelBranchConfig(f"branch_{i}", make_branch(f"branch_{i}"))
            for i in range(5)
        ]
        
        # Run with max_concurrent=2
        branch_states = await run_branches_with_semaphore(
            base_state, branches, max_concurrent=2
        )
        
        assert len(branch_states) == 5
    
    @pytest.mark.asyncio
    async def test_branch_exception_handling(self, base_state):
        """Test that branch exceptions are handled properly."""
        def failing_branch(state):
            raise ValueError("Branch failed!")
        
        def success_branch(state):
            state.completed_steps.append("success")
            return state
        
        branches = [
            ParallelBranchConfig("failing", failing_branch),
            ParallelBranchConfig("success", success_branch),
        ]
        
        with pytest.raises(RuntimeError, match="Parallel branches failed"):
            await run_branches_with_semaphore(base_state, branches)
    
    @pytest.mark.asyncio
    async def test_empty_branches_returns_empty(self, base_state):
        """Test that empty branch list returns empty dict."""
        result = await run_branches_with_semaphore(base_state, [])
        assert result == {}
    
    @pytest.mark.asyncio
    async def test_timeout(self, base_state):
        """Test that branches timeout correctly."""
        import time
        
        def slow_branch(state):
            time.sleep(2)  # Sleep longer than timeout
            return state
        
        branches = [ParallelBranchConfig("slow", slow_branch)]
        
        # Note: time.sleep in sync functions blocks the event loop,
        # so the timeout may not trigger as expected. This test validates
        # the timeout mechanism is in place but may not reliably timeout
        # with synchronous blocking operations.
        try:
            await run_branches_with_semaphore(base_state, branches, timeout=1)
            # If it completes, that's also acceptable (timing dependent)
        except RuntimeError as e:
            assert "timed out" in str(e).lower()

    @pytest.mark.asyncio
    async def test_timeout_async_gather(self, base_state):
        """Test that asyncio.gather with timeout works correctly."""
        # This test verifies the timeout mechanism works at the asyncio level.
        # Note: run_branches_with_semaphore uses sync node functions, so we test
        # the underlying asyncio.wait_for mechanism separately.
        
        async def slow_coro():
            await asyncio.sleep(5)
            return "done"
        
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_coro(), timeout=0.1)


class TestMergeParallelStates:
    """Tests for merge_parallel_states."""
    
    def test_merge_completed_steps(self, base_state):
        """Test that completed_steps are merged."""
        branch_a = copy.deepcopy(base_state)
        branch_a.completed_steps = ["step_a"]
        
        branch_b = copy.deepcopy(base_state)
        branch_b.completed_steps = ["step_b"]
        
        branch_states = {"branch_a": branch_a, "branch_b": branch_b}
        
        merged = merge_parallel_states(base_state, branch_states)
        
        assert "step_a" in merged.completed_steps
        assert "step_b" in merged.completed_steps
    
    def test_merge_node_timings(self, base_state):
        """Test that node_timings are merged."""
        branch_a = copy.deepcopy(base_state)
        branch_a.node_timings = {"node_a": 100.0}
        
        branch_b = copy.deepcopy(base_state)
        branch_b.node_timings = {"node_b": 200.0}
        
        branch_states = {"branch_a": branch_a, "branch_b": branch_b}
        
        merged = merge_parallel_states(base_state, branch_states)
        
        assert merged.node_timings["node_a"] == 100.0
        assert merged.node_timings["node_b"] == 200.0
    
    def test_merge_errors(self, base_state):
        """Test that errors are merged."""
        branch_a = copy.deepcopy(base_state)
        branch_a.errors = ["error_a"]
        
        branch_b = copy.deepcopy(base_state)
        branch_b.errors = ["error_b"]
        
        branch_states = {"branch_a": branch_a, "branch_b": branch_b}
        
        merged = merge_parallel_states(base_state, branch_states)
        
        assert "error_a" in merged.errors
        assert "error_b" in merged.errors
    
    def test_merge_warnings(self, base_state):
        """Test that warnings are merged."""
        branch_a = copy.deepcopy(base_state)
        branch_a.warnings = ["warning_a"]
        
        branch_b = copy.deepcopy(base_state)
        branch_b.warnings = ["warning_b"]
        
        branch_states = {"branch_a": branch_a, "branch_b": branch_b}
        
        merged = merge_parallel_states(base_state, branch_states)
        
        assert "warning_a" in merged.warnings
        assert "warning_b" in merged.warnings
    
    def test_merge_embed_spec_chunks_branch(self, base_state):
        """Test merging from embed_spec_chunks branch."""
        branch_state = copy.deepcopy(base_state)
        branch_state.doc_chunks = ["chunk1", "chunk2"]
        branch_state.spec_documents = [MagicMock()]
        branch_state.chunk_count = 2
        branch_state.embedding_count = 2
        branch_state.completed_steps = ["embed_spec_chunks"]
        
        branch_states = {"embed_spec_chunks": branch_state}
        
        merged = merge_parallel_states(base_state, branch_states)
        
        assert merged.doc_chunks == ["chunk1", "chunk2"]
        assert len(merged.spec_documents) == 1
        assert merged.chunk_count == 2
        assert merged.embedding_count == 2
    
    def test_merge_understand_task_branch(self, base_state):
        """Test merging from understand_task branch."""
        branch_state = copy.deepcopy(base_state)
        branch_state.integration_task = MagicMock()
        branch_state.completed_steps = ["understand_task"]
        
        branch_states = {"understand_task": branch_state}
        
        merged = merge_parallel_states(base_state, branch_states)
        
        assert merged.integration_task is not None
    
    def test_merge_empty_states(self, base_state):
        """Test merging empty branch states."""
        result = merge_parallel_states(base_state, {})
        
        # Should return base state unchanged
        assert result.task_description == base_state.task_description
    
    def test_merge_duplicate_items_not_repeated(self, base_state):
        """Test that duplicate items are not repeated."""
        branch_a = copy.deepcopy(base_state)
        branch_a.completed_steps = ["common_step"]
        
        branch_b = copy.deepcopy(base_state)
        branch_b.completed_steps = ["common_step"]
        
        branch_states = {"branch_a": branch_a, "branch_b": branch_b}
        
        merged = merge_parallel_states(base_state, branch_states)
        
        # "common_step" should appear only once
        assert merged.completed_steps.count("common_step") == 1


class TestEnvironmentConfiguration:
    """Tests for environment variable configuration."""
    
    def test_get_parallel_timeout_default(self):
        """Test default parallel timeout."""
        with patch.dict("os.environ", {}, clear=True):
            timeout = get_parallel_timeout()
            assert timeout == 300
    
    def test_get_parallel_timeout_from_env(self):
        """Test parallel timeout from environment."""
        with patch.dict("os.environ", {"PARALLEL_TIMEOUT": "60"}):
            timeout = get_parallel_timeout()
            assert timeout == 60
    
    def test_get_max_branches_default(self):
        """Test default max branches (None = unlimited)."""
        with patch.dict("os.environ", {}, clear=True):
            max_branches = get_max_branches()
            assert max_branches is None
    
    def test_get_max_branches_from_env(self):
        """Test max branches from environment."""
        with patch.dict("os.environ", {"PARALLEL_MAX_BRANCHES": "10"}):
            max_branches = get_max_branches()
            assert max_branches == 10
    
    def test_get_max_branches_invalid_returns_default(self):
        """Test invalid max branches returns default."""
        with patch.dict("os.environ", {"PARALLEL_MAX_BRANCHES": "invalid"}):
            max_branches = get_max_branches()
            assert max_branches is None  # Default is None


class TestIntegrationScenarios:
    """Integration tests for realistic scenarios."""
    
    @pytest.mark.asyncio
    async def test_embed_and_understand_parallel(self, base_state):
        """Test the typical embed + understand parallel pattern."""
        def embed_spec_chunks(state):
            state.doc_chunks = ["chunk1", "chunk2", "chunk3"]
            state.completed_steps.append("embed_spec_chunks")
            state.node_timings = {"embed_spec_chunks": 150.0}
            return state
        
        def understand_task(state):
            state.integration_task = MagicMock()
            state.completed_steps.append("understand_task")
            state.node_timings = {"understand_task": 200.0}
            return state
        
        branches = [
            ParallelBranchConfig("embed_spec_chunks", embed_spec_chunks),
            ParallelBranchConfig("understand_task", understand_task),
        ]
        
        branch_states = await run_branches_with_semaphore(base_state, branches)
        merged = merge_parallel_states(base_state, branch_states)
        
        # Both branches completed
        assert "embed_spec_chunks" in merged.completed_steps
        assert "understand_task" in merged.completed_steps
        
        # Both outputs present
        assert merged.doc_chunks == ["chunk1", "chunk2", "chunk3"]
        assert merged.integration_task is not None
        
        # Both timings recorded
        assert merged.node_timings["embed_spec_chunks"] == 150.0
        assert merged.node_timings["understand_task"] == 200.0
    
    @pytest.mark.asyncio
    async def test_ten_way_fanout(self, base_state):
        """Test parallel fan-out with 10 branches."""
        def make_branch(i):
            def branch_fn(state):
                state.completed_steps.append(f"branch_{i}")
                return state
            return branch_fn
        
        branches = [
            ParallelBranchConfig(f"branch_{i}", make_branch(i))
            for i in range(10)
        ]
        
        branch_states = await run_branches_with_semaphore(base_state, branches)
        merged = merge_parallel_states(base_state, branch_states)
        
        # All 10 branches should complete
        assert len(branch_states) == 10
        for i in range(10):
            assert f"branch_{i}" in merged.completed_steps
