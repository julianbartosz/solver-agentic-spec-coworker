"""
Tests for explicit Tier semantics in MultiLangSandboxConfig.

These tests verify that:
1. Tier defaults to Tier.EXP (explicit, never inferred from environment)
2. Tier.PROD requires explicit use_docker=True
3. Tier.PROD + Docker unavailable = HARD FAIL (no silent downgrade)
4. recommended_tier() helper is available but never called implicitly

CRITICAL INVARIANTS:
- Tier is NEVER inferred from Docker availability
- Tier.PROD + use_docker=False raises ValueError at config time
- Tier.PROD + Docker unavailable raises DockerNotAvailableError at config time
"""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from integration_coworker.codegen.sandbox_multilang import (
    MultiLangSandboxConfig,
    MultiLangSandboxResult,
    ArtifactLanguage,
    execute_multilang_sandbox,
    _check_docker_available_cached,
    recommended_tier,
)
from integration_coworker.codegen.sandbox import ArtifactFile
from integration_coworker.codegen.gates.base import Tier
from integration_coworker.codegen.gates.docker_runner import DockerNotAvailableError


class TestExplicitTierSemantics:
    """Tests that Tier is explicit, never inferred from environment."""
    
    def test_config_defaults_to_tier_exp(self):
        """Config should default to Tier.EXP regardless of Docker availability."""
        import integration_coworker.codegen.sandbox_multilang as module
        module._DOCKER_AVAILABLE_CACHE = True  # Even with Docker available
        
        config = MultiLangSandboxConfig()
        
        assert config.tier == Tier.EXP
        assert config.use_docker is False
    
    def test_config_defaults_to_tier_exp_without_docker(self):
        """Config should default to Tier.EXP even when Docker is unavailable."""
        import integration_coworker.codegen.sandbox_multilang as module
        module._DOCKER_AVAILABLE_CACHE = False
        
        config = MultiLangSandboxConfig()
        
        assert config.tier == Tier.EXP
        assert config.use_docker is False
    
    def test_explicit_tier_exp_accepted(self):
        """Explicit tier=Tier.EXP should be accepted."""
        config = MultiLangSandboxConfig(tier=Tier.EXP)
        
        assert config.tier == Tier.EXP
        assert config.use_docker is False
    
    def test_explicit_tier_exp_with_use_docker_false_accepted(self):
        """Tier.EXP with explicit use_docker=False should be accepted."""
        config = MultiLangSandboxConfig(tier=Tier.EXP, use_docker=False)
        
        assert config.tier == Tier.EXP
        assert config.use_docker is False


class TestTierProdRequiresExplicitDocker:
    """Tests that Tier.PROD requires explicit use_docker=True."""
    
    def test_tier_prod_without_use_docker_raises(self):
        """Tier.PROD without use_docker=True should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            MultiLangSandboxConfig(tier=Tier.PROD)
        
        assert "Tier.PROD requires use_docker=True" in str(exc_info.value)
    
    def test_tier_prod_with_use_docker_false_raises(self):
        """Tier.PROD with explicit use_docker=False should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            MultiLangSandboxConfig(tier=Tier.PROD, use_docker=False)
        
        assert "Tier.PROD requires use_docker=True" in str(exc_info.value)
    
    def test_tier_prod_with_use_docker_true_accepted_when_docker_available(self):
        """Tier.PROD with use_docker=True should be accepted when Docker is available."""
        import integration_coworker.codegen.sandbox_multilang as module
        module._DOCKER_AVAILABLE_CACHE = None
        
        with patch.object(module, 'is_docker_available', return_value=True):
            module._DOCKER_AVAILABLE_CACHE = None
            config = MultiLangSandboxConfig(tier=Tier.PROD, use_docker=True)
        
        assert config.tier == Tier.PROD
        assert config.use_docker is True


class TestTierProdFailsWithoutDocker:
    """Tests that Tier.PROD fails at config time when Docker is unavailable."""
    
    def test_tier_prod_fails_at_config_time_when_docker_unavailable(self):
        """Tier.PROD should raise at config time when Docker is unavailable."""
        import integration_coworker.codegen.sandbox_multilang as module
        module._DOCKER_AVAILABLE_CACHE = None  # Force re-check
        
        with patch.object(module, 'is_docker_available', return_value=False):
            module._DOCKER_AVAILABLE_CACHE = None
            with pytest.raises(DockerNotAvailableError) as exc_info:
                MultiLangSandboxConfig(tier=Tier.PROD, use_docker=True)
            
            assert "Docker is not available" in str(exc_info.value)
    
    def test_no_silent_downgrade_to_tier_exp(self):
        """Tier.PROD should NEVER silently downgrade to Tier.EXP."""
        import integration_coworker.codegen.sandbox_multilang as module
        module._DOCKER_AVAILABLE_CACHE = None
        
        with patch.object(module, 'is_docker_available', return_value=False):
            module._DOCKER_AVAILABLE_CACHE = None
            # This should raise, not silently create Tier.EXP
            with pytest.raises(DockerNotAvailableError):
                MultiLangSandboxConfig(tier=Tier.PROD, use_docker=True)


class TestRecommendedTierHelper:
    """Tests for the recommended_tier() convenience function."""
    
    def test_recommended_tier_returns_prod_when_docker_available(self):
        """recommended_tier() should return Tier.PROD when Docker is available."""
        import integration_coworker.codegen.sandbox_multilang as module
        module._DOCKER_AVAILABLE_CACHE = None
        
        with patch.object(module, 'is_docker_available', return_value=True):
            module._DOCKER_AVAILABLE_CACHE = None
            tier = recommended_tier()
        
        assert tier == Tier.PROD
    
    def test_recommended_tier_returns_exp_when_docker_unavailable(self):
        """recommended_tier() should return Tier.EXP when Docker is unavailable."""
        import integration_coworker.codegen.sandbox_multilang as module
        module._DOCKER_AVAILABLE_CACHE = None
        
        with patch.object(module, 'is_docker_available', return_value=False):
            module._DOCKER_AVAILABLE_CACHE = None
            tier = recommended_tier()
        
        assert tier == Tier.EXP
    
    def test_recommended_tier_accepts_override(self):
        """recommended_tier() should accept docker_available override."""
        tier_with_docker = recommended_tier(docker_available=True)
        tier_without_docker = recommended_tier(docker_available=False)
        
        assert tier_with_docker == Tier.PROD
        assert tier_without_docker == Tier.EXP
    
    def test_recommended_tier_is_not_called_by_config_init(self):
        """recommended_tier() should NOT be called implicitly by MultiLangSandboxConfig."""
        import integration_coworker.codegen.sandbox_multilang as module
        
        with patch.object(module, 'recommended_tier') as mock_recommended:
            # Creating default config should NOT call recommended_tier
            config = MultiLangSandboxConfig()
            mock_recommended.assert_not_called()


class TestDockerCaching:
    """Tests for Docker availability caching."""
    
    def test_docker_availability_cached(self):
        """Docker availability should be cached after first check."""
        import integration_coworker.codegen.sandbox_multilang as module
        
        # Reset cache
        module._DOCKER_AVAILABLE_CACHE = None
        
        with patch.object(module, 'is_docker_available', return_value=True) as mock:
            # First call should check
            result1 = _check_docker_available_cached()
            assert result1 is True
            assert mock.call_count == 1
            
            # Second call should use cache
            result2 = _check_docker_available_cached()
            assert result2 is True
            assert mock.call_count == 1  # No additional calls


class TestTierExpHostExecution:
    """Tests that Tier.EXP uses host execution without Docker requirement."""
    
    @pytest.mark.asyncio
    async def test_tier_exp_does_not_require_docker(self):
        """Tier.EXP should not fail even when Docker is unavailable."""
        import integration_coworker.codegen.sandbox_multilang as module
        module._DOCKER_AVAILABLE_CACHE = False
        
        config = MultiLangSandboxConfig(tier=Tier.EXP)
        
        artifacts = [
            ArtifactFile(path="src/client.ts", content="export class TestClient {}")
        ]
        
        # Mock strategy that succeeds
        mock_strategy = MagicMock()
        mock_strategy.probe_toolchain.return_value = {
            "tier_eligible": Tier.EXP,
            "executables_found": ["node", "npm"],
            "failure_reason": None,
        }
        mock_env = MagicMock()
        mock_env.deps_resolved = True
        mock_env.sandbox_path = Path("/tmp/test")
        mock_strategy.provision.return_value = mock_env
        
        mock_validation = MagicMock()
        mock_validation.success = True
        mock_validation.gates = []
        mock_validation.untrusted_checks = []
        mock_validation.skipped_gates = []
        mock_strategy.validate.return_value = mock_validation
        
        with patch.object(module, 'get_strategy', return_value=lambda: mock_strategy):
            with patch('tempfile.mkdtemp', return_value='/tmp/test_sandbox'):
                result = await execute_multilang_sandbox(
                    artifacts=artifacts,
                    language=ArtifactLanguage.TYPESCRIPT,
                    config=config,
                )
        
        # Should not fail - Tier.EXP does not require Docker
        assert result.tier == Tier.EXP


class TestPythonBypassesDocker:
    """Tests that Python language uses existing sandbox, not Docker."""
    
    @pytest.mark.asyncio
    async def test_python_uses_existing_sandbox(self):
        """Python artifacts should use the existing Python sandbox."""
        import integration_coworker.codegen.sandbox_multilang as module
        
        # Python doesn't need Docker for Tier.EXP
        config = MultiLangSandboxConfig(tier=Tier.EXP)
        
        artifacts = [
            ArtifactFile(path="client.py", content="class TestClient: pass")
        ]
        
        # Mock the Python sandbox
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.gate_results = []
        mock_result.sandbox_dir = "/tmp/py_sandbox"
        mock_result.summary = "PASSED"
        
        with patch.object(module, '_execute_python_sandbox', return_value=mock_result) as mock_py:
            result = await execute_multilang_sandbox(
                artifacts=artifacts,
                language=ArtifactLanguage.PYTHON,
                config=config,
            )
        
        # Should call Python sandbox, not Docker
        mock_py.assert_called_once()
        assert result.success is True
