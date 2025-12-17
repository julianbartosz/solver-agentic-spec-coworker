"""
Repository provider protocol and common types.

This module defines the interface that all repo providers must implement.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, List, Optional, Dict, Any, Iterator
from enum import Enum


class FileType(Enum):
    """Classification of file types for smart filtering."""
    SOURCE_CODE = "source_code"
    CONFIG = "config"
    DOCUMENTATION = "documentation"
    TEST = "test"
    BINARY = "binary"
    UNKNOWN = "unknown"


@dataclass
class SourceFile:
    """
    Represents a file in a repository with full metadata.
    
    Replaces MockFile for production use.
    """
    path: str                           # Relative path from repo root
    content: Optional[str] = None       # None for binary files
    size_bytes: int = 0
    file_type: FileType = FileType.UNKNOWN
    language: Optional[str] = None      # Detected programming language
    is_binary: bool = False
    last_modified: Optional[str] = None # ISO 8601 timestamp if available
    
    @property
    def extension(self) -> str:
        return Path(self.path).suffix.lower()


@dataclass
class RepoMetadata:
    """Repository-level metadata."""
    name: str
    owner: str                          # GitHub username, org name, or "local"
    default_branch: Optional[str] = None
    description: Optional[str] = None
    primary_language: Optional[str] = None
    file_count: int = 0
    total_size_bytes: int = 0


class RepoProvider(Protocol):
    """
    Protocol for repository content providers.
    
    Implementations must support both local filesystem and remote APIs.
    """
    
    def get_metadata(self) -> RepoMetadata:
        """Get repository-level metadata including owner."""
        ...
    
    def list_files(
        self,
        pattern: Optional[str] = None,
        file_types: Optional[List[FileType]] = None,
        max_files: Optional[int] = None,
    ) -> List[str]:
        """
        List files in the repository.
        
        Args:
            pattern: Glob pattern to filter files (e.g., "**/*.py")
            file_types: Filter by file type classification
            max_files: Maximum number of files to return
            
        Returns:
            List of relative file paths
        """
        ...
    
    def get_file(self, path: str) -> SourceFile:
        """
        Get a single file with content and metadata.
        
        Args:
            path: Relative path from repo root
            
        Returns:
            SourceFile with content loaded
            
        Raises:
            FileNotFoundError: If file doesn't exist
        """
        ...
    
    def get_files(self, paths: List[str]) -> Iterator[SourceFile]:
        """
        Batch get multiple files.
        
        More efficient than calling get_file() repeatedly for remote providers.
        """
        ...
    
    def get_tree_markdown(self, max_depth: int = 4) -> str:
        """Generate a markdown tree representation of the repo structure."""
        ...
    
    def select_relevant_files(
        self,
        task_description: str,
        max_files: int = 10,
        max_total_bytes: int = 50_000,
    ) -> List[SourceFile]:
        """
        Smart file selection based on task relevance.
        
        Uses heuristics or embeddings to select the most relevant files
        for a given task description.
        
        Args:
            task_description: Natural language description of the task
            max_files: Maximum number of files to return
            max_total_bytes: Maximum total content size
            
        Returns:
            List of SourceFile objects, most relevant first
        """
        ...
