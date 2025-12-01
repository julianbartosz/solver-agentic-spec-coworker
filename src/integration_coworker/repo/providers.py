"""
Repository provider abstraction and implementations.

Provides a unified interface for accessing repository content from:
1. Local filesystem (default)
2. GitHub API (for remote repo analysis without cloning)

The provider interface allows the system to analyze repositories
regardless of where they're hosted.
"""
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

from integration_coworker.repo.models import RepoSnapshot, MockFile

logger = logging.getLogger(__name__)


@dataclass
class RepoMetadata:
    """Metadata about a repository."""
    name: str
    owner: str
    default_branch: str = "main"
    description: Optional[str] = None
    language: Optional[str] = None
    size_kb: Optional[int] = None
    is_private: bool = False
    url: Optional[str] = None


@dataclass
class RepoSourceConfig:
    """Configuration for a repository source."""
    source_type: str  # "filesystem" or "github"

    # Filesystem options
    path: Optional[str] = None

    # GitHub options
    owner: Optional[str] = None
    repo: Optional[str] = None
    ref: Optional[str] = None  # Branch, tag, or commit SHA
    token_env_var: str = "GITHUB_TOKEN"
    api_base: str = "https://api.github.com"


class RepoProvider(ABC):
    """
    Abstract base class for repository providers.
    
    Implementations provide access to repository content from various sources.
    """

    @abstractmethod
    def list_files(self, prefix: str = "", pattern: Optional[str] = None) -> List[str]:
        """
        List files in the repository.
        
        Args:
            prefix: Directory prefix to filter by (e.g., "src/")
            pattern: Optional glob pattern to match (e.g., "*.py")
        
        Returns:
            List of relative file paths
        """
        pass

    @abstractmethod
    def read_file(self, path: str) -> str:
        """
        Read the contents of a file.
        
        Args:
            path: Relative path to the file
        
        Returns:
            File contents as string
        
        Raises:
            FileNotFoundError: If file doesn't exist
        """
        pass

    @abstractmethod
    def get_repo_metadata(self) -> RepoMetadata:
        """
        Get metadata about the repository.
        
        Returns:
            RepoMetadata with repo name, owner, language, etc.
        """
        pass

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """
        Check if a file exists in the repository.
        
        Args:
            path: Relative path to check
        
        Returns:
            True if file exists
        """
        pass

    def build_snapshot(self, max_files: int = 100) -> RepoSnapshot:
        """
        Build a RepoSnapshot from the repository.
        
        Args:
            max_files: Maximum number of files to include
        
        Returns:
            RepoSnapshot with files and tree markdown
        """
        metadata = self.get_repo_metadata()
        all_files = self.list_files()

        # Limit files
        file_paths = all_files[:max_files]

        # Read files
        files: Dict[str, MockFile] = {}
        for path in file_paths:
            try:
                content = self.read_file(path)
                files[path] = MockFile(path=path, content=content)
            except Exception as e:
                logger.debug(f"Could not read {path}: {e}")
                continue

        # Generate tree markdown
        tree_lines = [f"# {metadata.name} Repository Structure\n"]
        sorted_paths = sorted(files.keys())
        for path in sorted_paths:
            depth = path.count('/')
            indent = "  " * depth
            tree_lines.append(f"{indent}- {Path(path).name}")
        tree_markdown = "\n".join(tree_lines)

        # Generate full markdown
        full_lines = [tree_markdown, "\n## File Contents (Sample)\n"]
        for path in sorted_paths[:5]:
            file = files[path]
            full_lines.append(f"\n### {path}\n")
            content_preview = file.content[:500]
            if len(file.content) > 500:
                content_preview += "..."
            full_lines.append(f"```\n{content_preview}\n```")
        full_markdown = "\n".join(full_lines)

        # Stats
        stats = {
            "total_files": len(files),
            "total_size": sum(len(f.content) for f in files.values()),
            "source_type": "provider",
        }

        return RepoSnapshot(
            repo_name=metadata.name,
            owner=metadata.owner,
            files=files,
            stats=stats,
            tree_markdown=tree_markdown,
            full_markdown=full_markdown,
        )


class FilesystemProvider(RepoProvider):
    """
    Repository provider for local filesystem.
    
    This is the default provider for analyzing locally cloned repositories.
    """

    # Directories to skip during traversal
    SKIP_DIRS = {
        '.git', '__pycache__', '.venv', 'venv', 'node_modules',
        '.pytest_cache', '.mypy_cache', '.ruff_cache', 'dist', 'build',
        '.tox', '.eggs', '*.egg-info',
    }

    def __init__(self, repo_root: str):
        """
        Initialize filesystem provider.
        
        Args:
            repo_root: Path to the repository root directory
        
        Raises:
            FileNotFoundError: If repo_root doesn't exist
        """
        self.repo_root = Path(repo_root).resolve()
        if not self.repo_root.exists():
            raise FileNotFoundError(f"Repository root does not exist: {repo_root}")
        if not self.repo_root.is_dir():
            raise NotADirectoryError(f"Repository root is not a directory: {repo_root}")

    def list_files(self, prefix: str = "", pattern: Optional[str] = None) -> List[str]:
        """List files in the repository."""
        files = []
        start_path = self.repo_root / prefix if prefix else self.repo_root

        if not start_path.exists():
            return []

        for root, dirs, filenames in os.walk(start_path):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if d not in self.SKIP_DIRS]

            for filename in filenames:
                file_path = Path(root) / filename
                relative_path = str(file_path.relative_to(self.repo_root))

                # Apply pattern filter if provided
                if pattern:
                    import fnmatch
                    if not fnmatch.fnmatch(filename, pattern):
                        continue

                files.append(relative_path)

        return sorted(files)

    def read_file(self, path: str) -> str:
        """Read file contents."""
        file_path = self.repo_root / path

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not file_path.is_file():
            raise IsADirectoryError(f"Path is a directory: {path}")

        try:
            return file_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            raise ValueError(f"Cannot read binary file: {path}")

    def get_repo_metadata(self) -> RepoMetadata:
        """Get repository metadata from filesystem."""
        name = self.repo_root.name

        # Try to detect language from files
        language = self._detect_language()

        return RepoMetadata(
            name=name,
            owner="local",
            default_branch="main",
            description=None,
            language=language,
            size_kb=None,
            is_private=False,
            url=None,
        )

    def file_exists(self, path: str) -> bool:
        """Check if file exists."""
        return (self.repo_root / path).is_file()

    def _detect_language(self) -> Optional[str]:
        """Detect primary language from file extensions."""
        ext_counts: Dict[str, int] = {}

        for path in self.list_files()[:100]:  # Sample first 100 files
            ext = Path(path).suffix.lower()
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

        ext_to_lang = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.tsx': 'typescript',
            '.jsx': 'javascript',
            '.go': 'go',
            '.rs': 'rust',
            '.java': 'java',
            '.rb': 'ruby',
        }

        # Find most common language extension
        best_lang = None
        best_count = 0
        for ext, count in ext_counts.items():
            if ext in ext_to_lang and count > best_count:
                best_count = count
                best_lang = ext_to_lang[ext]

        return best_lang


class GitHubRepoProvider(RepoProvider):
    """
    Repository provider using GitHub API.
    
    Allows analyzing repositories without cloning them locally.
    Useful for CI/CD pipelines and SaaS deployments.
    
    Authentication:
    - Uses GITHUB_TOKEN environment variable by default
    - Token requires 'repo' scope for private repos
    - Public repos can be accessed without authentication (with rate limits)
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        ref: Optional[str] = None,
        token_env_var: str = "GITHUB_TOKEN",
        api_base: str = "https://api.github.com",
    ):
        """
        Initialize GitHub provider.
        
        Args:
            owner: Repository owner (user or organization)
            repo: Repository name
            ref: Branch, tag, or commit SHA (defaults to default branch)
            token_env_var: Environment variable containing GitHub token
            api_base: GitHub API base URL (for GitHub Enterprise)
        
        Raises:
            ValueError: If owner or repo is empty
        """
        if not owner or not repo:
            raise ValueError("owner and repo are required")

        self.owner = owner
        self.repo = repo
        self.ref = ref
        self.api_base = api_base.rstrip('/')
        self.token_env_var = token_env_var

        # Cache for API responses
        self._metadata_cache: Optional[RepoMetadata] = None
        self._tree_cache: Optional[List[Dict[str, Any]]] = None

    @property
    def _token(self) -> Optional[str]:
        """Get GitHub token from environment."""
        return os.environ.get(self.token_env_var)

    @property
    def _headers(self) -> Dict[str, str]:
        """Get HTTP headers for API requests."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "agentic-integration-designer",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, endpoint: str) -> Dict[str, Any]:
        """
        Make a request to GitHub API.
        
        Args:
            endpoint: API endpoint (e.g., "/repos/{owner}/{repo}")
        
        Returns:
            JSON response as dict
        
        Raises:
            RuntimeError: On API errors
        """
        import urllib.request
        import urllib.error
        import json

        url = f"{self.api_base}{endpoint}"

        req = urllib.request.Request(url, headers=self._headers)

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError(
                    f"GitHub authentication failed. "
                    f"Set {self.token_env_var} environment variable with a valid token."
                )
            elif e.code == 403:
                raise RuntimeError(
                    f"GitHub API rate limit exceeded or access denied. "
                    f"Provide a token via {self.token_env_var} for higher limits."
                )
            elif e.code == 404:
                raise RuntimeError(
                    f"Repository not found: {self.owner}/{self.repo}. "
                    f"Check the owner and repo name, or provide a token for private repos."
                )
            else:
                raise RuntimeError(f"GitHub API error {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error accessing GitHub: {e.reason}")

    def _get_tree(self) -> List[Dict[str, Any]]:
        """
        Get the repository tree (all files and directories).
        
        Returns:
            List of tree items with path, type, and sha
        """
        if self._tree_cache is not None:
            return self._tree_cache

        # Get default branch if ref not specified
        ref = self.ref
        if not ref:
            metadata = self.get_repo_metadata()
            ref = metadata.default_branch

        # Get tree recursively
        endpoint = f"/repos/{self.owner}/{self.repo}/git/trees/{ref}?recursive=1"
        response = self._request(endpoint)

        self._tree_cache = response.get("tree", [])
        return self._tree_cache

    def list_files(self, prefix: str = "", pattern: Optional[str] = None) -> List[str]:
        """List files in the repository via GitHub API."""
        tree = self._get_tree()

        files = []
        for item in tree:
            if item.get("type") != "blob":
                continue

            path = item.get("path", "")

            # Apply prefix filter
            if prefix and not path.startswith(prefix):
                continue

            # Apply pattern filter
            if pattern:
                import fnmatch
                filename = Path(path).name
                if not fnmatch.fnmatch(filename, pattern):
                    continue

            files.append(path)

        return sorted(files)

    def read_file(self, path: str) -> str:
        """Read file contents via GitHub API."""
        import base64

        # Get file ref (use cached ref or default branch)
        ref = self.ref
        if not ref:
            metadata = self.get_repo_metadata()
            ref = metadata.default_branch

        # Get file contents
        endpoint = f"/repos/{self.owner}/{self.repo}/contents/{path}?ref={ref}"
        response = self._request(endpoint)

        # Handle different response types
        if isinstance(response, list):
            # It's a directory
            raise IsADirectoryError(f"Path is a directory: {path}")

        encoding = response.get("encoding")
        content = response.get("content", "")

        if encoding == "base64":
            try:
                return base64.b64decode(content).decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError(f"Cannot decode binary file: {path}")
        elif encoding is None and response.get("type") == "file":
            # Small files may be returned directly
            return content
        else:
            raise ValueError(f"Unsupported encoding: {encoding}")

    def get_repo_metadata(self) -> RepoMetadata:
        """Get repository metadata from GitHub API."""
        if self._metadata_cache is not None:
            return self._metadata_cache

        endpoint = f"/repos/{self.owner}/{self.repo}"
        response = self._request(endpoint)

        self._metadata_cache = RepoMetadata(
            name=response.get("name", self.repo),
            owner=response.get("owner", {}).get("login", self.owner),
            default_branch=response.get("default_branch", "main"),
            description=response.get("description"),
            language=response.get("language"),
            size_kb=response.get("size"),
            is_private=response.get("private", False),
            url=response.get("html_url"),
        )

        return self._metadata_cache

    def file_exists(self, path: str) -> bool:
        """Check if file exists via GitHub API."""
        tree = self._get_tree()
        return any(
            item.get("path") == path and item.get("type") == "blob"
            for item in tree
        )


def get_repo_provider(source: RepoSourceConfig) -> RepoProvider:
    """
    Factory function to create appropriate repo provider.
    
    Args:
        source: Configuration specifying the source type and options
    
    Returns:
        RepoProvider instance for the specified source
    
    Raises:
        ValueError: If source type is unknown or configuration is invalid
    """
    if source.source_type == "filesystem":
        if not source.path:
            raise ValueError("Filesystem source requires 'path' configuration")
        return FilesystemProvider(source.path)

    elif source.source_type == "github":
        if not source.owner or not source.repo:
            raise ValueError("GitHub source requires 'owner' and 'repo' configuration")
        return GitHubRepoProvider(
            owner=source.owner,
            repo=source.repo,
            ref=source.ref,
            token_env_var=source.token_env_var,
            api_base=source.api_base,
        )

    else:
        raise ValueError(f"Unknown source type: {source.source_type}")
