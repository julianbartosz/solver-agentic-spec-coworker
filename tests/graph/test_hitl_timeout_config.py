"""
Tests for B-004: HITL timeout configuration.

These tests verify:
1. BoundedExecutionConfig has hitl_max_wait_seconds attribute
2. HITL_MAX_WAIT_SECONDS env var is honored
3. HITL interrupt payload includes hitl_requested_at timestamp
4. Default is 86400 seconds (24 hours)
"""

import os
import pytest
import time
from unittest.mock import patch, MagicMock


class TestHITLMaxWaitConfig:
    """Tests for hitl_max_wait_seconds configuration."""
    
    def test_config_has_hitl_max_wait_seconds(self):
        """Verify BoundedExecutionConfig has hitl_max_wait_seconds attribute."""
        from integration_coworker.graph.bounds import BoundedExecutionConfig
        
        config = BoundedExecutionConfig()
        assert hasattr(config, 'hitl_max_wait_seconds')
        assert isinstance(config.hitl_max_wait_seconds, float)
    
    def test_default_hitl_max_wait_is_24_hours(self):
        """Verify default is 86400 seconds (24 hours)."""
        from integration_coworker.graph.bounds import BoundedExecutionConfig
        
        config = BoundedExecutionConfig()
        assert config.hitl_max_wait_seconds == 86400.0
    
    def test_get_bounds_config_includes_hitl_max_wait(self):
        """Verify get_bounds_config() returns hitl_max_wait_seconds."""
        from integration_coworker.graph.bounds import get_bounds_config
        
        config = get_bounds_config()
        assert hasattr(config, 'hitl_max_wait_seconds')
        assert config.hitl_max_wait_seconds == 86400.0
    
    def test_hitl_max_wait_from_env(self):
        """Verify HITL_MAX_WAIT_SECONDS env var is honored."""
        from integration_coworker.graph.bounds import get_bounds_config
        
        with patch.dict(os.environ, {"HITL_MAX_WAIT_SECONDS": "3600"}):
            config = get_bounds_config()
            assert config.hitl_max_wait_seconds == 3600.0
    
    def test_hitl_max_wait_custom_value(self):
        """Verify custom values are accepted."""
        from integration_coworker.graph.bounds import get_bounds_config
        
        with patch.dict(os.environ, {"HITL_MAX_WAIT_SECONDS": "7200.5"}):
            config = get_bounds_config()
            assert config.hitl_max_wait_seconds == 7200.5


class TestHITLTimestamp:
    """Tests for hitl_requested_at timestamp in interrupt payload."""
    
    def test_payload_includes_timestamp(self):
        """Verify interrupt payload includes hitl_requested_at."""
        from integration_coworker.graph.nodes.hitl_gate import _build_interrupt_payload
        from integration_coworker.graph.state import WorkflowState
        
        # Create minimal state with required fields
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test task",
            run_id="test-run",
            provider_code="test-provider",
        )
        
        payload = _build_interrupt_payload(state)
        
        assert "hitl_requested_at" in payload
        assert isinstance(payload["hitl_requested_at"], float)
    
    def test_timestamp_is_current_time(self):
        """Verify timestamp is approximately current time."""
        from integration_coworker.graph.nodes.hitl_gate import _build_interrupt_payload
        from integration_coworker.graph.state import WorkflowState
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test task",
            run_id="test-run",
            provider_code="test-provider",
        )
        
        before = time.time()
        payload = _build_interrupt_payload(state)
        after = time.time()
        
        # Timestamp should be within the time window
        assert before <= payload["hitl_requested_at"] <= after
    
    def test_timestamp_is_unix_epoch(self):
        """Verify timestamp is in Unix epoch format (seconds since 1970)."""
        from integration_coworker.graph.nodes.hitl_gate import _build_interrupt_payload
        from integration_coworker.graph.state import WorkflowState
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test task",
            run_id="test-run",
            provider_code="test-provider",
        )
        
        payload = _build_interrupt_payload(state)
        
        # Unix timestamps for 2020-2030 should be in this range
        min_valid = 1577836800  # 2020-01-01
        max_valid = 1893456000  # 2030-01-01
        
        assert min_valid < payload["hitl_requested_at"] < max_valid


class TestHITLStaleDetection:
    """Tests for detecting stale HITL requests."""
    
    def test_can_compute_age_from_timestamp(self):
        """Verify we can compute age of HITL request from timestamp."""
        from integration_coworker.graph.bounds import get_bounds_config
        
        config = get_bounds_config()
        
        # Simulate a stale request from 2 days ago
        old_timestamp = time.time() - (48 * 3600)  # 48 hours ago
        age_seconds = time.time() - old_timestamp
        
        # This should be over the threshold
        assert age_seconds > config.hitl_max_wait_seconds
    
    def test_fresh_request_under_threshold(self):
        """Verify fresh requests are under threshold."""
        from integration_coworker.graph.bounds import get_bounds_config
        
        config = get_bounds_config()
        
        # Fresh request from 1 hour ago
        recent_timestamp = time.time() - 3600  # 1 hour ago
        age_seconds = time.time() - recent_timestamp
        
        # This should be under the default 24h threshold
        assert age_seconds < config.hitl_max_wait_seconds


class TestHITLDocumentation:
    """Tests to ensure documentation mentions HITL timeout behavior."""
    
    def test_bounds_config_docstring_mentions_hitl(self):
        """Verify BoundedExecutionConfig docstring documents hitl_max_wait_seconds."""
        from integration_coworker.graph.bounds import BoundedExecutionConfig
        
        docstring = BoundedExecutionConfig.__doc__ or ""
        assert "hitl_max_wait_seconds" in docstring
    
    def test_get_bounds_config_docstring_mentions_env_var(self):
        """Verify get_bounds_config docstring documents HITL_MAX_WAIT_SECONDS env var."""
        from integration_coworker.graph.bounds import get_bounds_config
        
        docstring = get_bounds_config.__doc__ or ""
        assert "HITL_MAX_WAIT_SECONDS" in docstring
