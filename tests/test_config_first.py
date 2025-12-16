"""
Tests for ADR-0002: Config-First with LLM Fallback.

Tests the config file loading, validation, and profile resolution cascade.
"""
import pytest
import tempfile
from pathlib import Path

# Mark all tests in this module as not requiring database
pytestmark = pytest.mark.no_db

from integration_coworker.repo.config_schema import (
    IntegrationCoworkerConfig,
    ProfileConfig,
    LayoutConfig,
    ConventionsConfig,
    HooksConfig,
    MetadataConfig,
    load_config,
    save_config,
    validate_config,
    config_to_profile,
    profile_to_config,
)
from integration_coworker.repo.models import RepoProfile
from integration_coworker.repo.profiles import (
    detect_profile_from_repo,
    GENERIC_PYTHON_PROFILE,
)


class TestConfigSchema:
    """Test IntegrationCoworkerConfig Pydantic models."""
    
    def test_minimal_config_creation(self):
        """Test creating a minimal valid config."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(
                name="test-project",
                language="python",
            ),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests/integrations",
            ),
        )
        
        assert config.version == "1.0"
        assert config.profile.name == "test-project"
        assert config.profile.language == "python"
        assert config.layout.integrations_root == "src/integrations"
    
    def test_full_config_creation(self):
        """Test creating a full config with all fields."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(
                name="my-fastapi-service",
                framework="fastapi",
                language="python",
            ),
            layout=LayoutConfig(
                integrations_root="src/external_apis",
                tests_root="tests/external_apis",
                clients_dir="clients",
                flows_dir="flows",
            ),
            conventions=ConventionsConfig(
                client_module="clients/{provider}.py",
                flow_module="flows/{provider}_{task}.py",
                test_module="test_{provider}_{task}.py",
            ),
            hooks=HooksConfig(
                router_file="src/app/api/router.py",
                router_marker="# AUTO_INTEGRATION",
            ),
            metadata=MetadataConfig(
                detection_method="user_provided",
                detection_confidence=1.0,
            ),
        )
        
        assert config.profile.framework == "fastapi"
        assert config.hooks.router_file == "src/app/api/router.py"
        assert config.metadata.detection_method == "user_provided"
    
    def test_typescript_convention_auto_adjustment(self):
        """Test that TypeScript projects get correct file extensions."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(
                name="ts-project",
                language="typescript",
            ),
            layout=LayoutConfig(
                integrations_root="lib/integrations",
                tests_root="__tests__/integrations",
            ),
        )
        
        # Conventions should be adjusted for TypeScript
        assert config.conventions.client_module.endswith(".ts")
        assert config.conventions.flow_module.endswith(".ts")
    
    def test_convention_pattern_validation(self):
        """Test that convention patterns require placeholders."""
        with pytest.raises(ValueError, match="placeholder"):
            ConventionsConfig(
                client_module="clients/fixed_name.py",  # Missing {provider}
                flow_module="flows/{provider}_{task}.py",
                test_module="test_{provider}_{task}.py",
            )


class TestConfigIO:
    """Test config file loading and saving."""
    
    def test_save_and_load_config(self):
        """Test round-trip of saving and loading config."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(
                name="test-roundtrip",
                framework="fastapi",
                language="python",
            ),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests",
            ),
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".integration-coworker.yaml"
            
            # Save
            assert save_config(config, config_path)
            assert config_path.exists()
            
            # Load
            loaded = load_config(config_path)
            assert loaded is not None
            assert loaded.profile.name == "test-roundtrip"
            assert loaded.profile.framework == "fastapi"
    
    def test_load_nonexistent_config(self):
        """Test loading a config that doesn't exist returns None."""
        result = load_config(Path("/nonexistent/.integration-coworker.yaml"))
        assert result is None
    
    def test_save_creates_yaml_with_header(self):
        """Test that saved config has helpful header comment."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="test", language="python"),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests",
            ),
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".integration-coworker.yaml"
            save_config(config, config_path)
            
            content = config_path.read_text()
            assert "# .integration-coworker.yaml" in content
            assert "feel free to edit" in content
            assert "ADR-0002" in content


class TestConfigValidation:
    """Test config validation against repo structure."""
    
    def test_validate_valid_config(self):
        """Test validation passes for valid config."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="test", language="python"),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests",
            ),
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src").mkdir()
            
            result = validate_config(config, repo_root)
            assert result.is_valid
    
    def test_validate_warns_on_missing_parent(self):
        """Test validation warns when parent directories don't exist."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="test", language="python"),
            layout=LayoutConfig(
                integrations_root="nonexistent/path/integrations",
                tests_root="tests",
            ),
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            
            result = validate_config(config, repo_root, check_paths_exist=True)
            assert len(result.warnings) > 0
            assert any("does not exist" in str(w) for w in result.warnings)


class TestProfileConversion:
    """Test conversion between Config and RepoProfile."""
    
    def test_config_to_profile(self):
        """Test converting IntegrationCoworkerConfig to RepoProfile."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(
                name="my-service",
                framework="fastapi",
                language="python",
            ),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests/integrations",
                clients_dir="clients",
                flows_dir="flows",
            ),
            hooks=HooksConfig(
                router_file="src/router.py",
                router_marker="# AUTO",
            ),
        )
        
        profile = config_to_profile(config)
        
        assert profile.name == "my-service"
        assert profile.framework == "fastapi"
        assert profile.language == "python"
        assert profile.integrations_root == "src/integrations"
        assert profile.integration_hooks["router_file"] == "src/router.py"
    
    def test_profile_to_config(self):
        """Test converting RepoProfile to IntegrationCoworkerConfig."""
        profile = RepoProfile(
            name="test-profile",
            framework="flask",
            language="python",
            integrations_root="app/integrations",
            tests_root="tests",
            conventions={
                "client_module_pattern": "clients/{provider}.py",
                "flow_module_pattern": "flows/{provider}_{task}.py",
                "test_module_pattern": "test_{provider}_{task}.py",
            },
        )
        
        config = profile_to_config(profile, detection_method="archetype")
        
        assert config.profile.name == "test-profile"
        assert config.profile.framework == "flask"
        assert config.metadata.detection_method == "archetype"


class TestConfigFirstCascade:
    """Test the config-first profile resolution cascade."""
    
    def test_config_file_takes_priority(self):
        """Test that .integration-coworker.yaml takes priority over detection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            
            # Create a config file
            config = IntegrationCoworkerConfig(
                profile=ProfileConfig(
                    name="explicit-config",
                    framework="custom",
                    language="python",
                ),
                layout=LayoutConfig(
                    integrations_root="custom/path",
                    tests_root="custom/tests",
                ),
            )
            config_path = repo_root / ".integration-coworker.yaml"
            save_config(config, config_path)
            
            # Also create a pyproject.toml with fastapi (would normally detect FastAPI)
            (repo_root / "pyproject.toml").write_text('[tool.poetry]\nfastapi = "0.100"')
            
            # Detection should use config file, not FastAPI detection
            profile = detect_profile_from_repo(repo_root)
            assert profile.name == "explicit-config"
            assert profile.profile_source == "config_file"
    
    @pytest.mark.skip(reason="Framework detection is now a supported feature, not deprecated")
    def test_archetype_detection_emits_deprecation_warning(self):
        """Test that archetype detection emits deprecation warning.
        
        NOTE: This test is skipped because framework detection (fastapi, flask, etc.)
        is now a supported feature for backward compatibility. Per ADR-0002, the
        config-first approach is preferred, but framework detection remains available
        for repos without explicit config files.
        """
        pass
    
    def test_generic_fallback_has_source_tracking(self):
        """Test that generic fallback profiles have profile_source set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            
            # Create empty repo (should fall back to generic)
            profile = detect_profile_from_repo(repo_root)
            assert profile.profile_source in ("generic_fallback", "archetype")


class TestDirForArtifact:
    """Test the _dir_for_artifact path resolution function."""
    
    def test_uses_layout_hints_first(self):
        """Test that layout_hints take priority."""
        from integration_coworker.graph.nodes.analyze_repo_layout import _dir_for_artifact
        
        profile = RepoProfile(
            name="test",
            language="python",
            integrations_root="src/integrations",
            tests_root="tests",
            layout_hints={
                "clients_dir": "custom/clients",
                "workflows_dir": "custom/workflows",
            },
        )
        
        assert _dir_for_artifact(profile, "client", "stripe", "charge") == "custom/clients"
        assert _dir_for_artifact(profile, "workflow", "stripe", "charge") == "custom/workflows"
    
    def test_flow_alias_works(self):
        """Test that 'flow' is an alias for 'workflow'."""
        from integration_coworker.graph.nodes.analyze_repo_layout import _dir_for_artifact
        
        profile = RepoProfile(
            name="test",
            language="python",
            integrations_root="src/integrations",
            tests_root="tests",
            layout_hints={
                "workflows_dir": "custom/flows",
            },
        )
        
        assert _dir_for_artifact(profile, "flow", "stripe", "charge") == "custom/flows"
    
    def test_falls_back_to_conventions(self):
        """Test fallback to conventions when no layout_hints."""
        from integration_coworker.graph.nodes.analyze_repo_layout import _dir_for_artifact
        
        profile = RepoProfile(
            name="test",
            language="python",
            integrations_root="lib/external",
            tests_root="tests",
            conventions={
                "client_module_pattern": "api_clients/{provider}.py",
                "flow_module_pattern": "services/{provider}_{task}.py",
            },
        )
        
        # Should derive from conventions
        client_dir = _dir_for_artifact(profile, "client", "stripe", "charge")
        assert "api_clients" in client_dir
