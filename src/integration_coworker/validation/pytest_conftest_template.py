"""Golden pytest conftest enforcing validation profiles for sandbox runs.

This mirrors the repo-level enforcement so sandboxed pytest executions
honor offline/record/live policies.

V38-001: Added sys.path setup for src-layout projects to enable imports.
"""
import os
import sys
from pathlib import Path

import pytest
from pytest_socket import disable_socket, socket_allow_hosts


# =============================================================================
# V38-001: Fix for Bug #3 - Test imports fail without PYTHONPATH
# Add src/ to sys.path so tests can import from src-layout projects
# =============================================================================
def _setup_pythonpath():
    """Add src directory to Python path for src-layout projects."""
    # Find the repository root (where conftest.py lives or its parent)
    conftest_dir = Path(__file__).parent.resolve()
    
    # Common locations for src directory
    candidates = [
        conftest_dir.parent / "src",  # If conftest is in tests/
        conftest_dir / "src",          # If conftest is in repo root
        conftest_dir.parent,           # The repo root itself
    ]
    
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            str_path = str(candidate)
            if str_path not in sys.path:
                sys.path.insert(0, str_path)
                break
    
    # Also add the repo root for flat-layout projects
    repo_root = conftest_dir.parent if conftest_dir.name == "tests" else conftest_dir
    str_root = str(repo_root)
    if str_root not in sys.path:
        sys.path.insert(0, str_root)


# Run path setup at import time
_setup_pythonpath()


VALIDATION_PROFILES = {"offline", "record", "live"}
DEFAULT_ALLOWED_LOCALHOST = ["127.0.0.1", "localhost"]


def _current_profile() -> str:
    profile = os.getenv("VALIDATION_PROFILE", "offline").strip().lower() or "offline"
    return profile if profile in VALIDATION_PROFILES else "offline"


def _parse_allowlist(env_var: str) -> list[str]:
    raw = os.getenv(env_var, "")
    return [h.strip() for h in raw.split(",") if h.strip()]


def _set_record_mode(config: pytest.Config, desired: str) -> None:
    try:
        existing = config.getoption("--record-mode", default=None)
    except Exception:
        existing = None
    if existing:
        return
    if hasattr(config, "option") and hasattr(config.option, "record_mode"):
        config.option.record_mode = desired


def pytest_configure(config: pytest.Config) -> None:
    profile = _current_profile()
    if profile == "record":
        _set_record_mode(config, "once")
    else:
        _set_record_mode(config, "none")


@pytest.fixture(scope="session", autouse=True)
def enforce_validation_profile(pytestconfig: pytest.Config):
    profile = _current_profile()
    if profile == "offline":
        socket_allow_hosts(DEFAULT_ALLOWED_LOCALHOST)
        disable_socket()
    elif profile == "live":
        if os.getenv("ALLOW_LIVE", "0") != "1":
            raise pytest.UsageError("VALIDATION_PROFILE=live requires ALLOW_LIVE=1")
        allowlist = _parse_allowlist("LIVE_HOST_ALLOWLIST")
        if not allowlist:
            raise pytest.UsageError("VALIDATION_PROFILE=live requires LIVE_HOST_ALLOWLIST to be set")
        socket_allow_hosts(allowlist)


@pytest.fixture(scope="session", autouse=True)
def vcr_config():
    return {
        "filter_headers": ["authorization", "x-api-key", "x-auth-token"],
        "filter_query_parameters": ["api_key", "access_token", "token"],
        "record_on_exception": False,
        "allow_playback_repeats": False,
        "ignore_localhost": False,
        "allowed_hosts": DEFAULT_ALLOWED_LOCALHOST,
        "match_on": ["method", "scheme", "host", "port", "path", "query", "body"],
    }


def pytest_runtest_setup(item):
    profile = _current_profile()
    if profile == "live":
        if os.getenv("ALLOW_LIVE", "0") != "1":
            pytest.skip("VALIDATION_PROFILE=live requires ALLOW_LIVE=1")
        if not item.get_closest_marker("integration_live"):
            pytest.skip("Live profile only runs tests marked integration_live")
    elif profile == "offline":
        if item.get_closest_marker("integration_live"):
            pytest.skip("integration_live tests require VALIDATION_PROFILE=live")
