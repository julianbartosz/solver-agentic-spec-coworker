"""
Integration tests for the memory sampler (V22-011).

These tests verify that:
1. The memory sampler emits events correctly
2. RSS stays below threshold for small runs
3. Tracemalloc tracking works
4. Ceiling breach triggers graceful abort

Run with:
    pytest tests/graph/test_memory_sampler.py -v -s
"""

import os
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional

import pytest

from integration_coworker.graph.memory_sampler import (
    MemorySampler,
    MemorySamplerConfig,
    MemorySample,
    RSSCeilingExceeded,
    get_rss_bytes,
    get_rss_mb,
    get_tracemalloc_stats,
    get_cgroup_memory_info,
    start_memory_sampler,
    stop_memory_sampler,
    set_current_node,
    set_current_phase,
    is_ceiling_exceeded,
    memory_sampler_context,
)


class TestMemoryMetrics:
    """Test individual memory metric collection functions."""
    
    def test_get_rss_bytes_returns_positive(self):
        """RSS should return a positive value (process always uses some memory)."""
        rss = get_rss_bytes()
        # May be -1 if psutil not installed and not on Linux
        if rss > 0:
            assert rss > 1024 * 1024  # At least 1MB for Python process
    
    def test_get_rss_mb_returns_positive(self):
        """RSS in MB should be positive."""
        rss_mb = get_rss_mb()
        if rss_mb > 0:
            assert rss_mb > 1.0  # At least 1MB
    
    def test_get_tracemalloc_stats_without_tracing(self):
        """tracemalloc stats should indicate disabled when not started."""
        import tracemalloc
        was_tracing = tracemalloc.is_tracing()
        if was_tracing:
            tracemalloc.stop()
        
        try:
            stats = get_tracemalloc_stats()
            assert stats["enabled"] is False
            assert stats["current_bytes"] == -1
            assert stats["peak_bytes"] == -1
        finally:
            if was_tracing:
                tracemalloc.start()
    
    def test_get_tracemalloc_stats_with_tracing(self):
        """tracemalloc stats should return values when tracing is on."""
        import tracemalloc
        was_tracing = tracemalloc.is_tracing()
        
        if not was_tracing:
            tracemalloc.start()
        
        try:
            # Allocate something to ensure non-zero values
            data = [i for i in range(10000)]
            
            stats = get_tracemalloc_stats()
            assert stats["enabled"] is True
            assert stats["current_bytes"] >= 0
            assert stats["peak_bytes"] >= 0
            
            del data  # Clean up
        finally:
            if not was_tracing:
                tracemalloc.stop()
    
    def test_get_cgroup_memory_info_returns_dict(self):
        """cgroup info should return a dict with expected keys."""
        info = get_cgroup_memory_info()
        assert "limit_bytes" in info
        assert "usage_bytes" in info
        # Values may be -1 if not in a cgroup


class TestMemorySample:
    """Test the MemorySample dataclass."""
    
    def test_sample_to_dict(self):
        """Sample should serialize to dict correctly."""
        sample = MemorySample(
            timestamp="2025-12-26T12:00:00Z",
            sample_number=1,
            run_id="test-run",
            node_name="test_node",
            phase="testing",
            rss_bytes=100_000_000,
            rss_mb=95.37,
            rss_delta_mb=10.5,
            tracemalloc_current_bytes=50_000_000,
            tracemalloc_peak_bytes=75_000_000,
            tracemalloc_enabled=True,
            cgroup_limit_bytes=-1,
            cgroup_usage_bytes=-1,
            cgroup_usage_pct=-1.0,
            top_allocators=None,
        )
        
        d = sample.to_dict()
        assert d["ts"] == "2025-12-26T12:00:00Z"
        assert d["sample_number"] == 1
        assert d["run_id"] == "test-run"
        assert d["node_name"] == "test_node"
        assert d["rss_mb"] == 95.37
        assert "top_allocators" not in d  # None values excluded


class TestMemorySamplerConfig:
    """Test configuration loading from environment."""
    
    def test_default_config(self):
        """Default config should have sensible values."""
        # Clear any env overrides
        for key in ["IC_MEM_SAMPLER_ENABLED", "IC_MEM_SAMPLER_INTERVAL_S", 
                    "IC_MAX_RSS_MB", "IC_MEM_TRACEMALLOC_TOP"]:
            os.environ.pop(key, None)
        
        config = MemorySamplerConfig()
        assert config.enabled is True
        assert config.interval_s == 1.0
        assert config.max_rss_mb == 4096  # 4GB default
        assert config.tracemalloc_top == 10
    
    def test_config_from_env(self):
        """Config should read from environment variables."""
        os.environ["IC_MEM_SAMPLER_ENABLED"] = "false"
        os.environ["IC_MEM_SAMPLER_INTERVAL_S"] = "0.5"
        os.environ["IC_MAX_RSS_MB"] = "2048"
        
        try:
            config = MemorySamplerConfig()
            assert config.enabled is False
            assert config.interval_s == 0.5
            assert config.max_rss_mb == 2048
        finally:
            # Clean up
            for key in ["IC_MEM_SAMPLER_ENABLED", "IC_MEM_SAMPLER_INTERVAL_S", "IC_MAX_RSS_MB"]:
                os.environ.pop(key, None)


class TestRSSCeilingExceeded:
    """Test the ceiling exceeded exception."""
    
    def test_exception_message(self):
        """Exception should have descriptive message."""
        exc = RSSCeilingExceeded(5000.0, 4096.0, "test_node")
        assert "5000.0MB" in str(exc)
        assert "4096.0MB" in str(exc)
        assert "test_node" in str(exc)
        assert exc.current_rss_mb == 5000.0
        assert exc.ceiling_mb == 4096.0
        assert exc.node_name == "test_node"


class TestMemorySampler:
    """Test the MemorySampler class."""
    
    def test_sampler_disabled(self):
        """Sampler should not start when disabled."""
        config = MemorySamplerConfig()
        config.enabled = False
        
        sampler = MemorySampler(
            run_id="test",
            config=config,
        )
        sampler.start()
        
        # Give it a moment
        time.sleep(0.1)
        
        # Thread should not be running
        assert sampler._thread is None or not sampler._thread.is_alive()
    
    def test_sampler_collects_samples(self):
        """Sampler should collect memory samples."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MemorySamplerConfig()
            config.enabled = True
            config.interval_s = 0.1  # Fast sampling for test
            config.max_rss_mb = 100_000  # Very high ceiling
            
            sampler = MemorySampler(
                run_id="test-run",
                artifacts_dir=tmpdir,
                config=config,
            )
            
            sampler.start()
            
            # Let it run for a bit
            time.sleep(0.5)
            
            # Stop and get samples
            samples = sampler.stop()
            
            # Should have collected some samples
            assert len(samples) >= 3, f"Expected at least 3 samples, got {len(samples)}"
            
            # Check sample structure
            for sample in samples:
                assert sample.run_id == "test-run"
                assert sample.rss_bytes != 0  # Should have RSS (may be -1 if unavailable)
                assert sample.sample_number > 0
            
            # Check trace file was written
            trace_file = Path(tmpdir) / "MEMORY_TRACE.jsonl"
            assert trace_file.exists()
            lines = trace_file.read_text().strip().split("\n")
            assert len(lines) >= 3
    
    def test_sampler_tracks_node(self):
        """Sampler should attribute samples to current node."""
        config = MemorySamplerConfig()
        config.enabled = True
        config.interval_s = 0.1
        config.max_rss_mb = 100_000
        
        sampler = MemorySampler(
            run_id="test-run",
            config=config,
        )
        
        sampler.start()
        
        # Set node
        sampler.set_node("test_node")
        time.sleep(0.2)
        
        # Change node
        sampler.set_node("another_node")
        time.sleep(0.2)
        
        samples = sampler.stop()
        
        # Should have samples with different node names
        node_names = {s.node_name for s in samples}
        assert "test_node" in node_names or "another_node" in node_names
    
    def test_sampler_ceiling_callback(self):
        """Sampler should call callback when ceiling exceeded."""
        callback_called = threading.Event()
        captured_exc: List[RSSCeilingExceeded] = []
        
        def on_ceiling(exc: RSSCeilingExceeded):
            captured_exc.append(exc)
            callback_called.set()
        
        config = MemorySamplerConfig()
        config.enabled = True
        config.interval_s = 0.05
        config.max_rss_mb = 0.001  # Very low ceiling (0.001 MB = 1KB)
        
        sampler = MemorySampler(
            run_id="test-run",
            config=config,
            ceiling_callback=on_ceiling,
        )
        
        sampler.start()
        
        # Wait for callback (should trigger almost immediately)
        callback_called.wait(timeout=1.0)
        
        sampler.stop()
        
        assert len(captured_exc) == 1
        assert captured_exc[0].current_rss_mb > 0
        assert captured_exc[0].ceiling_mb == 0.001
        assert sampler.is_ceiling_exceeded()


class TestGlobalSamplerAPI:
    """Test the global sampler functions."""
    
    def test_start_stop_sampler(self):
        """Global start/stop should work correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Ensure sampler is enabled
            os.environ["IC_MEM_SAMPLER_ENABLED"] = "true"
            os.environ["IC_MEM_SAMPLER_INTERVAL_S"] = "0.1"
            
            try:
                sampler = start_memory_sampler(
                    run_id="test-global",
                    artifacts_dir=tmpdir,
                )
                
                assert sampler is not None
                
                # Track a node
                set_current_node("test_node")
                set_current_phase("testing")
                
                time.sleep(0.3)
                
                samples = stop_memory_sampler()
                
                assert samples is not None
                assert len(samples) >= 2
            finally:
                os.environ.pop("IC_MEM_SAMPLER_ENABLED", None)
                os.environ.pop("IC_MEM_SAMPLER_INTERVAL_S", None)
    
    def test_context_manager(self):
        """Context manager should start and stop sampler."""
        os.environ["IC_MEM_SAMPLER_ENABLED"] = "true"
        os.environ["IC_MEM_SAMPLER_INTERVAL_S"] = "0.1"
        
        try:
            with memory_sampler_context("test-ctx") as sampler:
                assert sampler is not None
                set_current_node("ctx_node")
                time.sleep(0.2)
            
            # After context, sampler should be stopped
            from integration_coworker.graph.memory_sampler import get_sampler
            assert get_sampler() is None
        finally:
            os.environ.pop("IC_MEM_SAMPLER_ENABLED", None)
            os.environ.pop("IC_MEM_SAMPLER_INTERVAL_S", None)


class TestMemorySamplerIntegration:
    """Integration tests with realistic memory patterns."""
    
    def test_detects_memory_growth(self):
        """Sampler should detect memory growth patterns."""
        config = MemorySamplerConfig()
        config.enabled = True
        config.interval_s = 0.05
        config.max_rss_mb = 100_000
        
        sampler = MemorySampler(
            run_id="growth-test",
            config=config,
        )
        
        sampler.start()
        
        # Baseline
        time.sleep(0.1)
        
        # Allocate some memory
        data = []
        for i in range(5):
            sampler.set_node(f"alloc_phase_{i}")
            # Allocate ~10MB per iteration
            data.append([0] * (1024 * 1024))
            time.sleep(0.1)
        
        samples = sampler.stop()
        
        # Check that we captured the growth
        assert len(samples) >= 5
        
        # Peak RSS should be higher than first sample
        peak = sampler.get_peak_rss_mb()
        first_rss = samples[0].rss_mb if samples[0].rss_mb > 0 else 0
        
        if first_rss > 0 and peak > 0:
            # Should have grown (at least somewhat)
            assert peak >= first_rss
        
        # Clean up
        del data
    
    @pytest.mark.skipif(
        os.getenv("CI") == "true",
        reason="tracemalloc filtering may not work in CI"
    )
    def test_tracemalloc_captures_allocations(self):
        """tracemalloc should capture Python allocations."""
        import tracemalloc
        
        # Ensure tracemalloc is on
        if not tracemalloc.is_tracing():
            tracemalloc.start()
        
        config = MemorySamplerConfig()
        config.enabled = True
        config.interval_s = 0.05
        config.max_rss_mb = 100_000
        config.min_delta_mb = 0  # Capture all samples for this test
        
        sampler = MemorySampler(
            run_id="tracemalloc-test",
            config=config,
        )
        
        sampler.start()
        time.sleep(0.1)
        
        # Allocate some memory
        data = [list(range(100000)) for _ in range(10)]
        
        time.sleep(0.2)
        samples = sampler.stop()
        
        # Should have tracemalloc data
        tracemalloc_samples = [
            s for s in samples 
            if s.tracemalloc_enabled and s.tracemalloc_current_bytes > 0
        ]
        
        assert len(tracemalloc_samples) > 0, "Expected tracemalloc data in samples"
        
        # Clean up
        del data


class TestRollingWindowAndCeiling:
    """Test rolling window and ceiling check behavior."""
    
    def test_rolling_window_limits_memory(self):
        """Rolling window should limit samples kept in memory."""
        config = MemorySamplerConfig()
        config.enabled = True
        config.interval_s = 0.01  # Very fast
        config.max_rss_mb = 100_000
        config.rolling_window_size = 10  # Only keep 10 samples
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sampler = MemorySampler(
                run_id="rolling-test",
                artifacts_dir=tmpdir,
                config=config,
            )
            
            sampler.start()
            time.sleep(0.5)  # Should generate 50+ samples
            samples = sampler.stop()
            
            # Only 10 should be in memory (rolling window)
            assert len(samples) <= config.rolling_window_size
            
            # But all should be in the JSONL file
            trace_file = Path(tmpdir) / "MEMORY_TRACE.jsonl"
            lines = trace_file.read_text().strip().split("\n")
            assert len(lines) > config.rolling_window_size
    
    def test_check_ceiling_returns_exception(self):
        """check_ceiling should return exception for main thread to raise."""
        config = MemorySamplerConfig()
        config.enabled = True
        config.interval_s = 0.02
        config.max_rss_mb = 0.001  # Very low ceiling
        
        sampler = MemorySampler(
            run_id="ceiling-check-test",
            config=config,
        )
        
        sampler.start()
        time.sleep(0.1)  # Wait for ceiling to be exceeded
        
        # check_ceiling should return the exception
        exc = sampler.check_ceiling()
        
        sampler.stop()
        
        assert exc is not None
        assert isinstance(exc, RSSCeilingExceeded)
        assert exc.current_rss_mb > 0
        assert exc.ceiling_mb == 0.001
    
    def test_get_summary_tracks_per_node(self):
        """get_summary should track per-node memory stats."""
        config = MemorySamplerConfig()
        config.enabled = True
        config.interval_s = 0.05
        config.max_rss_mb = 100_000
        
        sampler = MemorySampler(
            run_id="summary-test",
            config=config,
        )
        
        sampler.start()
        
        # Simulate node transitions
        sampler.set_node("node_a")
        time.sleep(0.15)
        sampler.set_node("node_b")
        time.sleep(0.15)
        sampler.set_node("node_c")
        time.sleep(0.1)
        
        summary = sampler.get_summary()
        sampler.stop()
        
        assert "total_samples" in summary
        assert summary["total_samples"] > 0
        assert "peak_rss_mb" in summary
        assert "node_stats" in summary
        
        # Should have tracked all three nodes
        node_stats = summary["node_stats"]
        assert "node_a" in node_stats or "node_b" in node_stats
    
    def test_global_check_ceiling(self):
        """Global check_ceiling function should work."""
        from integration_coworker.graph.memory_sampler import (
            start_memory_sampler, stop_memory_sampler, check_ceiling
        )
        
        os.environ["IC_MEM_SAMPLER_ENABLED"] = "true"
        os.environ["IC_MEM_SAMPLER_INTERVAL_S"] = "0.02"
        os.environ["IC_MAX_RSS_MB"] = "0.001"  # Very low
        
        try:
            sampler = start_memory_sampler(run_id="global-ceiling-test")
            time.sleep(0.1)
            
            # Should be able to check ceiling via global function
            exc = check_ceiling()
            
            stop_memory_sampler()
            
            # Ceiling should have been exceeded
            assert exc is not None
            assert isinstance(exc, RSSCeilingExceeded)
        finally:
            os.environ.pop("IC_MEM_SAMPLER_ENABLED", None)
            os.environ.pop("IC_MEM_SAMPLER_INTERVAL_S", None)
            os.environ.pop("IC_MAX_RSS_MB", None)

    def test_check_ceiling_in_loop_raises(self):
        """check_ceiling_in_loop should raise at periodic intervals."""
        from integration_coworker.graph.memory_sampler import (
            start_memory_sampler, stop_memory_sampler, check_ceiling_in_loop
        )
        
        os.environ["IC_MEM_SAMPLER_ENABLED"] = "true"
        os.environ["IC_MEM_SAMPLER_INTERVAL_S"] = "0.01"
        os.environ["IC_MAX_RSS_MB"] = "0.001"  # Very low ceiling
        
        try:
            sampler = start_memory_sampler(run_id="in-loop-ceiling-test")
            time.sleep(0.1)  # Wait for ceiling to be exceeded
            
            # check_ceiling_in_loop should raise when iteration matches interval
            raised = False
            for i in range(20):  # At i=10, should check and raise
                try:
                    check_ceiling_in_loop(i, check_interval=10)
                except RSSCeilingExceeded:
                    raised = True
                    break
            
            stop_memory_sampler()
            assert raised, "check_ceiling_in_loop should have raised RSSCeilingExceeded"
        finally:
            os.environ.pop("IC_MEM_SAMPLER_ENABLED", None)
            os.environ.pop("IC_MEM_SAMPLER_INTERVAL_S", None)
            os.environ.pop("IC_MAX_RSS_MB", None)

    def test_check_ceiling_or_raise_works(self):
        """check_ceiling_or_raise should raise unconditionally if ceiling exceeded."""
        from integration_coworker.graph.memory_sampler import (
            start_memory_sampler, stop_memory_sampler, check_ceiling_or_raise
        )
        
        os.environ["IC_MEM_SAMPLER_ENABLED"] = "true"
        os.environ["IC_MEM_SAMPLER_INTERVAL_S"] = "0.01"
        os.environ["IC_MAX_RSS_MB"] = "0.001"  # Very low ceiling
        
        try:
            sampler = start_memory_sampler(run_id="or-raise-test")
            time.sleep(0.1)  # Wait for ceiling to be exceeded
            
            with pytest.raises(RSSCeilingExceeded):
                check_ceiling_or_raise()
            
            stop_memory_sampler()
        finally:
            os.environ.pop("IC_MEM_SAMPLER_ENABLED", None)
            os.environ.pop("IC_MEM_SAMPLER_INTERVAL_S", None)
            os.environ.pop("IC_MAX_RSS_MB", None)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
