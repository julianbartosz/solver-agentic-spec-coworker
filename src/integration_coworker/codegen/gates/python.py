"""
Python language validation strategy.

Wraps the existing sandbox.py Python validation logic in the LanguageStrategy interface.
This provides backward compatibility while enabling the multi-language architecture.

Gates:
- ruff (lint)
- mypy (typecheck)
- bandit (security)
- pytest (tests)
"""

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from integration_coworker.codegen.gates.base import (
    ArtifactFile,
    GateError,
    GateResult,
    GateStatus,
    LanguageStrategy,
    ProvisionError,
    SandboxEnv,
    Tier,
    ToolchainMissingError,
    ValidationResult,
)
import logging

logger = logging.getLogger(__name__)


@dataclass
class PythonGateConfig:
    """Configuration for Python gates."""
    enable_ruff: bool = True
    enable_mypy: bool = True
    enable_bandit: bool = True
    enable_pytest: bool = True
    mypy_strict: bool = False
    timeout_seconds: int = 300
    extra_ruff_rules: List[str] = None
    
    def __post_init__(self):
        if self.extra_ruff_rules is None:
            self.extra_ruff_rules = []


class PythonStrategy(LanguageStrategy):
    """
    Python validation strategy.
    
    Tier 1 (PROD): Uses venv isolation, runs ruff/mypy/bandit/pytest.
    Tier 2 (EXP): Same gates but may skip if tools unavailable.
    """
    
    language = "python"
    default_tier = Tier.PROD  # Python is Tier 1 ready
    required_executables = ["python", "pip"]
    required_config_files = []  # Python works without config
    container_image = None  # Python uses venv, not container
    
    def __init__(self, config: Optional[PythonGateConfig] = None):
        self.config = config or PythonGateConfig()
    
    def probe_toolchain(self, workspace: Path) -> Dict[str, Any]:
        """Probe for Python toolchain availability."""
        result = {
            "executables_found": [],
            "executables_missing": [],
            "config_files_found": [],
            "config_files_missing": [],
            "lockfile_found": None,
            "tier_eligible": Tier.PROD,
            "failure_reason": None,
        }
        
        # Check for python executable
        python_path = shutil.which("python3") or shutil.which("python")
        if python_path:
            result["executables_found"].append("python")
        else:
            result["executables_missing"].append("python")
            result["tier_eligible"] = Tier.EXP
            result["failure_reason"] = "Python executable not found"
        
        # Check for pip
        pip_path = shutil.which("pip3") or shutil.which("pip")
        if pip_path:
            result["executables_found"].append("pip")
        else:
            result["executables_missing"].append("pip")
        
        # Check for config files
        for config_file in ["pyproject.toml", "requirements.txt", "setup.py"]:
            if (workspace / config_file).exists():
                result["config_files_found"].append(config_file)
        
        # Check for lockfile
        for lockfile in ["requirements.lock", "requirements-lock.txt", "poetry.lock"]:
            if (workspace / lockfile).exists():
                result["lockfile_found"] = lockfile
                break
        
        return result
    
    def provision(
        self,
        artifacts: List[ArtifactFile],
        workspace: Path,
        tier: Tier,
    ) -> SandboxEnv:
        """
        Provision Python sandbox with venv.
        
        1. Write artifacts to workspace
        2. Create venv
        3. Install dependencies (ruff, mypy, bandit, pytest)
        """
        start = time.time()
        
        # Create workspace if needed
        workspace.mkdir(parents=True, exist_ok=True)
        
        # Write artifacts
        for artifact in artifacts:
            artifact_path = workspace / artifact.path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(artifact.content)
            logger.debug(f"Wrote artifact: {artifact.path}")
        
        # Create venv
        venv_path = workspace / ".venv"
        python_exe = shutil.which("python3") or shutil.which("python")
        
        if not python_exe:
            if tier == Tier.PROD:
                raise ToolchainMissingError("Python executable not found")
            else:
                logger.warning("Python not found, skipping venv creation")
                return SandboxEnv(
                    sandbox_path=workspace,
                    work_dir=workspace,
                    deps_resolved=False,
                )
        
        try:
            subprocess.run(
                [python_exe, "-m", "venv", str(venv_path)],
                check=True,
                capture_output=True,
                timeout=60,
            )
            logger.debug(f"Created venv at {venv_path}")
        except subprocess.CalledProcessError as e:
            raise ProvisionError(f"Failed to create venv: {e.stderr}")
        
        # Install tools
        pip_exe = venv_path / "bin" / "pip"
        if not pip_exe.exists():
            pip_exe = venv_path / "Scripts" / "pip.exe"  # Windows
        
        tools = []
        if self.config.enable_ruff:
            tools.append("ruff")
        if self.config.enable_mypy:
            tools.append("mypy")
        if self.config.enable_bandit:
            tools.append("bandit")
        if self.config.enable_pytest:
            tools.append("pytest")
        
        if tools:
            try:
                subprocess.run(
                    [str(pip_exe), "install", "--quiet"] + tools,
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                logger.debug(f"Installed tools: {tools}")
            except subprocess.CalledProcessError as e:
                raise ProvisionError(f"Failed to install tools: {e.stderr}")
        
        duration_ms = int((time.time() - start) * 1000)
        logger.info(f"Python provisioning completed in {duration_ms}ms")
        
        return SandboxEnv(
            sandbox_path=workspace,
            work_dir=workspace,
            env_vars={"VIRTUAL_ENV": str(venv_path)},
            deps_resolved=True,
        )
    
    def validate(
        self,
        env: SandboxEnv,
        tier: Tier,
    ) -> ValidationResult:
        """Run Python validation gates."""
        if not env.deps_resolved:
            raise ProvisionError("Must call provision() before validate()")
        
        start = time.time()
        gates: List[GateResult] = []
        all_passed = True
        
        # Get venv python/tools
        venv_path = env.sandbox_path / ".venv"
        bin_dir = venv_path / "bin"
        if not bin_dir.exists():
            bin_dir = venv_path / "Scripts"  # Windows
        
        # Run ruff
        if self.config.enable_ruff:
            gate = self._run_ruff(env, bin_dir)
            gates.append(gate)
            if not gate.passed:
                all_passed = False
        
        # Run mypy
        if self.config.enable_mypy:
            gate = self._run_mypy(env, bin_dir)
            gates.append(gate)
            if not gate.passed:
                all_passed = False
        
        # Run bandit
        if self.config.enable_bandit:
            gate = self._run_bandit(env, bin_dir)
            gates.append(gate)
            if not gate.passed:
                all_passed = False
        
        # Run pytest
        if self.config.enable_pytest:
            gate = self._run_pytest(env, bin_dir)
            gates.append(gate)
            if not gate.passed:
                all_passed = False
        
        duration_ms = int((time.time() - start) * 1000)
        
        # Collect validated artifacts
        validated = []
        for f in env.sandbox_path.rglob("*.py"):
            if ".venv" not in str(f):
                validated.append(str(f.relative_to(env.sandbox_path)))
        
        return ValidationResult(
            success=all_passed,
            tier=tier,
            language=self.language,
            gates=gates,
            artifacts_validated=validated,
            duration_ms=duration_ms,
            validate_duration_ms=duration_ms,
        )
    
    def _run_ruff(self, env: SandboxEnv, bin_dir: Path) -> GateResult:
        """Run ruff linter."""
        start = time.time()
        ruff_exe = bin_dir / "ruff"
        
        try:
            result = subprocess.run(
                [str(ruff_exe), "check", "."],
                cwd=env.work_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            passed = result.returncode == 0
            errors = []
            if not passed:
                # Parse ruff output for errors
                for line in result.stdout.splitlines():
                    if line.strip():
                        errors.append(line.strip())
            
            return GateResult(
                name="ruff",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors[:10],  # Limit to first 10 errors
            )
        except Exception as e:
            return GateResult(
                name="ruff",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
    
    def _run_mypy(self, env: SandboxEnv, bin_dir: Path) -> GateResult:
        """Run mypy type checker."""
        start = time.time()
        mypy_exe = bin_dir / "mypy"
        
        cmd = [str(mypy_exe), "."]
        if self.config.mypy_strict:
            cmd.append("--strict")
        
        try:
            result = subprocess.run(
                cmd,
                cwd=env.work_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            passed = result.returncode == 0
            errors = []
            if not passed:
                for line in result.stdout.splitlines():
                    if ": error:" in line:
                        errors.append(line.strip())
            
            return GateResult(
                name="mypy",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors[:10],
            )
        except Exception as e:
            return GateResult(
                name="mypy",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
    
    def _run_bandit(self, env: SandboxEnv, bin_dir: Path) -> GateResult:
        """Run bandit security scanner."""
        start = time.time()
        bandit_exe = bin_dir / "bandit"
        
        try:
            result = subprocess.run(
                [str(bandit_exe), "-r", ".", "-f", "json"],
                cwd=env.work_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            # Bandit returns 0 if no issues, 1 if issues found
            passed = result.returncode == 0
            errors = []
            
            if not passed:
                import json
                try:
                    data = json.loads(result.stdout)
                    for issue in data.get("results", [])[:10]:
                        errors.append(
                            f"{issue.get('filename')}:{issue.get('line_number')}: "
                            f"{issue.get('issue_severity')} - {issue.get('issue_text')}"
                        )
                except json.JSONDecodeError:
                    errors.append(result.stdout[:500])
            
            return GateResult(
                name="bandit",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors,
            )
        except Exception as e:
            return GateResult(
                name="bandit",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
    
    def _run_pytest(self, env: SandboxEnv, bin_dir: Path) -> GateResult:
        """Run pytest tests."""
        start = time.time()
        pytest_exe = bin_dir / "pytest"
        
        # Check if tests exist
        tests_dir = env.work_dir / "tests"
        if not tests_dir.exists() and not list(env.work_dir.glob("test_*.py")):
            return GateResult(
                name="pytest",
                status=GateStatus.SKIPPED,
                exit_code=0,
                stdout="No tests found",
                stderr="",
                duration_ms=int((time.time() - start) * 1000),
            )
        
        try:
            result = subprocess.run(
                [str(pytest_exe), "-v", "--tb=short"],
                cwd=env.work_dir,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            # pytest exit codes: 0=all passed, 1=some failed, 5=no tests collected
            passed = result.returncode == 0
            errors = []
            
            if result.returncode == 1:
                # Parse failures
                for line in result.stdout.splitlines():
                    if "FAILED" in line or "ERROR" in line:
                        errors.append(line.strip())
            
            return GateResult(
                name="pytest",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors[:10],
            )
        except Exception as e:
            return GateResult(
                name="pytest",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
