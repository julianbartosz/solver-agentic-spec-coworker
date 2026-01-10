"""
Docker Runner for Tier 1 (Production) Gate Execution

Implements 2-phase execution model per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md:

Phase 1 (Provision) - Network ALLOWED:
    - Copy artifacts into container workspace
    - Resolve dependencies (npm ci, go mod download)
    - Requires lockfile for determinism
    
Phase 2 (Validate) - Network DISABLED:
    - Run gates (tsc, vitest, go test, etc.)
    - Container started with --network none
    - All deps must already be local

Docker network isolation reference:
https://docs.docker.com/engine/network/drivers/none/

Usage:
    runner = DockerRunner(image="node:20-alpine")
    
    with runner.workspace(artifacts) as workspace:
        # Phase 1: Provision (network allowed)
        runner.provision(workspace, language="typescript")
        
        # Phase 2: Validate (network disabled)
        results = runner.validate(workspace, language="typescript")
"""
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any
from contextlib import contextmanager

# Import canonical exception from base module (single source of truth)
from integration_coworker.codegen.gates.base import ProvisionError

logger = logging.getLogger(__name__)


class DockerNotAvailableError(Exception):
    """Raised when Docker is not available on the host."""
    pass


class ValidationError(Exception):
    """Raised when validation fails unexpectedly (not a gate failure)."""
    pass


@dataclass
class GateResult:
    """Result of running a single gate in Docker."""
    name: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    command: List[str]


@dataclass
class DockerWorkspace:
    """Represents a workspace for Docker execution."""
    host_path: Path
    container_path: str = "/workspace"
    container_id: Optional[str] = None
    deps_resolved: bool = False
    language: Optional[str] = None


@dataclass
class DockerConfig:
    """Configuration for Docker runner."""
    # Container images per language
    images: Dict[str, str] = field(default_factory=lambda: {
        "typescript": "node:20-alpine",
        "javascript": "node:20-alpine",
        "go": "golang:1.21-alpine",
    })
    
    # Timeout settings
    provision_timeout: int = 300  # 5 minutes for npm ci / go mod download
    validate_timeout: int = 120   # 2 minutes for gates
    
    # Resource limits
    memory_limit: str = "2g"
    cpu_limit: str = "2"
    
    # Cleanup
    cleanup_on_success: bool = True
    cleanup_on_failure: bool = False


class DockerRunner:
    """
    Docker-based sandbox runner for Tier 1 gate execution.
    
    Provides deterministic validation by:
    1. Running in containers (consistent toolchain)
    2. Requiring lockfiles (deterministic deps)
    3. Disabling network during validation (no external calls)
    """
    
    def __init__(self, config: Optional[DockerConfig] = None):
        self.config = config or DockerConfig()
        self._check_docker_available()
    
    def _check_docker_available(self) -> None:
        """Verify Docker is available and running."""
        try:
            result = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise DockerNotAvailableError(
                    f"Docker not responding: {result.stderr}"
                )
            logger.debug(f"Docker version: {result.stdout.strip()}")
        except FileNotFoundError:
            raise DockerNotAvailableError(
                "Docker CLI not found. Install Docker to use Tier 1 validation."
            )
        except subprocess.TimeoutExpired:
            raise DockerNotAvailableError(
                "Docker daemon not responding (timeout)"
            )
    
    @contextmanager
    def workspace(self, artifacts: List[Dict[str, str]]):
        """
        Create a temporary workspace for Docker execution.
        
        Args:
            artifacts: List of {"path": "src/foo.ts", "content": "..."} dicts
            
        Yields:
            DockerWorkspace object
        """
        workspace_dir = tempfile.mkdtemp(prefix="codegen_docker_")
        workspace = DockerWorkspace(host_path=Path(workspace_dir))
        
        try:
            # Write artifacts to workspace
            for artifact in artifacts:
                file_path = workspace.host_path / artifact["path"]
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(artifact["content"])
                logger.debug(f"Wrote artifact: {artifact['path']}")
            
            yield workspace
            
        finally:
            # Cleanup
            should_cleanup = (
                (workspace.deps_resolved and self.config.cleanup_on_success) or
                (not workspace.deps_resolved and self.config.cleanup_on_failure)
            )
            if should_cleanup and workspace.host_path.exists():
                shutil.rmtree(workspace.host_path, ignore_errors=True)
                logger.debug(f"Cleaned up workspace: {workspace.host_path}")
    
    def provision(
        self,
        workspace: DockerWorkspace,
        language: str,
    ) -> None:
        """
        Phase 1: Provision workspace with dependencies (network allowed).
        
        - TypeScript: npm ci (requires package-lock.json)
        - Go: go mod download (requires go.mod)
        
        Args:
            workspace: DockerWorkspace from workspace() context
            language: "typescript" or "go"
            
        Raises:
            ProvisionError: If dependency resolution fails
        """
        workspace.language = language
        image = self.config.images.get(language)
        if not image:
            raise ProvisionError(f"No Docker image configured for {language}")
        
        # Pull image if needed
        self._pull_image(image)
        
        # Determine provision commands
        if language in ("typescript", "javascript"):
            # Verify lockfile exists
            lockfile = workspace.host_path / "package-lock.json"
            pnpm_lock = workspace.host_path / "pnpm-lock.yaml"
            if not lockfile.exists() and not pnpm_lock.exists():
                raise ProvisionError(
                    "Tier 1 requires lockfile. Missing package-lock.json or pnpm-lock.yaml"
                )
            
            # Determine package manager
            if pnpm_lock.exists():
                provision_cmd = ["pnpm", "install", "--frozen-lockfile"]
            else:
                # npm ci for deterministic installs - requires exact lockfile match
                # This is intentional for Tier.PROD: lockfile must be correct
                provision_cmd = ["npm", "ci"]
                
        elif language == "go":
            # Verify go.mod exists
            go_mod = workspace.host_path / "go.mod"
            if not go_mod.exists():
                raise ProvisionError(
                    "Tier 1 requires go.mod for Go projects"
                )
            provision_cmd = ["go", "mod", "download"]
            
        else:
            raise ProvisionError(f"Unknown language: {language}")
        
        # Run provision in container (network allowed)
        logger.info(f"Provisioning {language} dependencies...")
        result = self._run_container(
            image=image,
            workspace=workspace,
            command=provision_cmd,
            network="bridge",  # Network ALLOWED for provision
            timeout=self.config.provision_timeout,
        )
        
        if result.returncode != 0:
            raise ProvisionError(
                f"Dependency resolution failed:\n{result.stderr}\n{result.stdout}"
            )
        
        workspace.deps_resolved = True
        logger.info(f"Provisioned {language} workspace successfully")
    
    def validate(
        self,
        workspace: DockerWorkspace,
        gates: List[Dict[str, Any]],
    ) -> List[GateResult]:
        """
        Phase 2: Run validation gates (network disabled).
        
        Args:
            workspace: Provisioned DockerWorkspace
            gates: List of gate definitions: [{"name": "tsc", "command": ["npx", "tsc", ...]}]
            
        Returns:
            List of GateResult objects
            
        Raises:
            ValidationError: If deps not resolved or Docker fails
        """
        if not workspace.deps_resolved:
            raise ValidationError(
                "Cannot validate before provisioning. Call provision() first."
            )
        
        image = self.config.images.get(workspace.language)
        if not image:
            raise ValidationError(f"No Docker image for {workspace.language}")
        
        results = []
        
        for gate in gates:
            name = gate["name"]
            command = gate["command"]
            
            logger.info(f"Running gate: {name}")
            start_time = time.time()
            
            # Run gate with --network none (NO network access)
            result = self._run_container(
                image=image,
                workspace=workspace,
                command=command,
                network="none",  # Network DISABLED for validation
                timeout=self.config.validate_timeout,
            )
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            gate_result = GateResult(
                name=name,
                passed=(result.returncode == 0),
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                command=command,
            )
            results.append(gate_result)
            
            if gate_result.passed:
                logger.info(f"  ✅ {name} passed ({duration_ms}ms)")
            else:
                logger.warning(f"  ❌ {name} failed (exit={result.returncode})")
        
        return results
    
    def _pull_image(self, image: str) -> None:
        """Pull Docker image if not present locally."""
        # Check if image exists
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
        )
        
        if result.returncode == 0:
            logger.debug(f"Image {image} already present")
            return
        
        # Pull image
        logger.info(f"Pulling Docker image: {image}")
        result = subprocess.run(
            ["docker", "pull", image],
            capture_output=True,
            text=True,
            timeout=300,
        )
        
        if result.returncode != 0:
            raise DockerNotAvailableError(
                f"Failed to pull image {image}: {result.stderr}"
            )
    
    def _run_container(
        self,
        image: str,
        workspace: DockerWorkspace,
        command: List[str],
        network: str,
        timeout: int,
    ) -> subprocess.CompletedProcess:
        """
        Run a command in a Docker container.
        
        Args:
            image: Docker image name
            workspace: Workspace with host_path
            command: Command to run
            network: "bridge" (provision) or "none" (validate)
            timeout: Timeout in seconds
            
        Returns:
            CompletedProcess with returncode, stdout, stderr
        """
        docker_cmd = [
            "docker", "run",
            "--rm",  # Remove container after exit
            f"--network={network}",  # Network mode
            f"--memory={self.config.memory_limit}",
            f"--cpus={self.config.cpu_limit}",
            "-v", f"{workspace.host_path}:{workspace.container_path}:rw",
            "-w", workspace.container_path,
            image,
        ] + command
        
        logger.debug(f"Running: {' '.join(docker_cmd)}")
        
        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result
        except subprocess.TimeoutExpired as e:
            return subprocess.CompletedProcess(
                args=docker_cmd,
                returncode=124,  # Timeout exit code
                stdout=e.stdout or "",
                stderr=f"Command timed out after {timeout}s",
            )


def is_docker_available() -> bool:
    """Check if Docker is available for Tier 1 execution."""
    try:
        result = subprocess.run(
            ["docker", "version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# Gate command definitions per language
TYPESCRIPT_GATES = [
    {
        "name": "tsc",
        "command": ["npx", "tsc", "--noEmit", "--pretty", "false"],
    },
    {
        "name": "eslint",
        "command": ["npx", "eslint", ".", "--format", "json", "--no-error-on-unmatched-pattern"],
    },
    {
        "name": "vitest",
        "command": ["npx", "vitest", "run", "--reporter=json", "--outputFile=vitest-results.json"],
    },
]

GO_GATES = [
    {
        "name": "go_test",
        "command": ["go", "test", "-json", "./..."],
    },
    {
        "name": "go_vet",
        "command": ["go", "vet", "./..."],
    },
    {
        "name": "staticcheck",
        "command": ["staticcheck", "./..."],
    },
]


def get_gates_for_language(language: str) -> List[Dict[str, Any]]:
    """Get gate definitions for a language."""
    gates = {
        "typescript": TYPESCRIPT_GATES,
        "javascript": TYPESCRIPT_GATES,
        "go": GO_GATES,
    }
    return gates.get(language, [])
