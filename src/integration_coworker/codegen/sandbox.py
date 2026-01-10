"""
Codegen Sandbox Execution Module

Executes generated code in an isolated environment to validate quality:
1. Creates temp directory with artifacts
2. Creates isolated venv
3. Installs dependencies
4. Runs ruff as hard gate
5. Runs mypy as hard gate  
6. Runs pytest (if tests exist)

Per ADR-0005: Production-Grade Codegen Quality Gates

Usage:
    from integration_coworker.codegen.sandbox import execute_in_sandbox, SandboxConfig
    
    config = SandboxConfig(
        python_version="3.11",
        enable_ruff=True,
        enable_mypy=True,
        enable_pytest=True,
    )
    
    result = await execute_in_sandbox(
        artifacts=[
            ArtifactFile("src/module.py", "def hello(): return 'world'"),
            ArtifactFile("tests/test_module.py", "def test_hello(): assert True"),
        ],
        dependencies=["pytest"],
        config=config,
    )
    
    if not result.success:
        print(result.summary)
        for gate in result.gate_results:
            if not gate.passed:
                print(f"FAILED: {gate.name}\\n{gate.output}")
"""
import asyncio
import contextvars
import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from integration_coworker.validation import pytest_conftest_template

logger = logging.getLogger(__name__)


# =============================================================================
# Structured Logging Infrastructure
# =============================================================================
#
# Log Schema v1.0 - Stable JSON-line format for all sandbox events
#
# REQUIRED FIELDS (always present):
#   ts            - ISO8601 UTC timestamp
#   level         - Log level (DEBUG, INFO, WARNING, ERROR)
#   event         - Event name from fixed taxonomy below
#   run_id        - Unique run identifier (uuid4 prefix)
#   sandbox_id    - Sandbox identifier (sandbox-<run_id>)
#   sandbox_dir   - Resolved absolute path to sandbox directory
#   gate          - Gate name or null if not in gate context
#   pid           - Process ID
#
# RECOMMENDED FIELDS (when applicable):
#   artifact_count, dependency_count, command, cwd, timeout_s,
#   duration_ms, exit_code, signal, stdout_bytes, stderr_bytes,
#   stdout_sha256, stderr_sha256
#
# EVENT TAXONOMY (fixed set - do not deviate):
#   sandbox.create                  - Sandbox directory created
#   sandbox.write_artifacts.start   - Starting to write artifacts
#   sandbox.write_artifacts.done    - Finished writing artifacts
#   stub.write.start                - Starting to write stub modules
#   stub.write.done                 - Finished writing stub modules
#   stub.verify.ok                  - Stub verification passed
#   stub.verify.fail                - Stub verification failed
#   gate.start                      - Gate subprocess starting
#   gate.done                       - Gate subprocess completed
#   gate.timeout                    - Gate subprocess timed out
#   gate.error                      - Gate subprocess errored
#   sandbox.result.ok               - All gates passed
#   sandbox.result.fail             - One or more gates failed
#   sandbox.cleanup                 - Sandbox cleanup (with reason)
#   failure_bundle.written          - FAILURE_BUNDLE.json written
#
# =============================================================================

# Context variables for automatic log correlation
_run_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("run_id", default=None)
_sandbox_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("sandbox_id", default=None)
_sandbox_dir: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("sandbox_dir", default=None)
_gate_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("gate_name", default=None)

# Log level name mapping
_LEVEL_NAMES = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class SandboxLoggerAdapter(logging.LoggerAdapter):
    """
    Logger adapter that automatically injects sandbox context into all log records.
    
    This ensures every log line carries run_id, sandbox_id, and gate for
    easy correlation and filtering in log aggregation systems.
    
    Per Python docs: LoggerAdapter is the clean way to inject context fields.
    https://docs.python.org/3/library/logging.html#loggeradapter-objects
    """
    
    def process(self, msg: str, kwargs: Any) -> Tuple[str, Any]:
        extra = kwargs.get("extra", {})
        # Inject context variables
        extra.setdefault("run_id", _run_id.get())
        extra.setdefault("sandbox_id", _sandbox_id.get())
        extra.setdefault("sandbox_dir", _sandbox_dir.get())
        extra.setdefault("gate", _gate_name.get())
        extra.setdefault("pid", os.getpid())
        kwargs["extra"] = extra
        return msg, kwargs


def _get_context_logger() -> SandboxLoggerAdapter:
    """Get a logger adapter with current context injected."""
    return SandboxLoggerAdapter(logger, {})


def _sha256_prefix(content: str | bytes, length: int = 16) -> str:
    """Compute SHA256 prefix of content."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:length]


def _log_event(
    event: str,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """
    Emit a structured log event in stable JSON-line format.
    
    All events follow the Log Schema v1.0 with required fields always present.
    Events use the fixed taxonomy (see module header).
    
    Args:
        event: Event name from taxonomy (e.g., "sandbox.create", "gate.start")
        level: Log level (default INFO)
        **fields: Additional structured fields (recommended: duration_ms, exit_code, etc.)
    """
    # Build payload with REQUIRED fields first (always present)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": _LEVEL_NAMES.get(level, "INFO"),
        "event": event,
        "run_id": _run_id.get(),
        "sandbox_id": _sandbox_id.get(),
        "sandbox_dir": _sandbox_dir.get(),
        "gate": _gate_name.get(),
        "pid": os.getpid(),
    }
    
    # Add recommended/optional fields
    payload.update(fields)
    
    # Emit as JSON-line (single line, no indentation)
    json_line = json.dumps(payload, default=str, separators=(",", ":"))
    
    # Log with structured payload attached for handlers that want it
    ctx_logger = _get_context_logger()
    ctx_logger.log(level, json_line, extra={"structured_payload": payload})


def _persist_gate_output(
    sandbox_dir: str,
    gate_name: str,
    stdout: str,
    stderr: str,
) -> Tuple[Path, Path]:
    """
    Persist full gate stdout/stderr to files for forensic analysis.
    
    Args:
        sandbox_dir: Sandbox directory path
        gate_name: Name of the gate (e.g., "ruff", "mypy", "pytest")
        stdout: Full stdout content
        stderr: Full stderr content
        
    Returns:
        Tuple of (stdout_path, stderr_path)
    """
    logs_dir = Path(sandbox_dir) / "logs"
    logs_dir.mkdir(exist_ok=True)
    
    stdout_path = logs_dir / f"{gate_name}.out.txt"
    stderr_path = logs_dir / f"{gate_name}.err.txt"
    
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    
    return stdout_path, stderr_path


def _write_failure_bundle(
    sandbox_dir: str,
    gate_results: List["GateResult"],
    artifacts: List["ArtifactFile"],
    config: "SandboxConfig",
) -> Path:
    """
    Write FAILURE_BUNDLE.json containing everything needed to reproduce a failure.
    
    This makes "bug report equals a folder path" real.
    
    Args:
        sandbox_dir: Sandbox directory path
        gate_results: List of gate results
        artifacts: List of artifacts that were written
        config: Sandbox configuration
        
    Returns:
        Path to FAILURE_BUNDLE.json
    """
    bundle: Dict[str, Any] = {
        "bundle_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": _run_id.get(),
        "sandbox_id": _sandbox_id.get(),
    }
    
    # Include RUN_METADATA.json content
    metadata_path = Path(sandbox_dir) / "RUN_METADATA.json"
    if metadata_path.exists():
        try:
            bundle["run_metadata"] = json.loads(metadata_path.read_text())
        except Exception as e:
            bundle["run_metadata_error"] = str(e)
    
    # Gate summaries
    bundle["gate_summaries"] = [
        {
            "name": g.name,
            "passed": g.passed,
            "return_code": g.return_code,
            "duration_ms": g.duration_ms,
            "output_preview": g.output[:500] if g.output else "",
        }
        for g in gate_results
    ]
    
    # Log file pointers
    logs_dir = Path(sandbox_dir) / "logs"
    if logs_dir.exists():
        bundle["log_files"] = [str(p.relative_to(sandbox_dir)) for p in logs_dir.glob("*")]
    
    # Stub inventory snapshot
    clients_dir = Path(sandbox_dir) / "src" / "clients"
    if clients_dir.exists():
        bundle["stub_inventory"] = []
        for stub_file in sorted(clients_dir.glob("*.py")):
            try:
                content = stub_file.read_text()
                bundle["stub_inventory"].append({
                    "name": stub_file.name,
                    "bytes": len(content),
                    "sha256_prefix": _sha256_prefix(content),
                    "preview": content[:200] if len(content) < 500 else content[:200] + "...",
                })
            except Exception as e:
                bundle["stub_inventory"].append({
                    "name": stub_file.name,
                    "error": str(e),
                })
    
    # Artifact manifest
    bundle["artifact_manifest"] = [
        {
            "path": a.path,
            "bytes": len(a.content),
            "sha256_prefix": _sha256_prefix(a.content),
        }
        for a in artifacts
    ]
    
    # Config snapshot (excluding sensitive fields)
    bundle["config"] = {
        "python_version": config.python_version,
        "enable_ruff": config.enable_ruff,
        "enable_mypy": config.enable_mypy,
        "enable_bandit": config.enable_bandit,
        "enable_pytest": config.enable_pytest,
        "enable_coverage": config.enable_coverage,
        "enable_contract_tests": config.enable_contract_tests,
        "enable_live_tests": config.enable_live_tests,
        "timeout_seconds": config.timeout_seconds,
    }
    
    bundle_path = Path(sandbox_dir) / "FAILURE_BUNDLE.json"
    bundle_path.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
    
    _log_event("failure_bundle.written", path=str(bundle_path), gates_failed=sum(1 for g in gate_results if not g.passed))
    
    return bundle_path


# =============================================================================
# Runtime Provenance (Bug #101 regression-proofing)
# =============================================================================

def _get_run_provenance(sandbox_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Collect runtime provenance for deterministic debugging.
    
    This stamps every sandbox run with enough info to trace which code
    version produced it, eliminating "old code vs new code" ambiguity.
    
    Args:
        sandbox_dir: Optional sandbox directory path to record
    
    Returns:
        Dict with git_commit, python_version, module_path, stub_source_hash,
        timestamp, pid, hostname, and sandbox paths.
    """
    import hashlib
    import inspect
    
    provenance: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "python_version": sys.version,
    }
    
    # Git commit (best-effort - git may not exist or cwd may not be a repo)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent,
        )
        if result.returncode == 0:
            provenance["git_commit"] = result.stdout.strip()
            provenance["git_error"] = None
        else:
            provenance["git_commit"] = None
            provenance["git_error"] = f"git returned {result.returncode}: {result.stderr.strip()}"
    except FileNotFoundError:
        provenance["git_commit"] = None
        provenance["git_error"] = "git not found in PATH"
    except Exception as e:
        provenance["git_commit"] = None
        provenance["git_error"] = f"{type(e).__name__}: {e}"
    
    # Module path (resolved to defuse symlink confusion)
    try:
        import integration_coworker.codegen.sandbox as sandbox_module
        provenance["module_path"] = str(Path(sandbox_module.__file__).resolve())
    except Exception:
        provenance["module_path"] = None
    
    # Sandbox paths: both as-used and resolved (defuses /var vs /private/var)
    if sandbox_dir:
        provenance["sandbox_dir"] = sandbox_dir
        try:
            provenance["sandbox_dir_resolved"] = str(Path(sandbox_dir).resolve())
        except Exception:
            provenance["sandbox_dir_resolved"] = sandbox_dir
    
    # Hash of _write_stub_modules source (compact, redaction-safe)
    try:
        source = inspect.getsource(_write_stub_modules)
        provenance["stub_source_hash"] = hashlib.sha256(source.encode()).hexdigest()[:16]
        provenance["stub_source_lines"] = len(source.splitlines())
    except Exception as e:
        provenance["stub_source_hash"] = None
        provenance["stub_source_error"] = f"{type(e).__name__}"
    
    return provenance


def _write_run_metadata(sandbox_dir: str) -> Path:
    """
    Write RUN_METADATA.json to sandbox for postmortem debugging.
    
    This is the CANONICAL source of truth for which code version
    produced a given sandbox. Demo logs and SANDBOX_REPORT.md should
    also include this info, but RUN_METADATA.json is authoritative.
    
    Args:
        sandbox_dir: Path to sandbox directory
        
    Returns:
        Path to RUN_METADATA.json file
    """
    provenance = _get_run_provenance(sandbox_dir=sandbox_dir)
    metadata_path = Path(sandbox_dir) / "RUN_METADATA.json"
    
    # Atomic write with fsync for durability
    tmp_path = metadata_path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(provenance, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, metadata_path)
    
    commit = provenance.get('git_commit')
    commit_str = commit[:8] if commit else 'unknown'
    logger.debug(f"Wrote RUN_METADATA.json: commit={commit_str}")
    return metadata_path


@dataclass
class ArtifactFile:
    """A generated code artifact to validate."""
    path: str  # Relative path within sandbox, e.g. "src/module.py"
    content: str
    
    def __post_init__(self):
        # Normalize path separators
        self.path = self.path.replace("\\", "/")


@dataclass
class GateResult:
    """Result of a single quality gate."""
    name: str
    passed: bool
    output: str
    return_code: int
    duration_ms: int


@dataclass
class SandboxResult:
    """Complete result of sandbox execution."""
    success: bool
    gate_results: List[GateResult]
    sandbox_dir: Optional[str]  # None if cleaned up
    summary: str
    # V38-002: Fixed artifacts after lint auto-fixes (ruff --fix)
    # Maps relative path -> fixed content
    fixed_artifacts: Dict[str, str] = field(default_factory=dict)
    
    @property
    def failed_gates(self) -> List[GateResult]:
        return [g for g in self.gate_results if not g.passed]
    
    @property
    def passed_gates(self) -> List[GateResult]:
        return [g for g in self.gate_results if g.passed]


@dataclass
class SandboxConfig:
    """Configuration for sandbox execution."""
    python_version: str = "3.11"
    enable_ruff: bool = True
    enable_mypy: bool = True
    enable_bandit: bool = True
    enable_pytest: bool = True
    timeout_seconds: int = 300
    cleanup_on_success: bool = True
    cleanup_on_failure: bool = False  # Keep for debugging
    extra_ruff_rules: List[str] = field(default_factory=list)
    mypy_strict: bool = False
    bandit_config_path: Optional[str] = None  # Path to pyproject.toml or bandit.yaml
    
    # Coverage gate configuration (per user requirement A2)
    enable_coverage: bool = False
    coverage_target: Optional[str] = None  # REQUIRED if enable_coverage=True (e.g., "src")
    coverage_fail_under: int = 60  # Minimum coverage percentage
    coverage_report: str = "term-missing"  # Coverage report format
    
    # Profile-controlled behaviors
    fail_on_no_tests: bool = False  # If True, exit code 5 is failure (production)
    
    # Paths relative to sandbox root
    src_dir: str = "src"
    tests_dir: str = "tests"
    
    # PERF V27-001-004: Venv caching for faster sandbox execution
    # When enabled, creates a cached venv based on dependency hash
    enable_venv_cache: bool = True
    venv_cache_dir: Optional[str] = None  # Default: ~/.cache/integration-coworker/venvs
    venv_cache_max_age_hours: int = 24  # Prune cached venvs older than this
    venv_cache_max_entries: int = 10  # Maximum number of cached venvs to keep

    # Contract validation (Schemathesis + Prism)
    enable_contract_tests: bool = False
    contract_spec_path: Optional[str] = None  # Path to spec file (OR use contract_spec_dict)
    contract_spec_dict: Optional[Dict[str, Any]] = None  # Spec as dict (materialized to file in sandbox)
    contract_base_url: Optional[str] = None
    contract_prism_port: int = 4010
    contract_checks: List[str] = field(default_factory=lambda: [
        "status_code_conformance",
        "content_type_conformance",
    ])
    contract_workers: int = 1

    # Live integration tests (real API calls)
    # When enabled, sets VALIDATION_PROFILE=live and allows network access
    enable_live_tests: bool = False
    live_host_allowlist: List[str] = field(default_factory=list)
    # Environment variables to pass to sandbox (e.g., API keys)
    # WARNING: These are passed to the sandbox environment - use test keys only!
    live_env_vars: Dict[str, str] = field(default_factory=dict)
    
    # V45-001: Policy mode for code generation
    # - "inline": Generated code is self-contained, no runtime dependency
    # - "runtime": Generated code imports from integration_coworker_runtime package
    # When "runtime", sandbox automatically installs the runtime package
    policy_mode: str = "inline"
    
    def __post_init__(self):
        """Validate configuration."""
        if self.enable_coverage and not self.coverage_target:
            raise ValueError(
                "coverage_target is required when enable_coverage=True. "
                "Specify the module/path to measure coverage (e.g., 'src')."
            )

        if self.enable_contract_tests and not self.contract_spec_path and not self.contract_spec_dict:
            raise ValueError(
                "contract_spec_path or contract_spec_dict is required when enable_contract_tests=True"
            )

        if self.enable_live_tests and not self.live_host_allowlist:
            raise ValueError(
                "live_host_allowlist is required when enable_live_tests=True. "
                "Specify allowed hosts (e.g., ['api.stripe.com', 'api.twilio.com'])."
            )


# =============================================================================
# V45-001: Runtime Package Installation for policy_mode="runtime"
# =============================================================================
#
# When policy_mode is "runtime", generated code imports from the
# integration_coworker_runtime package. The sandbox must install this
# package to run tests successfully.
#
# Package Location Priority:
# 1. Local development: packages/integration-coworker-runtime (editable install)
# 2. Published package: pip install integration-coworker-runtime
#
# =============================================================================

def _get_runtime_package_install_spec() -> Optional[str]:
    """
    Get the pip install spec for integration-coworker-runtime.
    
    Returns the appropriate install spec based on available sources:
    - Local dev: path to packages/integration-coworker-runtime for editable install
    - Published: "integration-coworker-runtime" (from PyPI or local wheel)
    
    Returns:
        pip install spec string, or None if package cannot be located
    """
    # Strategy 1: Check for local development package
    # This supports the monorepo structure where the runtime lives in packages/
    current_file = Path(__file__).resolve()
    
    # Navigate up to find project root (contains packages/)
    # sandbox.py is at src/integration_coworker/codegen/sandbox.py
    project_root = current_file.parent.parent.parent.parent
    local_runtime_path = project_root / "packages" / "integration-coworker-runtime"
    
    if local_runtime_path.exists() and (local_runtime_path / "pyproject.toml").exists():
        # Use editable install for development - allows live code changes
        logger.debug(f"[V45-001] Using local runtime package: {local_runtime_path}")
        return str(local_runtime_path)
    
    # Strategy 2: Fall back to published package name
    # This is used in production or when the package is installed from PyPI
    logger.debug("[V45-001] Using published runtime package: integration-coworker-runtime")
    return "integration-coworker-runtime"


# =============================================================================
# PERF V27-001-004: Venv Caching Infrastructure
# =============================================================================
# 
# Problem: Creating a fresh venv and installing dependencies for each sandbox
# execution takes 10-30 seconds, contributing significantly to the 300s+ runtimes.
#
# Solution: Cache venvs by dependency hash. If the same set of dependencies
# is requested, copy the cached venv instead of creating a new one.
#
# Cache Key: SHA256 of sorted, normalized dependency list + Python version
#
# Cache Structure:
#   ~/.cache/integration-coworker/venvs/
#     {hash}/
#       .venv/           - The cached venv
#       deps.json        - Original dependencies (for debugging)
#       created_at.txt   - ISO timestamp for LRU eviction
#
# =============================================================================

# Global lock for cache operations (prevents race conditions)
_venv_cache_lock = asyncio.Lock()


def _get_venv_cache_dir(config: SandboxConfig) -> Path:
    """Get the venv cache directory, creating it if needed."""
    if config.venv_cache_dir:
        cache_dir = Path(config.venv_cache_dir)
    else:
        cache_dir = Path.home() / ".cache" / "integration-coworker" / "venvs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _compute_venv_cache_key(
    dependencies: List[str],
    python_version: str,
    tooling_deps: List[str],
) -> str:
    """
    Compute a cache key for a venv based on dependencies.
    
    Args:
        dependencies: User-specified dependencies
        python_version: Python version (e.g., "3.11")
        tooling_deps: Gate tooling deps (ruff, mypy, etc.)
        
    Returns:
        SHA256 hash (first 16 chars) of normalized dependency set
    """
    # Combine and normalize all dependencies
    all_deps = sorted(set(d.lower().strip() for d in (dependencies + tooling_deps)))
    key_data = {
        "python_version": python_version,
        "dependencies": all_deps,
        "cache_version": "1",  # Bump this if cache format changes
    }
    key_json = json.dumps(key_data, sort_keys=True)
    return hashlib.sha256(key_json.encode()).hexdigest()[:16]


async def _get_cached_venv(
    cache_key: str,
    config: SandboxConfig,
    dest_venv_path: str,
) -> Optional[GateResult]:
    """
    Try to retrieve a cached venv and copy it to dest_venv_path.
    
    Returns:
        GateResult if cache hit, None if cache miss
    """
    cache_dir = _get_venv_cache_dir(config)
    cached_venv_dir = cache_dir / cache_key
    cached_venv_path = cached_venv_dir / ".venv"
    
    if not cached_venv_path.exists():
        logger.debug(f"Venv cache miss: {cache_key}")
        return None
    
    # Check cache age
    created_at_path = cached_venv_dir / "created_at.txt"
    if created_at_path.exists():
        try:
            created_at = datetime.fromisoformat(created_at_path.read_text().strip())
            age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
            if age_hours > config.venv_cache_max_age_hours:
                logger.debug(f"Venv cache expired: {cache_key} (age={age_hours:.1f}h)")
                # Don't delete here, let prune handle it
                return None
        except Exception as e:
            logger.warning(f"Failed to check cache age: {e}")
    
    # Copy cached venv to destination
    start = time.time()
    try:
        # Use copytree for full copy (preserves symlinks)
        shutil.copytree(cached_venv_path, dest_venv_path, symlinks=True)
        duration_ms = int((time.time() - start) * 1000)
        
        # Update access time for LRU
        created_at_path.write_text(datetime.now(timezone.utc).isoformat())
        
        logger.info(f"Venv cache hit: {cache_key} (copy took {duration_ms}ms)")
        
        _log_event("venv.cache_hit", logging.INFO,
            cache_key=cache_key,
            duration_ms=duration_ms,
        )
        
        return GateResult(
            name="venv_creation",
            passed=True,
            output=f"✓ Restored venv from cache ({cache_key[:8]}...) in {duration_ms}ms",
            return_code=0,
            duration_ms=duration_ms,
        )
        
    except Exception as e:
        logger.warning(f"Failed to restore cached venv: {e}")
        # Clean up partial copy
        if os.path.exists(dest_venv_path):
            shutil.rmtree(dest_venv_path, ignore_errors=True)
        return None


async def _save_venv_to_cache(
    cache_key: str,
    venv_path: str,
    dependencies: List[str],
    config: SandboxConfig,
) -> None:
    """
    Save a venv to the cache for future reuse.
    
    Args:
        cache_key: The cache key for this dependency set
        venv_path: Path to the venv to cache
        dependencies: List of dependencies (for debugging)
        config: Sandbox config
    """
    async with _venv_cache_lock:
        cache_dir = _get_venv_cache_dir(config)
        cached_venv_dir = cache_dir / cache_key
        
        # Skip if already cached (race condition protection)
        if cached_venv_dir.exists():
            logger.debug(f"Venv already cached: {cache_key}")
            return
        
        # Prune old entries first
        await _prune_venv_cache(config)
        
        try:
            cached_venv_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy venv to cache
            cached_venv_path = cached_venv_dir / ".venv"
            shutil.copytree(venv_path, cached_venv_path, symlinks=True)
            
            # Write metadata for debugging
            deps_path = cached_venv_dir / "deps.json"
            deps_path.write_text(json.dumps(dependencies, indent=2))
            
            created_at_path = cached_venv_dir / "created_at.txt"
            created_at_path.write_text(datetime.now(timezone.utc).isoformat())
            
            logger.info(f"Cached venv: {cache_key}")
            
            _log_event("venv.cache_save", logging.INFO,
                cache_key=cache_key,
                dependency_count=len(dependencies),
            )
            
        except Exception as e:
            logger.warning(f"Failed to cache venv: {e}")
            # Clean up partial cache entry
            if cached_venv_dir.exists():
                shutil.rmtree(cached_venv_dir, ignore_errors=True)


async def _prune_venv_cache(config: SandboxConfig) -> None:
    """
    Prune old/excess venv cache entries.
    
    Uses LRU eviction based on created_at.txt timestamps.
    """
    cache_dir = _get_venv_cache_dir(config)
    
    entries: List[Tuple[Path, datetime]] = []
    
    for entry in cache_dir.iterdir():
        if not entry.is_dir():
            continue
        created_at_path = entry / "created_at.txt"
        if created_at_path.exists():
            try:
                created_at = datetime.fromisoformat(created_at_path.read_text().strip())
                entries.append((entry, created_at))
            except Exception:
                # Invalid entry, schedule for deletion
                entries.append((entry, datetime.min.replace(tzinfo=timezone.utc)))
        else:
            # No timestamp, schedule for deletion
            entries.append((entry, datetime.min.replace(tzinfo=timezone.utc)))
    
    # Sort by timestamp (oldest first)
    entries.sort(key=lambda x: x[1])
    
    now = datetime.now(timezone.utc)
    deleted = 0
    
    for entry, created_at in entries:
        should_delete = False
        
        # Delete if over max entries
        if len(entries) - deleted > config.venv_cache_max_entries:
            should_delete = True
            
        # Delete if too old
        if created_at != datetime.min.replace(tzinfo=timezone.utc):
            age_hours = (now - created_at).total_seconds() / 3600
            if age_hours > config.venv_cache_max_age_hours:
                should_delete = True
        else:
            # Invalid timestamp, delete
            should_delete = True
            
        if should_delete:
            try:
                shutil.rmtree(entry)
                deleted += 1
                logger.debug(f"Pruned venv cache: {entry.name}")
            except Exception as e:
                logger.warning(f"Failed to prune cache entry {entry.name}: {e}")
    
    if deleted > 0:
        logger.info(f"Pruned {deleted} venv cache entries")


# =============================================================================
# Contract Testing Helpers
# =============================================================================

def materialize_spec_for_contract(
    sandbox_dir: str,
    spec_dict: Dict[str, Any],
) -> Path:
    """
    Write OpenAPI spec dict to a file in sandbox for contract testing.
    
    Contract tools (Prism, Schemathesis) require file paths, not dicts.
    Always materialize to avoid reliance on spec_refs being local files.
    
    Args:
        sandbox_dir: Path to sandbox directory
        spec_dict: OpenAPI specification as a dictionary
        
    Returns:
        Path to the materialized spec file (_contract_spec.yaml)
    """
    import yaml
    spec_path = Path(sandbox_dir) / "_contract_spec.yaml"
    with open(spec_path, "w") as f:
        yaml.safe_dump(spec_dict, f, default_flow_style=False, allow_unicode=True)
    logger.debug(f"Materialized contract spec to: {spec_path}")
    return spec_path


def extract_hosts_from_spec(spec_dict: Dict[str, Any]) -> List[str]:
    """
    Extract hostnames from OpenAPI servers array (spec-compliant).
    
    Handles:
    - Missing servers → returns empty list (caller must require explicit allowlist)
    - Relative URLs → returns empty list (cannot derive host)
    - Server variables → substitutes defaults per OpenAPI 3.x spec
    - Port handling → includes both host and host:port forms
    
    Args:
        spec_dict: OpenAPI specification as a dictionary
        
    Returns:
        List of hostnames (no scheme, no path). Empty if cannot derive.
        
    Examples:
        >>> extract_hosts_from_spec({"servers": [{"url": "https://api.openai.com/v1"}]})
        ["api.openai.com"]
        
        >>> extract_hosts_from_spec({
        ...     "servers": [{
        ...         "url": "https://{env}.api.com",
        ...         "variables": {"env": {"default": "prod"}}
        ...     }]
        ... })
        ["prod.api.com"]
        
        >>> extract_hosts_from_spec({"servers": [{"url": "/v1"}]})
        []  # Relative URL, cannot derive host
        
        >>> extract_hosts_from_spec({})
        []  # Missing servers
    """
    from urllib.parse import urlparse
    
    servers = spec_dict.get("servers", [])
    if not servers:
        logger.debug("No servers in spec, cannot extract hosts")
        return []
    
    hosts: List[str] = []
    for server in servers:
        url = server.get("url", "")
        if not url:
            continue
        
        # Handle server variables: {var} → substitute with default
        variables = server.get("variables", {})
        for var_name, var_config in variables.items():
            default_val = var_config.get("default", "")
            if default_val:
                url = url.replace(f"{{{var_name}}}", default_val)
            else:
                # Variable without default - cannot safely expand
                logger.warning(f"Server variable '{var_name}' has no default, skipping URL: {server.get('url')}")
                url = ""
                break
        
        if not url:
            continue
            
        # Skip relative URLs (cannot derive host)
        if url.startswith("/"):
            logger.debug(f"Skipping relative URL: {url}")
            continue
        
        # Parse and extract host
        try:
            parsed = urlparse(url)
            if parsed.netloc:
                host = parsed.hostname or ""
                port = parsed.port
                if host:
                    hosts.append(host)
                    if port:
                        # Also allow host:port form
                        hosts.append(f"{host}:{port}")
        except Exception as e:
            logger.warning(f"Failed to parse server URL '{url}': {e}")
            continue
    
    # Deduplicate while preserving order
    seen: set = set()
    unique_hosts: List[str] = []
    for h in hosts:
        if h not in seen:
            seen.add(h)
            unique_hosts.append(h)
    
    logger.debug(f"Extracted hosts from spec: {unique_hosts}")
    return unique_hosts


def _read_fixed_artifacts(sandbox_dir: str, artifacts: List[ArtifactFile]) -> Dict[str, str]:
    """
    V38-002: Read back artifacts from sandbox after ruff --fix has applied auto-fixes.
    
    Returns a dict mapping relative path -> fixed content for any files that changed.
    """
    fixed: Dict[str, str] = {}
    
    for artifact in artifacts:
        file_path = Path(sandbox_dir) / artifact.path
        if file_path.exists():
            try:
                fixed_content = file_path.read_text(encoding="utf-8")
                # Only include if content actually changed
                if fixed_content != artifact.content:
                    fixed[artifact.path] = fixed_content
                    logger.debug(f"V38-002: Read back fixed artifact: {artifact.path}")
            except Exception as e:
                logger.warning(f"V38-002: Failed to read back {artifact.path}: {e}")
    
    return fixed


def _build_result(
    success: bool,
    gate_results: List[GateResult],
    sandbox_dir: Optional[str],
    config: SandboxConfig,
    artifacts: Optional[List[ArtifactFile]] = None,
) -> SandboxResult:
    """
    Build a SandboxResult and generate the human-readable report.
    
    This helper centralizes result construction so every exit path
    produces a consistent result object and SANDBOX_REPORT.md.
    On failures, also emits FAILURE_BUNDLE.json for forensic debugging.
    
    V38-002: Also reads back fixed artifacts after ruff --fix.
    """
    # Build summary line
    passed_count = sum(1 for g in gate_results if g.passed)
    total_count = len(gate_results)
    summary = f"{'PASSED' if success else 'FAILED'}: {passed_count}/{total_count} gates passed"
    
    # V38-002: Read back fixed artifacts (after ruff --fix)
    fixed_artifacts: Dict[str, str] = {}
    if sandbox_dir and artifacts:
        fixed_artifacts = _read_fixed_artifacts(sandbox_dir, artifacts)
        if fixed_artifacts:
            logger.info(f"V38-002: {len(fixed_artifacts)} artifact(s) were auto-fixed by ruff")
    
    # Generate report if sandbox_dir exists (not yet cleaned up)
    if sandbox_dir:
        _generate_report(sandbox_dir, success, gate_results, config)
        
        # On failure, write FAILURE_BUNDLE.json for postmortem
        if not success:
            _log_event("sandbox.result.fail", logging.WARNING,
                passed_gates=passed_count,
                total_gates=total_count,
                failed_gates=[g.name for g in gate_results if not g.passed],
            )
            _write_failure_bundle(sandbox_dir, gate_results, artifacts or [], config)
        else:
            _log_event("sandbox.result.ok", logging.INFO,
                passed_gates=passed_count,
                total_gates=total_count,
            )
    
    return SandboxResult(
        success=success,
        gate_results=gate_results,
        sandbox_dir=sandbox_dir,
        summary=summary,
        fixed_artifacts=fixed_artifacts,
    )


async def execute_in_sandbox(
    artifacts: List[ArtifactFile],
    dependencies: Optional[List[str]] = None,
    config: Optional[SandboxConfig] = None,
) -> SandboxResult:
    """
    Execute generated code artifacts in an isolated sandbox.
    
    Args:
        artifacts: List of code files to validate
        dependencies: Python packages to install (e.g., ["pytest", "requests"])
        config: Sandbox configuration
        
    Returns:
        SandboxResult with gate outcomes
        
    Raises:
        SandboxError: If sandbox setup fails critically
    """
    import uuid
    
    config = config or SandboxConfig()
    dependencies = dependencies or []
    gate_results: List[GateResult] = []
    sandbox_dir: Optional[str] = None
    contract_spec: Optional[Path] = None
    
    # Initialize correlation IDs for structured logging
    run_id = str(uuid.uuid4())[:8]
    sandbox_id = f"sandbox-{run_id}"
    _run_id.set(run_id)
    _sandbox_id.set(sandbox_id)
    
    ctx_log = _get_context_logger()
    
    try:
        # 1. Create sandbox directory
        sandbox_dir = tempfile.mkdtemp(prefix="codegen_sandbox_")
        _sandbox_dir.set(sandbox_dir)
        
        _log_event("sandbox.create", logging.INFO,
            sandbox_dir=sandbox_dir,
            artifact_count=len(artifacts),
            artifact_files=[a.path for a in artifacts],
            dependencies=dependencies or [],
            config_gates={
                "ruff": config.enable_ruff,
                "mypy": config.enable_mypy,
                "bandit": config.enable_bandit,
                "pytest": config.enable_pytest,
                "coverage": config.enable_coverage,
                "contract_tests": config.enable_contract_tests,
            },
            cleanup_on_success=config.cleanup_on_success,
        )
        ctx_log.info(f"Created sandbox at: {sandbox_dir}")
        
        # 1a. Write runtime provenance for postmortem debugging (Bug #101 regression-proofing)
        _write_run_metadata(sandbox_dir)
        
        # 2. Write artifacts to sandbox
        _log_event("sandbox.write_artifacts.start", logging.DEBUG,
            artifact_count=len(artifacts),
        )
        
        # V45-004: Check and fix interface compatibility between client and flow
        # This must happen BEFORE writing artifacts so fixed versions are used
        from integration_coworker.codegen.interface_validator import (
            check_artifacts_interface_compatibility,
        )
        artifacts, interface_fixes = check_artifacts_interface_compatibility(artifacts)
        if interface_fixes:
            _log_event("sandbox.interface_fix", logging.INFO,
                fixes_applied=len(interface_fixes),
                fixes=[f.reason for f in interface_fixes],
            )
            ctx_log.info(
                f"[V45-004] Fixed {len(interface_fixes)} interface compatibility issues"
            )
        
        await _write_artifacts(sandbox_dir, artifacts)
        _log_event("sandbox.write_artifacts.done", logging.DEBUG,
            artifact_count=len(artifacts),
            files_written=[a.path for a in artifacts],
        )

        # 2a. Prepare contract spec in sandbox if requested (supports both path and dict)
        if config.enable_contract_tests and (config.contract_spec_path or config.contract_spec_dict):
            contract_spec = _prepare_contract_spec(
                sandbox_dir, 
                spec_path=config.contract_spec_path,
                spec_dict=config.contract_spec_dict,
            )

        # 2b. Inject validation conftest to enforce profiles in sandbox pytest runs
        if config.enable_pytest or config.enable_coverage:
            _write_validation_conftest(sandbox_dir)
        
        # 3. Determine dependencies (needed for cache key before venv creation)
        # Always install tooling (ruff, mypy, pytest) when gates are enabled
        tooling_deps = []
        if config.enable_ruff:
            tooling_deps.append("ruff")
        if config.enable_mypy:
            tooling_deps.append("mypy")
        if config.enable_bandit:
            tooling_deps.append("bandit[toml]")
        if config.enable_pytest:
            tooling_deps.extend(["pytest", "pytest-timeout", "pytest-socket"])
            # A1: Install pytest-cov when coverage is enabled
            if config.enable_coverage:
                tooling_deps.append("pytest-cov")

        if config.enable_contract_tests:
            tooling_deps.append("schemathesis")
        
        # V45-001: Install integration-coworker-runtime when policy_mode is "runtime"
        # This package provides IntegrationHttpClient, IntegrationError, and auth/retry utilities
        # that generated code imports when using runtime policy mode
        if config.policy_mode == "runtime":
            runtime_pkg_spec = _get_runtime_package_install_spec()
            if runtime_pkg_spec:
                tooling_deps.append(runtime_pkg_spec)
                _log_event("sandbox.runtime_package", logging.INFO,
                    policy_mode="runtime",
                    runtime_package=runtime_pkg_spec,
                )
        
        all_deps = list(set((dependencies or []) + tooling_deps))
        
        # 3a. Create isolated venv (with caching for PERF V27-001-004)
        venv_path = os.path.join(sandbox_dir, ".venv")
        
        # Try to restore from cache first
        venv_result = None
        cache_key = None
        cache_hit = False
        
        if config.enable_venv_cache and all_deps:
            cache_key = _compute_venv_cache_key(
                dependencies or [], 
                config.python_version,
                tooling_deps,
            )
            venv_result = await _get_cached_venv(cache_key, config, venv_path)
            cache_hit = venv_result is not None
        
        # Cache miss: create fresh venv
        if venv_result is None:
            venv_result = await _create_venv(venv_path, config.python_version)
        
        gate_results.append(venv_result)
        
        if not venv_result.passed:
            return _build_result(False, gate_results, sandbox_dir, config, artifacts)
        
        python_exe = _get_python_exe(venv_path)
        pip_exe = _get_pip_exe(venv_path)
        
        # 4. Install dependencies + tooling (skip if cache hit)
        if all_deps and not cache_hit:
            deps_result = await _install_dependencies(
                pip_exe, all_deps, config.timeout_seconds
            )
            gate_results.append(deps_result)
            
            if not deps_result.passed:
                return _build_result(False, gate_results, sandbox_dir, config, artifacts)
            
            # Save to cache for future use
            if config.enable_venv_cache and cache_key:
                await _save_venv_to_cache(cache_key, venv_path, all_deps, config)
        elif cache_hit:
            # Add a synthetic deps_result for consistency
            deps_result = GateResult(
                name="dependency_install",
                passed=True,
                output=f"✓ Dependencies restored from cache ({len(all_deps)} packages)",
                return_code=0,
                duration_ms=0,
            )
            gate_results.append(deps_result)
        
        # 4.5 P1.1 Defense in Depth: AST-based bare except check (before ruff)
        # This catches cases where ruff E722 might miss (e.g., 'except: raise')
        bare_except_result = check_artifacts_for_bare_except(artifacts)
        gate_results.append(bare_except_result)
        
        if not bare_except_result.passed:
            return _build_result(False, gate_results, sandbox_dir, config, artifacts)
        
        # 4.6 V45-004: Interface compatibility validation gate
        # Verify that flow code uses client interfaces correctly
        interface_result = _check_interface_compatibility_gate(artifacts)
        gate_results.append(interface_result)
        
        if not interface_result.passed:
            return _build_result(False, gate_results, sandbox_dir, config, artifacts)
        
        # 5. Run ruff (hard gate)
        if config.enable_ruff:
            ruff_result = await _run_ruff(sandbox_dir, python_exe, config)
            gate_results.append(ruff_result)
            
            if not ruff_result.passed:
                return _build_result(False, gate_results, sandbox_dir, config, artifacts)
        
        # 6. Run mypy (hard gate)
        if config.enable_mypy:
            mypy_result = await _run_mypy(sandbox_dir, python_exe, config)
            gate_results.append(mypy_result)
            
            if not mypy_result.passed:
                return _build_result(False, gate_results, sandbox_dir, config, artifacts)
        
        # 6b. Run bandit (security gate)
        if config.enable_bandit:
            bandit_result = await _run_bandit(sandbox_dir, python_exe, config)
            gate_results.append(bandit_result)
            
            if not bandit_result.passed:
                return _build_result(False, gate_results, sandbox_dir, config, artifacts)

        # 7. Run pytest (if tests exist or fail_on_no_tests is True)
        if config.enable_pytest or config.enable_coverage:
            tests_path = os.path.join(sandbox_dir, config.tests_dir)
            # Look for test files recursively: test_*.py or *_test.py
            test_files_exist = os.path.exists(tests_path) and (
                list(Path(tests_path).glob("**/test_*.py")) or 
                list(Path(tests_path).glob("**/*_test.py"))
            )
            
            if test_files_exist:
                # _run_pytest returns List[GateResult] for two-phase execution
                pytest_results = await _run_pytest(
                    sandbox_dir, python_exe, config
                )
                gate_results.extend(pytest_results)
                
                # Check if any pytest gate failed
                if any(not r.passed for r in pytest_results):
                    return _build_result(False, gate_results, sandbox_dir, config, artifacts)
            elif config.fail_on_no_tests:
                # Production mode: no tests is a hard failure
                pytest_result = GateResult(
                    name="pytest",
                    passed=False,
                    output="No test files found. Production mode requires tests.",
                    return_code=5,  # pytest exit code for "no tests collected"
                    duration_ms=0,
                )
                gate_results.append(pytest_result)
                return _build_result(False, gate_results, sandbox_dir, config, artifacts)
            else:
                # Development mode: no tests is allowed, create a passing gate
                pytest_result = GateResult(
                    name="pytest",
                    passed=True,
                    output="No test files found. Development mode allows this.",
                    return_code=0,
                    duration_ms=0,
                )
                gate_results.append(pytest_result)
        
        # 8. Run contract tests (Schemathesis + Prism) if enabled
        if config.enable_contract_tests and contract_spec:
            contract_result = await _run_contract_tests(
                sandbox_dir, python_exe, config, contract_spec
            )
            gate_results.append(contract_result)

            if not contract_result.passed:
                return _build_result(False, gate_results, sandbox_dir, config, artifacts)

        # All gates passed
        result = _build_result(True, gate_results, sandbox_dir, config, artifacts)
        
        # Cleanup on success if configured
        if config.cleanup_on_success and sandbox_dir:
            _log_event("sandbox.cleanup", logging.DEBUG,
                action="deleted",
                reason="cleanup_on_success=True",
            )
            shutil.rmtree(sandbox_dir, ignore_errors=True)
            result.sandbox_dir = None
        elif sandbox_dir:
            _log_event("sandbox.cleanup", logging.DEBUG,
                action="preserved",
                reason="cleanup_on_success=False or failure",
            )
            
        return result
        
    except Exception as e:
        logger.exception(f"Sandbox execution failed: {e}")
        gate_results.append(GateResult(
            name="sandbox_setup",
            passed=False,
            output=str(e),
            return_code=-1,
            duration_ms=0,
        ))
        return _build_result(False, gate_results, sandbox_dir, config, artifacts)


def _atomic_write(path: str, content: str, log_write: bool = False) -> None:
    """
    Atomically write content to a file using tmp + fsync + os.replace.
    
    This prevents partial writes if the process is interrupted mid-write.
    Per Python docs, os.replace is atomic on the same filesystem.
    
    Requirements for true atomicity:
    1. Temp file MUST be in same directory (same filesystem for atomic rename)
    2. fsync before replace ensures content is on disk
    3. os.replace overwrites destination atomically
    
    Args:
        path: Target file path
        content: Content to write
        log_write: If True, emit a structured log event (use for critical files)
    """
    # Temp file in same directory for atomic rename
    dir_path = os.path.dirname(path) or "."
    basename = os.path.basename(path)
    tmp_path = os.path.join(dir_path, f".{basename}.tmp.{os.getpid()}")
    content_bytes = len(content.encode("utf-8"))
    content_hash = _sha256_prefix(content)
    
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        
        if log_write:
            _log_event(
                "atomic_write",
                level=logging.DEBUG,
                path=path,
                tmp_path=tmp_path,
                bytes=content_bytes,
                sha256_prefix=content_hash,
                fsync=True,
                replace=True,
            )
    except Exception as e:
        # Clean up temp file on failure
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        _log_event(
            "atomic_write.failed",
            level=logging.ERROR,
            path=path,
            tmp_path=tmp_path,
            bytes=content_bytes,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise


# Sentinel strings for stub verification (content that MUST exist)
STUB_SENTINELS = {
    "__init__.py": "class IntegrationError(Exception):",
    "integration_http_client.py": "class IntegrationHttpClient:",
    "integration_error.py": "from clients import IntegrationError",
}


class StubVerificationError(Exception):
    """Raised when stub verification fails."""
    pass


def _get_directory_inventory(dir_path: str) -> str:
    """
    Get ls -la style inventory of a directory for error messages.
    
    Args:
        dir_path: Directory to inventory
        
    Returns:
        String with file listing or error message
    """
    try:
        if not os.path.exists(dir_path):
            return f"  (directory does not exist: {dir_path})"
        
        entries = []
        for name in sorted(os.listdir(dir_path)):
            path = os.path.join(dir_path, name)
            try:
                stat = os.stat(path)
                size = stat.st_size
                entries.append(f"  {size:>8} {name}")
            except OSError as e:
                entries.append(f"  {'?':>8} {name} (stat failed: {e})")
        
        if not entries:
            return "  (directory is empty)"
        return "\n".join(entries)
    except Exception as e:
        return f"  (inventory failed: {e})"


def _get_stub_preview(path: str, max_bytes: int = 200) -> str:
    """
    Get first N bytes of a file for error messages.
    
    Args:
        path: File path
        max_bytes: Maximum bytes to read
        
    Returns:
        String preview or error message
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read(max_bytes)
        if len(content) == max_bytes:
            content += "..."
        return repr(content)
    except Exception as e:
        return f"(read failed: {e})"


def _verify_stubs_exist(clients_dir: str) -> None:
    """
    Verify that all required stub modules exist and contain expected content.
    
    This provides defense-in-depth: if stub creation silently fails or is
    skipped, this raises a clear error before running quality gates.
    
    Args:
        clients_dir: Path to src/clients directory
        
    Raises:
        StubVerificationError: If any stub is missing or malformed
    """
    errors = []
    failed_stubs: Dict[str, str] = {}  # filename -> preview for failed stubs
    
    for filename, sentinel in STUB_SENTINELS.items():
        stub_path = os.path.join(clients_dir, filename)
        
        if not os.path.exists(stub_path):
            errors.append(f"Missing stub: {filename}")
            continue
        
        # Check file is non-trivial (not just a package marker)
        size = os.path.getsize(stub_path)
        if size < 100:
            errors.append(f"Stub too small: {filename} ({size} bytes, expected >=100)")
            failed_stubs[filename] = _get_stub_preview(stub_path)
            continue
        
        # Check sentinel string exists
        try:
            with open(stub_path, "r", encoding="utf-8") as f:
                content = f.read()
            if sentinel not in content:
                errors.append(f"Stub missing sentinel: {filename} (expected: '{sentinel[:40]}...')")
                failed_stubs[filename] = _get_stub_preview(stub_path)
        except Exception as e:
            errors.append(f"Cannot read stub {filename}: {e}")
    
    if errors:
        # Build actionable error message with inventory
        msg_parts = [
            f"Stub verification failed in {clients_dir}",
            "",
            "Errors:",
        ]
        for err in errors:
            msg_parts.append(f"  - {err}")
        
        msg_parts.extend(["", "Directory inventory:", _get_directory_inventory(clients_dir)])
        
        if failed_stubs:
            msg_parts.extend(["", "Failed stub previews:"])
            for filename, preview in failed_stubs.items():
                msg_parts.append(f"  {filename}: {preview}")
        
        # Emit structured log event BEFORE raising for forensic capture
        _log_event(
            "stub.verify.fail",
            level=logging.ERROR,
            clients_dir=clients_dir,
            errors=errors,
            failed_stubs=list(failed_stubs.keys()),
            inventory=_get_directory_inventory(clients_dir),
        )
        
        raise StubVerificationError("\n".join(msg_parts))
    
    _log_event("stub.verify.ok", clients_dir=clients_dir, stubs_checked=list(STUB_SENTINELS.keys()))


def _write_stub_modules(sandbox_dir: str, artifacts: Optional[List["ArtifactFile"]] = None) -> None:
    """
    Create stub modules for common LLM-hallucinated import patterns.
    
    Bug Fix v21: The LLM often hallucinates imports like:
    - from clients.integration_http_client import IntegrationError
    - from clients.integration_error import IntegrationError
    - from clients.<provider>_client import <Client>
    
    This creates stub modules that provide these imports so tests can run.
    
    Enhancement: Now also scans artifacts to dynamically create stubs for
    provider-specific client modules like `clients.stripe_api_client`.
    
    Bug #101 regression-proofing:
    - Uses atomic writes (tmp + os.replace) to prevent partial files
    - Verifies stubs after creation with _verify_stubs_exist()
    
    Args:
        sandbox_dir: Path to sandbox directory
        artifacts: Optional list of artifacts to scan for additional imports
        
    Raises:
        StubVerificationError: If stub creation fails verification
    """
    import re
    import time
    
    start_ts = time.time()
    
    src_dir = os.path.join(sandbox_dir, "src")
    clients_dir = os.path.join(src_dir, "clients")
    os.makedirs(clients_dir, exist_ok=True)
    
    _log_event("stub.write.start", logging.DEBUG,
        clients_dir=clients_dir,
        artifact_count=len(artifacts) if artifacts else 0,
    )
    
    # clients/__init__.py - re-export common exception (ATOMIC WRITE)
    clients_init = os.path.join(clients_dir, "__init__.py")
    _atomic_write(clients_init, '''"""
Auto-generated stub module for sandbox compatibility.

This provides common imports that LLMs hallucinate.
"""
# Re-export IntegrationError for any import pattern
class IntegrationError(Exception):
    """Base exception for integration errors."""
    pass


class TransientIntegrationError(IntegrationError):
    """Retryable integration error."""
    pass


class AuthIntegrationError(IntegrationError):
    """Authentication error."""
    pass
''')
    
    # clients/integration_http_client.py - common hallucination pattern (ATOMIC WRITE)
    http_client_stub = os.path.join(clients_dir, "integration_http_client.py")
    _atomic_write(http_client_stub, '''"""
Auto-generated stub module for sandbox compatibility.

Provides IntegrationError and IntegrationHttpClient for
LLM-hallucinated import patterns like:
  from clients.integration_http_client import IntegrationError
"""
from typing import Any, Dict, Optional
from clients import IntegrationError, TransientIntegrationError, AuthIntegrationError

__all__ = ["IntegrationError", "TransientIntegrationError", "AuthIntegrationError", "IntegrationHttpClient", "Response"]


class Response:
    """Mock Response class for type hints."""
    status_code: int = 200
    text: str = ""
    
    def json(self) -> Dict[str, Any]:
        return {}
    
    def raise_for_status(self) -> None:
        pass


class IntegrationHttpClient:
    """Stub HTTP client for sandbox validation."""
    
    def __init__(self, base_url: str = "", api_key: str = "", timeout_s: float = 30.0, retries: int = 3):
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.retries = retries
    
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        """
        Make an HTTP request. Always returns Response, never None.
        On failure, raises IntegrationError instead of returning None.
        """
        raise NotImplementedError("Stub client - use mocks in tests")
''')
    
    # clients/integration_error.py - another common pattern (ATOMIC WRITE)
    error_stub = os.path.join(clients_dir, "integration_error.py")
    _atomic_write(error_stub, '''"""
Auto-generated stub module for sandbox compatibility.

Provides IntegrationError for LLM-hallucinated import patterns like:
  from clients.integration_error import IntegrationError
"""
from clients import IntegrationError, TransientIntegrationError, AuthIntegrationError

__all__ = ["IntegrationError", "TransientIntegrationError", "AuthIntegrationError"]
''')
    
    # Bug #101 v21: Dynamically create stubs for provider-specific client imports
    # Scan artifacts to find all `from clients.X import Y` patterns
    if artifacts:
        # Pattern to match: from clients.<module> import <class>
        import_pattern = re.compile(r'from\s+clients\.(\w+)\s+import\s+(\w+(?:\s*,\s*\w+)*)')
        
        # Collect all unique module→class mappings
        dynamic_stubs: Dict[str, set] = {}  # module_name -> set of class names
        
        for artifact in artifacts:
            for match in import_pattern.finditer(artifact.content):
                module_name = match.group(1)
                # Skip already-created stubs
                if module_name in ('integration_http_client', 'integration_error', '__init__'):
                    continue
                classes = [c.strip() for c in match.group(2).split(',')]
                if module_name not in dynamic_stubs:
                    dynamic_stubs[module_name] = set()
                dynamic_stubs[module_name].update(classes)
        
        # Create stub files for each dynamic import
        for module_name, class_names in dynamic_stubs.items():
            stub_path = os.path.join(clients_dir, f"{module_name}.py")
            if not os.path.exists(stub_path):
                # Generate a stub that provides all imported classes
                stub_content = f'''"""
Auto-generated stub module for sandbox compatibility.

Bug #101 v21: Dynamically created to satisfy import:
  from clients.{module_name} import {', '.join(sorted(class_names))}
"""
from typing import Any, Dict, Optional
from clients import IntegrationError

__all__ = {list(sorted(class_names))}

'''
                # Generate stub classes
                for class_name in sorted(class_names):
                    if class_name == 'IntegrationError':
                        # Re-export from clients
                        stub_content += f'# {class_name} imported from clients\n'
                    else:
                        # Generate stub client class
                        stub_content += f'''
class {class_name}:
    """
    Auto-generated stub client for sandbox validation.
    
    This is a placeholder that allows tests to run. Methods will raise
    NotImplementedError - use mocks in your tests.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "",
        timeout: float = 30.0,
        **kwargs: Any,
    ):
        self.api_key = api_key or ""
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.timeout = timeout
    
    def __getattr__(self, name: str) -> Any:
        """
        Catch-all for any method call.
        
        This allows the stub to accept any method call without defining
        each one explicitly. Returns a callable that raises NotImplementedError.
        """
        def method(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            raise NotImplementedError(
                f"Stub client {{self.__class__.__name__}}.{{name}}() - use mocks in tests"
            )
        return method

'''
                
                _atomic_write(stub_path, stub_content)
                logger.debug(f"Created dynamic stub: {module_name}.py for classes: {class_names}")
    
    # Verify stubs were created correctly (defense-in-depth)
    _verify_stubs_exist(clients_dir)
    
    duration_ms = int((time.time() - start_ts) * 1000)
    stubs_created = list(STUB_SENTINELS.keys())
    
    _log_event("stub.write.done", logging.DEBUG,
        clients_dir=clients_dir,
        stubs_created=stubs_created,
        duration_ms=duration_ms,
    )
    
    logger.debug(f"Created stub modules in {clients_dir}")


async def _write_artifacts(sandbox_dir: str, artifacts: List[ArtifactFile]) -> None:
    """Write artifact files to sandbox directory."""
    created_dirs = set()
    
    # First, create stub modules for common hallucinated imports
    # Pass artifacts so we can dynamically create provider-specific stubs (Bug #101 v21)
    _write_stub_modules(sandbox_dir, artifacts)
    
    for artifact in artifacts:
        file_path = os.path.join(sandbox_dir, artifact.path)
        dir_path = os.path.dirname(file_path)
        os.makedirs(dir_path, exist_ok=True)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(artifact.content)
        
        logger.debug(f"Wrote artifact: {artifact.path} ({len(artifact.content)} chars)")
        
        # Track directories for __init__.py creation
        created_dirs.add(dir_path)
    
    # Create __init__.py files in all Python package directories
    # This ensures imports work correctly
    for dir_path in created_dirs:
        init_path = os.path.join(dir_path, "__init__.py")
        if not os.path.exists(init_path):
            with open(init_path, "w") as f:
                f.write("# Auto-generated by sandbox\n")
        
        # Also create __init__.py in parent directories up to sandbox_dir
        current = dir_path
        while current != sandbox_dir and current.startswith(sandbox_dir):
            parent = os.path.dirname(current)
            if parent == current:
                break
            parent_init = os.path.join(parent, "__init__.py")
            if parent != sandbox_dir and not os.path.exists(parent_init):
                with open(parent_init, "w") as f:
                    f.write("# Auto-generated by sandbox\n")
            current = parent


def _write_validation_conftest(sandbox_dir: str) -> Path:
    """Copy the golden validation conftest into sandbox tests/ directory."""
    source_path = Path(pytest_conftest_template.__file__).resolve()
    dest_dir = Path(sandbox_dir) / "tests"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "conftest.py"
    dest_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    return dest_path


def _prepare_contract_spec(
    sandbox_dir: str, 
    spec_path: Optional[str] = None,
    spec_dict: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    Prepare contract spec for Prism/Schemathesis in sandbox.
    
    Supports two modes:
    1. spec_path: Copy existing file to sandbox
    2. spec_dict: Materialize dict to file in sandbox (preferred for robustness)
    
    Args:
        sandbox_dir: Path to sandbox directory
        spec_path: Path to existing spec file (optional)
        spec_dict: Spec as dictionary (optional, preferred)
        
    Returns:
        Path to the spec file in sandbox
        
    Raises:
        ValueError: If neither spec_path nor spec_dict provided
        FileNotFoundError: If spec_path provided but file doesn't exist
    """
    if spec_dict:
        # Materialize dict to file (preferred - avoids reliance on spec_refs being local files)
        return materialize_spec_for_contract(sandbox_dir, spec_dict)
    elif spec_path:
        # Copy existing file
        source = Path(spec_path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"Contract spec not found: {source}")
        dest = Path(sandbox_dir) / "_contract_spec.yaml"
        dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return dest
    else:
        raise ValueError("Either spec_path or spec_dict must be provided for contract tests")


# =============================================================================
# P1.1 Defense in Depth: AST-based bare except detection
# =============================================================================

def check_bare_except_ast(code: str, filename: str = "<generated>") -> List[str]:
    """
    Check for bare 'except:' clauses using AST parsing.
    
    Ruff E722 has known edge cases (e.g., 'except: raise') that may not be flagged.
    This provides defense-in-depth by explicitly checking the AST.
    
    Args:
        code: Python source code to check
        filename: Filename for error messages
        
    Returns:
        List of error messages for each bare except found
        
    Example:
        >>> code = '''
        ... try:
        ...     foo()
        ... except:
        ...     pass
        ... '''
        >>> errors = check_bare_except_ast(code)
        >>> len(errors) == 1
        True
    """
    import ast
    
    issues: List[str] = []
    
    try:
        tree = ast.parse(code)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # node.type is None for bare 'except:'
                if node.type is None:
                    issues.append(
                        f"{filename}:{node.lineno}: bare 'except:' clause - "
                        f"must specify exception type (e.g., 'except Exception:' or more specific)"
                    )
    except SyntaxError:
        # Syntax errors will be caught by other gates (ruff, python -m py_compile)
        pass
    
    return issues


def check_artifacts_for_bare_except(artifacts: List["ArtifactFile"]) -> GateResult:
    """
    Check all Python artifacts for bare except clauses.
    
    This runs as an additional gate after code generation but before ruff,
    providing defense-in-depth against bare except: that ruff might miss.
    
    Args:
        artifacts: List of generated artifact files
        
    Returns:
        GateResult indicating pass/fail
    """
    import time
    start = time.time()
    
    all_issues: List[str] = []
    
    for artifact in artifacts:
        if artifact.path.endswith(".py"):
            issues = check_bare_except_ast(artifact.content, artifact.path)
            all_issues.extend(issues)
    
    duration_ms = int((time.time() - start) * 1000)
    
    if all_issues:
        return GateResult(
            name="bare_except_check",
            passed=False,
            output="✗ Bare except clauses found:\n" + "\n".join(all_issues),
            return_code=1,
            duration_ms=duration_ms,
        )
    else:
        return GateResult(
            name="bare_except_check",
            passed=True,
            output="✓ No bare except clauses found",
            return_code=0,
            duration_ms=duration_ms,
        )


def _check_interface_compatibility_gate(artifacts: List["ArtifactFile"]) -> GateResult:
    """
    V45-004: Validate interface compatibility between client and flow.
    
    This gate ensures that flow code uses client interfaces correctly:
    - Constructor calls pass valid arguments
    - Method calls use valid signatures
    
    This is a hard gate because interface mismatches cause runtime errors.
    
    Args:
        artifacts: List of generated artifact files
        
    Returns:
        GateResult indicating pass/fail
    """
    import time
    from integration_coworker.codegen.interface_validator import (
        validate_interface_compatibility,
    )
    
    start = time.time()
    
    # Find client and flow artifacts
    client_content = None
    flow_content = None
    client_path = None
    flow_path = None
    
    for artifact in artifacts:
        path = artifact.path.lower()
        if path.endswith('.py'):
            if 'client' in path:
                client_content = artifact.content
                client_path = artifact.path
            elif 'flow' in path:
                flow_content = artifact.content
                flow_path = artifact.path
    
    duration_ms = int((time.time() - start) * 1000)
    
    # If we don't have both client and flow, skip validation
    if not client_content or not flow_content:
        return GateResult(
            name="interface_compatibility",
            passed=True,
            output="✓ Interface check skipped (no client/flow pair found)",
            return_code=0,
            duration_ms=duration_ms,
        )
    
    # Validate interface compatibility
    result = validate_interface_compatibility(client_content, flow_content)
    
    duration_ms = int((time.time() - start) * 1000)
    
    if result.is_valid:
        output_lines = ["✓ Interface compatibility check passed"]
        if result.client_interface:
            output_lines.append(f"  Client: {result.client_interface.name}")
            if result.client_interface.init_signature:
                params = result.client_interface.init_signature.param_names
                output_lines.append(f"  __init__ params: {params or 'none'}")
        return GateResult(
            name="interface_compatibility",
            passed=True,
            output="\n".join(output_lines),
            return_code=0,
            duration_ms=duration_ms,
        )
    else:
        output_lines = ["✗ Interface compatibility issues found:"]
        for mismatch in result.mismatches:
            output_lines.append(f"  [{mismatch.severity.upper()}] {mismatch.location}: {mismatch.message}")
            if mismatch.fix_suggestion:
                output_lines.append(f"    Fix: {mismatch.fix_suggestion}")
        
        return GateResult(
            name="interface_compatibility",
            passed=False,
            output="\n".join(output_lines),
            return_code=1,
            duration_ms=duration_ms,
        )


async def _create_venv(venv_path: str, python_version: str) -> GateResult:
    """Create isolated virtual environment."""
    import time
    start = time.time()
    
    try:
        # Try to use the specified Python version
        python_cmd = f"python{python_version}"
        
        # Fall back to current Python if version not available
        result = await _run_command([python_cmd, "--version"], gate_name="venv_check_python")
        if result[0] != 0:
            python_cmd = sys.executable
        
        result = await _run_command(
            [python_cmd, "-m", "venv", venv_path],
            timeout=60,
            gate_name="venv_creation",
        )
        
        duration_ms = int((time.time() - start) * 1000)
        
        if result[0] == 0:
            return GateResult(
                name="venv_creation",
                passed=True,
                output=f"Created venv at {venv_path}",
                return_code=result[0],
                duration_ms=duration_ms,
            )
        else:
            return GateResult(
                name="venv_creation",
                passed=False,
                output=result[1] or result[2] or "Unknown error",
                return_code=result[0],
                duration_ms=duration_ms,
            )
            
    except Exception as e:
        return GateResult(
            name="venv_creation",
            passed=False,
            output=str(e),
            return_code=-1,
            duration_ms=int((time.time() - start) * 1000),
        )


async def _install_dependencies(
    pip_exe: str, 
    dependencies: List[str],
    timeout: int
) -> GateResult:
    """Install dependencies in sandbox venv."""
    import time
    start = time.time()
    
    # Dependencies are already merged with tooling in execute_in_sandbox
    all_deps = dependencies
    
    result = await _run_command(
        [pip_exe, "install", "--quiet"] + all_deps,
        timeout=timeout,
        gate_name="dependency_install",
    )
    
    duration_ms = int((time.time() - start) * 1000)
    
    if result[0] == 0:
        return GateResult(
            name="dependency_install",
            passed=True,
            output=f"Installed: {', '.join(all_deps)}",
            return_code=result[0],
            duration_ms=duration_ms,
        )
    else:
        return GateResult(
            name="dependency_install",
            passed=False,
            output=result[1] or result[2] or "pip install failed",
            return_code=result[0],
            duration_ms=duration_ms,
        )


async def _run_ruff(sandbox_dir: str, python_exe: str, config: SandboxConfig) -> GateResult:
    """
    Run ruff linter AND formatter as hard gates.
    
    Per user requirement: Run BOTH ruff check AND ruff format.
    Auto-fixes format issues and safe lint issues (LLM code is rarely perfect),
    then checks for remaining unfixable lint errors.
    
    Note: E501 (line too long) is ignored because:
    1. ruff format doesn't auto-fix line length
    2. LLM-generated code often has descriptive variable names that exceed 88 chars
    3. We use line-length=100 to match project pyproject.toml
    """
    import time
    start = time.time()
    
    errors = []
    all_output = []
    
    # 1. Auto-format the code first (LLM-generated code is rarely perfectly formatted)
    # Use line-length=100 to match project config (pyproject.toml)
    ruff_format_fix_args = [python_exe, "-m", "ruff", "format", "--line-length", "100", "."]
    format_fix_result = await _run_command(ruff_format_fix_args, cwd=sandbox_dir, timeout=60, gate_name="ruff_format")
    
    if format_fix_result[0] == 0:
        all_output.append("[FORMAT] ✓ ruff format: Code formatted successfully")
    else:
        # Format failed - likely a syntax error
        output = format_fix_result[2] or format_fix_result[1] or "ruff format failed"
        errors.append(f"ruff format: {output.strip()}")
        all_output.append(f"[FORMAT] ✗ {output}")
    
    # 2. Auto-fix safe lint issues (unused imports, etc.)
    # P1.1 Fix: Include E722 (bare except) in rules to fix
    # V27-007 Fix: Include B904 in auto-fix to auto-chain exceptions with `raise ... from`
    # Use line-length=100 to match project config
    ruff_fix_args = [python_exe, "-m", "ruff", "check", ".", "--fix", "--unsafe-fixes", 
                     "--line-length", "100", "--select", "E,F,I,W,B",
                     "--extend-fixable", "B904"]
    fix_result = await _run_command(ruff_fix_args, cwd=sandbox_dir, timeout=60, gate_name="ruff_fix")
    # Note: --fix returns 0 even if it fixed things, so we don't check the return code
    all_output.append("[LINT] Applied auto-fixes for safe issues (including E722 bare except, B904 exception chaining)")
    
    # 3. Check for remaining unfixable lint errors
    # P1.1 Fix: Include E722 in checks - bare except is a code quality issue
    # Ignore E501 (line too long) - LLM code often has descriptive names
    # Ignore rules that LLMs commonly violate but aren't true bugs:
    # - E501: Line too long - LLM code often has descriptive names in f-strings
    # - B904: Raise without from - LLM misses exception chaining (not a runtime bug)
    # - B018: Useless expression - LLM sometimes generates standalone `response` lines
    # Use line-length=100 to match project config
    ruff_check_args = [python_exe, "-m", "ruff", "check", ".", 
                       "--line-length", "100",
                       "--select", "E,F,I,W,B",
                       "--ignore", "E501,B904,B018"]
    
    if config.extra_ruff_rules:
        ruff_check_args.extend(["--extend-select", ",".join(config.extra_ruff_rules)])
    
    # Output format for better error messages
    ruff_check_args.extend(["--output-format", "concise"])
    
    check_result = await _run_command(ruff_check_args, cwd=sandbox_dir, timeout=60, gate_name="ruff_check")
    
    if check_result[0] != 0:
        output = check_result[1] or check_result[2] or "ruff check failed"
        errors.append(f"ruff check: {output.strip()}")
        all_output.append(f"[LINT] ✗ {output}")
    else:
        all_output.append("[LINT] ✓ ruff check: No linting errors")
    
    duration_ms = int((time.time() - start) * 1000)
    
    passed = len(errors) == 0
    combined_output = "\n".join(all_output)
    
    if passed:
        combined_output = "✓ ruff: All checks passed (format + lint)"
    
    return GateResult(
        name="ruff",
        passed=passed,
        output=combined_output,
        return_code=0 if passed else 1,
        duration_ms=duration_ms,
    )


async def _run_mypy(
    sandbox_dir: str, 
    python_exe: str,
    config: SandboxConfig
) -> GateResult:
    """Run mypy type checker as hard gate."""
    import time
    start = time.time()
    
    mypy_args = [python_exe, "-m", "mypy", "."]
    
    if config.mypy_strict:
        mypy_args.append("--strict")
    else:
        # Reasonable defaults for generated code
        mypy_args.extend([
            "--ignore-missing-imports",
            "--no-error-summary",
        ])
    
    result = await _run_command(mypy_args, cwd=sandbox_dir, timeout=120, gate_name="mypy")
    duration_ms = int((time.time() - start) * 1000)
    
    passed = result[0] == 0
    output = result[1] if result[1] else result[2] if result[2] else "No output"
    
    if passed:
        output = "✓ mypy: No type errors"
    
    return GateResult(
        name="mypy",
        passed=passed,
        output=output,
        return_code=result[0],
        duration_ms=duration_ms,
    )


async def _run_pytest_single(
    sandbox_dir: str,
    python_exe: str, 
    config: SandboxConfig,
    profile: str,
    markers: Optional[str],
    name_suffix: str,
    extra_env: Optional[Dict[str, str]] = None,
) -> GateResult:
    """
    Run a single pytest invocation with specified profile and markers.
    
    Args:
        sandbox_dir: Path to sandbox directory
        python_exe: Path to Python executable in venv
        config: Sandbox configuration
        profile: VALIDATION_PROFILE value ("offline" or "live")
        markers: pytest marker expression (e.g., "not integration_live", "integration_live")
        name_suffix: Suffix for gate name (e.g., "", "_live")
        extra_env: Additional environment variables for this run
        
    Returns:
        GateResult for this pytest run
    """
    import time
    start = time.time()
    
    # Base pytest args
    pytest_args = [
        python_exe, "-m", "pytest",
        config.tests_dir,
        "-q",  # Quiet mode for cleaner output
        "--tb=short",
        f"--timeout={config.timeout_seconds}",
    ]
    
    # Add marker filter if specified
    if markers:
        pytest_args.extend(["-m", markers])
    
    # Add coverage flags when enabled (only for offline runs to avoid double-counting)
    if config.enable_coverage and config.coverage_target and profile == "offline":
        cov_target = config.coverage_target
        pytest_args.extend([
            f"--cov={cov_target}",
            f"--cov-report={config.coverage_report}",
            f"--cov-fail-under={config.coverage_fail_under}",
            "--cov-config=.coveragerc",
        ])
        
        # Create .coveragerc to exclude venv and hard-to-test defensive code
        coveragerc_path = os.path.join(sandbox_dir, ".coveragerc")
        coveragerc_content = f"""[run]
source = {cov_target}
omit =
    .venv/*
    */__pycache__/*
    */test_*
    tests/*

[report]
exclude_lines =
    pragma: no cover
    if __name__ == .__main__.:
    raise NotImplementedError
    # Defensive patterns that are hard to unit test but safe to skip:
    raise IntegrationError
    raise ImportError
    except httpx.TransportError
    except ImportError
    if response is None
    if self._rate_limit_lock is None
    self._logger.info
    self._logger.warning
    self._logger.error
    time.sleep
"""
        with open(coveragerc_path, "w") as f:
            f.write(coveragerc_content)
    
    # Build environment
    run_env = {"VALIDATION_PROFILE": profile}
    if profile == "live":
        run_env["ALLOW_LIVE"] = "1"
        run_env["LIVE_HOST_ALLOWLIST"] = ",".join(config.live_host_allowlist)
    if extra_env:
        run_env.update(extra_env)
    
    pytest_gate_name = f"pytest{name_suffix}"
    result = await _run_command(
        pytest_args, 
        cwd=sandbox_dir,
        timeout=config.timeout_seconds,
        extra_env=run_env,
        gate_name=pytest_gate_name,
    )
    duration_ms = int((time.time() - start) * 1000)
    
    return_code = result[0]
    output = result[1] if result[1] else result[2] if result[2] else "No output"
    
    # Handle exit code 5 (NO_TESTS_COLLECTED)
    PYTEST_NO_TESTS_COLLECTED = 5
    gate_name = f"pytest{name_suffix}"
    
    if return_code == PYTEST_NO_TESTS_COLLECTED:
        # For live tests, no tests is OK (they're opt-in)
        if profile == "live":
            return GateResult(
                name=gate_name,
                passed=True,
                output=f"⚠ {gate_name}: No integration_live tests found (OK for live profile)\n{output}",
                return_code=return_code,
                duration_ms=duration_ms,
            )
        elif config.fail_on_no_tests:
            return GateResult(
                name=gate_name,
                passed=False,
                output=f"✗ {gate_name}: NO_TESTS_COLLECTED (exit code 5) - production mode requires tests\n{output}",
                return_code=return_code,
                duration_ms=duration_ms,
            )
        else:
            return GateResult(
                name=gate_name,
                passed=True,
                output=f"⚠ {gate_name}: NO_TESTS_COLLECTED - no tests found (warning in dev mode)\n{output}",
                return_code=return_code,
                duration_ms=duration_ms,
            )
    
    passed = return_code == 0
    
    if passed:
        if profile == "live":
            output = f"✓ {gate_name}: Live integration tests passed (real API calls)"
        elif config.enable_coverage:
            output = f"✓ {gate_name}: All tests passed with coverage >= {config.coverage_fail_under}%"
        else:
            output = f"✓ {gate_name}: All tests passed"
    
    return GateResult(
        name=gate_name,
        passed=passed,
        output=output,
        return_code=return_code,
        duration_ms=duration_ms,
    )


async def _run_pytest(
    sandbox_dir: str,
    python_exe: str, 
    config: SandboxConfig
) -> List[GateResult]:
    """
    Run pytest in sandbox with two-phase execution when live tests are enabled.
    
    CRITICAL: When enable_live_tests=True, runs TWO pytest invocations:
    1. Offline suite: VALIDATION_PROFILE=offline, -m "not integration_live"
       - This ensures mocked unit tests (core correctness) always run
    2. Live suite: VALIDATION_PROFILE=live, -m "integration_live"
       - Only runs if offline suite passes
       - Uses real API credentials from config.live_env_vars
    
    This prevents the dangerous behavior where "live profile runs only
    integration_live tests" would skip the mocked tests that provide
    baseline correctness.
    
    Per user requirement A5:
    - Exit code 5 (NO_TESTS_COLLECTED) handling:
      - Production (fail_on_no_tests=True): hard failure for offline, OK for live
      - Development (fail_on_no_tests=False): warning, but passes
    """
    results: List[GateResult] = []
    
    # Phase 1: Always run offline suite first (mocked tests)
    logger.info("[sandbox] Running offline pytest suite (mocked tests)")
    offline_result = await _run_pytest_single(
        sandbox_dir=sandbox_dir,
        python_exe=python_exe,
        config=config,
        profile="offline",
        markers="not integration_live" if config.enable_live_tests else None,
        name_suffix="",
        extra_env=None,
    )
    results.append(offline_result)
    
    # Phase 2: If live enabled AND offline passed, run live suite
    if config.enable_live_tests:
        if offline_result.passed:
            logger.info(
                f"[sandbox] Running live pytest suite (real API calls) "
                f"with allowlist: {config.live_host_allowlist}"
            )
            live_env = dict(config.live_env_vars)  # Copy user-provided credentials
            live_result = await _run_pytest_single(
                sandbox_dir=sandbox_dir,
                python_exe=python_exe,
                config=config,
                profile="live",
                markers="integration_live",
                name_suffix="_live",
                extra_env=live_env,
            )
            results.append(live_result)
        else:
            # Skip live tests if offline failed
            logger.warning("[sandbox] Skipping live pytest suite - offline tests failed")
            results.append(GateResult(
                name="pytest_live",
                passed=False,
                output="⚠ pytest_live: Skipped - offline tests must pass first",
                return_code=-1,
                duration_ms=0,
            ))
    
    return results


async def _run_contract_tests(
    sandbox_dir: str,
    python_exe: str,
    config: SandboxConfig,
    contract_spec: Path,
) -> GateResult:
    """Run Schemathesis against Prism mock server for contract validation."""
    import time

    start = time.time()
    port = config.contract_prism_port
    base_url = config.contract_base_url or f"http://127.0.0.1:{port}"

    prism_cmd = shutil.which("prism")
    if not prism_cmd:
        duration_ms = int((time.time() - start) * 1000)
        return GateResult(
            name="contract",
            passed=False,
            output=(
                "✗ Prism CLI not found. Contract tests require Prism mock server.\n\n"
                "INSTALL OPTIONS:\n"
                "  Option 1 (global): npm install -g @stoplight/prism-cli@5\n"
                "  Option 2 (local):  npm install --save-dev @stoplight/prism-cli@5\n"
                "                     Then run via: npx prism mock <spec>\n\n"
                "For CI reproducibility, add to package.json:\n"
                '  "devDependencies": {"@stoplight/prism-cli": "^5.0.0"}\n\n'
                "Verify installation: prism --version\n"
                "Documentation: https://stoplight.io/open-source/prism"
            ),
            return_code=1,
            duration_ms=duration_ms,
        )

    proc = subprocess.Popen(
        [prism_cmd, "mock", str(contract_spec), "-p", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=sandbox_dir,
    )

    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            if _is_port_open("127.0.0.1", port):
                break
            await asyncio.sleep(0.2)
        else:
            stderr = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
            duration_ms = int((time.time() - start) * 1000)
            return GateResult(
                name="contract",
                passed=False,
                output=f"Prism failed to start on port {port}: {stderr}",
                return_code=1,
                duration_ms=duration_ms,
            )

        schemathesis_cmd = [
            python_exe,
            "-m",
            "schemathesis",
            "run",
            str(contract_spec),
            "--base-url",
            base_url,
            "--workers",
            str(config.contract_workers),
            "--request-timeout",
            str(config.timeout_seconds),
        ]

        for check in config.contract_checks:
            schemathesis_cmd.extend(["--checks", check])

        result = await _run_command(
            schemathesis_cmd,
            cwd=sandbox_dir,
            timeout=config.timeout_seconds,
            gate_name="contract",
        )
        duration_ms = int((time.time() - start) * 1000)

        passed = result[0] == 0
        output = result[1] if result[1] else result[2] if result[2] else "No output"
        if passed:
            output = "✓ contract: Schemathesis checks passed"

        return GateResult(
            name="contract",
            passed=passed,
            output=output,
            return_code=result[0],
            duration_ms=duration_ms,
        )

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


async def _run_command(
    cmd: List[str],
    cwd: Optional[str] = None,
    timeout: int = 60,
    extra_env: Optional[Dict[str, str]] = None,
    gate_name: Optional[str] = None,
) -> Tuple[int, str, str]:
    """
    Run a subprocess command asynchronously with structured logging.
    
    Args:
        cmd: Command and arguments to run
        cwd: Working directory
        timeout: Timeout in seconds
        extra_env: Additional environment variables to set
        gate_name: If provided, persist outputs to sandbox logs dir and emit gate events
    
    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    import time
    start_ts = time.time()
    
    # Set gate context for structured logging if provided
    if gate_name:
        _gate_name.set(gate_name)
        _log_event("gate.start", logging.DEBUG,
            command=cmd,
            cwd=cwd,
            timeout_s=timeout,
            extra_env=extra_env,
        )
    
    try:
        # Prepare environment with PYTHONPATH set to include sandbox src
        env = os.environ.copy()
        if cwd:
            src_path = os.path.join(cwd, "src")
            existing_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{src_path}:{cwd}:{existing_pythonpath}" if existing_pythonpath else f"{src_path}:{cwd}"
        
        # Add any extra environment variables
        if extra_env:
            env.update(extra_env)
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout
        )
        
        return_code = process.returncode or 0
        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")
        duration_ms = int((time.time() - start_ts) * 1000)
        
        # Persist outputs and emit structured log if gate_name provided
        if gate_name and cwd:
            _persist_gate_output(cwd, gate_name, stdout_str, stderr_str)
            
            # Compute output tails for log (last 10 lines each)
            stdout_tail = "\n".join(stdout_str.strip().split("\n")[-10:]) if stdout_str.strip() else ""
            stderr_tail = "\n".join(stderr_str.strip().split("\n")[-10:]) if stderr_str.strip() else ""
            
            _log_event("gate.done", logging.DEBUG if return_code == 0 else logging.WARNING,
                exit_code=return_code,
                duration_ms=duration_ms,
                stdout_bytes=len(stdout),
                stderr_bytes=len(stderr),
                stdout_sha256=_sha256_prefix(stdout_str) if stdout_str else None,
                stderr_sha256=_sha256_prefix(stderr_str) if stderr_str else None,
                stdout_tail=stdout_tail[:500] if stdout_tail else None,
                stderr_tail=stderr_tail[:500] if stderr_tail else None,
            )
        
        return (return_code, stdout_str, stderr_str)
        
    except asyncio.TimeoutError:
        process.kill()
        duration_ms = int((time.time() - start_ts) * 1000)
        if gate_name:
            _log_event("gate.timeout", logging.ERROR,
                timeout_s=timeout,
                duration_ms=duration_ms,
            )
        return (-1, "", f"Command timed out after {timeout}s")
    except Exception as e:
        duration_ms = int((time.time() - start_ts) * 1000)
        if gate_name:
            _log_event("gate.error", logging.ERROR,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=duration_ms,
            )
        return (-1, "", str(e))


def _get_python_exe(venv_path: str) -> str:
    """Get Python executable path for venv."""
    if sys.platform == "win32":
        return os.path.join(venv_path, "Scripts", "python.exe")
    return os.path.join(venv_path, "bin", "python")


def _get_pip_exe(venv_path: str) -> str:
    """Get pip executable path for venv."""
    if sys.platform == "win32":
        return os.path.join(venv_path, "Scripts", "pip.exe")
    return os.path.join(venv_path, "bin", "pip")


async def _run_bandit(
    sandbox_dir: str,
    python_exe: str,
    config: SandboxConfig
) -> GateResult:
    """Run bandit security scanner as hard gate.
    
    Note: We skip B101 (assert_used) because:
    1. Test files legitimately use assert statements
    2. Development code being validated is not production code
    3. Assertions are useful for invariant checking during development
    """
    import time
    start = time.time()
    
    # Scan only source code, explicitly exclude venv, tests, and other non-source directories
    # Skip B101 (assert_used) - assertions are valid in development/test code
    bandit_args = [
        python_exe, "-m", "bandit", "-r", ".",
        "--exclude", ".venv,venv,.git,__pycache__,.mypy_cache,.pytest_cache,build,dist,tests",
        "--skip", "B101",  # Skip assert_used - valid in dev/test code
    ]
    
    if config.bandit_config_path:
        # Copy config file to sandbox if it's outside
        config_path = Path(config.bandit_config_path).resolve()
        if config_path.exists():
            dest_path = Path(sandbox_dir) / config_path.name
            dest_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
            bandit_args.extend(["-c", config_path.name])
    
    # Output format
    bandit_args.extend(["-f", "txt"])
    
    result = await _run_command(bandit_args, cwd=sandbox_dir, timeout=60, gate_name="bandit")
    duration_ms = int((time.time() - start) * 1000)
    
    passed = result[0] == 0
    output = result[1] if result[1] else result[2] if result[2] else "No output"
    
    if passed:
        output = "✓ bandit: No security issues found"
    
    return GateResult(
        name="bandit",
        passed=passed,
        output=output,
        return_code=result[0],
        duration_ms=duration_ms,
    )


def _generate_report(
    sandbox_dir: str,
    success: bool,
    gate_results: List[GateResult],
    config: SandboxConfig
) -> Path:
    """Generate human-readable report in sandbox directory."""
    report_path = Path(sandbox_dir) / "SANDBOX_REPORT.md"
    
    status_icon = "✅" if success else "❌"
    status_text = "PASSED" if success else "FAILED"
    
    lines = [
        f"# Sandbox Validation Report {status_icon}",
        "",
        f"**Status**: {status_text}",
        f"**Python**: {config.python_version}",
        "",
        "## Gate Results",
        "",
        "| Gate | Status | Duration |",
        "|------|--------|----------|",
    ]
    
    for gate in gate_results:
        icon = "✅" if gate.passed else "❌"
        lines.append(f"| {gate.name} | {icon} | {gate.duration_ms}ms |")
    
    lines.append("")
    lines.append("## Detailed Output")
    
    for gate in gate_results:
        lines.append(f"### {gate.name}")
        lines.append("```")
        lines.append(gate.output)
        lines.append("```")
        lines.append("")
        
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def run_quick_validation(code: str, filename: str = "module.py") -> SandboxResult:
    """
    Synchronous quick validation without full sandbox.
    
    Runs ruff and mypy on a single file without venv creation.
    Useful for fast feedback during development.
    
    Args:
        code: Python code to validate
        filename: Filename for the code
        
    Returns:
        SandboxResult (limited gates)
    """
    return asyncio.run(execute_in_sandbox(
        artifacts=[ArtifactFile(f"src/{filename}", code)],
        dependencies=[],
        config=SandboxConfig(
            enable_pytest=False,  # No tests
            cleanup_on_success=True,
            cleanup_on_failure=True,
        ),
    ))
