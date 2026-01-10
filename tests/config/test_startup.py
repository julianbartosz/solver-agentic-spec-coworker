"""
Tests for startup configuration validation (Production Hardening S-2)
"""

import json
import pytest
from unittest.mock import patch

from integration_coworker.config.startup import (
    validate_startup_config,
    get_config_summary,
    ConfigurationError,
    StartupValidator,
    ConfigSeverity,
)


class TestStartupValidation:
    """Tests for startup configuration validation."""

    def test_valid_config_passes(self):
        """Test that valid configuration passes validation."""
        with patch.dict("os.environ", {
            "VALIDATION_PROFILE": "offline",
            "USE_MOCK_LLM": "true",
            "USE_SQLITE": "true",
        }, clear=False):
            result = validate_startup_config(fail_fast=False, emit_summary=False)
            assert result.is_valid

    def test_invalid_validation_profile_fails(self):
        """Test that invalid VALIDATION_PROFILE causes error."""
        with patch.dict("os.environ", {
            "VALIDATION_PROFILE": "invalid",
        }, clear=False):
            result = validate_startup_config(fail_fast=False, emit_summary=False)
            assert not result.is_valid
            assert any(e.key == "VALIDATION_PROFILE" for e in result.errors)

    def test_missing_database_url_with_postgres(self):
        """Test that missing DATABASE_URL fails when not using SQLite."""
        with patch.dict("os.environ", {
            "USE_SQLITE": "false",
        }, clear=False):
            # Remove DATABASE_URL if it exists
            env = {"USE_SQLITE": "false"}
            if "DATABASE_URL" in os.environ:
                del os.environ["DATABASE_URL"]
            
            result = validate_startup_config(fail_fast=False, emit_summary=False)
            assert any(e.key == "DATABASE_URL" for e in result.errors)

    def test_missing_llm_key_with_real_llm(self):
        """Test that missing API key fails when not using mock LLM."""
        env = {
            "USE_MOCK_LLM": "false",
        }
        with patch.dict("os.environ", env, clear=True):
            result = validate_startup_config(fail_fast=False, emit_summary=False)
            assert any("API_KEY" in e.key for e in result.errors)

    def test_health_server_external_binding_warns(self):
        """Test that binding to 0.0.0.0 produces warning."""
        with patch.dict("os.environ", {
            "HEALTH_SERVER_HOST": "0.0.0.0",
        }, clear=False):
            result = validate_startup_config(fail_fast=False, emit_summary=False)
            assert any(
                w.key == "HEALTH_SERVER_HOST" and "0.0.0.0" in w.message
                for w in result.warnings
            )

    def test_invalid_port_fails(self):
        """Test that invalid port produces error."""
        with patch.dict("os.environ", {
            "HEALTH_SERVER_PORT": "not-a-number",
        }, clear=False):
            result = validate_startup_config(fail_fast=False, emit_summary=False)
            assert any(e.key == "HEALTH_SERVER_PORT" for e in result.errors)

    def test_invalid_timeout_fails(self):
        """Test that invalid timeout produces error."""
        with patch.dict("os.environ", {
            "HEALTH_SERVER_TIMEOUT": "invalid",
        }, clear=False):
            result = validate_startup_config(fail_fast=False, emit_summary=False)
            assert any(e.key == "HEALTH_SERVER_TIMEOUT" for e in result.errors)

    def test_debug_mode_warns(self):
        """Test that debug mode produces warning."""
        with patch.dict("os.environ", {
            "DEBUG": "true",
        }, clear=False):
            result = validate_startup_config(fail_fast=False, emit_summary=False)
            assert any(w.key == "DEBUG" for w in result.warnings)

    def test_fail_fast_raises(self):
        """Test that fail_fast=True raises on errors."""
        with patch.dict("os.environ", {
            "VALIDATION_PROFILE": "invalid",
        }, clear=False):
            with pytest.raises(ConfigurationError):
                validate_startup_config(fail_fast=True, emit_summary=False)


class TestConfigSummary:
    """Tests for configuration summary."""

    def test_summary_includes_all_sections(self):
        """Test that summary includes all configuration sections."""
        with patch.dict("os.environ", {
            "USE_MOCK_LLM": "true",
            "USE_SQLITE": "true",
        }, clear=False):
            summary = get_config_summary()
            
            assert "environment" in summary
            assert "database" in summary
            assert "llm" in summary
            assert "health_server" in summary
            assert "cache" in summary

    def test_summary_python_version(self):
        """Test that summary includes Python version."""
        summary = get_config_summary()
        assert "python_version" in summary["environment"]
        # Should be a valid version string
        assert "." in summary["environment"]["python_version"]

    def test_summary_database_config(self):
        """Test that summary includes database configuration."""
        with patch.dict("os.environ", {
            "USE_SQLITE": "true",
        }, clear=False):
            summary = get_config_summary()
            assert summary["database"]["use_sqlite"] is True

    def test_summary_llm_config(self):
        """Test that summary includes LLM configuration."""
        with patch.dict("os.environ", {
            "USE_MOCK_LLM": "true",
            "LLM_CIRCUIT_FAILURE_THRESHOLD": "10",
        }, clear=False):
            summary = get_config_summary()
            assert summary["llm"]["use_mock"] is True
            assert summary["llm"]["circuit_breaker_threshold"] == 10

    def test_summary_does_not_expose_secrets(self):
        """Test that summary doesn't expose API keys or passwords."""
        with patch.dict("os.environ", {
            "OPENAI_API_KEY": "sk-secret-key-123",
            "DATABASE_URL": "postgresql://user:password@host/db",
        }, clear=False):
            summary = get_config_summary()
            
            # Convert to string to search for secrets
            summary_str = json.dumps(summary)
            
            assert "sk-secret-key-123" not in summary_str
            assert "password" not in summary_str
            # Should only show whether key is set, not the actual value
            assert summary["llm"]["openai_key_set"] is True

    def test_summary_health_server_defaults(self):
        """Test that summary shows health server defaults."""
        with patch.dict("os.environ", {}, clear=True):
            # Force a fresh import to get defaults
            summary = get_config_summary()
            
            assert summary["health_server"]["host"] == "127.0.0.1"
            assert summary["health_server"]["port"] == 8080
            assert summary["health_server"]["timeout"] == 5.0


class TestCircuitBreakerValidation:
    """Tests for circuit breaker configuration validation."""

    def test_valid_circuit_breaker_config(self):
        """Test that valid circuit breaker config passes."""
        with patch.dict("os.environ", {
            "LLM_CIRCUIT_FAILURE_THRESHOLD": "5",
            "LLM_CIRCUIT_RECOVERY_TIMEOUT": "60",
        }, clear=False):
            result = validate_startup_config(fail_fast=False, emit_summary=False)
            # Should not have circuit breaker errors
            assert not any(
                "CIRCUIT" in e.key for e in result.errors
            )

    def test_invalid_circuit_threshold_fails(self):
        """Test that invalid circuit threshold fails."""
        with patch.dict("os.environ", {
            "LLM_CIRCUIT_FAILURE_THRESHOLD": "not-a-number",
        }, clear=False):
            result = validate_startup_config(fail_fast=False, emit_summary=False)
            assert any(
                e.key == "LLM_CIRCUIT_FAILURE_THRESHOLD" for e in result.errors
            )

    def test_zero_circuit_threshold_warns(self):
        """Test that zero circuit threshold produces warning."""
        with patch.dict("os.environ", {
            "LLM_CIRCUIT_FAILURE_THRESHOLD": "0",
        }, clear=False):
            result = validate_startup_config(fail_fast=False, emit_summary=False)
            assert any(
                w.key == "LLM_CIRCUIT_FAILURE_THRESHOLD" for w in result.warnings
            )


class TestTestEnvironmentDetection:
    """Tests for test environment detection."""

    def test_detects_pytest(self):
        """Test that pytest is detected as test environment."""
        # We're running in pytest, so this should be detected
        summary = get_config_summary()
        assert summary["environment"]["is_test_environment"] is True


# Need to import os for the tests
import os
