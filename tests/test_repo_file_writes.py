"""
Tests for P2: Repo File Writes

Tests the apply_repo_integration_changes node including:
- Dry-run mode (no files written)
- Creating new files
- Updating existing files
- Backup functionality
- Integration hooks
"""
import pytest
import tempfile
from pathlib import Path
from typing import Optional

from integration_coworker.graph.nodes.apply_repo_integration_changes import (
    apply_repo_integration_changes,
    _backup_file,
    _merge_imports,
    _find_integration_hook,
    _apply_code_artifact,
)
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import CodeArtifact
from integration_coworker.api.types import IntegrationOptions
from integration_coworker.repo.models import FileChange, RepoChangeSet


# Mark all tests in this module as not needing database
pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_repo():
    """Create a temporary directory to act as a test repository."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        # Create basic structure
        (repo_path / "src").mkdir()
        (repo_path / "tests").mkdir()
        yield repo_path


@pytest.fixture
def basic_state(temp_repo) -> WorkflowState:
    """Create a basic WorkflowState for testing."""
    state = WorkflowState(
        source_refs=["test.yaml"],
        spec_refs=["test.yaml"],
        task_description="Test integration",
    )
    state.repo_root = str(temp_repo)
    state.options = IntegrationOptions(dry_run=False)
    return state


# ---------------------------------------------------------------------------
# Test: _backup_file
# ---------------------------------------------------------------------------

class TestBackupFile:
    """Tests for file backup functionality."""
    
    def test_backup_creates_backup_file(self, temp_repo):
        """Should create a backup of existing file."""
        # Create a file to backup
        target = temp_repo / "test.py"
        target.write_text("original content", encoding="utf-8")
        backup_dir = temp_repo / ".backups"
        
        backup_path = _backup_file(target, backup_dir)
        
        assert backup_path is not None
        assert backup_path.exists()
        assert backup_path.read_text() == "original content"
        assert ".bak" in backup_path.name
    
    def test_backup_nonexistent_returns_none(self, temp_repo):
        """Should return None for non-existent file."""
        target = temp_repo / "nonexistent.py"
        backup_dir = temp_repo / ".backups"
        
        backup_path = _backup_file(target, backup_dir)
        
        assert backup_path is None
    
    def test_backup_creates_backup_dir(self, temp_repo):
        """Should create backup directory if it doesn't exist."""
        target = temp_repo / "test.py"
        target.write_text("content", encoding="utf-8")
        backup_dir = temp_repo / "nested" / "backup" / "dir"
        
        _backup_file(target, backup_dir)
        
        assert backup_dir.exists()


# ---------------------------------------------------------------------------
# Test: _merge_imports
# ---------------------------------------------------------------------------

class TestMergeImports:
    """Tests for import merging functionality."""
    
    def test_adds_new_imports(self):
        """Should add new imports after existing imports."""
        existing = '''import os
import sys

class MyClass:
    pass
'''
        new_imports = ["from typing import List"]
        
        result = _merge_imports(existing, new_imports)
        
        assert "from typing import List" in result
        assert "# Generated integration imports" in result
    
    def test_no_duplicate_imports(self):
        """Should not add duplicate imports."""
        existing = '''import os
import sys

class MyClass:
    pass
'''
        new_imports = ["import os", "from typing import List"]
        
        result = _merge_imports(existing, new_imports)
        
        # Original 'import os' should exist once, new one should not be added
        assert result.count("import os") == 1
        assert "from typing import List" in result
    
    def test_handles_empty_imports_list(self):
        """Should handle empty new imports list."""
        existing = '''import os

class MyClass:
    pass
'''
        result = _merge_imports(existing, [])
        
        assert result == existing


# ---------------------------------------------------------------------------
# Test: _find_integration_hook
# ---------------------------------------------------------------------------

class TestFindIntegrationHook:
    """Tests for finding integration hook markers."""
    
    def test_finds_hook_marker(self):
        """Should find hook marker line."""
        content = '''# File header
import os

# @integration_hook: imports

class Router:
    pass
'''
        line_num = _find_integration_hook(content, "imports")
        
        assert line_num == 3  # 0-indexed line number
    
    def test_returns_none_when_not_found(self):
        """Should return None when hook not found."""
        content = '''import os

class Router:
    pass
'''
        line_num = _find_integration_hook(content, "nonexistent")
        
        assert line_num is None


# ---------------------------------------------------------------------------
# Test: _apply_code_artifact
# ---------------------------------------------------------------------------

class TestApplyCodeArtifact:
    """Tests for applying code artifacts."""
    
    def test_creates_new_file(self, temp_repo):
        """Should create new file from artifact."""
        artifact = CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="client",
            language="python",
            module_name="stripe_client",
            rel_path="src/clients/stripe_client.py",
            content="class StripeClient: pass",
        )
        backup_dir = temp_repo / ".backups"
        
        result = _apply_code_artifact(artifact, temp_repo, backup_dir, dry_run=False)
        
        assert result["action"] == "created"
        target_file = temp_repo / "src/clients/stripe_client.py"
        assert target_file.exists()
        assert target_file.read_text() == "class StripeClient: pass"
    
    def test_updates_existing_file(self, temp_repo):
        """Should update existing file and create backup."""
        # Create existing file
        existing_path = temp_repo / "src/existing.py"
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("old content", encoding="utf-8")
        
        artifact = CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="client",
            language="python",
            module_name="existing",
            rel_path="src/existing.py",
            content="new content",
        )
        backup_dir = temp_repo / ".backups"
        
        result = _apply_code_artifact(artifact, temp_repo, backup_dir, dry_run=False)
        
        assert result["action"] == "updated"
        assert result["backed_up"] is True
        assert existing_path.read_text() == "new content"
    
    def test_dry_run_does_not_write(self, temp_repo):
        """Dry run should not create files."""
        artifact = CodeArtifact(
            id=None,
            task_id=None,
            artifact_type="client",
            language="python",
            module_name="test",
            rel_path="src/test.py",
            content="content",
        )
        backup_dir = temp_repo / ".backups"
        
        result = _apply_code_artifact(artifact, temp_repo, backup_dir, dry_run=True)
        
        target_file = temp_repo / "src/test.py"
        assert not target_file.exists()
        assert result["action"] == "created"


# ---------------------------------------------------------------------------
# Test: apply_repo_integration_changes
# ---------------------------------------------------------------------------

class TestApplyRepoIntegrationChanges:
    """Integration tests for the full node."""
    
    def test_applies_code_artifacts(self, basic_state, temp_repo):
        """Should apply all code artifacts to repo."""
        basic_state.code_artifacts = [
            CodeArtifact(
                id=None,
                task_id=None,
                artifact_type="client",
                language="python",
                module_name="stripe_client",
                rel_path="src/clients/stripe_client.py",
                content="class StripeClient: pass",
            ),
            CodeArtifact(
                id=None,
                task_id=None,
                artifact_type="flow",
                language="python",
                module_name="create_session",
                rel_path="src/flows/create_session.py",
                content="def create_session(): pass",
            ),
        ]
        
        result = apply_repo_integration_changes(basic_state)
        
        assert "apply_repo_integration_changes" in result.completed_steps
        assert len(result.plan["applied_changes"]) >= 2
        
        client_file = temp_repo / "src/clients/stripe_client.py"
        flow_file = temp_repo / "src/flows/create_session.py"
        assert client_file.exists()
        assert flow_file.exists()
    
    def test_dry_run_mode(self, basic_state, temp_repo):
        """Dry run should not write files."""
        basic_state.options = IntegrationOptions(dry_run=True)
        basic_state.code_artifacts = [
            CodeArtifact(
                id=None,
                task_id=None,
                artifact_type="client",
                language="python",
                module_name="test",
                rel_path="src/test.py",
                content="content",
            ),
        ]
        
        result = apply_repo_integration_changes(basic_state)
        
        assert "dry_run_summary" in result.plan
        assert "DRY RUN" in result.plan["dry_run_summary"]
        target_file = temp_repo / "src/test.py"
        assert not target_file.exists()
    
    def test_creates_init_files(self, basic_state, temp_repo):
        """Should create __init__.py files for new directories."""
        basic_state.code_artifacts = [
            CodeArtifact(
                id=None,
                task_id=None,
                artifact_type="client",
                language="python",
                module_name="new_client",
                rel_path="src/new_package/clients/new_client.py",
                content="content",
            ),
        ]
        
        result = apply_repo_integration_changes(basic_state)
        
        init_file = temp_repo / "src/new_package/clients/__init__.py"
        assert init_file.exists()
    
    def test_no_repo_root_skips_gracefully(self):
        """Should skip when no repo_root is set."""
        state = WorkflowState(
            source_refs=["test.yaml"],
            spec_refs=["test.yaml"],
            task_description="Test",
        )
        state.repo_root = None
        
        result = apply_repo_integration_changes(state)
        
        assert "apply_repo_integration_changes" in result.completed_steps
    
    def test_applies_repo_changes(self, basic_state, temp_repo):
        """Should apply RepoChangeSet changes."""
        basic_state.repo_changes = RepoChangeSet(
            repo_root=temp_repo,
            changes=[
                FileChange(
                    rel_path="config/settings.yaml",
                    change_type="create",
                    content="api_key: test",
                    after="api_key: test",
                ),
            ],
        )
        
        result = apply_repo_integration_changes(basic_state)
        
        config_file = temp_repo / "config/settings.yaml"
        assert config_file.exists()
        assert "api_key: test" in config_file.read_text()


# ---------------------------------------------------------------------------
# Test: Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_handles_empty_artifacts_list(self, basic_state):
        """Should handle empty artifacts list gracefully."""
        basic_state.code_artifacts = []
        
        result = apply_repo_integration_changes(basic_state)
        
        assert "apply_repo_integration_changes" in result.completed_steps
        assert result.plan.get("applied_changes", []) == []
    
    def test_handles_none_options(self, temp_repo):
        """Should handle None options (defaults to dry_run=False behavior)."""
        state = WorkflowState(
            source_refs=["test.yaml"],
            spec_refs=["test.yaml"],
            task_description="Test",
        )
        state.repo_root = str(temp_repo)
        state.options = None
        state.code_artifacts = []
        
        result = apply_repo_integration_changes(state)
        
        assert "apply_repo_integration_changes" in result.completed_steps
