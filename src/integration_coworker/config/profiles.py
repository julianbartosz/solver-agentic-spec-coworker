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
    
    # V5: Environment overrides for subprocess harness
    # IC_SANDBOX_GATES=ruff,mypy,pytest - controls which gates run
    # IC_ENABLE_LIVE_TESTS=1 - enables live tests (requires allowlist)
    # IC_LIVE_HOST_ALLOWLIST=api.stripe.com,... - required if live enabled
"""
import logging
import os
from dataclasses import dataclass, replace
from typing import Literal, Optional, Set

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
        enable_contract_tests: If True, run Schemathesis+Prism contract tests against spec
        enable_live_tests: If True, run live integration tests with real API calls
        live_host_allowlist: Tuple of allowed hosts for live tests (extracted from spec or explicit)
        live_env_passthrough: Tuple of env var NAMES to pass through to sandbox for live tests.
            Values are resolved at runtime from os.environ, keeping secrets out of config objects.
            Example: ("OPENAI_API_KEY", "STRIPE_SECRET_KEY")
        
        V5 additions (individual gate control):
        enable_ruff: If True, run ruff linting gate
        enable_mypy: If True, run mypy type checking gate
        enable_bandit: If True, run bandit security gate
        enable_pytest: If True, run pytest testing gate
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
    # Contract testing (Schemathesis + Prism mock server)
    enable_contract_tests: bool = False
    # Live integration tests (real API calls)
    enable_live_tests: bool = False
    live_host_allowlist: tuple = ()  # Tuple of allowed hosts (extracted from spec or explicit)
    live_env_passthrough: tuple = ()  # Tuple of env var NAMES to resolve at runtime
    
    # V5: Individual gate controls (default: all enabled in production)
    # These are overridden by IC_SANDBOX_GATES env var
    enable_ruff: bool = True
    enable_mypy: bool = True
    enable_bandit: bool = True
    enable_pytest: bool = True
    
    def __str__(self) -> str:
        return f"CodegenProfile({self.name})"


# Profile definitions
PROFILES: dict[str, CodegenProfile] = {
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
        enable_contract_tests=False,     # No contract tests by default
        enable_live_tests=False,         # No live tests by default
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
        coverage_fail_under=20,          # 20% minimum (client boilerplate: retry, rate limit, etc.)
        enable_self_review=True,         # LLM review + repair pass
        fail_on_no_tests=True,           # NO_TESTS_COLLECTED is failure
        # Contract tests DISABLED by default - requires Prism CLI (Node.js tool)
        # Enable via CODEGEN_PROFILE=production_contract once Prism is vendored
        enable_contract_tests=False,
        # Live tests DISABLED by default - requires explicit opt-in:
        # ALLOW_LIVE=1 + LIVE_HOST_ALLOWLIST + LIVE_ENV_PASSTHROUGH
        enable_live_tests=False,
    ),
}


def _apply_env_overrides(profile: CodegenProfile) -> CodegenProfile:
    """
    Apply environment variable overrides to a profile.
    
    V5: Harness subprocess can control sandbox gates via env vars.
    This keeps the "profile controls behavior" architecture while allowing
    runtime customization.
    
    Environment variables:
        IC_SANDBOX_GATES: Comma-separated list of gates to enable.
            Valid gates: ruff, mypy, bandit, pytest, coverage
            Example: IC_SANDBOX_GATES=ruff,mypy,pytest
            
        IC_ENABLE_LIVE_TESTS: Set to "1" to enable live tests.
            Requires IC_LIVE_HOST_ALLOWLIST to be set.
            
        IC_LIVE_HOST_ALLOWLIST: Comma-separated list of allowed hosts.
            Required when IC_ENABLE_LIVE_TESTS=1.
            Example: IC_LIVE_HOST_ALLOWLIST=api.stripe.com,api.twilio.com
    
    Returns:
        Modified profile with env overrides applied.
    """
    overrides = {}
    
    # IC_SANDBOX_GATES: Control which gates run
    sandbox_gates_str = os.getenv("IC_SANDBOX_GATES", "").strip()
    if sandbox_gates_str:
        gates = {g.strip().lower() for g in sandbox_gates_str.split(",") if g.strip()}
        logger.info(f"[profile] IC_SANDBOX_GATES override: {gates}")
        
        # Enable sandbox execution if any gates specified
        overrides["enable_sandbox_execution"] = True
        
        # Set individual gate flags based on allowlist
        overrides["enable_ruff"] = "ruff" in gates
        overrides["enable_mypy"] = "mypy" in gates
        overrides["enable_bandit"] = "bandit" in gates
        overrides["enable_pytest"] = "pytest" in gates
        
        # Coverage is a gate too
        if "coverage" in gates:
            overrides["enable_coverage"] = True
            overrides["enable_pytest"] = True  # Coverage requires pytest
    
    # IC_ENABLE_LIVE_TESTS: Enable live tests
    live_tests_str = os.getenv("IC_ENABLE_LIVE_TESTS", "").strip()
    if live_tests_str == "1":
        # Require allowlist
        allowlist_str = os.getenv("IC_LIVE_HOST_ALLOWLIST", "").strip()
        if not allowlist_str:
            raise ValueError(
                "IC_ENABLE_LIVE_TESTS=1 requires IC_LIVE_HOST_ALLOWLIST to be set. "
                "Example: IC_LIVE_HOST_ALLOWLIST=api.stripe.com,api.twilio.com"
            )
        hosts = tuple(h.strip() for h in allowlist_str.split(",") if h.strip())
        logger.info(f"[profile] Live tests enabled with allowlist: {hosts}")
        overrides["enable_live_tests"] = True
        overrides["live_host_allowlist"] = hosts
    
    # Apply overrides if any
    if overrides:
        return replace(profile, **overrides)
    return profile


def get_active_profile() -> CodegenProfile:
    """
    Get the active codegen profile.
    
    Reads from CODEGEN_PROFILE environment variable.
    Defaults to 'development' if not set or invalid.
    
    V5: Applies environment variable overrides after base profile selection.
    See _apply_env_overrides() for override contract.
    
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
    
    # V5: Apply environment overrides
    profile = _apply_env_overrides(profile)
    
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
        f"  Contract tests: {'ON' if profile.enable_contract_tests else 'OFF'}",
        f"  Live tests: {'ON' if profile.enable_live_tests else 'OFF'}",
    ]
    
    return "\n".join(lines)
