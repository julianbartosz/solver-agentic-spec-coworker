"""
Tests for git_ops module.

Uses a real temporary git repository to test all git operations.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from integration_coworker.harness.git_ops import (
    DirtyRepoError,
    GitError,
    checkout,
    commit_all,
    create_and_checkout_branch,
    current_branch,
    diff_stat,
    diff_stat_working_tree,
    diff_name_only_working_tree,
    ensure_clean_working_tree,
    generate_demo_branch_name,
    is_git_repo,
    status_porcelain,
    stash_all,
    safe_checkout,
)


# -----------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------
@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """
    Create a temporary git repository with an initial commit.
    
    Returns the path to the repo root.
    """
    repo = tmp_path / "test_repo"
    repo.mkdir()
    
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    
    # Configure git user (required for commits)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    
    # Create initial file and commit
    (repo / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    
    return repo


@pytest.fixture
def dirty_git_repo(temp_git_repo: Path) -> Path:
    """Create a temp git repo with uncommitted changes."""
    (temp_git_repo / "dirty.txt").write_text("uncommitted changes\n")
    return temp_git_repo


# -----------------------------------------------------------------
# Tests: is_git_repo
# -----------------------------------------------------------------
class TestIsGitRepo:
    def test_is_git_repo_true(self, temp_git_repo: Path) -> None:
        """Test that is_git_repo returns True for a git repo."""
        assert is_git_repo(temp_git_repo) is True

    def test_is_git_repo_false(self, tmp_path: Path) -> None:
        """Test that is_git_repo returns False for non-git directory."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        assert is_git_repo(non_repo) is False

    def test_is_git_repo_nonexistent(self, tmp_path: Path) -> None:
        """Test that is_git_repo returns False for nonexistent path."""
        assert is_git_repo(tmp_path / "does_not_exist") is False


# -----------------------------------------------------------------
# Tests: current_branch
# -----------------------------------------------------------------
class TestCurrentBranch:
    def test_current_branch_returns_main_or_master(self, temp_git_repo: Path) -> None:
        """Test that current_branch returns the default branch name."""
        branch = current_branch(temp_git_repo)
        # Could be main or master depending on git config
        assert branch in ("main", "master")

    def test_current_branch_after_checkout(self, temp_git_repo: Path) -> None:
        """Test that current_branch returns the new branch after checkout."""
        subprocess.run(
            ["git", "checkout", "-b", "feature-test"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )
        assert current_branch(temp_git_repo) == "feature-test"

    def test_current_branch_not_a_repo(self, tmp_path: Path) -> None:
        """Test that current_branch raises GitError for non-repo."""
        with pytest.raises(GitError):
            current_branch(tmp_path)


# -----------------------------------------------------------------
# Tests: ensure_clean_working_tree
# -----------------------------------------------------------------
class TestEnsureCleanWorkingTree:
    def test_clean_repo_passes(self, temp_git_repo: Path) -> None:
        """Test that clean repo passes validation."""
        # Should not raise
        ensure_clean_working_tree(temp_git_repo)

    def test_dirty_repo_raises(self, dirty_git_repo: Path) -> None:
        """Test that dirty repo raises DirtyRepoError."""
        with pytest.raises(DirtyRepoError) as exc_info:
            ensure_clean_working_tree(dirty_git_repo)
        assert "dirty.txt" in str(exc_info.value)


# -----------------------------------------------------------------
# Tests: create_and_checkout_branch
# -----------------------------------------------------------------
class TestCreateAndCheckoutBranch:
    def test_creates_new_branch(self, temp_git_repo: Path) -> None:
        """Test that create_and_checkout_branch creates and checks out new branch."""
        create_and_checkout_branch(temp_git_repo, "new-feature")
        assert current_branch(temp_git_repo) == "new-feature"

    def test_branch_already_exists_raises(self, temp_git_repo: Path) -> None:
        """Test that creating existing branch raises GitError."""
        # Get the original branch before creating new one
        original_branch = current_branch(temp_git_repo)
        
        # Create a branch
        create_and_checkout_branch(temp_git_repo, "existing")
        
        # Go back to original branch
        checkout(temp_git_repo, original_branch)
        
        # Try to create the same branch again - should fail
        with pytest.raises(GitError):
            create_and_checkout_branch(temp_git_repo, "existing")


# -----------------------------------------------------------------
# Tests: checkout
# -----------------------------------------------------------------
class TestCheckout:
    def test_checkout_existing_branch(self, temp_git_repo: Path) -> None:
        """Test checkout of existing branch."""
        original = current_branch(temp_git_repo)
        create_and_checkout_branch(temp_git_repo, "feature")
        assert current_branch(temp_git_repo) == "feature"
        
        checkout(temp_git_repo, original)
        assert current_branch(temp_git_repo) == original

    def test_checkout_nonexistent_raises(self, temp_git_repo: Path) -> None:
        """Test that checking out nonexistent branch raises GitError."""
        with pytest.raises(GitError):
            checkout(temp_git_repo, "does-not-exist")


# -----------------------------------------------------------------
# Tests: diff_stat
# -----------------------------------------------------------------
class TestDiffStat:
    def test_diff_stat_no_changes(self, temp_git_repo: Path) -> None:
        """Test diff_stat with no changes."""
        original = current_branch(temp_git_repo)
        create_and_checkout_branch(temp_git_repo, "no-changes")
        
        stat = diff_stat(temp_git_repo, original, "no-changes")
        assert stat == ""  # No changes

    def test_diff_stat_with_changes(self, temp_git_repo: Path) -> None:
        """Test diff_stat with changes."""
        original = current_branch(temp_git_repo)
        create_and_checkout_branch(temp_git_repo, "with-changes")
        
        # Make a change and commit
        (temp_git_repo / "new_file.py").write_text("print('hello')\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add new file"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )
        
        stat = diff_stat(temp_git_repo, original, "with-changes")
        assert "new_file.py" in stat
        assert "1 file changed" in stat


# -----------------------------------------------------------------
# Tests: status_porcelain
# -----------------------------------------------------------------
class TestStatusPorcelain:
    def test_status_clean(self, temp_git_repo: Path) -> None:
        """Test status on clean repo."""
        status = status_porcelain(temp_git_repo)
        assert status == ""

    def test_status_with_untracked(self, temp_git_repo: Path) -> None:
        """Test status with untracked file."""
        (temp_git_repo / "untracked.txt").write_text("content\n")
        status = status_porcelain(temp_git_repo)
        assert "?? untracked.txt" in status

    def test_status_with_modified(self, temp_git_repo: Path) -> None:
        """Test status with modified file."""
        (temp_git_repo / "README.md").write_text("# Modified\n")
        status = status_porcelain(temp_git_repo)
        assert "M" in status or " M" in status


# -----------------------------------------------------------------
# Tests: commit_all
# -----------------------------------------------------------------
class TestCommitAll:
    def test_commit_all_with_changes(self, temp_git_repo: Path) -> None:
        """Test commit_all with staged changes."""
        (temp_git_repo / "new.txt").write_text("new content\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True, capture_output=True)
        
        commit_hash = commit_all(temp_git_repo, "Test commit")
        assert commit_hash is not None
        assert len(commit_hash) == 40  # Full SHA

    def test_commit_all_empty_returns_none(self, temp_git_repo: Path) -> None:
        """Test commit_all with nothing to commit returns None."""
        result = commit_all(temp_git_repo, "Empty commit")
        assert result is None


# -----------------------------------------------------------------
# Tests: generate_demo_branch_name
# -----------------------------------------------------------------
class TestGenerateDemoBranchName:
    def test_format(self) -> None:
        """Test that demo branch name has correct format."""
        name = generate_demo_branch_name()
        assert name.startswith("demo-")
        # Format: demo-YYYYMMDD-HHMMSS
        parts = name.split("-")
        assert len(parts) == 3
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 6  # HHMMSS

    def test_uniqueness(self) -> None:
        """Test that consecutive calls may differ (at least in seconds)."""
        import time
        name1 = generate_demo_branch_name()
        time.sleep(0.01)  # Just to ensure different timestamp possible
        name2 = generate_demo_branch_name()
        # Names could be same if within same second, so just check format
        assert name1.startswith("demo-")
        assert name2.startswith("demo-")


# -----------------------------------------------------------------
# Integration Test: Full workflow
# -----------------------------------------------------------------
class TestFullWorkflow:
    def test_demo_branch_workflow(self, temp_git_repo: Path) -> None:
        """Test the full demo branch workflow."""
        # 1. Start on clean repo
        ensure_clean_working_tree(temp_git_repo)
        base_branch = current_branch(temp_git_repo)
        
        # 2. Create demo branch
        demo_branch = generate_demo_branch_name()
        create_and_checkout_branch(temp_git_repo, demo_branch)
        assert current_branch(temp_git_repo) == demo_branch
        
        # 3. Make changes
        (temp_git_repo / "generated.py").write_text("# Generated by pipeline\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True, capture_output=True)
        commit_hash = commit_all(temp_git_repo, "Pipeline changes")
        assert commit_hash is not None
        
        # 4. Get diff stat
        stat = diff_stat(temp_git_repo, base_branch, demo_branch)
        assert "generated.py" in stat
        
        # 5. Return to base branch
        checkout(temp_git_repo, base_branch)
        assert current_branch(temp_git_repo) == base_branch
        
        # 6. Verify demo branch still exists
        result = subprocess.run(
            ["git", "branch", "--list", demo_branch],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert demo_branch in result.stdout


# -----------------------------------------------------------------
# CRITICAL TESTS: Uncommitted writes (Phase 2 correctness)
# -----------------------------------------------------------------
class TestUncommittedWritesDiffProof:
    """
    These tests verify that diff proof works for UNCOMMITTED file writes.
    
    This is the critical bug that Phase 2 must handle correctly:
    - Pipeline writes files but does NOT commit them
    - `git diff base...HEAD` will show EMPTY (comparing commits)
    - `git diff --stat <base>` (commit vs working tree) shows the changes
    """

    def test_diff_stat_working_tree_shows_uncommitted_files(self, temp_git_repo: Path) -> None:
        """
        CRITICAL: diff_stat_working_tree must show uncommitted writes.
        
        This test would FAIL if we used `git diff base...HEAD` instead of
        `git diff --stat <base>`.
        """
        base_branch = current_branch(temp_git_repo)
        
        # Create demo branch
        create_and_checkout_branch(temp_git_repo, "demo-test")
        
        # Simulate pipeline: write files but DON'T commit
        (temp_git_repo / "generated_code.py").write_text("print('hello world')\n")
        (temp_git_repo / "test_generated.py").write_text("def test_it(): pass\n")
        
        # Using commit-comparison would show empty diff
        commit_diff = diff_stat(temp_git_repo, base_branch, "HEAD")
        assert commit_diff.strip() == "", "Commit diff should be empty (no commits made)"
        
        # Using working-tree comparison MUST show the uncommitted files
        working_diff = diff_stat_working_tree(temp_git_repo, base_branch)
        assert "generated_code.py" in working_diff, "Working tree diff must show uncommitted file"
        assert "test_generated.py" in working_diff, "Working tree diff must show uncommitted file"
        
        # File list should also work
        files = diff_name_only_working_tree(temp_git_repo, base_branch)
        assert "generated_code.py" in files
        assert "test_generated.py" in files

    def test_stash_enables_checkout_with_uncommitted_changes(self, temp_git_repo: Path) -> None:
        """
        CRITICAL: Must be able to return to base branch even with uncommitted changes.
        
        This tests the case where checkout would fail without stashing:
        - A tracked file is modified (not just new untracked files)
        """
        base_branch = current_branch(temp_git_repo)
        
        # Create demo branch
        create_and_checkout_branch(temp_git_repo, "demo-dirty")
        
        # Modify an existing tracked file (README.md from fixture)
        (temp_git_repo / "README.md").write_text("# Modified content\n")
        
        # Checkout should still work for modified files in newer git versions,
        # but our safe_checkout should stash anyway to be deterministic
        stashed = safe_checkout(temp_git_repo, base_branch, "test stash")
        
        # safe_checkout should have stashed the changes
        assert stashed is True, "Should have stashed changes"
        assert current_branch(temp_git_repo) == base_branch

    def test_safe_checkout_no_stash_needed_for_clean_tree(self, temp_git_repo: Path) -> None:
        """safe_checkout returns False when no stash was needed."""
        base_branch = current_branch(temp_git_repo)
        create_and_checkout_branch(temp_git_repo, "demo-clean")
        
        # No changes made, checkout should work without stashing
        stashed = safe_checkout(temp_git_repo, base_branch)
        assert stashed is False
        assert current_branch(temp_git_repo) == base_branch


class TestFullWorkflowWithUncommittedWrites:
    """
    End-to-end test simulating pipeline writing files without committing.
    This is the scenario that would silently fail with empty proof if using
    commit-to-commit diff instead of working-tree diff.
    """

    def test_e2e_uncommitted_writes_captured_in_proof(self, temp_git_repo: Path) -> None:
        """
        Full workflow test: pipeline writes files, we capture proof, cleanup succeeds.
        
        Simulates the real harness behavior with a pipeline that writes files
        but exits before committing them.
        """
        # 1. Setup: clean repo, get base branch
        ensure_clean_working_tree(temp_git_repo)
        base_branch = current_branch(temp_git_repo)
        
        # 2. Create demo branch (like _setup_repo_branch)
        demo_branch = generate_demo_branch_name()
        create_and_checkout_branch(temp_git_repo, demo_branch)
        assert current_branch(temp_git_repo) == demo_branch
        
        # 3. Simulate pipeline writing files WITHOUT COMMITTING
        # This is the realistic case - the subprocess child writes files,
        # but may not git add/commit them
        (temp_git_repo / "src").mkdir(exist_ok=True)
        (temp_git_repo / "src" / "generated_api.py").write_text(
            "class GeneratedAPI:\n    pass\n"
        )
        (temp_git_repo / "tests").mkdir(exist_ok=True)
        (temp_git_repo / "tests" / "test_api.py").write_text(
            "from src.generated_api import GeneratedAPI\n\ndef test_api():\n    pass\n"
        )
        
        # 4. Capture working-tree diff (BEFORE committing)
        working_diff = diff_stat_working_tree(temp_git_repo, base_branch)
        files_changed = diff_name_only_working_tree(temp_git_repo, base_branch)
        
        # CRITICAL ASSERTIONS: proof must not be empty
        assert working_diff.strip() != "", "Working tree diff must NOT be empty"
        assert "generated_api.py" in working_diff, "Diff must show generated file"
        assert "test_api.py" in working_diff, "Diff must show test file"
        assert "2 files changed" in working_diff
        
        # Files list must also capture them
        assert "src/generated_api.py" in files_changed
        assert "tests/test_api.py" in files_changed
        
        # 5. Optionally commit (like the harness does)
        commit_hash = commit_all(temp_git_repo, "Pipeline output")
        assert commit_hash is not None
        
        # 6. Now commit-based diff should also work
        commit_diff = diff_stat(temp_git_repo, base_branch, demo_branch)
        assert "generated_api.py" in commit_diff
        
        # 7. Return to base branch (safe even though we committed)
        safe_checkout(temp_git_repo, base_branch)
        assert current_branch(temp_git_repo) == base_branch

    def test_e2e_partial_failure_still_captures_proof(self, temp_git_repo: Path) -> None:
        """
        Even if pipeline fails partway, we should capture whatever was written.
        """
        base_branch = current_branch(temp_git_repo)
        demo_branch = generate_demo_branch_name()
        create_and_checkout_branch(temp_git_repo, demo_branch)
        
        # Pipeline writes one file then "crashes" (exits without commit)
        (temp_git_repo / "partial_output.py").write_text("# Partial output before crash\n")
        
        # Simulate teardown after failure - should still get proof
        working_diff = diff_stat_working_tree(temp_git_repo, base_branch)
        assert "partial_output.py" in working_diff, "Must capture partial output"
        
        # Cleanup should still work
        stashed = safe_checkout(temp_git_repo, base_branch, "partial run stash")
        assert current_branch(temp_git_repo) == base_branch
        # Changes were stashed, not committed
        assert stashed is True
