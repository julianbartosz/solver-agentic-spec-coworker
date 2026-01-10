"""
Repository fixtures for E2E testing.

Provides:
- Cached git cloning with version pinning
- Thread-safe caching with file locks
- Pre-defined fixtures for common test repos
"""

import hashlib
import logging
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Cache directory for cloned repos (survives test runs)
REPO_CACHE_DIR = Path(tempfile.gettempdir()) / "integration_coworker_repo_cache"

# Module-level lock for thread safety
_clone_lock = threading.Lock()


@dataclass
class RepoFixture:
    """
    A pinned repository fixture for deterministic testing.
    
    Key design decisions:
    - ref must be immutable (SHA or tag, not branch)
    - Cache key includes URL + ref + subpath for uniqueness
    - Thread-safe cloning with file locks
    
    Usage:
        fixture = RepoFixture(
            name="stripe-openapi",
            url="https://github.com/stripe/openapi.git",
            ref="v1234",  # Pinned tag or SHA
            subpath="openapi",  # Optional subdirectory
        )
        
        path = fixture.clone()
        # Work with path / "spec3.yaml"
    """
    
    name: str
    url: str
    ref: str  # Must be immutable: SHA or tag
    subpath: Optional[str] = None
    sparse_checkout: Optional[list] = field(default_factory=list)
    
    _local_path: Optional[Path] = field(default=None, init=False, repr=False)
    
    @property
    def cache_key(self) -> str:
        """Generate unique cache key from URL + ref + subpath."""
        content = f"{self.url}:{self.ref}:{self.subpath or ''}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    @property
    def cache_path(self) -> Path:
        """Path in cache directory."""
        return REPO_CACHE_DIR / f"{self.name}_{self.cache_key}"
    
    @property
    def local_path(self) -> Path:
        """
        Path to the cloned repo (or subpath within it).
        
        Raises:
            RuntimeError: If clone() hasn't been called
        """
        if self._local_path is None:
            raise RuntimeError(
                f"Repo '{self.name}' not cloned. Call clone() first."
            )
        base = self._local_path
        if self.subpath:
            return base / self.subpath
        return base
    
    def clone(self, force: bool = False) -> Path:
        """
        Clone the repository (cached, thread-safe).
        
        Args:
            force: Re-clone even if cached
            
        Returns:
            Path to the cloned repo (or subpath)
            
        Raises:
            subprocess.CalledProcessError: If git clone fails
        """
        with _clone_lock:
            return self._clone_impl(force)
    
    def _clone_impl(self, force: bool) -> Path:
        """Implementation of clone (called under lock)."""
        REPO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
        # Return cached if available
        if self.cache_path.exists() and not force:
            logger.debug(f"Using cached repo: {self.cache_path}")
            self._local_path = self.cache_path
            return self.local_path
        
        # Clean up existing cache
        if self.cache_path.exists():
            logger.info(f"Removing existing cache: {self.cache_path}")
            shutil.rmtree(self.cache_path, ignore_errors=True)
        
        logger.info(f"Cloning {self.url} at {self.ref}")
        
        # Clone with sparse checkout if specified
        if self.sparse_checkout:
            self._sparse_clone()
        else:
            self._full_clone()
        
        self._local_path = self.cache_path
        logger.info(f"Cloned to: {self.local_path}")
        return self.local_path
    
    def _full_clone(self):
        """Full shallow clone."""
        # Clone with depth=1 for speed
        subprocess.run(
            [
                "git", "clone",
                "--depth=1",
                "--single-branch",
                self.url,
                str(self.cache_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        
        # Checkout specific ref (may need to fetch if it's a tag)
        try:
            subprocess.run(
                ["git", "-C", str(self.cache_path), "checkout", self.ref],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            # Ref might not be in shallow clone, fetch it
            subprocess.run(
                [
                    "git", "-C", str(self.cache_path),
                    "fetch", "--depth=1", "origin", self.ref,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", str(self.cache_path), "checkout", "FETCH_HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
    
    def _sparse_clone(self):
        """Sparse checkout for large repos."""
        # Initialize with sparse checkout
        subprocess.run(
            [
                "git", "clone",
                "--depth=1",
                "--filter=blob:none",
                "--sparse",
                self.url,
                str(self.cache_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        
        # Configure sparse checkout paths
        for path in self.sparse_checkout:
            subprocess.run(
                [
                    "git", "-C", str(self.cache_path),
                    "sparse-checkout", "add", path,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        
        # Checkout ref
        subprocess.run(
            ["git", "-C", str(self.cache_path), "checkout", self.ref],
            check=True,
            capture_output=True,
            text=True,
        )
    
    def cleanup(self):
        """Remove cached clone."""
        if self.cache_path.exists():
            shutil.rmtree(self.cache_path, ignore_errors=True)
            logger.info(f"Cleaned up: {self.cache_path}")
        self._local_path = None
    
    def get_file(self, path: str) -> str:
        """
        Read a file from the cloned repo.
        
        Args:
            path: Relative path within the repo (or subpath)
            
        Returns:
            File contents as string
        """
        full_path = self.local_path / path
        return full_path.read_text()
    
    def list_files(self, pattern: str = "**/*") -> list[Path]:
        """
        List files matching a glob pattern.
        
        Args:
            pattern: Glob pattern (default: all files)
            
        Returns:
            List of matching paths
        """
        return list(self.local_path.glob(pattern))


def clone_repo(
    url: str,
    ref: str,
    name: Optional[str] = None,
    subpath: Optional[str] = None,
) -> Path:
    """
    Convenience function to clone a repo.
    
    Args:
        url: Git repository URL
        ref: Commit SHA or tag (must be immutable)
        name: Optional name for caching (default: derived from URL)
        subpath: Optional subdirectory to return
        
    Returns:
        Path to cloned repo (or subpath)
    """
    if name is None:
        # Derive name from URL
        name = url.rstrip("/").split("/")[-1].replace(".git", "")
    
    fixture = RepoFixture(
        name=name,
        url=url,
        ref=ref,
        subpath=subpath,
    )
    return fixture.clone()


# =============================================================================
# Pre-defined fixtures for common test repos
# =============================================================================

# Stripe OpenAPI specs - large, well-structured API
STRIPE_OPENAPI = RepoFixture(
    name="stripe-openapi",
    url="https://github.com/stripe/openapi.git",
    ref="master",  # TODO: Pin to specific SHA for production
    subpath="openapi",
)

# Kubernetes client-go - complex Go project
KUBERNETES_CLIENT_GO = RepoFixture(
    name="k8s-client-go",
    url="https://github.com/kubernetes/client-go.git",
    ref="v0.29.0",  # Pinned release tag
)

# Twilio OpenAPI - another real-world API spec
TWILIO_OPENAPI = RepoFixture(
    name="twilio-openapi",
    url="https://github.com/twilio/twilio-oai.git",
    ref="main",  # TODO: Pin to specific SHA
    subpath="spec",
)

# Small TypeScript starter - for quick tests
TYPESCRIPT_STARTER = RepoFixture(
    name="ts-starter",
    url="https://github.com/bitjson/typescript-starter.git",
    ref="main",
)
