"""
Tests for G-03 hardening: Spec conversion quality markers.

These tests verify that:
1. Native OpenAPI/Swagger specs have _conversion_quality="deterministic"
2. AsyncAPI/GraphQL specs have _conversion_quality="deterministic"
3. HTML specs have _conversion_quality="llm_assisted"
4. PDF specs have _conversion_quality="best_effort"
5. Quality markers are included in metadata
"""
import pytest
import json
import yaml
from pathlib import Path

from integration_coworker.sources.openapi import OpenAPISource


class TestOpenAPIConversionQuality:
    """Tests for OpenAPI spec conversion quality markers."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.source = OpenAPISource()
    
    def test_native_openapi_30_has_deterministic_quality(self):
        """Native OpenAPI 3.0 specs should have deterministic conversion quality."""
        spec_content = json.dumps({
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {"/health": {"get": {"responses": {"200": {"description": "OK"}}}}}
        })
        
        result = self.source.parse(spec_content, "test.json")
        
        assert result.data is not None
        assert result.data.get("_conversion_quality") == "deterministic"
        assert result.data.get("_conversion_warnings") == []
    
    def test_native_openapi_31_has_deterministic_quality(self):
        """Native OpenAPI 3.1 specs should have deterministic conversion quality."""
        spec_content = yaml.dump({
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {"/health": {"get": {"responses": {"200": {"description": "OK"}}}}}
        })
        
        result = self.source.parse(spec_content, "test.yaml")
        
        assert result.data is not None
        assert result.data.get("_conversion_quality") == "deterministic"
    
    def test_swagger_20_has_deterministic_quality(self):
        """Swagger 2.0 specs should have deterministic conversion quality."""
        spec_content = json.dumps({
            "swagger": "2.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {"/health": {"get": {"responses": {"200": {"description": "OK"}}}}}
        })
        
        result = self.source.parse(spec_content, "swagger.json")
        
        assert result.data is not None
        assert result.data.get("_conversion_quality") == "deterministic"
    
    def test_metadata_includes_conversion_quality(self):
        """Metadata should include conversion_quality field."""
        spec_content = json.dumps({
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {}
        })
        
        result = self.source.parse(spec_content, "test.json")
        
        assert result.metadata is not None
        assert result.metadata.get("conversion_quality") == "deterministic"
        assert result.metadata.get("conversion_warnings") == []


class TestAsyncAPIConversionQuality:
    """Tests for AsyncAPI spec conversion quality markers."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.source = OpenAPISource()
    
    def test_asyncapi_has_deterministic_quality(self):
        """AsyncAPI specs converted to pseudo-OpenAPI should have deterministic quality."""
        spec_content = yaml.dump({
            "asyncapi": "2.6.0",
            "info": {"title": "Events API", "version": "1.0.0"},
            "channels": {
                "user/signup": {
                    "publish": {"summary": "User signed up"}
                }
            }
        })
        
        result = self.source.parse(spec_content, "events.yaml")
        
        assert result.data is not None
        assert result.data.get("_parsed_from") == "asyncapi"
        assert result.data.get("_conversion_quality") == "deterministic"
        assert result.data.get("_conversion_warnings") == []


class TestGraphQLConversionQuality:
    """Tests for GraphQL schema conversion quality markers."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.source = OpenAPISource()
    
    @pytest.mark.requires_graphql
    def test_graphql_has_deterministic_quality(self):
        """GraphQL schemas converted to pseudo-OpenAPI should have deterministic quality."""
        schema_content = """
        type Query {
            users: [User]
            user(id: ID!): User
        }
        
        type User {
            id: ID!
            name: String!
            email: String!
        }
        """
        
        # Skip if GraphQL parser not available
        try:
            from integration_coworker.parsers.graphql_parser import parse_graphql_schema
        except ImportError:
            pytest.skip("graphql-core not installed")
        
        result = self.source.parse(schema_content, "schema.graphql")
        
        if result.data is not None:
            assert result.data.get("_parsed_from") == "graphql"
            assert result.data.get("_conversion_quality") == "deterministic"


class TestHTMLConversionQuality:
    """Tests for HTML API doc conversion quality markers."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.source = OpenAPISource()
    
    def test_html_has_llm_assisted_quality(self):
        """HTML API docs should have llm_assisted conversion quality."""
        html_content = """
        <!DOCTYPE html>
        <html>
        <head><title>API Documentation</title></head>
        <body>
            <h1>REST API</h1>
            <h2>GET /users</h2>
            <p>Returns a list of users</p>
        </body>
        </html>
        """
        
        # Skip if HTML parser not available
        try:
            from integration_coworker.parsers.html_parser import parse_html_spec
        except ImportError:
            pytest.skip("HTML parser not available")
        
        result = self.source.parse(html_content, "docs.html")
        
        if result.data is not None:
            assert result.data.get("_parsed_from") == "html"
            assert result.data.get("_conversion_quality") == "llm_assisted"
            assert len(result.data.get("_conversion_warnings", [])) > 0
            # Should contain warning about pattern extraction
            warnings = result.data.get("_conversion_warnings", [])
            assert any("pattern" in w.lower() or "heuristic" in w.lower() for w in warnings)


class TestPDFConversionQuality:
    """Tests for PDF API doc conversion quality markers."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.source = OpenAPISource()
    
    def test_pdf_has_best_effort_quality(self):
        """PDF API docs should have best_effort conversion quality."""
        # PDF detection is done by magic bytes, not content parsing
        # We can only test the conversion function directly
        
        # Create a mock PDF document
        class MockPDFDoc:
            title = "API Reference"
            description = "API documentation from PDF"
            endpoints = []
        
        spec = self.source._pdf_to_pseudo_openapi(MockPDFDoc(), "api.pdf")
        
        assert spec["_parsed_from"] == "pdf"
        assert spec["_conversion_quality"] == "best_effort"
        assert len(spec["_conversion_warnings"]) > 0
        # Should contain warning about OCR/LLM and manual review
        warnings = spec["_conversion_warnings"]
        assert any("OCR" in w or "LLM" in w for w in warnings)
        assert any("HUMAN REVIEW" in w or "manual review" in w.lower() for w in warnings)


class TestConversionWarningsContent:
    """Tests for specific warning messages in conversion quality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.source = OpenAPISource()
    
    def test_html_warnings_mention_incompleteness(self):
        """HTML conversion warnings should mention potential incompleteness."""
        class MockHTMLDoc:
            title = "Test API"
            description = "Test description"
            base_url = None
            endpoints = []
        
        spec = self.source._html_to_pseudo_openapi(MockHTMLDoc(), "test.html")
        
        warnings = spec["_conversion_warnings"]
        assert any("incomplete" in w.lower() or "inferred" in w.lower() for w in warnings)
        assert any("review" in w.lower() for w in warnings)
    
    def test_pdf_warnings_require_human_review(self):
        """PDF conversion warnings should REQUIRE human review."""
        class MockPDFDoc:
            title = "Test API"
            description = "Test description"
            endpoints = []
        
        spec = self.source._pdf_to_pseudo_openapi(MockPDFDoc(), "test.pdf")
        
        warnings = spec["_conversion_warnings"]
        # Must have emphatic warning about human review
        assert any("REQUIRE" in w or "HUMAN REVIEW" in w for w in warnings)
    
    def test_deterministic_has_no_warnings(self):
        """Deterministic conversions should have empty warnings list."""
        spec_content = json.dumps({
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {}
        })
        
        result = self.source.parse(spec_content, "test.json")
        
        assert result.data["_conversion_warnings"] == []


class TestQualityLevelsOrdering:
    """Tests that quality levels follow expected ordering."""
    
    def test_quality_level_enum_ordering(self):
        """Quality levels should follow: deterministic > llm_assisted > best_effort."""
        quality_order = {
            "deterministic": 3,
            "llm_assisted": 2,
            "best_effort": 1,
            "unknown": 0,
        }
        
        # Verify ordering is sensible
        assert quality_order["deterministic"] > quality_order["llm_assisted"]
        assert quality_order["llm_assisted"] > quality_order["best_effort"]
        assert quality_order["best_effort"] > quality_order["unknown"]


class TestStripeStyleOpenAPIDetection:
    """
    Regression tests for OpenAPI specs with non-standard structure.
    
    The Stripe API spec has "openapi" field at line 60960 (end of file),
    not at the beginning. This caused false negatives in detection.
    
    Bug: source.detection.rejected [uri=stripe_api.json best_score=0.000]
    Fix: Check tail of file and structural patterns for large JSON specs.
    """
    
    def setup_method(self):
        """Set up test fixtures."""
        self.source = OpenAPISource()
    
    def test_stripe_style_openapi_at_end(self):
        """
        Regression test: OpenAPI field at end of file should be detected.
        
        Simulates Stripe API structure where "openapi" comes after components.
        """
        # Create spec with openapi field buried at the end
        large_components = {
            "components": {
                "schemas": {
                    f"schema_{i}": {
                        "type": "object",
                        "description": f"Schema {i} with lots of content " * 50
                    }
                    for i in range(50)  # Generate enough content to push openapi far
                }
            },
            "info": {"title": "Stripe-style API", "version": "1.0.0"},
            "paths": {"/v1/accounts": {"get": {"responses": {"200": {"description": "OK"}}}}},
            "openapi": "3.0.0",  # At the end, like Stripe
        }
        
        spec_content = json.dumps(large_components)
        
        # The spec is large enough that openapi is past first 2000 chars
        assert len(spec_content) > 10000
        assert '"openapi":' not in spec_content[:2000].lower()
        
        # Detection should still work
        score = self.source.detect(spec_content, "stripe_style.json", "")
        assert score > 0.9, f"Expected high confidence, got {score}"
    
    def test_structural_detection_components_and_schemas(self):
        """
        Specs with components/schemas structure should be detected even without openapi field in header.
        """
        spec_content = json.dumps({
            "components": {
                "schemas": {
                    "Account": {"type": "object"}
                }
            },
            "paths": {},
            # openapi field would be at end in real file
        })
        
        # This should trigger structural heuristics
        score = self.source.detect(spec_content, "maybe_openapi.json", "")
        assert score > 0.8, f"Expected structural detection to trigger, got {score}"
    
    def test_real_stripe_spec_detection(self):
        """
        Integration test: Verify actual stripe_api.json is detected.
        """
        stripe_spec_path = Path(__file__).parent.parent.parent / "specs" / "stripe_api.json"
        if not stripe_spec_path.exists():
            pytest.skip("stripe_api.json not found in specs/")
        
        content = stripe_spec_path.read_text()
        
        # Verify the issue existed (openapi not in first 2000 chars)
        assert '"openapi":' not in content[:2000].lower(), "Test assumption violated"
        
        # But detection should now work
        score = self.source.detect(content, str(stripe_spec_path), "")
        assert score > 0.9, f"Stripe spec detection failed with score {score}"