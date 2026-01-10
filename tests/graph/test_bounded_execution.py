"""
Tests for P2: Bounded Execution.

Tests:
1. test_bounded_execution_timeout_trips - Node timeout enforcement
2. test_bounded_execution_not_applied_when_disabled - Disabled config
3. test_recursion_limit_is_set_in_invoke_config - Config building
4. test_run_budget_wall_time - Wall time budget enforcement
5. test_run_budget_node_count - Node count budget enforcement
6. test_global_timeout_error - GlobalTimeoutError exception
"""
import asyncio
import os
import pytest
from unittest import mock

from integration_coworker.graph.bounds import (
    BoundedExecutionConfig,
    get_bounds_config,
    get_invoke_config,
    make_bounded,
    bounded_run_context,
    RunBudgetTracker,
    NodeTimeoutError,
    RunBudgetExceededError,
    GlobalTimeoutError,
    set_budget_tracker,
    get_budget_tracker,
)


class TestBoundedExecutionConfig:
    """Tests for BoundedExecutionConfig."""
    
    def test_default_config_values(self):
        """Test default configuration values."""
        cfg = BoundedExecutionConfig()
        
        assert cfg.enabled is True
        assert cfg.recursion_limit == 100
        assert cfg.node_timeout_seconds_default == 120.0
        assert cfg.max_run_wall_seconds == 3600.0
        assert cfg.max_nodes_executed == 200
        assert cfg.max_concurrency == 10
    
    def test_get_timeout_for_node_default(self):
        """Test default timeout lookup."""
        cfg = BoundedExecutionConfig()
        
        # Unknown node gets default timeout
        assert cfg.get_timeout_for_node("unknown_node") == 120.0
    
    def test_get_timeout_for_node_override(self):
        """Test per-node timeout override."""
        cfg = BoundedExecutionConfig(
            node_timeout_overrides={"slow_node": 300.0}
        )
        
        assert cfg.get_timeout_for_node("slow_node") == 300.0
        assert cfg.get_timeout_for_node("other_node") == 120.0


class TestGetBoundsConfig:
    """Tests for get_bounds_config()."""
    
    def test_production_profile_enables_bounded_exec(self):
        """Test that production profile enables bounded execution by default."""
        with mock.patch.dict(os.environ, {"CODEGEN_PROFILE": "production"}, clear=False):
            # Clear any explicit setting
            env = os.environ.copy()
            env.pop("BOUNDED_EXEC_ENABLED", None)
            with mock.patch.dict(os.environ, env, clear=True):
                os.environ["CODEGEN_PROFILE"] = "production"
                cfg = get_bounds_config()
                assert cfg.enabled is True
    
    def test_development_profile_disables_bounded_exec(self):
        """Test that development profile disables bounded execution by default."""
        with mock.patch.dict(os.environ, {
            "CODEGEN_PROFILE": "development",
        }, clear=False):
            # Clear any explicit setting
            env = os.environ.copy()
            env.pop("BOUNDED_EXEC_ENABLED", None)
            with mock.patch.dict(os.environ, env, clear=True):
                os.environ["CODEGEN_PROFILE"] = "development"
                cfg = get_bounds_config()
                assert cfg.enabled is False
    
    def test_explicit_enable_overrides_profile(self):
        """Test that explicit env var overrides profile default."""
        with mock.patch.dict(os.environ, {
            "CODEGEN_PROFILE": "development",
            "BOUNDED_EXEC_ENABLED": "true",
        }, clear=False):
            cfg = get_bounds_config()
            assert cfg.enabled is True
    
    def test_explicit_disable_overrides_profile(self):
        """Test that explicit disable overrides production profile."""
        with mock.patch.dict(os.environ, {
            "CODEGEN_PROFILE": "production",
            "BOUNDED_EXEC_ENABLED": "false",
        }, clear=False):
            cfg = get_bounds_config()
            assert cfg.enabled is False
    
    def test_env_var_overrides(self):
        """Test environment variable configuration."""
        with mock.patch.dict(os.environ, {
            "BOUNDED_EXEC_ENABLED": "true",
            "BOUNDED_EXEC_RECURSION_LIMIT": "50",
            "BOUNDED_EXEC_NODE_TIMEOUT": "60.0",
            "BOUNDED_EXEC_MAX_WALL_SECONDS": "1800.0",
            "BOUNDED_EXEC_MAX_NODES": "100",
            "BOUNDED_EXEC_MAX_CONCURRENCY": "5",
        }, clear=False):
            cfg = get_bounds_config()
            
            assert cfg.enabled is True
            assert cfg.recursion_limit == 50
            assert cfg.node_timeout_seconds_default == 60.0
            assert cfg.max_run_wall_seconds == 1800.0
            assert cfg.max_nodes_executed == 100
            assert cfg.max_concurrency == 5
    
    def test_per_node_timeout_from_env(self):
        """Test per-node timeout override from environment."""
        with mock.patch.dict(os.environ, {
            "BOUNDED_EXEC_ENABLED": "true",
            "BOUNDED_EXEC_TIMEOUT_SLOW_NODE": "500.0",
        }, clear=False):
            cfg = get_bounds_config()
            
            assert cfg.get_timeout_for_node("slow_node") == 500.0


class TestGetInvokeConfig:
    """Tests for get_invoke_config()."""
    
    def test_recursion_limit_is_set_in_invoke_config(self):
        """P2 requirement: recursion_limit must be in invoke config."""
        cfg = BoundedExecutionConfig(enabled=True, recursion_limit=75)
        
        invoke_cfg = get_invoke_config("thread-123", cfg)
        
        assert invoke_cfg["recursion_limit"] == 75
        assert invoke_cfg["configurable"]["thread_id"] == "thread-123"
    
    def test_max_concurrency_is_set(self):
        """Test max_concurrency in invoke config."""
        cfg = BoundedExecutionConfig(enabled=True, max_concurrency=8)
        
        invoke_cfg = get_invoke_config("thread-123", cfg)
        
        assert invoke_cfg["max_concurrency"] == 8
    
    def test_disabled_config_omits_bounds(self):
        """Test that disabled config doesn't add recursion_limit."""
        cfg = BoundedExecutionConfig(enabled=False)
        
        invoke_cfg = get_invoke_config("thread-123", cfg)
        
        assert "recursion_limit" not in invoke_cfg
        assert invoke_cfg["configurable"]["thread_id"] == "thread-123"
    
    def test_extra_config_merged(self):
        """Test that extra config is properly merged."""
        cfg = BoundedExecutionConfig(enabled=True)
        
        invoke_cfg = get_invoke_config(
            "thread-123",
            cfg,
            extra_config={"extra_key": "value"}
        )
        
        assert invoke_cfg["extra_key"] == "value"
        assert invoke_cfg["configurable"]["thread_id"] == "thread-123"


class TestMakeBounded:
    """Tests for make_bounded() wrapper."""
    
    @pytest.mark.asyncio
    async def test_bounded_execution_timeout_trips(self):
        """P2 requirement: Node that exceeds timeout raises NodeTimeoutError."""
        cfg = BoundedExecutionConfig(
            enabled=True,
            node_timeout_seconds_default=0.01,  # 10ms timeout
        )
        
        async def slow_node(state):
            await asyncio.sleep(999)  # Way over timeout
            return state
        
        bounded_fn = make_bounded(slow_node, "slow_node", cfg)
        
        with pytest.raises(NodeTimeoutError) as exc_info:
            await bounded_fn({"run_id": "test-run"})
        
        assert exc_info.value.node_name == "slow_node"
        assert exc_info.value.timeout_seconds == 0.01
    
    @pytest.mark.asyncio
    async def test_bounded_execution_not_applied_when_disabled(self):
        """P2 requirement: Disabled config doesn't apply timeout."""
        cfg = BoundedExecutionConfig(enabled=False)
        
        call_count = 0
        
        async def fast_node(state):
            nonlocal call_count
            call_count += 1
            return state
        
        bounded_fn = make_bounded(fast_node, "fast_node", cfg)
        
        # Should execute without any timeout enforcement
        result = await bounded_fn({"run_id": "test-run"})
        
        assert call_count == 1
        assert result == {"run_id": "test-run"}
    
    @pytest.mark.asyncio
    async def test_successful_execution_within_timeout(self):
        """Test that fast nodes complete successfully."""
        cfg = BoundedExecutionConfig(
            enabled=True,
            node_timeout_seconds_default=10.0,
        )
        
        async def fast_node(state):
            return {"completed": True, **state}
        
        bounded_fn = make_bounded(fast_node, "fast_node", cfg)
        
        result = await bounded_fn({"run_id": "test-run"})
        
        assert result["completed"] is True
    
    def test_sync_node_wrapping(self):
        """Test that sync nodes are wrapped correctly."""
        cfg = BoundedExecutionConfig(enabled=True)
        
        def sync_node(state):
            return {"sync": True, **state}
        
        bounded_fn = make_bounded(sync_node, "sync_node", cfg)
        
        result = bounded_fn({"run_id": "test-run"})
        
        assert result["sync"] is True


class TestRunBudgetTracker:
    """Tests for RunBudgetTracker."""
    
    def test_node_count_tracking(self):
        """Test that node count is tracked correctly."""
        cfg = BoundedExecutionConfig(max_nodes_executed=10)
        tracker = RunBudgetTracker(cfg, "test-run")
        
        for i in range(5):
            tracker.check_and_increment(f"node_{i}")
        
        summary = tracker.get_summary()
        assert summary["nodes_executed"] == 5
    
    def test_run_budget_node_count_exceeded(self):
        """P2 requirement: Max nodes budget enforcement."""
        cfg = BoundedExecutionConfig(max_nodes_executed=3)
        tracker = RunBudgetTracker(cfg, "test-run")
        
        # First 3 nodes should pass
        for i in range(3):
            tracker.check_and_increment(f"node_{i}")
        
        # 4th node should fail
        with pytest.raises(RunBudgetExceededError) as exc_info:
            tracker.check_and_increment("node_3")
        
        assert "Max nodes 3 exceeded" in str(exc_info.value)
    
    def test_run_budget_wall_time_exceeded(self):
        """P2 requirement: Wall time budget enforcement."""
        cfg = BoundedExecutionConfig(max_run_wall_seconds=0.001)  # 1ms
        tracker = RunBudgetTracker(cfg, "test-run")
        
        # Wait for wall time to exceed
        import time
        time.sleep(0.01)  # 10ms - well over budget
        
        with pytest.raises(RunBudgetExceededError) as exc_info:
            tracker.check_and_increment("late_node")
        
        assert "Max wall time" in str(exc_info.value)
    
    def test_unlimited_budget(self):
        """Test that None values mean unlimited."""
        cfg = BoundedExecutionConfig(
            max_nodes_executed=None,
            max_run_wall_seconds=None,
        )
        tracker = RunBudgetTracker(cfg, "test-run")
        
        # Should not raise even with many nodes
        for i in range(100):
            tracker.check_and_increment(f"node_{i}")
        
        summary = tracker.get_summary()
        assert summary["nodes_executed"] == 100


class TestBoundedRunContext:
    """Tests for bounded_run_context context manager."""
    
    def test_context_sets_tracker(self):
        """Test that context manager sets budget tracker."""
        cfg = BoundedExecutionConfig(enabled=True)
        
        assert get_budget_tracker() is None
        
        with bounded_run_context(cfg, "test-run") as tracker:
            assert get_budget_tracker() is tracker
            assert tracker is not None
        
        assert get_budget_tracker() is None
    
    def test_disabled_context_returns_none(self):
        """Test that disabled config doesn't create tracker."""
        cfg = BoundedExecutionConfig(enabled=False)
        
        with bounded_run_context(cfg, "test-run") as tracker:
            assert tracker is None
            assert get_budget_tracker() is None
    
    def test_context_provides_summary(self):
        """Test that context tracks execution summary."""
        cfg = BoundedExecutionConfig(enabled=True, max_nodes_executed=100)
        
        with bounded_run_context(cfg, "test-run") as tracker:
            tracker.check_and_increment("node_1")
            tracker.check_and_increment("node_2")
            
            summary = tracker.get_summary()
            assert summary["nodes_executed"] == 2
            assert summary["run_id"] == "test-run"


class TestIntegrationWithTimedNode:
    """Integration tests for bounded execution with timed_node wrapper."""
    
    @pytest.mark.asyncio
    async def test_budget_check_in_timed_node(self):
        """Test that timed_node checks budget when tracker is set."""
        from integration_coworker.graph.runtime import timed_node
        from integration_coworker.graph.state import WorkflowState
        
        cfg = BoundedExecutionConfig(enabled=True, max_nodes_executed=1)
        
        async def test_node(state):
            return state
        
        wrapped = timed_node(test_node, enable_checkpoint=False)
        
        # Create minimal state
        state = WorkflowState(
            task_description="test",
            spec_refs=["test.yaml"],
            source_refs=[],
            run_id="test-run",
        )
        
        with bounded_run_context(cfg, "test-run") as tracker:
            # First call should pass
            await wrapped(state)
            
            # Second call should fail (budget exceeded)
            with pytest.raises(RunBudgetExceededError):
                await wrapped(state)


class TestNodeTimeoutOverrides:
    """Tests for per-node timeout overrides."""
    
    def test_default_overrides_for_slow_nodes(self):
        """Test that known slow nodes have default overrides."""
        cfg = get_bounds_config()
        
        # generate_code_and_tests should have longer timeout
        assert cfg.get_timeout_for_node("generate_code_and_tests") == 300.0
        
        # embed_spec_chunks should have longer timeout
        assert cfg.get_timeout_for_node("embed_spec_chunks") == 300.0
    
    def test_custom_override_takes_precedence(self):
        """Test that custom env overrides take precedence."""
        with mock.patch.dict(os.environ, {
            "BOUNDED_EXEC_ENABLED": "true",
            "BOUNDED_EXEC_TIMEOUT_GENERATE_CODE_AND_TESTS": "600.0",
        }, clear=False):
            cfg = get_bounds_config()
            
            assert cfg.get_timeout_for_node("generate_code_and_tests") == 600.0


class TestHITLBoundedExecution:
    """
    Tests for HITL (Human-in-the-Loop) bounded execution behavior.
    
    These tests verify that HITL nodes (using interrupt()) are:
    1. Exempt from node timeouts (can pause indefinitely)
    2. Don't burn wall clock budget while paused
    """
    
    @pytest.mark.asyncio
    async def test_hitl_node_does_not_timeout(self):
        """
        P2 HITL requirement: HITL nodes can pause indefinitely without timeout.
        
        Scenario:
        - Node timeout is 50ms
        - HITL node waits for 100ms (simulating human approval delay)
        - Should NOT raise NodeTimeoutError
        
        This prevents false timeouts on legitimate HITL pauses.
        """
        cfg = BoundedExecutionConfig(
            enabled=True,
            node_timeout_seconds_default=0.05,  # 50ms timeout for non-HITL
            hitl_exempt_nodes={"hitl_review_gate", "test_hitl_node"},
        )
        
        hitl_invoked = False
        
        async def hitl_node(state):
            """Simulate HITL node that takes longer than default timeout."""
            nonlocal hitl_invoked
            hitl_invoked = True
            # Simulate waiting for human approval - exceeds 50ms timeout
            await asyncio.sleep(0.1)  # 100ms
            return {**state, "hitl_approved": True}
        
        bounded_fn = make_bounded(hitl_node, "test_hitl_node", cfg)
        
        state = {"run_id": "hitl-test-123"}
        
        # Should NOT timeout despite exceeding default timeout
        result = await bounded_fn(state)
        
        assert hitl_invoked
        assert result["hitl_approved"] is True
    
    @pytest.mark.asyncio
    async def test_hitl_timeout_exemption_per_config(self):
        """
        Verify that get_timeout_for_node returns None for HITL nodes.
        """
        cfg = BoundedExecutionConfig(
            enabled=True,
            node_timeout_seconds_default=120.0,
            hitl_exempt_nodes={"hitl_review_gate"},
        )
        
        # HITL node should have None timeout (no timeout)
        assert cfg.get_timeout_for_node("hitl_review_gate") is None
        
        # Non-HITL node should have default timeout
        assert cfg.get_timeout_for_node("regular_node") == 120.0
    
    @pytest.mark.asyncio
    async def test_paused_time_does_not_burn_wall_budget(self):
        """
        P2 HITL requirement: Time spent paused at interrupt() doesn't count
        against max wall time budget.
        
        Scenario:
        - Max wall time is 100ms
        - HITL node pauses for 200ms (simulating human approval delay)
        - After resume, should NOT fail with wall budget exceeded
        
        This prevents false "budget exceeded" on legitimate long approval waits.
        """
        cfg = BoundedExecutionConfig(
            enabled=True,
            max_run_wall_seconds=0.1,  # 100ms wall budget
            max_nodes_executed=100,
            hitl_exempt_nodes={"test_hitl_node"},
        )
        
        hitl_pause_duration = 0.2  # 200ms - exceeds wall budget
        
        async def hitl_node(state):
            """HITL node that pauses longer than wall budget."""
            await asyncio.sleep(hitl_pause_duration)  # Simulate approval wait
            return {**state, "hitl_done": True}
        
        async def post_hitl_node(state):
            """Node that runs after HITL approval."""
            return {**state, "post_hitl": True}
        
        bounded_hitl = make_bounded(hitl_node, "test_hitl_node", cfg)
        bounded_post = make_bounded(post_hitl_node, "post_hitl_node", cfg)
        
        with bounded_run_context(cfg, "wall-budget-test") as tracker:
            state = {"run_id": "wall-budget-test"}
            
            # HITL node should not count against wall budget
            state = await bounded_hitl(state)
            assert state["hitl_done"] is True
            
            # Post-HITL node should succeed (budget not exhausted)
            state = await bounded_post(state)
            assert state["post_hitl"] is True
            
            # Verify effective elapsed is much less than total elapsed
            summary = tracker.get_summary()
            assert summary["paused_time_seconds"] >= hitl_pause_duration * 0.9
            assert summary["effective_wall_time_seconds"] < hitl_pause_duration
    
    @pytest.mark.asyncio
    async def test_wall_budget_pauses_and_resumes_correctly(self):
        """
        Test that pause/resume tracking is accurate.
        """
        cfg = BoundedExecutionConfig(
            enabled=True,
            max_run_wall_seconds=10.0,
        )
        
        tracker = RunBudgetTracker(cfg, "pause-test")
        
        # Initial state - not paused
        assert not tracker.is_paused()
        
        # Pause
        tracker.pause_wall_clock()
        assert tracker.is_paused()
        
        # Wait while paused
        await asyncio.sleep(0.05)  # 50ms paused
        
        # Resume
        tracker.resume_wall_clock()
        assert not tracker.is_paused()
        
        # Verify paused time was tracked
        summary = tracker.get_summary()
        assert summary["paused_time_seconds"] >= 0.04  # Allow some timing slack
    
    @pytest.mark.asyncio
    async def test_non_hitl_node_still_times_out(self):
        """
        Verify that non-HITL nodes still timeout normally.
        """
        cfg = BoundedExecutionConfig(
            enabled=True,
            node_timeout_seconds_default=0.05,  # 50ms
            hitl_exempt_nodes={"hitl_review_gate"},  # Only this is exempt
        )
        
        async def slow_regular_node(state):
            await asyncio.sleep(0.1)  # 100ms - exceeds timeout
            return state
        
        bounded_fn = make_bounded(slow_regular_node, "regular_slow", cfg)
        
        with pytest.raises(NodeTimeoutError) as exc_info:
            await bounded_fn({"run_id": "timeout-test"})
        
        assert exc_info.value.node_name == "regular_slow"
    
    @pytest.mark.asyncio
    async def test_hitl_exempt_node_skips_budget_check(self):
        """
        HITL nodes should not increment the node count budget.
        """
        cfg = BoundedExecutionConfig(
            enabled=True,
            max_nodes_executed=2,
            hitl_exempt_nodes={"hitl_node"},
        )
        
        async def hitl_node(state):
            return state
        
        async def regular_node(state):
            return state
        
        bounded_hitl = make_bounded(hitl_node, "hitl_node", cfg)
        bounded_regular = make_bounded(regular_node, "regular_node", cfg)
        
        with bounded_run_context(cfg, "exempt-test") as tracker:
            state = {"run_id": "exempt-test"}
            
            # HITL calls don't count toward budget
            await bounded_hitl(state)
            await bounded_hitl(state)
            await bounded_hitl(state)
            
            # Regular calls count
            await bounded_regular(state)  # Count: 1
            await bounded_regular(state)  # Count: 2
            
            # This should fail - budget exceeded
            with pytest.raises(RunBudgetExceededError):
                await bounded_regular(state)  # Would be count 3


class TestProductionScenarios:
    """
    Production-themed validation scenarios for bounded execution.
    
    These tests verify that bounded execution behaves correctly in
    realistic production scenarios.
    """
    
    @pytest.mark.asyncio
    async def test_slow_node_timeout_stops_run_cleanly(self):
        """
        PRODUCTION SCENARIO: A slow node exceeds timeout.
        
        Expected behavior:
        1. Run stops with NodeTimeoutError
        2. Error is recorded in state
        3. Checkpoint would be saved (if checkpointer active)
        """
        cfg = BoundedExecutionConfig(
            enabled=True,
            node_timeout_seconds_default=0.05,  # 50ms timeout
        )
        
        async def slow_node(state):
            await asyncio.sleep(1.0)  # 1 second - exceeds 50ms timeout
            return state
        
        bounded_fn = make_bounded(slow_node, "slow_node", cfg)
        
        state = {"run_id": "prod-test-123", "errors": []}
        
        with pytest.raises(NodeTimeoutError) as exc_info:
            await bounded_fn(state)
        
        # Error should include node name and timeout
        assert exc_info.value.node_name == "slow_node"
        assert exc_info.value.run_id == "prod-test-123"
        
        # State should have recorded the error
        assert len(state["errors"]) == 1
        assert "bounded_exec" in state["errors"][0]
    
    @pytest.mark.asyncio
    async def test_budget_exceeded_stops_run(self):
        """
        PRODUCTION SCENARIO: Run exceeds max nodes budget.
        
        Expected behavior:
        1. Run stops with RunBudgetExceededError
        2. Error is recorded
        3. Summary shows nodes executed
        """
        cfg = BoundedExecutionConfig(
            enabled=True,
            max_nodes_executed=5,  # Small budget for test
        )
        
        async def fast_node(state):
            return state
        
        bounded_fn = make_bounded(fast_node, "fast_node", cfg)
        
        with bounded_run_context(cfg, "budget-test-123") as tracker:
            state = {"run_id": "budget-test-123", "errors": []}
            
            # Execute 5 nodes (budget limit)
            for i in range(5):
                await bounded_fn(state)
            
            # 6th node should fail
            with pytest.raises(RunBudgetExceededError) as exc_info:
                await bounded_fn(state)
            
            assert "Max nodes 5 exceeded" in str(exc_info.value)
            
            # Summary should show 5 nodes executed
            summary = tracker.get_summary()
            assert summary["nodes_executed"] == 5
    
    @pytest.mark.asyncio
    async def test_successful_run_within_bounds(self):
        """
        PRODUCTION SCENARIO: Normal run within all bounds.
        
        Expected behavior:
        1. All nodes execute successfully
        2. Budget is tracked correctly
        3. No errors
        """
        cfg = BoundedExecutionConfig(
            enabled=True,
            node_timeout_seconds_default=10.0,
            max_nodes_executed=100,
            max_run_wall_seconds=3600.0,
        )
        
        execution_count = 0
        
        async def normal_node(state):
            nonlocal execution_count
            execution_count += 1
            await asyncio.sleep(0.001)  # 1ms - fast node
            return {**state, "step": execution_count}
        
        bounded_fn = make_bounded(normal_node, "normal_node", cfg)
        
        with bounded_run_context(cfg, "success-test-123") as tracker:
            state = {"run_id": "success-test-123"}
            
            # Execute 10 nodes
            for i in range(10):
                state = await bounded_fn(state)
            
            # All should succeed
            assert execution_count == 10
            assert state["step"] == 10
            
            # Budget should be tracked
            summary = tracker.get_summary()
            assert summary["nodes_executed"] == 10
    
    def test_production_profile_enables_bounds(self):
        """
        PRODUCTION SCENARIO: Production profile enables bounded execution.
        
        Expected behavior:
        1. production profile → enabled=True by default
        2. recursion_limit is set
        3. Timeouts are enforced
        """
        with mock.patch.dict(os.environ, {
            "CODEGEN_PROFILE": "production",
        }, clear=False):
            # Clear explicit setting
            env = {k: v for k, v in os.environ.items() if k != "BOUNDED_EXEC_ENABLED"}
            with mock.patch.dict(os.environ, env, clear=True):
                os.environ["CODEGEN_PROFILE"] = "production"
                cfg = get_bounds_config()
                
                assert cfg.enabled is True
                assert cfg.recursion_limit == 100
                assert cfg.node_timeout_seconds_default == 120.0
    
    def test_invoke_config_has_all_limits(self):
        """
        PRODUCTION SCENARIO: Invoke config includes all limits.
        
        Expected behavior:
        1. recursion_limit is passed to LangGraph
        2. max_concurrency is passed
        3. thread_id is set for checkpointing
        """
        cfg = BoundedExecutionConfig(
            enabled=True,
            recursion_limit=75,
            max_concurrency=5,
        )
        
        invoke_cfg = get_invoke_config("prod-thread-123", cfg)
        
        assert invoke_cfg["recursion_limit"] == 75
        assert invoke_cfg["max_concurrency"] == 5
        assert invoke_cfg["configurable"]["thread_id"] == "prod-thread-123"


class TestGlobalTimeoutError:
    """
    Tests for GlobalTimeoutError (Item B: Hard global workflow timeout).
    
    GlobalTimeoutError is raised when the entire workflow exceeds the
    max_run_wall_seconds limit, using asyncio.timeout() (Python 3.11+).
    
    This is independent of:
    - NodeTimeoutError (per-node timeout via asyncio.wait_for)
    - RunBudgetExceededError (soft budget checked at node transitions)
    
    The hard global timeout catches runaway nodes that don't check budget
    at transitions.
    """
    
    def test_global_timeout_error_attributes(self):
        """Test that GlobalTimeoutError has correct attributes."""
        err = GlobalTimeoutError(3600.0, "test-run-123")
        
        assert err.timeout_seconds == 3600.0
        assert err.run_id == "test-run-123"
        assert "3600.0s" in str(err)
        assert "test-run-123" in str(err)
    
    def test_global_timeout_error_without_run_id(self):
        """Test GlobalTimeoutError with empty run_id."""
        err = GlobalTimeoutError(1800.0)
        
        assert err.timeout_seconds == 1800.0
        assert err.run_id == ""
        assert "1800.0s" in str(err)
        assert "run_id=" not in str(err)
    
    def test_global_timeout_error_is_bounded_execution_error(self):
        """Test that GlobalTimeoutError inherits from BoundedExecutionError."""
        from integration_coworker.graph.bounds import BoundedExecutionError
        
        err = GlobalTimeoutError(3600.0, "test-run")
        
        assert isinstance(err, BoundedExecutionError)
        assert isinstance(err, Exception)
    
    @pytest.mark.asyncio
    async def test_asyncio_timeout_raises_timeout_error(self):
        """
        Verify that asyncio.timeout() raises TimeoutError (Python 3.11+).
        
        This confirms the primitive we use for global timeout.
        """
        async def slow_task():
            await asyncio.sleep(1.0)
            return "done"
        
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.01):  # 10ms timeout
                await slow_task()
    
    @pytest.mark.asyncio
    async def test_global_timeout_conversion_pattern(self):
        """
        Test the pattern used in runtime.py to convert TimeoutError to GlobalTimeoutError.
        
        This mimics the actual implementation in _run_workflow_async().
        Python 3.11+: asyncio.timeout() raises built-in TimeoutError (not asyncio.TimeoutError).
        See: https://docs.python.org/3/library/asyncio-task.html#asyncio.timeout
        """
        timeout_seconds = 0.01  # 10ms
        run_id = "conversion-test"
        
        async def slow_task():
            await asyncio.sleep(1.0)
            return "done"
        
        with pytest.raises(GlobalTimeoutError) as exc_info:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await slow_task()
            except TimeoutError:  # Python 3.11+: asyncio.timeout() raises TimeoutError (built-in)
                raise GlobalTimeoutError(timeout_seconds, run_id)
        
        assert exc_info.value.timeout_seconds == timeout_seconds
        assert exc_info.value.run_id == run_id
    
    @pytest.mark.asyncio
    async def test_fast_task_completes_without_timeout(self):
        """Test that fast tasks complete successfully within timeout."""
        timeout_seconds = 1.0  # 1 second
        
        async def fast_task():
            await asyncio.sleep(0.001)  # 1ms
            return "done"
        
        # Should NOT raise
        async with asyncio.timeout(timeout_seconds):
            result = await fast_task()
        
        assert result == "done"
    
    @pytest.mark.asyncio
    async def test_none_timeout_runs_without_limit(self):
        """
        Test that None timeout means no time limit (mimics runtime.py pattern).
        
        When max_run_wall_seconds is None, the workflow should run without
        asyncio.timeout() being applied.
        """
        timeout_seconds = None  # No timeout
        
        async def task_that_would_timeout():
            # This would timeout if 10ms timeout was applied
            await asyncio.sleep(0.02)  # 20ms
            return "completed"
        
        # Mimic the runtime.py pattern: skip timeout when None
        if timeout_seconds is not None:
            async with asyncio.timeout(timeout_seconds):
                result = await task_that_would_timeout()
        else:
            # No timeout - runs without limit
            result = await task_that_would_timeout()
        
        assert result == "completed"
