"""Tests for MkDocs documentation structure.

These tests verify the documentation is properly structured
and all required files exist.
"""
import os
import sys
from pathlib import Path

import pytest


# Force SQLite for docs tests to avoid Postgres dependency
os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("USE_MOCK_LLM", "true")

# Get project root
PROJECT_ROOT = Path(__file__).parent.parent


class TestDocumentationStructure:
    """Test that documentation files exist and are properly structured."""

    def test_mkdocs_config_exists(self):
        """mkdocs.yml configuration file should exist."""
        config_path = PROJECT_ROOT / "mkdocs.yml"
        assert config_path.exists(), "mkdocs.yml not found"

    def test_docs_index_exists(self):
        """docs/index.md should exist."""
        index_path = PROJECT_ROOT / "docs" / "index.md"
        assert index_path.exists(), "docs/index.md not found"

    def test_getting_started_docs_exist(self):
        """Getting started docs should exist."""
        getting_started = PROJECT_ROOT / "docs" / "getting-started"
        required_files = [
            "installation.md",
            "quickstart.md",
            "configuration.md",
        ]
        for filename in required_files:
            filepath = getting_started / filename
            assert filepath.exists(), f"docs/getting-started/{filename} not found"

    def test_user_guide_docs_exist(self):
        """User guide docs should exist."""
        user_guide = PROJECT_ROOT / "docs" / "user-guide"
        required_files = [
            "cli-reference.md",
            "web-ui.md",
            "specs.md",
            "workflows.md",
        ]
        for filename in required_files:
            filepath = user_guide / filename
            assert filepath.exists(), f"docs/user-guide/{filename} not found"

    def test_api_reference_docs_exist(self):
        """API reference docs should exist."""
        api_ref = PROJECT_ROOT / "docs" / "api-reference"
        required_files = [
            "entrypoint.md",
            "types.md",
            "workflow-state.md",
        ]
        for filename in required_files:
            filepath = api_ref / filename
            assert filepath.exists(), f"docs/api-reference/{filename} not found"

    def test_features_docs_exist(self):
        """Feature docs should exist."""
        features = PROJECT_ROOT / "docs" / "features"
        required_files = [
            "knowledge-graph.md",
            "llm-cache.md",
            "parallel-execution.md",
            "recovery.md",
        ]
        for filename in required_files:
            filepath = features / filename
            assert filepath.exists(), f"docs/features/{filename} not found"

    def test_development_docs_exist(self):
        """Development docs should exist."""
        development = PROJECT_ROOT / "docs" / "development"
        required_files = [
            "architecture.md",
            "code-tour.md",
            "contributing.md",
            "testing.md",
            "changelog.md",
        ]
        for filename in required_files:
            filepath = development / filename
            assert filepath.exists(), f"docs/development/{filename} not found"

    def test_stylesheets_exist(self):
        """Custom stylesheets should exist."""
        css_path = PROJECT_ROOT / "docs" / "stylesheets" / "extra.css"
        assert css_path.exists(), "docs/stylesheets/extra.css not found"

    def test_cli_generator_exists(self):
        """CLI reference generator script should exist."""
        gen_path = PROJECT_ROOT / "docs" / "gen_cli_reference.py"
        assert gen_path.exists(), "docs/gen_cli_reference.py not found"


class TestDocumentationContent:
    """Test that documentation content is valid."""

    def test_index_has_required_sections(self):
        """docs/index.md should have required sections."""
        index_path = PROJECT_ROOT / "docs" / "index.md"
        content = index_path.read_text()
        
        required_sections = [
            "Integration Co-Worker",
            "Features",
            "Quick Start",
        ]
        for section in required_sections:
            assert section in content, f"Missing section: {section}"

    def test_mkdocs_config_valid_yaml(self):
        """mkdocs.yml should be valid YAML."""
        import yaml
        
        config_path = PROJECT_ROOT / "mkdocs.yml"
        content = config_path.read_text()
        
        # Should not raise
        config = yaml.safe_load(content)
        assert config is not None
        assert "site_name" in config
        assert "theme" in config
        assert "nav" in config

    def test_all_nav_files_exist(self):
        """All files referenced in mkdocs.yml nav should exist."""
        import yaml
        
        config_path = PROJECT_ROOT / "mkdocs.yml"
        config = yaml.safe_load(config_path.read_text())
        
        def extract_files(nav_item):
            """Recursively extract file paths from nav structure."""
            files = []
            if isinstance(nav_item, str):
                files.append(nav_item)
            elif isinstance(nav_item, dict):
                for value in nav_item.values():
                    files.extend(extract_files(value))
            elif isinstance(nav_item, list):
                for item in nav_item:
                    files.extend(extract_files(item))
            return files
        
        nav_files = extract_files(config.get("nav", []))
        docs_dir = PROJECT_ROOT / "docs"
        
        for filepath in nav_files:
            full_path = docs_dir / filepath
            assert full_path.exists(), f"Nav references missing file: {filepath}"

    def test_ops_runbook_keeps_getting_started_in_nav(self):
        """GETTING_STARTED.md must remain visible under the 'Ops Runbook' nav section."""
        import yaml

        config_path = PROJECT_ROOT / "mkdocs.yml"
        config = yaml.safe_load(config_path.read_text())

        nav = config.get("nav", [])

        # Find the top-level 'Ops Runbook' entry.
        ops_runbook = None
        for item in nav:
            if isinstance(item, dict) and "Ops Runbook" in item:
                ops_runbook = item["Ops Runbook"]
                break

        assert ops_runbook is not None, "mkdocs nav is missing top-level 'Ops Runbook' section"

        # 'Ops Runbook' is a list of {Title: path} entries.
        assert any(
            isinstance(entry, dict) and ("Getting Started (Ops)" in entry) and entry["Getting Started (Ops)"] == "GETTING_STARTED.md"
            for entry in ops_runbook
        ), "Ops Runbook nav must include 'Getting Started (Ops): GETTING_STARTED.md'"

    def test_ops_runbook_getting_started_is_not_redirected(self):
        """GETTING_STARTED.md must not be redirected away via mkdocs-redirects redirect_maps."""
        import yaml

        config = yaml.safe_load((PROJECT_ROOT / "mkdocs.yml").read_text())

        redirect_maps = {}
        for plugin in config.get("plugins", []):
            if isinstance(plugin, dict) and "redirects" in plugin:
                redirects_cfg = plugin.get("redirects") or {}
                redirect_maps = redirects_cfg.get("redirect_maps") or {}
                break

        assert "GETTING_STARTED.md" not in redirect_maps, "GETTING_STARTED.md must not be a redirect source"
        assert "GETTING_STARTED.md" not in set(redirect_maps.values()), "GETTING_STARTED.md must not be a redirect destination"

    def test_mermaid_fence_format_resolved_by_plugin(self, monkeypatch):
        """Plugin should convert mermaid fence format string to callable."""
        import yaml
        import pytest

        pytest.importorskip("mkdocs")

        plugin_dir = PROJECT_ROOT / "docs" / "plugins"
        monkeypatch.setenv("USE_SQLITE", "true")
        monkeypatch.setenv("USE_MOCK_LLM", "true")
        monkeypatch.delenv("DATABASE_URL", raising=False)

        from integration_coworker.config import reset_settings

        reset_settings()
        monkeypatch.syspath_prepend(str(plugin_dir))

        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "mermaid_fence_resolver", plugin_dir / "mermaid_fence_resolver.py"
        )
        assert spec and spec.loader, "Failed to load mermaid_fence_resolver plugin module"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        config = yaml.safe_load((PROJECT_ROOT / "mkdocs.yml").read_text())
        plugin = module.MermaidFenceResolverPlugin()
        updated = plugin.on_config(config)

        format_value = None
        for ext in updated.get("markdown_extensions", []):
            if not isinstance(ext, dict):
                continue
            superfences = ext.get("pymdownx.superfences")
            if not isinstance(superfences, dict):
                continue
            for fence in superfences.get("custom_fences", []):
                if fence.get("name") == "mermaid":
                    format_value = fence.get("format")
                    break
        assert callable(format_value), "Mermaid fence format was not resolved to callable"


class TestGitHubActions:
    """Test GitHub Actions workflow for docs."""

    def test_docs_workflow_exists(self):
        """docs.yml workflow should exist."""
        workflow_path = PROJECT_ROOT / ".github" / "workflows" / "docs.yml"
        assert workflow_path.exists(), ".github/workflows/docs.yml not found"

    def test_docs_workflow_valid_yaml(self):
        """docs.yml should be valid YAML."""
        import yaml
        
        workflow_path = PROJECT_ROOT / ".github" / "workflows" / "docs.yml"
        content = workflow_path.read_text()
        
        # Should not raise
        workflow = yaml.safe_load(content)
        assert workflow is not None
        assert "jobs" in workflow
        assert "deploy" in workflow["jobs"]
