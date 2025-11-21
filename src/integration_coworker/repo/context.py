"""
Repo context provider for building RepoSnapshot from local filesystem.
Phase 2: Minimal implementation.
"""
import os
from pathlib import Path
from typing import Dict, Any, cast
from integration_coworker.repo.models import RepoSnapshot, MockFile


def filesystem_repo_context_provider(repo_root: str) -> RepoSnapshot:
    """
    Build a RepoSnapshot from a local filesystem directory.
    Phase 2: Minimal implementation that reads files and generates markdown.
    """
    repo_path = Path(repo_root)
    if not repo_path.exists():
        raise FileNotFoundError(f"Repo root does not exist: {repo_root}")
    
    # Extract repo name from path
    repo_name = repo_path.name
    owner = "local"  # Phase 2 simplification
    
    # Collect files
    files: Dict[str, MockFile] = {}
    for root, dirs, filenames in os.walk(repo_root):
        # Skip common non-source directories
        dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', '.venv', 'venv', 'node_modules', '.pytest_cache'}]
        
        for filename in filenames:
            file_path = Path(root) / filename
            relative_path = str(file_path.relative_to(repo_path))
            
            # Read file content (skip binary files in Phase 2)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                files[relative_path] = MockFile(
                    path=relative_path,
                    content=content
                )
            except (UnicodeDecodeError, PermissionError):
                # Skip binary or unreadable files
                continue
    
    # Generate tree markdown
    tree_lines = [f"# {repo_name} Repository Structure\n"]
    sorted_paths = sorted(files.keys())
    for path in sorted_paths:
        depth = path.count('/')
        indent = "  " * depth
        tree_lines.append(f"{indent}- {Path(path).name}")
    tree_markdown = "\n".join(tree_lines)
    
    # Generate full markdown (tree + file contents sample)
    full_lines = [tree_markdown, "\n## File Contents (Sample)\n"]
    for path in sorted_paths[:5]:  # Only include first 5 files
        file = files[path]
        full_lines.append(f"\n### {path}\n")
        full_lines.append(f"```\n{file.content[:500]}...\n```")  # First 500 chars
    full_markdown = "\n".join(full_lines)
    
    # Basic stats
    stats = {
        "total_files": len(files),
        "total_size": sum(len(f.content) for f in files.values()),
    }
    
    return RepoSnapshot(
        repo_name=repo_name,
        owner=owner,
        files=files,
        stats=stats,
        tree_markdown=tree_markdown,
        full_markdown=full_markdown
    )
