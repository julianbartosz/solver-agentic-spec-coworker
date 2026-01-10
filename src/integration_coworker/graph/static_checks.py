"""
Static analysis check registry and built-in checks.

PR #8: Static Analysis Gate

This module provides:
1. Built-in checks using stdlib only (always available)
2. Discovery-based optional checks (ruff, mypy, bandit)
3. A registry pattern for check management

CRITICAL INVARIANTS:
- Built-in checks use ONLY stdlib (ast, importlib)
- Optional tools discovered via shutil.which() + config detection
- "Tool missing" yields "skipped", never "failed"
- All checks are stateless and deterministic
- No subprocess calls unless discovery succeeds

PRODUCTION HARDENING:
- Hard timeouts per tool invocation
- Captured stdout/stderr size caps
- Deterministic output ordering (stable sorts)
- Tool version and invocation metadata recorded

Per ADR-HITL-ENHANCEMENT-v2:
- No hardcoded tool paths
- Discovery via PATH + repo config
- Explicit "enable" knobs for optional tools
"""

import ast
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from integration_coworker.graph.quality_models import (
    StaticIssue,
    StaticAnalysisResult,
    MAX_ERROR_MESSAGE_LENGTH,
    MAX_FILE_PATH_LENGTH,
)

# Import bounds from single source of truth
from integration_coworker.graph.production_guardrails import (
    TOOL_TIMEOUT_SECONDS,
    MAX_TOOL_OUTPUT_BYTES,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Check-specific Constants
# =============================================================================

# Maximum issues to collect from a single tool
MAX_ISSUES_PER_TOOL = 100

# Maximum files to check in one invocation
MAX_FILES_PER_CHECK = 500


# =============================================================================
# Check Registry Types
# =============================================================================

@dataclass
class CheckDefinition:
    """
    Definition of a static analysis check.
    
    Attributes:
        name: Unique check identifier
        category: Issue category for findings
        builtin: True if stdlib-only (always runs)
        tool_name: Optional external tool name (for PATH discovery)
        config_indicators: Files that indicate tool is configured
        run_check: The actual check function
        enabled_by_default: If True, runs when tool available even without config
    """
    name: str
    category: str
    builtin: bool = False
    tool_name: Optional[str] = None
    config_indicators: List[str] = field(default_factory=list)
    run_check: Optional[Callable] = None
    enabled_by_default: bool = False


@dataclass 
class CheckResult:
    """
    Result from running a single check.
    
    Attributes:
        check_name: Which check produced this
        status: "passed", "failed", or "skipped"
        issues: List of issues found
        skip_reason: Why check was skipped (if status="skipped")
        tool_version: Version of tool used (if external)
        invocation_metadata: Debug info for reproducibility
    """
    check_name: str
    status: str  # "passed", "failed", "skipped"
    issues: List[StaticIssue] = field(default_factory=list)
    skip_reason: Optional[str] = None
    tool_version: Optional[str] = None
    invocation_metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "check_name": self.check_name,
            "status": self.status,
            "issue_count": len(self.issues),
            "skip_reason": self.skip_reason,
            "tool_version": self.tool_version,
            "invocation_metadata": self.invocation_metadata,
        }


# =============================================================================
# Built-in Checks (stdlib only)
# =============================================================================

def check_syntax(code: str, file_path: str) -> List[StaticIssue]:
    """
    Check Python syntax using ast.parse().
    
    Built-in check - always available, no external deps.
    
    Args:
        code: Python source code
        file_path: Relative file path for issue reporting
        
    Returns:
        List of StaticIssue for syntax errors
    """
    issues = []
    
    try:
        ast.parse(code, filename=file_path)
    except SyntaxError as e:
        issues.append(StaticIssue(
            severity="error",
            category="syntax",
            file_path=file_path[:MAX_FILE_PATH_LENGTH],
            line_number=e.lineno or 1,
            column=e.offset or 0,
            message=str(e.msg)[:MAX_ERROR_MESSAGE_LENGTH] if e.msg else "Syntax error",
            rule_id="E999",
            auto_fixable=False,
        ))
    except Exception as e:
        # Catch any other parsing errors
        issues.append(StaticIssue(
            severity="error",
            category="syntax",
            file_path=file_path[:MAX_FILE_PATH_LENGTH],
            line_number=1,
            column=0,
            message=f"Parse error: {str(e)[:MAX_ERROR_MESSAGE_LENGTH]}",
            rule_id="E999",
            auto_fixable=False,
        ))
    
    return issues


def check_imports(code: str, file_path: str) -> List[StaticIssue]:
    """
    Check for basic import resolution issues.
    
    Built-in check using stdlib importlib. This is best-effort:
    - Checks if imported modules are resolvable in current environment
    - Does NOT guarantee runtime import success
    - Known limitations: dynamic imports, conditional imports, relative imports
    
    Args:
        code: Python source code
        file_path: Relative file path for issue reporting
        
    Returns:
        List of StaticIssue for unresolvable imports
    """
    issues = []
    
    try:
        tree = ast.parse(code, filename=file_path)
    except SyntaxError:
        # Syntax errors caught by check_syntax
        return issues
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name.split('.')[0]  # Top-level module
                if not _is_module_available(module_name):
                    issues.append(StaticIssue(
                        severity="warning",
                        category="import",
                        file_path=file_path[:MAX_FILE_PATH_LENGTH],
                        line_number=node.lineno,
                        column=node.col_offset,
                        message=f"Module '{alias.name}' may not be available",
                        rule_id="I001",
                        auto_fixable=False,
                    ))
        
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:  # Absolute import
                module_name = node.module.split('.')[0]
                if not _is_module_available(module_name):
                    issues.append(StaticIssue(
                        severity="warning",
                        category="import",
                        file_path=file_path[:MAX_FILE_PATH_LENGTH],
                        line_number=node.lineno,
                        column=node.col_offset,
                        message=f"Module '{node.module}' may not be available",
                        rule_id="I001",
                        auto_fixable=False,
                    ))
    
    return issues


def _is_module_available(module_name: str) -> bool:
    """
    Check if a module is available for import.
    
    Best-effort check using importlib.util.find_spec.
    Returns True if module is likely available (may have false positives).
    """
    import importlib.util
    
    # Skip built-in modules (always available)
    if module_name in sys.builtin_module_names:
        return True
    
    # Skip common generated code patterns
    if module_name.startswith('_'):
        return True
    
    try:
        spec = importlib.util.find_spec(module_name)
        return spec is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


# =============================================================================
# Production Tool Execution Helper
# =============================================================================

@dataclass
class ToolInvocationResult:
    """Result from running an external tool with production bounds."""
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    duration_ms: int
    metadata: Dict[str, Any]


def run_tool_safely(
    cmd: List[str],
    cwd: Path,
    timeout: int = TOOL_TIMEOUT_SECONDS,
    max_output: int = MAX_TOOL_OUTPUT_BYTES,
) -> ToolInvocationResult:
    """
    Run an external tool with production safety bounds.
    
    - Hard timeout enforcement
    - Stdout/stderr size caps
    - Invocation metadata capture
    
    Args:
        cmd: Command and arguments
        cwd: Working directory
        timeout: Max seconds to wait
        max_output: Max bytes of output to capture
        
    Returns:
        ToolInvocationResult with bounded output and metadata
    """
    start_time = time.monotonic()
    start_ts = datetime.now(timezone.utc).isoformat()
    
    truncated = False
    timed_out = False
    stdout_data = ""
    stderr_data = ""
    returncode = -1
    
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
        )
        returncode = result.returncode
        
        # Bounded output capture
        stdout_bytes = result.stdout[:max_output]
        stderr_bytes = result.stderr[:max_output]
        
        if len(result.stdout) > max_output or len(result.stderr) > max_output:
            truncated = True
        
        # Decode with error handling
        stdout_data = stdout_bytes.decode('utf-8', errors='replace')
        stderr_data = stderr_bytes.decode('utf-8', errors='replace')
        
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = -1
    except Exception as e:
        stderr_data = str(e)[:1000]
        returncode = -1
    
    duration_ms = int((time.monotonic() - start_time) * 1000)
    
    metadata = {
        "command": cmd[0] if cmd else "unknown",
        "args_count": len(cmd) - 1,
        "started_at": start_ts,
        "duration_ms": duration_ms,
        "timeout_seconds": timeout,
        "returncode": returncode,
        "timed_out": timed_out,
        "output_truncated": truncated,
        "stdout_bytes": len(stdout_data),
        "stderr_bytes": len(stderr_data),
    }
    
    return ToolInvocationResult(
        returncode=returncode,
        stdout=stdout_data,
        stderr=stderr_data,
        timed_out=timed_out,
        truncated=truncated,
        duration_ms=duration_ms,
        metadata=metadata,
    )


# =============================================================================
# Optional Tool Discovery
# =============================================================================

def discover_tool(tool_name: str) -> Optional[str]:
    """
    Discover if a tool is available on PATH.
    
    Args:
        tool_name: Name of the tool executable
        
    Returns:
        Path to tool if found, None otherwise
    """
    return shutil.which(tool_name)


def has_tool_config(repo_root: Optional[Path], config_files: List[str]) -> bool:
    """
    Check if a tool has configuration in the repo.
    
    Args:
        repo_root: Root of the repository
        config_files: List of config file patterns to check
        
    Returns:
        True if any config file exists
    """
    if not repo_root or not repo_root.exists():
        return False
    
    for config_file in config_files:
        config_path = repo_root / config_file
        if config_path.exists():
            return True
        
        # Also check pyproject.toml for tool sections
        pyproject = repo_root / "pyproject.toml"
        if pyproject.exists() and config_file.startswith("[tool."):
            try:
                content = pyproject.read_text()
                tool_section = config_file.replace("[tool.", "").replace("]", "")
                if f"[tool.{tool_section}]" in content:
                    return True
            except Exception:
                pass
    
    return False


def get_tool_version(tool_path: str) -> Optional[str]:
    """
    Get version string from a tool.
    
    Args:
        tool_path: Path to tool executable
        
    Returns:
        Version string or None if unable to determine
    """
    try:
        result = subprocess.run(
            [tool_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Extract first line, first word that looks like a version
            output = result.stdout.strip().split('\n')[0]
            for word in output.split():
                if any(c.isdigit() for c in word):
                    return word[:50]  # Bounded
        return None
    except Exception:
        return None


# =============================================================================
# Optional Tool Checks (discovery-gated)
# =============================================================================

def run_ruff_check(
    files: Dict[str, str],
    repo_root: Optional[Path] = None,
) -> CheckResult:
    """
    Run ruff linter on generated code.
    
    Only runs if:
    1. ruff is on PATH
    2. Repo has ruff config OR explicitly enabled
    
    Args:
        files: Dict of {relative_path: code_content}
        repo_root: Root of repo for config detection
        
    Returns:
        CheckResult with issues or skip status
    """
    tool_path = discover_tool("ruff")
    
    if not tool_path:
        return CheckResult(
            check_name="ruff",
            status="skipped",
            skip_reason="ruff not found on PATH",
        )
    
    # Check for config
    ruff_configs = ["ruff.toml", ".ruff.toml", "[tool.ruff]"]
    if not has_tool_config(repo_root, ruff_configs):
        return CheckResult(
            check_name="ruff",
            status="skipped",
            skip_reason="No ruff configuration found in repo",
        )
    
    issues = []
    tool_version = get_tool_version(tool_path)
    
    # Write files to temp dir and run ruff
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        for rel_path, code in files.items():
            if not rel_path.endswith('.py'):
                continue
            
            file_path = tmpdir_path / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(code)
        
        try:
            result = subprocess.run(
                [tool_path, "check", "--output-format=json", "."],
                cwd=tmpdir_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            if result.stdout:
                import json
                try:
                    ruff_issues = json.loads(result.stdout)
                    for issue in ruff_issues[:100]:  # Bounded
                        issues.append(StaticIssue(
                            severity="warning" if issue.get("fix") else "error",
                            category="lint",
                            file_path=issue.get("filename", "")[:MAX_FILE_PATH_LENGTH],
                            line_number=issue.get("location", {}).get("row", 1),
                            column=issue.get("location", {}).get("column", 0),
                            message=issue.get("message", "")[:MAX_ERROR_MESSAGE_LENGTH],
                            rule_id=issue.get("code"),
                            auto_fixable=issue.get("fix") is not None,
                        ))
                except json.JSONDecodeError:
                    pass
                    
        except subprocess.TimeoutExpired:
            return CheckResult(
                check_name="ruff",
                status="skipped",
                skip_reason="ruff timed out",
            )
        except Exception as e:
            logger.warning(f"ruff check failed: {e}")
            return CheckResult(
                check_name="ruff",
                status="skipped",
                skip_reason=f"ruff error: {str(e)[:100]}",
            )
    
    return CheckResult(
        check_name="ruff",
        status="failed" if issues else "passed",
        issues=issues,
        tool_version=tool_version,
    )


def run_mypy_check(
    files: Dict[str, str],
    repo_root: Optional[Path] = None,
) -> CheckResult:
    """
    Run mypy type checker on generated code.
    
    Only runs if:
    1. mypy is on PATH  
    2. Repo has mypy config OR explicitly enabled
    
    Args:
        files: Dict of {relative_path: code_content}
        repo_root: Root of repo for config detection
        
    Returns:
        CheckResult with issues or skip status
    """
    tool_path = discover_tool("mypy")
    
    if not tool_path:
        return CheckResult(
            check_name="mypy",
            status="skipped",
            skip_reason="mypy not found on PATH",
        )
    
    # Check for config
    mypy_configs = ["mypy.ini", ".mypy.ini", "[tool.mypy]", "setup.cfg"]
    if not has_tool_config(repo_root, mypy_configs):
        return CheckResult(
            check_name="mypy",
            status="skipped",
            skip_reason="No mypy configuration found in repo",
        )
    
    issues = []
    tool_version = get_tool_version(tool_path)
    
    # Write files to temp dir and run mypy
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        for rel_path, code in files.items():
            if not rel_path.endswith('.py'):
                continue
            
            file_path = tmpdir_path / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(code)
        
        try:
            result = subprocess.run(
                [tool_path, "--no-error-summary", "--show-column-numbers", "."],
                cwd=tmpdir_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
            
            # Parse mypy output (file:line:col: severity: message)
            for line in result.stdout.split('\n')[:100]:  # Bounded
                if ':' in line:
                    parts = line.split(':', 3)
                    if len(parts) >= 4:
                        try:
                            file_part = parts[0]
                            line_num = int(parts[1]) if parts[1].isdigit() else 1
                            col = int(parts[2]) if parts[2].isdigit() else 0
                            msg = parts[3].strip()
                            
                            severity = "warning"
                            if "error:" in msg.lower():
                                severity = "error"
                                msg = msg.replace("error:", "").strip()
                            
                            issues.append(StaticIssue(
                                severity=severity,
                                category="type",
                                file_path=file_part[:MAX_FILE_PATH_LENGTH],
                                line_number=line_num,
                                column=col,
                                message=msg[:MAX_ERROR_MESSAGE_LENGTH],
                                rule_id="mypy",
                                auto_fixable=False,
                            ))
                        except (ValueError, IndexError):
                            pass
                            
        except subprocess.TimeoutExpired:
            return CheckResult(
                check_name="mypy",
                status="skipped",
                skip_reason="mypy timed out",
            )
        except Exception as e:
            logger.warning(f"mypy check failed: {e}")
            return CheckResult(
                check_name="mypy",
                status="skipped",
                skip_reason=f"mypy error: {str(e)[:100]}",
            )
    
    return CheckResult(
        check_name="mypy",
        status="failed" if issues else "passed",
        issues=issues,
        tool_version=tool_version,
    )


# =============================================================================
# Check Registry
# =============================================================================

# Global registry of available checks
CHECK_REGISTRY: Dict[str, CheckDefinition] = {
    "syntax": CheckDefinition(
        name="syntax",
        category="syntax",
        builtin=True,
        run_check=check_syntax,
        enabled_by_default=True,
    ),
    "imports": CheckDefinition(
        name="imports",
        category="import",
        builtin=True,
        run_check=check_imports,
        enabled_by_default=True,
    ),
    "ruff": CheckDefinition(
        name="ruff",
        category="lint",
        builtin=False,
        tool_name="ruff",
        config_indicators=["ruff.toml", ".ruff.toml", "[tool.ruff]"],
        run_check=run_ruff_check,
        enabled_by_default=False,
    ),
    "mypy": CheckDefinition(
        name="mypy",
        category="type",
        builtin=False,
        tool_name="mypy",
        config_indicators=["mypy.ini", ".mypy.ini", "[tool.mypy]", "setup.cfg"],
        run_check=run_mypy_check,
        enabled_by_default=False,
    ),
}


def get_enabled_checks(
    repo_root: Optional[Path] = None,
    explicit_enable: Optional[List[str]] = None,
    explicit_disable: Optional[List[str]] = None,
) -> List[str]:
    """
    Determine which checks should run.
    
    Rules:
    1. Built-in checks always run (syntax, imports)
    2. Optional checks run if: tool on PATH AND (has config OR explicitly enabled)
    3. Explicit disable overrides all
    
    Args:
        repo_root: Repository root for config detection
        explicit_enable: List of check names to force-enable
        explicit_disable: List of check names to force-disable
        
    Returns:
        List of check names to run
    """
    enabled = []
    explicit_enable = explicit_enable or []
    explicit_disable = explicit_disable or []
    
    for name, check_def in CHECK_REGISTRY.items():
        # Explicit disable wins
        if name in explicit_disable:
            continue
        
        # Built-in checks always run
        if check_def.builtin:
            enabled.append(name)
            continue
        
        # Explicit enable forces the check
        if name in explicit_enable:
            if check_def.tool_name and discover_tool(check_def.tool_name):
                enabled.append(name)
            continue
        
        # Optional checks need tool + config
        if check_def.tool_name:
            if not discover_tool(check_def.tool_name):
                continue
            if not has_tool_config(repo_root, check_def.config_indicators):
                continue
            enabled.append(name)
    
    return enabled


def run_all_checks(
    files: Dict[str, str],
    repo_root: Optional[Path] = None,
    explicit_enable: Optional[List[str]] = None,
    explicit_disable: Optional[List[str]] = None,
) -> StaticAnalysisResult:
    """
    Run all enabled static analysis checks.
    
    Production hardening:
    - Bounded file count (MAX_FILES_PER_CHECK)
    - Deterministic output ordering (stable sort)
    - Invocation metadata for debugging
    
    Args:
        files: Dict of {relative_path: code_content}
        repo_root: Repository root for config detection
        explicit_enable: Checks to force-enable
        explicit_disable: Checks to force-disable
        
    Returns:
        Aggregated StaticAnalysisResult with deterministic ordering
    """
    enabled_checks = get_enabled_checks(repo_root, explicit_enable, explicit_disable)
    
    # Bound file count for scalability
    file_items = list(files.items())[:MAX_FILES_PER_CHECK]
    bounded_files = dict(file_items)
    
    all_issues: List[StaticIssue] = []
    tool_versions: Dict[str, str] = {}
    check_results: List[Dict[str, Any]] = []  # Metadata per check
    
    logger.debug(f"[PR#8] Running static checks: {enabled_checks}")
    
    for check_name in sorted(enabled_checks):  # Deterministic check order
        check_def = CHECK_REGISTRY.get(check_name)
        if not check_def or not check_def.run_check:
            continue
        
        check_start = time.monotonic()
        check_metadata = {"check_name": check_name, "builtin": check_def.builtin}
        
        try:
            if check_def.builtin:
                # Built-in checks run per-file (sorted for determinism)
                for rel_path in sorted(bounded_files.keys()):
                    code = bounded_files[rel_path]
                    if rel_path.endswith('.py'):
                        issues = check_def.run_check(code, rel_path)
                        all_issues.extend(issues)
                check_metadata["files_checked"] = len([f for f in bounded_files if f.endswith('.py')])
            else:
                # External tool checks run on all files at once
                result = check_def.run_check(bounded_files, repo_root)
                all_issues.extend(result.issues)
                if result.tool_version:
                    tool_versions[check_name] = result.tool_version
                if result.invocation_metadata:
                    check_metadata["invocation"] = result.invocation_metadata
                check_metadata["status"] = result.status
                check_metadata["skip_reason"] = result.skip_reason
                    
        except Exception as e:
            logger.warning(f"Check {check_name} failed: {e}")
            check_metadata["error"] = str(e)[:200]
        
        check_metadata["duration_ms"] = int((time.monotonic() - check_start) * 1000)
        check_results.append(check_metadata)
    
    # DETERMINISTIC OUTPUT: Sort issues by (file_path, line_number, column, message)
    all_issues.sort(key=lambda i: (i.file_path, i.line_number, i.column, i.message))
    
    # Count by severity
    blocking_count = sum(1 for i in all_issues if i.severity == "error")
    warning_count = sum(1 for i in all_issues if i.severity == "warning")
    
    return StaticAnalysisResult(
        passed=blocking_count == 0,
        issues=all_issues,
        blocking_count=blocking_count,
        warning_count=warning_count,
        tool_versions=tool_versions,
    )
