"""
Unit tests for discovery/validator.py - Spec URL Validation

Tests validation logic using small fixture specs.
Live HTTP tests are guarded with @pytest.mark.integration.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from contextlib import asynccontextmanager

from integration_coworker.discovery.validator import (
    ValidationResult,
    SpecFormat,
    _detect_content_type,
    _parse_spec_content,
    _detect_spec_format,
    _quick_structure_check,
    validate_spec_url,
    validate_url_security,
    validate_url_security_with_dns,
    _is_ip_blocked,
    _is_hostname_blocked,
    _resolve_and_validate_hostname,
)


# Fixture specs for testing
VALID_OPENAPI_3_SPEC = """
openapi: "3.0.3"
info:
  title: Test API
  version: "1.0.0"
paths:
  /test:
    get:
      summary: Test endpoint
      responses:
        "200":
          description: OK
"""

VALID_OPENAPI_31_SPEC = """
openapi: "3.1.0"
info:
  title: Test API
  version: "1.0.0"
paths:
  /test:
    get:
      summary: Test endpoint
      responses:
        "200":
          description: OK
"""

VALID_SWAGGER_2_SPEC = """
swagger: "2.0"
info:
  title: Test API
  version: "1.0.0"
paths:
  /test:
    get:
      summary: Test endpoint
      responses:
        200:
          description: OK
"""

INVALID_SPEC_NO_VERSION = """
info:
  title: Test API
  version: "1.0.0"
paths:
  /test:
    get:
      summary: Test endpoint
"""

INVALID_SPEC_NO_PATHS = """
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0.0"
"""

INVALID_SPEC_NO_INFO = """
openapi: "3.0.0"
paths:
  /test:
    get:
      summary: Test endpoint
"""


class TestDetectContentType:
    """Tests for content type detection."""
    
    @pytest.mark.parametrize("content,content_type,expected", [
        ('{"openapi": "3.0.0"}', "application/json", "json"),
        ('openapi: "3.0.0"', "application/yaml", "yaml"),
        ('{"openapi": "3.0.0"}', None, "json"),  # Heuristic
        ('openapi: "3.0.0"', None, "yaml"),  # Heuristic
        ('[{"a": 1}]', None, "json"),  # Array
        ('  {  "x": 1 }', None, "json"),  # Whitespace
    ])
    def test_detect_content_type(self, content, content_type, expected):
        """Test content type detection."""
        result = _detect_content_type(content, content_type)
        assert result == expected


class TestParseSpecContent:
    """Tests for spec content parsing."""
    
    def test_parse_json(self):
        """Test parsing JSON content."""
        content = '{"openapi": "3.0.0", "info": {"title": "Test"}}'
        spec, fmt = _parse_spec_content(content, "application/json")
        
        assert fmt == "json"
        assert spec["openapi"] == "3.0.0"
    
    def test_parse_yaml(self):
        """Test parsing YAML content."""
        content = 'openapi: "3.0.0"\ninfo:\n  title: Test'
        spec, fmt = _parse_spec_content(content, "application/yaml")
        
        assert fmt == "yaml"
        assert spec["openapi"] == "3.0.0"
    
    def test_parse_yaml_without_content_type(self):
        """Test parsing YAML when no content-type is provided."""
        content = 'openapi: "3.0.0"\ninfo:\n  title: Test'
        spec, fmt = _parse_spec_content(content, None)
        
        assert spec["openapi"] == "3.0.0"


class TestDetectSpecFormat:
    """Tests for spec format detection."""
    
    def test_detect_openapi_30(self):
        """Test detecting OpenAPI 3.0."""
        spec = {"openapi": "3.0.3", "info": {}, "paths": {}}
        assert _detect_spec_format(spec) == SpecFormat.OPENAPI_3_0
    
    def test_detect_openapi_31(self):
        """Test detecting OpenAPI 3.1."""
        spec = {"openapi": "3.1.0", "info": {}, "paths": {}}
        assert _detect_spec_format(spec) == SpecFormat.OPENAPI_3_1
    
    def test_detect_swagger_2(self):
        """Test detecting Swagger 2.0."""
        spec = {"swagger": "2.0", "info": {}, "paths": {}}
        assert _detect_spec_format(spec) == SpecFormat.SWAGGER_2
    
    def test_detect_unknown(self):
        """Test detecting unknown format."""
        spec = {"info": {}, "paths": {}}
        assert _detect_spec_format(spec) == SpecFormat.UNKNOWN


class TestQuickStructureCheck:
    """Tests for quick structural validation."""
    
    def test_valid_openapi_3(self):
        """Test valid OpenAPI 3.0 spec passes."""
        import yaml
        spec = yaml.safe_load(VALID_OPENAPI_3_SPEC)
        valid, error = _quick_structure_check(spec)
        assert valid is True
        assert error is None
    
    def test_valid_swagger_2(self):
        """Test valid Swagger 2.0 spec passes."""
        import yaml
        spec = yaml.safe_load(VALID_SWAGGER_2_SPEC)
        valid, error = _quick_structure_check(spec)
        assert valid is True
        assert error is None
    
    def test_missing_version(self):
        """Test spec without version field fails."""
        import yaml
        spec = yaml.safe_load(INVALID_SPEC_NO_VERSION)
        valid, error = _quick_structure_check(spec)
        assert valid is False
        assert "version" in error.lower() or "openapi" in error.lower() or "swagger" in error.lower()
    
    def test_missing_paths(self):
        """Test spec without paths fails."""
        import yaml
        spec = yaml.safe_load(INVALID_SPEC_NO_PATHS)
        valid, error = _quick_structure_check(spec)
        assert valid is False
        assert "paths" in error.lower()
    
    def test_missing_info(self):
        """Test spec without info fails."""
        import yaml
        spec = yaml.safe_load(INVALID_SPEC_NO_INFO)
        valid, error = _quick_structure_check(spec)
        assert valid is False
        assert "info" in error.lower()


class TestValidationResult:
    """Tests for ValidationResult dataclass."""
    
    def test_valid_result(self):
        """Test creating a valid result."""
        result = ValidationResult(
            valid=True,
            spec_url="https://example.com/spec.yaml",
            spec_format=SpecFormat.OPENAPI_3_0,
            title="Test API",
            version="1.0.0",
        )
        
        assert result.valid is True
        assert result.spec_format == SpecFormat.OPENAPI_3_0
        assert result.error is None
    
    def test_invalid_result(self):
        """Test creating an invalid result."""
        result = ValidationResult(
            valid=False,
            spec_url="https://example.com/spec.yaml",
            error="Missing paths",
        )
        
        assert result.valid is False
        assert result.error == "Missing paths"
    
    def test_to_dict(self):
        """Test serialization to dict."""
        result = ValidationResult(
            valid=True,
            spec_url="https://example.com/spec.yaml",
            spec_format=SpecFormat.OPENAPI_3_0,
            title="Test API",
            version="1.0.0",
            warnings=["Warning 1"],
        )
        
        d = result.to_dict()
        assert d["valid"] is True
        assert d["spec_format"] == "openapi_3.0"
        assert d["warnings"] == ["Warning 1"]


# =============================================================================
# SSRF Protection Tests
# =============================================================================

class TestSSRFProtection:
    """Tests for SSRF protection - blocking private IPs and metadata endpoints."""
    
    @pytest.mark.parametrize("ip,expected_blocked", [
        ("127.0.0.1", True),      # Loopback
        ("192.168.1.1", True),    # Private
        ("10.0.0.1", True),       # Private
        ("172.16.0.1", True),     # Private
        ("169.254.169.254", True),  # Link-local (AWS metadata)
        ("8.8.8.8", False),       # Public (Google DNS)
        ("1.1.1.1", False),       # Public (Cloudflare DNS)
        ("::1", True),            # IPv6 loopback
        ("fc00::1", True),        # IPv6 private
    ])
    def test_ip_blocking(self, ip, expected_blocked):
        """Test that private IPs are blocked."""
        assert _is_ip_blocked(ip) == expected_blocked
    
    @pytest.mark.parametrize("hostname,expected_blocked", [
        ("localhost", True),
        ("localhost.localdomain", True),
        ("169.254.169.254", True),  # AWS/GCP/Azure metadata
        ("metadata.google.internal", True),
        ("100.100.100.200", True),  # Alibaba metadata
        ("api.stripe.com", False),
        ("example.com", False),
    ])
    def test_hostname_blocking(self, hostname, expected_blocked):
        """Test that metadata hostnames are blocked."""
        assert _is_hostname_blocked(hostname) == expected_blocked
    
    @pytest.mark.parametrize("url,expected_safe,error_contains", [
        ("https://api.stripe.com/v1/openapi", True, None),
        ("https://petstore.swagger.io/v2/swagger.json", True, None),
        ("http://example.com/spec.yaml", False, "scheme"),  # HTTP blocked by default
        ("https://localhost/spec.yaml", False, "Blocked hostname"),
        ("https://127.0.0.1/spec.yaml", False, "Private"),
        ("https://192.168.1.1/spec.yaml", False, "Private"),
        ("https://169.254.169.254/latest/meta-data/", False, "Blocked"),
        ("ftp://example.com/spec.yaml", False, "scheme"),
        ("file:///etc/passwd", False, "scheme"),
    ])
    def test_url_security_validation(self, url, expected_safe, error_contains):
        """Test URL security validation."""
        is_safe, error = validate_url_security(url)
        assert is_safe == expected_safe
        if error_contains:
            assert error_contains.lower() in error.lower()
    
    def test_http_allowed_when_flag_set(self):
        """Test that HTTP is allowed when allow_http=True."""
        is_safe, error = validate_url_security("http://example.com/spec.yaml", allow_http=True)
        assert is_safe is True
        assert error is None


class TestDNSResolutionSecurity:
    """Tests for DNS resolution-based SSRF protection."""
    
    def test_resolve_public_hostname(self):
        """Public hostnames should resolve successfully."""
        # Use a well-known public hostname
        is_safe, error, resolved_ips = _resolve_and_validate_hostname("google.com")
        assert is_safe is True
        assert error is None
        assert len(resolved_ips) > 0
    
    def test_resolve_blocked_hostname(self):
        """Hostnames resolving to private IPs should be blocked."""
        # localhost should resolve to 127.0.0.1
        is_safe, error, resolved_ips = _resolve_and_validate_hostname("localhost")
        assert is_safe is False
        assert "blocked" in error.lower() or "127.0.0.1" in error
    
    def test_resolve_nonexistent_hostname(self):
        """Non-existent hostnames should fail gracefully."""
        is_safe, error, resolved_ips = _resolve_and_validate_hostname("this-hostname-definitely-does-not-exist-12345.com")
        assert is_safe is False
        assert "resolution" in error.lower() or "resolve" in error.lower()
    
    def test_validate_url_with_dns_public(self):
        """Public URLs should pass DNS validation."""
        # Use a well-known safe URL
        is_safe, error, resolved_ips = validate_url_security_with_dns("https://api.github.com/")
        assert is_safe is True
        assert error is None
        assert len(resolved_ips) > 0
    
    def test_validate_url_with_dns_localhost(self):
        """Localhost should be blocked even with DNS resolution."""
        is_safe, error, resolved_ips = validate_url_security_with_dns("https://localhost/spec.yaml")
        # Should fail on hostname blocklist before DNS resolution
        assert is_safe is False
        assert "blocked" in error.lower() or "localhost" in error.lower()
    
    def test_validate_url_with_dns_ip_literal(self):
        """IP literals should bypass DNS resolution but still validate."""
        # Public IP should pass
        is_safe, error, resolved_ips = validate_url_security_with_dns("https://8.8.8.8/spec.yaml")
        assert is_safe is True
        
        # Private IP should fail
        is_safe, error, resolved_ips = validate_url_security_with_dns("https://192.168.1.1/spec.yaml")
        assert is_safe is False


@pytest.mark.asyncio
class TestValidateSpecUrlSSRF:
    """Tests for SSRF protection in validate_spec_url."""
    
    async def test_blocks_localhost(self):
        """Validation rejects localhost URLs without making HTTP call."""
        result = await validate_spec_url("https://localhost/spec.yaml")
        assert result.valid is False
        assert "security" in result.error.lower() or "blocked" in result.error.lower()
    
    async def test_blocks_private_ip(self):
        """Validation rejects private IP URLs without making HTTP call."""
        result = await validate_spec_url("https://192.168.1.1/spec.yaml")
        assert result.valid is False
        assert "security" in result.error.lower() or "private" in result.error.lower()
    
    async def test_blocks_metadata_endpoint(self):
        """Validation rejects cloud metadata URLs."""
        result = await validate_spec_url("https://169.254.169.254/latest/meta-data/")
        assert result.valid is False
        assert "security" in result.error.lower() or "blocked" in result.error.lower()
    
    async def test_blocks_http_by_default(self):
        """Validation rejects http:// URLs by default."""
        result = await validate_spec_url("http://api.example.com/spec.yaml")
        assert result.valid is False
        assert "scheme" in result.error.lower() or "https" in result.error.lower()


# =============================================================================
# HTTP Validation Tests (Mocked)
# =============================================================================

def _create_mock_streaming_response(content: str, status_code: int = 200, content_type: str = "application/yaml"):
    """Helper to create a mock streaming response for httpx."""
    class MockStreamResponse:
        def __init__(self):
            self.status_code = status_code
            self.headers = {"content-type": content_type}
            self.url = "https://example.com/spec.yaml"
            
        def raise_for_status(self):
            if self.status_code >= 400:
                import httpx
                raise httpx.HTTPStatusError("Error", request=MagicMock(), response=self)
        
        async def aiter_bytes(self):
            yield content.encode("utf-8")
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
    
    return MockStreamResponse()


@pytest.mark.asyncio
class TestValidateSpecUrlMocked:
    """Tests for validate_spec_url with mocked HTTP via hardened_fetch."""
    
    async def test_valid_spec(self):
        """Test validating a valid spec."""
        from integration_coworker.discovery.http_client import FetchResult
        
        mock_result = FetchResult(
            success=True,
            content=VALID_OPENAPI_3_SPEC,
            final_url="https://example.com/spec.yaml",
            content_type="application/yaml",
            redirect_chain=["https://example.com/spec.yaml"],
        )
        
        with patch("integration_coworker.discovery.http_client.hardened_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_result
            
            result = await validate_spec_url("https://example.com/spec.yaml")
        
        assert result.valid is True
        assert result.title == "Test API"
        assert result.spec_format == SpecFormat.OPENAPI_3_0
    
    async def test_http_error(self):
        """Test handling HTTP errors."""
        from integration_coworker.discovery.http_client import FetchResult
        
        mock_result = FetchResult(
            success=False,
            error="HTTP error: 404",
            redirect_chain=["https://example.com/notfound.yaml"],
        )
        
        with patch("integration_coworker.discovery.http_client.hardened_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_result
            
            result = await validate_spec_url("https://example.com/notfound.yaml")
        
        assert result.valid is False
        assert "404" in result.error or "HTTP" in result.error
    
    async def test_timeout(self):
        """Test handling timeout."""
        from integration_coworker.discovery.http_client import FetchResult
        
        mock_result = FetchResult(
            success=False,
            error="Timeout at hop 0 (>30s): TimeoutException",
            redirect_chain=["https://example.com/slow.yaml"],
        )
        
        with patch("integration_coworker.discovery.http_client.hardened_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_result
            
            result = await validate_spec_url("https://example.com/slow.yaml")
        
        assert result.valid is False
        assert "timeout" in result.error.lower()
    
    async def test_invalid_spec_structure(self):
        """Test validating a spec with invalid structure."""
        from integration_coworker.discovery.http_client import FetchResult
        
        mock_result = FetchResult(
            success=True,
            content=INVALID_SPEC_NO_PATHS,
            final_url="https://example.com/invalid.yaml",
            content_type="application/yaml",
            redirect_chain=["https://example.com/invalid.yaml"],
        )
        
        with patch("integration_coworker.discovery.http_client.hardened_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_result
            
            result = await validate_spec_url("https://example.com/invalid.yaml")
        
        assert result.valid is False
        assert "paths" in result.error.lower()


@pytest.mark.integration
@pytest.mark.asyncio
class TestValidateSpecUrlLive:
    """Live integration tests for spec validation (guarded)."""
    
    async def test_validate_petstore(self):
        """Test validating the Petstore spec."""
        result = await validate_spec_url(
            "https://petstore3.swagger.io/api/v3/openapi.json"
        )
        
        assert result.valid is True
        assert "Petstore" in result.title
        assert result.spec_format in (SpecFormat.OPENAPI_3_0, SpecFormat.OPENAPI_3_1)
    
    async def test_validate_invalid_url(self):
        """Test validating an invalid URL."""
        result = await validate_spec_url(
            "https://example.com/this-does-not-exist-12345.yaml"
        )
        
        assert result.valid is False


# =============================================================================
# Redirect Chain SSRF Regression Tests
# Per OWASP SSRF Cheat Sheet: redirects are a common SSRF bypass vector
# =============================================================================

@pytest.mark.asyncio
class TestRedirectChainSSRF:
    """
    Regression tests for SSRF via redirect chains.
    
    Per OWASP: "The application must validate the IP address of any DNS name 
    obtained from the URL or follow redirects" - attackers can redirect from 
    a public URL to an internal resource.
    
    These tests verify that post-redirect validation catches:
    1. Redirect from public host to localhost
    2. Redirect from public host to private IP
    3. Redirect from public host to metadata endpoint
    4. Multi-hop redirect chains ending at blocked target
    """
    
    async def test_redirect_to_localhost_blocked(self):
        """
        SSRF: Public URL redirects to localhost should be blocked.
        
        Scenario: Attacker controls example.com which 302s to http://localhost/admin
        """
        class MockRedirectResponse:
            def __init__(self):
                self.status_code = 200
                self.headers = {"content-type": "application/yaml"}
                # Final URL after redirect is localhost
                self.url = "http://localhost/spec.yaml"
                
            def raise_for_status(self):
                pass
            
            async def aiter_bytes(self):
                yield VALID_OPENAPI_3_SPEC.encode("utf-8")
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, *args):
                pass
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.stream = MagicMock(return_value=MockRedirectResponse())
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            result = await validate_spec_url("https://public-site.com/spec.yaml")
        
        assert result.valid is False
        assert "redirect" in result.error.lower() or "security" in result.error.lower()
    
    async def test_redirect_to_private_ip_blocked(self):
        """
        SSRF: Public URL redirects to private IP should be blocked.
        
        Scenario: Attacker controls example.com which 302s to http://192.168.1.1/internal
        """
        class MockRedirectResponse:
            def __init__(self):
                self.status_code = 200
                self.headers = {"content-type": "application/yaml"}
                # Final URL after redirect is private IP
                self.url = "http://192.168.1.100/api/spec.yaml"
                
            def raise_for_status(self):
                pass
            
            async def aiter_bytes(self):
                yield VALID_OPENAPI_3_SPEC.encode("utf-8")
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, *args):
                pass
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.stream = MagicMock(return_value=MockRedirectResponse())
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            result = await validate_spec_url("https://public-site.com/spec.yaml")
        
        assert result.valid is False
        assert "redirect" in result.error.lower() or "security" in result.error.lower() or "private" in result.error.lower()
    
    async def test_redirect_to_metadata_endpoint_blocked(self):
        """
        SSRF: Public URL redirects to cloud metadata endpoint should be blocked.
        
        Scenario: Attacker controls example.com which 302s to http://169.254.169.254/
        This is a critical SSRF vector for extracting cloud credentials.
        """
        class MockRedirectResponse:
            def __init__(self):
                self.status_code = 200
                self.headers = {"content-type": "application/yaml"}
                # Final URL is AWS/GCP/Azure metadata endpoint
                self.url = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
                
            def raise_for_status(self):
                pass
            
            async def aiter_bytes(self):
                yield b'{"AccessKeyId": "AKIA...", "SecretAccessKey": "..."}'
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, *args):
                pass
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.stream = MagicMock(return_value=MockRedirectResponse())
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            result = await validate_spec_url("https://public-site.com/spec.yaml")
        
        assert result.valid is False
        assert "redirect" in result.error.lower() or "security" in result.error.lower() or "blocked" in result.error.lower()
    
    async def test_redirect_to_internal_hostname_blocked(self):
        """
        SSRF: Public URL redirects to internal hostname should be blocked.
        
        Scenario: Attacker controls example.com which 302s to http://internal-api.local/
        """
        class MockRedirectResponse:
            def __init__(self):
                self.status_code = 200
                self.headers = {"content-type": "application/yaml"}
                # Final URL uses .local TLD or internal hostname
                self.url = "http://metadata.google.internal/computeMetadata/v1/"
                
            def raise_for_status(self):
                pass
            
            async def aiter_bytes(self):
                yield b'{"project": "...", "zone": "..."}'
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, *args):
                pass
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.stream = MagicMock(return_value=MockRedirectResponse())
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            result = await validate_spec_url("https://public-site.com/spec.yaml")
        
        assert result.valid is False
        assert "redirect" in result.error.lower() or "security" in result.error.lower()
    
    async def test_redirect_to_ipv6_localhost_blocked(self):
        """
        SSRF: Redirect to IPv6 localhost should be blocked.
        
        Scenario: Attacker redirects to http://[::1]/internal-api
        IPv6 localhost is often missed in blocklists.
        """
        class MockRedirectResponse:
            def __init__(self):
                self.status_code = 200
                self.headers = {"content-type": "application/yaml"}
                # IPv6 localhost
                self.url = "http://[::1]/spec.yaml"
                
            def raise_for_status(self):
                pass
            
            async def aiter_bytes(self):
                yield VALID_OPENAPI_3_SPEC.encode("utf-8")
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, *args):
                pass
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.stream = MagicMock(return_value=MockRedirectResponse())
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            result = await validate_spec_url("https://public-site.com/spec.yaml")
        
        assert result.valid is False
