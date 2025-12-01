"""
Repo Provider Sanity Matrix Tests.

This test module exercises attach_repo_context under multiple combinations:
1. Filesystem provider + FastAPI profile
2. Filesystem provider + Generic profile  
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
    RepoSourceConfig,
    FilesystemProvider,
    GitHubRepoProvider,
    get_repo_provider,
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
            {"path": "README.md", "type": "blob", "sha": "abc1"},
            {"path": "pyproject.toml", "type": "blob", "sha": "abc2"},
            {"path": "src", "type": "tree", "sha": "dir1"},
            {"path": "src/main.py", "type": "blob", "sha": "abc3"},
            {"path": "src/api.py", "type": "blob", "sha": "abc4"},
            {"path": "tests", "type": "tree", "sha": "dir2"},
            {"path": "tests/test_api.py", "type": "blob", "sha": "abc5"},
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
    repo_source: Optional[RepoSourceConfig] = None,
) -> WorkflowState:
    """Create a WorkflowState configured for attach_repo_context testing."""
    @dataclass
    class Options:
        repo_source: Optional[RepoSourceConfig] = None
    
    state = WorkflowState(
        source_refs=["test_spec.yaml"],
        spec_refs=["test_spec.yaml"],
        task_description="Test task",
        repo_root=repo_root,
        repo_profile=repo_profile,
    )
    
    if repo_source:
        state.options = Options(repo_source=repo_source)
    
    return state


# =============================================================================
# Matrix Tests: Filesystem Provider + Profile Combinations
# =============================================================================

class TestFilesystemProviderMatrix:
    """Test attach_repo_context with filesystem provider and various profiles."""
    
    @pytest.mark.no_db
    def test_filesystem_fastapi_detected(self, fastapi_repo):
        """Filesystem + FastAPI: profile should be auto-detected."""
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
    def test_filesystem_explicit_profile(self, fastapi_repo):
        """Filesystem + explicit profile: should not override provided profile."""
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
    def test_filesystem_generic_python(self, generic_python_repo):
        """Filesystem + generic Python repo: fallback to default profile."""
        state = make_state(repo_root=str(generic_python_repo))
        
        result = attach_repo_context(state)
        
        assert "attach_repo_context" in result.completed_steps
        
        # Snapshot should have files
        assert result.repo_snapshot is not None
        assert len(result.repo_snapshot.files) >= 3
        
        # Profile should be default (no framework detected)
        assert result.repo_profile is not None
        # Generic repos fall back to SUBATOMIC_MOCK_PROFILE
        assert result.repo_profile.language == "python"
    
    @pytest.mark.no_db
    def test_filesystem_flask_detected(self, flask_repo):
        """Filesystem + Flask: profile should be auto-detected."""
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
    def test_filesystem_no_repo_root_is_noop(self):
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
    """Test GitHubRepoProvider directly (not through attach_repo_context).
    
    Note: The GitHub provider integration with attach_repo_context is not
    fully wired up yet - IntegrationOptions doesn't have a repo_source field.
    These tests verify the provider works correctly in isolation.
    """
    
    @pytest.mark.no_db
    @patch('urllib.request.urlopen')
    def test_github_provider_builds_snapshot(self, mock_urlopen, mock_github_responses):
        """GitHub provider should build snapshot from API."""
        import base64
        
        def mock_response_factory(*args, **kwargs):
            url = args[0].full_url if hasattr(args[0], 'full_url') else str(args[0])
            mock_response = MagicMock()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            
            if "/git/trees/" in url:
                mock_response.read.return_value = json.dumps(mock_github_responses["tree"]).encode()
            elif "/contents/" in url:
                # Return mock file content
                content = "# Mock file content\nprint('hello')"
                encoded = base64.b64encode(content.encode()).decode()
                mock_response.read.return_value = json.dumps({
                    "type": "file",
                    "encoding": "base64",
                    "content": encoded,
                }).encode()
            else:
                mock_response.read.return_value = json.dumps(mock_github_responses["repo"]).encode()
            
            return mock_response
        
        mock_urlopen.side_effect = mock_response_factory
        
        # Create provider directly
        provider = GitHubRepoProvider(
            owner="acme-corp",
            repo="remote-api-service",
            ref="main",
        )
        
        # Build snapshot directly (bypassing attach_repo_context)
        snapshot = provider.build_snapshot()
        
        # Verify snapshot is built
        assert snapshot is not None
        assert snapshot.repo_name == "remote-api-service"
        assert snapshot.owner == "acme-corp"
        
        # Verify files are present
        assert snapshot.files is not None
        assert len(snapshot.files) > 0
        
        # Verify markdown context exists
        assert snapshot.full_markdown is not None
        assert len(snapshot.full_markdown) > 0
    
    @pytest.mark.no_db
    @patch('urllib.request.urlopen')
    def test_github_provider_metadata(self, mock_urlopen, mock_github_responses):
        """GitHub provider should return correct metadata."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(mock_github_responses["repo"]).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response
        
        provider = GitHubRepoProvider(
            owner="acme-corp",
            repo="remote-api-service",
            ref="main",
        )
        
        metadata = provider.get_repo_metadata()
        
        assert metadata.name == "remote-api-service"
        assert metadata.owner == "acme-corp"
        assert metadata.language == "Python"
        assert metadata.default_branch == "main"


# =============================================================================
# Matrix Tests: Provider Factory
# =============================================================================

class TestProviderFactoryMatrix:
    """Test get_repo_provider factory with different configurations."""
    
    @pytest.mark.no_db
    def test_factory_creates_filesystem_provider(self, fastapi_repo):
        """Factory should create FilesystemProvider for filesystem source."""
        config = RepoSourceConfig(
            source_type="filesystem",
            path=str(fastapi_repo),
        )
        
        provider = get_repo_provider(config)
        
        assert isinstance(provider, FilesystemProvider)
        
        # Should be able to list files
        files = provider.list_files()
        assert len(files) >= 3
        assert any("main.py" in f for f in files)
    
    @pytest.mark.no_db
    def test_factory_creates_github_provider(self):
        """Factory should create GitHubRepoProvider for github source."""
        config = RepoSourceConfig(
            source_type="github",
            owner="test-owner",
            repo="test-repo",
            ref="develop",
        )
        
        provider = get_repo_provider(config)
        
        assert isinstance(provider, GitHubRepoProvider)
        assert provider.owner == "test-owner"
        assert provider.repo == "test-repo"
        assert provider.ref == "develop"
    
    @pytest.mark.no_db
    def test_factory_rejects_unknown_source_type(self):
        """Factory should reject unknown source types."""
        config = RepoSourceConfig(source_type="gitlab")
        
        with pytest.raises(ValueError, match="Unknown source type"):
            get_repo_provider(config)


# =============================================================================
# Matrix Tests: Profile Detection Across Frameworks
# =============================================================================

class TestProfileDetectionMatrix:
    """Test profile detection across multiple framework types."""
    
    @pytest.mark.no_db
    def test_detection_matrix_coverage(self, tmp_path):
        """Verify detection works for each major framework."""
        test_cases = [
            # (name, setup_fn, expected_framework)
            ("fastapi", self._setup_fastapi, "fastapi"),
            ("flask", self._setup_flask, "flask"),
            ("django", self._setup_django, "django"),
            ("nextjs", self._setup_nextjs, "nextjs"),
            ("express", self._setup_express, "express"),
            ("nestjs", self._setup_nestjs, "nestjs"),
        ]
        
        results = []
        for name, setup_fn, expected in test_cases:
            # Create isolated directory for each case
            case_dir = tmp_path / name
            case_dir.mkdir()
            setup_fn(case_dir)
            
            profile = detect_profile_from_repo(case_dir)
            match = profile.framework == expected
            results.append((name, expected, profile.framework, match))
        
        # Report failures with details
        failures = [r for r in results if not r[3]]
        if failures:
            msg = "\n".join(
                f"  {name}: expected {expected}, got {actual}"
                for name, expected, actual, _ in failures
            )
            pytest.fail(f"Profile detection failures:\n{msg}")
    
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
    def test_e2e_filesystem_complete_flow(self, fastapi_repo):
        """Complete E2E: filesystem provider -> snapshot -> context."""
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
