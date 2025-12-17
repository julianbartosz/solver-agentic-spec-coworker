"""
Tests for codegen profile configuration.

Per ADR-0005: Production-Grade Codegen Quality Gates
"""
import os
import pytest
from unittest.mock import patch

from integration_coworker.config.profiles import (
    CodegenProfile,
    PROFILES,
    get_active_profile,
    is_production_profile,
    is_development_profile,
    get_profile_summary,
)


class TestCodegenProfile:
    """Tests for CodegenProfile dataclass."""
    
    def test_development_profile_defaults(self):
        """Test development profile has lenient defaults."""
        profile = PROFILES["development"]
        
        assert profile.name == "development"
        assert profile.enable_strict_gates is False
        assert profile.enable_sandbox_execution is False
        assert profile.enable_pattern_learning is False
        assert profile.fallback_to_skeleton is True
    
    def test_production_profile_defaults(self):
        """Test production profile has strict defaults."""
        profile = PROFILES["production"]
        
        assert profile.name == "production"
        assert profile.enable_strict_gates is True
        assert profile.enable_sandbox_execution is True
        assert profile.enable_pattern_learning is True
        assert profile.fallback_to_skeleton is False
    
    def test_profile_frozen(self):
        """Test that profiles are immutable."""
        profile = PROFILES["development"]
        
        with pytest.raises(Exception):  # FrozenInstanceError
            profile.enable_strict_gates = True
    
    def test_str_representation(self):
        """Test string representation."""
        profile = PROFILES["production"]
        assert str(profile) == "CodegenProfile(production)"


class TestGetActiveProfile:
    """Tests for get_active_profile function."""
    
    def test_default_is_development(self):
        """Test default profile is development when env not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove CODEGEN_PROFILE if present
            os.environ.pop("CODEGEN_PROFILE", None)
            profile = get_active_profile()
            assert profile.name == "development"
    
    def test_production_env_var(self):
        """Test production profile from env var."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "production"}):
            profile = get_active_profile()
            assert profile.name == "production"
            assert profile.enable_strict_gates is True
    
    def test_development_env_var(self):
        """Test development profile from env var."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "development"}):
            profile = get_active_profile()
            assert profile.name == "development"
            assert profile.enable_strict_gates is False
    
    def test_case_insensitive(self):
        """Test env var is case insensitive."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "PRODUCTION"}):
            profile = get_active_profile()
            assert profile.name == "production"
    
    def test_whitespace_stripped(self):
        """Test whitespace is stripped from env var."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "  production  "}):
            profile = get_active_profile()
            assert profile.name == "production"
    
    def test_invalid_profile_defaults_to_development(self, caplog):
        """Test invalid profile name defaults to development with warning."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "invalid_profile"}):
            profile = get_active_profile()
            assert profile.name == "development"
            # Check warning was logged
            assert "Unknown CODEGEN_PROFILE" in caplog.text


class TestProfileHelpers:
    """Tests for profile helper functions."""
    
    def test_is_production_profile_true(self):
        """Test is_production_profile returns True for production."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "production"}):
            assert is_production_profile() is True
    
    def test_is_production_profile_false(self):
        """Test is_production_profile returns False for development."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "development"}):
            assert is_production_profile() is False
    
    def test_is_development_profile_true(self):
        """Test is_development_profile returns True for development."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "development"}):
            assert is_development_profile() is True
    
    def test_is_development_profile_false(self):
        """Test is_development_profile returns False for production."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "production"}):
            assert is_development_profile() is False


class TestGetProfileSummary:
    """Tests for get_profile_summary function."""
    
    def test_summary_contains_profile_name(self):
        """Test summary includes profile name."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "production"}):
            summary = get_profile_summary()
            assert "production" in summary.lower()
    
    def test_summary_contains_gate_status(self):
        """Test summary includes gate status."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "production"}):
            summary = get_profile_summary()
            assert "Strict gates: ON" in summary
    
    def test_summary_development(self):
        """Test summary for development profile."""
        with patch.dict(os.environ, {"CODEGEN_PROFILE": "development"}):
            summary = get_profile_summary()
            assert "development" in summary.lower()
            assert "Strict gates: OFF" in summary
            assert "Skeleton fallback: ON" in summary


class TestPatternLearningDefault:
    """Tests for pattern learning default based on profile."""
    
    def test_pattern_learning_disabled_in_development(self):
        """Test pattern learning is disabled in development profile."""
        profile = PROFILES["development"]
        assert profile.enable_pattern_learning is False
    
    def test_pattern_learning_enabled_in_production(self):
        """Test pattern learning is enabled in production profile."""
        profile = PROFILES["production"]
        assert profile.enable_pattern_learning is True
