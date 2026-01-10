# Phase 2: Repo Integration Proof

**Status**: Complete  
**Date**: 2025-01-28  
**Updated**: 2025-12-22 (Fixed uncommitted-writes bug)

## Overview

When `repo_root` is provided to the harness, the system:
1. Validates the repo is clean (no uncommitted changes)
2. Creates an isolated `demo-YYYYMMDD-HHMMSS` branch
3. Runs the pipeline in that branch
4. **Stages all changes** and captures working-tree diff (even for uncommitted files)
5. Commits any changes with a descriptive message
6. Returns to the original branch using safe checkout (stashes if needed)
7. Displays the diff stat in the Streamlit UI

## Critical Bug Fix (2025-12-22)

The original implementation had a correctness bug where `git diff base...HEAD` would show **empty** when files were written but not committed. This was fixed by:

1. **Using working-tree diff**: `git diff --cached --stat <base>` after staging
2. **Staging before diff**: `git add -A` to capture untracked files
3. **Safe checkout**: Uses `stash_all()` before checkout to handle uncommitted changes
4. **Added tests**: 5 new tests specifically for uncommitted-writes scenarios

## New Components

### `src/integration_coworker/harness/git_ops.py`

Pure functions for git operations:
- `is_git_repo(path)` - Check if path is a git repo
- `current_branch(repo)` - Get current branch name
- `ensure_clean_working_tree(repo)` - Validate no uncommitted changes
- `create_and_checkout_branch(repo, name)` - Create and switch to new branch
- `checkout(repo, ref)` - Switch to branch/ref
- `diff_stat(repo, base, head)` - Get `git diff --stat` between commits
- `diff_stat_working_tree(repo, base)` - **NEW**: Stages changes, then diffs against base
- `diff_name_only_working_tree(repo, base)` - **NEW**: File list for ungameable proof
- `status_porcelain(repo)` - Get porcelain status output
- `commit_all(repo, message)` - Stage all and commit
- `stash_all(repo, message)` - **NEW**: Stash all including untracked
- `safe_checkout(repo, ref)` - **NEW**: Stash-then-checkout for dirty trees
- `generate_demo_branch_name()` - Generate `demo-YYYYMMDD-HHMMSS`

### Exceptions

- `GitError` - General git operation failure
- `DirtyRepoError` - Repo has uncommitted changes

## Manual Verification

### Prerequisites

```bash
# Create a test repo
mkdir /tmp/test-repo && cd /tmp/test-repo
git init
git config user.email "test@test.com"
git config user.name "Test User"
echo "# Test" > README.md
git add . && git commit -m "Initial"
```

### Test 1: Verify git_ops module

```bash
cd /path/to/solver-agentic-spec-coworker
python -c "
from integration_coworker.harness.git_ops import (
    is_git_repo, current_branch, ensure_clean_working_tree,
    create_and_checkout_branch, checkout, diff_stat, generate_demo_branch_name
)
import tempfile
import subprocess
from pathlib import Path

# Create temp repo
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init'], cwd=repo, check=True, capture_output=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=repo, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=repo, check=True)
    (repo / 'README.md').write_text('# Test')
    subprocess.run(['git', 'add', '.'], cwd=repo, check=True)
    subprocess.run(['git', 'commit', '-m', 'Init'], cwd=repo, check=True)
    
    print(f'is_git_repo: {is_git_repo(repo)}')
    print(f'current_branch: {current_branch(repo)}')
    ensure_clean_working_tree(repo)
    print('ensure_clean_working_tree: passed')
    
    demo = generate_demo_branch_name()
    print(f'demo branch name: {demo}')
    
    create_and_checkout_branch(repo, demo)
    print(f'on branch: {current_branch(repo)}')
    
print('All git_ops tests passed!')
"
```

### Test 2: Run tests

```bash
pytest tests/harness/test_git_ops.py -v
```

Expected: All 22 tests pass.

### Test 3: Dirty repo rejection

```bash
cd /tmp/test-repo
echo "dirty" > dirty.txt
# Now try to use harness with repo_root=/tmp/test-repo
# Should fail with DirtyRepoError
```

### Test 4: Full harness run (E2E)

```bash
# Clean the test repo first
cd /tmp/test-repo
rm -f dirty.txt

# Run Streamlit with repo_root
cd /path/to/solver-agentic-spec-coworker
REPO_ROOT=/tmp/test-repo streamlit run src/integration_coworker/ui/streamlit_app.py
```

In the UI:
1. Run an integration
2. After completion, verify "📂 Repo Integration Proof" section appears
3. Check that base branch and demo branch are displayed
4. Verify diff stat shows any files changed

### Test 5: Verify branch cleanup

After running:
```bash
cd /tmp/test-repo
git branch  # Should show demo-* branch(es)
git log --oneline -5  # Verify commits on demo branch
```

## Acceptance Criteria

| Criteria | Status |
|----------|--------|
| `repo_root` unset: behavior unchanged | ✅ |
| Clean git repo: demo branch created, pipeline runs, UI shows diff | ✅ |
| Dirty repo: run blocked with clear error | ✅ |
| Tests: `pytest tests/harness/test_git_ops.py` passes | ✅ |

## Files Changed

- `src/integration_coworker/harness/git_ops.py` (NEW)
- `src/integration_coworker/harness/__init__.py` (export git_ops)
- `src/integration_coworker/harness/runner.py` (repo workflow)
- `src/integration_coworker/ui/streamlit_app.py` (repo proof panel)
- `tests/harness/test_git_ops.py` (NEW)
- `docs/PHASE2_REPO_PROOF.md` (NEW - this file)
