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
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


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
    enable_pytest: bool = True
    timeout_seconds: int = 300
    cleanup_on_success: bool = True
    cleanup_on_failure: bool = False  # Keep for debugging
    extra_ruff_rules: List[str] = field(default_factory=list)
    mypy_strict: bool = False
    
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
    
    def __post_init__(self):
        """Validate configuration."""
        if self.enable_coverage and not self.coverage_target:
            raise ValueError(
                "coverage_target is required when enable_coverage=True. "
                "Specify the module/path to measure coverage (e.g., 'src')."
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
    config = config or SandboxConfig()
    dependencies = dependencies or []
    gate_results: List[GateResult] = []
    sandbox_dir: Optional[str] = None
    
    try:
        # 1. Create sandbox directory
        sandbox_dir = tempfile.mkdtemp(prefix="codegen_sandbox_")
        logger.info(f"Created sandbox at: {sandbox_dir}")
        
        # 2. Write artifacts to sandbox
        await _write_artifacts(sandbox_dir, artifacts)
        
        # 3. Create isolated venv
        venv_path = os.path.join(sandbox_dir, ".venv")
        venv_result = await _create_venv(venv_path, config.python_version)
        gate_results.append(venv_result)
        
        if not venv_result.passed:
            return _build_result(False, gate_results, sandbox_dir, config)
        
        python_exe = _get_python_exe(venv_path)
        pip_exe = _get_pip_exe(venv_path)
        
        # 4. Install dependencies + tooling
        # Always install tooling (ruff, mypy, pytest) when gates are enabled
        tooling_deps = []
        if config.enable_ruff:
            tooling_deps.append("ruff")
        if config.enable_mypy:
            tooling_deps.append("mypy")
        if config.enable_pytest:
            tooling_deps.extend(["pytest", "pytest-timeout"])
            # A1: Install pytest-cov when coverage is enabled
            if config.enable_coverage:
                tooling_deps.append("pytest-cov")
        
        all_deps = list(set((dependencies or []) + tooling_deps))
        
        if all_deps:
            deps_result = await _install_dependencies(
                pip_exe, all_deps, config.timeout_seconds
            )
            gate_results.append(deps_result)
            
            if not deps_result.passed:
                return _build_result(False, gate_results, sandbox_dir, config)
        
        # 5. Run ruff (hard gate)
        if config.enable_ruff:
            ruff_result = await _run_ruff(sandbox_dir, python_exe, config)
            gate_results.append(ruff_result)
            
            if not ruff_result.passed:
                return _build_result(False, gate_results, sandbox_dir, config)
        
        # 6. Run mypy (hard gate)
        if config.enable_mypy:
            mypy_result = await _run_mypy(sandbox_dir, python_exe, config)
            gate_results.append(mypy_result)
            
            if not mypy_result.passed:
                return _build_result(False, gate_results, sandbox_dir, config)
        
        # 7. Run pytest (if tests exist or fail_on_no_tests is True)
        if config.enable_pytest or config.enable_coverage:
            tests_path = os.path.join(sandbox_dir, config.tests_dir)
            # Look for test files recursively: test_*.py or *_test.py
            test_files_exist = os.path.exists(tests_path) and (
                list(Path(tests_path).glob("**/test_*.py")) or 
                list(Path(tests_path).glob("**/*_test.py"))
            )
            
            if test_files_exist:
                pytest_result = await _run_pytest(
                    sandbox_dir, python_exe, config
                )
                gate_results.append(pytest_result)
                
                if not pytest_result.passed:
                    return _build_result(False, gate_results, sandbox_dir, config)
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
                return _build_result(False, gate_results, sandbox_dir, config)
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
        
        # All gates passed
        result = _build_result(True, gate_results, sandbox_dir, config)
        
        # Cleanup on success if configured
        if config.cleanup_on_success and sandbox_dir:
            shutil.rmtree(sandbox_dir, ignore_errors=True)
            result.sandbox_dir = None
            
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
        return _build_result(False, gate_results, sandbox_dir, config)


async def _write_artifacts(sandbox_dir: str, artifacts: List[ArtifactFile]) -> None:
    """Write artifact files to sandbox directory."""
    created_dirs = set()
    
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


async def _create_venv(venv_path: str, python_version: str) -> GateResult:
    """Create isolated virtual environment."""
    import time
    start = time.time()
    
    try:
        # Try to use the specified Python version
        python_cmd = f"python{python_version}"
        
        # Fall back to current Python if version not available
        result = await _run_command([python_cmd, "--version"])
        if result[0] != 0:
            python_cmd = sys.executable
        
        result = await _run_command(
            [python_cmd, "-m", "venv", venv_path],
            timeout=60
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
        timeout=timeout
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
    """
    import time
    start = time.time()
    
    errors = []
    all_output = []
    
    # 1. Auto-format the code first (LLM-generated code is rarely perfectly formatted)
    ruff_format_fix_args = [python_exe, "-m", "ruff", "format", "."]
    format_fix_result = await _run_command(ruff_format_fix_args, cwd=sandbox_dir, timeout=60)
    
    if format_fix_result[0] == 0:
        all_output.append("[FORMAT] ✓ ruff format: Code formatted successfully")
    else:
        # Format failed - likely a syntax error
        output = format_fix_result[2] or format_fix_result[1] or "ruff format failed"
        errors.append(f"ruff format: {output.strip()}")
        all_output.append(f"[FORMAT] ✗ {output}")
    
    # 2. Auto-fix safe lint issues (unused imports, etc.)
    ruff_fix_args = [python_exe, "-m", "ruff", "check", ".", "--fix", "--unsafe-fixes"]
    fix_result = await _run_command(ruff_fix_args, cwd=sandbox_dir, timeout=60)
    # Note: --fix returns 0 even if it fixed things, so we don't check the return code
    all_output.append("[LINT] Applied auto-fixes for safe issues")
    
    # 3. Check for remaining unfixable lint errors
    ruff_check_args = [python_exe, "-m", "ruff", "check", "."]
    
    if config.extra_ruff_rules:
        ruff_check_args.extend(["--extend-select", ",".join(config.extra_ruff_rules)])
    
    # Output format for better error messages
    ruff_check_args.extend(["--output-format", "concise"])
    
    check_result = await _run_command(ruff_check_args, cwd=sandbox_dir, timeout=60)
    
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
    
    result = await _run_command(mypy_args, cwd=sandbox_dir, timeout=120)
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


async def _run_pytest(
    sandbox_dir: str,
    python_exe: str, 
    config: SandboxConfig
) -> GateResult:
    """
    Run pytest in sandbox with optional coverage.
    
    Per user requirement A3:
    - When enable_coverage=False: standard pytest invocation
    - When enable_coverage=True: add --cov flags
    
    Per user requirement A5:
    - Exit code 5 (NO_TESTS_COLLECTED) handling:
      - Production (fail_on_no_tests=True): hard failure
      - Development (fail_on_no_tests=False): warning, but passes
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
    
    # A3: Add coverage flags when enabled
    if config.enable_coverage and config.coverage_target:
        # Ensure coverage target doesn't include venv
        cov_target = config.coverage_target
        pytest_args.extend([
            f"--cov={cov_target}",
            f"--cov-report={config.coverage_report}",
            f"--cov-fail-under={config.coverage_fail_under}",
            # A4: Exclude sandbox venv and common non-source dirs
            "--cov-config=.coveragerc",  # Will create if needed
        ])
        
        # Create .coveragerc to exclude venv (A4: deterministic exclusions)
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
"""
        with open(coveragerc_path, "w") as f:
            f.write(coveragerc_content)
    
    result = await _run_command(
        pytest_args, 
        cwd=sandbox_dir,
        timeout=config.timeout_seconds
    )
    duration_ms = int((time.time() - start) * 1000)
    
    return_code = result[0]
    output = result[1] if result[1] else result[2] if result[2] else "No output"
    
    # A5: Handle exit code 5 (NO_TESTS_COLLECTED)
    # pytest exit codes: 0=pass, 1=fail, 2=interrupt, 3=internal error, 4=usage error, 5=no tests
    PYTEST_NO_TESTS_COLLECTED = 5
    
    if return_code == PYTEST_NO_TESTS_COLLECTED:
        if config.fail_on_no_tests:
            # Production: hard failure
            return GateResult(
                name="pytest",
                passed=False,
                output=f"✗ pytest: NO_TESTS_COLLECTED (exit code 5) - production mode requires tests\n{output}",
                return_code=return_code,
                duration_ms=duration_ms,
            )
        else:
            # Development: warning but pass
            logger.warning("pytest: NO_TESTS_COLLECTED (exit code 5) - no tests found")
            return GateResult(
                name="pytest",
                passed=True,  # Allow to proceed in development
                output=f"⚠ pytest: NO_TESTS_COLLECTED - no tests found (warning in dev mode)\n{output}",
                return_code=return_code,
                duration_ms=duration_ms,
            )
    
    # Handle coverage failure (exit code 1 with coverage below threshold)
    passed = return_code == 0
    
    if passed:
        if config.enable_coverage:
            output = f"✓ pytest: All tests passed with coverage >= {config.coverage_fail_under}%"
        else:
            output = "✓ pytest: All tests passed"
    
    return GateResult(
        name="pytest",
        passed=passed,
        output=output,
        return_code=return_code,
        duration_ms=duration_ms,
    )


async def _run_command(
    cmd: List[str],
    cwd: Optional[str] = None,
    timeout: int = 60,
) -> Tuple[int, str, str]:
    """
    Run a subprocess command asynchronously.
    
    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    try:
        # Prepare environment with PYTHONPATH set to include sandbox src
        env = os.environ.copy()
        if cwd:
            src_path = os.path.join(cwd, "src")
            existing_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{src_path}:{cwd}:{existing_pythonpath}" if existing_pythonpath else f"{src_path}:{cwd}"
        
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
        
        return (
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
        
    except asyncio.TimeoutError:
        process.kill()
        return (-1, "", f"Command timed out after {timeout}s")
    except Exception as e:
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


def _build_result(
    success: bool,
    gate_results: List[GateResult],
    sandbox_dir: Optional[str],
    config: SandboxConfig,
) -> SandboxResult:
    """Build final sandbox result with summary."""
    passed = [g for g in gate_results if g.passed]
    failed = [g for g in gate_results if not g.passed]
    
    if success:
        summary = f"✓ All {len(gate_results)} gates passed"
    else:
        failed_names = ", ".join(g.name for g in failed)
        summary = f"✗ Failed gates: {failed_names} ({len(failed)}/{len(gate_results)})"
    
    # Cleanup on failure if configured
    if not success and config.cleanup_on_failure and sandbox_dir:
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        sandbox_dir = None
    
    return SandboxResult(
        success=success,
        gate_results=gate_results,
        sandbox_dir=sandbox_dir,
        summary=summary,
    )


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
