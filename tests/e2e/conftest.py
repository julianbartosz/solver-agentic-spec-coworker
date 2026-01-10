"""
Fixtures for end-to-end tests.

Provides:
- Repo cloning with caching and version pinning
- Docker runner for Tier.PROD validation
- LLM budget guards for cost control
- Marker definitions for test selection

NOTE: Postgres fixtures are defined in tests/conftest.py and imported automatically.
Use `postgres_env` fixture in tests that need DATABASE_URL configured.
"""

import os
import shutil
import subprocess
import tempfile
import hashlib
from pathlib import Path
from typing import Optional, Generator

import pytest

from integration_coworker.codegen.gates.docker_runner import (
    DockerRunner,
    DockerConfig,
    is_docker_available,
)


# ============================================================================
# Markers
# ============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "e2e: End-to-end tests requiring real infrastructure"
    )
    config.addinivalue_line(
        "markers", "docker: Tests requiring Docker daemon"
    )
    config.addinivalue_line(
        "markers", "live_llm: Tests calling real LLM APIs (cost incurred)"
    )
    config.addinivalue_line(
        "markers", "slow: Tests that take > 30 seconds"
    )


# ============================================================================
# Skip Conditions
# ============================================================================

# Skip E2E tests if Docker not available
docker_available = pytest.mark.skipif(
    not is_docker_available(),
    reason="Docker not available"
)

# Skip live LLM tests unless explicitly enabled
live_llm_enabled = pytest.mark.skipif(
    os.getenv("ENABLE_LIVE_LLM_TESTS", "").lower() != "true",
    reason="Live LLM tests disabled (set ENABLE_LIVE_LLM_TESTS=true)"
)


# ============================================================================
# Repo Clone Fixtures
# ============================================================================

# Cache directory for cloned repos
REPO_CACHE_DIR = Path(tempfile.gettempdir()) / "e2e_repo_cache"


class RepoFixture:
    """A cloned repository fixture with version pinning."""
    
    def __init__(
        self,
        name: str,
        url: str,
        ref: str,  # SHA or tag - must be immutable
        subpath: Optional[str] = None,  # Optional subdirectory
    ):
        self.name = name
        self.url = url
        self.ref = ref
        self.subpath = subpath
        self._local_path: Optional[Path] = None
    
    @property
    def cache_key(self) -> str:
        """Unique key for caching."""
        content = f"{self.url}:{self.ref}:{self.subpath or ''}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    @property
    def cache_path(self) -> Path:
        """Path in cache directory."""
        return REPO_CACHE_DIR / f"{self.name}_{self.cache_key}"
    
    @property
    def local_path(self) -> Path:
        """Path to the cloned repo (or subpath if specified)."""
        if self._local_path is None:
            raise RuntimeError("Repo not cloned. Use clone() or fixture.")
        base = self._local_path
        if self.subpath:
            return base / self.subpath
        return base
    
    def clone(self, force: bool = False) -> Path:
        """
        Clone the repository (cached).
        
        Args:
            force: If True, re-clone even if cached
            
        Returns:
            Path to the cloned repository
        """
        REPO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
        if self.cache_path.exists() and not force:
            self._local_path = self.cache_path
            return self.local_path
        
        # Clone fresh
        if self.cache_path.exists():
            shutil.rmtree(self.cache_path)
        
        subprocess.run(
            ["git", "clone", "--depth=1", self.url, str(self.cache_path)],
            check=True,
            capture_output=True,
        )
        
        # Checkout specific ref
        subprocess.run(
            ["git", "-C", str(self.cache_path), "checkout", self.ref],
            check=True,
            capture_output=True,
        )
        
        self._local_path = self.cache_path
        return self.local_path
    
    def cleanup(self):
        """Remove cached clone (for fresh test runs)."""
        if self.cache_path.exists():
            shutil.rmtree(self.cache_path, ignore_errors=True)


# Pre-defined repo fixtures for testing
# Using pinned SHAs for deterministic tests

STRIPE_OPENAPI = RepoFixture(
    name="stripe-openapi",
    url="https://github.com/stripe/openapi.git",
    ref="master",  # Will pin to specific SHA in production
    subpath="openapi",
)

KUBERNETES_CLIENT_GO = RepoFixture(
    name="k8s-client-go",
    url="https://github.com/kubernetes/client-go.git",
    ref="v0.29.0",  # Pinned release tag
)

SIMPLE_TYPESCRIPT_PROJECT = RepoFixture(
    name="typescript-starter",
    url="https://github.com/bitjson/typescript-starter.git",
    ref="main",  # Starter template
)


# ============================================================================
# Docker Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def docker_runner() -> Generator[DockerRunner, None, None]:
    """
    Session-scoped Docker runner.
    
    Skips if Docker not available.
    """
    if not is_docker_available():
        pytest.skip("Docker not available")
    
    config = DockerConfig()
    runner = DockerRunner(config)
    yield runner


@pytest.fixture
def temp_project_dir() -> Generator[Path, None, None]:
    """Temporary directory for generated code."""
    with tempfile.TemporaryDirectory(prefix="e2e_project_") as tmpdir:
        yield Path(tmpdir)


# ============================================================================
# LLM Budget Guards
# ============================================================================

class LLMBudgetExceeded(Exception):
    """Raised when LLM spending exceeds budget."""
    pass


class LLMBudgetGuard:
    """
    Guard against runaway LLM costs in E2E tests.
    
    Usage:
        with LLMBudgetGuard(max_calls=10, max_tokens=50000) as guard:
            # Your LLM calls here
            guard.record_call(tokens=1500)
    """
    
    def __init__(
        self,
        max_calls: int = 20,
        max_input_tokens: int = 100_000,
        max_output_tokens: int = 50_000,
    ):
        self.max_calls = max_calls
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.call_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
    
    def record_call(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ):
        """Record an LLM call and check budget."""
        self.call_count += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        
        if self.call_count > self.max_calls:
            raise LLMBudgetExceeded(
                f"Exceeded max calls: {self.call_count} > {self.max_calls}"
            )
        if self.input_tokens > self.max_input_tokens:
            raise LLMBudgetExceeded(
                f"Exceeded input tokens: {self.input_tokens} > {self.max_input_tokens}"
            )
        if self.output_tokens > self.max_output_tokens:
            raise LLMBudgetExceeded(
                f"Exceeded output tokens: {self.output_tokens} > {self.max_output_tokens}"
            )
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Log final usage
        print(
            f"LLM Budget: {self.call_count} calls, "
            f"{self.input_tokens} input, {self.output_tokens} output"
        )
        return False


@pytest.fixture
def llm_budget_guard():
    """Fixture providing LLM budget guard with test-appropriate limits."""
    return LLMBudgetGuard(
        max_calls=10,
        max_input_tokens=50_000,
        max_output_tokens=25_000,
    )
