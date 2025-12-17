"""
Tests for IntegrationCoworkerConfig schema, load_config, save_config, validate_config.

Per ADR-0002: Config-First with LLM Fallback.
"""
import pytest
import tempfile
from pathlib import Path
from datetime import datetime

from integration_coworker.repo.config_schema import (
    IntegrationCoworkerConfig,
    ProfileConfig,
    LayoutConfig,
    ConventionsConfig,
    HooksConfig,
    MetadataConfig,
    ConfigValidationResult,
    load_config,
    save_config,
    validate_config,
    config_to_profile,
    profile_to_config,
)
from integration_coworker.repo.models import RepoProfile


class TestProfileConfig:
    """Tests for ProfileConfig model."""

    def test_profile_config_python(self):
        """Test creating a Python profile config."""
        config = ProfileConfig(
            name="my-fastapi-service",
            framework="fastapi",
            language="python",
        )
        assert config.name == "my-fastapi-service"
        assert config.framework == "fastapi"
        assert config.language == "python"

    def test_profile_config_typescript(self):
        """Test creating a TypeScript profile config."""
        config = ProfileConfig(
            name="my-nextjs-app",
            framework="nextjs",
            language="typescript",
        )
        assert config.language == "typescript"

    def test_profile_config_no_framework(self):
        """Test creating a profile without framework hint."""
        config = ProfileConfig(
            name="generic-project",
            language="python",
        )
        assert config.framework is None


class TestLayoutConfig:
    """Tests for LayoutConfig model."""

    def test_layout_config_basic(self):
        """Test basic layout config."""
        config = LayoutConfig(
            integrations_root="src/integrations",
            tests_root="tests/integrations",
        )
        assert config.integrations_root == "src/integrations"
        assert config.tests_root == "tests/integrations"
        assert config.clients_dir is None

    def test_layout_config_with_subdirs(self):
        """Test layout config with subdirectories."""
        config = LayoutConfig(
            integrations_root="lib/external",
            tests_root="__tests__/external",
            clients_dir="lib/external/clients",
            flows_dir="lib/external/flows",
        )
        assert config.clients_dir == "lib/external/clients"
        assert config.flows_dir == "lib/external/flows"


class TestConventionsConfig:
    """Tests for ConventionsConfig model."""

    def test_conventions_defaults(self):
        """Test default conventions."""
        config = ConventionsConfig()
        assert "{provider}" in config.client_module
        assert "{provider}" in config.flow_module
        assert "{task}" in config.flow_module

    def test_conventions_custom(self):
        """Test custom conventions."""
        config = ConventionsConfig(
            client_module="clients/{provider}_client.py",
            flow_module="services/{provider}/{task}_service.py",
            test_module="test_{provider}_{task}.py",
        )
        assert config.client_module == "clients/{provider}_client.py"

    def test_conventions_invalid_pattern(self):
        """Test that patterns without placeholders are rejected."""
        with pytest.raises(ValueError, match="placeholders"):
            ConventionsConfig(
                client_module="client.py",  # No placeholder
            )


class TestIntegrationCoworkerConfig:
    """Tests for the main IntegrationCoworkerConfig model."""

    def test_full_config(self):
        """Test creating a complete config."""
        config = IntegrationCoworkerConfig(
            version="1.0",
            profile=ProfileConfig(
                name="my-service",
                framework="fastapi",
                language="python",
            ),
            layout=LayoutConfig(
                integrations_root="src/external_apis",
                tests_root="tests/external_apis",
            ),
            conventions=ConventionsConfig(
                client_module="clients/{provider}.py",
                flow_module="flows/{provider}_{task}.py",
                test_module="test_{provider}_{task}.py",
            ),
            hooks=HooksConfig(
                router_file="src/app/router.py",
                router_marker="# AUTO_INTEGRATION",
            ),
            metadata=MetadataConfig(
                generated_by="integration-coworker",
                detection_method="user_provided",
            ),
        )
        assert config.version == "1.0"
        assert config.profile.name == "my-service"
        assert config.hooks.router_file == "src/app/router.py"

    def test_minimal_config(self):
        """Test creating a minimal config."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="minimal", language="python"),
            layout=LayoutConfig(
                integrations_root="integrations",
                tests_root="tests",
            ),
        )
        assert config.version == "1.0"
        assert config.conventions is not None  # Has defaults
        assert config.hooks is None

    def test_typescript_convention_adjustment(self):
        """Test that TypeScript projects get .ts extensions."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="ts-project", language="typescript"),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests/integrations",
            ),
        )
        # Model validator should adjust .py to .ts
        assert config.conventions.client_module.endswith(".ts")
        assert config.conventions.flow_module.endswith(".ts")


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_valid_config(self, tmp_path):
        """Test loading a valid YAML config."""
        config_content = """
version: "1.0"
profile:
  name: test-project
  framework: fastapi
  language: python
layout:
  integrations_root: src/integrations
  tests_root: tests/integrations
conventions:
  client_module: "clients/{provider}.py"
  flow_module: "flows/{provider}_{task}.py"
  test_module: "test_{provider}_{task}.py"
"""
        config_file = tmp_path / ".integration-coworker.yaml"
        config_file.write_text(config_content)

        config = load_config(config_file)

        assert config is not None
        assert config.profile.name == "test-project"
        assert config.profile.framework == "fastapi"
        assert config.layout.integrations_root == "src/integrations"

    def test_load_minimal_config(self, tmp_path):
        """Test loading a minimal config with defaults."""
        config_content = """
profile:
  name: minimal
  language: python
layout:
  integrations_root: integrations
  tests_root: tests
"""
        config_file = tmp_path / ".integration-coworker.yaml"
        config_file.write_text(config_content)

        config = load_config(config_file)

        assert config is not None
        assert config.version == "1.0"
        assert config.conventions.client_module == "clients/{provider}.py"

    def test_load_nonexistent_file(self, tmp_path):
        """Test loading a non-existent file returns None."""
        config = load_config(tmp_path / "nonexistent.yaml")
        assert config is None

    def test_load_invalid_yaml(self, tmp_path):
        """Test loading invalid YAML returns None."""
        config_file = tmp_path / ".integration-coworker.yaml"
        config_file.write_text("this: is: not: valid: yaml:")

        config = load_config(config_file)
        assert config is None

    def test_load_missing_required_fields(self, tmp_path):
        """Test loading config with missing required fields returns None."""
        config_content = """
profile:
  name: incomplete
  # Missing language
layout:
  integrations_root: src
  tests_root: tests
"""
        config_file = tmp_path / ".integration-coworker.yaml"
        config_file.write_text(config_content)

        config = load_config(config_file)
        assert config is None


class TestSaveConfig:
    """Tests for save_config function."""

    def test_save_and_reload(self, tmp_path):
        """Test saving a config and reloading it."""
        original = IntegrationCoworkerConfig(
            profile=ProfileConfig(
                name="save-test",
                framework="fastapi",
                language="python",
            ),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests/integrations",
            ),
            metadata=MetadataConfig(
                detection_method="llm_inference",
            ),
        )

        config_file = tmp_path / ".integration-coworker.yaml"
        result = save_config(original, config_file)

        assert result is True
        assert config_file.exists()

        # Reload and verify
        reloaded = load_config(config_file)
        assert reloaded is not None
        assert reloaded.profile.name == "save-test"
        assert reloaded.profile.framework == "fastapi"
        assert reloaded.layout.integrations_root == "src/integrations"

    def test_save_includes_header(self, tmp_path):
        """Test that saved config includes helpful header comments."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="header-test", language="python"),
            layout=LayoutConfig(integrations_root="src", tests_root="tests"),
        )

        config_file = tmp_path / ".integration-coworker.yaml"
        save_config(config, config_file)

        content = config_file.read_text()
        assert "# .integration-coworker.yaml" in content
        assert "feel free to edit" in content.lower()

    def test_save_sets_generated_at(self, tmp_path):
        """Test that save sets the generated_at timestamp."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="timestamp-test", language="python"),
            layout=LayoutConfig(integrations_root="src", tests_root="tests"),
        )

        config_file = tmp_path / ".integration-coworker.yaml"
        save_config(config, config_file)

        reloaded = load_config(config_file)
        assert reloaded.metadata.generated_at is not None


class TestValidateConfig:
    """Tests for validate_config function."""

    def test_validate_valid_config(self, tmp_path):
        """Test validating a correct config."""
        # Create the expected directories
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()

        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="valid", language="python"),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests/integrations",
            ),
        )

        result = validate_config(config, tmp_path)

        assert result.is_valid
        assert len(result.errors) == 0

    def test_validate_missing_parent_directory(self, tmp_path):
        """Test validation warns about missing parent directories."""
        # Don't create any directories
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="missing-parent", language="python"),
            layout=LayoutConfig(
                integrations_root="nonexistent/integrations",
                tests_root="also-nonexistent/tests",
            ),
        )

        result = validate_config(config, tmp_path)

        # Should have warnings but still be valid (we can create the dirs)
        assert result.is_valid
        assert len(result.warnings) >= 1

    def test_validate_convention_missing_provider(self, tmp_path):
        """Test validation rejects patterns without {provider}."""
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()

        # Manually construct config with invalid pattern
        # (normally blocked by Pydantic, but test the validator)
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="bad-pattern", language="python"),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests/integrations",
            ),
        )
        # Override the validated pattern
        object.__setattr__(config.conventions, "client_module", "client.py")

        result = validate_config(config, tmp_path, check_paths_exist=False)

        assert not result.is_valid
        assert any("provider" in err.message for err in result.errors)

    def test_validate_hooks_missing_files(self, tmp_path):
        """Test validation warns about missing hook files."""
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()

        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="missing-hooks", language="python"),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests/integrations",
            ),
            hooks=HooksConfig(
                router_file="src/router.py",  # Doesn't exist
            ),
        )

        result = validate_config(config, tmp_path)

        assert result.is_valid  # Warnings don't make it invalid
        assert len(result.warnings) >= 1
        assert any("router_file" in w.field for w in result.warnings)


class TestConfigToProfile:
    """Tests for config_to_profile conversion."""

    def test_config_to_profile_basic(self):
        """Test converting config to RepoProfile."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(
                name="convert-test",
                framework="fastapi",
                language="python",
            ),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests/integrations",
            ),
            metadata=MetadataConfig(
                detection_method="llm_inference",
                detection_confidence=0.85,
            ),
        )

        profile = config_to_profile(config)

        assert isinstance(profile, RepoProfile)
        assert profile.name == "convert-test"
        assert profile.framework == "fastapi"
        assert profile.language == "python"
        assert profile.integrations_root == "src/integrations"
        assert profile.tests_root == "tests/integrations"
        assert profile.profile_source == "llm_inference"
        assert profile.detection_confidence == 0.85

    def test_config_to_profile_with_hooks(self):
        """Test converting config with hooks."""
        config = IntegrationCoworkerConfig(
            profile=ProfileConfig(name="hooks-test", language="python"),
            layout=LayoutConfig(
                integrations_root="src/integrations",
                tests_root="tests",
            ),
            hooks=HooksConfig(
                router_file="src/router.py",
                router_marker="# AUTO",
            ),
        )

        profile = config_to_profile(config)

        assert profile.integration_hooks is not None
        assert profile.integration_hooks["router_file"] == "src/router.py"


class TestProfileToConfig:
    """Tests for profile_to_config conversion."""

    def test_profile_to_config_basic(self):
        """Test converting RepoProfile to config."""
        profile = RepoProfile(
            name="reverse-convert",
            framework="django",
            language="python",
            integrations_root="apps/integrations",
            tests_root="tests/apps",
            detection_confidence=0.9,
            profile_source="archetype",
        )

        config = profile_to_config(profile)

        assert isinstance(config, IntegrationCoworkerConfig)
        assert config.profile.name == "reverse-convert"
        assert config.profile.framework == "django"
        assert config.layout.integrations_root == "apps/integrations"
        assert config.metadata.detection_method == "archetype"
        assert config.metadata.detection_confidence == 0.9

    def test_profile_to_config_preserves_conventions(self):
        """Test that conventions are preserved in conversion."""
        profile = RepoProfile(
            name="conventions-test",
            language="typescript",
            integrations_root="lib/external",
            tests_root="test/external",
            conventions={
                "client_module_pattern": "clients/{provider}Client.ts",
                "flow_module_pattern": "flows/{provider}/{task}Flow.ts",
                "test_module_pattern": "{provider}.{task}.spec.ts",
            },
        )

        config = profile_to_config(profile)

        assert config.conventions.client_module == "clients/{provider}Client.ts"
        assert config.conventions.flow_module == "flows/{provider}/{task}Flow.ts"

    def test_roundtrip_conversion(self):
        """Test that profile -> config -> profile preserves data."""
        original = RepoProfile(
            name="roundtrip",
            framework="nextjs",
            language="typescript",
            integrations_root="lib/integrations",
            tests_root="__tests__/integrations",
            conventions={
                "client_module_pattern": "clients/{provider}.ts",
                "flow_module_pattern": "flows/{provider}_{task}.ts",
                "test_module_pattern": "{provider}_{task}.test.ts",
            },
            detection_confidence=0.75,
            profile_source="heuristic",
        )

        config = profile_to_config(original)
        restored = config_to_profile(config)

        assert restored.name == original.name
        assert restored.framework == original.framework
        assert restored.language == original.language
        assert restored.integrations_root == original.integrations_root
        assert restored.tests_root == original.tests_root
        assert restored.detection_confidence == original.detection_confidence
        assert restored.profile_source == original.profile_source
