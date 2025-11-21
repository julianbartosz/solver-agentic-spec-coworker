"""
Repo integration models.

Defines structures for repository profiles, snapshots, and change sets.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from pathlib import Path


@dataclass
class RepoProfile:
    """
    Metadata about a target repository's structure and conventions.
    
    This helps the system understand where to place generated code.
    """
    name: str  # e.g., "next-js-app-router", "django-rest"
    framework: Optional[str] = None  # e.g., "nextjs", "django", "fastapi"
    language: str = "python"
    integrations_root: str = "integrations"  # Where to place integration code
    tests_root: str = "tests"  # Where to place test files
    conventions: Optional[Dict[str, Any]] = None  # Framework-specific patterns
    layout_hints: Optional[Dict[str, Any]] = None  # Additional layout information
    integration_hooks: Optional[Dict[str, Any]] = None  # Files to update (router, settings, etc.)


@dataclass
class MockFile:
    """A simplified representation of a file for repo analysis."""
    path: str  # Relative path from repo root
    content: str  # File contents


@dataclass
class RepoSnapshot:
    """
    A snapshot of repository structure and key files.
    
    Used for understanding the target repo's layout without persisting everything.
    """
    repo_name: str
    owner: str
    files: Dict[str, MockFile] = field(default_factory=dict)  # Maps rel_path -> MockFile
    stats: Dict[str, Any] = field(default_factory=dict)  # Stats like total_files, total_size
    tree_markdown: Optional[str] = None  # Tree structure as markdown
    full_markdown: Optional[str] = None  # Full context including file samples
    repo_root: Optional[Path] = None  # Optional explicit root path


@dataclass
class FileChange:
    """Represents a single file change (create, update, or delete)."""
    rel_path: str  # Relative path from repo root
    change_type: str  # "create", "update", or "delete"
    content: Optional[str] = None  # New content (for create/update)
    original_content: Optional[str] = None  # Original content (for update)
    before: Optional[str] = None  # Alias for original_content
    after: Optional[str] = None  # Alias for content


@dataclass
class RepoChangeSet:
    """
    A set of changes to apply to a repository.
    
    Used for both planning (dry-run) and actual file writes.
    """
    repo_root: Path
    changes: List[FileChange] = field(default_factory=list)
    applied: bool = False  # Whether changes have been written to disk
    
    def files_created(self) -> List[FileChange]:
        """Get list of files to be created."""
        return [c for c in self.changes if c.change_type == "create"]
    
    def files_updated(self) -> List[FileChange]:
        """Get list of files to be updated."""
        return [c for c in self.changes if c.change_type == "update"]
    
    def files_deleted(self) -> List[FileChange]:
        """Get list of files to be deleted."""
        return [c for c in self.changes if c.change_type == "delete"]
