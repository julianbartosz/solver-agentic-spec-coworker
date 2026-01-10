"""
Tests for checkpoint version policy (Phase 1 - P0-6).

Validates that:
1. Production profile blocks "unknown" checkpoint versions by default
2. ALLOW_UNKNOWN_CHECKPOINT_VERSION=1 allows unknown in production
3. Development profile allows unknown versions
4. Version mismatches raise WorkflowVersionMismatchError
"""
import os
import pytest
from unittest.mock import patch

from integration_coworker.graph.runtime import (
    validate_checkpoint_version,
    WorkflowVersionMismatchError,
    get_workflow_version,
)


class TestCheckpointVersionPolicy:
    """Test checkpoint version validation policy."""

    def test_production_blocks_unknown_checkpoint_version(self):
        """Unknown checkpoint version raises error in production profile."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "production", "ALLOW_UNKNOWN_CHECKPOINT_VERSION": "0"}):
            # Clear any cached profile
            with patch("integration_coworker.config.profiles.get_active_profile") as mock_profile:
                mock_profile.return_value.name = "production"
                
                with pytest.raises(WorkflowVersionMismatchError):
                    validate_checkpoint_version("unknown", force=False)

    def test_production_blocks_unknown_current_version(self):
        """Unknown current version raises error in production profile."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "production", "ALLOW_UNKNOWN_CHECKPOINT_VERSION": "0"}):
            with patch("integration_coworker.graph.runtime.get_workflow_version", return_value="unknown"):
                with patch("integration_coworker.config.profiles.is_production_profile", return_value=True):
                    with pytest.raises(WorkflowVersionMismatchError):
                        validate_checkpoint_version("1.0.0", force=False)

    def test_production_allows_unknown_with_env_override(self):
        """ALLOW_UNKNOWN_CHECKPOINT_VERSION=1 allows unknown in production."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "production", "ALLOW_UNKNOWN_CHECKPOINT_VERSION": "1"}):
            with patch("integration_coworker.config.profiles.is_production_profile", return_value=True):
                # Should not raise
                validate_checkpoint_version("unknown", force=False)

    def test_production_allows_unknown_with_env_true(self):
        """ALLOW_UNKNOWN_CHECKPOINT_VERSION=true also works."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "production", "ALLOW_UNKNOWN_CHECKPOINT_VERSION": "true"}):
            with patch("integration_coworker.config.profiles.is_production_profile", return_value=True):
                # Should not raise
                validate_checkpoint_version("unknown", force=False)

    def test_development_allows_unknown_checkpoint_version(self):
        """Unknown checkpoint version allowed in development profile."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "development"}, clear=False):
            # Remove the allow flag to ensure dev behavior isn't from override
            env = os.environ.copy()
            env.pop("ALLOW_UNKNOWN_CHECKPOINT_VERSION", None)
            
            with patch.dict(os.environ, env, clear=True):
                with patch("integration_coworker.config.profiles.is_production_profile", return_value=False):
                    # Should not raise
                    validate_checkpoint_version("unknown", force=False)

    def test_version_mismatch_raises_error(self):
        """Mismatched versions raise WorkflowVersionMismatchError."""
        with patch("integration_coworker.graph.runtime.get_workflow_version", return_value="2.0.0"):
            with patch("integration_coworker.config.profiles.is_production_profile", return_value=False):
                with pytest.raises(WorkflowVersionMismatchError) as exc_info:
                    validate_checkpoint_version("1.0.0", force=False)
                
                assert exc_info.value.checkpoint_version == "1.0.0"
                assert exc_info.value.current_version == "2.0.0"

    def test_version_mismatch_allowed_with_force(self):
        """force=True allows version mismatch with warning."""
        with patch("integration_coworker.graph.runtime.get_workflow_version", return_value="2.0.0"):
            with patch("integration_coworker.config.profiles.is_production_profile", return_value=False):
                # Should not raise
                validate_checkpoint_version("1.0.0", force=True)

    def test_matching_versions_pass(self):
        """Matching versions pass validation."""
        with patch("integration_coworker.graph.runtime.get_workflow_version", return_value="1.0.0"):
            with patch("integration_coworker.config.profiles.is_production_profile", return_value=True):
                # Should not raise
                validate_checkpoint_version("1.0.0", force=False)


class TestWorkflowVersionMismatchError:
    """Test the error class itself."""

    def test_error_stores_versions(self):
        """Error stores checkpoint and current versions."""
        err = WorkflowVersionMismatchError("1.0.0", "2.0.0")
        assert err.checkpoint_version == "1.0.0"
        assert err.current_version == "2.0.0"

    def test_error_message_includes_versions(self):
        """Error message includes both versions."""
        err = WorkflowVersionMismatchError("1.0.0", "2.0.0")
        msg = str(err)
        assert "1.0.0" in msg
        assert "2.0.0" in msg


class TestGetWorkflowVersion:
    """Test workflow version detection."""

    def test_returns_string(self):
        """get_workflow_version returns a string."""
        version = get_workflow_version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_version_is_consistent(self):
        """get_workflow_version returns consistent results."""
        v1 = get_workflow_version()
        v2 = get_workflow_version()
        assert v1 == v2
