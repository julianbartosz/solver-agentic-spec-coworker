"""
Startup Configuration Validation (Production Hardening S-2)

Validates environment configuration at process startup and emits
a structured config summary for debugging and audit purposes.

This module should be called early in the application lifecycle to:
1. Fail fast if critical configuration is missing or invalid
2. Emit a structured summary of all effective configuration
3. Warn about potentially dangerous or suboptimal settings

Usage:
    from integration_coworker.config.startup import validate_startup_config
    
    # Validate and emit summary (raises on critical errors)
    config_summary = validate_startup_config()
    
    # Or validate without raising (returns errors in result)
    result = validate_startup_config(fail_fast=False)
    if result.errors:
        handle_errors(result.errors)

Configuration Categories:
- Critical: Missing required config causes immediate failure
- Warning: Suboptimal config is logged but doesn't fail
- Info: Configuration summary for debugging
"""

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ConfigSeverity(Enum):
    """Severity level for configuration issues."""
    CRITICAL = "critical"  # Missing required config, fail startup
    WARNING = "warning"    # Suboptimal but not fatal
    INFO = "info"          # Informational only


@dataclass
class ConfigIssue:
    """A configuration issue detected during validation."""
    severity: ConfigSeverity
    key: str
    message: str
    current_value: Optional[str] = None
    recommended_value: Optional[str] = None
    
    def __str__(self) -> str:
        """Human-readable string representation."""
        parts = [f"[{self.key}] {self.message}"]
        if self.recommended_value:
            parts.append(f"(recommended: {self.recommended_value})")
        return " ".join(parts)


@dataclass
class ConfigValidationResult:
    """Result of startup configuration validation."""
    errors: List[ConfigIssue] = field(default_factory=list)
    warnings: List[ConfigIssue] = field(default_factory=list)
    config_summary: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_valid(self) -> bool:
        """True if no critical errors."""
        return len(self.errors) == 0
    
    def add_error(self, key: str, message: str, current: Optional[str] = None) -> None:
        """Add a critical error."""
        self.errors.append(ConfigIssue(
            severity=ConfigSeverity.CRITICAL,
            key=key,
            message=message,
            current_value=current,
        ))
    
    def add_warning(
        self, key: str, message: str, 
        current: Optional[str] = None, 
        recommended: Optional[str] = None
    ) -> None:
        """Add a warning."""
        self.warnings.append(ConfigIssue(
            severity=ConfigSeverity.WARNING,
            key=key,
            message=message,
            current_value=current,
            recommended_value=recommended,
        ))


class StartupValidator:
    """
    Validates startup configuration and emits summary.
    
    Validation is performed in stages:
    1. Required environment variables
    2. Database configuration
    3. LLM provider configuration  
    4. Health server configuration
    5. Security settings
    6. Knowledge Graph seeding status (B-006)
    """
    
    def __init__(self):
        self.result = ConfigValidationResult()
    
    def validate(self) -> ConfigValidationResult:
        """Run all validation checks and return result."""
        self._validate_required_env()
        self._validate_database_config()
        self._validate_llm_config()
        self._validate_health_config()
        self._validate_security_config()
        self._validate_kg_seeding()  # B-006
        self._build_config_summary()
        return self.result
    
    def _validate_required_env(self) -> None:
        """Validate required environment variables."""
        # VALIDATION_PROFILE is critical for determining test mode
        profile = os.getenv("VALIDATION_PROFILE")
        if profile not in ("offline", "record", "live", None):
            self.result.add_error(
                "VALIDATION_PROFILE",
                f"Invalid profile: {profile}. Must be 'offline', 'record', or 'live'",
                current=profile,
            )
        
        # Log level validation
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            self.result.add_warning(
                "LOG_LEVEL",
                f"Invalid log level: {log_level}. Defaulting to INFO.",
                current=log_level,
                recommended="INFO",
            )
    
    def _validate_database_config(self) -> None:
        """Validate database configuration."""
        use_sqlite = os.getenv("USE_SQLITE", "true").lower() == "true"
        database_url = os.getenv("DATABASE_URL")
        
        if not use_sqlite and not database_url:
            self.result.add_error(
                "DATABASE_URL",
                "DATABASE_URL is required when USE_SQLITE=false",
            )
        
        if not use_sqlite and database_url:
            # Validate Postgres connection string format
            if not database_url.startswith(("postgresql://", "postgres://")):
                self.result.add_warning(
                    "DATABASE_URL",
                    "DATABASE_URL should start with 'postgresql://' or 'postgres://'",
                    current=_redact_password(database_url),
                )
            
            # Check for connection timeout
            if "connect_timeout" not in database_url:
                self.result.add_warning(
                    "DATABASE_URL",
                    "Consider adding connect_timeout parameter to prevent hanging on startup",
                    recommended="?connect_timeout=10",
                )
    
    def _validate_llm_config(self) -> None:
        """Validate LLM provider configuration."""
        use_mock = os.getenv("USE_MOCK_LLM", "true").lower() == "true"
        
        if not use_mock:
            # Real LLM requires API key
            openai_key = os.getenv("OPENAI_API_KEY")
            anthropic_key = os.getenv("ANTHROPIC_API_KEY")
            
            if not openai_key and not anthropic_key:
                self.result.add_error(
                    "OPENAI_API_KEY/ANTHROPIC_API_KEY",
                    "At least one LLM API key is required when USE_MOCK_LLM=false",
                )
            
            # Check for rate limit configuration
            if not os.getenv("LLM_RATE_LIMIT_REQUESTS"):
                self.result.add_warning(
                    "LLM_RATE_LIMIT_REQUESTS",
                    "Consider setting rate limit to prevent API quota exhaustion",
                    recommended="100",
                )
        
        # Validate circuit breaker config
        failure_threshold = os.getenv("LLM_CIRCUIT_FAILURE_THRESHOLD", "5")
        try:
            if int(failure_threshold) < 1:
                self.result.add_warning(
                    "LLM_CIRCUIT_FAILURE_THRESHOLD",
                    "Circuit breaker threshold should be at least 1",
                    current=failure_threshold,
                    recommended="5",
                )
        except ValueError:
            self.result.add_error(
                "LLM_CIRCUIT_FAILURE_THRESHOLD",
                f"Invalid integer value: {failure_threshold}",
                current=failure_threshold,
            )
    
    def _validate_health_config(self) -> None:
        """Validate health server configuration."""
        host = os.getenv("HEALTH_SERVER_HOST", "127.0.0.1")
        
        if host == "0.0.0.0":
            self.result.add_warning(
                "HEALTH_SERVER_HOST",
                "Health server bound to 0.0.0.0 - ensure this is behind a firewall/proxy",
                current=host,
                recommended="127.0.0.1",
            )
        
        # Validate port
        port = os.getenv("HEALTH_SERVER_PORT", "8080")
        try:
            port_int = int(port)
            if port_int < 1 or port_int > 65535:
                self.result.add_error(
                    "HEALTH_SERVER_PORT",
                    f"Port must be between 1 and 65535",
                    current=port,
                )
        except ValueError:
            self.result.add_error(
                "HEALTH_SERVER_PORT",
                f"Invalid port number: {port}",
                current=port,
            )
        
        # Validate timeout
        timeout = os.getenv("HEALTH_SERVER_TIMEOUT", "5.0")
        try:
            timeout_float = float(timeout)
            if timeout_float < 0.1:
                self.result.add_warning(
                    "HEALTH_SERVER_TIMEOUT",
                    "Health server timeout is very short, may cause false negatives",
                    current=timeout,
                    recommended="5.0",
                )
        except ValueError:
            self.result.add_error(
                "HEALTH_SERVER_TIMEOUT",
                f"Invalid timeout value: {timeout}",
                current=timeout,
            )
    
    def _validate_security_config(self) -> None:
        """Validate security-related configuration."""
        # Check for debug mode in production
        debug = os.getenv("DEBUG", "false").lower() == "true"
        if debug:
            self.result.add_warning(
                "DEBUG",
                "Debug mode is enabled - disable in production",
                current="true",
                recommended="false",
            )
        
        # Check for test mode detection
        is_test = _is_test_environment()
        if is_test:
            self.result.add_warning(
                "_TEST_ENVIRONMENT_DETECTED",
                "Running in test environment - some features may be disabled",
            )
    
    def _validate_kg_seeding(self) -> None:
        """
        Validate Knowledge Graph seeding status (B-006).
        
        Checks if the KG has workflow templates. If empty, logs a WARNING
        and suggests running the seed command. This allows production to
        "just work" while being observable.
        """
        try:
            from integration_coworker.persistence.seed_kg import get_template_count
            
            template_count = get_template_count()
            
            if template_count == 0:
                self.result.add_warning(
                    "KG_SEEDING",
                    "Knowledge Graph has no workflow templates. Run 'bd seed' or "
                    "seed programmatically for optimal template matching.",
                    current="0 templates",
                    recommended="Run: bd seed",
                )
            else:
                logger.debug(f"Knowledge Graph has {template_count} templates")
        except ImportError:
            # seed_kg module not available (minimal install)
            logger.debug("KG seeding validation skipped (seed_kg not available)")
        except Exception as e:
            # Database not initialized yet - this is normal for first run
            logger.debug(f"KG seeding validation skipped: {e}")
    
    def _build_config_summary(self) -> None:
        """Build structured configuration summary.
        
        Uses safe parsing with defaults for any invalid values.
        """
        self.result.config_summary = {
            "environment": {
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "validation_profile": os.getenv("VALIDATION_PROFILE", "offline"),
                "log_level": os.getenv("LOG_LEVEL", "INFO"),
                "is_test_environment": _is_test_environment(),
            },
            "database": {
                "use_sqlite": os.getenv("USE_SQLITE", "true").lower() == "true",
                "database_url_set": bool(os.getenv("DATABASE_URL")),
            },
            "llm": {
                "use_mock": os.getenv("USE_MOCK_LLM", "true").lower() == "true",
                "openai_key_set": bool(os.getenv("OPENAI_API_KEY")),
                "anthropic_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),
                "circuit_breaker_threshold": _safe_int(os.getenv("LLM_CIRCUIT_FAILURE_THRESHOLD"), 5),
                "circuit_breaker_recovery": _safe_float(os.getenv("LLM_CIRCUIT_RECOVERY_TIMEOUT"), 60.0),
            },
            "health_server": {
                "host": os.getenv("HEALTH_SERVER_HOST", "127.0.0.1"),
                "port": _safe_int(os.getenv("HEALTH_SERVER_PORT"), 8080),
                "timeout": _safe_float(os.getenv("HEALTH_SERVER_TIMEOUT"), 5.0),
                "max_workers": _safe_int(os.getenv("HEALTH_SERVER_MAX_WORKERS"), 4),
            },
            "cache": {
                "enabled": os.getenv("LLM_CACHE_ENABLED", "true").lower() == "true",
                "redis_url_set": bool(os.getenv("REDIS_URL")),
                "ttl": _safe_int(os.getenv("LLM_CACHE_TTL"), 86400),
            },
        }


def _safe_int(value: Optional[str], default: int) -> int:
    """Safely parse an integer with a default fallback."""
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _safe_float(value: Optional[str], default: float) -> float:
    """Safely parse a float with a default fallback."""
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _redact_password(url: str) -> str:
    """Redact password from database URL for logging."""
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        if parsed.password:
            # Replace password with ****
            redacted = parsed._replace(
                netloc=f"{parsed.username}:****@{parsed.hostname}"
                + (f":{parsed.port}" if parsed.port else "")
            )
            return urlunparse(redacted)
        return url
    except Exception:
        return "****"


def _is_test_environment() -> bool:
    """Detect if running in a test environment."""
    # Check for pytest
    if "pytest" in sys.modules:
        return True
    
    # Check for test-specific environment variables
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    
    # Check for unittest
    if "unittest" in sys.modules:
        return True
    
    return False


def validate_startup_config(
    fail_fast: bool = True,
    emit_summary: bool = True,
) -> ConfigValidationResult:
    """
    Validate startup configuration and optionally fail fast.
    
    This should be called early in application startup to ensure
    configuration is valid before any work is done.
    
    Args:
        fail_fast: If True, raise ConfigurationError on critical issues
        emit_summary: If True, log the configuration summary
        
    Returns:
        ConfigValidationResult with any errors, warnings, and summary
        
    Raises:
        ConfigurationError: If fail_fast=True and critical issues found
    """
    validator = StartupValidator()
    result = validator.validate()
    
    # Emit summary
    if emit_summary:
        logger.info("Configuration summary:")
        logger.info(json.dumps(result.config_summary, indent=2, default=str))
    
    # Log warnings
    for warning in result.warnings:
        logger.warning(
            f"Config warning [{warning.key}]: {warning.message}"
            + (f" (current: {warning.current_value})" if warning.current_value else "")
            + (f" (recommended: {warning.recommended_value})" if warning.recommended_value else "")
        )
    
    # Handle errors
    if result.errors:
        for error in result.errors:
            logger.error(
                f"Config error [{error.key}]: {error.message}"
                + (f" (current: {error.current_value})" if error.current_value else "")
            )
        
        if fail_fast:
            raise ConfigurationError(
                f"Startup configuration validation failed with {len(result.errors)} error(s). "
                "Check logs for details."
            )
    
    return result


class ConfigurationError(Exception):
    """Raised when startup configuration is invalid."""
    pass


def get_config_summary() -> Dict[str, Any]:
    """
    Get current configuration summary without validation.
    
    Useful for debugging and health endpoints.
    """
    validator = StartupValidator()
    validator._build_config_summary()
    return validator.result.config_summary
