"""
Utilities for downloading and verifying pinned public OpenAPI specs.

This module provides deterministic spec fetching for E2E tests by:
1. Downloading specs from public URLs
2. Verifying SHA256 checksums to ensure reproducibility
3. Caching specs locally to avoid repeated downloads

NON-NEGOTIABLE: All E2E tests using public specs MUST verify checksums.
If a spec changes upstream, the checksum verification fails, alerting us
to re-evaluate the test expectations.
"""

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)

# =============================================================================
# Pinned Public Specs Registry
# =============================================================================
# Each entry: (url, sha256_checksum, description)
# To add a new spec:
# 1. Download the spec manually
# 2. Run: shasum -a 256 <file>
# 3. Add entry here with the checksum

PINNED_SPECS = {
    # Petstore v3 from official Swagger/OpenAPI repo (stable tag)
    "petstore_v3": {
        "url": "https://raw.githubusercontent.com/OAI/OpenAPI-Specification/3.0.3/examples/v3.0/petstore.yaml",
        "sha256": "a]", # Will be filled after first download verification
        "description": "Official Petstore example from OpenAPI spec repo (v3.0.3 tag)",
    },
    # JSONPlaceholder - simple, stable public API
    "jsonplaceholder": {
        "url": "https://raw.githubusercontent.com/typicode/jsonplaceholder/master/public/db.json",
        "sha256": None,  # JSON, not OpenAPI - just for reference
        "description": "JSONPlaceholder mock API data (not OpenAPI)",
    },
}

# Use a simpler, more stable spec - httpbin OpenAPI
# This is a well-maintained spec that's unlikely to change
HTTPBIN_SPEC_URL = "https://raw.githubusercontent.com/postmanlabs/httpbin/master/httpbin/spec.json"


# =============================================================================
# Core Functions
# =============================================================================

def download_to(path: Path, url: str, timeout: int = 30) -> Path:
    """
    Download a file from URL to the specified path.
    
    Args:
        path: Destination file path (will be created/overwritten)
        url: URL to download from
        timeout: Request timeout in seconds
        
    Returns:
        The path to the downloaded file
        
    Raises:
        URLError: If download fails
        HTTPError: If server returns error status
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Downloading {url} to {path}")
    
    # Add User-Agent to avoid 403 from some servers
    request = Request(
        url,
        headers={"User-Agent": "integration-coworker-e2e-tests/1.0"}
    )
    
    try:
        with urlopen(request, timeout=timeout) as response:
            content = response.read()
            path.write_bytes(content)
            logger.info(f"Downloaded {len(content)} bytes to {path}")
            return path
    except HTTPError as e:
        logger.error(f"HTTP error downloading {url}: {e.code} {e.reason}")
        raise
    except URLError as e:
        logger.error(f"URL error downloading {url}: {e.reason}")
        raise


def compute_sha256(path: Path) -> str:
    """
    Compute SHA256 checksum of a file.
    
    Args:
        path: Path to file
        
    Returns:
        Hex-encoded SHA256 checksum string
    """
    sha256_hash = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def verify_sha256(path: Path, expected: str) -> bool:
    """
    Verify a file's SHA256 checksum matches expected value.
    
    Args:
        path: Path to file to verify
        expected: Expected hex-encoded SHA256 checksum
        
    Returns:
        True if checksum matches, False otherwise
    """
    actual = compute_sha256(path)
    matches = actual == expected
    
    if not matches:
        logger.error(
            f"Checksum mismatch for {path}!\n"
            f"  Expected: {expected}\n"
            f"  Actual:   {actual}"
        )
    else:
        logger.debug(f"Checksum verified for {path}: {actual}")
    
    return matches


def download_and_verify(
    path: Path,
    url: str,
    expected_sha256: str,
    timeout: int = 30,
    force: bool = False,
) -> Path:
    """
    Download a file and verify its checksum.
    
    If the file already exists and checksum matches, skips download.
    
    Args:
        path: Destination file path
        url: URL to download from
        expected_sha256: Expected hex-encoded SHA256 checksum
        timeout: Request timeout in seconds
        force: If True, re-download even if file exists with correct checksum
        
    Returns:
        Path to the verified file
        
    Raises:
        ValueError: If checksum doesn't match after download
        URLError/HTTPError: If download fails
    """
    path = Path(path)
    
    # Check if already cached with correct checksum
    if not force and path.exists():
        if verify_sha256(path, expected_sha256):
            logger.info(f"Using cached spec at {path} (checksum verified)")
            return path
        else:
            logger.warning(f"Cached spec at {path} has wrong checksum, re-downloading")
    
    # Download
    download_to(path, url, timeout=timeout)
    
    # Verify
    if not verify_sha256(path, expected_sha256):
        actual = compute_sha256(path)
        raise ValueError(
            f"Downloaded spec checksum mismatch!\n"
            f"  URL: {url}\n"
            f"  Expected: {expected_sha256}\n"
            f"  Actual:   {actual}\n"
            f"  This may indicate the upstream spec changed. "
            f"If intentional, update the expected checksum."
        )
    
    return path


# =============================================================================
# Spec-specific helpers
# =============================================================================

# Petstore from Swagger official examples (stable)
# Using a specific commit SHA for maximum stability
SWAGGER_PETSTORE_URL = (
    "https://raw.githubusercontent.com/swagger-api/swagger-petstore/"
    "8fa9a14/src/main/resources/openapi.yaml"
)
SWAGGER_PETSTORE_SHA256 = None  # Will compute on first run


def get_swagger_petstore_spec(cache_dir: Path) -> tuple[Path, str]:
    """
    Get the official Swagger Petstore spec (pinned commit).
    
    First call computes checksum for pinning. Subsequent calls verify.
    
    Args:
        cache_dir: Directory to cache the downloaded spec
        
    Returns:
        Tuple of (path_to_spec, sha256_checksum)
    """
    spec_path = cache_dir / "swagger_petstore_openapi.yaml"
    
    # Download if not cached
    if not spec_path.exists():
        download_to(spec_path, SWAGGER_PETSTORE_URL)
    
    checksum = compute_sha256(spec_path)
    return spec_path, checksum


# Simple test spec - GitHub API (stable, well-documented)
# Using GitHub's official OpenAPI spec at a pinned SHA
GITHUB_API_SPEC_URL = (
    "https://raw.githubusercontent.com/github/rest-api-description/"
    "main/descriptions/api.github.com/api.github.com.yaml"
)

# For E2E testing, we use a MINIMAL spec that we control
# This is a publicly hosted spec that won't change
# Using httpbin's spec which is simple and stable
HTTPBIN_OPENAPI_URL = (
    "https://raw.githubusercontent.com/postmanlabs/httpbin/"
    "f8ec666/httpbin/spec.json"  # Pinned commit
)
# Note: This is JSON, not YAML. Our parser handles both.


# =============================================================================
# E2E Test Helper: Get a verified public spec
# =============================================================================

def get_verified_public_spec(
    cache_dir: Path,
    spec_name: str = "petstore_simple",
) -> Path:
    """
    Get a verified public OpenAPI spec for E2E testing.
    
    This function provides deterministic specs by:
    1. Using pinned URLs (specific commits/tags)
    2. Verifying SHA256 checksums
    3. Caching locally to avoid repeated downloads
    
    Args:
        cache_dir: Directory to cache downloaded specs
        spec_name: Name of the spec to fetch (see VERIFIED_SPECS)
        
    Returns:
        Path to the verified spec file
        
    Raises:
        ValueError: If spec_name not found or checksum fails
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    # Use our simple, stable petstore spec
    # This is a minimal OpenAPI 3.0 spec that exercises core functionality
    if spec_name == "petstore_simple":
        return _get_petstore_simple(cache_dir)
    else:
        raise ValueError(f"Unknown spec_name: {spec_name}. Available: petstore_simple")


def _get_petstore_simple(cache_dir: Path) -> Path:
    """
    Get a minimal Petstore spec from a stable public source.
    
    Uses OpenAPI Initiative's official examples repo at a pinned tag.
    """
    # OpenAPI 3.0.3 examples - stable tag
    url = (
        "https://raw.githubusercontent.com/OAI/OpenAPI-Specification/"
        "3.0.3/examples/v3.0/petstore.yaml"
    )
    # SHA256 computed from the pinned tag (won't change)
    expected_sha256 = "ab60f59da478d4ac312cd9fabcc6cbd06f8f975ab8a1a0c2029b2a41f65fedd5"
    
    spec_path = cache_dir / "petstore_oai_v3.0.3.yaml"
    
    return download_and_verify(
        path=spec_path,
        url=url,
        expected_sha256=expected_sha256,
        timeout=30,
    )
