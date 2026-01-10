"""
Subprocess-based pipeline runner with reliable timeout.

CRITICAL DESIGN DECISION (per user requirements):
- Uses subprocess isolation, NOT ThreadPoolExecutor
- Parent process can SIGTERM/SIGKILL the child if timeout exceeded
- Thread-based timeout cannot kill stuck LLM or DB calls
- Subprocess can be terminated at any point

Architecture:
                      ┌─────────────────┐
                      │  Parent Process │
                      │  (Streamlit/CLI)│
                      │                 │
                      │  ┌───────────┐  │
                      │  │ Timer     │  │
                      │  │ Watchdog  │  │
                      │  └─────┬─────┘  │
                      │        │        │
                      └────────┼────────┘
                               │ subprocess.Popen
                               │ stdout/stderr capture
                               ▼
                      ┌─────────────────┐
                      │  Child Process  │
                      │  (isolated env) │
                      │                 │
                      │  run_pipeline() │
                      │                 │
                      └─────────────────┘

The child process writes JSON result to stdout on success.
Parent reads stdout, parses JSON, returns result.
On timeout: parent sends SIGTERM, waits 5s, sends SIGKILL.

Phase 2: Adds repo integration proof with git branch/diff handling.
"""
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import select

# Import git operations
from integration_coworker.harness.git_ops import (
    is_git_repo,
    current_branch,
    is_working_tree_clean,
    ensure_clean_working_tree,
    create_and_checkout_branch,
    checkout,
    diff_stat,
    diff_stat_working_tree,
    diff_name_only_working_tree,
    status_porcelain,
    generate_demo_branch_name,
    commit_all,
    stash_all,
    safe_checkout,
    GitError,
    DirtyRepoError,
)

logger = logging.getLogger(__name__)

# Default timeout matches demo-final-showcase.sh behavior (~10-15 min typical)
DEFAULT_TIMEOUT_SECONDS = 600  # 10 minutes

# Grace period after SIGTERM before SIGKILL
GRACEFUL_SHUTDOWN_SECONDS = 5

# Type alias for event callback
EventCallback = Optional[Callable[[Dict[str, Any]], None]]


@dataclass  
class TimeoutResult:
    """Result wrapper for timed pipeline execution."""
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timed_out: bool = False
    elapsed_seconds: float = 0.0
    return_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    events: List[Dict[str, Any]] = field(default_factory=list)  # Progress events
    # Phase 2: Repo integration proof
    repo: Optional[Dict[str, Any]] = None  # { repo_root, base_branch, demo_branch, diff_stat, status }


def _extract_events(stdout: str) -> List[Dict[str, Any]]:
    """
    Extract progress events from subprocess stdout.
    
    Events are emitted as:
        __EVENT__ {"type":"phase","timestamp":1234.5,...}
    """
    events = []
    for line in stdout.splitlines():
        if line.startswith("__EVENT__ "):
            try:
                event_json = line[len("__EVENT__ "):]
                events.append(json.loads(event_json))
            except json.JSONDecodeError:
                pass
    return events


def _create_runner_script(
    spec_refs: List[str],
    task_description: str,
    dry_run: bool,
    enable_live_tests: bool,
    sandbox_gates: List[str],
    repo_root: Optional[str] = None,
) -> str:
    """
    Generate a Python script that runs the pipeline and outputs JSON result.
    
    This script runs in the subprocess and contains all logic for:
    1. Setting CODEGEN_PROFILE=production for sandbox execution
    2. Calling design_and_generate_integration (the actual API)
    3. Emitting progress events as __EVENT__ JSON lines
    4. Serializing result to JSON on stdout
    5. Proper error handling with traceback
    
    V4 Fix: Uses design_and_generate_integration (correct API), not run_integration_workflow
    V4.1: Added progress event emission for real-time UI updates
    V5: Added repo_root support for repo integration proof
    """
    # Escape strings for embedding in Python code
    def escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    
    # Build spec_refs list as Python literal
    spec_refs_literal = "[" + ", ".join(f'"{escape(s)}"' for s in spec_refs) + "]"
    sandbox_gates_str = json.dumps(sandbox_gates)
    repo_root_literal = f'"{escape(repo_root)}"' if repo_root else "None"
    
    script = f'''#!/usr/bin/env python3
"""Auto-generated runner script for subprocess execution."""
import json
import sys
import traceback
import os
import time
from pathlib import Path

def emit_event(event_type, **kwargs):
    """Emit a progress event as JSON line for parent to parse."""
    event = {{"type": event_type, "timestamp": time.time(), **kwargs}}
    print(f"__EVENT__ {{json.dumps(event)}}", flush=True)

def main():
    start_time = time.time()
    try:
        emit_event("phase", phase="init", message="Initializing runner...")
        
        # V4 Fix: Set production profile BEFORE importing anything else
        # This ensures sandbox execution is enabled
        os.environ["CODEGEN_PROFILE"] = "production"
        
        # Set live test environment if enabled
        enable_live = {enable_live_tests}
        if enable_live:
            os.environ["ALLOW_LIVE"] = "1"
        
        emit_event("phase", phase="import", message="Loading pipeline modules...")
        from integration_coworker.api.entrypoint import design_and_generate_integration
        from integration_coworker.api.types import IntegrationOptions
        
        # Build options - dry_run prevents DB writes
        options = IntegrationOptions(
            dry_run={dry_run},
        )
        
        # V5: Parse repo_root from script literal
        repo_root_path = Path({repo_root_literal}) if {repo_root_literal} else None
        
        emit_event("phase", phase="pipeline", message="Starting pipeline execution...", 
                   spec_refs={spec_refs_literal},
                   repo_root=str(repo_root_path) if repo_root_path else None)
        
        # Run the pipeline with correct API (V5: includes repo_root)
        result = design_and_generate_integration(
            spec_refs={spec_refs_literal},
            task_description="{escape(task_description)}",
            options=options,
            repo_root=repo_root_path,
        )
        
        emit_event("phase", phase="pipeline_complete", 
                   message=f"Pipeline completed in {{time.time() - start_time:.1f}}s",
                   steps=result.completed_steps)
        
        # Serialize key result fields to JSON
        # (Full object serialization is complex due to dataclass nesting)
        result_dict = {{
            "run_id": result.run_id,
            "completed_steps": result.completed_steps,
            "workflow_nodes": len(result.workflow_nodes),
            "workflow_edges": len(result.workflow_edges),
            "code_artifacts": len(result.code_artifacts),
            "endpoints": len(result.endpoints),
            "schemas": len(result.schemas),
            "sandbox_result": result.sandbox_result,
            "errors": result.errors,
            "report_markdown": result.report_markdown[:500] if result.report_markdown else None,
            "provider_code": result.provider_code,
            # V4: HITL review gate fields (for UI to detect paused workflows)
            "pending_review_kind": getattr(result, 'pending_review_kind', None),
            "review_artifact_refs": getattr(result, 'review_artifact_refs', None),
            "review_decisions": getattr(result, 'review_decisions', None),
        }}
        
        emit_event("phase", phase="done", message="Run complete",
                   success=True, elapsed=time.time() - start_time)
        
        # Output JSON to stdout (parent will parse this)
        print("__RESULT_JSON_START__")
        print(json.dumps(result_dict, default=str))
        print("__RESULT_JSON_END__")
        sys.exit(0)
        
    except Exception as e:
        emit_event("phase", phase="error", message=str(e),
                   success=False, elapsed=time.time() - start_time)
        
        # Output error as JSON
        error_dict = {{
            "error": str(e),
            "traceback": traceback.format_exc(),
        }}
        print("__RESULT_JSON_START__")
        print(json.dumps(error_dict, default=str))
        print("__RESULT_JSON_END__")
        sys.exit(1)

if __name__ == "__main__":
    main()
'''
    return script


def _extract_result_json(stdout: str) -> Optional[Dict[str, Any]]:
    """
    Extract JSON result from subprocess stdout.
    
    The child script wraps JSON in markers to distinguish from
    other output (logging, print statements, etc.).
    """
    start_marker = "__RESULT_JSON_START__"
    end_marker = "__RESULT_JSON_END__"
    
    start_idx = stdout.find(start_marker)
    end_idx = stdout.find(end_marker)
    
    if start_idx == -1 or end_idx == -1:
        return None
    
    json_str = stdout[start_idx + len(start_marker):end_idx].strip()
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse result JSON: {e}")
        return None


def _stream_subprocess_output(
    proc: subprocess.Popen,
    timeout_seconds: int,
    event_callback: EventCallback = None,
) -> tuple:
    """
    Stream subprocess output line-by-line while respecting timeout.
    
    V6: Live streaming implementation for real-time progress.
    Uses select.select() for non-blocking IO on stdout/stderr.
    
    Args:
        proc: Running subprocess
        timeout_seconds: Maximum execution time
        event_callback: Optional callback for __EVENT__ lines
        
    Returns:
        Tuple of (stdout_lines, stderr_lines, timed_out)
    """
    import select
    
    stdout_lines = []
    stderr_lines = []
    start_time = time.time()
    timed_out = False
    
    # Set non-blocking mode on stdout/stderr
    import fcntl
    for fd in [proc.stdout, proc.stderr]:
        if fd:
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    
    # Buffer for incomplete lines
    stdout_buffer = ""
    stderr_buffer = ""
    
    while proc.poll() is None:
        # Check timeout
        elapsed = time.time() - start_time
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            timed_out = True
            break
        
        # Wait for data with timeout (max 0.5s to stay responsive)
        select_timeout = min(0.5, remaining)
        readable, _, _ = select.select(
            [proc.stdout, proc.stderr], [], [], select_timeout
        )
        
        for fd in readable:
            try:
                chunk = fd.read(4096)
                if chunk:
                    chunk_str = chunk.decode('utf-8', errors='replace')
                    
                    if fd == proc.stdout:
                        stdout_buffer += chunk_str
                        # Process complete lines
                        while '\n' in stdout_buffer:
                            line, stdout_buffer = stdout_buffer.split('\n', 1)
                            stdout_lines.append(line)
                            # Check for event
                            if line.startswith("__EVENT__ ") and event_callback:
                                try:
                                    event_json = line[len("__EVENT__ "):]
                                    event = json.loads(event_json)
                                    event_callback(event)
                                except json.JSONDecodeError:
                                    pass
                    else:
                        stderr_buffer += chunk_str
                        while '\n' in stderr_buffer:
                            line, stderr_buffer = stderr_buffer.split('\n', 1)
                            stderr_lines.append(line)
            except (IOError, OSError):
                # Non-blocking read returned nothing
                pass
    
    # Read any remaining data after process completes
    if proc.stdout:
        try:
            remaining_stdout = proc.stdout.read()
            if remaining_stdout:
                stdout_buffer += remaining_stdout.decode('utf-8', errors='replace')
        except (IOError, OSError):
            pass
    
    if proc.stderr:
        try:
            remaining_stderr = proc.stderr.read()
            if remaining_stderr:
                stderr_buffer += remaining_stderr.decode('utf-8', errors='replace')
        except (IOError, OSError):
            pass
    
    # Process any remaining buffer content
    if stdout_buffer:
        stdout_lines.append(stdout_buffer)
        if stdout_buffer.startswith("__EVENT__ ") and event_callback:
            try:
                event_json = stdout_buffer[len("__EVENT__ "):]
                event = json.loads(event_json)
                event_callback(event)
            except json.JSONDecodeError:
                pass
    if stderr_buffer:
        stderr_lines.append(stderr_buffer)
    
    return stdout_lines, stderr_lines, timed_out


def _setup_repo_branch(repo_root: str) -> Tuple[str, str]:
    """
    Setup demo branch for repo integration proof.
    
    Args:
        repo_root: Path to git repository
        
    Returns:
        Tuple of (base_branch, demo_branch)
        
    Raises:
        GitError: If not a git repo
        DirtyRepoError: If working tree is dirty
    """
    # Verify it's a git repo
    if not is_git_repo(repo_root):
        raise GitError(f"repo_root is not a git repository: {repo_root}")
    
    # Fail fast if dirty
    ensure_clean_working_tree(repo_root)
    
    # Get current branch
    base_branch = current_branch(repo_root)
    
    # Create demo branch
    demo_branch = generate_demo_branch_name()
    create_and_checkout_branch(repo_root, demo_branch)
    
    logger.info(f"Created demo branch '{demo_branch}' from '{base_branch}'")
    return base_branch, demo_branch


def _teardown_repo_branch(
    repo_root: str, 
    base_branch: str,
    demo_branch: str,
    pipeline_succeeded: bool,
) -> Dict[str, Any]:
    """
    Teardown demo branch and collect git proof.
    
    CRITICAL FIX: Uses working-tree comparison for diff proof, not commit comparison.
    This ensures we capture file changes even if they weren't committed.
    
    Strategy:
    1. First capture diff using working-tree comparison (before any commits)
    2. Then optionally commit changes
    3. Use safe_checkout to return to base branch (stashes if needed)
    
    Args:
        repo_root: Path to git repository
        base_branch: Original branch to return to
        demo_branch: Demo branch that was created
        pipeline_succeeded: Whether pipeline execution succeeded
        
    Returns:
        Dict with repo proof: { repo_root, base_branch, demo_branch, diff_stat, 
                                files_changed, status, commit_hash, stashed }
    """
    repo_info = {
        "repo_root": repo_root,
        "base_branch": base_branch,
        "demo_branch": demo_branch,
        "diff_stat": "",
        "files_changed": "",
        "status": "",
        "commit_hash": None,
        "stashed": False,
    }
    
    try:
        # FIRST: Capture working-tree diff BEFORE any commits
        # This shows what files were actually written, regardless of commit state
        try:
            # Get diff stat comparing base branch to current working tree
            working_diff = diff_stat_working_tree(repo_root, base_branch)
            repo_info["diff_stat"] = working_diff
            
            # Also get file list for ungameable proof
            files = diff_name_only_working_tree(repo_root, base_branch)
            repo_info["files_changed"] = files.strip()
            
            logger.info(f"Working tree diff captured: {len(files.splitlines())} files changed")
        except GitError as e:
            logger.warning(f"Failed to get working-tree diff: {e}")
            repo_info["diff_stat"] = f"(diff unavailable: {e})"
        
        # Capture status (shows uncommitted state)
        try:
            status = status_porcelain(repo_root)
            repo_info["status"] = status
        except GitError:
            pass
        
        # If pipeline succeeded and there are changes, commit them
        if pipeline_succeeded:
            commit_hash = commit_all(
                repo_root, 
                f"[integration-coworker] Auto-generated code from pipeline run"
            )
            repo_info["commit_hash"] = commit_hash
            if commit_hash:
                logger.info(f"Committed changes: {commit_hash[:12]}")
            
    finally:
        # Always return to base branch using safe_checkout
        # This stashes any uncommitted changes first if needed
        try:
            stashed = safe_checkout(
                repo_root, 
                base_branch,
                stash_message=f"[integration-coworker] Stashed from demo branch {demo_branch}"
            )
            repo_info["stashed"] = stashed
            if stashed:
                logger.warning(f"Stashed uncommitted changes to return to {base_branch}")
            logger.info(f"Returned to base branch '{base_branch}'")
        except GitError as e:
            logger.error(f"Failed to return to base branch: {e}")
            repo_info["error"] = f"Failed to return to base branch: {e}"
    
    return repo_info


def run_pipeline_with_timeout(
    spec_refs: List[str],
    task_description: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    dry_run: bool = False,
    enable_live_tests: bool = False,
    sandbox_gates: Optional[List[str]] = None,
    repo_root: Optional[str] = None,
    live_host_allowlist: Optional[str] = None,
    event_callback: EventCallback = None,
    inherit_env: bool = True,
    extra_env: Optional[Dict[str, str]] = None,
) -> TimeoutResult:
    """
    Run pipeline in subprocess with enforced timeout.
    
    CRITICAL: This uses subprocess isolation so the parent can kill
    stuck processes. Thread-based timeouts cannot reliably kill
    stuck LLM calls, DB connections, or network operations.
    
    V4 Fix: Updated signature to match UI needs:
    - spec_refs: List of spec paths (not single spec_uri)
    - task_description: Natural language task description
    - dry_run: If True, skip DB persistence
    
    V5: Added repo_root for repo integration proof
    V6: Added event_callback for real-time progress streaming
    V6.1: Added live_host_allowlist for safety
    
    Args:
        spec_refs: List of paths/URLs to OpenAPI specs
        task_description: Natural language description of integration task
        timeout_seconds: Max execution time (default: 600s = 10min)
        dry_run: If True, skip database persistence
        enable_live_tests: Whether to run live validation tests
        sandbox_gates: List of sandbox gates to run (default: all)
        repo_root: Optional path to target repository for file writes
        live_host_allowlist: Comma-separated list of allowed hosts for live tests
        event_callback: Optional callback for real-time progress events
        inherit_env: Inherit parent environment variables
        extra_env: Additional environment variables for subprocess
        
    Returns:
        TimeoutResult with success status, result dict, or error info.
        
    Usage:
        # Basic usage
        result = run_pipeline_with_timeout(
            spec_refs=["specs/stripe_api.json"],
            task_description="Create a checkout session",
            timeout_seconds=300,  # 5 min
            dry_run=True,
            repo_root="/path/to/target/repo",
        )
        
        # With live tests and allowlist
        result = run_pipeline_with_timeout(
            spec_refs=["specs/stripe_api.json"],
            task_description="Create a checkout session",
            enable_live_tests=True,
            live_host_allowlist="api.stripe.com",
        )
        
        # With streaming events
        def on_event(event):
            print(f"[{event['phase']}] {event['message']}")
            
        result = run_pipeline_with_timeout(
            spec_refs=["specs/stripe_api.json"],
            task_description="Create a checkout session",
            event_callback=on_event,  # Called as events arrive
        )
        
        if result.timed_out:
            print("Pipeline timed out!")
        elif result.success:
            print(f"Success: {result.result}")
        else:
            print(f"Error: {result.error}")
    """
    if sandbox_gates is None:
        # Default to all gates
        sandbox_gates = ["ruff", "mypy", "bandit", "pytest", "coverage"]
    
    start_time = time.time()
    
    # Phase 2: Git repo integration setup
    base_branch: Optional[str] = None
    demo_branch: Optional[str] = None
    repo_info: Optional[Dict[str, Any]] = None
    
    if repo_root:
        # Validate repo_root exists
        if not Path(repo_root).exists():
            return TimeoutResult(
                success=False,
                error=f"repo_root does not exist: {repo_root}",
                elapsed_seconds=time.time() - start_time,
            )
        
        # Setup git branch
        try:
            base_branch, demo_branch = _setup_repo_branch(repo_root)
        except DirtyRepoError as e:
            return TimeoutResult(
                success=False,
                error=str(e),
                elapsed_seconds=time.time() - start_time,
            )
        except GitError as e:
            return TimeoutResult(
                success=False,
                error=f"repo_root is not a git repository: {repo_root}",
                elapsed_seconds=time.time() - start_time,
            )
    
    # Generate the runner script with correct parameters (V5: includes repo_root)
    script_content = _create_runner_script(
        spec_refs=spec_refs,
        task_description=task_description,
        dry_run=dry_run,
        enable_live_tests=enable_live_tests,
        sandbox_gates=sandbox_gates,
        repo_root=repo_root,
    )
    
    # Write to temp file
    with tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.py',
        delete=False,
        prefix='pipeline_runner_'
    ) as f:
        f.write(script_content)
        script_path = f.name
    
    try:
        # Build environment
        env = os.environ.copy() if inherit_env else {}
        if extra_env:
            env.update(extra_env)
        
        # V5: Set IC_SANDBOX_GATES for profile override
        env["IC_SANDBOX_GATES"] = ",".join(sandbox_gates)
        logger.info(f"Setting IC_SANDBOX_GATES={env['IC_SANDBOX_GATES']}")
        
        # V6.1: Set live test env vars if enabled (with allowlist for safety)
        if enable_live_tests:
            env["IC_ENABLE_LIVE_TESTS"] = "1"
            # Allowlist from UI takes priority, else extracted from spec inside process
            if live_host_allowlist:
                env["IC_LIVE_HOST_ALLOWLIST"] = live_host_allowlist
                logger.info(f"Setting IC_LIVE_HOST_ALLOWLIST={live_host_allowlist}")
            else:
                logger.info("Live tests enabled but no allowlist provided - will extract from spec")
        
        # Use same Python interpreter as parent
        python_exe = sys.executable
        
        # Start subprocess
        logger.info(f"Starting pipeline subprocess with {timeout_seconds}s timeout")
        proc = subprocess.Popen(
            [python_exe, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,  # Create new process group for clean kill
        )
        
        # V6: Use streaming reader for real-time progress
        # This calls event_callback as events arrive, not after completion
        stdout_lines, stderr_lines, timed_out = _stream_subprocess_output(
            proc, timeout_seconds, event_callback
        )
        
        if timed_out:
            # TIMEOUT: Kill the subprocess
            elapsed = time.time() - start_time
            logger.warning(f"Pipeline timed out after {elapsed:.1f}s, sending SIGTERM...")
            
            # Send SIGTERM to process group
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass  # Already dead
            
            # Wait briefly for graceful shutdown
            try:
                proc.wait(timeout=GRACEFUL_SHUTDOWN_SECONDS)
                logger.info("Process terminated gracefully after SIGTERM")
            except subprocess.TimeoutExpired:
                # Still alive, use SIGKILL
                logger.warning("Process did not terminate, sending SIGKILL...")
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            
            return TimeoutResult(
                success=False,
                timed_out=True,
                error=f"Pipeline exceeded {timeout_seconds}s timeout and was terminated",
                elapsed_seconds=elapsed,
                stdout="\n".join(stdout_lines),
                stderr="\n".join(stderr_lines),
                events=_extract_events("\n".join(stdout_lines)),
                repo=_teardown_repo_branch(repo_root, base_branch, demo_branch, False) if repo_root and base_branch else None,
            )
        
        # Wait for process to complete (should already be done if not timed out)
        proc.wait()
        stdout = "\n".join(stdout_lines)
        stderr = "\n".join(stderr_lines)
        elapsed = time.time() - start_time
        
        # Parse result from stdout
        result_dict = _extract_result_json(stdout)
        events = _extract_events(stdout)
        
        pipeline_succeeded = proc.returncode == 0 and result_dict is not None
        
        # Phase 2: Git teardown and capture diff
        if repo_root and base_branch:
            repo_info = _teardown_repo_branch(repo_root, base_branch, demo_branch, pipeline_succeeded)
        
        if pipeline_succeeded:
            return TimeoutResult(
                success=True,
                result=result_dict,
                elapsed_seconds=elapsed,
                return_code=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                events=events,
                repo=repo_info,
            )
        else:
            error = result_dict.get("error") if result_dict else f"Exit code {proc.returncode}"
            return TimeoutResult(
                success=False,
                result=result_dict,
                error=error,
                elapsed_seconds=elapsed,
                return_code=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                events=events,
                repo=repo_info,
            )
            
    finally:
        # Cleanup temp script
        try:
            os.unlink(script_path)
        except:
            pass


def cancel_pipeline(pid: int) -> bool:
    """
    Cancel a running pipeline by PID.
    
    Useful for UI "Cancel" button.
    Sends SIGTERM, waits, then SIGKILL if needed.
    
    Returns True if process was terminated.
    """
    try:
        # Send SIGTERM to process group
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        
        # Wait for termination
        start = time.time()
        while time.time() - start < GRACEFUL_SHUTDOWN_SECONDS:
            try:
                os.kill(pid, 0)  # Check if still alive
                time.sleep(0.1)
            except OSError:
                return True  # Dead
        
        # Still alive, SIGKILL
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        return True
        
    except ProcessLookupError:
        return True  # Already dead
    except Exception as e:
        logger.error(f"Failed to cancel pipeline {pid}: {e}")
        return False


@dataclass
class ShowcaseResult:
    """Result for multi-spec showcase run."""
    total_specs: int
    passed: int
    failed: int
    elapsed_seconds: float
    per_spec_results: List[Dict[str, Any]] = field(default_factory=list)
    # Summary: Each entry has {spec, task, success, elapsed, events, result_dict, error}


def run_showcase(
    specs: List[tuple],  # List of (spec_file, task_description)
    timeout_per_spec: int = DEFAULT_TIMEOUT_SECONDS,
    dry_run: bool = False,
    enable_live_tests: bool = False,
    sandbox_gates: Optional[List[str]] = None,
    on_spec_start: Optional[callable] = None,
    on_spec_end: Optional[callable] = None,
) -> ShowcaseResult:
    """
    Run multiple specs sequentially like demo-final-showcase.sh.
    
    Args:
        specs: List of (spec_file, task_description) tuples
        timeout_per_spec: Timeout for each spec (default: 600s)
        dry_run: If True, skip DB persistence
        enable_live_tests: Whether to run live validation tests
        sandbox_gates: List of sandbox gates to run
        on_spec_start: Callback(idx, spec_file, task) called before each spec
        on_spec_end: Callback(idx, spec_file, result) called after each spec
        
    Returns:
        ShowcaseResult with per-spec results and summary
    """
    start_time = time.time()
    per_spec_results = []
    passed = 0
    failed = 0
    
    for idx, (spec_file, task_description) in enumerate(specs):
        if on_spec_start:
            on_spec_start(idx, spec_file, task_description)
        
        # Resolve spec path
        spec_path = _resolve_spec_path(spec_file)
        
        result = run_pipeline_with_timeout(
            spec_refs=[spec_path],
            task_description=task_description,
            timeout_seconds=timeout_per_spec,
            dry_run=dry_run,
            enable_live_tests=enable_live_tests,
            sandbox_gates=sandbox_gates,
        )
        
        spec_result = {
            "spec": spec_file,
            "task": task_description,
            "success": result.success,
            "timed_out": result.timed_out,
            "elapsed": result.elapsed_seconds,
            "events": result.events,
            "result": result.result,
            "error": result.error,
        }
        per_spec_results.append(spec_result)
        
        if result.success:
            passed += 1
        else:
            failed += 1
        
        if on_spec_end:
            on_spec_end(idx, spec_file, spec_result)
    
    return ShowcaseResult(
        total_specs=len(specs),
        passed=passed,
        failed=failed,
        elapsed_seconds=time.time() - start_time,
        per_spec_results=per_spec_results,
    )


def _resolve_spec_path(spec_file: str) -> str:
    """Resolve spec file name to full path."""
    # If already a path, return as-is
    if "/" in spec_file or spec_file.startswith("."):
        return spec_file
    
    # Try common locations
    candidates = [
        Path.cwd() / "specs" / spec_file,
        Path(__file__).parent.parent.parent.parent / "specs" / spec_file,
    ]
    
    for path in candidates:
        if path.exists():
            return str(path)
    
    # Return as-is, let the pipeline handle the error
    return spec_file
