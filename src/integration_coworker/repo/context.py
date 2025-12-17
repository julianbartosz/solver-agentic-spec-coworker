"""
Repo context provider for building RepoSnapshot from local filesystem.

This module provides backwards-compatible wrapper functions that delegate
to the new provider abstraction layer.
"""
from typing import Optional

from integration_coworker.repo.models import RepoSnapshot
from integration_coworker.repo.providers import (
    get_provider,
    LocalRepoProvider,
    FileType,
    SourceFile,
)


def filesystem_repo_context_provider(repo_root: str) -> RepoSnapshot:
    """
    Build a RepoSnapshot from a local filesystem directory.
    
    Backwards-compatible wrapper that delegates to LocalRepoProvider.
    
    Args:
        repo_root: Path to the local repository root
        
    Returns:
        RepoSnapshot with files, stats, and markdown representations
        
    Raises:
        FileNotFoundError: If repo_root does not exist
    """
    provider = LocalRepoProvider(root_path=repo_root)
    metadata = provider.get_metadata()
    
    # Get all files
    file_paths = provider.list_files(
        file_types=[FileType.SOURCE_CODE, FileType.CONFIG, FileType.DOCUMENTATION]
    )
    
    # Build files dict using SourceFile
    files = {}
    for sf in provider.get_files(file_paths):
        if sf.content is not None:
            files[sf.path] = SourceFile(
                path=sf.path,
                content=sf.content,
                size_bytes=len(sf.content),
            )
    
    # Generate tree markdown
    tree_markdown = provider.get_tree_markdown(max_depth=4)
    
    # Generate full markdown (tree + file contents sample)
    sorted_paths = sorted(files.keys())
    full_lines = [tree_markdown, "\n## File Contents (Sample)\n"]
    for path in sorted_paths[:5]:  # Only include first 5 files
        file = files[path]
        content_preview = file.content[:500] if len(file.content) > 500 else file.content
        full_lines.append(f"\n### {path}\n")
        full_lines.append(f"```\n{content_preview}...\n```")
    full_markdown = "\n".join(full_lines)
    
    # Basic stats
    stats = {
        "total_files": len(files),
        "total_size": sum(len(f.content) for f in files.values()),
    }
    
    return RepoSnapshot(
        repo_name=metadata.name,
        owner=metadata.owner,
        files=files,
        stats=stats,
        tree_markdown=tree_markdown,
        full_markdown=full_markdown
    )


def repo_context_from_source(
    source: str,
    *,
    github_token: Optional[str] = None,
    github_ref: Optional[str] = None,
    max_files: int = 100,
) -> RepoSnapshot:
    """
    Build a RepoSnapshot from any supported source.
    
    This is the new recommended entry point that supports both local
    and remote repositories through the unified provider abstraction.
    
    Args:
        source: Local path, GitHub URL, or owner/repo slug
        github_token: Personal access token for private repos
        github_ref: Branch, tag, or commit SHA
        max_files: Maximum number of files to include
        
    Returns:
        RepoSnapshot with files, stats, and markdown representations
        
    Example:
        >>> snapshot = repo_context_from_source("/path/to/local/repo")
        >>> snapshot = repo_context_from_source("microsoft/vscode", github_token="ghp_...")
    """
    provider = get_provider(
        source,
        github_token=github_token,
        github_ref=github_ref,
    )
    
    metadata = provider.get_metadata()
    
    # Get files
    file_paths = provider.list_files(
        file_types=[FileType.SOURCE_CODE, FileType.CONFIG, FileType.DOCUMENTATION],
        max_files=max_files,
    )
    
    # Build files dict
    files = {}
    for sf in provider.get_files(file_paths):
        if sf.content is not None:
            files[sf.path] = SourceFile(
                path=sf.path,
                content=sf.content,
                size_bytes=len(sf.content),
            )
    
    # Generate markdown
    tree_markdown = provider.get_tree_markdown(max_depth=4)
    
    sorted_paths = sorted(files.keys())
    full_lines = [tree_markdown, "\n## File Contents (Sample)\n"]
    for path in sorted_paths[:5]:
        file = files[path]
        content_preview = file.content[:500] if len(file.content) > 500 else file.content
        full_lines.append(f"\n### {path}\n")
        full_lines.append(f"```\n{content_preview}...\n```")
    full_markdown = "\n".join(full_lines)
    
    stats = {
        "total_files": len(files),
        "total_size": sum(len(f.content) for f in files.values()),
    }
    
    return RepoSnapshot(
        repo_name=metadata.name,
        owner=metadata.owner,
        files=files,
        stats=stats,
        tree_markdown=tree_markdown,
        full_markdown=full_markdown
    )
