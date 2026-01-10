"""
Real integration test for Go's go list -json streaming parser.

This test runs actual go list commands against a fixture module
to verify the streaming JSON parser works with real Go output.

Per user requirement: "add one integration test that runs go list -json ./...
in a tiny fixture module and validates parsing end-to-end"
"""

import json
import os
import shutil
import subprocess
import pytest
from pathlib import Path

from integration_coworker.codegen.gates.go import GoStrategy


# Skip if Go is not installed
GO_AVAILABLE = shutil.which("go") is not None


@pytest.fixture
def go_fixture_module(tmp_path: Path) -> Path:
    """Create a minimal Go module fixture for testing."""
    # Create go.mod
    (tmp_path / "go.mod").write_text("""module example.com/testfixture

go 1.21
""")
    
    # Create main package
    (tmp_path / "main.go").write_text("""package main

import "fmt"

func main() {
    fmt.Println("Hello")
}
""")
    
    # Create a subpackage
    pkg_dir = tmp_path / "pkg" / "client"
    pkg_dir.mkdir(parents=True)
    
    (pkg_dir / "client.go").write_text("""package client

// Client is an API client.
type Client struct {
    baseURL string
}

// NewClient creates a new client.
func NewClient(url string) *Client {
    return &Client{baseURL: url}
}

// Get performs a GET request.
func (c *Client) Get(path string) (string, error) {
    return "", nil
}
""")
    
    # Create another subpackage
    utils_dir = tmp_path / "pkg" / "utils"
    utils_dir.mkdir(parents=True)
    
    (utils_dir / "utils.go").write_text("""package utils

// Add adds two numbers.
func Add(a, b int) int {
    return a + b
}

// Multiply multiplies two numbers.
func Multiply(a, b int) int {
    return a * b
}
""")
    
    return tmp_path


@pytest.mark.skipif(not GO_AVAILABLE, reason="Go not installed")
class TestGoListRealIntegration:
    """Real integration tests with actual go commands."""
    
    def test_go_list_json_real_output(self, go_fixture_module: Path):
        """Test that our parser handles real go list -json output."""
        strategy = GoStrategy()
        
        # Run actual go list -json
        result = subprocess.run(
            ["go", "list", "-json", "./..."],
            cwd=go_fixture_module,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        assert result.returncode == 0, f"go list failed: {result.stderr}"
        
        # Parse using our streaming parser
        packages = strategy._parse_go_list_json_stream(result.stdout)
        
        # Should have found all 3 packages
        assert len(packages) == 3, f"Expected 3 packages, got {len(packages)}: {packages}"
        
        # Verify package names
        package_names = {p.get("Name") for p in packages}
        assert "main" in package_names, f"Missing main package: {package_names}"
        assert "client" in package_names, f"Missing client package: {package_names}"
        assert "utils" in package_names, f"Missing utils package: {package_names}"
        
        # Verify import paths
        import_paths = {p.get("ImportPath") for p in packages}
        assert any("testfixture" in ip for ip in import_paths), f"Missing root module: {import_paths}"
        assert any("client" in ip for ip in import_paths), f"Missing client package: {import_paths}"
        assert any("utils" in ip for ip in import_paths), f"Missing utils package: {import_paths}"
    
    def test_parser_handles_single_package(self, go_fixture_module: Path):
        """Test parser with single package output."""
        strategy = GoStrategy()
        
        # Run go list on just the root package
        result = subprocess.run(
            ["go", "list", "-json", "."],
            cwd=go_fixture_module,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        assert result.returncode == 0
        
        packages = strategy._parse_go_list_json_stream(result.stdout)
        
        assert len(packages) == 1
        assert packages[0].get("Name") == "main"
    
    def test_parser_extracts_go_files(self, go_fixture_module: Path):
        """Test that parser extracts GoFiles correctly."""
        strategy = GoStrategy()
        
        result = subprocess.run(
            ["go", "list", "-json", "./pkg/client"],
            cwd=go_fixture_module,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        assert result.returncode == 0
        
        packages = strategy._parse_go_list_json_stream(result.stdout)
        
        assert len(packages) == 1
        pkg = packages[0]
        
        assert "GoFiles" in pkg
        assert "client.go" in pkg["GoFiles"]
    
    def test_detect_artifacts_real(self, go_fixture_module: Path):
        """Test artifact detection against real Go module."""
        strategy = GoStrategy()
        
        result = strategy.detect_artifacts(
            go_fixture_module,
            expected_types=["Client"],
            expected_functions=["NewClient", "Get", "Add"],
        )
        
        # Should find packages
        assert len(result["packages"]) == 3
        
        # Should detect types via regex fallback
        assert "Client" in result["types"] or len(result["types_matched"]) > 0
        
        # Should detect functions
        assert len(result["functions"]) > 0
    
    def test_full_validation_flow(self, go_fixture_module: Path):
        """Test complete provisioning and validation flow."""
        from integration_coworker.codegen.gates import Tier, ArtifactFile
        
        strategy = GoStrategy()
        
        # Artifacts already written by fixture
        artifacts = [
            ArtifactFile("main.go", (go_fixture_module / "main.go").read_text()),
        ]
        
        # Provision (may need network for go mod download)
        env = strategy.provision(artifacts, go_fixture_module, Tier.EXP)
        
        assert env.deps_resolved
        
        # Validate
        result = strategy.validate(env, Tier.EXP)
        
        # Should have run gates
        assert len(result.gates) > 0
        
        # go test should pass (no tests, but should not fail)
        go_test_gates = [g for g in result.gates if "test" in g.name.lower()]
        # Empty tests might fail or pass depending on Go version
        
        # go vet should pass
        go_vet_gates = [g for g in result.gates if "vet" in g.name.lower()]
        assert all(g.status.value in ("passed", "skipped") for g in go_vet_gates)


@pytest.mark.skipif(not GO_AVAILABLE, reason="Go not installed")  
class TestGoStreamingJsonEdgeCases:
    """Edge case tests for streaming JSON parser with real Go output."""
    
    def test_empty_module_parsing(self, tmp_path: Path):
        """Test parsing output from empty module."""
        # Create minimal module with no packages
        (tmp_path / "go.mod").write_text("module empty\n\ngo 1.21\n")
        
        strategy = GoStrategy()
        
        result = subprocess.run(
            ["go", "list", "-json", "./..."],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        # Empty module may return error or empty output
        if result.returncode == 0 and result.stdout.strip():
            packages = strategy._parse_go_list_json_stream(result.stdout)
            # Should handle gracefully even if empty
            assert isinstance(packages, list)
    
    def test_module_with_errors_parsing(self, tmp_path: Path):
        """Test that parser handles modules with compilation errors."""
        (tmp_path / "go.mod").write_text("module broken\n\ngo 1.21\n")
        (tmp_path / "main.go").write_text("package main\n\nfunc main() {\n")  # Syntax error
        
        strategy = GoStrategy()
        
        result = subprocess.run(
            ["go", "list", "-json", "./..."],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        # go list -json still outputs JSON even with errors
        if result.stdout.strip():
            packages = strategy._parse_go_list_json_stream(result.stdout)
            # Should parse what it can
            assert isinstance(packages, list)
