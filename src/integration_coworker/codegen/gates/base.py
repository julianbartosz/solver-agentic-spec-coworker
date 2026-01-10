"""
Base classes for multi-language validation gates.

Implements the 2-phase design:
1. Provision Phase (network allowed): Install dependencies
2. Validate Phase (network disabled): Run gates

Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.2
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Literal, Optional, Any
import logging

logger = logging.getLogger(__name__)


class Tier(Enum):
    """Validation tier with different guarantees."""
    PROD = "prod"  # Tier 1: Compiler-backed, no regex, fail on missing toolchain
    EXP = "exp"    # Tier 2: Regex allowed, may skip gates, marked UNTRUSTED


class GateStatus(Enum):
    """Result status of a single gate."""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"  # Tier 2 only - tooling unavailable


class ProvisionError(Exception):
    """Raised when provisioning fails (e.g., npm ci fails, missing lockfile)."""
    pass


class GateError(Exception):
    """Raised when a gate fails unexpectedly (not a validation failure)."""
    pass


class ToolchainMissingError(Exception):
    """Raised when required toolchain is not available (Tier 1 blocker)."""
    pass


@dataclass
class GateResult:
    """Result of running a single validation gate."""
    name: str                          # e.g., "tsc", "eslint", "vitest", "go_test"
    status: GateStatus
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    artifacts_validated: List[str] = field(default_factory=list)  # Files that passed this gate
    errors: List[str] = field(default_factory=list)               # Parsed error messages
    untrusted: bool = False            # True if regex-based (Tier 2 only)
    output_file: Optional[str] = None  # Path to structured output file (e.g., vitest-results.json)
    
    @property
    def passed(self) -> bool:
        return self.status == GateStatus.PASSED
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:2000] if len(self.stdout) > 2000 else self.stdout,
            "stderr": self.stderr[:2000] if len(self.stderr) > 2000 else self.stderr,
            "duration_ms": self.duration_ms,
            "artifacts_validated": self.artifacts_validated,
            "errors": self.errors,
            "untrusted": self.untrusted,
        }


@dataclass
class SandboxEnv:
    """Prepared sandbox environment."""
    sandbox_path: Path                 # Root of sandbox directory
    work_dir: Path                     # Working directory for commands
    env_vars: Dict[str, str] = field(default_factory=dict)  # e.g., {"GOPATH": "/sandbox/.go"}
    container_id: Optional[str] = None # Docker container ID if containerized
    deps_resolved: bool = False        # True after provision phase completes
    network_disabled: bool = False     # True during validate phase (Tier 1)
    
    def __post_init__(self):
        self.sandbox_path = Path(self.sandbox_path)
        self.work_dir = Path(self.work_dir)


@dataclass
class ValidationResult:
    """Structured output from sandbox validation."""
    success: bool
    tier: Tier
    language: str
    gates: List[GateResult]
    artifacts_validated: List[str]
    
    # Tier compliance markers
    untrusted_checks: List[str] = field(default_factory=list)  # Empty for Tier 1
    skipped_gates: List[str] = field(default_factory=list)     # Empty for Tier 1
    
    # Debugging
    container_id: Optional[str] = None
    duration_ms: int = 0
    provision_duration_ms: int = 0
    validate_duration_ms: int = 0
    error_message: Optional[str] = None
    
    def is_tier1_compliant(self) -> bool:
        """Returns True only if all checks are compiler-backed."""
        return (
            self.tier == Tier.PROD
            and len(self.untrusted_checks) == 0
            and len(self.skipped_gates) == 0
            and self.success
        )
    
    @property
    def failed_gates(self) -> List[GateResult]:
        return [g for g in self.gates if not g.passed]
    
    @property
    def passed_gates(self) -> List[GateResult]:
        return [g for g in self.gates if g.passed]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "tier": self.tier.value,
            "language": self.language,
            "gates": [g.to_dict() for g in self.gates],
            "artifacts_validated": self.artifacts_validated,
            "untrusted_checks": self.untrusted_checks,
            "skipped_gates": self.skipped_gates,
            "container_id": self.container_id,
            "duration_ms": self.duration_ms,
            "is_tier1_compliant": self.is_tier1_compliant(),
            "error_message": self.error_message,
        }


@dataclass
class ArtifactFile:
    """A generated code artifact to validate."""
    path: str      # Relative path within sandbox, e.g. "src/module.ts"
    content: str
    language: Optional[str] = None  # Detected or specified language
    
    def __post_init__(self):
        # Normalize path separators
        self.path = self.path.replace("\\", "/")
        
        # Auto-detect language from extension if not specified
        if self.language is None:
            ext = Path(self.path).suffix.lower()
            self.language = {
                ".py": "python",
                ".ts": "typescript",
                ".tsx": "typescript",
                ".js": "javascript",
                ".jsx": "javascript",
                ".go": "go",
                ".java": "java",
                ".rb": "ruby",
                ".cs": "csharp",
            }.get(ext)


class LanguageStrategy(ABC):
    """
    Abstract base class for language-specific validation strategies.
    
    Implements 2-phase design:
    1. provision(): Network allowed - install deps, setup workspace
    2. validate(): Network disabled - run gates
    
    Tier 1 (PROD) requirements:
    - Must NOT skip gates silently
    - Must use compiler/toolchain-based validation (no regex)
    - Must fail if required toolchain missing
    
    Tier 2 (EXP) allowances:
    - May skip gates if tooling unavailable (logged)
    - May use regex-based checks (marked UNTRUSTED)
    """
    
    # Language identifier (lowercase)
    language: str = ""
    
    # Default tier for this language
    default_tier: Tier = Tier.EXP
    
    # Required executables for Tier 1 (probed before gates)
    required_executables: List[str] = []
    
    # Required config files for Tier 1 (probed in workspace)
    required_config_files: List[str] = []
    
    # Container image for Tier 1 (if containerized)
    container_image: Optional[str] = None
    
    @abstractmethod
    def probe_toolchain(self, workspace: Path) -> Dict[str, Any]:
        """
        Probe workspace for toolchain availability.
        
        Returns dict with:
        - executables_found: List[str]
        - executables_missing: List[str]
        - config_files_found: List[str]
        - config_files_missing: List[str]
        - lockfile_found: Optional[str]
        - tier_eligible: Tier  (PROD if all requirements met, else EXP)
        - failure_reason: Optional[str]
        """
        raise NotImplementedError
    
    @abstractmethod
    def provision(
        self,
        artifacts: List[ArtifactFile],
        workspace: Path,
        tier: Tier,
    ) -> SandboxEnv:
        """
        Provision sandbox environment (Phase 1 - network allowed).
        
        - Writes artifacts to workspace
        - Installs dependencies (npm ci, go mod download, etc.)
        - Returns SandboxEnv with deps_resolved=True
        
        Raises:
            ProvisionError: If provisioning fails (missing lockfile, npm ci fails, etc.)
            ToolchainMissingError: If Tier 1 required toolchain is missing
        """
        raise NotImplementedError
    
    @abstractmethod
    def validate(
        self,
        env: SandboxEnv,
        tier: Tier,
    ) -> ValidationResult:
        """
        Run validation gates (Phase 2 - network disabled for Tier 1).
        
        Tier 1: All gates must run and pass/fail. No skipping.
        Tier 2: Gates may be skipped if tooling unavailable.
        
        Returns ValidationResult with all gate outcomes.
        
        Raises:
            ProvisionError: If env.deps_resolved is False
            GateError: If gate execution fails unexpectedly
        """
        if not env.deps_resolved:
            raise ProvisionError("Must call provision() before validate()")
        raise NotImplementedError
    
    def cleanup(self, env: SandboxEnv) -> None:
        """
        Cleanup sandbox resources.
        
        Default implementation removes sandbox directory.
        Override for container cleanup.
        """
        import shutil
        if env.sandbox_path.exists():
            try:
                shutil.rmtree(env.sandbox_path)
                logger.debug(f"Cleaned up sandbox: {env.sandbox_path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup sandbox {env.sandbox_path}: {e}")
        
        if env.container_id:
            # Subclass should override to stop/remove container
            logger.warning(f"Container {env.container_id} not cleaned up (override cleanup())")
    
    def run_command(
        self,
        command: List[str],
        cwd: Path,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 60,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess:
        """
        Run a command in the workspace.
        
        Helper for subclasses to execute gates.
        """
        import time
        
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        
        start = time.time()
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=full_env,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
            )
            duration_ms = int((time.time() - start) * 1000)
            logger.debug(
                f"Command {' '.join(command[:3])}... "
                f"returned {result.returncode} in {duration_ms}ms"
            )
            return result
        except subprocess.TimeoutExpired as e:
            logger.error(f"Command timed out after {timeout}s: {' '.join(command[:3])}")
            raise
