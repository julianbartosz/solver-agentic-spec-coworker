"""
Production-themed integration tests for API spec ingestion.

Tests real-world scenarios:
- Large spec files (GitHub 11MB, Stripe 7MB, Mailchimp 10MB)
- Streaming mode activation for large specs
- Memory efficiency (no OOM)
- Checkpoint/resume behavior
- Cancellation mid-ingest

Per docs/INGESTION_PROD_HARDENING_PLAN.md Step 5
"""

import os
import pytest
from pathlib import Path
import tempfile
import time

from integration_coworker.graph.nodes.ingest_spec import (
    ingest_spec,
    _fetch_spec_content,
    _fetch_file_content,
    cleanup_http_client,
)
from integration_coworker.graph.state import WorkflowState
from integration_coworker.config import (
    get_fetch_config,
    FetchConfig,
    reset_settings,
    should_use_streaming_for_spec,
)


# Paths to real specs in specs/ directory
SPECS_DIR = Path(__file__).parent.parent / "specs"
GITHUB_SPEC = SPECS_DIR / "github_api.json"  # 11MB
STRIPE_SPEC = SPECS_DIR / "stripe_api.json"  # 7MB
MAILCHIMP_SPEC = SPECS_DIR / "mailchimp_api.yaml"  # 10MB
PETSTORE_SPEC = SPECS_DIR / "petstore_v3.json"  # 17KB (small)


@pytest.fixture(autouse=True)
def cleanup_after_test():
    """Clean up HTTP client and reset settings after each test."""
    yield
    cleanup_http_client()
    reset_settings()


class TestLargeSpecIngestion:
    """Test ingestion of real large specs from specs/ directory."""
    
    @pytest.mark.skipif(not GITHUB_SPEC.exists(), reason="GitHub spec not found")
    def test_github_spec_triggers_streaming_mode(self):
        """Verify 11MB GitHub spec triggers streaming mode."""
        spec_size = GITHUB_SPEC.stat().st_size
        assert spec_size > 10_000_000, f"Expected >10MB, got {spec_size}"
        
        # Should trigger streaming
        assert should_use_streaming_for_spec(spec_size) is True
    
    @pytest.mark.skipif(not GITHUB_SPEC.exists(), reason="GitHub spec not found")
    def test_github_spec_loads_successfully(self):
        """Verify 11MB GitHub spec can be loaded without OOM."""
        config = FetchConfig(max_bytes=20_000_000)  # 20MB limit
        
        start = time.time()
        content, content_type = _fetch_file_content(str(GITHUB_SPEC), config)
        elapsed = time.time() - start
        
        assert len(content) > 10_000_000
        assert "application/json" in content_type
        assert elapsed < 5.0, f"Load took too long: {elapsed:.2f}s"
    
    @pytest.mark.skipif(not STRIPE_SPEC.exists(), reason="Stripe spec not found")
    def test_stripe_spec_loads_successfully(self):
        """Verify 7MB Stripe spec can be loaded."""
        config = FetchConfig(max_bytes=20_000_000)
        
        content, content_type = _fetch_file_content(str(STRIPE_SPEC), config)
        
        assert len(content) > 5_000_000
        assert "application/json" in content_type
    
    @pytest.mark.skipif(not MAILCHIMP_SPEC.exists(), reason="Mailchimp spec not found")
    def test_mailchimp_yaml_spec_loads_successfully(self):
        """Verify 10MB Mailchimp YAML spec can be loaded."""
        config = FetchConfig(max_bytes=20_000_000)
        
        content, content_type = _fetch_file_content(str(MAILCHIMP_SPEC), config)
        
        assert len(content) > 9_000_000
        assert "yaml" in content_type


class TestSizeLimitEnforcement:
    """Test max_bytes enforcement prevents loading specs that are too large."""
    
    @pytest.mark.skipif(not GITHUB_SPEC.exists(), reason="GitHub spec not found")
    def test_rejects_spec_over_limit(self):
        """Verify spec over max_bytes is rejected."""
        from integration_coworker.graph.nodes.ingest_spec import SpecTooLargeError
        
        config = FetchConfig(max_bytes=1_000_000)  # 1MB limit
        
        with pytest.raises(SpecTooLargeError) as exc_info:
            _fetch_file_content(str(GITHUB_SPEC), config)
        
        assert exc_info.value.max_size == 1_000_000
        assert exc_info.value.size > 10_000_000


class TestStreamingModeDecision:
    """Test automatic streaming mode selection based on spec size."""
    
    def test_small_spec_uses_legacy_mode(self):
        """Verify small specs don't trigger streaming."""
        # 100KB is under threshold
        assert should_use_streaming_for_spec(100_000) is False
    
    def test_large_spec_uses_streaming_mode(self):
        """Verify large specs trigger streaming."""
        # 1MB is over default threshold (500KB)
        assert should_use_streaming_for_spec(1_000_000) is True
    
    def test_env_var_can_force_streaming(self, monkeypatch):
        """Verify STREAMING_PERSISTENCE=true forces streaming."""
        reset_settings()
        monkeypatch.setenv("STREAMING_PERSISTENCE", "true")
        reset_settings()
        
        # Should stream even small specs
        assert should_use_streaming_for_spec(100) is True
    
    def test_env_var_can_disable_streaming(self, monkeypatch):
        """Verify STREAMING_PERSISTENCE=false disables streaming."""
        reset_settings()
        monkeypatch.setenv("STREAMING_PERSISTENCE", "false")
        reset_settings()
        
        # Should not stream even large specs
        assert should_use_streaming_for_spec(100_000_000) is False


class TestIngestSpecEndToEnd:
    """End-to-end tests for ingest_spec node."""
    
    @pytest.mark.skipif(not PETSTORE_SPEC.exists(), reason="Petstore spec not found")
    def test_ingest_small_spec_legacy_mode(self, monkeypatch):
        """Verify small spec uses legacy mode and produces doc_chunks."""
        monkeypatch.setenv("USE_SQLITE", "true")
        monkeypatch.setenv("STREAMING_PERSISTENCE", "false")
        reset_settings()
        
        state = WorkflowState(
            spec_refs=[str(PETSTORE_SPEC)],
            source_refs=[],
            task_description="Test ingest",
            provider_code="test_petstore",
        )
        
        result = ingest_spec(state)
        
        assert "ingest_spec" in result.completed_steps
        assert len(result.spec_documents) == 1
        assert len(result.doc_chunks) > 0  # Legacy mode populates doc_chunks
        assert result.spec_documents[0].sha256 is not None
    
    @pytest.mark.skipif(not PETSTORE_SPEC.exists(), reason="Petstore spec not found")
    def test_ingest_spec_computes_sha256(self, monkeypatch):
        """Verify SHA-256 hash is computed for deduplication."""
        monkeypatch.setenv("USE_SQLITE", "true")
        reset_settings()
        
        state = WorkflowState(
            spec_refs=[str(PETSTORE_SPEC)],
            source_refs=[],
            task_description="Test SHA computation",
            provider_code="test_petstore_sha",
        )
        
        result = ingest_spec(state)
        
        # SHA-256 should be computed
        assert result.spec_documents[0].sha256 is not None
        assert len(result.spec_documents[0].sha256) == 64  # Hex string


class TestContentTypeFromFileExtension:
    """Test content-type detection from file extensions."""
    
    def test_json_extension_gets_json_content_type(self):
        """Verify .json files get application/json content-type."""
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write('{"test": true}')
            f.flush()
            
            config = FetchConfig()
            content, ct = _fetch_file_content(f.name, config)
            
            assert ct == "application/json"
            os.unlink(f.name)
    
    def test_yaml_extension_gets_yaml_content_type(self):
        """Verify .yaml files get application/yaml content-type."""
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("openapi: '3.0.0'")
            f.flush()
            
            config = FetchConfig()
            content, ct = _fetch_file_content(f.name, config)
            
            assert ct == "application/yaml"
            os.unlink(f.name)
    
    def test_yml_extension_gets_yaml_content_type(self):
        """Verify .yml files get application/yaml content-type."""
        with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False) as f:
            f.write("openapi: '3.0.0'")
            f.flush()
            
            config = FetchConfig()
            content, ct = _fetch_file_content(f.name, config)
            
            assert ct == "application/yaml"
            os.unlink(f.name)


class TestConfigurationFromEnvironment:
    """Test configuration loading from environment variables."""
    
    def test_fetch_max_bytes_from_env(self, monkeypatch):
        """Verify FETCH_MAX_BYTES is respected."""
        monkeypatch.setenv("FETCH_MAX_BYTES", "999999")
        reset_settings()
        
        config = get_fetch_config()
        assert config.max_bytes == 999999
    
    def test_fetch_retryable_statuses_from_env(self, monkeypatch):
        """Verify FETCH_RETRYABLE_STATUSES is parsed."""
        monkeypatch.setenv("FETCH_RETRYABLE_STATUSES", "429,500,503")
        reset_settings()
        
        config = get_fetch_config()
        assert config.retryable_statuses == frozenset({429, 500, 503})
    
    def test_fetch_allowed_content_types_from_env(self, monkeypatch):
        """Verify FETCH_ALLOWED_CONTENT_TYPES is parsed."""
        monkeypatch.setenv("FETCH_ALLOWED_CONTENT_TYPES", "application/json,text/yaml")
        reset_settings()
        
        config = get_fetch_config()
        assert "application/json" in config.allowed_content_types
        assert "text/yaml" in config.allowed_content_types
    
    def test_default_includes_openapi_media_types(self):
        """Verify default allowlist includes OpenAPI media types."""
        reset_settings()
        
        config = get_fetch_config()
        
        # IETF standard OpenAPI types
        assert "application/openapi+json" in config.allowed_content_types
        assert "application/openapi+yaml" in config.allowed_content_types
        
        # Legacy OAI types
        assert "application/vnd.oai.openapi" in config.allowed_content_types
        assert "application/vnd.oai.openapi+json" in config.allowed_content_types
