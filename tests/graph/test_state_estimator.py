"""
Test for state size estimation - regression tests for V22-001 fix.

These tests FAIL with the buggy implementation and PASS after fix.
"""

import sys
import pytest
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from unittest.mock import MagicMock


# Fake classes for testing without full imports
@dataclass
class FakeSourceFile:
    path: str
    content: str
    language: str = "python"
    size_bytes: int = 0
    
    def __post_init__(self):
        self.size_bytes = len(self.content)


@dataclass 
class FakeRepoSnapshot:
    files: Dict[str, FakeSourceFile] = field(default_factory=dict)
    root: str = "/tmp"
    

@dataclass
class FakeWorkflowState:
    """Minimal state for testing size estimation."""
    run_id: str = "test-run"
    task_description: str = "Test task"
    repo_snapshot: Optional[FakeRepoSnapshot] = None
    spec_documents: List[Dict[str, Any]] = field(default_factory=list)
    code_artifacts: List[Dict[str, Any]] = field(default_factory=list)
    completed_steps: set = field(default_factory=set)
    node_timings: Dict[str, float] = field(default_factory=dict)
    persisted_ids: Dict[str, Any] = field(default_factory=dict)
    openapi_spec: Optional[Dict] = None
    raw_spec_content: Optional[str] = None


class TestStateSizeEstimator:
    """Tests for _get_state_size() function."""
    
    def test_none_state_returns_zero(self):
        """None state should return 0."""
        from integration_coworker.graph.runtime import _get_state_size
        assert _get_state_size(None) == 0
    
    def test_empty_state_small(self):
        """Empty state should be measured in KB, not GB."""
        from integration_coworker.graph.runtime import _get_state_size
        
        state = FakeWorkflowState()
        size = _get_state_size(state)
        
        # Empty state should be < 10KB
        assert size < 10_000, f"Empty state estimated at {size} bytes, expected < 10KB"
    
    def test_string_field_measured_by_length(self):
        """String fields should be measured by their length."""
        from integration_coworker.graph.runtime import _get_state_size
        
        state = FakeWorkflowState()
        state.task_description = "x" * 10_000  # 10KB string
        
        size = _get_state_size(state)
        
        # Should include the 10KB string
        assert size >= 10_000, f"10KB string not measured: size={size}"
    
    def test_nested_dict_measured_recursively(self):
        """Nested dicts should have their contents measured."""
        from integration_coworker.graph.runtime import _get_state_size
        
        state = FakeWorkflowState()
        state.openapi_spec = {
            "paths": {
                "/api/v1": {"description": "x" * 5000},  # 5KB
                "/api/v2": {"description": "y" * 5000},  # 5KB
            }
        }
        
        size = _get_state_size(state)
        
        # Should measure nested string content (~10KB)
        assert size >= 10_000, f"Nested dict content not measured: size={size}"
    
    def test_repo_snapshot_files_measured(self):
        """RepoSnapshot with files should measure file content."""
        from integration_coworker.graph.runtime import _get_state_size
        
        # Create repo snapshot with 100 files × 1KB each = ~100KB
        files = {
            f"src/file_{i}.py": FakeSourceFile(
                path=f"src/file_{i}.py",
                content="x" * 1000
            )
            for i in range(100)
        }
        snapshot = FakeRepoSnapshot(files=files)
        
        state = FakeWorkflowState()
        state.repo_snapshot = snapshot
        
        size = _get_state_size(state)
        
        # Should measure ~100KB of file content
        # Allow some variance but definitely more than 1KB (shallow) and less than 1GB (buggy)
        assert 50_000 <= size <= 500_000, (
            f"RepoSnapshot with 100×1KB files estimated at {size} bytes, "
            f"expected 50KB-500KB"
        )
    
    def test_size_capped_at_maximum(self):
        """Estimated size should be capped to prevent absurd values."""
        from integration_coworker.graph.runtime import _get_state_size
        
        state = FakeWorkflowState()
        # Create pathologically large nested structure
        state.openapi_spec = {
            f"path_{i}": {f"nested_{j}": "x" * 1000 for j in range(100)}
            for i in range(1000)
        }
        # This is ~100MB of strings
        
        size = _get_state_size(state)
        
        # Should be capped at reasonable max (100MB)
        MAX_REASONABLE = 100_000_000  # 100MB
        assert size <= MAX_REASONABLE, (
            f"Estimated size {size} exceeds cap of {MAX_REASONABLE}"
        )
    
    def test_no_exponential_growth(self):
        """Size should not grow exponentially on repeated calls."""
        from integration_coworker.graph.runtime import _get_state_size
        
        state = FakeWorkflowState()
        state.openapi_spec = {"data": "x" * 10000}
        
        size1 = _get_state_size(state)
        size2 = _get_state_size(state)
        size3 = _get_state_size(state)
        
        # Sizes should be consistent, not doubling
        assert size1 == size2 == size3, (
            f"Size changed on repeated calls: {size1}, {size2}, {size3}"
        )
    
    def test_realistic_workflow_state_reasonable(self):
        """A realistic workflow state should estimate in MB, not GB."""
        from integration_coworker.graph.runtime import _get_state_size
        
        # Build realistic state similar to actual workflow
        state = FakeWorkflowState()
        
        # 500 files × 2KB each (repo)
        files = {
            f"src/module_{i}/file_{j}.py": FakeSourceFile(
                path=f"src/module_{i}/file_{j}.py", 
                content="# Python\n" + "x" * 2000
            )
            for i in range(50) for j in range(10)
        }
        state.repo_snapshot = FakeRepoSnapshot(files=files)
        
        # 100 spec documents × 1KB each
        state.spec_documents = [
            {"name": f"spec_{i}", "content": "y" * 1000}
            for i in range(100)
        ]
        
        # 50 code artifacts × 2KB each
        state.code_artifacts = [
            {"name": f"artifact_{i}", "code": "z" * 2000}
            for i in range(50)
        ]
        
        # OpenAPI spec ~1MB
        state.openapi_spec = {
            "paths": {f"/api/{i}": {"desc": "w" * 100} for i in range(1000)}
        }
        
        # Tracking dicts (small)
        state.node_timings = {f"node_{i}": float(i) for i in range(30)}
        state.persisted_ids = {f"id_{i}": i for i in range(200)}
        state.completed_steps = set(range(20))
        
        size = _get_state_size(state)
        
        # Realistic state should be 1-50MB, definitely not 139GB
        assert 500_000 <= size <= 50_000_000, (
            f"Realistic workflow state estimated at {size} bytes ({size/1e6:.1f} MB), "
            f"expected 0.5-50 MB"
        )
    
    def test_v22_001_regression_no_139gb(self):
        """V22-001: State should NEVER estimate to 139GB."""
        from integration_coworker.graph.runtime import _get_state_size
        
        state = FakeWorkflowState()
        # Even with pathological data, should not reach 139GB
        state.raw_spec_content = "x" * 26_000_000  # 26MB (Twilio spec size)
        state.openapi_spec = {"huge": {"nested": "y" * 10_000_000}}
        
        size = _get_state_size(state)
        
        V22_BUGGY_SIZE = 138_924_226_074  # 139GB
        assert size < V22_BUGGY_SIZE / 1000, (
            f"Estimated size {size} is unreasonably large (V22-001 regression)"
        )


class TestRSSMonitoring:
    """Tests for RSS (actual memory) monitoring."""
    
    def test_rss_helper_returns_positive(self):
        """RSS monitoring should return positive value if psutil is available."""
        from integration_coworker.graph.runtime import _get_rss_mb
        
        rss = _get_rss_mb()
        
        # If psutil is not available, -1.0 is returned
        if rss < 0:
            pytest.skip("psutil not available - RSS monitoring disabled")
        
        assert rss > 0, "RSS should be positive when psutil is available"
    
    def test_rss_helper_handles_missing_psutil(self):
        """RSS helper should handle missing psutil gracefully."""
        from integration_coworker.graph.runtime import _get_rss_mb
        
        # The function should not crash regardless of psutil availability
        rss = _get_rss_mb()
        # Returns positive (psutil available) or -1.0 (psutil not available)
        assert isinstance(rss, float), "RSS should return a float"
    
    def test_rss_bytes_returns_integer(self):
        """RSS bytes helper should return integer value."""
        from integration_coworker.graph.runtime import _get_rss_bytes
        
        rss = _get_rss_bytes()
        assert isinstance(rss, int), f"RSS bytes should be int, got {type(rss)}"
        # Could be -1 if psutil not available, otherwise positive
        assert rss >= -1, "RSS should be >= -1"


class TestTracemalloc:
    """Tests for tracemalloc integration."""
    
    def test_tracemalloc_init_is_idempotent(self):
        """Tracemalloc init can be called multiple times safely."""
        from integration_coworker.graph.runtime import (
            _init_tracemalloc_if_enabled,
            _get_tracemalloc_stats,
        )
        
        # First call
        _init_tracemalloc_if_enabled()
        stats1 = _get_tracemalloc_stats()
        
        # Second call (should be idempotent)
        _init_tracemalloc_if_enabled()
        stats2 = _get_tracemalloc_stats()
        
        # Should not crash and return valid stats
        assert isinstance(stats1, dict), "Stats should be a dict"
        assert isinstance(stats2, dict), "Stats should be a dict"
        assert "enabled" in stats1
        assert "current_bytes" in stats1
        assert "peak_bytes" in stats1
    
    def test_tracemalloc_stats_structure(self):
        """Tracemalloc stats should have correct structure."""
        from integration_coworker.graph.runtime import _get_tracemalloc_stats
        
        stats = _get_tracemalloc_stats()
        
        assert isinstance(stats, dict), "Stats should be a dict"
        assert "enabled" in stats, "Stats should have 'enabled' key"
        assert "current_bytes" in stats, "Stats should have 'current_bytes' key"
        assert "peak_bytes" in stats, "Stats should have 'peak_bytes' key"
        
        if stats["enabled"]:
            assert stats["current_bytes"] >= 0, "Current bytes should be >= 0"
            assert stats["peak_bytes"] >= 0, "Peak bytes should be >= 0"


class TestMemoryDiagnostics:
    """Tests for comprehensive memory diagnostics."""
    
    def test_memory_diagnostics_structure(self):
        """Memory diagnostics should return proper structure."""
        from integration_coworker.graph.runtime import _get_memory_diagnostics
        
        diag = _get_memory_diagnostics()
        
        assert isinstance(diag, dict), "Diagnostics should be a dict"
        assert "rss_bytes" in diag, "Should have rss_bytes"
        assert "rss_mb" in diag, "Should have rss_mb"
        assert isinstance(diag["rss_bytes"], int), "rss_bytes should be int"
        assert isinstance(diag["rss_mb"], float), "rss_mb should be float"
    
    def test_oom_check_structure(self):
        """OOM likelihood check should return proper structure."""
        from integration_coworker.graph.runtime import _check_oom_likelihood
        
        # _check_oom_likelihood calls _get_memory_diagnostics internally
        oom = _check_oom_likelihood()
        
        assert isinstance(oom, dict), "OOM check should be a dict"
        assert "at_risk" in oom, "Should have at_risk key"
        assert "diagnosis" in oom, "Should have diagnosis key"
        assert isinstance(oom["at_risk"], bool), "at_risk should be bool"
        assert isinstance(oom["diagnosis"], str), "diagnosis should be str"


class TestDeterministicEstimator:
    """Tests for deterministic estimator behavior (V22-001 Gap 3)."""
    
    def test_dict_estimation_is_deterministic(self):
        """Dict estimation should be deterministic across calls."""
        from integration_coworker.graph.runtime import _get_state_size_metrics
        
        state = FakeWorkflowState()
        state.openapi_spec = {
            "b": "value_b",
            "a": "value_a", 
            "c": "value_c",
        }
        
        results = [_get_state_size_metrics(state) for _ in range(5)]
        estimates = [r["estimate_capped"] for r in results]
        
        # All estimates should be identical
        assert len(set(estimates)) == 1, (
            f"Dict estimation not deterministic: {estimates}"
        )
    
    def test_set_estimation_is_deterministic(self):
        """Set estimation should be deterministic across calls."""
        from integration_coworker.graph.runtime import _get_state_size_metrics
        
        state = FakeWorkflowState()
        state.completed_steps = {3, 1, 4, 1, 5, 9, 2, 6}  # Intentionally unordered
        
        results = [_get_state_size_metrics(state) for _ in range(5)]
        estimates = [r["estimate_capped"] for r in results]
        
        # All estimates should be identical
        assert len(set(estimates)) == 1, (
            f"Set estimation not deterministic: {estimates}"
        )
    
    def test_metrics_include_method_and_params(self):
        """Size metrics should include method and parameters."""
        from integration_coworker.graph.runtime import _get_state_size_metrics
        
        state = FakeWorkflowState()
        state.task_description = "x" * 1000
        
        metrics = _get_state_size_metrics(state)
        
        assert "method" in metrics, "Metrics should include 'method'"
        assert "max_depth" in metrics, "Metrics should include 'max_depth'"
        assert "max_sample_items" in metrics, "Metrics should include 'max_sample_items'"
        assert metrics["method"] == "sampled_recursive", (
            f"Expected method 'sampled_recursive', got '{metrics['method']}'"
        )
    
    def test_uncapped_vs_capped_values(self):
        """Metrics should provide both uncapped and capped values."""
        from integration_coworker.graph.runtime import _get_state_size_metrics
        
        state = FakeWorkflowState()
        # Create moderately large state
        state.raw_spec_content = "x" * 10_000_000  # 10MB
        
        metrics = _get_state_size_metrics(state)
        
        assert "estimate_uncapped" in metrics, "Metrics should include uncapped"
        assert "estimate_capped" in metrics, "Metrics should include capped"
        
        # Both should be positive
        assert metrics["estimate_uncapped"] > 0, "Uncapped should be positive"
        assert metrics["estimate_capped"] > 0, "Capped should be positive"
        
        # Capped should be <= uncapped
        assert metrics["estimate_capped"] <= metrics["estimate_uncapped"], (
            f"Capped {metrics['estimate_capped']} > uncapped {metrics['estimate_uncapped']}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
