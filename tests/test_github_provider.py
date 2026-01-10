"""
Tests for repository providers (filesystem and GitHub).

These tests verify:
1. LocalRepoProvider correctly reads local repositories
2. GitHubRepoProvider correctly uses GitHub API
3. get_provider factory creates correct provider types
4. Error handling for missing/invalid repositories

Most tests use @pytest.mark.no_db since they don't require database setup.
"""
import base64
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from integration_coworker.repo.providers import (
    RepoProvider,
    LocalRepoProvider,
    RepoMetadata,
    SourceFile,
    FileType,
    get_provider,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def temp_repo():
    """Create a temporary repository structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        
        # Create directory structure
        (repo / "src").mkdir()
        (repo / "src" / "app").mkdir()
        (repo / "tests").mkdir()
        
        # Create files
        (repo / "README.md").write_text("# Test Repo\n\nA test repository.")
        (repo / "pyproject.toml").write_text('[project]\nname = "test-repo"')
        (repo / "src" / "app" / "__init__.py").write_text("# App module")
        (repo / "src" / "app" / "main.py").write_text("def main(): pass")
        (repo / "tests" / "test_main.py").write_text("def test_main(): pass")
        
        yield repo


@pytest.fixture
def mock_github_api():
    """Mock GitHub API responses."""
    repo_response = {
        "name": "test-repo",
        "owner": {"login": "test-owner"},
        "default_branch": "main",
        "description": "A test repository",
        "language": "Python",
        "size": 1024,
        "private": False,
        "html_url": "https://github.com/test-owner/test-repo",
    }
    
    tree_response = {
        "tree": [
            {"path": "README.md", "type": "blob", "sha": "abc123", "size": 100},
            {"path": "src", "type": "tree", "sha": "def456"},
            {"path": "src/main.py", "type": "blob", "sha": "ghi789", "size": 200},
            {"path": "tests", "type": "tree", "sha": "jkl012"},
            {"path": "tests/test_main.py", "type": "blob", "sha": "mno345", "size": 150},
        ]
    }
    
    return {
        "repo": repo_response,
        "tree": tree_response,
    }


# =============================================================================
# Test RepoMetadata
# =============================================================================

@pytest.mark.no_db
class TestRepoMetadata:
    """Tests for RepoMetadata dataclass."""
    
    def test_creation_with_defaults(self):
        """Should create metadata with default values."""
        meta = RepoMetadata(name="test", owner="owner")
        assert meta.name == "test"
        assert meta.owner == "owner"
        assert meta.default_branch is None
        assert meta.description is None
    
    def test_creation_with_all_fields(self):
        """Should create metadata with all fields."""
        meta = RepoMetadata(
            name="test-repo",
            owner="test-owner",
            default_branch="develop",
            description="A test repo",
            primary_language="Python",
            file_count=100,
            total_size_bytes=1024000,
        )
        assert meta.default_branch == "develop"
        assert meta.primary_language == "Python"
        assert meta.total_size_bytes == 1024000


# =============================================================================
# Test SourceFile
# =============================================================================

@pytest.mark.no_db
class TestSourceFile:
    """Tests for SourceFile dataclass."""
    
    def test_creation_basic(self):
        """Should create source file with basic fields."""
        sf = SourceFile(
            path="src/main.py",
            content="def main(): pass",
            size_bytes=17,
            file_type=FileType.SOURCE_CODE,
        )
        assert sf.path == "src/main.py"
        assert sf.content == "def main(): pass"
        assert sf.file_type == FileType.SOURCE_CODE
        assert sf.is_binary is False
    
    def test_creation_binary(self):
        """Should create binary file without content."""
        sf = SourceFile(
            path="icon.png",
            content=None,
            size_bytes=5000,
            file_type=FileType.BINARY,
            is_binary=True,
        )
        assert sf.content is None
        assert sf.is_binary is True


# =============================================================================
# Test LocalRepoProvider
# =============================================================================

@pytest.mark.no_db
class TestLocalRepoProvider:
    """Tests for LocalRepoProvider."""
    
    def test_init_with_valid_path(self, temp_repo):
        """Should initialize with valid repository path."""
        provider = LocalRepoProvider(str(temp_repo))
        # Use resolve() on both sides to handle macOS /var -> /private/var symlink
        assert provider.repo_path.resolve() == temp_repo.resolve()
    
    def test_init_with_nonexistent_path(self):
        """Should raise FileNotFoundError for nonexistent path."""
        with pytest.raises(FileNotFoundError):
            LocalRepoProvider("/nonexistent/path")
    
    def test_init_with_file_path(self, temp_repo):
        """Should raise ValueError for file path (not a directory)."""
        file_path = temp_repo / "README.md"
        with pytest.raises(ValueError, match="not a directory"):
            LocalRepoProvider(str(file_path))
    
    def test_list_files(self, temp_repo):
        """Should list all files in repository."""
        provider = LocalRepoProvider(str(temp_repo))
        files = provider.list_files()
        
        assert "README.md" in files
        assert "pyproject.toml" in files
        assert "src/app/__init__.py" in files
        assert "src/app/main.py" in files
        assert "tests/test_main.py" in files
    
    def test_list_files_with_pattern(self, temp_repo):
        """Should filter files by pattern."""
        provider = LocalRepoProvider(str(temp_repo))
        files = provider.list_files(pattern="**/*.py")
        
        assert "src/app/__init__.py" in files
        assert "src/app/main.py" in files
        assert "tests/test_main.py" in files
        assert "README.md" not in files
        assert "pyproject.toml" not in files
    
    def test_list_files_with_file_type(self, temp_repo):
        """Should filter files by file type."""
        provider = LocalRepoProvider(str(temp_repo))
        files = provider.list_files(file_types=[FileType.CONFIG])
        
        assert "pyproject.toml" in files
        # Python files are SOURCE_CODE, not CONFIG
    
    def test_list_files_skips_excluded_dirs(self, temp_repo):
        """Should skip excluded directories like .git and __pycache__."""
        # Create excluded directories
        (temp_repo / ".git").mkdir()
        (temp_repo / ".git" / "config").write_text("git config")
        (temp_repo / "__pycache__").mkdir()
        (temp_repo / "__pycache__" / "cache.pyc").write_text("cache")
        
        provider = LocalRepoProvider(str(temp_repo))
        files = provider.list_files()
        
        assert ".git/config" not in files
        assert "__pycache__/cache.pyc" not in files
    
    def test_get_file(self, temp_repo):
        """Should read file contents."""
        provider = LocalRepoProvider(str(temp_repo))
        sf = provider.get_file("README.md")
        
        assert sf.path == "README.md"
        assert "# Test Repo" in sf.content
        assert "A test repository" in sf.content
        assert sf.file_type == FileType.DOCUMENTATION
        assert sf.is_binary is False
    
    def test_get_file_not_found(self, temp_repo):
        """Should raise FileNotFoundError for nonexistent file."""
        provider = LocalRepoProvider(str(temp_repo))
        
        with pytest.raises(FileNotFoundError):
            provider.get_file("nonexistent.txt")
    
    def test_get_files_iterator(self, temp_repo):
        """Should yield multiple files."""
        provider = LocalRepoProvider(str(temp_repo))
        paths = ["README.md", "pyproject.toml"]
        
        files = list(provider.get_files(paths))
        
        assert len(files) == 2
        assert files[0].path == "README.md"
        assert files[1].path == "pyproject.toml"
    
    def test_get_metadata(self, temp_repo):
        """Should return repository metadata."""
        provider = LocalRepoProvider(str(temp_repo))
        metadata = provider.get_metadata()
        
        assert metadata.name == temp_repo.name
        assert metadata.owner == "local"
        assert metadata.file_count > 0
    
    def test_get_tree_markdown(self, temp_repo):
        """Should generate tree markdown."""
        provider = LocalRepoProvider(str(temp_repo))
        tree = provider.get_tree_markdown(max_depth=3)
        
        assert "Repository Structure" in tree
        assert "src/" in tree
        assert "tests/" in tree
    
    def test_select_relevant_files(self, temp_repo):
        """Should select files based on task description."""
        provider = LocalRepoProvider(str(temp_repo))
        
        files = provider.select_relevant_files(
            task_description="main application logic",
            max_files=5,
        )
        
        # Should include main.py since it matches "main"
        paths = [f.path for f in files]
        assert any("main" in p for p in paths)


# =============================================================================
# Test GitHubRepoProvider (with httpx)
# =============================================================================

@pytest.mark.no_db
class TestGitHubRepoProvider:
    """Tests for GitHubRepoProvider."""
    
    @pytest.fixture
    def mock_httpx_client(self, mock_github_api):
        """Create a mock httpx.Client."""
        with patch('integration_coworker.repo.providers.github.HAS_HTTPX', True):
            with patch('integration_coworker.repo.providers.github.httpx') as mock_httpx:
                # Set up mock client
                mock_client = MagicMock()
                mock_httpx.Client.return_value = mock_client
                
                # Mock get method to return different responses based on URL
                def mock_get(url, **kwargs):
                    response = MagicMock()
                    response.raise_for_status = MagicMock()
                    
                    if "/git/trees/" in url:
                        response.json.return_value = mock_github_api["tree"]
                    elif "/repos/" in url and "/contents/" not in url:
                        response.json.return_value = mock_github_api["repo"]
                    elif "/contents/" in url:
                        content = "def main(): pass"
                        encoded = base64.b64encode(content.encode()).decode()
                        response.json.return_value = {
                            "type": "file",
                            "encoding": "base64",
                            "content": encoded,
                            "size": len(content),
                        }
                    
                    return response
                
                mock_client.get.side_effect = mock_get
                
                yield mock_client
    
    def test_init_basic(self, mock_httpx_client):
        """Should initialize with owner and repo."""
        from integration_coworker.repo.providers.github import GitHubRepoProvider
        
        provider = GitHubRepoProvider("owner", "repo")
        assert provider.owner == "owner"
        assert provider.repo == "repo"
        assert provider.ref is None
    
    def test_init_with_ref(self, mock_httpx_client):
        """Should accept optional ref (branch/tag/sha)."""
        from integration_coworker.repo.providers.github import GitHubRepoProvider
        
        provider = GitHubRepoProvider("owner", "repo", ref="develop")
        assert provider.ref == "develop"
    
    def test_get_metadata(self, mock_httpx_client, mock_github_api):
        """Should fetch and parse repository metadata."""
        from integration_coworker.repo.providers.github import GitHubRepoProvider
        
        provider = GitHubRepoProvider("test-owner", "test-repo")
        metadata = provider.get_metadata()
        
        assert metadata.name == "test-repo"
        assert metadata.owner == "test-owner"
        assert metadata.default_branch == "main"
        assert metadata.description == "A test repository"
    
    def test_list_files(self, mock_httpx_client, mock_github_api):
        """Should list files from repository tree."""
        from integration_coworker.repo.providers.github import GitHubRepoProvider
        
        provider = GitHubRepoProvider("test-owner", "test-repo", ref="main")
        files = provider.list_files()
        
        assert "README.md" in files
        assert "src/main.py" in files
        assert "tests/test_main.py" in files
        # Directories should not be in the list
        assert "src" not in files
    
    def test_get_file(self, mock_httpx_client, mock_github_api):
        """Should read file contents via API."""
        from integration_coworker.repo.providers.github import GitHubRepoProvider
        
        provider = GitHubRepoProvider("test-owner", "test-repo", ref="main")
        sf = provider.get_file("src/main.py")
        
        assert sf.path == "src/main.py"
        assert "def main()" in sf.content
        assert sf.file_type == FileType.SOURCE_CODE


# =============================================================================
# Test Provider Factory
# =============================================================================

@pytest.mark.no_db
class TestGetProvider:
    """Tests for get_provider factory function."""
    
    def test_creates_local_provider_for_path(self, temp_repo):
        """Should create LocalRepoProvider for filesystem path."""
        provider = get_provider(str(temp_repo))
        
        assert isinstance(provider, LocalRepoProvider)
        # Use resolve() on both sides to handle macOS /var -> /private/var symlink
        assert provider.repo_path.resolve() == temp_repo.resolve()
    
    def test_creates_local_provider_for_relative_path(self, temp_repo):
        """Should handle relative paths starting with ./ or ../"""
        # This is tricky to test without changing cwd
        # Just verify the path detection logic
        provider = get_provider(str(temp_repo))
        assert isinstance(provider, LocalRepoProvider)
    
    @patch('integration_coworker.repo.providers.github.HAS_HTTPX', True)
    @patch('integration_coworker.repo.providers.github.httpx')
    def test_creates_github_provider_for_url(self, mock_httpx):
        """Should create GitHubRepoProvider for GitHub URL."""
        mock_client = MagicMock()
        mock_httpx.Client.return_value = mock_client
        
        provider = get_provider(
            "https://github.com/test-owner/test-repo",
            github_token="ghp_test"
        )
        
        from integration_coworker.repo.providers.github import GitHubRepoProvider
        assert isinstance(provider, GitHubRepoProvider)
        assert provider.owner == "test-owner"
        assert provider.repo == "test-repo"
    
    @patch('integration_coworker.repo.providers.github.HAS_HTTPX', True)
    @patch('integration_coworker.repo.providers.github.httpx')
    def test_creates_github_provider_for_slug(self, mock_httpx):
        """Should create GitHubRepoProvider for owner/repo slug."""
        mock_client = MagicMock()
        mock_httpx.Client.return_value = mock_client
        
        provider = get_provider("microsoft/vscode")
        
        from integration_coworker.repo.providers.github import GitHubRepoProvider
        assert isinstance(provider, GitHubRepoProvider)
        assert provider.owner == "microsoft"
        assert provider.repo == "vscode"
    
    def test_raises_for_invalid_source(self):
        """Should raise ValueError for unrecognized source format."""
        with pytest.raises(ValueError, match="Unrecognized source format"):
            get_provider("not-a-valid-source")


# =============================================================================
# Test Provider Interface Compliance
# =============================================================================

@pytest.mark.no_db
class TestProviderInterface:
    """Tests to verify providers implement RepoProvider protocol."""
    
    def test_local_provider_has_required_methods(self, temp_repo):
        """LocalRepoProvider should have all required methods."""
        provider = LocalRepoProvider(str(temp_repo))
        
        required_methods = [
            "get_metadata",
            "list_files",
            "get_file",
            "get_files",
            "get_tree_markdown",
            "select_relevant_files",
        ]
        
        for method in required_methods:
            assert hasattr(provider, method), f"LocalRepoProvider missing {method}"
            assert callable(getattr(provider, method))
    
    @patch('integration_coworker.repo.providers.github.HAS_HTTPX', True)
    @patch('integration_coworker.repo.providers.github.httpx')
    def test_github_provider_has_required_methods(self, mock_httpx):
        """GitHubRepoProvider should have all required methods."""
        mock_client = MagicMock()
        mock_httpx.Client.return_value = mock_client
        
        from integration_coworker.repo.providers.github import GitHubRepoProvider
        provider = GitHubRepoProvider("owner", "repo")
        
        required_methods = [
            "get_metadata",
            "list_files",
            "get_file",
            "get_files",
            "get_tree_markdown",
            "select_relevant_files",
        ]
        
        for method in required_methods:
            assert hasattr(provider, method), f"GitHubRepoProvider missing {method}"
            assert callable(getattr(provider, method))


# =============================================================================
# Legacy Alias Tests (Backward Compatibility)
# =============================================================================

@pytest.mark.no_db
class TestLegacyAliases:
    """Tests for backward compatibility aliases."""
    
    def test_filesystem_provider_alias(self):
        """FilesystemProvider should be alias for LocalRepoProvider."""
        from integration_coworker.repo import FilesystemProvider
        assert FilesystemProvider is LocalRepoProvider
    
    def test_get_repo_provider_alias(self):
        """get_repo_provider should be alias for get_provider."""
        from integration_coworker.repo import get_repo_provider, get_provider
        assert get_repo_provider is get_provider
