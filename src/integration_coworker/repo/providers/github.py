"""
GitHub API repository provider.

Implements RepoProvider for GitHub repositories via API.
"""
import base64
from typing import List, Optional, Iterator, Dict, Any
import logging
import fnmatch

from .base import (
    RepoMetadata,
    SourceFile,
    FileType,
)
from .local import _classify_file_type, EXTENSION_TO_LANGUAGE, BINARY_EXTENSIONS

logger = logging.getLogger(__name__)

# Optional dependency
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


class GitHubRepoProvider:
    """
    Repository provider for GitHub via REST API.
    
    Supports both public and private repositories with token auth.
    """
    
    def __init__(
        self,
        owner: str,
        repo: str,
        token: Optional[str] = None,
        ref: Optional[str] = None,  # Branch, tag, or commit SHA
    ):
        """
        Initialize GitHub provider.
        
        Args:
            owner: Repository owner (user or org)
            repo: Repository name
            token: GitHub personal access token (optional for public repos)
            ref: Git reference (branch, tag, SHA). Defaults to default branch.
        """
        if not HAS_HTTPX:
            raise RuntimeError(
                "httpx is required for GitHubRepoProvider. "
                "Install with: pip install httpx"
            )
        
        self.owner = owner
        self.repo = repo
        self.ref = ref
        self._token = token
        
        # Set up HTTP client
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "integration-coworker/2.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        self._client = httpx.Client(
            base_url="https://api.github.com",
            headers=headers,
            timeout=30.0,
        )
        
        # Cache metadata
        self._metadata: Optional[RepoMetadata] = None
        self._tree_cache: Optional[Dict[str, Any]] = None
    
    def __del__(self):
        if hasattr(self, '_client'):
            self._client.close()
    
    def _get_repo_info(self) -> Dict[str, Any]:
        """Fetch repository information from API."""
        response = self._client.get(f"/repos/{self.owner}/{self.repo}")
        response.raise_for_status()
        return response.json()
    
    def _get_tree(self, recursive: bool = True) -> Dict[str, Any]:
        """Fetch repository tree."""
        if self._tree_cache:
            return self._tree_cache
        
        ref = self.ref or "HEAD"
        response = self._client.get(
            f"/repos/{self.owner}/{self.repo}/git/trees/{ref}",
            params={"recursive": "1"} if recursive else {},
        )
        response.raise_for_status()
        self._tree_cache = response.json()
        return self._tree_cache
    
    def get_metadata(self) -> RepoMetadata:
        """Get repository metadata from GitHub API."""
        if self._metadata:
            return self._metadata
        
        info = self._get_repo_info()
        tree = self._get_tree()
        
        file_count = sum(1 for item in tree.get("tree", []) if item["type"] == "blob")
        total_size = sum(
            item.get("size", 0)
            for item in tree.get("tree", [])
            if item["type"] == "blob"
        )
        
        self._metadata = RepoMetadata(
            name=info["name"],
            owner=info["owner"]["login"],
            default_branch=info.get("default_branch"),
            description=info.get("description"),
            primary_language=info.get("language"),
            file_count=file_count,
            total_size_bytes=total_size,
        )
        return self._metadata
    
    def list_files(
        self,
        pattern: Optional[str] = None,
        file_types: Optional[List[FileType]] = None,
        max_files: Optional[int] = None,
    ) -> List[str]:
        """List files from GitHub tree."""
        tree = self._get_tree()
        results = []
        
        for item in tree.get("tree", []):
            if item["type"] != "blob":
                continue
            
            path = item["path"]
            
            # Pattern filter
            if pattern and not fnmatch.fnmatch(path, pattern):
                continue
            
            # File type filter
            if file_types:
                ft = _classify_file_type(path)
                if ft not in file_types:
                    continue
            
            results.append(path)
            
            if max_files and len(results) >= max_files:
                break
        
        return results
    
    def get_file(self, path: str) -> SourceFile:
        """Get a file from GitHub."""
        response = self._client.get(
            f"/repos/{self.owner}/{self.repo}/contents/{path}",
            params={"ref": self.ref} if self.ref else {},
        )
        response.raise_for_status()
        data = response.json()
        
        ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
        is_binary = f'.{ext}' in BINARY_EXTENSIONS
        
        content = None
        if not is_binary and data.get("encoding") == "base64":
            try:
                content = base64.b64decode(data["content"]).decode("utf-8")
            except UnicodeDecodeError:
                is_binary = True
        
        return SourceFile(
            path=path,
            content=content,
            size_bytes=data.get("size", 0),
            file_type=_classify_file_type(path),
            language=EXTENSION_TO_LANGUAGE.get(f'.{ext}'),
            is_binary=is_binary,
            last_modified=None,  # Would need separate commit info call
        )
    
    def get_files(self, paths: List[str]) -> Iterator[SourceFile]:
        """Batch get files from GitHub."""
        # GitHub doesn't have a batch contents API, so we fetch one by one
        # Could optimize with GraphQL in future
        for path in paths:
            try:
                yield self.get_file(path)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.warning(f"File not found on GitHub: {path}")
                else:
                    raise
    
    def get_tree_markdown(self, max_depth: int = 4) -> str:
        """Generate markdown tree from GitHub tree API."""
        tree = self._get_tree()
        
        lines = [f"# {self.repo} Repository Structure\n"]
        
        # Build path hierarchy
        items = sorted(tree.get("tree", []), key=lambda x: x["path"])
        
        for item in items:
            path = item["path"]
            depth = path.count('/')
            
            if depth >= max_depth:
                continue
            
            indent = "  " * depth
            name = path.rsplit('/', 1)[-1]
            suffix = '/' if item["type"] == "tree" else ''
            lines.append(f"{indent}- {name}{suffix}")
        
        return "\n".join(lines)
    
    def select_relevant_files(
        self,
        task_description: str,
        max_files: int = 10,
        max_total_bytes: int = 50_000,
    ) -> List[SourceFile]:
        """Select relevant files using same heuristics as LocalRepoProvider."""
        task_lower = task_description.lower()
        task_words = set(task_lower.split())
        
        type_priority = {
            FileType.SOURCE_CODE: 3.0,
            FileType.CONFIG: 2.0,
            FileType.TEST: 1.5,
            FileType.DOCUMENTATION: 1.0,
            FileType.UNKNOWN: 0.5,
            FileType.BINARY: 0.0,
        }
        
        tree = self._get_tree()
        scored_files: List[tuple] = []
        
        for item in tree.get("tree", []):
            if item["type"] != "blob":
                continue
            
            path = item["path"]
            size = item.get("size", 0)
            file_type = _classify_file_type(path)
            
            if file_type == FileType.BINARY:
                continue
            
            score = type_priority.get(file_type, 0.5)
            
            path_lower = path.lower()
            path_words = set(path_lower.replace('/', ' ').replace('_', ' ').replace('-', ' ').split())
            overlap = len(task_words & path_words)
            score += overlap * 2.0
            
            if any(kw in path_lower for kw in ['client', 'api', 'service', 'integration']):
                score += 1.5
            
            scored_files.append((score, path, size))
        
        scored_files.sort(key=lambda x: x[0], reverse=True)
        
        result: List[SourceFile] = []
        total_bytes = 0
        
        for score, path, size in scored_files:
            if len(result) >= max_files:
                break
            
            if total_bytes + size > max_total_bytes:
                continue
            
            try:
                sf = self.get_file(path)
                if not sf.is_binary and sf.content:
                    result.append(sf)
                    total_bytes += sf.size_bytes
            except Exception as e:
                logger.warning(f"Failed to fetch {path}: {e}")
        
        return result
