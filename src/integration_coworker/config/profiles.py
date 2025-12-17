"""
Codegen Profiles Configuration

Defines configuration profiles for different environments:
- development: Lenient validation, skeleton fallback on failure
- production: Strict gates, fail hard on validation errors

Per ADR-0005: Production-Grade Codegen Quality Gates

Usage:
    # Check active profile
    from integration_coworker.config.profiles import get_active_profile, is_production_profile
    
    profile = get_active_profile()
    if profile.enable_strict_gates:
        # Run ruff/mypy as hard gates
        ...
    
    # Or use environment variable
    CODEGEN_PROFILE=production python run_workflow.py
"""
import os
import logging
from dataclasses import dataclass
from typing import Literal, Dict

logger = logging.getLogger(__name__)

ProfileName = Literal["development", "production"]


@dataclass(frozen=True)
class CodegenProfile:
    """
    Configuration profile for codegen behavior.
    
    Attributes:
        name: Profile identifier
        enable_strict_gates: If True, ruff/mypy failures fail the workflow
        enable_sandbox_execution: If True, run pytest in isolated sandbox
        enable_pattern_learning: If True, capture events to DB for pattern discovery
        fallback_to_skeleton: If True, use skeleton code when validation fails
        ruff_strict: If True, use stricter ruff rules
        mypy_strict: If True, use --strict mode for mypy
        enable_coverage: If True, run pytest-cov and enforce coverage threshold
        coverage_fail_under: Minimum coverage percentage required (when enable_coverage=True)
        enable_self_review: If True, run LLM self-review pass on generated code
        fail_on_no_tests: If True, pytest exit code 5 (NO_TESTS_COLLECTED) is a failure
    """
    name: ProfileName
    enable_strict_gates: bool
    enable_sandbox_execution: bool
    enable_pattern_learning: bool
    fallback_to_skeleton: bool
    ruff_strict: bool = False
    mypy_strict: bool = False
    enable_coverage: bool = False
    coverage_fail_under: int = 60
    enable_self_review: bool = False
    fail_on_no_tests: bool = False
    
    def __str__(self) -> str:
        return f"CodegenProfile({self.name})"


# Profile definitions
PROFILES: Dict[str, CodegenProfile] = {
    "development": CodegenProfile(
        name="development",
        enable_strict_gates=False,      # Gates warn but don't fail
        enable_sandbox_execution=False,  # Skip sandbox for speed
        enable_pattern_learning=False,   # Don't capture events
        fallback_to_skeleton=True,       # Use skeleton on validation failure
        ruff_strict=False,
        mypy_strict=False,
        enable_coverage=False,           # No coverage requirement
        coverage_fail_under=60,          # N/A when disabled
        enable_self_review=False,        # No LLM review pass
        fail_on_no_tests=False,          # NO_TESTS_COLLECTED is warning
    ),
    "production": CodegenProfile(
        name="production",
        enable_strict_gates=True,        # Gates fail the workflow
        enable_sandbox_execution=True,   # Run pytest in sandbox
        enable_pattern_learning=True,    # Capture events for learning
        fallback_to_skeleton=False,      # Fail hard, don't silently degrade
        ruff_strict=True,                # Stricter lint rules
        mypy_strict=False,               # Not --strict, but type errors fail
        enable_coverage=True,            # Require coverage threshold
        coverage_fail_under=30,          # 30% minimum (LLM code varies, client coverage low)
        enable_self_review=True,         # LLM review + repair pass
        fail_on_no_tests=True,           # NO_TESTS_COLLECTED is failure
    ),
}


def get_active_profile() -> CodegenProfile:
    """
    Get the active codegen profile.
    
    Reads from CODEGEN_PROFILE environment variable.
    Defaults to 'development' if not set or invalid.
    
    Returns:
        CodegenProfile for the active environment
        
    Example:
        >>> import os
        >>> os.environ["CODEGEN_PROFILE"] = "production"
        >>> profile = get_active_profile()
        >>> profile.enable_strict_gates
        True
    """
    profile_name = os.getenv("CODEGEN_PROFILE", "development").lower().strip()
    
    if profile_name not in PROFILES:
        logger.warning(
            f"Unknown CODEGEN_PROFILE '{profile_name}', using 'development'. "
            f"Valid profiles: {list(PROFILES.keys())}"
        )
        profile_name = "development"
    
    profile = PROFILES[profile_name]
    logger.debug(f"Active codegen profile: {profile}")
    return profile


def is_production_profile() -> bool:
    """
    Check if running with production profile.
    
    Convenience function for conditional logic.
    
    Returns:
        True if CODEGEN_PROFILE=production
    """
    return get_active_profile().name == "production"


def is_development_profile() -> bool:
    """
    Check if running with development profile.
    
    Returns:
        True if CODEGEN_PROFILE=development or unset
    """
    return get_active_profile().name == "development"


def get_profile_summary() -> str:
    """
    Get human-readable summary of active profile.
    
    Useful for logging at workflow start.
    
    Returns:
        Multi-line string describing active profile settings
    """
    profile = get_active_profile()
    
    lines = [
        f"Codegen Profile: {profile.name}",
        f"  Strict gates: {'ON' if profile.enable_strict_gates else 'OFF'}",
        f"  Sandbox execution: {'ON' if profile.enable_sandbox_execution else 'OFF'}",
        f"  Pattern learning: {'ON' if profile.enable_pattern_learning else 'OFF'}",
        f"  Skeleton fallback: {'ON' if profile.fallback_to_skeleton else 'OFF'}",
        f"  Coverage gate: {'ON (>={profile.coverage_fail_under}%)' if profile.enable_coverage else 'OFF'}",
        f"  Self-review: {'ON' if profile.enable_self_review else 'OFF'}",
        f"  Fail on no tests: {'ON' if profile.fail_on_no_tests else 'OFF'}",
    ]
    
    return "\n".join(lines)
