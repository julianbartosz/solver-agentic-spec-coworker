"""
Integration Coworker Config Schema.

Defines the Pydantic models for .integration-coworker.yaml config files.
Per ADR-0002: Config-First with LLM Fallback.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# =============================================================================
# Config Sub-Models
# =============================================================================


class ProfileConfig(BaseModel):
    """Profile identification section of the config."""

    name: str = Field(
        description="Human-readable name for this configuration"
    )
    framework: Optional[str] = Field(
        default=None,
        description="Framework hint for codegen style (e.g., 'fastapi', 'nextjs')",
    )
    language: Literal["python", "typescript", "javascript"] = Field(
        description="Primary programming language"
    )


class LayoutConfig(BaseModel):
    """File layout configuration - where to place generated code."""

    integrations_root: str = Field(
        description="Root directory for integration code (relative to repo root)"
    )
    tests_root: str = Field(
        description="Root directory for integration tests (relative to repo root)"
    )
    clients_dir: Optional[str] = Field(
        default=None,
        description="Subdirectory for API client modules",
    )
    flows_dir: Optional[str] = Field(
        default=None,
        description="Subdirectory for workflow/flow modules",
    )


class ConventionsConfig(BaseModel):
    """Naming conventions for generated files."""

    client_module: str = Field(
        default="clients/{provider}.py",
        description="Pattern for client module filenames",
    )
    flow_module: str = Field(
        default="flows/{provider}_{task}.py",
        description="Pattern for flow/workflow module filenames",
    )
    test_module: str = Field(
        default="test_{provider}_{task}.py",
        description="Pattern for test module filenames",
    )

    @field_validator("client_module", "flow_module", "test_module")
    @classmethod
    def validate_pattern(cls, v: str) -> str:
        """Ensure patterns contain at least one placeholder."""
        if "{" not in v or "}" not in v:
            raise ValueError(
                f"Convention pattern must contain placeholders like {{provider}}: {v}"
            )
        return v


class HooksConfig(BaseModel):
    """Optional hooks for auto-wiring integrations into the target repo."""

    router_file: Optional[str] = Field(
        default=None,
        description="File to register new routes (e.g., 'src/app/api/router.py')",
    )
    router_marker: Optional[str] = Field(
        default=None,
        description="Marker comment where routes should be added",
    )
    settings_file: Optional[str] = Field(
        default=None,
        description="File containing configuration/settings",
    )
    settings_marker: Optional[str] = Field(
        default=None,
        description="Marker comment where settings should be added",
    )
    module_file: Optional[str] = Field(
        default=None,
        description="Module registration file (e.g., NestJS module)",
    )


class MetadataConfig(BaseModel):
    """Metadata about how the config was generated."""

    generated_by: str = Field(
        default="integration-coworker",
        description="Tool that generated this config",
    )
    generated_at: Optional[datetime] = Field(
        default=None,
        description="When the config was generated",
    )
    detection_method: Optional[
        Literal["user_provided", "llm_inference", "archetype", "heuristic"]
    ] = Field(
        default=None,
        description="How the config was determined",
    )
    detection_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score of detection (0.0-1.0)",
    )


# =============================================================================
# Main Config Model
# =============================================================================


class IntegrationCoworkerConfig(BaseModel):
    """
    Complete schema for .integration-coworker.yaml config files.

    Per ADR-0002, this config file takes priority over archetype detection.
    Once generated (by LLM or manually), it caches the repo structure decisions.

    Example YAML:
        version: "1.0"
        profile:
          name: "my-fastapi-service"
          framework: "fastapi"
          language: "python"
        layout:
          integrations_root: "src/external_apis"
          tests_root: "tests/external_apis"
        conventions:
          client_module: "clients/{provider}.py"
          flow_module: "flows/{provider}_{task}.py"
          test_module: "test_{provider}_{task}.py"
        hooks:
          router_file: "src/app/api/router.py"
          router_marker: "# AUTO_INTEGRATION"
        metadata:
          generated_by: "integration-coworker"
          detection_method: "llm_inference"
    """

    version: str = Field(
        default="1.0",
        description="Config schema version",
    )
    profile: ProfileConfig = Field(
        description="Profile identification"
    )
    layout: LayoutConfig = Field(
        description="File layout configuration"
    )
    conventions: ConventionsConfig = Field(
        default_factory=ConventionsConfig,
        description="Naming conventions",
    )
    hooks: Optional[HooksConfig] = Field(
        default=None,
        description="Optional auto-wiring hooks",
    )
    metadata: MetadataConfig = Field(
        default_factory=MetadataConfig,
        description="Generation metadata",
    )

    @model_validator(mode="after")
    def set_default_conventions_for_language(self) -> "IntegrationCoworkerConfig":
        """Set language-appropriate default patterns if not specified."""
        lang = self.profile.language
        ext = ".ts" if lang in ("typescript", "javascript") else ".py"

        # Only update if using the Python defaults on a non-Python project
        if lang != "python":
            if self.conventions.client_module.endswith(".py"):
                self.conventions.client_module = self.conventions.client_module.replace(
                    ".py", ext
                )
            if self.conventions.flow_module.endswith(".py"):
                self.conventions.flow_module = self.conventions.flow_module.replace(
                    ".py", ext
                )
            if self.conventions.test_module.endswith(".py"):
                # TypeScript uses .test.ts, not test_.ts
                if lang == "typescript":
                    self.conventions.test_module = "{provider}_{task}.test.ts"
                else:
                    self.conventions.test_module = self.conventions.test_module.replace(
                        ".py", ext
                    )

        return self


# =============================================================================
# Config I/O Functions
# =============================================================================


def load_config(config_path: Path) -> Optional[IntegrationCoworkerConfig]:
    """
    Load and validate an IntegrationCoworkerConfig from a YAML file.

    Args:
        config_path: Path to the .integration-coworker.yaml file

    Returns:
        Validated IntegrationCoworkerConfig, or None if parsing fails
    """
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed, cannot load config file")
        return None

    if not config_path.exists():
        logger.debug(f"Config file does not exist: {config_path}")
        return None

    try:
        content = yaml.safe_load(config_path.read_text())
    except Exception as e:
        logger.warning(f"Failed to parse YAML config file: {e}")
        return None

    if not content:
        logger.warning("Config file is empty")
        return None

    try:
        return IntegrationCoworkerConfig.model_validate(content)
    except Exception as e:
        logger.warning(f"Config validation failed: {e}")
        return None


def save_config(config: IntegrationCoworkerConfig, config_path: Path) -> bool:
    """
    Save an IntegrationCoworkerConfig to a YAML file.

    Args:
        config: The config to save
        config_path: Path where to write the .integration-coworker.yaml

    Returns:
        True if saved successfully, False otherwise
    """
    try:
        import yaml
    except ImportError:
        logger.error("PyYAML not installed, cannot save config file")
        return False

    try:
        # Convert to dict, handling datetime serialization
        data = config.model_dump(mode="json", exclude_none=True)

        # Update metadata with timezone-aware UTC timestamp
        data["metadata"]["generated_at"] = datetime.now(timezone.utc).isoformat()

        # Generate YAML with a helpful header comment
        yaml_content = (
            "# .integration-coworker.yaml\n"
            "# Generated by Integration Coworker — feel free to edit\n"
            "# See ADR-0002 for schema documentation\n"
            "\n"
        )
        yaml_content += yaml.dump(
            data,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

        config_path.write_text(yaml_content)
        logger.info(f"Saved config to {config_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        return False


# =============================================================================
# Validation Functions
# =============================================================================


class ConfigValidationError:
    """A single validation error with details."""

    def __init__(self, field: str, message: str, severity: str = "error"):
        self.field = field
        self.message = message
        self.severity = severity  # "error" or "warning"

    def __repr__(self) -> str:
        return f"{self.severity.upper()}: {self.field} - {self.message}"


class ConfigValidationResult:
    """Result of config validation."""

    def __init__(self):
        self.errors: List[ConfigValidationError] = []
        self.warnings: List[ConfigValidationError] = []

    @property
    def is_valid(self) -> bool:
        """True if no errors (warnings are OK)."""
        return len(self.errors) == 0

    def add_error(self, field: str, message: str) -> None:
        """Add a validation error."""
        self.errors.append(ConfigValidationError(field, message, "error"))

    def add_warning(self, field: str, message: str) -> None:
        """Add a validation warning."""
        self.warnings.append(ConfigValidationError(field, message, "warning"))

    def __repr__(self) -> str:
        if self.is_valid and not self.warnings:
            return "ConfigValidationResult: VALID"
        parts = []
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warning(s)")
        return f"ConfigValidationResult: {', '.join(parts)}"


def validate_config(
    config: IntegrationCoworkerConfig,
    repo_root: Path,
    check_paths_exist: bool = True,
) -> ConfigValidationResult:
    """
    Validate a config against the actual repository structure.

    Args:
        config: The config to validate
        repo_root: Path to the repository root
        check_paths_exist: Whether to verify that specified paths exist

    Returns:
        ConfigValidationResult with any errors or warnings
    """
    result = ConfigValidationResult()

    # Validate layout paths
    if check_paths_exist:
        integrations_path = repo_root / config.layout.integrations_root
        tests_path = repo_root / config.layout.tests_root

        # For integrations_root, it's OK if it doesn't exist (we'll create it)
        # But warn if the parent doesn't exist
        integrations_parent = integrations_path.parent
        if not integrations_parent.exists():
            result.add_warning(
                "layout.integrations_root",
                f"Parent directory does not exist: {integrations_parent}",
            )

        tests_parent = tests_path.parent
        if not tests_parent.exists():
            result.add_warning(
                "layout.tests_root",
                f"Parent directory does not exist: {tests_parent}",
            )

        # Validate hooks paths if specified
        if config.hooks:
            if config.hooks.router_file:
                router_path = repo_root / config.hooks.router_file
                if not router_path.exists():
                    result.add_warning(
                        "hooks.router_file",
                        f"Router file does not exist: {router_path}",
                    )

            if config.hooks.settings_file:
                settings_path = repo_root / config.hooks.settings_file
                if not settings_path.exists():
                    result.add_warning(
                        "hooks.settings_file",
                        f"Settings file does not exist: {settings_path}",
                    )

            if config.hooks.module_file:
                module_path = repo_root / config.hooks.module_file
                if not module_path.exists():
                    result.add_warning(
                        "hooks.module_file",
                        f"Module file does not exist: {module_path}",
                    )

    # Validate convention patterns have required placeholders
    for pattern_name in ["client_module", "flow_module", "test_module"]:
        pattern = getattr(config.conventions, pattern_name)
        if "{provider}" not in pattern:
            result.add_error(
                f"conventions.{pattern_name}",
                f"Pattern must include {{provider}} placeholder: {pattern}",
            )

    # Validate test_module has {task} for flow tests
    if "{task}" not in config.conventions.test_module:
        result.add_warning(
            "conventions.test_module",
            "Pattern should include {task} placeholder for flow-specific tests",
        )

    return result


# =============================================================================
# Conversion Functions (RepoProfile <-> Config)
# =============================================================================


def config_to_profile(config: IntegrationCoworkerConfig) -> "RepoProfile":
    """
    Convert an IntegrationCoworkerConfig to a RepoProfile.

    This allows the rest of the system to use the unified RepoProfile interface.
    """
    from integration_coworker.repo.models import RepoProfile

    return RepoProfile(
        name=config.profile.name,
        framework=config.profile.framework,
        language=config.profile.language,
        integrations_root=config.layout.integrations_root,
        tests_root=config.layout.tests_root,
        conventions={
            "client_module_pattern": config.conventions.client_module,
            "flow_module_pattern": config.conventions.flow_module,
            "test_module_pattern": config.conventions.test_module,
        },
        layout_hints={
            "clients_dir": config.layout.clients_dir,
            "flows_dir": config.layout.flows_dir,
        }
        if config.layout.clients_dir or config.layout.flows_dir
        else None,
        integration_hooks={
            "router_file": config.hooks.router_file,
            "router_marker": config.hooks.router_marker,
            "settings_file": config.hooks.settings_file,
            "settings_marker": config.hooks.settings_marker,
            "module_file": config.hooks.module_file,
        }
        if config.hooks
        else None,
        detection_confidence=config.metadata.detection_confidence,
        profile_source=config.metadata.detection_method,
    )


def profile_to_config(
    profile: "RepoProfile",
    detection_method: Optional[str] = None,
) -> IntegrationCoworkerConfig:
    """
    Convert a RepoProfile to an IntegrationCoworkerConfig.

    This is used when saving a detected profile as a config file.
    """
    conventions = profile.conventions or {}
    layout_hints = profile.layout_hints or {}
    hooks = profile.integration_hooks or {}

    config = IntegrationCoworkerConfig(
        profile=ProfileConfig(
            name=profile.name,
            framework=profile.framework,
            language=profile.language,
        ),
        layout=LayoutConfig(
            integrations_root=profile.integrations_root,
            tests_root=profile.tests_root,
            clients_dir=layout_hints.get("clients_dir"),
            flows_dir=layout_hints.get("flows_dir") or layout_hints.get("workflows_dir"),
        ),
        conventions=ConventionsConfig(
            client_module=conventions.get(
                "client_module_pattern", f"clients/{{provider}}.{_ext(profile.language)}"
            ),
            flow_module=conventions.get(
                "flow_module_pattern",
                f"flows/{{provider}}_{{task}}.{_ext(profile.language)}",
            ),
            test_module=conventions.get(
                "test_module_pattern",
                f"test_{{provider}}_{{task}}.{_ext(profile.language)}",
            ),
        ),
        hooks=HooksConfig(
            router_file=hooks.get("router_file"),
            router_marker=hooks.get("router_registration_marker")
            or hooks.get("router_marker"),
            settings_file=hooks.get("settings_file"),
            settings_marker=hooks.get("settings_marker"),
            module_file=hooks.get("module_file"),
        )
        if hooks
        else None,
        metadata=MetadataConfig(
            detection_method=detection_method or profile.profile_source,
            detection_confidence=profile.detection_confidence,
        ),
    )

    return config


def _ext(language: str) -> str:
    """Get file extension for a language."""
    return "ts" if language in ("typescript", "javascript") else "py"
