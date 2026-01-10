"""
Production harness module.

Provides shared logic for preflight checks, fresh reset, and timeout-wrapped execution.
Used by CLI, Streamlit UI, and future entrypoints.

Design decisions:
- Subprocess isolation for timeout safety (can kill stuck LLM/DB calls)
- Dynamic schema discovery for fresh reset (no hardcoded table lists)
- Preflight checks are fast and don't block on slow operations
- Git operations for repo integration proof
"""
from integration_coworker.harness.preflight import run_preflight_checks, PreflightResult, PreflightCheck
from integration_coworker.harness.fresh_reset import fresh_reset_all, FreshResetResult
# Fix: Actual function is run_pipeline_with_timeout, alias for API compatibility
from integration_coworker.harness.runner import (
    run_pipeline_with_timeout as run_with_timeout,
    run_pipeline_with_timeout,
    TimeoutResult,
    run_showcase,
    ShowcaseResult,
    cancel_pipeline,
)
from integration_coworker.harness.showcase_specs import (
    ShowcaseSpec,
    SHOWCASE_SPECS,
    QUICK_SHOWCASE_SPECS,
    MINIMAL_SHOWCASE_SPECS,
    get_showcase_specs,
)
from integration_coworker.harness.git_ops import (
    is_git_repo,
    current_branch,
    status_porcelain,
    is_working_tree_clean,
    ensure_clean_working_tree,
    create_and_checkout_branch,
    checkout,
    diff_stat,
    diff_stat_working_tree,
    diff_name_only_working_tree,
    generate_demo_branch_name,
    commit_all,
    stash_all,
    reset_hard,
    safe_checkout,
    GitError,
    DirtyRepoError,
)

__all__ = [
    "run_preflight_checks", "PreflightResult", "PreflightCheck",
    "fresh_reset_all", "FreshResetResult",
    "run_with_timeout", "run_pipeline_with_timeout", "TimeoutResult",
    "run_showcase", "ShowcaseResult", "cancel_pipeline",
    "ShowcaseSpec", "SHOWCASE_SPECS", "QUICK_SHOWCASE_SPECS", 
    "MINIMAL_SHOWCASE_SPECS", "get_showcase_specs",
    # Git operations
    "is_git_repo", "current_branch", "status_porcelain",
    "is_working_tree_clean", "ensure_clean_working_tree",
    "create_and_checkout_branch", "checkout", "diff_stat",
    "diff_stat_working_tree", "diff_name_only_working_tree",
    "generate_demo_branch_name", "commit_all",
    "stash_all", "reset_hard", "safe_checkout",
    "GitError", "DirtyRepoError",
]
