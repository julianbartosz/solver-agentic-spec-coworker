"""
Tests for Docker Runner (Tier 1 gate execution).

These tests verify:
1. Docker availability detection
2. 2-phase execution (provision with network, validate without)
3. Lockfile requirements for Tier 1
4. Gate command definitions

Tests that require Docker are skipped when Docker is not available.
"""
import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import shutil

from integration_coworker.codegen.gates.docker_runner import (
    DockerRunner,
    DockerConfig,
    DockerWorkspace,
    DockerNotAvailableError,
    ProvisionError,
    ValidationError,
    GateResult,
    is_docker_available,
    get_gates_for_language,
    TYPESCRIPT_GATES,
    GO_GATES,
)


# Skip all Docker tests if Docker not available
DOCKER_AVAILABLE = is_docker_available()
skip_without_docker = pytest.mark.skipif(
    not DOCKER_AVAILABLE,
    reason="Docker not available"
)


class TestDockerAvailability:
    """Tests for Docker availability detection."""
    
    def test_is_docker_available_returns_bool(self):
        """is_docker_available should return True/False."""
        result = is_docker_available()
        assert isinstance(result, bool)
    
    def test_docker_not_available_raises_error(self):
        """DockerRunner should raise if Docker not found."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            
            with pytest.raises(DockerNotAvailableError) as exc:
                DockerRunner()
            
            assert "not found" in str(exc.value).lower()
    
    def test_docker_daemon_not_responding(self):
        """DockerRunner should raise if daemon not responding."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Cannot connect to daemon"
            )
            
            with pytest.raises(DockerNotAvailableError) as exc:
                DockerRunner()
            
            assert "not responding" in str(exc.value).lower()


class TestGateDefinitions:
    """Tests for gate command definitions."""
    
    def test_typescript_gates_defined(self):
        """TypeScript gates should include tsc, eslint, vitest."""
        gate_names = [g["name"] for g in TYPESCRIPT_GATES]
        assert "tsc" in gate_names
        assert "eslint" in gate_names
        assert "vitest" in gate_names
    
    def test_typescript_tsc_command(self):
        """tsc gate should use --noEmit --pretty false."""
        tsc_gate = next(g for g in TYPESCRIPT_GATES if g["name"] == "tsc")
        cmd = tsc_gate["command"]
        assert "--noEmit" in cmd
        assert "--pretty" in cmd
        assert "false" in cmd
    
    def test_typescript_eslint_command(self):
        """eslint gate should use --format json."""
        eslint_gate = next(g for g in TYPESCRIPT_GATES if g["name"] == "eslint")
        cmd = eslint_gate["command"]
        assert "--format" in cmd
        assert "json" in cmd
        assert "--no-error-on-unmatched-pattern" in cmd
    
    def test_typescript_vitest_command(self):
        """vitest gate should use --reporter=json --outputFile."""
        vitest_gate = next(g for g in TYPESCRIPT_GATES if g["name"] == "vitest")
        cmd = vitest_gate["command"]
        assert "--reporter=json" in cmd
        assert any("--outputFile" in c for c in cmd)
    
    def test_go_gates_defined(self):
        """Go gates should include go_test, go_vet, staticcheck."""
        gate_names = [g["name"] for g in GO_GATES]
        assert "go_test" in gate_names
        assert "go_vet" in gate_names
        assert "staticcheck" in gate_names
    
    def test_go_test_uses_json_flag(self):
        """go test should use -json for structured output."""
        go_test_gate = next(g for g in GO_GATES if g["name"] == "go_test")
        cmd = go_test_gate["command"]
        assert "-json" in cmd
        assert "./..." in cmd
    
    def test_get_gates_for_typescript(self):
        """get_gates_for_language should return TS gates."""
        gates = get_gates_for_language("typescript")
        assert len(gates) == 3
        assert gates == TYPESCRIPT_GATES
    
    def test_get_gates_for_go(self):
        """get_gates_for_language should return Go gates."""
        gates = get_gates_for_language("go")
        assert len(gates) == 3
        assert gates == GO_GATES
    
    def test_get_gates_for_unknown(self):
        """get_gates_for_language should return empty for unknown."""
        gates = get_gates_for_language("cobol")
        assert gates == []


class TestDockerConfig:
    """Tests for DockerConfig defaults."""
    
    def test_default_images(self):
        """Default images should be defined for TS and Go."""
        config = DockerConfig()
        assert "typescript" in config.images
        assert "go" in config.images
        assert "node" in config.images["typescript"]
        assert "golang" in config.images["go"]
    
    def test_default_timeouts(self):
        """Default timeouts should be reasonable."""
        config = DockerConfig()
        assert config.provision_timeout >= 60  # At least 1 minute
        assert config.validate_timeout >= 30
    
    def test_default_resource_limits(self):
        """Resource limits should be set."""
        config = DockerConfig()
        assert config.memory_limit
        assert config.cpu_limit


class TestDockerWorkspace:
    """Tests for workspace management."""
    
    def test_workspace_artifacts_written(self):
        """Workspace should write artifacts to host_path."""
        with patch.object(DockerRunner, "_check_docker_available"):
            runner = DockerRunner()
        
        artifacts = [
            {"path": "src/client.ts", "content": "export class Client {}"},
            {"path": "package.json", "content": '{"name": "test"}'},
        ]
        
        with runner.workspace(artifacts) as ws:
            assert (ws.host_path / "src/client.ts").exists()
            assert (ws.host_path / "package.json").exists()
            
            content = (ws.host_path / "src/client.ts").read_text()
            assert "export class Client" in content
    
    def test_workspace_creates_directories(self):
        """Workspace should create nested directories."""
        with patch.object(DockerRunner, "_check_docker_available"):
            runner = DockerRunner()
        
        artifacts = [
            {"path": "src/deep/nested/file.ts", "content": "// deep"},
        ]
        
        with runner.workspace(artifacts) as ws:
            assert (ws.host_path / "src/deep/nested/file.ts").exists()


class TestProvisionPhase:
    """Tests for Phase 1 (Provision) with mocked Docker."""
    
    def test_provision_requires_lockfile_typescript(self):
        """TypeScript provision should require lockfile."""
        with patch.object(DockerRunner, "_check_docker_available"):
            runner = DockerRunner()
        
        # Create workspace without lockfile
        ws = DockerWorkspace(host_path=Path(tempfile.mkdtemp()))
        (ws.host_path / "package.json").write_text('{"name": "test"}')
        
        try:
            with pytest.raises(ProvisionError) as exc:
                runner.provision(ws, language="typescript")
            
            assert "lockfile" in str(exc.value).lower()
        finally:
            shutil.rmtree(ws.host_path, ignore_errors=True)
    
    def test_provision_requires_go_mod(self):
        """Go provision should require go.mod."""
        with patch.object(DockerRunner, "_check_docker_available"):
            runner = DockerRunner()
        
        # Create workspace without go.mod
        ws = DockerWorkspace(host_path=Path(tempfile.mkdtemp()))
        (ws.host_path / "main.go").write_text("package main")
        
        try:
            with pytest.raises(ProvisionError) as exc:
                runner.provision(ws, language="go")
            
            assert "go.mod" in str(exc.value).lower()
        finally:
            shutil.rmtree(ws.host_path, ignore_errors=True)
    
    def test_provision_unknown_language(self):
        """Provision should fail for unknown language."""
        with patch.object(DockerRunner, "_check_docker_available"):
            runner = DockerRunner()
        
        ws = DockerWorkspace(host_path=Path(tempfile.mkdtemp()))
        
        try:
            with pytest.raises(ProvisionError) as exc:
                runner.provision(ws, language="fortran")
            
            assert "fortran" in str(exc.value).lower()
        finally:
            shutil.rmtree(ws.host_path, ignore_errors=True)


class TestValidatePhase:
    """Tests for Phase 2 (Validate) with mocked Docker."""
    
    def test_validate_requires_provision_first(self):
        """Validate should fail if provision not called."""
        with patch.object(DockerRunner, "_check_docker_available"):
            runner = DockerRunner()
        
        ws = DockerWorkspace(host_path=Path(tempfile.mkdtemp()))
        ws.language = "typescript"
        ws.deps_resolved = False  # Not provisioned
        
        try:
            with pytest.raises(ValidationError) as exc:
                runner.validate(ws, gates=TYPESCRIPT_GATES)
            
            assert "provision" in str(exc.value).lower()
        finally:
            shutil.rmtree(ws.host_path, ignore_errors=True)
    
    def test_validate_uses_network_none(self):
        """Validate should run with --network=none."""
        with patch.object(DockerRunner, "_check_docker_available"):
            runner = DockerRunner()
        
        ws = DockerWorkspace(host_path=Path(tempfile.mkdtemp()))
        ws.language = "typescript"
        ws.deps_resolved = True
        
        with patch.object(runner, "_run_container") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            
            runner.validate(ws, gates=[TYPESCRIPT_GATES[0]])  # Just tsc
            
            # Verify --network=none was used
            assert mock_run.called
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["network"] == "none"
        
        shutil.rmtree(ws.host_path, ignore_errors=True)


@skip_without_docker
class TestDockerIntegration:
    """Integration tests that actually run Docker containers.
    
    These are skipped when Docker is not available.
    """
    
    def test_docker_runner_initializes(self):
        """DockerRunner should initialize when Docker is available."""
        runner = DockerRunner()
        assert runner is not None
    
    def test_simple_container_command(self):
        """Should be able to run a simple command in Docker."""
        runner = DockerRunner()
        
        artifacts = [{"path": "test.txt", "content": "hello"}]
        
        with runner.workspace(artifacts) as ws:
            result = runner._run_container(
                image="alpine:latest",
                workspace=ws,
                command=["cat", "/workspace/test.txt"],
                network="none",
                timeout=30,
            )
            
            assert result.returncode == 0
            assert "hello" in result.stdout
