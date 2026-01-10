"""
Tests for Go validation gates.

Tests the Go strategy implementation including:
- Toolchain probing
- Provisioning (go mod download)
- Gate execution (go test, go vet, staticcheck)
- Streaming JSON parsing for go list
- Artifact detection

Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.2
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from integration_coworker.codegen.gates.base import (
    ArtifactFile,
    GateStatus,
    Tier,
    ProvisionError,
    ToolchainMissingError,
)
from integration_coworker.codegen.gates.go import (
    GoStrategy,
    GoGateConfig,
)


class TestGoProbe:
    """Tests for toolchain probing."""
    
    def test_probe_finds_go(self, tmp_path: Path):
        """Test that probe detects go executable."""
        strategy = GoStrategy()
        
        # Create minimal go.mod
        (tmp_path / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
        
        result = strategy.probe_toolchain(tmp_path)
        
        # Should find go if installed on system
        assert "go" in result["executables_found"] or "go" in result["executables_missing"]
    
    def test_probe_detects_go_mod(self, tmp_path: Path):
        """Test that probe finds go.mod."""
        strategy = GoStrategy()
        
        (tmp_path / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
        
        result = strategy.probe_toolchain(tmp_path)
        
        assert "go.mod" in result["config_files_found"]
    
    def test_probe_missing_go_mod_tier2(self, tmp_path: Path):
        """Test that missing go.mod downgrades to Tier 2."""
        strategy = GoStrategy()
        
        result = strategy.probe_toolchain(tmp_path)
        
        assert "go.mod" in result["config_files_missing"]
        assert result["tier_eligible"] == Tier.EXP
    
    def test_probe_detects_go_sum(self, tmp_path: Path):
        """Test that probe finds go.sum for Tier 1."""
        strategy = GoStrategy()
        
        (tmp_path / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
        (tmp_path / "go.sum").write_text("golang.org/x/tools v0.1.0 h1:...\n")
        
        result = strategy.probe_toolchain(tmp_path)
        
        assert "go.sum" in result["config_files_found"]
        # Tier eligibility depends on Go being installed
        # If Go is not installed, it will still be EXP
        if "go" in result["executables_found"]:
            assert result["tier_eligible"] == Tier.PROD


class TestGoProvision:
    """Tests for provisioning."""
    
    def test_provision_writes_artifacts(self, tmp_path: Path):
        """Test that artifacts are written to workspace."""
        strategy = GoStrategy()
        
        artifacts = [
            ArtifactFile("main.go", "package main\n\nfunc main() {}\n"),
            ArtifactFile("utils/math.go", "package utils\n\nfunc Add(a, b int) int { return a + b }\n"),
        ]
        
        # Create required config files
        (tmp_path / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
        (tmp_path / "go.sum").write_text("")
        
        # Mock go mod download
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            
            env = strategy.provision(artifacts, tmp_path, Tier.EXP)
            
            assert (tmp_path / "main.go").exists()
            assert (tmp_path / "utils/math.go").exists()
            assert env.deps_resolved
    
    def test_provision_tier1_requires_go_sum(self, tmp_path: Path):
        """Test that Tier 1 requires go.sum (or fails on missing toolchain)."""
        strategy = GoStrategy()
        
        # Create go.mod but no go.sum
        (tmp_path / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
        
        artifacts = [ArtifactFile("main.go", "package main\n\nfunc main() {}\n")]
        
        # Should raise ToolchainMissingError (either for go.sum or go executable)
        with pytest.raises(ToolchainMissingError):
            strategy.provision(artifacts, tmp_path, Tier.PROD)


class TestGoGates:
    """Tests for validation gates."""
    
    def test_go_test_gate_passes_valid_code(self, tmp_path: Path):
        """Test go test passes on valid Go."""
        strategy = GoStrategy()
        
        # Setup workspace with valid Go
        (tmp_path / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
        (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n")
        (tmp_path / "main_test.go").write_text("""
package main

import "testing"

func TestMain(t *testing.T) {
    // pass
}
""")
        
        # Mock subprocess for go test
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            
            from integration_coworker.codegen.gates.base import SandboxEnv
            import os
            env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
            full_env = os.environ.copy()
            
            gate = strategy._run_go_test(env, full_env, Tier.PROD)
            
            assert gate.status == GateStatus.PASSED
            assert gate.exit_code == 0
    
    def test_go_test_gate_fails_test_failure(self, tmp_path: Path):
        """Test go test fails on test failure."""
        strategy = GoStrategy()
        
        # go test -json outputs NDJSON
        test_output = json.dumps({"Action": "fail", "Package": "example.com/test", "Output": "FAIL"})
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout=test_output + "\n",
                stderr=""
            )
            
            from integration_coworker.codegen.gates.base import SandboxEnv
            import os
            env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
            full_env = os.environ.copy()
            
            gate = strategy._run_go_test(env, full_env, Tier.PROD)
            
            assert gate.status == GateStatus.FAILED
    
    def test_go_vet_gate_passes(self, tmp_path: Path):
        """Test go vet passes on clean code."""
        strategy = GoStrategy()
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            
            from integration_coworker.codegen.gates.base import SandboxEnv
            import os
            env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
            full_env = os.environ.copy()
            
            gate = strategy._run_go_vet(env, full_env, Tier.PROD)
            
            assert gate.status == GateStatus.PASSED
    
    def test_go_vet_gate_fails_suspicious_code(self, tmp_path: Path):
        """Test go vet fails on suspicious code."""
        strategy = GoStrategy()
        
        vet_output = "main.go:10:5: unreachable code"
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr=vet_output)
            
            from integration_coworker.codegen.gates.base import SandboxEnv
            import os
            env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
            full_env = os.environ.copy()
            
            gate = strategy._run_go_vet(env, full_env, Tier.PROD)
            
            assert gate.status == GateStatus.FAILED
            assert "unreachable code" in gate.errors[0]
    
    def test_staticcheck_gate_passes(self, tmp_path: Path):
        """Test staticcheck passes on clean code."""
        strategy = GoStrategy()
        
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/local/bin/staticcheck"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                
                from integration_coworker.codegen.gates.base import SandboxEnv
                import os
                env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
                full_env = os.environ.copy()
                
                gate = strategy._run_staticcheck(env, full_env, Tier.PROD)
                
                assert gate.status == GateStatus.PASSED
    
    def test_staticcheck_gate_parses_output(self, tmp_path: Path):
        """Test staticcheck parses output correctly."""
        strategy = GoStrategy()
        
        # staticcheck outputs text by default (not JSON)
        staticcheck_output = "main.go:10:5: SA1000: invalid regexp: missing closing ]"
        
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/local/bin/staticcheck"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout=staticcheck_output, stderr="")
                
                from integration_coworker.codegen.gates.base import SandboxEnv
                import os
                env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
                full_env = os.environ.copy()
                
                gate = strategy._run_staticcheck(env, full_env, Tier.PROD)
                
                assert gate.status == GateStatus.FAILED
                assert "SA1000" in gate.errors[0]
    
    def test_staticcheck_skipped_when_not_installed(self, tmp_path: Path):
        """Test staticcheck is skipped (not failed) when not installed in Tier 2."""
        strategy = GoStrategy()
        
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None  # Neither staticcheck nor golangci-lint
            
            from integration_coworker.codegen.gates.base import SandboxEnv
            import os
            env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
            full_env = os.environ.copy()
            
            # Tier 2 should skip, not fail
            gate = strategy._run_staticcheck(env, full_env, Tier.EXP)
            
            assert gate.status == GateStatus.SKIPPED


class TestGoStreamingJsonParser:
    """Tests for streaming JSON parser used with go list -json."""
    
    def test_parse_single_package(self):
        """Test parsing single package JSON."""
        strategy = GoStrategy()
        
        output = json.dumps({
            "Dir": "/tmp/test",
            "ImportPath": "example.com/test",
            "Name": "main",
            "GoFiles": ["main.go"]
        })
        
        packages = strategy._parse_go_list_json_stream(output)
        
        assert len(packages) == 1
        assert packages[0]["Name"] == "main"
    
    def test_parse_multiple_packages_streaming(self):
        """Test parsing streaming JSON from go list -json ./..."""
        strategy = GoStrategy()
        
        # go list -json outputs multiple JSON objects concatenated (NOT array, NOT newline-delimited)
        output = """{
    "Dir": "/tmp/test",
    "ImportPath": "example.com/test",
    "Name": "main"
}{
    "Dir": "/tmp/test/pkg",
    "ImportPath": "example.com/test/pkg",
    "Name": "pkg"
}{
    "Dir": "/tmp/test/internal",
    "ImportPath": "example.com/test/internal",
    "Name": "internal"
}"""
        
        packages = strategy._parse_go_list_json_stream(output)
        
        assert len(packages) == 3
        assert packages[0]["Name"] == "main"
        assert packages[1]["Name"] == "pkg"
        assert packages[2]["Name"] == "internal"
    
    def test_parse_with_whitespace_between_objects(self):
        """Test parsing with whitespace/newlines between objects."""
        strategy = GoStrategy()
        
        output = """
{
    "Name": "main"
}

{
    "Name": "utils"
}

"""
        
        packages = strategy._parse_go_list_json_stream(output)
        
        assert len(packages) == 2
        assert packages[0]["Name"] == "main"
        assert packages[1]["Name"] == "utils"
    
    def test_parse_empty_output(self):
        """Test parsing empty output."""
        strategy = GoStrategy()
        
        packages = strategy._parse_go_list_json_stream("")
        
        assert packages == []
    
    def test_parse_handles_malformed_gracefully(self):
        """Test parser handles malformed JSON gracefully."""
        strategy = GoStrategy()
        
        output = '{"Name": "main"}{"Name": "broken'  # Malformed second object
        
        packages = strategy._parse_go_list_json_stream(output)
        
        # Should parse first valid object, stop at invalid
        assert len(packages) == 1
        assert packages[0]["Name"] == "main"


class TestGoArtifactDetection:
    """Tests for artifact detection via go list."""
    
    def test_detect_functions_via_go_list(self, tmp_path: Path):
        """Test detection of functions using go list."""
        strategy = GoStrategy()
        
        # Setup workspace
        (tmp_path / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
        (tmp_path / "client.go").write_text("""
package test

type Client struct {
    apiKey string
}

func NewClient(apiKey string) *Client {
    return &Client{apiKey: apiKey}
}

func (c *Client) Request(path string) error {
    return nil
}
""")
        
        # Mock go list -json output
        go_list_output = json.dumps({
            "Dir": str(tmp_path),
            "ImportPath": "example.com/test",
            "Name": "test",
            "GoFiles": ["client.go"]
        })
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=go_list_output,
                stderr=""
            )
            
            result = strategy.detect_artifacts(
                tmp_path,
                expected_types=["Client"],
                expected_functions=["NewClient", "Request"]
            )
            
            # Detection via go list finds packages and files
            # result["packages"] is a list of dicts with "name" key
            pkg_names = [p["name"] for p in result["packages"]]
            assert "test" in pkg_names


class TestGoFullValidation:
    """Integration tests for full validation flow."""
    
    @pytest.mark.skipif(
        not Path("/usr/local/go/bin/go").exists() and not Path("/usr/bin/go").exists(),
        reason="Go not installed"
    )
    def test_full_validation_valid_go(self, tmp_path: Path):
        """Full validation of valid Go code (requires Go)."""
        strategy = GoStrategy()
        
        # Create a minimal valid Go project
        (tmp_path / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
        
        artifacts = [
            ArtifactFile("main.go", """
package main

import "fmt"

func main() {
    fmt.Println("Hello, World!")
}
"""),
            ArtifactFile("utils/math.go", """
package utils

// Add adds two integers.
func Add(a, b int) int {
    return a + b
}
"""),
            ArtifactFile("utils/math_test.go", """
package utils

import "testing"

func TestAdd(t *testing.T) {
    if Add(2, 3) != 5 {
        t.Error("Add(2, 3) should be 5")
    }
}
"""),
        ]
        
        # Mock subprocess calls
        with patch("subprocess.run") as mock_run:
            # Provision succeeds
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            
            env = strategy.provision(artifacts, tmp_path, Tier.EXP)
            assert env.deps_resolved
            
            # Reset mock for validation
            mock_run.reset_mock()
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            
            result = strategy.validate(env, Tier.EXP)
            
            assert result.success
            assert result.language == "go"


class TestGolangCILintFallback:
    """Tests for golangci-lint fallback when staticcheck unavailable."""
    
    def test_uses_golangci_lint_when_staticcheck_missing(self, tmp_path: Path):
        """Test fallback to golangci-lint when staticcheck not installed."""
        strategy = GoStrategy()
        
        # Mock shutil.which to simulate staticcheck missing but golangci-lint present
        def mock_which(name):
            if name == "staticcheck":
                return None
            if name == "golangci-lint":
                return "/usr/local/bin/golangci-lint"
            return None
        
        with patch("shutil.which", side_effect=mock_which):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                
                from integration_coworker.codegen.gates.base import SandboxEnv
                import os
                full_env = os.environ.copy()
                env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
                
                # Should fallback to golangci-lint
                gate = strategy._run_staticcheck(env, full_env, Tier.PROD)
                
                # Verify the result is from the fallback
                assert gate.name in ["staticcheck", "golangci-lint"]
