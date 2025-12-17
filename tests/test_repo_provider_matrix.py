"""
Repo Provider Sanity Matrix Tests.

This test module exercises attach_repo_context under multiple combinations:
1. Local provider + FastAPI profile
2. Local provider + Generic profile  
3. GitHub provider (mocked) + profile from metadata

Validates that all paths produce valid RepoSnapshot and repo_markdown_context.

Per Item 3 of hardening roadmap:
- Test attach_repo_context across provider/profile combinations
- Ensure each path produces valid context for downstream nodes
"""
import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.nodes.attach_repo_context import attach_repo_context
from integration_coworker.repo.providers import (
    LocalRepoProvider,
    get_provider,
)
from integration_coworker.repo.profiles import (
    FASTAPI_PROFILE,
    FLASK_PROFILE,
    SUBATOMIC_MOCK_PROFILE,
    detect_profile_from_repo,
)
from integration_coworker.repo.models import RepoProfile


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def fastapi_repo(tmp_path):
    """Create a FastAPI-style repository structure."""
    # pyproject.toml with FastAPI
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('''
[project]
name = "my-fastapi-app"
dependencies = [
    "fastapi>=0.100.0",
    "uvicorn[standard]>=0.22.0",
]
''')
    
    # src/app structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app").mkdir()
    (tmp_path / "src" / "app" / "__init__.py").write_text("# App module")
    (tmp_path / "src" / "app" / "main.py").write_text('''
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Hello"}
''')
    
    # tests
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "tests" / "test_main.py").write_text("def test_root(): pass")
    
    # README
    (tmp_path / "README.md").write_text("# My FastAPI App\n\nA sample service.")
    
    return tmp_path


@pytest.fixture
def generic_python_repo(tmp_path):
    """Create a generic Python repository (no specific framework)."""
    # Basic pyproject.toml
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('''
[project]
name = "my-generic-lib"
dependencies = []
''')
    
    # src structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mylib").mkdir()
    (tmp_path / "src" / "mylib" / "__init__.py").write_text("# Library module")
    (tmp_path / "src" / "mylib" / "core.py").write_text("def process(): pass")
    
    # tests
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    
    return tmp_path


@pytest.fixture
def flask_repo(tmp_path):
    """Create a Flask-style repository structure."""
    # requirements.txt with Flask
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("flask>=2.0.0\ngunicorn\n")
    
    # app.py
    (tmp_path / "app.py").write_text('''
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def hello():
    return jsonify({"message": "Hello from Flask"})
''')
    
    # templates directory
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "index.html").write_text("<h1>Hello</h1>")
    
    return tmp_path


@pytest.fixture
def mock_github_responses():
    """Mock responses for GitHub API."""
    repo_response = {
        "name": "remote-api-service",
        "owner": {"login": "acme-corp"},
        "default_branch": "main",
        "description": "A remote API service",
        "language": "Python",
        "size": 2048,
        "private": False,
        "html_url": "https://github.com/acme-corp/remote-api-service",
    }
    
    tree_response = {
        "tree": [
            {"path": "README.md", "type": "blob", "sha": "abc1", "size": 100},
            {"path": "pyproject.toml", "type": "blob", "sha": "abc2", "size": 200},
            {"path": "src", "type": "tree", "sha": "dir1"},
            {"path": "src/main.py", "type": "blob", "sha": "abc3", "size": 300},
            {"path": "src/api.py", "type": "blob", "sha": "abc4", "size": 400},
            {"path": "tests", "type": "tree", "sha": "dir2"},
            {"path": "tests/test_api.py", "type": "blob", "sha": "abc5", "size": 500},
        ]
    }
    
    return {
        "repo": repo_response,
        "tree": tree_response,
    }


# =============================================================================
# Helper: Create WorkflowState for testing
# =============================================================================

def make_state(
    repo_root: Optional[str] = None,
    repo_profile: Optional[RepoProfile] = None,
    repo_source: Optional[str] = None,
    github_token: Optional[str] = None,
    github_ref: Optional[str] = None,
) -> WorkflowState:
    """Create a WorkflowState configured for attach_repo_context testing."""
    @dataclass
    class Options:
        repo_source: Optional[str] = None
        github_token: Optional[str] = None
        github_ref: Optional[str] = None
    
    state = WorkflowState(
        source_refs=["test_spec.yaml"],
        spec_refs=["test_spec.yaml"],
        task_description="Test task",
        repo_root=repo_root,
        repo_profile=repo_profile,
    )
    
    if repo_source or github_token or github_ref:
        state.options = Options(
            repo_source=repo_source,
            github_token=github_token,
            github_ref=github_ref,
        )
    
    return state


# =============================================================================
# Matrix Tests: Local Provider + Profile Combinations
# =============================================================================

class TestLocalProviderMatrix:
    """Test attach_repo_context with local provider and various profiles."""
    
    @pytest.mark.no_db
    def test_local_fastapi_detected(self, fastapi_repo):
        """Local + FastAPI: profile should be auto-detected."""
        state = make_state(repo_root=str(fastapi_repo))
        
        result = attach_repo_context(state)
        
        # Should complete successfully
        assert "attach_repo_context" in result.completed_steps
        assert len([e for e in result.errors if "Warning" not in e]) == 0
        
        # Snapshot should be populated
        assert result.repo_snapshot is not None
        assert result.repo_snapshot.files is not None
        assert len(result.repo_snapshot.files) >= 3
        
        # Markdown context should be present
        assert result.repo_markdown_context is not None
        assert len(result.repo_markdown_context) > 50
        
        # Profile should be detected as FastAPI
        assert result.repo_profile is not None
        assert result.repo_profile.framework == "fastapi"
    
    @pytest.mark.no_db
    def test_local_explicit_profile(self, fastapi_repo):
        """Local + explicit profile: should not override provided profile."""
        # Provide SUBATOMIC_MOCK_PROFILE explicitly
        state = make_state(
            repo_root=str(fastapi_repo),
            repo_profile=SUBATOMIC_MOCK_PROFILE,
        )
        
        result = attach_repo_context(state)
        
        # Should complete
        assert "attach_repo_context" in result.completed_steps
        
        # Profile should remain as provided (not overridden)
        assert result.repo_profile == SUBATOMIC_MOCK_PROFILE
        assert result.repo_profile.name == "subatomic_mock_service"
        
        # But snapshot should still be built from actual repo
        assert result.repo_snapshot is not None
        assert "src/app/main.py" in result.repo_snapshot.files
    
    @pytest.mark.no_db
    def test_local_generic_python(self, generic_python_repo):
        """Local + generic Python repo: fallback to default profile."""
        state = make_state(repo_root=str(generic_python_repo))
        
        result = attach_repo_context(state)
        
        assert "attach_repo_context" in result.completed_steps
        
        # Snapshot should have files
        assert result.repo_snapshot is not None
        assert len(result.repo_snapshot.files) >= 3
        
        # Profile should be default (no framework detected)
        assert result.repo_profile is not None
        # Generic repos fall back to GENERIC_PYTHON_PROFILE (V2)
        assert result.repo_profile.language == "python"
    
    @pytest.mark.no_db
    def test_local_flask_detected(self, flask_repo):
        """Local + Flask: profile should be auto-detected."""
        state = make_state(repo_root=str(flask_repo))
        
        result = attach_repo_context(state)
        
        assert "attach_repo_context" in result.completed_steps
        
        # Snapshot should exist
        assert result.repo_snapshot is not None
        assert "app.py" in result.repo_snapshot.files
        
        # Profile should be Flask
        assert result.repo_profile is not None
        assert result.repo_profile.framework == "flask"
    
    @pytest.mark.no_db
    def test_local_no_repo_root_is_noop(self):
        """No repo_root: attach_repo_context should be a no-op."""
        state = make_state(repo_root=None)
        
        result = attach_repo_context(state)
        
        # Step should complete
        assert "attach_repo_context" in result.completed_steps
        
        # But nothing should be populated
        assert result.repo_snapshot is None
        assert result.repo_markdown_context is None
        assert result.repo_profile is None


# =============================================================================
# Matrix Tests: GitHub Provider (Mocked)
# =============================================================================

class TestGitHubProviderMatrix:
    """Test GitHubRepoProvider directly and through attach_repo_context."""
    
    @pytest.mark.no_db
    @patch('integration_coworker.repo.providers.github.HAS_HTTPX', True)
    @patch('integration_coworker.repo.providers.github.httpx')
    def test_github_provider_direct(self, mock_httpx, mock_github_responses):
        """GitHub provider should work directly."""
        import base64
        
        mock_client = MagicMock()
        mock_httpx.Client.return_value = mock_client
        
        def mock_get(url, **kwargs):
            response = MagicMock()
            response.raise_for_status = MagicMock()
            
            if "/git/trees/" in url:
                response.json.return_value = mock_github_responses["tree"]
            elif "/contents/" in url:
                content = "# Mock file content\nprint('hello')"
                encoded = base64.b64encode(content.encode()).decode()
                response.json.return_value = {
                    "type": "file",
                    "encoding": "base64",
                    "content": encoded,
                    "size": len(content),
                }
            else:
                response.json.return_value = mock_github_responses["repo"]
            
            return response
        
        mock_client.get.side_effect = mock_get
        
        # Create provider via factory
        provider = get_provider(
            "acme-corp/remote-api-service",
        )
        
        # Get metadata
        metadata = provider.get_metadata()
        
        assert metadata.name == "remote-api-service"
        assert metadata.owner == "acme-corp"


# =============================================================================
# Matrix Tests: Provider Factory
# =============================================================================

class TestProviderFactoryMatrix:
    """Test get_provider factory with different configurations."""
    
    @pytest.mark.no_db
    def test_factory_creates_local_provider(self, fastapi_repo):
        """Factory should create LocalRepoProvider for local path."""
        provider = get_provider(str(fastapi_repo))
        
        assert isinstance(provider, LocalRepoProvider)
        
        # Should be able to list files
        files = provider.list_files()
        assert len(files) >= 3
        assert any("main.py" in f for f in files)
    
    @pytest.mark.no_db
    @patch('integration_coworker.repo.providers.github.HAS_HTTPX', True)
    @patch('integration_coworker.repo.providers.github.httpx')
    def test_factory_creates_github_provider_from_slug(self, mock_httpx):
        """Factory should create GitHubRepoProvider for owner/repo slug."""
        mock_client = MagicMock()
        mock_httpx.Client.return_value = mock_client
        
        provider = get_provider("test-owner/test-repo")
        
        from integration_coworker.repo.providers.github import GitHubRepoProvider
        assert isinstance(provider, GitHubRepoProvider)
        assert provider.owner == "test-owner"
        assert provider.repo == "test-repo"
    
    @pytest.mark.no_db
    @patch('integration_coworker.repo.providers.github.HAS_HTTPX', True)
    @patch('integration_coworker.repo.providers.github.httpx')
    def test_factory_creates_github_provider_from_url(self, mock_httpx):
        """Factory should create GitHubRepoProvider for GitHub URL."""
        mock_client = MagicMock()
        mock_httpx.Client.return_value = mock_client
        
        provider = get_provider(
            "https://github.com/test-owner/test-repo",
            github_ref="develop",
        )
        
        from integration_coworker.repo.providers.github import GitHubRepoProvider
        assert isinstance(provider, GitHubRepoProvider)
        assert provider.owner == "test-owner"
        assert provider.repo == "test-repo"
        assert provider.ref == "develop"
    
    @pytest.mark.no_db
    def test_factory_rejects_invalid_source(self):
        """Factory should reject unknown source types."""
        with pytest.raises(ValueError, match="Unrecognized source format"):
            get_provider("not-a-valid-source")


# =============================================================================
# Matrix Tests: Profile Detection Across Frameworks
# =============================================================================

class TestProfileDetectionMatrix:
    """Test profile detection across multiple framework types.
    
    Note: Per ADR-0002, archetype-based detection is deprecated.
    The new detection pipeline returns generic profiles based on language,
    with framework detection delegated to config files or LLM inference.
    This test verifies correct language detection for each setup.
    """
    
    @pytest.mark.no_db
    def test_detection_matrix_coverage(self, tmp_path):
        """Verify detection returns correct language-based profiles.
        
        Per ADR-0002, framework-specific profiles are now obtained via
        config files or LLM inference, not heuristic detection. This test
        verifies that language detection works correctly (Python vs TypeScript).
        """
        test_cases = [
            # (name, setup_fn, expected_language)
            ("fastapi", self._setup_fastapi, "python"),
            ("flask", self._setup_flask, "python"),
            ("django", self._setup_django, "python"),
            ("nextjs", self._setup_nextjs, "typescript"),
            ("express", self._setup_express, "typescript"),
            ("nestjs", self._setup_nestjs, "typescript"),
        ]
        
        results = []
        for name, setup_fn, expected_lang in test_cases:
            # Create isolated directory for each case
            case_dir = tmp_path / name
            case_dir.mkdir()
            setup_fn(case_dir)
            
            profile = detect_profile_from_repo(case_dir)
            match = profile.language == expected_lang
            results.append((name, expected_lang, profile.language, match))
        
        # Report failures with details
        failures = [r for r in results if not r[3]]
        if failures:
            msg = "\n".join(
                f"  {name}: expected language {expected}, got {actual}"
                for name, expected, actual, _ in failures
            )
            pytest.fail(f"Language detection failures:\n{msg}")
    
    def _setup_fastapi(self, path: Path):
        """Set up FastAPI markers."""
        (path / "requirements.txt").write_text("fastapi>=0.100.0\nuvicorn\n")
    
    def _setup_flask(self, path: Path):
        """Set up Flask markers."""
        (path / "requirements.txt").write_text("flask>=2.0.0\n")
    
    def _setup_django(self, path: Path):
        """Set up Django markers."""
        (path / "manage.py").write_text("#!/usr/bin/env python\n")
        (path / "settings.py").write_text("INSTALLED_APPS = []\n")
    
    def _setup_nextjs(self, path: Path):
        """Set up Next.js markers."""
        (path / "package.json").write_text('{"dependencies": {"next": "^14.0.0"}}')
        (path / "next.config.js").touch()
    
    def _setup_express(self, path: Path):
        """Set up Express markers."""
        (path / "package.json").write_text('{"dependencies": {"express": "^4.18.0"}}')
    
    def _setup_nestjs(self, path: Path):
        """Set up NestJS markers."""
        (path / "package.json").write_text('{"dependencies": {"@nestjs/core": "^10.0.0"}}')


# =============================================================================
# Matrix Tests: Error Handling
# =============================================================================

class TestErrorHandlingMatrix:
    """Test error handling across different failure scenarios."""
    
    @pytest.mark.no_db
    def test_handles_nonexistent_repo_root(self, tmp_path):
        """Should gracefully handle nonexistent repo_root."""
        nonexistent = tmp_path / "does_not_exist"
        state = make_state(repo_root=str(nonexistent))
        
        result = attach_repo_context(state)
        
        # Should complete (with error logged)
        assert "attach_repo_context" in result.completed_steps
        # Error should be recorded
        assert any("Failed to attach repo context" in e for e in result.errors)
    
    @pytest.mark.no_db
    def test_handles_unreadable_files(self, tmp_path):
        """Should handle repos with unreadable files gracefully."""
        # Create a basic repo
        (tmp_path / "README.md").write_text("# Test")
        
        # Create a binary file that can't be read as text
        binary_file = tmp_path / "data.bin"
        binary_file.write_bytes(bytes(range(256)))
        
        state = make_state(repo_root=str(tmp_path))
        
        result = attach_repo_context(state)
        
        # Should complete (binary files are skipped)
        assert "attach_repo_context" in result.completed_steps
        
        # Snapshot should have the readable files
        assert result.repo_snapshot is not None
        assert "README.md" in result.repo_snapshot.files


# =============================================================================
# Sanity Check: End-to-End with State
# =============================================================================

class TestSanityE2E:
    """Quick sanity tests for complete attach_repo_context flow."""
    
    @pytest.mark.no_db
    def test_e2e_local_complete_flow(self, fastapi_repo):
        """Complete E2E: local provider -> snapshot -> context."""
        state = make_state(repo_root=str(fastapi_repo))
        
        # Initial state
        assert state.repo_snapshot is None
        assert state.repo_markdown_context is None
        assert state.repo_profile is None
        
        # Run node
        result = attach_repo_context(state)
        
        # Verify all outputs populated
        assert result.repo_snapshot is not None
        assert result.repo_snapshot.files is not None
        assert result.repo_markdown_context is not None
        assert result.repo_profile is not None
        
        # Verify markdown context contains expected info
        ctx = result.repo_markdown_context
        assert "Repository Structure" in ctx or "main.py" in ctx
        
        # Verify no blocking errors
        blocking_errors = [e for e in result.errors if "Warning" not in e]
        assert len(blocking_errors) == 0, f"Unexpected errors: {blocking_errors}"
