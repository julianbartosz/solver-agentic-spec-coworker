"""
Tests for P3: Request/Response Field Mappings

Tests for field mapping generation utilities including:
- Request mapping from parameters and schemas
- Response mapping from schemas
- Case conversion utilities
- Pagination detection
"""
import pytest

from integration_coworker.codegen.field_mappings import (
    FieldMapping,
    RequestMapping,
    ResponseMapping,
    to_snake_case,
    to_camel_case,
    generate_request_mapping,
    generate_response_mapping,
    generate_field_mappings,
    merge_mappings,
)
from integration_coworker.domain.models import (
    Endpoint,
    EndpointParameter,
    Schema,
    SchemaField,
)

# Mark all tests as not needing database
pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# Test: Case Conversion Utilities
# ---------------------------------------------------------------------------

class TestCaseConversion:
    """Tests for case conversion utilities."""
    
    def test_to_snake_case_camel(self):
        """Should convert camelCase to snake_case."""
        assert to_snake_case("camelCase") == "camel_case"
        assert to_snake_case("somePropertyName") == "some_property_name"
    
    def test_to_snake_case_pascal(self):
        """Should convert PascalCase to snake_case."""
        assert to_snake_case("PascalCase") == "pascal_case"
        assert to_snake_case("SomeClassName") == "some_class_name"
    
    def test_to_snake_case_already_snake(self):
        """Should leave snake_case unchanged."""
        assert to_snake_case("already_snake") == "already_snake"
        assert to_snake_case("some_name") == "some_name"
    
    def test_to_snake_case_kebab(self):
        """Should convert kebab-case to snake_case."""
        assert to_snake_case("kebab-case") == "kebab_case"
        assert to_snake_case("some-property") == "some_property"
    
    def test_to_snake_case_acronyms(self):
        """Should handle acronyms in names."""
        assert to_snake_case("getHTTPResponse") == "get_http_response"
        assert to_snake_case("APIKey") == "api_key"
    
    def test_to_camel_case(self):
        """Should convert snake_case to camelCase."""
        assert to_camel_case("snake_case") == "snakeCase"
        assert to_camel_case("some_property_name") == "somePropertyName"
    
    def test_to_camel_case_single_word(self):
        """Should handle single words."""
        assert to_camel_case("name") == "name"


# ---------------------------------------------------------------------------
# Test: FieldMapping Dataclass
# ---------------------------------------------------------------------------

class TestFieldMapping:
    """Tests for FieldMapping dataclass."""
    
    def test_field_mapping_defaults(self):
        """Should have sensible defaults."""
        mapping = FieldMapping(source="foo", target="bar")
        
        assert mapping.source == "foo"
        assert mapping.target == "bar"
        assert mapping.type is None
        assert mapping.required is False
        assert mapping.transform is None
    
    def test_field_mapping_with_all_fields(self):
        """Should accept all fields."""
        mapping = FieldMapping(
            source="amount",
            target="amount",
            type="integer",
            required=True,
            transform="cents_to_dollars",
        )
        
        assert mapping.type == "integer"
        assert mapping.required is True
        assert mapping.transform == "cents_to_dollars"


# ---------------------------------------------------------------------------
# Test: RequestMapping Dataclass
# ---------------------------------------------------------------------------

class TestRequestMapping:
    """Tests for RequestMapping dataclass."""
    
    def test_empty_request_mapping(self):
        """Should have empty lists by default."""
        mapping = RequestMapping()
        
        assert mapping.path_params == []
        assert mapping.query_params == []
        assert mapping.header_params == []
        assert mapping.body_fields == []
        assert mapping.content_type == "application/json"
    
    def test_to_dict(self):
        """Should convert to dictionary."""
        mapping = RequestMapping(
            path_params=[FieldMapping("id", "id", "string", True)],
            query_params=[FieldMapping("limit", "limit", "integer", False)],
            body_fields=[FieldMapping("name", "name", "string", True)],
        )
        
        result = mapping.to_dict()
        
        assert len(result["path_params"]) == 1
        assert result["path_params"][0]["source"] == "id"
        assert len(result["query_params"]) == 1
        assert len(result["body_fields"]) == 1
        assert result["content_type"] == "application/json"


# ---------------------------------------------------------------------------
# Test: ResponseMapping Dataclass
# ---------------------------------------------------------------------------

class TestResponseMapping:
    """Tests for ResponseMapping dataclass."""
    
    def test_empty_response_mapping(self):
        """Should have empty lists by default."""
        mapping = ResponseMapping()
        
        assert mapping.extract_fields == []
        assert mapping.status_field is None
        assert mapping.error_field is None
        assert mapping.pagination_fields is None
    
    def test_to_dict(self):
        """Should convert to dictionary."""
        mapping = ResponseMapping(
            extract_fields=[FieldMapping("id", "id", "string")],
            status_field="status",
            pagination_fields={"type": "cursor", "cursor_field": "next_cursor"},
        )
        
        result = mapping.to_dict()
        
        assert len(result["extract_fields"]) == 1
        assert result["status_field"] == "status"
        assert result["pagination_fields"]["type"] == "cursor"


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_endpoint():
    """Create a sample endpoint for testing."""
    return Endpoint(
        id=1,
        source_system_id=1,
        spec_document_id=1,
        path="/v1/customers/{customer_id}",
        method="GET",
        operation_id="getCustomer",
        summary="Get a customer",
        description="Retrieves a customer by ID",
        request_schema_id=1,
        response_schema_id=2,
    )


@pytest.fixture
def sample_parameters():
    """Create sample endpoint parameters."""
    return [
        EndpointParameter(
            id=1,
            endpoint_id=1,
            name="customer_id",
            location="path",
            required=True,
            schema_ref="string",
        ),
        EndpointParameter(
            id=2,
            endpoint_id=1,
            name="expand",
            location="query",
            required=False,
            schema_ref="array",
            description="Fields to expand",
        ),
    ]


@pytest.fixture
def sample_schemas():
    """Create sample schemas."""
    return [
        Schema(id=1, source_system_id=1, name="CustomerRequest", ref="#/components/schemas/CustomerRequest"),
        Schema(id=2, source_system_id=1, name="Customer", ref="#/components/schemas/Customer"),
    ]


@pytest.fixture
def sample_schema_fields():
    """Create sample schema fields."""
    return [
        # Request schema fields
        SchemaField(id=1, schema_id=1, name="name", field_type="string", required=True),
        SchemaField(id=2, schema_id=1, name="email", field_type="string", required=True),
        # Response schema fields
        SchemaField(id=3, schema_id=2, name="id", field_type="string", required=True),
        SchemaField(id=4, schema_id=2, name="name", field_type="string"),
        SchemaField(id=5, schema_id=2, name="email", field_type="string"),
        SchemaField(id=6, schema_id=2, name="created", field_type="integer"),
    ]


# ---------------------------------------------------------------------------
# Test: generate_request_mapping
# ---------------------------------------------------------------------------

class TestGenerateRequestMapping:
    """Tests for generate_request_mapping function."""
    
    def test_maps_path_parameters(self, sample_endpoint, sample_parameters, sample_schemas, sample_schema_fields):
        """Should map path parameters correctly."""
        request_schema = sample_schemas[0]
        
        result = generate_request_mapping(
            sample_endpoint, sample_parameters, request_schema, sample_schema_fields
        )
        
        assert len(result.path_params) == 1
        assert result.path_params[0].source == "customer_id"
        assert result.path_params[0].target == "customer_id"
        assert result.path_params[0].required is True
    
    def test_maps_query_parameters(self, sample_endpoint, sample_parameters, sample_schemas, sample_schema_fields):
        """Should map query parameters correctly."""
        request_schema = sample_schemas[0]
        
        result = generate_request_mapping(
            sample_endpoint, sample_parameters, request_schema, sample_schema_fields
        )
        
        assert len(result.query_params) == 1
        assert result.query_params[0].source == "expand"
        assert result.query_params[0].required is False
    
    def test_maps_body_fields_from_schema(self, sample_endpoint, sample_parameters, sample_schemas, sample_schema_fields):
        """Should map body fields from schema."""
        request_schema = sample_schemas[0]
        
        result = generate_request_mapping(
            sample_endpoint, sample_parameters, request_schema, sample_schema_fields
        )
        
        assert len(result.body_fields) == 2
        field_names = {f.source for f in result.body_fields}
        assert "name" in field_names
        assert "email" in field_names
    
    def test_converts_field_names_to_snake_case(self, sample_endpoint, sample_parameters, sample_schemas, sample_schema_fields):
        """Should convert field names to snake_case for source."""
        # Add a camelCase field
        sample_schema_fields.append(
            SchemaField(id=7, schema_id=1, name="dateOfBirth", field_type="string")
        )
        request_schema = sample_schemas[0]
        
        result = generate_request_mapping(
            sample_endpoint, sample_parameters, request_schema, sample_schema_fields
        )
        
        date_field = next((f for f in result.body_fields if "date" in f.source), None)
        assert date_field is not None
        assert date_field.source == "date_of_birth"
        assert date_field.target == "dateOfBirth"


# ---------------------------------------------------------------------------
# Test: generate_response_mapping
# ---------------------------------------------------------------------------

class TestGenerateResponseMapping:
    """Tests for generate_response_mapping function."""
    
    def test_maps_response_fields_from_schema(self, sample_endpoint, sample_schemas, sample_schema_fields):
        """Should map response fields from schema."""
        response_schema = sample_schemas[1]
        
        result = generate_response_mapping(
            sample_endpoint, response_schema, sample_schema_fields
        )
        
        assert len(result.extract_fields) == 4  # id, name, email, created
        field_names = {f.target for f in result.extract_fields}
        assert "id" in field_names
        assert "name" in field_names
    
    def test_detects_status_field(self, sample_endpoint, sample_schemas, sample_schema_fields):
        """Should detect status field."""
        # Add status field to response schema
        sample_schema_fields.append(
            SchemaField(id=8, schema_id=2, name="status", field_type="string")
        )
        response_schema = sample_schemas[1]
        
        result = generate_response_mapping(
            sample_endpoint, response_schema, sample_schema_fields
        )
        
        assert result.status_field == "status"
    
    def test_detects_error_field(self, sample_endpoint, sample_schemas, sample_schema_fields):
        """Should detect error field."""
        # Add error field to response schema
        sample_schema_fields.append(
            SchemaField(id=9, schema_id=2, name="error", field_type="object")
        )
        response_schema = sample_schemas[1]
        
        result = generate_response_mapping(
            sample_endpoint, response_schema, sample_schema_fields
        )
        
        assert result.error_field == "error"
    
    def test_detects_cursor_pagination(self, sample_endpoint, sample_schemas, sample_schema_fields):
        """Should detect cursor-based pagination."""
        # Add pagination fields
        sample_schema_fields.extend([
            SchemaField(id=10, schema_id=2, name="next_cursor", field_type="string"),
            SchemaField(id=11, schema_id=2, name="has_more", field_type="boolean"),
        ])
        response_schema = sample_schemas[1]
        
        result = generate_response_mapping(
            sample_endpoint, response_schema, sample_schema_fields
        )
        
        assert result.pagination_fields is not None
        assert result.pagination_fields["type"] == "cursor"
        assert result.pagination_fields["cursor_field"] == "next_cursor"
        assert result.pagination_fields["has_more_field"] == "has_more"


# ---------------------------------------------------------------------------
# Test: generate_field_mappings
# ---------------------------------------------------------------------------

class TestGenerateFieldMappings:
    """Tests for generate_field_mappings function."""
    
    def test_returns_both_mappings(self, sample_endpoint, sample_parameters, sample_schemas, sample_schema_fields):
        """Should return both request and response mappings."""
        request_mapping, response_mapping = generate_field_mappings(
            sample_endpoint, sample_parameters, sample_schemas, sample_schema_fields
        )
        
        assert isinstance(request_mapping, dict)
        assert isinstance(response_mapping, dict)
        assert "path_params" in request_mapping
        assert "extract_fields" in response_mapping
    
    def test_handles_missing_schemas(self, sample_parameters, sample_schema_fields):
        """Should handle missing schemas gracefully."""
        endpoint = Endpoint(
            id=1,
            source_system_id=1,
            spec_document_id=1,
            path="/v1/test",
            method="GET",
            operation_id=None,
            summary=None,
            description=None,
            request_schema_id=None,
            response_schema_id=None,
        )
        
        request_mapping, response_mapping = generate_field_mappings(
            endpoint, sample_parameters, [], sample_schema_fields
        )
        
        assert request_mapping is not None
        assert response_mapping is not None


# ---------------------------------------------------------------------------
# Test: merge_mappings
# ---------------------------------------------------------------------------

class TestMergeMappings:
    """Tests for merge_mappings function."""
    
    def test_returns_schema_mapping_when_no_llm(self):
        """Should return schema mapping when LLM mapping is empty."""
        schema_mapping = {"path_params": [{"source": "id"}]}
        
        result = merge_mappings(schema_mapping, {})
        
        assert result == schema_mapping
    
    def test_adds_description_from_llm(self):
        """Should add description from LLM mapping."""
        schema_mapping = {"path_params": [{"source": "id"}]}
        llm_mapping = {"description": "Maps customer ID to path"}
        
        result = merge_mappings(schema_mapping, llm_mapping)
        
        assert result["description"] == "Maps customer ID to path"
        assert result["path_params"] == schema_mapping["path_params"]
    
    def test_adds_transforms_from_llm(self):
        """Should add transforms from LLM mapping."""
        schema_mapping = {"body_fields": [{"source": "amount"}]}
        llm_mapping = {"transforms": {"amount": "cents_to_dollars"}}
        
        result = merge_mappings(schema_mapping, llm_mapping)
        
        assert result["transforms"]["amount"] == "cents_to_dollars"


# ---------------------------------------------------------------------------
# Test: Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_handles_empty_parameters(self, sample_endpoint, sample_schemas, sample_schema_fields):
        """Should handle empty parameters list."""
        result = generate_request_mapping(
            sample_endpoint, [], sample_schemas[0], sample_schema_fields
        )
        
        assert result.path_params == []
        assert result.query_params == []
    
    def test_handles_empty_schema_fields(self, sample_endpoint, sample_parameters, sample_schemas):
        """Should handle empty schema fields."""
        result = generate_request_mapping(
            sample_endpoint, sample_parameters, sample_schemas[0], []
        )
        
        assert result.body_fields == []
    
    def test_infers_body_fields_for_post(self):
        """Should infer body fields for POST endpoints without schema."""
        endpoint = Endpoint(
            id=1,
            source_system_id=1,
            spec_document_id=1,
            path="/v1/customers",
            method="POST",
            operation_id=None,
            summary=None,
            description=None,
            request_schema_id=None,
            response_schema_id=None,
        )
        
        result = generate_request_mapping(endpoint, [], None, [])
        
        # Should infer common customer fields
        field_names = {f.source for f in result.body_fields}
        assert "name" in field_names or "email" in field_names
    
    def test_infers_response_fields_for_get_list(self):
        """Should infer response fields for list endpoints."""
        endpoint = Endpoint(
            id=1,
            source_system_id=1,
            spec_document_id=1,
            path="/v1/customers",
            method="GET",
            operation_id=None,
            summary=None,
            description=None,
            request_schema_id=None,
            response_schema_id=None,
        )
        
        result = generate_response_mapping(endpoint, None, [])
        
        # Should infer common list fields
        field_names = {f.target for f in result.extract_fields}
        assert "id" in field_names
        assert "data" in field_names
