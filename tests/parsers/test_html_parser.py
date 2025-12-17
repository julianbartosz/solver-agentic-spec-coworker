"""
Tests for html_parser module.

Tests HTML spec parsing with BeautifulSoup integration.
"""
import pytest
from integration_coworker.parsers.html_parser import (
    parse_html_spec,
    extract_html_endpoints,
    HtmlSpecDocument,
)

# Check if beautifulsoup4 is available
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


@pytest.mark.no_db
class TestParseHtmlSpec:
    """Test HTML spec parsing."""
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_parse_simple_html_with_endpoints(self):
        """Test parsing simple HTML with endpoint definitions."""
        html = """
        <html>
        <head><title>API Documentation</title></head>
        <body>
            <h1>Customers API</h1>
            <p>GET /v1/customers - List all customers</p>
            <p>POST /v1/customers - Create a customer</p>
        </body>
        </html>
        """
        doc = parse_html_spec(html)
        
        assert doc.title == "API Documentation"
        assert len(doc.endpoints) >= 2
        methods = {ep.method for ep in doc.endpoints}
        assert "GET" in methods
        assert "POST" in methods
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_parse_html_with_code_blocks(self):
        """Test parsing HTML with endpoints in code blocks."""
        html = """
        <html>
        <body>
            <h2>Create Payment</h2>
            <code>POST /v1/payments</code>
            <pre>GET /v1/payments/{id}</pre>
        </body>
        </html>
        """
        doc = parse_html_spec(html)
        
        assert len(doc.endpoints) >= 2
        paths = {ep.path for ep in doc.endpoints}
        assert "/v1/payments" in paths
        assert "/v1/payments/{id}" in paths
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_parse_html_extracts_meta_description(self):
        """Test extraction of meta description."""
        html = """
        <html>
        <head>
            <title>My API</title>
            <meta name="description" content="This is the official API documentation.">
        </head>
        <body>
            <p>Content</p>
        </body>
        </html>
        """
        doc = parse_html_spec(html)
        
        assert doc.title == "My API"
        assert doc.description == "This is the official API documentation."
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_parse_html_extracts_base_url(self):
        """Test extraction of base URL."""
        html = """
        <html>
        <head><base href="https://api.example.com"></head>
        <body>
            <p>Content</p>
        </body>
        </html>
        """
        doc = parse_html_spec(html)
        
        assert doc.base_url == "https://api.example.com"
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_parse_html_with_table(self):
        """Test parsing HTML with endpoint table."""
        html = """
        <html>
        <body>
            <h2>Endpoints</h2>
            <table>
                <tr><th>Method</th><th>Path</th><th>Description</th></tr>
                <tr><td>GET</td><td>/users</td><td>List users</td></tr>
                <tr><td>POST</td><td>/users</td><td>Create user</td></tr>
                <tr><td>DELETE</td><td>/users/{id}</td><td>Delete user</td></tr>
            </table>
        </body>
        </html>
        """
        doc = parse_html_spec(html)
        
        assert len(doc.endpoints) >= 3
        methods = {ep.method for ep in doc.endpoints}
        assert "GET" in methods
        assert "POST" in methods
        assert "DELETE" in methods
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_parse_html_extracts_sections(self):
        """Test extraction of document sections."""
        html = """
        <html>
        <body>
            <h1>API Reference</h1>
            <p>Welcome to our API</p>
            <h2>Authentication</h2>
            <p>Use Bearer tokens</p>
            <h2>Endpoints</h2>
            <p>GET /v1/data</p>
        </body>
        </html>
        """
        doc = parse_html_spec(html)
        
        assert len(doc.sections) >= 2
        section_titles = [s['title'] for s in doc.sections]
        assert "API Reference" in section_titles or any("API" in t for t in section_titles)
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_parse_html_detects_events(self):
        """Test detection of webhook events in HTML."""
        html = """
        <html>
        <body>
            <h2>Webhooks</h2>
            <p>Event: payment.completed is sent when payment finishes.</p>
            <p>Event: customer.created is sent on signup.</p>
        </body>
        </html>
        """
        doc = parse_html_spec(html)
        
        assert len(doc.events) >= 2
        event_names = {e.name for e in doc.events}
        assert "payment.completed" in event_names
        assert "customer.created" in event_names
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_parse_html_removes_scripts(self):
        """Test that script content is removed from text extraction."""
        html = """
        <html>
        <body>
            <script>var secret = 'hidden';</script>
            <p>GET /v1/public</p>
        </body>
        </html>
        """
        doc = parse_html_spec(html)
        
        assert doc.raw_text is not None
        assert "secret" not in doc.raw_text
        assert "hidden" not in doc.raw_text
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_parse_empty_html(self):
        """Test parsing empty HTML."""
        doc = parse_html_spec("")
        
        assert doc.title is None
        assert len(doc.endpoints) == 0
    
    def test_parse_html_without_bs4_returns_error(self):
        """Test that missing BeautifulSoup is handled gracefully."""
        import integration_coworker.parsers.html_parser as html_module
        original_has_bs4 = html_module.HAS_BS4
        
        try:
            html_module.HAS_BS4 = False
            doc = parse_html_spec("<html><body>Test</body></html>")
            
            assert len(doc.errors) > 0
            assert "beautifulsoup4" in doc.errors[0].lower()
        finally:
            html_module.HAS_BS4 = original_has_bs4


@pytest.mark.no_db
class TestExtractHtmlEndpoints:
    """Test HTML endpoint extraction."""
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_extract_from_code_elements(self):
        """Test extraction from code elements."""
        html = """
        <div>
            <code>GET /api/users</code>
            <code>POST /api/users</code>
        </div>
        """
        endpoints = extract_html_endpoints(html)
        
        assert len(endpoints) >= 2
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_extract_from_endpoint_class(self):
        """Test extraction from elements with endpoint-related classes."""
        html = """
        <div class="api-endpoint">
            DELETE /v1/resources/{id}
        </div>
        """
        endpoints = extract_html_endpoints(html)
        
        assert len(endpoints) >= 1
        assert endpoints[0].method == "DELETE"
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_extract_from_table_structure(self):
        """Test extraction from table with method/path columns."""
        html = """
        <table>
            <tr><th>Method</th><th>Endpoint</th></tr>
            <tr><td>GET</td><td>/items</td></tr>
            <tr><td>PUT</td><td>/items/{id}</td></tr>
        </table>
        """
        endpoints = extract_html_endpoints(html)
        
        assert len(endpoints) >= 2
        methods = {ep.method for ep in endpoints}
        assert "GET" in methods
        assert "PUT" in methods
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_extract_deduplicates(self):
        """Test that duplicate endpoints are deduplicated."""
        html = """
        <code>GET /api/test</code>
        <pre>GET /api/test</pre>
        <div class="endpoint">GET /api/test</div>
        """
        endpoints = extract_html_endpoints(html)
        
        # Should only have one unique endpoint
        assert len(endpoints) == 1
    
    def test_extract_without_bs4_returns_empty(self):
        """Test that missing BeautifulSoup returns empty list."""
        import integration_coworker.parsers.html_parser as html_module
        original_has_bs4 = html_module.HAS_BS4
        
        try:
            html_module.HAS_BS4 = False
            endpoints = extract_html_endpoints("<html><body>GET /test</body></html>")
            
            assert endpoints == []
        finally:
            html_module.HAS_BS4 = original_has_bs4


@pytest.mark.no_db
class TestHtmlSpecDocument:
    """Test HtmlSpecDocument dataclass."""
    
    def test_create_html_spec_document(self):
        """Test creating an HtmlSpecDocument."""
        doc = HtmlSpecDocument(
            title="Test API",
            base_url="https://api.test.com"
        )
        
        assert doc.title == "Test API"
        assert doc.base_url == "https://api.test.com"
        assert doc.endpoints == []
        assert doc.schemas == []
        assert doc.events == []
        assert doc.errors == []
    
    def test_html_spec_document_defaults(self):
        """Test HtmlSpecDocument default values."""
        doc = HtmlSpecDocument()
        
        assert doc.title is None
        assert doc.description is None
        assert doc.base_url is None
        assert doc.raw_text is None


@pytest.mark.no_db
class TestHtmlParserEdgeCases:
    """Test edge cases for HTML parsing."""
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_malformed_html(self):
        """Test handling of malformed HTML."""
        html = "<html><body><p>Unclosed tag<div>Nested wrong</p></div>"
        
        # Should not raise exception
        doc = parse_html_spec(html)
        assert isinstance(doc, HtmlSpecDocument)
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_html_with_unicode(self):
        """Test handling of HTML with unicode characters."""
        html = """
        <html>
        <body>
            <h1>API 文档</h1>
            <p>GET /v1/用户</p>
        </body>
        </html>
        """
        doc = parse_html_spec(html)
        
        # Should handle gracefully
        assert isinstance(doc, HtmlSpecDocument)
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_html_with_only_styles(self):
        """Test HTML with only style content."""
        html = """
        <html>
        <head><style>body { color: red; }</style></head>
        <body></body>
        </html>
        """
        doc = parse_html_spec(html)
        
        assert len(doc.endpoints) == 0
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_deeply_nested_html(self):
        """Test deeply nested HTML structure."""
        nested = "<div>" * 50 + "GET /api/deep" + "</div>" * 50
        html = f"<html><body>{nested}</body></html>"
        
        doc = parse_html_spec(html)
        
        # Should still find the endpoint
        assert len(doc.endpoints) >= 1


@pytest.mark.no_db
class TestBaseUrlExtraction:
    """Test base URL extraction patterns."""
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_extract_from_base_tag(self):
        """Test extraction from <base> tag."""
        html = '<html><head><base href="https://api.example.com/v1"></head></html>'
        doc = parse_html_spec(html)
        
        assert doc.base_url == "https://api.example.com/v1"
    
    @pytest.mark.skipif(not HAS_BS4, reason="beautifulsoup4 not installed")
    def test_extract_from_text_pattern(self):
        """Test extraction from text pattern."""
        html = """
        <html>
        <body>
            <p>Base URL: https://api.myservice.io</p>
        </body>
        </html>
        """
        doc = parse_html_spec(html)
        
        assert doc.base_url == "https://api.myservice.io"
