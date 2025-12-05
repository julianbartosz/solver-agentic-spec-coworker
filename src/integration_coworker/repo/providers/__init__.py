"""
Repository provider abstraction layer.

Provides unified access to local and remote repositories.
"""
from typing import Union, Optional

from .base import RepoProvider, RepoMetadata, SourceFile, FileType
from .local import LocalRepoProvider


# Export public API
__all__ = [
    "RepoProvider",
    "RepoMetadata",
    "SourceFile",
    "FileType",
    "LocalRepoProvider",
    "get_provider",
]


def get_provider(
    source: Union[str, "os.PathLike"],
    *,
    github_token: Optional[str] = None,
    github_ref: Optional[str] = None,
) -> RepoProvider:
    """
    Factory function to get appropriate repository provider.
    
    Automatically detects whether source is a local path or GitHub URL/slug.
    
    Args:
        source: Either a local filesystem path or GitHub identifier.
                Supported formats:
                - Local path: "/path/to/repo" or "./relative/path" or Path object
                - GitHub URL: "https://github.com/owner/repo"
                - GitHub slug: "owner/repo"
        github_token: Personal access token for private repos (optional)
        github_ref: Branch, tag, or commit SHA (optional, defaults to default branch)
    
    Returns:
        RepoProvider: Configured provider instance
        
    Raises:
        ValueError: If source format is not recognized
        RuntimeError: If GitHubRepoProvider is needed but httpx is not installed
    
    Example:
        >>> provider = get_provider("/path/to/local/repo")
        >>> provider = get_provider("microsoft/vscode", github_token="ghp_...")
        >>> provider = get_provider("https://github.com/owner/repo", github_ref="main")
    """
    import os
    from pathlib import Path
    
    # Convert Path objects to strings
    if isinstance(source, Path):
        source = str(source)
    
    source = source.strip()
    
    # Check if it's a local path
    if os.path.exists(source) or source.startswith(('./', '../', '/')):
        return LocalRepoProvider(root_path=source)
    
    # Check for GitHub URL patterns
    if source.startswith("https://github.com/"):
        # Parse: https://github.com/owner/repo
        parts = source.replace("https://github.com/", "").rstrip("/").split("/")
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            # Import here to avoid import error if httpx not installed
            from .github import GitHubRepoProvider
            return GitHubRepoProvider(
                owner=owner,
                repo=repo,
                token=github_token,
                ref=github_ref,
            )
    
    # Check for owner/repo slug format
    if "/" in source and not source.startswith(('http://', 'https://', '/')):
        parts = source.split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            owner, repo = parts
            from .github import GitHubRepoProvider
            return GitHubRepoProvider(
                owner=owner,
                repo=repo,
                token=github_token,
                ref=github_ref,
            )
    
    raise ValueError(
        f"Unrecognized source format: '{source}'. "
        "Expected local path, GitHub URL, or owner/repo slug."
    )
