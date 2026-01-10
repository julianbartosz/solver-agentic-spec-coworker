"""
Unit tests for local filesystem spec discovery.
"""
import tempfile
from pathlib import Path

import pytest
import yaml

from integration_coworker.discovery.local_filesystem import (
    search_local_filesystem,
    _is_likely_openapi_content,
    _derive_provider_from_path,
)


class TestIsLikelyOpenAPIContent:
    """Test detection of OpenAPI/Swagger/AsyncAPI specs."""
    
    def test_openapi_3_0_detected(self):
        content = {"openapi": "3.0.0", "info": {"title": "Test API", "version": "1.0"}, "paths": {}}
        is_valid, spec_format, title = _is_likely_openapi_content(content)
        assert is_valid
        assert spec_format == "openapi-3.0.0"
        assert title == "Test API"
    
    def test_openapi_3_1_detected(self):
        content = {"openapi": "3.1.0", "info": {"title": "Test API v2", "version": "2.0"}, "paths": {}}
        is_valid, spec_format, title = _is_likely_openapi_content(content)
        assert is_valid
        assert spec_format == "openapi-3.1.0"
        assert title == "Test API v2"
    
    def test_swagger_2_0_detected(self):
        content = {"swagger": "2.0", "info": {"title": "Swagger API", "version": "1.0"}, "paths": {}}
        is_valid, spec_format, title = _is_likely_openapi_content(content)
        assert is_valid
        assert spec_format == "swagger-2.0"
        assert title == "Swagger API"
    
    def test_asyncapi_detected(self):
        content = {"asyncapi": "2.0.0", "info": {"title": "Async API", "version": "1.0"}}
        is_valid, spec_format, title = _is_likely_openapi_content(content)
        assert is_valid
        assert spec_format == "asyncapi-2.0.0"
        assert title == "Async API"
    
    def test_non_spec_not_detected(self):
        content = {"name": "test", "data": [1, 2, 3]}
        is_valid, spec_format, title = _is_likely_openapi_content(content)
        assert not is_valid
        assert spec_format == ""
        assert title == ""


class TestDeriveProviderFromPath:
    """Test provider name derivation from file path."""
    
    def test_extracts_from_filename(self):
        path = Path("/repo/openai_api.yaml")
        provider = _derive_provider_from_path(path, "OpenAI API")
        assert provider == "openai"
    
    def test_strips_api_suffix(self):
        path = Path("/repo/stripe_api.yaml")
        provider = _derive_provider_from_path(path, "Stripe API")
        assert provider == "stripe"
    
    def test_strips_spec_suffix(self):
        path = Path("/repo/github-spec.yaml")
        provider = _derive_provider_from_path(path, "GitHub API")
        assert provider == "github"
    
    def test_uses_api_name_fallback(self):
        path = Path("/repo/api.yaml")
        provider = _derive_provider_from_path(path, "Custom Service")
        assert provider == "custom"
    
    def test_returns_local_for_generic(self):
        path = Path("/repo/spec.yaml")
        provider = _derive_provider_from_path(path, "")
        assert provider == "local"


class TestSearchLocalFilesystem:
    """Integration tests for local filesystem search."""
    
    def test_finds_openapi_spec_in_root(self, tmp_path: Path):
        # Create a valid OpenAPI spec
        spec_content = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0"},
            "paths": {},
        }
        spec_file = tmp_path / "openapi.yaml"
        spec_file.write_text(yaml.dump(spec_content))
        
        # Search
        candidates = search_local_filesystem(tmp_path)
        
        assert len(candidates) == 1
        assert candidates[0].api_name == "Test API"
        assert candidates[0].spec_url.startswith("file://")
        assert "openapi.yaml" in candidates[0].spec_url
    
    def test_finds_spec_in_specs_directory(self, tmp_path: Path):
        # Create specs directory
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        
        spec_content = {
            "openapi": "3.0.0",
            "info": {"title": "Nested API", "version": "1.0"},
            "paths": {},
        }
        spec_file = specs_dir / "api.yaml"
        spec_file.write_text(yaml.dump(spec_content))
        
        # Search
        candidates = search_local_filesystem(tmp_path)
        
        assert len(candidates) == 1
        assert candidates[0].api_name == "Nested API"
    
    def test_ignores_non_spec_yaml(self, tmp_path: Path):
        # Create a non-spec YAML file
        config = {"name": "config", "value": 123}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config))
        
        # Search
        candidates = search_local_filesystem(tmp_path)
        
        assert len(candidates) == 0
    
    def test_ignores_excluded_directories(self, tmp_path: Path):
        # Create spec in node_modules (should be ignored)
        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        
        spec_content = {
            "openapi": "3.0.0",
            "info": {"title": "Hidden API", "version": "1.0"},
            "paths": {},
        }
        spec_file = node_modules / "openapi.yaml"
        spec_file.write_text(yaml.dump(spec_content))
        
        # Search
        candidates = search_local_filesystem(tmp_path)
        
        assert len(candidates) == 0
    
    def test_scores_match_by_provider_hint(self, tmp_path: Path):
        # Create two specs
        spec1 = {
            "openapi": "3.0.0",
            "info": {"title": "Stripe API", "version": "1.0"},
            "paths": {},
        }
        spec2 = {
            "openapi": "3.0.0",
            "info": {"title": "GitHub API", "version": "1.0"},
            "paths": {},
        }
        (tmp_path / "stripe_api.yaml").write_text(yaml.dump(spec1))
        (tmp_path / "github_api.yaml").write_text(yaml.dump(spec2))
        
        # Search with provider hint for stripe
        candidates = search_local_filesystem(
            tmp_path,
            provider_hint="stripe",
        )
        
        # Stripe should be first (higher score)
        assert len(candidates) == 2
        assert candidates[0].api_name == "Stripe API"
    
    def test_returns_empty_for_nonexistent_directory(self):
        candidates = search_local_filesystem(Path("/nonexistent/path"))
        assert len(candidates) == 0
    
    def test_handles_json_specs(self, tmp_path: Path):
        import json
        spec_content = {
            "openapi": "3.0.0",
            "info": {"title": "JSON API", "version": "1.0"},
            "paths": {},
        }
        spec_file = tmp_path / "api.json"
        spec_file.write_text(json.dumps(spec_content))
        
        candidates = search_local_filesystem(tmp_path)
        
        assert len(candidates) == 1
        assert candidates[0].api_name == "JSON API"
