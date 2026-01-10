"""
Git operations for repo integration proof.

Provides pure functions + subprocess wrappers for git operations.
All operations are read-only or branch-local (no remote pushes).

Design constraints:
- Never mutate git config
- Never push to remotes
- Fail fast if repo is dirty (no auto-stash)
- All commands use subprocess.run with -C repo_root
"""
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GitError(Exception):
    """Raised when a git operation fails."""
    pass


class DirtyRepoError(GitError):
    """Raised when attempting to operate on a dirty repository."""
    pass


def _run_git(repo_root: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """
    Run a git command in the given repository.
    
    Args:
        repo_root: Path to repository root
        *args: Git command arguments (without 'git' prefix)
        check: Whether to raise on non-zero exit
        
    Returns:
        CompletedProcess with stdout/stderr
        
    Raises:
        GitError: If command fails and check=True
    """
    cmd = ["git", "-C", str(repo_root)] + list(args)
    logger.debug(f"Running: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=True,
            timeout=30,  # Git commands should be fast
        )
        return result
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        logger.error(f"Git command failed: {error_msg}")
        raise GitError(f"git {args[0]} failed: {error_msg}") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out after 30s") from e


def is_git_repo(repo_root: str) -> bool:
    """
    Check if the given path is inside a git repository.
    
    Args:
        repo_root: Path to check
        
    Returns:
        True if path is inside a git work tree
    """
    if not Path(repo_root).exists():
        return False
    
    try:
        result = _run_git(repo_root, "rev-parse", "--is-inside-work-tree", check=False)
        return result.returncode == 0 and result.stdout.strip() == "true"
    except GitError:
        return False


def current_branch(repo_root: str) -> str:
    """
    Get the current branch name.
    
    Args:
        repo_root: Path to repository root
        
    Returns:
        Current branch name (e.g., 'main', 'master', 'feature-x')
        
    Raises:
        GitError: If not in a git repo or in detached HEAD state
    """
    result = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = result.stdout.strip()
    
    if branch == "HEAD":
        # Detached HEAD state
        raise GitError("Repository is in detached HEAD state")
    
    return branch


def status_porcelain(repo_root: str) -> str:
    """
    Get git status in porcelain format (machine-readable).
    
    Args:
        repo_root: Path to repository root
        
    Returns:
        Porcelain status output (empty string = clean working tree)
    """
    result = _run_git(repo_root, "status", "--porcelain")
    return result.stdout


def is_working_tree_clean(repo_root: str) -> bool:
    """
    Check if the working tree is clean (no uncommitted changes).
    
    Args:
        repo_root: Path to repository root
        
    Returns:
        True if working tree is clean
    """
    status = status_porcelain(repo_root)
    return len(status.strip()) == 0


def ensure_clean_working_tree(repo_root: str) -> None:
    """
    Verify working tree is clean, raise if dirty.
    
    Args:
        repo_root: Path to repository root
        
    Raises:
        DirtyRepoError: If working tree has uncommitted changes
    """
    status = status_porcelain(repo_root)
    if status.strip():
        # Show first few lines of status in error
        lines = status.strip().split('\n')
        preview = '\n'.join(lines[:5])
        if len(lines) > 5:
            preview += f"\n... and {len(lines) - 5} more"
        raise DirtyRepoError(
            f"Repository has uncommitted changes. Please commit or stash them first.\n{preview}"
        )


def create_and_checkout_branch(repo_root: str, branch_name: str) -> None:
    """
    Create a new branch and check it out.
    
    Args:
        repo_root: Path to repository root
        branch_name: Name for the new branch
        
    Raises:
        GitError: If branch creation or checkout fails
    """
    logger.info(f"Creating and checking out branch: {branch_name}")
    _run_git(repo_root, "checkout", "-b", branch_name)


def checkout(repo_root: str, ref: str) -> None:
    """
    Checkout a branch or ref.
    
    Args:
        repo_root: Path to repository root
        ref: Branch name, tag, or commit hash to checkout
        
    Raises:
        GitError: If checkout fails
    """
    logger.info(f"Checking out: {ref}")
    _run_git(repo_root, "checkout", ref)


def diff_stat(repo_root: str, base_ref: str, head_ref: str = "HEAD") -> str:
    """
    Get diff statistics between two refs (committed changes only).
    
    Args:
        repo_root: Path to repository root
        base_ref: Base reference (branch, tag, or commit)
        head_ref: Head reference (default: HEAD)
        
    Returns:
        Diff stat output showing changed files and line counts
        
    Example output:
        src/file.py | 10 +++++-----
        tests/test.py | 5 +++++
        2 files changed, 10 insertions(+), 5 deletions(-)
        
    Note:
        This compares commits only. For uncommitted changes, use diff_stat_working_tree.
    """
    # Use ... for symmetric diff (shows changes since common ancestor)
    result = _run_git(repo_root, "diff", "--stat", f"{base_ref}...{head_ref}")
    return result.stdout


def diff_stat_working_tree(repo_root: str, base_ref: str) -> str:
    """
    Get diff statistics between a ref and the STAGED working tree.
    
    This stages all changes first (git add -A) then computes the diff.
    This is necessary because git diff doesn't show untracked files.
    
    IMPORTANT: This function stages files but does NOT commit them.
    The staging is necessary to get accurate diff stats for new files.
    
    Args:
        repo_root: Path to repository root
        base_ref: Base reference (branch, tag, or commit) to compare against
        
    Returns:
        Diff stat output showing changed files and line counts
        
    Example:
        If pipeline wrote files but didn't commit, this will still show them:
        
        src/generated.py | 100 +++++++++++++++++++++
        1 file changed, 100 insertions(+)
    """
    # Stage all changes first (including untracked files)
    # This is necessary because git diff doesn't see untracked files
    _run_git(repo_root, "add", "-A")
    
    # Now diff staged changes against the base ref
    result = _run_git(repo_root, "diff", "--cached", "--stat", base_ref)
    return result.stdout


def diff_name_only_working_tree(repo_root: str, base_ref: str) -> str:
    """
    Get list of changed files between a ref and the staged working tree.
    
    Stages all changes first to capture untracked files.
    Simpler than diff_stat, just lists file paths that changed.
    Useful for an "ungameable" proof of what files were modified.
    
    Args:
        repo_root: Path to repository root
        base_ref: Base reference to compare against
        
    Returns:
        Newline-separated list of changed file paths
    """
    # Stage all changes first
    _run_git(repo_root, "add", "-A")
    
    # Diff staged against base
    result = _run_git(repo_root, "diff", "--cached", "--name-only", base_ref)
    return result.stdout


def generate_demo_branch_name() -> str:
    """
    Generate a unique branch name for demo runs.
    
    Returns:
        Branch name like 'demo-20251222-143052'
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"demo-{timestamp}"


def commit_all(repo_root: str, message: str) -> Optional[str]:
    """
    Stage all changes and commit with message.
    
    Args:
        repo_root: Path to repository root
        message: Commit message
        
    Returns:
        Commit hash if changes were committed, None if nothing to commit
    """
    # Check if there are changes to commit
    status = status_porcelain(repo_root)
    if not status.strip():
        logger.info("No changes to commit")
        return None
    
    # Stage all changes
    _run_git(repo_root, "add", "-A")
    
    # Commit
    _run_git(repo_root, "commit", "-m", message)
    
    # Get the commit hash
    result = _run_git(repo_root, "rev-parse", "HEAD")
    commit_hash = result.stdout.strip()
    logger.info(f"Committed: {commit_hash[:8]}")
    return commit_hash


def get_commit_count(repo_root: str, base_ref: str, head_ref: str = "HEAD") -> int:
    """
    Count commits between two refs.
    
    Args:
        repo_root: Path to repository root
        base_ref: Base reference
        head_ref: Head reference (default: HEAD)
        
    Returns:
        Number of commits between base and head
    """
    result = _run_git(repo_root, "rev-list", "--count", f"{base_ref}..{head_ref}")
    return int(result.stdout.strip())


def stash_all(repo_root: str, message: Optional[str] = None) -> bool:
    """
    Stash all changes (including untracked files) to enable safe checkout.
    
    Args:
        repo_root: Path to repository root
        message: Optional stash message
        
    Returns:
        True if changes were stashed, False if nothing to stash
    """
    # Check if there's anything to stash
    if is_working_tree_clean(repo_root):
        return False
    
    args = ["stash", "push", "--include-untracked"]
    if message:
        args.extend(["-m", message])
    
    _run_git(repo_root, *args)
    logger.info(f"Stashed changes: {message or '(no message)'}")
    return True


def reset_hard(repo_root: str, ref: str = "HEAD") -> None:
    """
    Hard reset to a ref, discarding all changes.
    
    WARNING: This discards uncommitted changes permanently.
    
    Args:
        repo_root: Path to repository root
        ref: Reference to reset to (default: HEAD)
    """
    _run_git(repo_root, "reset", "--hard", ref)
    # Also clean untracked files
    _run_git(repo_root, "clean", "-fd")
    logger.info(f"Hard reset to {ref}")


def safe_checkout(repo_root: str, ref: str, stash_message: Optional[str] = None) -> bool:
    """
    Checkout a ref, stashing any uncommitted changes first if needed.
    
    This is the safe way to checkout when you don't know if the working tree is clean.
    
    Args:
        repo_root: Path to repository root
        ref: Branch or ref to checkout
        stash_message: Message for the stash if changes need to be stashed
        
    Returns:
        True if changes were stashed to enable checkout, False if tree was clean
        
    Raises:
        GitError: If checkout fails even after stashing
    """
    stashed = stash_all(repo_root, stash_message)
    checkout(repo_root, ref)
    return stashed
