"""
Tests for workflow versioning and checkpoint compatibility.

Production Readiness v4 - P0-6:
Tests resume semantics and version validation.
"""

import functools
import pytest
from unittest.mock import patch, MagicMock

from integration_coworker.graph.runtime import (
    get_workflow_version,
    validate_checkpoint_version,
    get_versioned_thread_config,
    WorkflowVersionMismatchError,
    get_thread_config,
)


class TestGetWorkflowVersion:
    """Test get_workflow_version function."""
    
    def setup_method(self):
        """Clear cached version before each test."""
        # Clear the lru_cache
        get_workflow_version.cache_clear()
    
    def teardown_method(self):
        """Clear cache after each test."""
        get_workflow_version.cache_clear()
    
    def test_returns_string(self):
        """Test that get_workflow_version returns a string."""
        version = get_workflow_version()
        assert isinstance(version, str)
        assert len(version) > 0
    
    def test_cached_result(self):
        """Test that result is cached."""
        version1 = get_workflow_version()
        version2 = get_workflow_version()
        assert version1 == version2
        
        # Check cache info
        info = get_workflow_version.cache_info()
        assert info.hits >= 1
    
    def test_git_version_format(self):
        """Test git version format when in git repo."""
        with patch('subprocess.run') as mock_run:
            # Clear cache to force re-computation
            get_workflow_version.cache_clear()
            
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "abc123def456789012345\n"
            mock_run.return_value = mock_result
            
            version = get_workflow_version()
            
            # Should be git:<12-char-hash>
            assert version.startswith("git:")
            assert len(version) == 16  # "git:" + 12 chars
    
    def test_fallback_to_package_version(self):
        """Test fallback to package version when not in git repo."""
        with patch('subprocess.run') as mock_run:
            get_workflow_version.cache_clear()
            
            # Git fails
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_run.return_value = mock_result
            
            with patch('importlib.metadata.version') as mock_version:
                mock_version.return_value = "1.2.3"
                
                version = get_workflow_version()
                
                assert version == "pkg:1.2.3"
    
    def test_fallback_to_unknown(self):
        """Test fallback to 'unknown' when both git and package fail."""
        with patch('subprocess.run') as mock_run:
            get_workflow_version.cache_clear()
            
            # Git fails
            mock_run.side_effect = Exception("git not found")
            
            with patch('importlib.metadata.version') as mock_version:
                mock_version.side_effect = Exception("package not installed")
                
                version = get_workflow_version()
                
                assert version == "unknown"
    
    def test_git_timeout_handled(self):
        """Test that git timeout is handled gracefully."""
        import subprocess
        with patch('subprocess.run') as mock_run:
            get_workflow_version.cache_clear()
            
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=5)
            
            with patch('importlib.metadata.version') as mock_version:
                mock_version.return_value = "0.1.0"
                
                version = get_workflow_version()
                
                # Should fall back to package version
                assert version == "pkg:0.1.0"


class TestValidateCheckpointVersion:
    """Test validate_checkpoint_version function."""
    
    def setup_method(self):
        """Clear cached version before each test."""
        get_workflow_version.cache_clear()
    
    def teardown_method(self):
        """Clear cache after each test."""
        get_workflow_version.cache_clear()
    
    def test_matching_versions_pass(self):
        """Test that matching versions pass validation."""
        with patch('integration_coworker.graph.runtime.get_workflow_version') as mock:
            mock.return_value = "git:abc123def456"
            
            # Should not raise
            validate_checkpoint_version("git:abc123def456")
    
    def test_mismatched_versions_raise(self):
        """Test that mismatched versions raise error."""
        with patch('integration_coworker.graph.runtime.get_workflow_version') as mock:
            mock.return_value = "git:newversion123"
            
            with pytest.raises(WorkflowVersionMismatchError) as exc_info:
                validate_checkpoint_version("git:oldversion123")
            
            assert "git:oldversion123" in str(exc_info.value)
            assert "git:newversion123" in str(exc_info.value)
            assert exc_info.value.checkpoint_version == "git:oldversion123"
            assert exc_info.value.current_version == "git:newversion123"
    
    def test_force_allows_mismatch(self):
        """Test that force=True allows version mismatch."""
        with patch('integration_coworker.graph.runtime.get_workflow_version') as mock:
            mock.return_value = "git:newversion123"
            
            # Should not raise with force=True
            validate_checkpoint_version("git:oldversion123", force=True)
    
    def test_unknown_checkpoint_version_allowed(self):
        """Test that 'unknown' checkpoint version is always allowed."""
        with patch('integration_coworker.graph.runtime.get_workflow_version') as mock:
            mock.return_value = "git:abc123def456"
            
            # Should not raise
            validate_checkpoint_version("unknown")
    
    def test_unknown_current_version_allowed(self):
        """Test that 'unknown' current version is always allowed."""
        with patch('integration_coworker.graph.runtime.get_workflow_version') as mock:
            mock.return_value = "unknown"
            
            # Should not raise
            validate_checkpoint_version("git:abc123def456")
    
    def test_both_unknown_allowed(self):
        """Test that both 'unknown' versions are allowed."""
        with patch('integration_coworker.graph.runtime.get_workflow_version') as mock:
            mock.return_value = "unknown"
            
            # Should not raise
            validate_checkpoint_version("unknown")


class TestWorkflowVersionMismatchError:
    """Test WorkflowVersionMismatchError exception."""
    
    def test_error_message(self):
        """Test error message contains version info."""
        error = WorkflowVersionMismatchError("v1.0", "v2.0")
        
        assert "v1.0" in str(error)
        assert "v2.0" in str(error)
        assert "--force" in str(error)
    
    def test_error_attributes(self):
        """Test error has version attributes."""
        error = WorkflowVersionMismatchError("checkpoint_v", "current_v")
        
        assert error.checkpoint_version == "checkpoint_v"
        assert error.current_version == "current_v"
    
    def test_error_is_exception(self):
        """Test error is a proper exception."""
        error = WorkflowVersionMismatchError("v1", "v2")
        
        assert isinstance(error, Exception)


class TestGetVersionedThreadConfig:
    """Test get_versioned_thread_config function."""
    
    def setup_method(self):
        """Clear cached version before each test."""
        get_workflow_version.cache_clear()
    
    def teardown_method(self):
        """Clear cache after each test."""
        get_workflow_version.cache_clear()
    
    def test_includes_thread_id(self):
        """Test that config includes thread_id."""
        config = get_versioned_thread_config("run-123")
        
        assert "configurable" in config
        assert "thread_id" in config["configurable"]
        assert config["configurable"]["thread_id"] == "run-123"
    
    def test_includes_workflow_version(self):
        """Test that config includes workflow_version."""
        with patch('integration_coworker.graph.runtime.get_workflow_version') as mock:
            mock.return_value = "git:abc123def456"
            
            config = get_versioned_thread_config("run-123")
            
            assert "workflow_version" in config["configurable"]
            assert config["configurable"]["workflow_version"] == "git:abc123def456"
    
    def test_run_id_required(self):
        """Test that run_id is required."""
        with pytest.raises(ValueError):
            get_versioned_thread_config("")
    
    def test_none_run_id_raises(self):
        """Test that None run_id raises."""
        with pytest.raises((ValueError, TypeError)):
            get_versioned_thread_config(None)


class TestGetThreadConfig:
    """Test get_thread_config function."""
    
    def test_returns_correct_format(self):
        """Test that config has correct format."""
        config = get_thread_config("test-run")
        
        assert config == {"configurable": {"thread_id": "test-run"}}
    
    def test_empty_run_id_raises(self):
        """Test that empty run_id raises."""
        with pytest.raises(ValueError) as exc_info:
            get_thread_config("")
        
        assert "run_id is required" in str(exc_info.value)
    
    def test_different_run_ids(self):
        """Test that different run_ids produce different configs."""
        config1 = get_thread_config("run-1")
        config2 = get_thread_config("run-2")
        
        assert config1 != config2
        assert config1["configurable"]["thread_id"] == "run-1"
        assert config2["configurable"]["thread_id"] == "run-2"


class TestVersionIntegration:
    """Integration tests for versioning workflow."""
    
    def setup_method(self):
        """Clear cached version before each test."""
        get_workflow_version.cache_clear()
    
    def teardown_method(self):
        """Clear cache after each test."""
        get_workflow_version.cache_clear()
    
    def test_version_in_real_environment(self):
        """Test version detection in real environment."""
        # This test runs in the actual environment
        version = get_workflow_version()
        
        # Should be one of the expected formats
        assert version.startswith("git:") or version.startswith("pkg:") or version == "unknown"
    
    def test_versioned_config_in_real_environment(self):
        """Test versioned config in real environment."""
        config = get_versioned_thread_config("integration-test-run")
        
        assert "thread_id" in config["configurable"]
        assert "workflow_version" in config["configurable"]
        
        version = config["configurable"]["workflow_version"]
        assert version.startswith("git:") or version.startswith("pkg:") or version == "unknown"
