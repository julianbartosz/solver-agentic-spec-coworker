"""
Tests for explicit tier configuration in multi-language sandbox.

Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.4:
- Tier is EXPLICIT from config/env, never inferred from Docker availability
- Tier.PROD requires use_docker=True (validated at config creation)
- Tier.PROD + Docker unavailable = hard fail (no silent downgrade)

Evidence for production hardening:
- docker_runner.py:288 - validate phase uses network="none"
- MultiLangSandboxConfig.__post_init__ - validates tier+docker consistency
- Settings.validate() - validates MULTILANG_TIER+MULTILANG_USE_DOCKER
"""
import os
import pytest
from unittest.mock import patch

from integration_coworker.config import Settings
from integration_coworker.codegen.sandbox_multilang import (
    MultiLangSandboxConfig,
    Tier,
    recommended_tier,
)
from integration_coworker.codegen.gates.docker_runner import (
    DockerNotAvailableError,
    is_docker_available,
)


class TestMultilangTierConfig:
    """Tests for explicit tier configuration."""
    
    def test_settings_default_tier_is_exp(self):
        """Default tier should be EXP (safe, host execution)."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear any existing env vars
            for key in list(os.environ.keys()):
                if key.startswith('MULTILANG'):
                    del os.environ[key]
            
            s = Settings()
            assert s.multilang_tier == "exp"
            assert s.multilang_use_docker is False
    
    def test_settings_validates_prod_without_docker(self):
        """MULTILANG_TIER=prod without use_docker should warn."""
        with patch.dict(os.environ, {
            'MULTILANG_TIER': 'prod',
            'MULTILANG_USE_DOCKER': 'false',
        }):
            s = Settings()
            warnings = s.validate()
            assert any('MULTILANG_TIER=prod requires MULTILANG_USE_DOCKER=true' in w for w in warnings)
    
    def test_settings_accepts_valid_prod_config(self):
        """MULTILANG_TIER=prod with use_docker=true should be valid."""
        with patch.dict(os.environ, {
            'MULTILANG_TIER': 'prod',
            'MULTILANG_USE_DOCKER': 'true',
        }):
            s = Settings()
            # Filter out unrelated warnings (e.g., database warnings)
            tier_warnings = [w for w in s.validate() if 'MULTILANG' in w]
            assert len(tier_warnings) == 0
    
    def test_config_rejects_prod_without_use_docker(self):
        """MultiLangSandboxConfig(tier=PROD, use_docker=False) should raise."""
        with pytest.raises(ValueError) as exc:
            MultiLangSandboxConfig(tier=Tier.PROD, use_docker=False)
        
        assert "use_docker=True" in str(exc.value)
    
    def test_config_prod_requires_docker_available(self):
        """MultiLangSandboxConfig(tier=PROD, use_docker=True) should fail without Docker."""
        # Mock Docker as unavailable
        with patch('integration_coworker.codegen.sandbox_multilang._check_docker_available_cached', return_value=False):
            with pytest.raises(DockerNotAvailableError):
                MultiLangSandboxConfig(tier=Tier.PROD, use_docker=True)
    
    def test_config_exp_works_without_docker(self):
        """MultiLangSandboxConfig(tier=EXP) should work without Docker."""
        # This should not raise even if Docker is unavailable
        config = MultiLangSandboxConfig(tier=Tier.EXP, use_docker=False)
        assert config.tier == Tier.EXP
        assert config.use_docker is False
    
    def test_recommended_tier_is_advisory_only(self):
        """recommended_tier() should be advisory, not auto-applied."""
        # With Docker
        with patch('integration_coworker.codegen.sandbox_multilang._check_docker_available_cached', return_value=True):
            tier = recommended_tier()
            assert tier == Tier.PROD
        
        # Without Docker
        with patch('integration_coworker.codegen.sandbox_multilang._check_docker_available_cached', return_value=False):
            tier = recommended_tier()
            assert tier == Tier.EXP


class TestNetworkNoneValidation:
    """Tests for --network none in validate phase.
    
    Evidence citation:
    - docker_runner.py:283-288 - validate phase sets network="none"
    - docker_runner.py:354 - documents network: "bridge" (provision) or "none" (validate)
    """
    
    def test_config_default_network_none(self):
        """Default config should enable --network none for validate phase."""
        config = MultiLangSandboxConfig()
        assert config.container_network_none is True
    
    def test_settings_default_network_none(self):
        """Settings should default to network_none=True."""
        s = Settings()
        assert s.multilang_network_none is True
    
    def test_network_none_can_be_disabled(self):
        """Network none can be disabled via env (for debugging only)."""
        with patch.dict(os.environ, {'MULTILANG_NETWORK_NONE': 'false'}):
            s = Settings()
            assert s.multilang_network_none is False


class TestPytestExitCode5Policy:
    """Tests for pytest exit code 5 (no tests collected) policy.
    
    Per pytest docs: https://docs.pytest.org/en/4.6.x/usage.html
    Exit code 5 means "No tests were collected."
    
    Policy: Tier.PROD should treat exit code 5 as failure unless allowlisted.
    
    Implementation: SandboxConfig.fail_on_no_tests controls the behavior.
    - Production profile sets fail_on_no_tests=True
    - Development profile sets fail_on_no_tests=False
    """
    
    def test_exit_code_5_documented(self):
        """Document pytest exit codes for reference."""
        # From pytest docs
        EXIT_CODES = {
            0: "All tests passed",
            1: "Tests failed",
            2: "Test execution interrupted",
            3: "Internal error",
            4: "pytest CLI usage error",
            5: "No tests collected",
        }
        
        # 5 is explicitly "no tests collected"
        assert EXIT_CODES[5] == "No tests collected"
    
    def test_sandbox_config_fail_on_no_tests_default_false(self):
        """SandboxConfig.fail_on_no_tests should default to False."""
        from integration_coworker.codegen.sandbox import SandboxConfig
        
        config = SandboxConfig()
        assert config.fail_on_no_tests is False
    
    def test_sandbox_config_fail_on_no_tests_can_be_enabled(self):
        """SandboxConfig.fail_on_no_tests can be set to True."""
        from integration_coworker.codegen.sandbox import SandboxConfig
        
        config = SandboxConfig(fail_on_no_tests=True)
        assert config.fail_on_no_tests is True
    
    def test_production_profile_sets_fail_on_no_tests(self):
        """Production profile should set fail_on_no_tests=True."""
        # This tests that the production codegen profile enforces the policy
        # The profile is defined in config/profiles.py
        from integration_coworker.config.profiles import PROFILES
        
        prod_profile = PROFILES.get("production")
        assert prod_profile is not None, "Production profile must exist"
        assert prod_profile.fail_on_no_tests is True, \
            "Production profile must set fail_on_no_tests=True"
