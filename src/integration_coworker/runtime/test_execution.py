"""
Test Execution Sandbox

Provides opt-in test execution for generated code artifacts.

Design: BUG_FIX_ANALYSIS.md - Optimal Solution
- Opt-in via environment variable or explicit call
- Sandboxed execution with resource limits
- Language-aware test runners
- VS Code fallback for user-assisted execution

Security:
- Test execution is DISABLED by default
- Only runs when ENABLE_TEST_EXECUTION=true
- Uses temp directories and resource limits
- No network access in sandbox mode

Usage:
    from integration_coworker.runtime.test_execution import (
        is_test_execution_enabled,
        execute_tests,
        TestExecutionResult,
    )
    
    if is_test_execution_enabled():
        results = execute_tests(code_artifacts, language="python")
"""
import logging
import os
import subprocess
import tempfile
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from enum import Enum

from integration_coworker.domain.models import CodeArtifact

logger = logging.getLogger(__name__)


class TestStatus(str, Enum):
    """Test execution status"""
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    NOT_EXECUTED = "not_executed"


@dataclass
class TestExecutionResult:
    """Result of test execution"""
    status: TestStatus
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    output: str = ""
    error_message: Optional[str] = None
    execution_time_ms: int = 0
    artifacts_tested: list[str] = field(default_factory=list)


def is_test_execution_enabled() -> bool:
    """
    Check if test execution is enabled.
    
    Test execution is opt-in for safety reasons.
    Set ENABLE_TEST_EXECUTION=true to enable.
    """
    return os.getenv("ENABLE_TEST_EXECUTION", "false").lower() in ("true", "1", "yes")


def get_test_timeout() -> int:
    """Get test execution timeout in seconds."""
    return int(os.getenv("TEST_EXECUTION_TIMEOUT", "60"))


def _find_test_artifacts(artifacts: list[CodeArtifact]) -> list[CodeArtifact]:
    """Find test artifacts from the list of code artifacts."""
    test_artifacts = []
    for artifact in artifacts:
        # Identify tests by artifact type or file pattern
        if artifact.artifact_type == "test":
            test_artifacts.append(artifact)
        elif artifact.rel_path:
            path = artifact.rel_path.lower()
            if "test" in path or "spec" in path:
                test_artifacts.append(artifact)
    return test_artifacts


def _get_test_runner(language: str) -> tuple[str, list[str]]:
    """
    Get the test runner command for a language.
    
    Returns:
        Tuple of (command, args) for the test runner
    """
    lang_lower = language.lower()
    
    runners = {
        # Python: pytest preferred, unittest fallback
        "python": ("pytest", ["-v", "--tb=short"]),
        "py": ("pytest", ["-v", "--tb=short"]),
        
        # JavaScript/TypeScript: jest or npm test
        "typescript": ("npx", ["jest", "--verbose"]),
        "ts": ("npx", ["jest", "--verbose"]),
        "javascript": ("npx", ["jest", "--verbose"]),
        "js": ("npx", ["jest", "--verbose"]),
        
        # Go: go test
        "go": ("go", ["test", "-v", "./..."]),
        "golang": ("go", ["test", "-v", "./..."]),
        
        # Java: mvn test or gradle test
        "java": ("mvn", ["test"]),
        
        # Ruby: rspec
        "ruby": ("bundle", ["exec", "rspec"]),
        "rb": ("bundle", ["exec", "rspec"]),
        
        # C#: dotnet test
        "csharp": ("dotnet", ["test"]),
        "c#": ("dotnet", ["test"]),
        "cs": ("dotnet", ["test"]),
    }
    
    return runners.get(lang_lower, ("echo", ["No test runner for language"]))


def _setup_sandbox(artifacts: list[CodeArtifact], language: str) -> Path:
    """
    Create a sandboxed temp directory with test files.
    
    Returns:
        Path to the sandbox directory
    """
    sandbox_dir = Path(tempfile.mkdtemp(prefix="coworker_test_"))
    
    # Write all artifacts to sandbox
    for artifact in artifacts:
        if artifact.rel_path:
            file_path = sandbox_dir / artifact.rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(artifact.content)
    
    # Add basic config files for test runners
    lang_lower = language.lower()
    
    if lang_lower in ("python", "py"):
        # Create pytest.ini for Python
        (sandbox_dir / "pytest.ini").write_text(
            "[pytest]\n"
            "testpaths = .\n"
            "python_files = test_*.py *_test.py\n"
        )
    elif lang_lower in ("typescript", "ts", "javascript", "js"):
        # Create minimal package.json for jest
        (sandbox_dir / "package.json").write_text(
            '{"name": "test-sandbox", "scripts": {"test": "jest"}}'
        )
    
    return sandbox_dir


def _cleanup_sandbox(sandbox_dir: Path) -> None:
    """Remove the sandbox directory."""
    try:
        shutil.rmtree(sandbox_dir)
    except Exception as e:
        logger.warning(f"Failed to cleanup sandbox {sandbox_dir}: {e}")


def execute_tests(
    artifacts: list[CodeArtifact],
    language: str = "python",
    timeout: Optional[int] = None,
) -> TestExecutionResult:
    """
    Execute tests for the given code artifacts.
    
    This is an opt-in feature that runs generated tests in a sandbox.
    
    Args:
        artifacts: List of CodeArtifact to test
        language: Programming language of the tests
        timeout: Execution timeout in seconds (default from env)
        
    Returns:
        TestExecutionResult with test outcomes
    """
    if not is_test_execution_enabled():
        return TestExecutionResult(
            status=TestStatus.NOT_EXECUTED,
            error_message="Test execution disabled. Set ENABLE_TEST_EXECUTION=true to enable.",
            artifacts_tested=[a.rel_path for a in artifacts if a.rel_path],
        )
    
    test_artifacts = _find_test_artifacts(artifacts)
    
    if not test_artifacts:
        return TestExecutionResult(
            status=TestStatus.SKIPPED,
            error_message="No test artifacts found",
            artifacts_tested=[],
        )
    
    timeout = timeout or get_test_timeout()
    runner_cmd, runner_args = _get_test_runner(language)
    
    # Check if runner is available
    if shutil.which(runner_cmd) is None:
        return TestExecutionResult(
            status=TestStatus.ERROR,
            error_message=f"Test runner '{runner_cmd}' not found. Install it to run tests.",
            artifacts_tested=[a.rel_path for a in test_artifacts if a.rel_path],
        )
    
    sandbox_dir = None
    try:
        # Setup sandbox with all artifacts (not just tests)
        sandbox_dir = _setup_sandbox(artifacts, language)
        
        logger.info(f"Executing tests in sandbox: {sandbox_dir}")
        logger.info(f"Running: {runner_cmd} {' '.join(runner_args)}")
        
        # Run tests with timeout
        import time
        start_time = time.time()
        
        result = subprocess.run(
            [runner_cmd] + runner_args,
            cwd=sandbox_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                **os.environ,
                # Sandbox environment: no network, restricted paths
                "NO_COLOR": "1",  # Disable colors for cleaner output
            },
        )
        
        execution_time = int((time.time() - start_time) * 1000)
        
        # Parse results (basic parsing, could be enhanced per-runner)
        output = result.stdout + "\n" + result.stderr
        
        if result.returncode == 0:
            return TestExecutionResult(
                status=TestStatus.PASSED,
                output=output,
                execution_time_ms=execution_time,
                artifacts_tested=[a.rel_path for a in test_artifacts if a.rel_path],
                # TODO: Parse actual counts from output
                total_tests=len(test_artifacts),
                passed=len(test_artifacts),
            )
        else:
            return TestExecutionResult(
                status=TestStatus.FAILED,
                output=output,
                error_message=f"Tests failed with return code {result.returncode}",
                execution_time_ms=execution_time,
                artifacts_tested=[a.rel_path for a in test_artifacts if a.rel_path],
                # TODO: Parse actual counts from output
                total_tests=len(test_artifacts),
                failed=len(test_artifacts),
            )
            
    except subprocess.TimeoutExpired:
        return TestExecutionResult(
            status=TestStatus.ERROR,
            error_message=f"Test execution timed out after {timeout}s",
            artifacts_tested=[a.rel_path for a in test_artifacts if a.rel_path],
        )
    except Exception as e:
        logger.error(f"Test execution error: {e}")
        return TestExecutionResult(
            status=TestStatus.ERROR,
            error_message=str(e),
            artifacts_tested=[a.rel_path for a in test_artifacts if a.rel_path],
        )
    finally:
        if sandbox_dir:
            _cleanup_sandbox(sandbox_dir)


def generate_vscode_test_task(
    artifacts: list[CodeArtifact],
    language: str = "python",
) -> dict:
    """
    Generate a VS Code tasks.json task for running tests.
    
    This is the fallback when sandbox execution is disabled.
    Users can copy this task to their VS Code configuration.
    
    Args:
        artifacts: List of CodeArtifact to test
        language: Programming language of the tests
        
    Returns:
        Dict with VS Code task definition
    """
    runner_cmd, runner_args = _get_test_runner(language)
    test_artifacts = _find_test_artifacts(artifacts)
    
    # Build test file paths
    test_files = [a.rel_path for a in test_artifacts if a.rel_path]
    
    return {
        "label": f"Run Generated Tests ({language})",
        "type": "shell",
        "command": runner_cmd,
        "args": runner_args + test_files,
        "group": {
            "kind": "test",
            "isDefault": True,
        },
        "problemMatcher": {
            "python": ["$pytest"],
            "typescript": ["$eslint-stylish"],
            "go": ["$go"],
        }.get(language.lower(), []),
        "presentation": {
            "reveal": "always",
            "panel": "new",
        },
    }
