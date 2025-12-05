"""
Local filesystem repository provider.

Implements RepoProvider for local directories.
"""
import os
import fnmatch
from pathlib import Path
from typing import List, Optional, Iterator, Dict
import logging
import re

from .base import (
    RepoProvider,
    RepoMetadata,
    SourceFile,
    FileType,
)

logger = logging.getLogger(__name__)

# Directories to always skip
SKIP_DIRS = {
    '.git', '__pycache__', '.venv', 'venv', 'node_modules',
    '.pytest_cache', '.mypy_cache', '.tox', 'dist', 'build',
    '.eggs', '*.egg-info', '.coverage', 'htmlcov',
}

# File extensions that are typically binary
BINARY_EXTENSIONS = {
    '.pyc', '.pyo', '.so', '.dll', '.exe', '.bin',
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx',
    '.zip', '.tar', '.gz', '.rar', '.7z',
    '.woff', '.woff2', '.ttf', '.eot',
    '.mp3', '.mp4', '.avi', '.mov',
}

# Extension to language mapping
EXTENSION_TO_LANGUAGE = {
    '.py': 'python',
    '.js': 'javascript',
    '.ts': 'typescript',
    '.jsx': 'javascript',
    '.tsx': 'typescript',
    '.java': 'java',
    '.go': 'go',
    '.rs': 'rust',
    '.rb': 'ruby',
    '.php': 'php',
    '.cs': 'csharp',
    '.cpp': 'cpp',
    '.c': 'c',
    '.h': 'c',
    '.hpp': 'cpp',
    '.swift': 'swift',
    '.kt': 'kotlin',
    '.scala': 'scala',
    '.r': 'r',
    '.sql': 'sql',
    '.sh': 'bash',
    '.bash': 'bash',
    '.zsh': 'zsh',
    '.ps1': 'powershell',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.json': 'json',
    '.xml': 'xml',
    '.html': 'html',
    '.css': 'css',
    '.scss': 'scss',
    '.less': 'less',
    '.md': 'markdown',
    '.rst': 'restructuredtext',
    '.txt': 'text',
    '.toml': 'toml',
    '.ini': 'ini',
    '.cfg': 'ini',
    '.dockerfile': 'dockerfile',
}


def _classify_file_type(path: str) -> FileType:
    """Classify a file based on its path and extension."""
    path_lower = path.lower()
    ext = Path(path).suffix.lower()
    
    # Binary check
    if ext in BINARY_EXTENSIONS:
        return FileType.BINARY
    
    # Test files
    if 'test' in path_lower or 'spec' in path_lower:
        return FileType.TEST
    
    # Config files
    config_patterns = [
        'config', 'settings', '.env', 'requirements',
        'package.json', 'pyproject.toml', 'setup.py', 'setup.cfg',
        'tsconfig', 'webpack', 'babel', 'eslint', 'prettier',
        'dockerfile', 'docker-compose', 'makefile', 'rakefile',
    ]
    if any(p in path_lower for p in config_patterns):
        return FileType.CONFIG
    
    # Documentation
    doc_patterns = ['readme', 'changelog', 'contributing', 'license', 'docs/']
    if any(p in path_lower for p in doc_patterns) or ext in {'.md', '.rst', '.txt'}:
        return FileType.DOCUMENTATION
    
    # Source code
    if ext in EXTENSION_TO_LANGUAGE:
        return FileType.SOURCE_CODE
    
    return FileType.UNKNOWN


def _is_binary_file(file_path: Path) -> bool:
    """Check if a file is binary by reading first 8KB."""
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(8192)
            # Check for null bytes (common in binary files)
            if b'\x00' in chunk:
                return True
            # Try to decode as UTF-8
            try:
                chunk.decode('utf-8')
                return False
            except UnicodeDecodeError:
                return True
    except (IOError, OSError):
        return True


class LocalRepoProvider:
    """
    Repository provider for local filesystem directories.
    
    Implements smart file selection and binary detection.
    """
    
    def __init__(self, repo_root: str = None, *, root_path: str = None):
        """
        Initialize with a local directory path.
        
        Args:
            repo_root: Absolute path to repository root (positional or keyword)
            root_path: Alias for repo_root (for backward compatibility)
            
        Raises:
            FileNotFoundError: If directory doesn't exist
            ValueError: If directory path is not a directory
        """
        # Support both repo_root and root_path for backward compatibility
        path = repo_root or root_path
        if path is None:
            raise ValueError("repo_root or root_path must be provided")
        
        self.repo_path = Path(path).resolve()
        if not self.repo_path.exists():
            raise FileNotFoundError(f"Repository root does not exist: {path}")
        if not self.repo_path.is_dir():
            raise ValueError(f"Repository root is not a directory: {path}")
        
        self._file_cache: Optional[List[str]] = None
    
    def get_metadata(self) -> RepoMetadata:
        """Get repository metadata from local directory."""
        # Try to get owner from git config
        owner = self._detect_owner()
        
        # Count files and size
        file_count = 0
        total_size = 0
        for f in self._walk_files():
            file_count += 1
            try:
                total_size += (self.repo_path / f).stat().st_size
            except OSError:
                pass
        
        # Detect primary language by file count
        language_counts: Dict[str, int] = {}
        for f in self._walk_files():
            ext = Path(f).suffix.lower()
            lang = EXTENSION_TO_LANGUAGE.get(ext)
            if lang:
                language_counts[lang] = language_counts.get(lang, 0) + 1
        
        primary_language = None
        if language_counts:
            primary_language = max(language_counts, key=language_counts.get)
        
        return RepoMetadata(
            name=self.repo_path.name,
            owner=owner,
            default_branch=self._detect_default_branch(),
            description=self._read_description(),
            primary_language=primary_language,
            file_count=file_count,
            total_size_bytes=total_size,
        )
    
    def _detect_owner(self) -> str:
        """
        Detect repository owner from git remote URL.
        
        Returns "local" if not a git repo or no remote configured.
        """
        git_config = self.repo_path / '.git' / 'config'
        if not git_config.exists():
            return "local"
        
        try:
            content = git_config.read_text()
            # Parse [remote "origin"] url = ...
            match = re.search(
                r'\[remote "origin"\].*?url\s*=\s*(?:git@github\.com:|https://github\.com/)([^/\n]+)',
                content,
                re.DOTALL
            )
            if match:
                return match.group(1)
        except Exception:
            pass
        
        return "local"
    
    def _detect_default_branch(self) -> Optional[str]:
        """Detect default branch from .git/HEAD."""
        head_file = self.repo_path / '.git' / 'HEAD'
        if head_file.exists():
            try:
                content = head_file.read_text().strip()
                if content.startswith('ref: refs/heads/'):
                    return content.replace('ref: refs/heads/', '')
            except Exception:
                pass
        return None
    
    def _read_description(self) -> Optional[str]:
        """Read repository description from README."""
        for readme_name in ['README.md', 'README.rst', 'README.txt', 'README']:
            readme_path = self.repo_path / readme_name
            if readme_path.exists():
                try:
                    content = readme_path.read_text()[:500]
                    # Return first paragraph
                    lines = content.split('\n\n')[0].split('\n')
                    # Skip title (# heading)
                    desc_lines = [l for l in lines if not l.startswith('#')]
                    return ' '.join(desc_lines).strip()[:200]
                except Exception:
                    pass
        return None
    
    def _walk_files(self) -> Iterator[str]:
        """Walk directory tree, respecting skip patterns."""
        for root, dirs, files in os.walk(self.repo_path):
            # Modify dirs in-place to skip unwanted directories
            dirs[:] = [
                d for d in dirs 
                if d not in SKIP_DIRS and not any(
                    fnmatch.fnmatch(d, pattern) for pattern in SKIP_DIRS
                )
            ]
            
            rel_root = Path(root).relative_to(self.repo_path)
            
            for filename in files:
                rel_path = str(rel_root / filename) if str(rel_root) != '.' else filename
                yield rel_path
    
    def list_files(
        self,
        pattern: Optional[str] = None,
        file_types: Optional[List[FileType]] = None,
        max_files: Optional[int] = None,
    ) -> List[str]:
        """List files with optional filtering."""
        results = []
        
        for rel_path in self._walk_files():
            # Pattern filter
            if pattern and not fnmatch.fnmatch(rel_path, pattern):
                continue
            
            # File type filter
            if file_types:
                ft = _classify_file_type(rel_path)
                if ft not in file_types:
                    continue
            
            results.append(rel_path)
            
            if max_files and len(results) >= max_files:
                break
        
        return results
    
    def get_file(self, path: str) -> SourceFile:
        """Get a file with content and metadata."""
        full_path = self.repo_path / path
        
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        stat = full_path.stat()
        ext = Path(path).suffix.lower()
        is_binary = ext in BINARY_EXTENSIONS or _is_binary_file(full_path)
        
        content = None
        if not is_binary:
            try:
                content = full_path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                is_binary = True
        
        return SourceFile(
            path=path,
            content=content,
            size_bytes=stat.st_size,
            file_type=_classify_file_type(path),
            language=EXTENSION_TO_LANGUAGE.get(ext),
            is_binary=is_binary,
            last_modified=None,  # Could use stat.st_mtime
        )
    
    def get_files(self, paths: List[str]) -> Iterator[SourceFile]:
        """Batch get files."""
        for path in paths:
            try:
                yield self.get_file(path)
            except FileNotFoundError:
                logger.warning(f"File not found during batch get: {path}")
    
    def get_tree_markdown(self, max_depth: int = 4) -> str:
        """Generate markdown tree of repository structure."""
        lines = [f"# {self.repo_path.name} Repository Structure\n"]
        
        def add_tree(path: Path, prefix: str, depth: int):
            if depth > max_depth:
                return
            
            try:
                entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except PermissionError:
                return
            
            # Filter entries
            entries = [
                e for e in entries
                if e.name not in SKIP_DIRS and not e.name.startswith('.')
            ]
            
            for i, entry in enumerate(entries):
                is_last = i == len(entries) - 1
                connector = "└── " if is_last else "├── "
                lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
                
                if entry.is_dir():
                    extension = "    " if is_last else "│   "
                    add_tree(entry, prefix + extension, depth + 1)
        
        add_tree(self.repo_path, "", 0)
        return "\n".join(lines)
    
    def select_relevant_files(
        self,
        task_description: str,
        max_files: int = 10,
        max_total_bytes: int = 50_000,
    ) -> List[SourceFile]:
        """
        Select files most relevant to the task.
        
        Uses keyword matching and file type prioritization.
        Future: Use embeddings for semantic matching.
        """
        # Score each file
        scored_files: List[tuple] = []
        task_lower = task_description.lower()
        task_words = set(task_lower.split())
        
        # Priority by file type
        type_priority = {
            FileType.SOURCE_CODE: 3.0,
            FileType.CONFIG: 2.0,
            FileType.TEST: 1.5,
            FileType.DOCUMENTATION: 1.0,
            FileType.UNKNOWN: 0.5,
            FileType.BINARY: 0.0,
        }
        
        for rel_path in self._walk_files():
            file_type = _classify_file_type(rel_path)
            
            # Skip binary files entirely
            if file_type == FileType.BINARY:
                continue
            
            score = type_priority.get(file_type, 0.5)
            
            # Keyword matching in path
            path_lower = rel_path.lower()
            path_words = set(path_lower.replace('/', ' ').replace('_', ' ').replace('-', ' ').split())
            overlap = len(task_words & path_words)
            score += overlap * 2.0
            
            # Boost for common integration-related files
            if any(kw in path_lower for kw in ['client', 'api', 'service', 'integration']):
                score += 1.5
            
            scored_files.append((score, rel_path))
        
        # Sort by score descending
        scored_files.sort(key=lambda x: x[0], reverse=True)
        
        # Collect files up to limits
        result: List[SourceFile] = []
        total_bytes = 0
        
        for score, path in scored_files:
            if len(result) >= max_files:
                break
            
            try:
                sf = self.get_file(path)
            except Exception:
                continue
            
            if sf.is_binary or sf.content is None:
                continue
            
            if total_bytes + sf.size_bytes > max_total_bytes:
                continue
            
            result.append(sf)
            total_bytes += sf.size_bytes
        
        return result
