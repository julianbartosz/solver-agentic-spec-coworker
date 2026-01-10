"""
End-to-end tests for Go code generation and validation.

These tests exercise Tier.PROD Go validation:
1. Real go.mod with pinned dependencies
2. Docker 2-phase execution (provision: go mod download, validate: --network none)
3. Compiler-backed validation (go build, go test)

NON-NEGOTIABLES per PRODUCTION_CONTRACT.md:
- Tier.PROD requires go.mod (and go.sum for full verification)
- Tier.PROD validate phase MUST use --network none
- go mod download in provision phase
"""

import json
import pytest
from pathlib import Path

from integration_coworker.codegen.gates.docker_runner import (
    DockerRunner,
    DockerConfig,
    is_docker_available,
)
from tests.e2e.conftest import docker_available


@pytest.mark.e2e
@pytest.mark.docker
@docker_available
class TestGoDockerGates:
    """
    Tests for Go validation gates in Docker.
    
    Go Tier.PROD requirements:
    - go.mod must exist
    - go mod download in provision phase (network allowed)
    - go build/test in validate phase (network disabled)
    """
    
    def test_go_provision_requires_go_mod(
        self,
        docker_runner: DockerRunner,
    ):
        """
        Test: Provision phase fails without go.mod.
        
        Tier.PROD requires go.mod for Go projects.
        """
        # Import from canonical base module (single source of truth)
        from integration_coworker.codegen.gates.base import ProvisionError
        
        # Project WITHOUT go.mod
        artifacts = [
            {
                "path": "main.go",
                "content": 'package main\n\nfunc main() {}\n'
            },
        ]
        
        with docker_runner.workspace(artifacts) as ws:
            with pytest.raises(ProvisionError, match="go.mod"):
                docker_runner.provision(ws, "go")
    
    def test_go_basic_project_provision_and_validate(
        self,
        docker_runner: DockerRunner,
    ):
        """
        Test: Basic Go project provisions and validates in Docker.
        
        This validates:
        - Docker runner provision phase (go mod download)
        - Docker runner validate phase (go build)
        - Network isolation in validate phase
        """
        artifacts = [
            {
                "path": "go.mod",
                "content": """module example.com/e2e-test

go 1.21
"""
            },
            {
                "path": "main.go",
                "content": """package main

import "fmt"

func main() {
    fmt.Println("Hello, E2E!")
}

func Add(a, b int) int {
    return a + b
}
"""
            },
        ]
        
        with docker_runner.workspace(artifacts) as ws:
            # Phase 1: Provision (network allowed)
            docker_runner.provision(ws, "go")
            assert ws.deps_resolved, "Dependencies should be resolved"
            
            # Phase 2: Validate with go build (network disabled)
            gates = [{"name": "go_build", "command": ["go", "build", "-v", "."]}]
            results = docker_runner.validate(ws, gates)
            
            assert len(results) == 1
            build_result = results[0]
            assert build_result.passed, f"go build should pass: {build_result.stderr}"
            assert build_result.exit_code == 0
    
    def test_go_syntax_error_detected(
        self,
        docker_runner: DockerRunner,
    ):
        """
        Test: Go syntax errors are detected in Docker gates.
        
        Validates that Tier.PROD correctly fails on syntax errors.
        """
        artifacts = [
            {
                "path": "go.mod",
                "content": """module example.com/e2e-test

go 1.21
"""
            },
            {
                "path": "main.go",
                "content": """package main

func main() {
    // Missing closing brace - syntax error
    fmt.Println("broken"
}
"""
            },
        ]
        
        with docker_runner.workspace(artifacts) as ws:
            docker_runner.provision(ws, "go")
            
            gates = [{"name": "go_build", "command": ["go", "build", "-v", "."]}]
            results = docker_runner.validate(ws, gates)
            
            assert len(results) == 1
            build_result = results[0]
            assert not build_result.passed, "go build should fail on syntax error"
            assert build_result.exit_code != 0
    
    def test_go_type_error_detected(
        self,
        docker_runner: DockerRunner,
    ):
        """
        Test: Go type errors are detected in Docker gates.
        
        Validates that Tier.PROD correctly fails on type errors.
        """
        artifacts = [
            {
                "path": "go.mod",
                "content": """module example.com/e2e-test

go 1.21
"""
            },
            {
                "path": "main.go",
                "content": """package main

func main() {
    var x string = 123  // Type error: cannot use 123 as string
    _ = x
}
"""
            },
        ]
        
        with docker_runner.workspace(artifacts) as ws:
            docker_runner.provision(ws, "go")
            
            gates = [{"name": "go_build", "command": ["go", "build", "-v", "."]}]
            results = docker_runner.validate(ws, gates)
            
            assert len(results) == 1
            build_result = results[0]
            assert not build_result.passed, "go build should fail on type error"
            assert build_result.exit_code != 0
    
    def test_go_project_with_tests(
        self,
        docker_runner: DockerRunner,
    ):
        """
        Test: Go project with tests validates correctly.
        
        Validates go test gate in Tier.PROD.
        """
        artifacts = [
            {
                "path": "go.mod",
                "content": """module example.com/e2e-test

go 1.21
"""
            },
            {
                "path": "math.go",
                "content": """package main

func main() {
    // Entry point for executable
}

func Add(a, b int) int {
    return a + b
}

func Multiply(a, b int) int {
    return a * b
}
"""
            },
            {
                "path": "math_test.go",
                "content": """package main

import "testing"

func TestAdd(t *testing.T) {
    result := Add(2, 3)
    if result != 5 {
        t.Errorf("Add(2, 3) = %d; want 5", result)
    }
}

func TestMultiply(t *testing.T) {
    result := Multiply(2, 3)
    if result != 6 {
        t.Errorf("Multiply(2, 3) = %d; want 6", result)
    }
}
"""
            },
        ]
        
        with docker_runner.workspace(artifacts) as ws:
            docker_runner.provision(ws, "go")
            
            # Run both build and test gates
            gates = [
                {"name": "go_build", "command": ["go", "build", "-v", "."]},
                {"name": "go_test", "command": ["go", "test", "-v", "./..."]},
            ]
            results = docker_runner.validate(ws, gates)
            
            assert len(results) == 2
            
            build_result = results[0]
            assert build_result.passed, f"go build should pass: {build_result.stderr}"
            
            test_result = results[1]
            assert test_result.passed, f"go test should pass: {test_result.stderr}"
    
    def test_go_failing_tests_detected(
        self,
        docker_runner: DockerRunner,
    ):
        """
        Test: Failing Go tests are detected in Docker gates.
        """
        artifacts = [
            {
                "path": "go.mod",
                "content": """module example.com/e2e-test

go 1.21
"""
            },
            {
                "path": "math.go",
                "content": """package main

func Add(a, b int) int {
    return a + b
}
"""
            },
            {
                "path": "math_test.go",
                "content": """package main

import "testing"

func TestAdd_Broken(t *testing.T) {
    result := Add(2, 3)
    if result != 999 {  // This will fail
        t.Errorf("Add(2, 3) = %d; want 999", result)
    }
}
"""
            },
        ]
        
        with docker_runner.workspace(artifacts) as ws:
            docker_runner.provision(ws, "go")
            
            gates = [{"name": "go_test", "command": ["go", "test", "-v", "./..."]}]
            results = docker_runner.validate(ws, gates)
            
            assert len(results) == 1
            test_result = results[0]
            assert not test_result.passed, "go test should fail"
            assert test_result.exit_code != 0
