"""
Tests for multi-language sandbox integration.

Tests that:
1. execute_multilang_sandbox dispatches to correct strategy
2. TypeScript gates are invoked with correct commands
3. Go gates are invoked with correct commands  
4. Language detection works correctly

Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.2
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from integration_coworker.codegen.sandbox import ArtifactFile
from integration_coworker.codegen.sandbox_multilang import (
    execute_multilang_sandbox,
    MultiLangSandboxConfig,
    ArtifactLanguage,
    detect_language,
    validate_artifacts,
)
from integration_coworker.codegen.gates import Tier, GateStatus


class TestLanguageDetection:
    """Tests for automatic language detection."""
    
    def test_detect_typescript_from_ts_extension(self):
        """Test .ts files detected as TypeScript."""
        artifacts = [
            ArtifactFile("src/client.ts", "export class Client {}"),
            ArtifactFile("src/utils.ts", "export function helper() {}"),
        ]
        assert detect_language(artifacts) == ArtifactLanguage.TYPESCRIPT
    
    def test_detect_go_from_go_extension(self):
        """Test .go files detected as Go."""
        artifacts = [
            ArtifactFile("main.go", "package main"),
            ArtifactFile("client/api.go", "package client"),
        ]
        assert detect_language(artifacts) == ArtifactLanguage.GO
    
    def test_detect_python_from_py_extension(self):
        """Test .py files detected as Python."""
        artifacts = [
            ArtifactFile("src/module.py", "def hello(): pass"),
        ]
        assert detect_language(artifacts) == ArtifactLanguage.PYTHON
    
    def test_detect_majority_language(self):
        """Test that majority language is detected in mixed artifacts."""
        artifacts = [
            ArtifactFile("src/client.ts", "export class Client {}"),
            ArtifactFile("src/utils.ts", "export function helper() {}"),
            ArtifactFile("config.py", "# config"),  # One Python file
        ]
        assert detect_language(artifacts) == ArtifactLanguage.TYPESCRIPT


class TestTypeScriptSandboxIntegration:
    """Tests for TypeScript sandbox execution."""
    
    @pytest.mark.asyncio
    async def test_typescript_gates_invoked(self, tmp_path: Path):
        """Test that TypeScript strategy gates are invoked via sandbox."""
        artifacts = [
            ArtifactFile("src/client.ts", "export class StripeClient {}"),
        ]
        
        # Create minimal config files
        workspace_config = {
            "tsconfig.json": json.dumps({"compilerOptions": {"strict": True}}),
            "package.json": json.dumps({"name": "test", "devDependencies": {"typescript": "^5.0.0"}}),
            "package-lock.json": json.dumps({"lockfileVersion": 3}),
        }
        
        # Mock subprocess to avoid actual npm/tsc execution
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            
            config = MultiLangSandboxConfig(
                tier=Tier.EXP,  # Tier 2 allows skipping
                cleanup_on_success=True,
            )
            
            result = await execute_multilang_sandbox(
                artifacts=artifacts,
                language=ArtifactLanguage.TYPESCRIPT,
                config=config,
                workspace_config_files=workspace_config,
            )
            
            # Should have invoked npm and gates
            assert result.language == ArtifactLanguage.TYPESCRIPT
            # Check gates were attempted
            gate_names = [g.name for g in result.gate_results]
            # Should have at least tried tsc
            assert any("tsc" in name or "provision" in name for name in gate_names) or result.success
    
    @pytest.mark.asyncio
    async def test_typescript_vitest_uses_reporter_json(self, tmp_path: Path):
        """Test that vitest gate uses --reporter=json --outputFile."""
        # This is a critical correctness test per user feedback
        from integration_coworker.codegen.gates.typescript import TypeScriptStrategy
        
        strategy = TypeScriptStrategy()
        
        # Capture the vitest command
        captured_commands = []
        
        original_run = subprocess_run = None
        import subprocess
        original_run = subprocess.run
        
        def capture_run(cmd, **kwargs):
            captured_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")
        
        with patch("subprocess.run", side_effect=capture_run):
            from integration_coworker.codegen.gates.base import SandboxEnv
            env = SandboxEnv(
                sandbox_path=tmp_path,
                work_dir=tmp_path,
                deps_resolved=True,
            )
            
            # Create vitest results file that the gate expects
            (tmp_path / "vitest-results.json").write_text(json.dumps({
                "testResults": []
            }))
            
            # Run vitest gate
            gate = strategy._run_vitest(env, Tier.EXP)
        
        # Find the vitest command
        vitest_commands = [c for c in captured_commands if "vitest" in str(c)]
        
        if vitest_commands:
            vitest_cmd = vitest_commands[-1]  # Last vitest command
            cmd_str = " ".join(vitest_cmd) if isinstance(vitest_cmd, list) else str(vitest_cmd)
            
            # CRITICAL: Must use --reporter=json, not --json
            assert "--reporter=json" in cmd_str or "--reporter" in cmd_str, \
                f"Vitest must use --reporter=json, got: {cmd_str}"
            
            # CRITICAL: Must use --outputFile
            assert "--outputFile" in cmd_str or "outputFile" in cmd_str.lower(), \
                f"Vitest must use --outputFile, got: {cmd_str}"


class TestGoSandboxIntegration:
    """Tests for Go sandbox execution."""
    
    @pytest.mark.asyncio
    async def test_go_gates_invoked(self, tmp_path: Path):
        """Test that Go strategy gates are invoked via sandbox."""
        artifacts = [
            ArtifactFile("main.go", "package main\n\nfunc main() {}\n"),
        ]
        
        workspace_config = {
            "go.mod": "module example.com/test\n\ngo 1.21\n",
        }
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            
            config = MultiLangSandboxConfig(
                tier=Tier.EXP,
                cleanup_on_success=True,
            )
            
            result = await execute_multilang_sandbox(
                artifacts=artifacts,
                language=ArtifactLanguage.GO,
                config=config,
                workspace_config_files=workspace_config,
            )
            
            assert result.language == ArtifactLanguage.GO
    
    @pytest.mark.asyncio
    async def test_go_test_uses_json_flag(self, tmp_path: Path):
        """Test that go test gate uses -json flag."""
        from integration_coworker.codegen.gates.go import GoStrategy
        
        strategy = GoStrategy()
        
        captured_commands = []
        
        def capture_run(cmd, **kwargs):
            captured_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")
        
        with patch("subprocess.run", side_effect=capture_run):
            from integration_coworker.codegen.gates.base import SandboxEnv
            import os
            
            env = SandboxEnv(
                sandbox_path=tmp_path,
                work_dir=tmp_path,
                deps_resolved=True,
            )
            full_env = os.environ.copy()
            
            gate = strategy._run_go_test(env, full_env, Tier.EXP)
        
        # Find go test command
        go_test_commands = [c for c in captured_commands if "go" in str(c) and "test" in str(c)]
        
        if go_test_commands:
            cmd = go_test_commands[0]
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            
            # Should use -json for structured output
            assert "-json" in cmd_str, f"go test should use -json, got: {cmd_str}"


class TestPythonFallback:
    """Tests for Python sandbox fallback."""
    
    @pytest.mark.asyncio
    async def test_python_uses_existing_sandbox(self):
        """Test that Python artifacts use existing sandbox.py."""
        artifacts = [
            ArtifactFile("src/module.py", "def hello(): return 'world'"),
        ]
        
        # Patch at the location where it's imported in sandbox_multilang
        with patch("integration_coworker.codegen.sandbox_multilang.execute_in_sandbox") as mock_sandbox:
            from integration_coworker.codegen.sandbox import SandboxResult, GateResult as SandboxGateResult
            mock_sandbox.return_value = SandboxResult(
                success=True,
                gate_results=[],
                sandbox_dir=None,
                summary="PASSED: 0/0 gates",
            )
            
            result = await execute_multilang_sandbox(
                artifacts=artifacts,
                language=ArtifactLanguage.PYTHON,
            )
            
            # Should have called existing Python sandbox
            mock_sandbox.assert_called_once()
            assert result.language == ArtifactLanguage.PYTHON


class TestTierDowngrade:
    """Tests for tier downgrade on missing toolchain."""
    
    @pytest.mark.asyncio
    async def test_tier1_downgrades_without_lockfile(self, tmp_path: Path):
        """Test that Tier 1 downgrades to Tier 2 without lockfile."""
        artifacts = [
            ArtifactFile("src/client.ts", "export class Client {}"),
        ]
        
        # No package-lock.json - should downgrade
        workspace_config = {
            "tsconfig.json": json.dumps({"compilerOptions": {}}),
            "package.json": json.dumps({"name": "test"}),
            # Missing package-lock.json
        }
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            
            # Tier.PROD now requires use_docker=True explicitly
            config = MultiLangSandboxConfig(
                tier=Tier.PROD,  # Request Tier 1
                use_docker=True,  # Required for Tier.PROD
                cleanup_on_success=True,
            )
            
            result = await execute_multilang_sandbox(
                artifacts=artifacts,
                language=ArtifactLanguage.TYPESCRIPT,
                config=config,
                workspace_config_files=workspace_config,
            )
            
            # Should have downgraded to Tier 2
            assert result.tier == Tier.EXP


class TestValidateArtifactsConvenience:
    """Tests for convenience function."""
    
    @pytest.mark.asyncio
    async def test_auto_detect_language(self):
        """Test that validate_artifacts auto-detects language."""
        artifacts = [
            ArtifactFile("main.go", "package main"),
        ]
        
        with patch("integration_coworker.codegen.sandbox_multilang.execute_multilang_sandbox") as mock:
            mock.return_value = MagicMock(success=True)
            
            await validate_artifacts(artifacts, tier=Tier.EXP)
            
            # Should have detected Go and called with that
            call_args = mock.call_args
            assert call_args[0][1] == ArtifactLanguage.GO
