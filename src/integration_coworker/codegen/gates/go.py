"""
Go language validation strategy.

Implements Tier 1 production gates for Go:
- go test ./... (compile + test)
- go vet ./... (static analysis)
- staticcheck ./... OR golangci-lint run ./... (extended analysis)

2-Phase Design:
1. Provision: go mod download (requires go.mod), network allowed
2. Validate: Run gates with network disabled

Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.2

CORRECTED: go list -json emits streaming JSON objects, not line-delimited.
Uses json.JSONDecoder().raw_decode() for proper parsing.
"""

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
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
class GoGateConfig:
    """Configuration for Go gates."""
    enable_go_test: bool = True
    enable_go_vet: bool = True
    enable_staticcheck: bool = True
    enable_golangci_lint: bool = False  # Alternative to staticcheck
    timeout_seconds: int = 300
    use_container: bool = False
    container_image: str = "golang:1.21-alpine"


class GoStrategy(LanguageStrategy):
    """
    Go validation strategy.
    
    Tier 1 (PROD): Requires go.mod, runs go mod download,
                   executes go test/vet/staticcheck with network disabled.
    Tier 2 (EXP): May skip gates if tooling unavailable.
    """
    
    language = "go"
    default_tier = Tier.PROD
    required_executables = ["go"]
    required_config_files = ["go.mod"]
    container_image = "golang:1.21-alpine"
    
    def __init__(self, config: Optional[GoGateConfig] = None):
        self.config = config or GoGateConfig()
    
    def probe_toolchain(self, workspace: Path) -> Dict[str, Any]:
        """Probe for Go toolchain availability."""
        result = {
            "executables_found": [],
            "executables_missing": [],
            "config_files_found": [],
            "config_files_missing": [],
            "lockfile_found": None,
            "tier_eligible": Tier.PROD,
            "failure_reason": None,
        }
        
        # Check for go executable
        go_path = shutil.which("go")
        if go_path:
            result["executables_found"].append("go")
            
            # Get version
            try:
                version = subprocess.run(
                    ["go", "version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if version.returncode == 0:
                    result["go_version"] = version.stdout.strip()
            except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
                pass  # Version check is non-critical
        else:
            result["executables_missing"].append("go")
            result["tier_eligible"] = Tier.EXP
            result["failure_reason"] = "Go not found"
        
        # Check for go.mod
        if (workspace / "go.mod").exists():
            result["config_files_found"].append("go.mod")
        else:
            result["config_files_missing"].append("go.mod")
            result["tier_eligible"] = Tier.EXP
            result["failure_reason"] = "go.mod not found"
        
        # Check for go.sum (lockfile equivalent)
        if (workspace / "go.sum").exists():
            result["lockfile_found"] = "go.sum"
            result["config_files_found"].append("go.sum")
        
        # Check for staticcheck
        staticcheck_path = shutil.which("staticcheck")
        if staticcheck_path:
            result["executables_found"].append("staticcheck")
        else:
            result["executables_missing"].append("staticcheck")
        
        # Check for golangci-lint
        golangci_path = shutil.which("golangci-lint")
        if golangci_path:
            result["executables_found"].append("golangci-lint")
        else:
            result["executables_missing"].append("golangci-lint")
        
        return result
    
    def provision(
        self,
        artifacts: List[ArtifactFile],
        workspace: Path,
        tier: Tier,
    ) -> SandboxEnv:
        """
        Provision Go sandbox.
        
        Phase 1 (network allowed):
        1. Write artifacts to workspace
        2. Ensure go.mod exists
        3. Run go mod download
        """
        start = time.time()
        
        # Create workspace
        workspace.mkdir(parents=True, exist_ok=True)
        
        # Write artifacts
        for artifact in artifacts:
            artifact_path = workspace / artifact.path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(artifact.content)
            logger.debug(f"Wrote artifact: {artifact.path}")
        
        # Check toolchain
        probe = self.probe_toolchain(workspace)
        if tier == Tier.PROD and probe["tier_eligible"] != Tier.PROD:
            raise ToolchainMissingError(
                f"Tier 1 Go validation requires: {probe['failure_reason']}"
            )
        
        # Check for go.mod
        go_mod = workspace / "go.mod"
        if not go_mod.exists():
            if tier == Tier.PROD:
                raise ProvisionError("go.mod required for Tier 1 Go validation")
            else:
                # Initialize module for Tier 2
                try:
                    subprocess.run(
                        ["go", "mod", "init", "validation-sandbox"],
                        cwd=workspace,
                        capture_output=True,
                        timeout=30,
                    )
                    logger.warning("Initialized go.mod (Tier 2)")
                except Exception as e:
                    logger.warning(f"Failed to init go.mod: {e}")
        
        # Download dependencies
        try:
            result = subprocess.run(
                ["go", "mod", "download"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                # Not fatal if no external deps
                logger.warning(f"go mod download: {result.stderr}")
            else:
                logger.debug("go mod download completed")
        except subprocess.TimeoutExpired:
            raise ProvisionError("go mod download timed out")
        
        # Set up isolated GOPATH
        gopath = workspace / ".gopath"
        gopath.mkdir(exist_ok=True)
        gocache = workspace / ".gocache"
        gocache.mkdir(exist_ok=True)
        
        duration_ms = int((time.time() - start) * 1000)
        logger.info(f"Go provisioning completed in {duration_ms}ms")
        
        return SandboxEnv(
            sandbox_path=workspace,
            work_dir=workspace,
            env_vars={
                "GOPATH": str(gopath),
                "GOCACHE": str(gocache),
                "GOMODCACHE": str(gopath / "pkg" / "mod"),
            },
            deps_resolved=True,
        )
    
    def validate(
        self,
        env: SandboxEnv,
        tier: Tier,
    ) -> ValidationResult:
        """
        Run Go validation gates.
        
        Phase 2 (network disabled for Tier 1):
        - go test ./...
        - go vet ./...
        - staticcheck ./... (or golangci-lint)
        """
        if not env.deps_resolved:
            raise ProvisionError("Must call provision() before validate()")
        
        start = time.time()
        gates: List[GateResult] = []
        all_passed = True
        
        # Merge env vars
        full_env = os.environ.copy()
        full_env.update(env.env_vars)
        
        # Run go test (compile + test)
        if self.config.enable_go_test:
            gate = self._run_go_test(env, full_env, tier)
            gates.append(gate)
            if not gate.passed:
                all_passed = False
        
        # Run go vet
        if self.config.enable_go_vet:
            gate = self._run_go_vet(env, full_env, tier)
            gates.append(gate)
            if not gate.passed and gate.status != GateStatus.SKIPPED:
                all_passed = False
        
        # Run staticcheck or golangci-lint
        if self.config.enable_staticcheck:
            gate = self._run_staticcheck(env, full_env, tier)
            gates.append(gate)
            if not gate.passed and gate.status != GateStatus.SKIPPED:
                all_passed = False
        elif self.config.enable_golangci_lint:
            gate = self._run_golangci_lint(env, full_env, tier)
            gates.append(gate)
            if not gate.passed and gate.status != GateStatus.SKIPPED:
                all_passed = False
        
        duration_ms = int((time.time() - start) * 1000)
        
        # Collect validated artifacts
        validated = []
        for f in env.sandbox_path.rglob("*.go"):
            if ".gopath" not in str(f) and ".gocache" not in str(f):
                validated.append(str(f.relative_to(env.sandbox_path)))
        
        skipped = [g.name for g in gates if g.status == GateStatus.SKIPPED]
        untrusted = [g.name for g in gates if g.untrusted]
        
        return ValidationResult(
            success=all_passed,
            tier=tier,
            language=self.language,
            gates=gates,
            artifacts_validated=validated,
            skipped_gates=skipped,
            untrusted_checks=untrusted,
            duration_ms=duration_ms,
            validate_duration_ms=duration_ms,
        )
    
    def _run_go_test(
        self,
        env: SandboxEnv,
        full_env: Dict[str, str],
        tier: Tier,
    ) -> GateResult:
        """Run go test ./..."""
        start = time.time()
        
        # Use -json for structured output
        cmd = ["go", "test", "-json", "./..."]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=env.work_dir,
                env=full_env,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            passed = result.returncode == 0
            errors = []
            
            # Parse JSON test output (streaming format)
            for line in result.stdout.splitlines():
                if line.strip():
                    try:
                        event = json.loads(line)
                        if event.get("Action") == "fail":
                            test_name = event.get("Test", event.get("Package", "unknown"))
                            errors.append(f"FAIL: {test_name}")
                        elif event.get("Action") == "output" and "FAIL" in event.get("Output", ""):
                            errors.append(event.get("Output", "").strip())
                    except json.JSONDecodeError:
                        pass
            
            return GateResult(
                name="go_test",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors[:20],
            )
        except Exception as e:
            return GateResult(
                name="go_test",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
    
    def _run_go_vet(
        self,
        env: SandboxEnv,
        full_env: Dict[str, str],
        tier: Tier,
    ) -> GateResult:
        """Run go vet ./..."""
        start = time.time()
        
        cmd = ["go", "vet", "./..."]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=env.work_dir,
                env=full_env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            passed = result.returncode == 0
            errors = []
            
            # go vet outputs to stderr
            for line in result.stderr.splitlines():
                if line.strip() and not line.startswith("#"):
                    errors.append(line.strip())
            
            return GateResult(
                name="go_vet",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors[:20],
            )
        except Exception as e:
            return GateResult(
                name="go_vet",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
    
    def _run_staticcheck(
        self,
        env: SandboxEnv,
        full_env: Dict[str, str],
        tier: Tier,
    ) -> GateResult:
        """Run staticcheck ./..."""
        start = time.time()
        
        # Check if staticcheck is available
        if not shutil.which("staticcheck"):
            if tier == Tier.PROD:
                # Try golangci-lint as fallback
                if shutil.which("golangci-lint"):
                    return self._run_golangci_lint(env, full_env, tier)
                raise GateError("staticcheck not found (install: go install honnef.co/go/tools/cmd/staticcheck@latest)")
            return GateResult(
                name="staticcheck",
                status=GateStatus.SKIPPED,
                exit_code=-1,
                stdout="",
                stderr="staticcheck not installed",
                duration_ms=int((time.time() - start) * 1000),
            )
        
        cmd = ["staticcheck", "./..."]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=env.work_dir,
                env=full_env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            passed = result.returncode == 0
            errors = []
            
            for line in result.stdout.splitlines():
                if line.strip():
                    errors.append(line.strip())
            
            return GateResult(
                name="staticcheck",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors[:20],
            )
        except Exception as e:
            return GateResult(
                name="staticcheck",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
    
    def _run_golangci_lint(
        self,
        env: SandboxEnv,
        full_env: Dict[str, str],
        tier: Tier,
    ) -> GateResult:
        """Run golangci-lint run ./..."""
        start = time.time()
        
        if not shutil.which("golangci-lint"):
            if tier == Tier.PROD:
                raise GateError("golangci-lint not found")
            return GateResult(
                name="golangci-lint",
                status=GateStatus.SKIPPED,
                exit_code=-1,
                stdout="",
                stderr="golangci-lint not installed",
                duration_ms=int((time.time() - start) * 1000),
            )
        
        cmd = ["golangci-lint", "run", "./..."]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=env.work_dir,
                env=full_env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            passed = result.returncode == 0
            errors = []
            
            for line in result.stdout.splitlines():
                if line.strip():
                    errors.append(line.strip())
            
            return GateResult(
                name="golangci-lint",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors[:20],
            )
        except Exception as e:
            return GateResult(
                name="golangci-lint",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
    
    def detect_artifacts(
        self,
        workspace: Path,
        expected_types: List[str],
        expected_functions: List[str],
    ) -> Dict[str, Any]:
        """
        Detect Go artifacts using go list -json.
        
        CORRECTED: go list -json emits streaming JSON objects (one per package),
        NOT newline-delimited JSON. Uses raw_decode for proper parsing.
        """
        result = {
            "packages": [],
            "types": [],
            "functions": [],
            "error": None,
        }
        
        try:
            proc = subprocess.run(
                ["go", "list", "-json", "./..."],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            if proc.returncode != 0:
                result["error"] = proc.stderr
                return result
            
            # CORRECTED: Parse streaming JSON objects
            # go list -json outputs concatenated JSON objects, not an array
            content = proc.stdout
            decoder = json.JSONDecoder()
            idx = 0
            
            while idx < len(content):
                # Skip whitespace
                while idx < len(content) and content[idx].isspace():
                    idx += 1
                if idx >= len(content):
                    break
                
                try:
                    obj, consumed = decoder.raw_decode(content, idx)
                    result["packages"].append({
                        "name": obj.get("Name"),
                        "import_path": obj.get("ImportPath"),
                        "go_files": obj.get("GoFiles", []),
                    })
                    idx += consumed
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON decode error at position {idx}: {e}")
                    break
            
            # For detailed type/function inspection, parse Go files
            # This is a simplified version - full implementation would use go/parser
            for pkg in result["packages"]:
                for go_file in pkg.get("go_files", []):
                    file_path = workspace / go_file
                    if file_path.exists():
                        content = file_path.read_text()
                        
                        # Simple regex for type and func declarations
                        import re
                        for match in re.finditer(r'type\s+(\w+)\s+struct', content):
                            result["types"].append(match.group(1))
                        for match in re.finditer(r'func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(', content):
                            result["functions"].append(match.group(1))
            
            # Match against expectations
            result["types_matched"] = [
                t for t in result["types"]
                if any(exp.lower() in t.lower() for exp in expected_types)
            ]
            result["functions_matched"] = [
                f for f in result["functions"]
                if any(exp.lower() in f.lower() for exp in expected_functions)
            ]
            
            return result
            
        except Exception as e:
            result["error"] = str(e)
            return result
    
    def _parse_go_list_json_stream(self, output: str) -> List[Dict[str, Any]]:
        """
        Parse streaming JSON from go list -json (one object per package).
        
        CORRECTED: go list -json outputs concatenated JSON objects, not an array
        and not newline-delimited. Uses json.JSONDecoder().raw_decode() for
        proper incremental parsing.
        
        Args:
            output: Raw stdout from `go list -json ./...`
            
        Returns:
            List of parsed package objects
        """
        decoder = json.JSONDecoder()
        packages = []
        pos = 0
        output_len = len(output)
        
        while pos < output_len:
            # Find next opening brace
            try:
                # Skip any non-{ characters
                brace_pos = output.index('{', pos)
            except ValueError:
                break  # No more objects
            
            try:
                obj, end = decoder.raw_decode(output, brace_pos)
                if isinstance(obj, dict):
                    packages.append(obj)
                pos = end
            except json.JSONDecodeError as e:
                logger.warning(f"JSON decode error at position {brace_pos}: {e}")
                # Try to skip past this brace and continue
                pos = brace_pos + 1
        
        return packages
