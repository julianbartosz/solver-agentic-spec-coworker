"""
Tests for TypeScript validation gates.

Tests the TypeScript strategy implementation including:
- Toolchain probing
- Provisioning (npm ci)
- Gate execution (tsc, eslint, vitest)
- Artifact detection

Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.2
"""

import json
import tempfile
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
from integration_coworker.codegen.gates.typescript import (
    TypeScriptStrategy,
    TypeScriptGateConfig,
)


class TestTypeScriptProbe:
    """Tests for toolchain probing."""
    
    def test_probe_finds_node_and_npm(self, tmp_path: Path):
        """Test that probe detects node and npm."""
        strategy = TypeScriptStrategy()
        
        # Create minimal config files
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {}}')
        (tmp_path / "package.json").write_text('{"name": "test"}')
        (tmp_path / "package-lock.json").write_text('{"lockfileVersion": 3}')
        
        result = strategy.probe_toolchain(tmp_path)
        
        # Should find node/npm if installed on system
        assert "node" in result["executables_found"] or "node" in result["executables_missing"]
        assert "npm" in result["executables_found"] or "npm" in result["executables_missing"]
    
    def test_probe_detects_tsconfig(self, tmp_path: Path):
        """Test that probe finds tsconfig.json."""
        strategy = TypeScriptStrategy()
        
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {}}')
        (tmp_path / "package-lock.json").write_text('{}')
        
        result = strategy.probe_toolchain(tmp_path)
        
        assert "tsconfig.json" in result["config_files_found"]
    
    def test_probe_missing_tsconfig_tier2(self, tmp_path: Path):
        """Test that missing tsconfig.json downgrades to Tier 2."""
        strategy = TypeScriptStrategy()
        
        result = strategy.probe_toolchain(tmp_path)
        
        assert "tsconfig.json" in result["config_files_missing"]
        assert result["tier_eligible"] == Tier.EXP
    
    def test_probe_detects_lockfile(self, tmp_path: Path):
        """Test that probe finds lockfiles."""
        strategy = TypeScriptStrategy()
        
        (tmp_path / "tsconfig.json").write_text('{}')
        (tmp_path / "package-lock.json").write_text('{}')
        
        result = strategy.probe_toolchain(tmp_path)
        
        assert result["lockfile_found"] == "package-lock.json"
    
    def test_probe_detects_pnpm_lock(self, tmp_path: Path):
        """Test that probe finds pnpm lockfile."""
        strategy = TypeScriptStrategy()
        
        (tmp_path / "tsconfig.json").write_text('{}')
        (tmp_path / "pnpm-lock.yaml").write_text('')
        
        result = strategy.probe_toolchain(tmp_path)
        
        assert result["lockfile_found"] == "pnpm-lock.yaml"


class TestTypeScriptProvision:
    """Tests for provisioning."""
    
    def test_provision_writes_artifacts(self, tmp_path: Path):
        """Test that artifacts are written to workspace."""
        strategy = TypeScriptStrategy()
        
        artifacts = [
            ArtifactFile("src/index.ts", "export const hello = () => 'world';"),
            ArtifactFile("src/utils.ts", "export const add = (a: number, b: number) => a + b;"),
        ]
        
        # Create required config files
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}}')
        (tmp_path / "package.json").write_text(json.dumps({
            "name": "test",
            "devDependencies": {"typescript": "^5.0.0"}
        }))
        (tmp_path / "package-lock.json").write_text('{"lockfileVersion": 3}')
        
        # Mock npm ci to avoid network
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            
            env = strategy.provision(artifacts, tmp_path, Tier.EXP)
            
            assert (tmp_path / "src/index.ts").exists()
            assert (tmp_path / "src/utils.ts").exists()
            assert env.deps_resolved
    
    def test_provision_tier1_requires_lockfile(self, tmp_path: Path):
        """Test that Tier 1 requires lockfile."""
        strategy = TypeScriptStrategy()
        
        # Create tsconfig but no lockfile
        (tmp_path / "tsconfig.json").write_text('{}')
        (tmp_path / "package.json").write_text('{"name": "test"}')
        
        artifacts = [ArtifactFile("src/index.ts", "export const x = 1;")]
        
        with pytest.raises(ToolchainMissingError, match="lockfile"):
            strategy.provision(artifacts, tmp_path, Tier.PROD)


class TestTypeScriptGates:
    """Tests for validation gates."""
    
    def test_tsc_gate_passes_valid_code(self, tmp_path: Path):
        """Test tsc passes on valid TypeScript."""
        strategy = TypeScriptStrategy()
        
        # Setup workspace with valid TS
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}}')
        (tmp_path / "src").mkdir()
        (tmp_path / "src/index.ts").write_text("export const hello: string = 'world';")
        
        # Mock subprocess for tsc
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            
            from integration_coworker.codegen.gates.base import SandboxEnv
            env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
            
            gate = strategy._run_tsc(env, Tier.PROD)
            
            assert gate.status == GateStatus.PASSED
            assert gate.exit_code == 0
    
    def test_tsc_gate_fails_type_error(self, tmp_path: Path):
        """Test tsc fails on type error: const x: number = 'bad'."""
        strategy = TypeScriptStrategy()
        
        # Setup workspace with invalid TS
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}}')
        (tmp_path / "src").mkdir()
        (tmp_path / "src/index.ts").write_text("const x: number = 'bad';")
        
        # Mock subprocess for tsc with type error
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="src/index.ts(1,7): error TS2322: Type 'string' is not assignable to type 'number'.",
                stderr=""
            )
            
            from integration_coworker.codegen.gates.base import SandboxEnv
            env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
            
            gate = strategy._run_tsc(env, Tier.PROD)
            
            assert gate.status == GateStatus.FAILED
            assert "TS2322" in gate.errors[0]
    
    def test_eslint_gate_parses_json(self, tmp_path: Path):
        """Test eslint gate parses JSON output correctly."""
        strategy = TypeScriptStrategy()
        
        eslint_output = json.dumps([
            {
                "filePath": "/tmp/src/index.ts",
                "messages": [
                    {"line": 1, "severity": 2, "message": "Unexpected eval", "ruleId": "no-eval"}
                ]
            }
        ])
        
        with patch("subprocess.run") as mock_run:
            # First call: eslint --version (success)
            # Second call: eslint . --format json (failure)
            mock_run.side_effect = [
                MagicMock(returncode=0),  # version check
                MagicMock(returncode=1, stdout=eslint_output, stderr=""),  # lint
            ]
            
            from integration_coworker.codegen.gates.base import SandboxEnv
            env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
            
            gate = strategy._run_eslint(env, Tier.PROD)
            
            assert gate.status == GateStatus.FAILED
            assert "no-eval" in gate.errors[0]
    
    def test_vitest_gate_uses_reporter_json(self, tmp_path: Path):
        """Test vitest gate uses --reporter=json --outputFile correctly."""
        strategy = TypeScriptStrategy()
        
        # Create output file that vitest would create
        vitest_results = {
            "testResults": [
                {
                    "name": "src/index.test.ts",
                    "assertionResults": [
                        {"title": "should pass", "status": "passed"},
                        {"title": "should fail", "status": "failed"},
                    ]
                }
            ]
        }
        
        output_file = tmp_path / "vitest-results.json"
        output_file.write_text(json.dumps(vitest_results))
        
        with patch("subprocess.run") as mock_run:
            # First call: vitest --version (success)
            # Second call: vitest run (failure due to test failure)
            mock_run.side_effect = [
                MagicMock(returncode=0),  # version check
                MagicMock(returncode=1, stdout="", stderr=""),  # test run
            ]
            
            from integration_coworker.codegen.gates.base import SandboxEnv
            env = SandboxEnv(sandbox_path=tmp_path, work_dir=tmp_path, deps_resolved=True)
            
            gate = strategy._run_vitest(env, Tier.PROD)
            
            assert gate.status == GateStatus.FAILED
            assert "should fail" in gate.errors[0]
            
            # Verify command used --reporter=json --outputFile
            call_args = mock_run.call_args_list[1][0][0]
            assert "--reporter=json" in call_args
            assert any("--outputFile=" in arg for arg in call_args)


class TestTypeScriptArtifactDetection:
    """Tests for compiler-backed artifact detection."""
    
    def test_detect_class_declaration(self, tmp_path: Path):
        """Test detection of class declarations using TS compiler API."""
        strategy = TypeScriptStrategy()
        
        # Setup workspace
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "src/client.ts").write_text("""
export class StripeClient {
    constructor(private apiKey: string) {}
    
    async charge(amount: number): Promise<void> {
        // Implementation
    }
}
""")
        
        # Mock the node subprocess
        detector_output = json.dumps({
            "classes": ["StripeClient"],
            "functions": []
        })
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=detector_output,
                stderr=""
            )
            
            result = strategy.detect_artifacts(
                tmp_path,
                expected_classes=["Client"],
                expected_functions=[]
            )
            
            assert "StripeClient" in result["classes"]
            assert "StripeClient" in result["classes_matched"]
    
    def test_detect_exported_functions(self, tmp_path: Path):
        """Test detection of exported functions."""
        strategy = TypeScriptStrategy()
        
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "src/utils.ts").write_text("""
export function validateInput(input: string): boolean {
    return input.length > 0;
}

export const processData = async (data: any) => {
    return data;
};
""")
        
        detector_output = json.dumps({
            "classes": [],
            "functions": ["validateInput", "processData"]
        })
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=detector_output,
                stderr=""
            )
            
            result = strategy.detect_artifacts(
                tmp_path,
                expected_classes=[],
                expected_functions=["validate", "process"]
            )
            
            assert "validateInput" in result["functions"]
            assert "processData" in result["functions"]


class TestTypeScriptFullValidation:
    """Integration tests for full validation flow."""
    
    @pytest.mark.skipif(
        not Path("/usr/bin/node").exists() and not Path("/usr/local/bin/node").exists(),
        reason="Node.js not installed"
    )
    def test_full_validation_valid_ts(self, tmp_path: Path):
        """Full validation of valid TypeScript code (requires Node.js)."""
        strategy = TypeScriptStrategy()
        
        # Create a minimal valid TS project
        (tmp_path / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {
                "target": "ES2020",
                "module": "commonjs",
                "strict": True,
                "esModuleInterop": True,
                "skipLibCheck": True,
                "outDir": "dist"
            },
            "include": ["src/**/*"]
        }))
        
        (tmp_path / "package.json").write_text(json.dumps({
            "name": "test-project",
            "private": True,
            "devDependencies": {
                "typescript": "^5.0.0"
            }
        }))
        
        artifacts = [
            ArtifactFile("src/index.ts", """
export interface Config {
    apiKey: string;
    baseUrl: string;
}

export class ApiClient {
    constructor(private config: Config) {}
    
    async request(path: string): Promise<Response> {
        return fetch(this.config.baseUrl + path, {
            headers: { 'Authorization': 'Bearer ' + this.config.apiKey }
        });
    }
}
""")
        ]
        
        # This test would require actual npm ci to work
        # For unit testing, we mock the subprocess calls
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
            assert result.language == "typescript"
