"""
MockedGithubRepoRetriever for generating markdown context from repo files.

Per Appendix D, this class provides deterministic markdown export of a repository
structure for use in RAG context by the attach_repo_context node.
"""
from typing import Dict
from collections import Counter
from pathlib import Path

from integration_coworker.repo.models import MockFile


class MockedGithubRepoRetriever:
    """
    In-memory representation of a repository for markdown export.
    
    Behavior:
    - Stores repo contents in memory via add_file()
    - export_markdown() produces deterministic markdown with:
        • Repo header (owner/repo_name)
        • File list with paths
        • Basic extension statistics
        • Full content or snippets of each file
    
    This is the canonical utility for converting (path, content) pairs into
    rich, deterministic markdown for RAG context.
    """
    
    def __init__(self, repo_name: str, owner: str = "mocked-user") -> None:
        """
        Initialize a mocked repo retriever.
        
        Args:
            repo_name: Name of the repository
            owner: Owner/organization name (default: "mocked-user")
        """
        self.repo_name = repo_name
        self.owner = owner
        self._files: Dict[str, MockFile] = {}
    
    def add_file(self, path: str, content: str) -> None:
        """
        Add or replace a file in the in-memory structure.
        
        Args:
            path: POSIX-style relative file path
            content: File text content
        """
        self._files[path] = MockFile(path=path, content=content)
    
    def calculate_stats(self) -> Dict[str, int]:
        """
        Calculate basic repository statistics.
        
        Returns:
            Dict with stats like total_files, total_size, extension counts
        """
        stats = {
            "total_files": len(self._files),
            "total_size": sum(len(f.content) for f in self._files.values()),
        }
        
        # Count files by extension
        extensions = Counter()
        for file_path in self._files.keys():
            ext = Path(file_path).suffix or "(no extension)"
            extensions[ext] += 1
        
        stats["extensions"] = dict(extensions)
        return stats
    
    def generate_tree(self) -> str:
        """
        Generate a simple directory tree representation.
        
        Returns:
            Markdown-formatted tree structure
        """
        if not self._files:
            return "*(empty repository)*"
        
        lines = ["```"]
        lines.append(f"{self.repo_name}/")
        
        # Sort paths for deterministic output
        sorted_paths = sorted(self._files.keys())
        
        # Build simple tree
        for path in sorted_paths:
            depth = path.count("/")
            indent = "  " * depth
            name = Path(path).name
            lines.append(f"{indent}├── {name}")
        
        lines.append("```")
        return "\n".join(lines)
    
    def export_markdown(self) -> str:
        """
        Produce a markdown summary of all files.
        
        Format includes:
        - Repo header
        - File list
        - Extension statistics
        - Content sections for each file
        
        Returns:
            Complete markdown representation of the repository
        """
        lines = []
        
        # Header
        lines.append(f"# Repository: {self.owner}/{self.repo_name}")
        lines.append("")
        
        # Stats
        stats = self.calculate_stats()
        lines.append("## Repository Statistics")
        lines.append(f"- Total files: {stats['total_files']}")
        lines.append(f"- Total size: {stats['total_size']:,} bytes")
        lines.append("")
        
        if stats.get("extensions"):
            lines.append("### Files by Extension")
            for ext, count in sorted(stats["extensions"].items()):
                lines.append(f"- `{ext}`: {count} file(s)")
            lines.append("")
        
        # File tree
        lines.append("## Directory Structure")
        lines.append(self.generate_tree())
        lines.append("")
        
        # File list with paths
        if self._files:
            lines.append("## Files")
            sorted_paths = sorted(self._files.keys())
            for path in sorted_paths:
                lines.append(f"- `{path}`")
            lines.append("")
        
        # File contents
        lines.append("## File Contents")
        lines.append("")
        
        sorted_paths = sorted(self._files.keys())
        for path in sorted_paths:
            file = self._files[path]
            lines.append(f"### {path}")
            lines.append("")
            
            # Determine language for syntax highlighting
            ext = Path(path).suffix.lower()
            lang_map = {
                ".py": "python",
                ".js": "javascript",
                ".ts": "typescript",
                ".json": "json",
                ".yaml": "yaml",
                ".yml": "yaml",
                ".md": "markdown",
                ".sh": "bash",
                ".sql": "sql",
            }
            lang = lang_map.get(ext, "")
            
            # Truncate very long files
            content = file.content
            max_lines = 100
            content_lines = content.split("\n")
            
            if len(content_lines) > max_lines:
                truncated = "\n".join(content_lines[:max_lines])
                lines.append(f"```{lang}")
                lines.append(truncated)
                lines.append(f"... ({len(content_lines) - max_lines} more lines omitted)")
                lines.append("```")
            else:
                lines.append(f"```{lang}")
                lines.append(content)
                lines.append("```")
            
            lines.append("")
        
        return "\n".join(lines)
