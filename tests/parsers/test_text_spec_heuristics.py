"""
Tests for text_spec_heuristics module.

Tests endpoint detection, schema inference, and event detection from unstructured text.
"""
import pytest
from integration_coworker.parsers.text_spec_heuristics import (
    detect_endpoints,
    infer_request_schema,
    infer_response_schema,
    detect_events,
    ParsedEndpoint,
    ParsedSchema,
    ParsedEvent,
)


@pytest.mark.no_db
class TestDetectEndpoints:
    """Test endpoint detection from text."""
    
    def test_detect_simple_get_endpoint(self):
        """Test detection of simple GET endpoint."""
        text = "GET /v1/customers"
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 1
        assert endpoints[0].method == "GET"
        assert endpoints[0].path == "/v1/customers"
    
    def test_detect_post_endpoint_with_path_params(self):
        """Test detection of POST endpoint with path parameters."""
        text = "POST /v1/customers/{customer_id}/subscriptions"
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 1
        assert endpoints[0].method == "POST"
        assert endpoints[0].path == "/v1/customers/{customer_id}/subscriptions"
    
    def test_detect_multiple_endpoints(self):
        """Test detection of multiple endpoints in text."""
        text = """
        # API Endpoints
        
        GET /v1/customers - List all customers
        POST /v1/customers - Create a customer
        GET /v1/customers/{id} - Get a customer
        PUT /v1/customers/{id} - Update a customer
        DELETE /v1/customers/{id} - Delete a customer
        """
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 5
        methods = {ep.method for ep in endpoints}
        assert methods == {"GET", "POST", "PUT", "DELETE"}
    
    def test_detect_lowercase_method(self):
        """Test that lowercase methods are detected and normalized."""
        text = "get /api/users"
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 1
        assert endpoints[0].method == "GET"
        assert endpoints[0].path == "/api/users"
    
    def test_detect_all_http_methods(self):
        """Test detection of all standard HTTP methods."""
        text = """
        GET /resource
        POST /resource
        PUT /resource
        PATCH /resource
        DELETE /resource
        HEAD /resource
        OPTIONS /resource
        """
        endpoints = detect_endpoints(text)
        
        # Should detect 7 unique endpoints (same path, different methods)
        assert len(endpoints) == 7
        methods = {ep.method for ep in endpoints}
        assert methods == {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    
    def test_detect_endpoint_from_url(self):
        """Test extraction of path from full URL."""
        text = "The API is available at https://api.example.com/api/v2/users"
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 1
        assert "/api/v2/users" in endpoints[0].path
    
    def test_detect_auth_hint_bearer(self):
        """Test detection of Bearer token authentication hint."""
        text = """
        GET /v1/secure/data
        
        Authentication: Bearer token required.
        """
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 1
        assert endpoints[0].auth_hint == "bearer"
    
    def test_detect_auth_hint_api_key(self):
        """Test detection of API key authentication hint."""
        text = """
        GET /v1/data
        
        Pass your API-KEY in the header.
        """
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 1
        assert endpoints[0].auth_hint == "api_key"
    
    def test_no_endpoints_in_non_api_text(self):
        """Test that non-API text doesn't produce false positives."""
        text = "This is just some regular text about programming."
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 0
    
    def test_deduplicate_endpoints(self):
        """Test that duplicate endpoints are deduplicated."""
        text = """
        GET /v1/customers
        
        And again:
        GET /v1/customers
        """
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 1
    
    def test_endpoint_with_dots_in_path(self):
        """Test endpoint paths with dots (e.g., for versioning)."""
        text = "GET /api/v1.0/users"
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 1
        assert endpoints[0].path == "/api/v1.0/users"
    
    def test_source_line_tracking(self):
        """Test that source line numbers are tracked."""
        text = """Line 1
Line 2
GET /api/test
Line 4"""
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 1
        assert endpoints[0].source_line == 3


@pytest.mark.no_db
class TestInferRequestSchema:
    """Test request schema inference from text."""
    
    def test_infer_schema_from_request_body_section(self):
        """Test schema inference from a labeled request body section."""
        text = """
        # Request Body
        
        name: string (required)
        email: string (required)
        age: integer
        """
        schema = infer_request_schema(text)
        
        assert schema is not None
        assert "name" in schema.fields
        assert schema.fields["name"]["type"] == "string"
        assert schema.fields["name"]["required"] is True
    
    def test_infer_schema_with_endpoint_context(self):
        """Test that endpoint context affects schema naming."""
        text = "name: string\nemail: string"
        endpoint = ParsedEndpoint(
            method="POST",
            path="/v1/customers",
            source_context=text
        )
        
        schema = infer_request_schema(text, endpoint)
        
        assert schema is not None
        assert "customers" in schema.name.lower() or "request" in schema.name.lower()
    
    def test_infer_multiple_field_types(self):
        """Test inference of various field types."""
        text = """
        # Parameters
        
        name: string
        count: integer
        price: number
        active: boolean
        tags: array
        metadata: object
        """
        schema = infer_request_schema(text)
        
        assert schema is not None
        assert schema.fields.get("count", {}).get("type") == "integer"
        assert schema.fields.get("price", {}).get("type") == "number"
        assert schema.fields.get("active", {}).get("type") == "boolean"
    
    def test_no_schema_from_empty_text(self):
        """Test that empty text produces no schema."""
        schema = infer_request_schema("")
        
        assert schema is None


@pytest.mark.no_db
class TestInferResponseSchema:
    """Test response schema inference from text."""
    
    def test_infer_schema_from_response_section(self):
        """Test schema inference from a labeled response section."""
        text = """
        # Response
        
        id: string
        created_at: datetime
        status: string
        """
        schema = infer_response_schema(text)
        
        assert schema is not None
        assert "id" in schema.fields
        assert "created_at" in schema.fields
    
    def test_infer_schema_from_returns_section(self):
        """Test schema inference from 'Returns' section."""
        text = """
        # Returns
        
        success: boolean
        data: object
        """
        schema = infer_response_schema(text)
        
        assert schema is not None
        assert "success" in schema.fields


@pytest.mark.no_db
class TestDetectEvents:
    """Test webhook/event detection from text."""
    
    def test_detect_event_from_text(self):
        """Test detection of event definitions."""
        text = "The webhook event: customer.created is sent when a new customer is created."
        events = detect_events(text)
        
        assert len(events) == 1
        assert events[0].name == "customer.created"
    
    def test_detect_multiple_events(self):
        """Test detection of multiple events."""
        text = """
        # Webhooks
        
        Event: payment.succeeded
        Event: payment.failed
        Event: customer.updated
        """
        events = detect_events(text)
        
        assert len(events) == 3
        event_names = {e.name for e in events}
        assert "payment.succeeded" in event_names
        assert "payment.failed" in event_names
        assert "customer.updated" in event_names
    
    def test_no_events_in_non_webhook_text(self):
        """Test that non-webhook text doesn't produce false positives."""
        text = "This is just some documentation about our API."
        events = detect_events(text)
        
        # Should be empty or very few false positives
        assert len(events) == 0


@pytest.mark.no_db
class TestParsedEndpointDataclass:
    """Test ParsedEndpoint dataclass."""
    
    def test_create_parsed_endpoint(self):
        """Test creating a ParsedEndpoint."""
        endpoint = ParsedEndpoint(
            method="GET",
            path="/v1/test",
            summary="Test endpoint",
            auth_hint="bearer"
        )
        
        assert endpoint.method == "GET"
        assert endpoint.path == "/v1/test"
        assert endpoint.summary == "Test endpoint"
        assert endpoint.auth_hint == "bearer"
    
    def test_parsed_endpoint_defaults(self):
        """Test ParsedEndpoint default values."""
        endpoint = ParsedEndpoint(method="GET", path="/test")
        
        assert endpoint.summary is None
        assert endpoint.description is None
        assert endpoint.source_line is None


@pytest.mark.no_db
class TestParsedSchemaDataclass:
    """Test ParsedSchema dataclass."""
    
    def test_create_parsed_schema(self):
        """Test creating a ParsedSchema."""
        schema = ParsedSchema(
            name="TestSchema",
            fields={"id": {"type": "string", "required": True}},
            description="A test schema"
        )
        
        assert schema.name == "TestSchema"
        assert schema.fields["id"]["type"] == "string"
        assert schema.description == "A test schema"
    
    def test_parsed_schema_empty_fields_default(self):
        """Test that ParsedSchema has empty dict as default for fields."""
        schema = ParsedSchema(name="Empty")
        
        assert schema.fields == {}


@pytest.mark.no_db
class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_malformed_endpoint_path(self):
        """Test handling of malformed endpoint paths."""
        text = "GET /api/users/{id}/posts/{post_id}"
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 1
        assert "{id}" in endpoints[0].path
        assert "{post_id}" in endpoints[0].path
    
    def test_unicode_in_text(self):
        """Test handling of unicode characters in text."""
        text = "GET /api/用户  # Chinese characters"
        endpoints = detect_endpoints(text)
        
        # Should handle gracefully (may or may not detect depending on pattern)
        # Main thing is it shouldn't crash
        assert isinstance(endpoints, list)
    
    def test_very_long_path(self):
        """Test handling of very long endpoint paths."""
        long_path = "/api/" + "/".join(["segment"] * 20)
        text = f"GET {long_path}"
        endpoints = detect_endpoints(text)
        
        assert len(endpoints) == 1
        assert len(endpoints[0].path) > 100
    
    def test_empty_text(self):
        """Test handling of empty text."""
        assert detect_endpoints("") == []
        assert infer_request_schema("") is None
        assert infer_response_schema("") is None
        assert detect_events("") == []
