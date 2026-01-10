"""
Regression test: Tier.PROD TypeScript validation must use npm ci.

This test proves the invariant documented in the hardening plan:
- Tier.PROD + no lockfile → raises ToolchainMissingError (does NOT attempt npm install)
- Tier.PROD + lockfile → uses npm ci (not npm install)

EVIDENCE (typescript.py line citations):
- Lines 174-178: Guard checks probe["tier_eligible"] != Tier.PROD and raises ToolchainMissingError
- Lines 120-123: probe["tier_eligible"] set to Tier.EXP when no lockfile found
- Line 197: npm_cmd = ["npm", "ci"] if probe["lockfile_found"] else ["npm", "install"]

INVARIANT PROOF:
If tier == Tier.PROD and no lockfile:
1. probe_toolchain() at line 174 checks probe["tier_eligible"]
2. Lines 120-123 set tier_eligible = Tier.EXP when no lockfile
3. Line 175 condition (tier == Tier.PROD and probe["tier_eligible"] != Tier.PROD) is TRUE
4. Line 176-178 raises ToolchainMissingError BEFORE reaching line 197
5. Therefore, npm_cmd selection at line 197 is UNREACHABLE with Tier.PROD + no lockfile

This test validates this control flow.
"""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from integration_coworker.codegen.gates.typescript import TypeScriptStrategy
from integration_coworker.codegen.gates.base import (
    Tier,
    ToolchainMissingError,
    ProvisionError,
    ArtifactFile,
)


class TestTierProdRequiresLockfile:
    """Tests that Tier.PROD TypeScript requires lockfile and uses npm ci."""
    
    def test_tier_prod_without_lockfile_raises_toolchain_missing_error(self):
        """
        Tier.PROD + no lockfile must raise ToolchainMissingError.
        
        This proves that npm_cmd = ["npm", "install"] fallback is UNREACHABLE
        for Tier.PROD, because the ToolchainMissingError is raised first.
        
        Evidence: typescript.py lines 174-178 (guard), lines 120-123 (tier_eligible)
        """
        strategy = TypeScriptStrategy()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            # Create minimal valid TypeScript project WITHOUT lockfile
            (workspace / "package.json").write_text('{"name": "test", "devDependencies": {"typescript": "^5.0.0"}}')
            (workspace / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}}')
            
            artifacts = [
                ArtifactFile(path="src/client.ts", content="export class Client {}")
            ]
            
            # Tier.PROD should fail because no lockfile
            with pytest.raises(ToolchainMissingError) as exc_info:
                strategy.provision(artifacts, workspace, Tier.PROD)
            
            # Error message should mention lockfile requirement
            assert "lockfile" in str(exc_info.value).lower() or "Tier 1" in str(exc_info.value)
    
    def test_tier_exp_without_lockfile_uses_npm_install(self):
        """
        Tier.EXP + no lockfile should use npm install (not raise).
        
        This is the expected fallback behavior for development.
        """
        strategy = TypeScriptStrategy()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            # Create minimal valid TypeScript project WITHOUT lockfile
            (workspace / "package.json").write_text('{"name": "test", "devDependencies": {"typescript": "^5.0.0"}}')
            (workspace / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}}')
            
            artifacts = [
                ArtifactFile(path="src/client.ts", content="export class Client {}")
            ]
            
            # Mock subprocess.run to avoid actual npm calls
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                
                # Tier.EXP should NOT raise for missing lockfile
                env = strategy.provision(artifacts, workspace, Tier.EXP)
                
                # Verify npm install was called (not npm ci)
                mock_run.assert_called()
                call_args = mock_run.call_args
                npm_cmd = call_args[0][0] if call_args[0] else call_args[1].get('args', [])
                
                assert npm_cmd == ["npm", "install"], f"Expected npm install, got {npm_cmd}"
    
    def test_tier_prod_with_lockfile_uses_npm_ci(self):
        """
        Tier.PROD + lockfile should use npm ci (deterministic).
        
        Evidence: typescript.py line 197 - npm_cmd = ["npm", "ci"] if probe["lockfile_found"]
        """
        strategy = TypeScriptStrategy()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            # Create valid TypeScript project WITH lockfile
            (workspace / "package.json").write_text('{"name": "test", "devDependencies": {"typescript": "^5.0.0"}}')
            (workspace / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}}')
            (workspace / "package-lock.json").write_text('{"name": "test", "lockfileVersion": 3}')
            
            artifacts = [
                ArtifactFile(path="src/client.ts", content="export class Client {}")
            ]
            
            # Mock subprocess.run to avoid actual npm calls
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                
                # Tier.PROD with lockfile should succeed
                env = strategy.provision(artifacts, workspace, Tier.PROD)
                
                # Verify npm ci was called (not npm install)
                mock_run.assert_called()
                call_args = mock_run.call_args
                npm_cmd = call_args[0][0] if call_args[0] else call_args[1].get('args', [])
                
                assert npm_cmd == ["npm", "ci"], f"Expected npm ci, got {npm_cmd}"
    
    def test_probe_toolchain_marks_tier_exp_when_no_lockfile(self):
        """
        probe_toolchain() must set tier_eligible=Tier.EXP when no lockfile.
        
        Evidence: typescript.py lines 120-123
        """
        strategy = TypeScriptStrategy()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            # Create project WITHOUT lockfile
            (workspace / "package.json").write_text('{"name": "test"}')
            (workspace / "tsconfig.json").write_text('{"compilerOptions": {}}')
            
            probe = strategy.probe_toolchain(workspace)
            
            # Must indicate Tier.EXP due to missing lockfile
            assert probe["tier_eligible"] == Tier.EXP
            assert probe["lockfile_found"] is None
            assert "lockfile" in probe.get("failure_reason", "").lower() or "Tier 1" in probe.get("failure_reason", "")
    
    def test_probe_toolchain_marks_tier_prod_when_lockfile_present(self):
        """
        probe_toolchain() must set tier_eligible=Tier.PROD when lockfile exists.
        
        Evidence: typescript.py lines 113-117
        """
        strategy = TypeScriptStrategy()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            # Create project WITH lockfile
            (workspace / "package.json").write_text('{"name": "test"}')
            (workspace / "tsconfig.json").write_text('{"compilerOptions": {}}')
            (workspace / "package-lock.json").write_text('{"lockfileVersion": 3}')
            
            probe = strategy.probe_toolchain(workspace)
            
            # Must indicate Tier.PROD eligible
            assert probe["tier_eligible"] == Tier.PROD
            assert probe["lockfile_found"] == "package-lock.json"


class TestNpmCiVsInstallSemantics:
    """
    Document the difference between npm ci and npm install.
    
    npm ci (https://docs.npmjs.com/cli/v8/commands/npm-ci):
    - Requires package-lock.json or npm-shrinkwrap.json
    - Removes existing node_modules before installing
    - Never writes to package.json or package-lock.json
    - Fails if lockfile is out of sync with package.json
    - Deterministic: always produces identical node_modules
    
    npm install:
    - Can work without lockfile (generates one)
    - Updates lockfile if dependencies change
    - May install different versions over time
    - Non-deterministic
    
    For Tier.PROD (production), npm ci is REQUIRED for determinism.
    """
    
    def test_documentation_only(self):
        """This test class documents npm ci vs install semantics."""
        # The actual behavior is tested in other test methods
        assert True
