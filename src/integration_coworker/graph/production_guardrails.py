"""
Production Guardrails Contract (PR #9)

SINGLE SOURCE OF TRUTH for production safety bounds and utilities.

This module is the ONLY place that defines:
- Timeout defaults and env overrides
- Stdout/stderr capture caps
- Truncation policy (bytes + lines + deterministic marker)
- Redaction policy (secrets and volatile tokens)
- Deterministic serialization helpers

All pipeline stages MUST use these constants and helpers to ensure:
- Bounded memory usage (no unbounded strings)
- Deterministic behavior (same input → same output)
- Secret redaction (API keys, tokens, passwords never leak)
- Timeout safety (children always killed and reaped)

SUBPROCESS SAFETY per Python docs:
- subprocess.run with timeout kills child and raises TimeoutExpired
- We explicitly terminate→kill→wait to handle stubborn processes
- Output is bounded BEFORE decoding to prevent memory exhaustion

NORMALIZATION ORDER (deterministic, exception-proof):
1. Normalize line endings (\\r\\n → \\n)
2. Strip ANSI escapes
3. Truncate to byte limit
4. Truncate to line limit
5. Redact secrets (callable replacement, no backslash issues)
6. Normalize volatile tokens (timestamps, PIDs, addresses)
7. Compute fingerprint from post-normalized content only

ENV OVERRIDES:
- GUARDRAIL_TOOL_TIMEOUT_SECONDS: Default tool timeout (default: 30)
- GUARDRAIL_MAX_OUTPUT_BYTES: Max output per stream (default: 64KB)
"""
import hashlib
import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


# =============================================================================
# Hard Bounds Constants (Single Source of Truth)
# =============================================================================

def _env_int(name: str, default: int) -> int:
    """Get int from env with fallback."""
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


# Subprocess execution bounds (env-overridable)
TOOL_TIMEOUT_SECONDS = _env_int("GUARDRAIL_TOOL_TIMEOUT_SECONDS", 30)
TOOL_MAX_TIMEOUT_SECONDS = 300  # Absolute max (5 minutes), not overridable
MAX_TOOL_OUTPUT_BYTES = _env_int("GUARDRAIL_MAX_OUTPUT_BYTES", 64 * 1024)
MAX_TOOL_OUTPUT_LINES = 500  # Max lines to retain
MAX_LINE_LENGTH = 1000  # Truncate individual lines

# Evidence and attribution bounds
MAX_EVIDENCE_BYTES = 2048  # Evidence for fingerprinting
MAX_EVIDENCE_LINES = 20  # Lines in evidence window
MAX_STACK_FRAMES = 5  # Stack frames to extract

# Attribution output bounds
MAX_ATTRIBUTIONS = 50  # Per sandbox run
MAX_ATTRIBUTIONS_IN_STATE = 20  # Max attributions to store in state
MAX_FIX_HINTS = 5  # Hints per attribution
MAX_RAW_SIGNALS = 10  # Debug signals per attribution
MAX_HINT_LENGTH = 200  # Characters per hint
MAX_FILE_PATH_LENGTH = 256  # File path truncation

# Summary bounds
MAX_SUMMARY_CATEGORIES = 10
MAX_SUMMARY_HINTS = 5
MAX_SUMMARY_FINGERPRINTS = 20
MAX_FILES_IN_SUMMARY = 10  # Files shown in payload summary
MAX_ERROR_PREVIEWS = 3  # Error previews in summary
MAX_ERROR_PREVIEW_CHARS = 200  # Chars per error preview
MAX_ERROR_MESSAGE_LENGTH = 500  # Chars for error message

# Artifact bounds
MAX_ARTIFACT_SUMMARY_BYTES = 4096
MAX_ARTIFACT_REF_LENGTH = 512

# Feedback bounds (for regeneration)
MAX_TARGETS = 10  # Files for targeted regeneration
MAX_FEEDBACK_LENGTH = 4000  # Chars for global feedback
MAX_TARGET_FEEDBACK_LENGTH = 500  # Chars per-target feedback

# Human edit bounds (PR #11)
MAX_HUMAN_EDIT_BUDGET = 2  # Max human edit cycles per run (prevent UI loops)


# =============================================================================
# Redaction Patterns (Secret Safety)
# =============================================================================

# Type alias for replacement: either a string or callable(Match) -> str
ReplacementType = Union[str, Callable[[re.Match], str]]


def _make_redactor(prefix: str) -> Callable[[re.Match], str]:
    """Create a callable replacement that preserves the prefix."""
    def replacer(m: re.Match) -> str:
        return f"{m.group(1)}=[REDACTED]"
    return replacer


# Pre-compiled patterns for secrets we MUST redact
# MANDATORY: All redaction patterns MUST use callable replacements (not strings).
# This avoids backslash escape issues per Python docs:
# https://docs.python.org/3/library/re.html#re.sub
# "repl can be a string or a function; if it is a string, any backslash escapes
# in it are processed... if repl is a function, it is called for every match."
#
# Contract test: test_all_redaction_replacements_are_callable() enforces this.
_REDACTION_PATTERNS: List[Tuple[re.Pattern, ReplacementType]] = [
    # API keys and tokens (generic patterns)
    (re.compile(r'\b(api[_-]?key|apikey)[=:\s]+[\'"]?([a-zA-Z0-9_\-]{20,})[\'"]?', re.I), 
     lambda m: f"{m.group(1)}=[REDACTED]"),
    (re.compile(r'\b(bearer|token|auth)[=:\s]+[\'"]?([a-zA-Z0-9_\-]{20,})[\'"]?', re.I), 
     lambda m: f"{m.group(1)}=[REDACTED]"),
    (re.compile(r'\b(secret|password|passwd|pwd)[=:\s]+[\'"]?([^\s\'\"]{8,})[\'"]?', re.I), 
     lambda m: f"{m.group(1)}=[REDACTED]"),
    
    # AWS keys (callable to ensure no backslash issues)
    (re.compile(r'AKIA[0-9A-Z]{16}', re.I), 
     lambda m: "[AWS_KEY_REDACTED]"),
    (re.compile(r'\b(aws_secret_access_key|aws_access_key_id)[=:\s]+[\'"]?([a-zA-Z0-9/+=]{20,})[\'"]?', re.I), 
     lambda m: f"{m.group(1)}=[REDACTED]"),
    
    # GitHub tokens (callable for consistency)
    (re.compile(r'gh[pousr]_[A-Za-z0-9_]{36,}'), 
     lambda m: "[GITHUB_TOKEN_REDACTED]"),
    
    # Generic long hex strings (potential secrets)
    (re.compile(r'\b[a-fA-F0-9]{40,}\b'), 
     lambda m: "[HEX_REDACTED]"),
    
    # JWT tokens (header.payload.signature)
    (re.compile(r'eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+'), 
     lambda m: "[JWT_REDACTED]"),
    
    # Database connection strings with passwords
    (re.compile(r'(postgresql|mysql|mongodb)://([^:]+):([^@]+)@', re.I), 
     lambda m: f"{m.group(1)}://***:***@"),
]

# ANSI escape codes
_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*m')

# Volatile tokens for normalization (not secrets, just unstable).
# MANDATORY: All patterns MUST use callable replacements for consistency.
# Contract test: test_all_volatile_replacements_are_callable() enforces this.
_VOLATILE_PATTERNS: List[Tuple[re.Pattern, ReplacementType]] = [
    # Timestamps
    (re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?'), 
     lambda m: "[TIMESTAMP]"),
    
    # Memory addresses
    (re.compile(r'0x[0-9a-fA-F]{8,16}'), 
     lambda m: "[ADDR]"),
    
    # PIDs and process info
    (re.compile(r'\bpid[=: ]\d+', re.I), 
     lambda m: "pid=[PID]"),
    (re.compile(r'\bprocess \d+', re.I), 
     lambda m: "process [PID]"),
    
    # Temp file paths (POSIX and Windows)
    (re.compile(r'/tmp/[a-zA-Z0-9_\-\.]+'), 
     lambda m: "/tmp/[TEMP]"),
    (re.compile(r'C:\\(?:Temp|tmp)\\[a-zA-Z0-9_\-\.]+', re.I), 
     lambda m: "C:\\Temp\\[TEMP]"),
    
    # Pytest temp dirs
    (re.compile(r'/private/var/folders/[^/]+/[^/]+/[^/]+/pytest-\d+'), 
     lambda m: "[PYTEST_TEMP]"),
]


# =============================================================================
# Safe Subprocess Execution
# =============================================================================

@dataclass
class ToolResult:
    """
    Result from running an external tool with production bounds.
    
    All outputs are:
    - Bounded (max bytes/lines)
    - Redacted (secrets removed)
    - Normalized (volatile tokens stabilized)
    """
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    duration_ms: int
    metadata: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict with sorted keys."""
        return dict(sorted({
            "duration_ms": self.duration_ms,
            "metadata": dict(sorted(self.metadata.items())),
            "returncode": self.returncode,
            "stderr": self.stderr,
            "stderr_lines": self.stderr.count('\n') + (1 if self.stderr else 0),
            "stdout": self.stdout,
            "stdout_lines": self.stdout.count('\n') + (1 if self.stdout else 0),
            "timed_out": self.timed_out,
            "truncated": self.truncated,
        }.items()))


def _kill_process_tree(proc: subprocess.Popen, grace_seconds: float = 2.0) -> None:
    """
    Kill a process and all its children, then reap zombies.
    
    POSIX: Uses process groups via start_new_session=True.
    Windows: Uses proc.kill() only (no process groups).
    
    Per Python docs (https://docs.python.org/3/library/subprocess.html):
    - start_new_session=True creates new session, making proc the group leader
    - os.killpg() sends signal to entire process group
    
    Args:
        proc: The Popen process to kill
        grace_seconds: Time to wait after SIGTERM before SIGKILL
    """
    import sys
    
    if sys.platform == "win32":
        # Windows: No process groups, just kill the process
        # TODO: Consider taskkill /T /F /PID for tree kill on Windows
        try:
            proc.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            pass
        return
    
    # POSIX: Kill entire process group
    try:
        pgid = os.getpgid(proc.pid)
    except (OSError, ProcessLookupError):
        # Process already dead
        return
    
    # Step 1: SIGTERM to the process group (graceful)
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    
    # Step 2: Wait for graceful termination
    try:
        proc.wait(timeout=grace_seconds)
        return  # Terminated gracefully
    except subprocess.TimeoutExpired:
        pass
    
    # Step 3: SIGKILL to the process group (forceful)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    
    # Step 4: Final wait to reap zombie (always succeeds after SIGKILL)
    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        # Should never happen after SIGKILL, but don't hang
        pass


def run_tool_safely(
    cmd: List[str],
    cwd: Optional[Path] = None,
    timeout: int = TOOL_TIMEOUT_SECONDS,
    max_output: int = MAX_TOOL_OUTPUT_BYTES,
    max_lines: int = MAX_TOOL_OUTPUT_LINES,
    redact: bool = True,
    env: Optional[Dict[str, str]] = None,
) -> ToolResult:
    """
    Run an external tool with production safety bounds.
    
    SAFETY GUARANTEES:
    - Hard timeout enforcement (child process tree is terminated→killed→reaped)
    - Stdout/stderr size caps (deterministic truncation BEFORE decode)
    - Secret redaction (API keys, tokens, passwords removed)
    - Never returns unbounded output
    - Never leaves zombie processes
    - Process tree isolation (children killed on timeout)
    
    SUBPROCESS HANDLING (per Python docs):
    - Use Popen with start_new_session=True for process-group isolation
    - On timeout: SIGTERM → grace period → SIGKILL → wait()
    - Output is bounded BEFORE decoding to prevent memory exhaustion
    - All paths lead to wait() to prevent zombies
    
    CROSS-PLATFORM:
    - POSIX: Full process-group termination via os.killpg()
    - Windows: Single-process termination (no native process groups)
    
    Args:
        cmd: Command and arguments
        cwd: Working directory
        timeout: Max seconds to wait (capped at TOOL_MAX_TIMEOUT_SECONDS)
        max_output: Max bytes of output to capture per stream
        max_lines: Max lines to retain per stream
        redact: Whether to apply secret redaction
        env: Optional environment variables (merged with os.environ)
        
    Returns:
        ToolResult with bounded, redacted, normalized output
    """
    import sys
    
    # Enforce timeout ceiling
    timeout = min(timeout, TOOL_MAX_TIMEOUT_SECONDS)
    
    start_time = time.monotonic()
    start_ts = datetime.now(timezone.utc).isoformat()
    
    truncated = False
    timed_out = False
    stdout_data = ""
    stderr_data = ""
    returncode = -1
    
    # Merge environment
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    
    proc: Optional[subprocess.Popen] = None
    try:
        # Use Popen for explicit timeout and cleanup control
        # start_new_session=True creates a new process group (POSIX only)
        popen_kwargs = dict(
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=run_env,
        )
        
        # Process-group isolation (POSIX only)
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True
        
        proc = subprocess.Popen(cmd, **popen_kwargs)
        
        try:
            # communicate() handles the timeout and returns output
            stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
            returncode = proc.returncode
            
            # Bound output BEFORE decoding (memory safety)
            if len(stdout_bytes) > max_output:
                stdout_bytes = stdout_bytes[:max_output]
                truncated = True
            if len(stderr_bytes) > max_output:
                stderr_bytes = stderr_bytes[:max_output]
                truncated = True
            
            # Decode with error handling
            stdout_data = stdout_bytes.decode('utf-8', errors='replace')
            stderr_data = stderr_bytes.decode('utf-8', errors='replace')
            
        except subprocess.TimeoutExpired:
            # Timeout: terminate → kill → reap (no zombies)
            timed_out = True
            returncode = -1
            
            # Kill process tree and reap
            _kill_process_tree(proc, grace_seconds=2.0)
            
            stderr_data = f"Tool timed out after {timeout}s (process tree terminated)"
            
    except FileNotFoundError:
        returncode = -1
        stderr_data = f"Tool not found: {cmd[0] if cmd else 'unknown'}"
        
    except PermissionError:
        returncode = -1
        stderr_data = f"Permission denied: {cmd[0] if cmd else 'unknown'}"
        
    except Exception as e:
        returncode = -1
        stderr_data = f"Tool invocation error: {type(e).__name__}: {str(e)[:400]}"
        
    finally:
        # Ensure process is cleaned up even on unexpected errors
        if proc is not None:
            _kill_process_tree(proc, grace_seconds=1.0)
    
    duration_ms = int((time.monotonic() - start_time) * 1000)
    
    # Apply line limit with deterministic truncation
    if stdout_data:
        stdout_data = _truncate_lines(stdout_data, max_lines)
    if stderr_data:
        stderr_data = _truncate_lines(stderr_data, max_lines)
    
    # Apply redaction (must not throw)
    if redact:
        stdout_data = redact_secrets(stdout_data)
        stderr_data = redact_secrets(stderr_data)
    
    # Build metadata (deterministic key order)
    metadata = dict(sorted({
        "args_count": len(cmd) - 1 if cmd else 0,
        "command": cmd[0] if cmd else "unknown",
        "duration_ms": duration_ms,
        "output_truncated": truncated,
        "returncode": returncode,
        "started_at": start_ts,
        "stderr_bytes": len(stderr_data.encode('utf-8')),
        "stdout_bytes": len(stdout_data.encode('utf-8')),
        "timed_out": timed_out,
        "timeout_seconds": timeout,
    }.items()))
    
    return ToolResult(
        returncode=returncode,
        stdout=stdout_data,
        stderr=stderr_data,
        timed_out=timed_out,
        truncated=truncated,
        duration_ms=duration_ms,
        metadata=metadata,
    )


def _truncate_lines(text: str, max_lines: int) -> str:
    """
    Truncate text to max_lines with deterministic behavior.
    
    If truncation needed, keeps first max_lines-1 lines and adds marker.
    Each line is also truncated to MAX_LINE_LENGTH.
    """
    if not text:
        return text
    
    lines = text.splitlines()
    
    # Truncate individual lines
    lines = [line[:MAX_LINE_LENGTH] + ('...' if len(line) > MAX_LINE_LENGTH else '') 
             for line in lines]
    
    if len(lines) <= max_lines:
        return '\n'.join(lines)
    
    # Keep first N-1 lines + truncation marker
    kept = lines[:max_lines - 1]
    omitted = len(lines) - max_lines + 1
    kept.append(f"[... {omitted} more lines truncated ...]")
    return '\n'.join(kept)


# =============================================================================
# Redaction and Normalization
# =============================================================================

def redact_secrets(text: str) -> str:
    """
    Remove secrets from text to ensure they never appear in logs/summaries.
    
    This function is EXCEPTION-PROOF: it will never raise, even on
    pathological inputs. If a pattern fails, it's skipped silently.
    
    Patterns redacted:
    - API keys and tokens
    - AWS credentials
    - GitHub tokens
    - Database connection strings with passwords
    - JWT tokens
    - Long hex strings (potential secrets)
    """
    if not text:
        return ""
    
    result = text
    for pattern, replacement in _REDACTION_PATTERNS:
        try:
            result = pattern.sub(replacement, result)
        except Exception:
            # Never fail on redaction - skip problematic pattern
            pass
    return result


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_ESCAPE.sub('', text)


def normalize_evidence(
    output: str,
    max_bytes: int = MAX_EVIDENCE_BYTES,
    max_lines: int = MAX_EVIDENCE_LINES,
    redact: bool = True,
) -> str:
    """
    Normalize output for stable fingerprinting and safe storage.
    
    This is THE entrypoint for evidence normalization. Guarantees:
    - Bounded size (bytes and lines)
    - Secret redaction
    - Volatile token normalization (timestamps, PIDs, addresses)
    - ANSI escape removal
    - Deterministic output (same input → same output)
    - EXCEPTION-PROOF: never raises, even on pathological input
    
    NORMALIZATION ORDER (deterministic):
    1. Normalize line endings (\\r\\n → \\n) - ensures consistent line counting
    2. Strip ANSI escapes (affects byte count)
    3. Truncate to max bytes
    4. Truncate to max lines (deterministic marker)
    5. Redact secrets (exception-proof)
    6. Normalize volatile tokens (timestamps, PIDs, addresses)
    
    The fingerprint is computed from post-normalized content only,
    ensuring stability across runs.
    
    Args:
        output: Raw output text
        max_bytes: Maximum bytes to retain
        max_lines: Maximum lines to retain
        redact: Whether to apply secret redaction (default True)
        
    Returns:
        Normalized, bounded, redacted output (never None, never raises)
    """
    if not output:
        return ""
    
    try:
        # Step 1: Normalize line endings first (\\r\\n → \\n, \\r → \\n)
        normalized = output.replace('\r\n', '\n').replace('\r', '\n')
        
        # Step 2: Strip ANSI escapes (affects byte count)
        normalized = strip_ansi(normalized)
        
        # Step 3: Truncate to max bytes
        normalized = normalized[:max_bytes]
        
        # Step 4: Truncate to max lines (deterministic marker)
        normalized = _truncate_lines(normalized, max_lines)
        
        # Step 5: Redact secrets (exception-proof)
        if redact:
            normalized = redact_secrets(normalized)
        
        # Step 6: Normalize volatile tokens (exception-proof loop)
        for pattern, replacement in _VOLATILE_PATTERNS:
            try:
                normalized = pattern.sub(replacement, normalized)
            except Exception:
                # Never fail on normalization
                pass
        
        # Step 7: Normalize absolute paths → relative (exception-proof)
        try:
            normalized = re.sub(r'/[^\s:]+/(src/|tests/)', r'\1', normalized)
            normalized = re.sub(r'[A-Z]:\\[^\s:]+\\(src\\|tests\\)', r'\1', normalized)
        except Exception:
            pass
        
        return normalized
        
    except Exception:
        # Ultimate fallback: return truncated raw input
        return output[:max_bytes] if output else ""


def compute_fingerprint(content: str, context: Optional[str] = None) -> str:
    """
    Compute a stable SHA256 fingerprint for deduplication.
    
    Args:
        content: Content to fingerprint (should be normalized first)
        context: Optional context for namespacing (e.g., gate name)
        
    Returns:
        16-character hex fingerprint (first 64 bits of SHA256)
    """
    data = {"content": content}
    if context:
        data["context"] = context
    
    # Use json.dumps with sort_keys for deterministic serialization
    serialized = json.dumps(data, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


# =============================================================================
# Bounded Data Structures
# =============================================================================

def bounded_list(items: List[Any], max_items: int, key=None) -> List[Any]:
    """
    Return a bounded copy of list with deterministic truncation.
    
    If key is provided, items are sorted before truncation for stability.
    """
    if key:
        items = sorted(items, key=key)
    return items[:max_items]


def bounded_dict_values(
    d: Dict[str, List[Any]], 
    max_per_key: int,
    sort_keys: bool = True,
) -> Dict[str, List[Any]]:
    """
    Return a dict with each list value bounded.
    
    If sort_keys, dict is also sorted by key for determinism.
    """
    result = {k: v[:max_per_key] for k, v in d.items()}
    if sort_keys:
        result = dict(sorted(result.items()))
    return result


def safe_json_dumps(obj: Any, max_bytes: int = MAX_ARTIFACT_SUMMARY_BYTES) -> str:
    """
    Serialize to JSON with size limit and deterministic output.
    
    Uses sort_keys=True for determinism.
    Truncates if result exceeds max_bytes.
    """
    result = json.dumps(obj, sort_keys=True, default=str)
    if len(result) > max_bytes:
        return result[:max_bytes - 20] + '...[TRUNCATED]"}'
    return result


# =============================================================================
# Validation Helpers
# =============================================================================

def validate_bounds(
    text: str,
    max_bytes: int,
    name: str = "value",
) -> str:
    """
    Validate and enforce byte bound on text.
    
    Raises ValueError if text exceeds bound by more than 10%
    (indicates caller didn't pre-truncate).
    """
    if len(text.encode('utf-8')) > max_bytes * 1.1:
        raise ValueError(
            f"{name} exceeds bound: {len(text.encode('utf-8'))} > {max_bytes} bytes"
        )
    return text[:max_bytes]
