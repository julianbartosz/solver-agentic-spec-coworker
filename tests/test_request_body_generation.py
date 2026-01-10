"""Unit tests for V38-006 request body example generation - spec-agnostic validation."""

import pytest
from integration_coworker.codegen.prompts import (
    _resolve_schema_with_allof,
    _extract_request_example,
    _generate_minimal_example,
    _get_example_value,
    _format_required_structure,
)


class TestResolveSchemaWithAllof:
    """Test schema resolution for different spec patterns."""
    
    def test_simple_object_schema(self):
        """Test resolving a simple object schema without refs."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        spec = {"components": {"schemas": {}}}
        
        resolved = _resolve_schema_with_allof(schema, spec)
        
        assert resolved["type"] == "object"
        assert "name" in resolved["properties"]
        assert "age" in resolved["properties"]
        assert "name" in resolved["required"]
    
    def test_ref_resolution(self):
        """Test $ref resolution."""
        schema = {"$ref": "#/components/schemas/User"}
        spec = {
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                    }
                }
            }
        }
        
        resolved = _resolve_schema_with_allof(schema, spec)
        
        assert "id" in resolved["properties"]
        assert "id" in resolved["required"]
    
    def test_allof_merging(self):
        """Test allOf schema merging (like OpenAI uses)."""
        schema = {
            "allOf": [
                {"$ref": "#/components/schemas/BaseRequest"},
                {
                    "type": "object",
                    "properties": {"messages": {"type": "array"}},
                    "required": ["messages"],
                },
            ]
        }
        spec = {
            "components": {
                "schemas": {
                    "BaseRequest": {
                        "type": "object",
                        "properties": {"model": {"type": "string"}},
                        "required": ["model"],
                    }
                }
            }
        }
        
        resolved = _resolve_schema_with_allof(schema, spec)
        
        # Both base and extended properties should be present
        assert "model" in resolved["properties"]
        assert "messages" in resolved["properties"]
        # Both required fields should be merged
        assert "model" in resolved["required"]
        assert "messages" in resolved["required"]
    
    def test_circular_ref_protection(self):
        """Test that circular refs don't cause infinite recursion."""
        schema = {"$ref": "#/components/schemas/Node"}
        spec = {
            "components": {
                "schemas": {
                    "Node": {
                        "type": "object",
                        "properties": {
                            "child": {"$ref": "#/components/schemas/Node"}
                        },
                    }
                }
            }
        }
        
        # Should not hang or raise
        resolved = _resolve_schema_with_allof(schema, spec)
        assert resolved is not None


class TestExtractRequestExample:
    """Test example extraction from various spec formats."""
    
    def test_standard_openapi_example(self):
        """Test extraction from standard OpenAPI example field."""
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "example": {"name": "Test User", "email": "test@example.com"}
                    }
                }
            }
        }
        spec = {}
        
        example = _extract_request_example(operation, spec)
        
        assert example == {"name": "Test User", "email": "test@example.com"}
    
    def test_openapi_examples_collection(self):
        """Test extraction from OpenAPI examples collection."""
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "examples": {
                            "default": {
                                "value": {"status": "active"}
                            }
                        }
                    }
                }
            }
        }
        spec = {}
        
        example = _extract_request_example(operation, spec)
        
        assert example == {"status": "active"}
    
    def test_vendor_extension_example(self):
        """Test extraction from vendor extension (x-oaiMeta style)."""
        operation = {
            "x-oaiMeta": {
                "examples": {
                    "request": {
                        "body": {"model": "gpt-4", "messages": []}
                    }
                }
            },
            "requestBody": {
                "content": {"application/json": {}}
            }
        }
        spec = {}
        
        example = _extract_request_example(operation, spec)
        
        assert example == {"model": "gpt-4", "messages": []}
    
    def test_fallback_to_schema_generation(self):
        """Test fallback to generating example from schema."""
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "count": {"type": "integer"},
                            },
                            "required": ["name"],
                        }
                    }
                }
            }
        }
        spec = {"components": {"schemas": {}}}
        
        example = _extract_request_example(operation, spec)
        
        # Should generate example with required field
        assert "name" in example
        assert isinstance(example["name"], str)
    
    def test_no_request_body(self):
        """Test handling when no request body exists."""
        operation = {"operationId": "listItems"}
        spec = {}
        
        example = _extract_request_example(operation, spec)
        
        assert example is None


class TestGetExampleValue:
    """Test example value generation is spec-agnostic."""
    
    def test_uses_explicit_example(self):
        """Test that explicit examples in schema are preferred."""
        prop = {"type": "string", "example": "my-custom-value"}
        
        value = _get_example_value(prop, {}, "field")
        
        assert value == "my-custom-value"
    
    def test_uses_enum_first_value(self):
        """Test that enum values are used when available."""
        prop = {"type": "string", "enum": ["active", "inactive", "pending"]}
        
        value = _get_example_value(prop, {}, "status")
        
        assert value == "active"
    
    def test_uses_default_value(self):
        """Test that default values are used."""
        prop = {"type": "integer", "default": 100}
        
        value = _get_example_value(prop, {}, "count")
        
        assert value == 100
    
    def test_generic_string_placeholder(self):
        """Test that string fields get generic placeholders, not hardcoded values."""
        prop = {"type": "string"}
        
        value = _get_example_value(prop, {}, "model")
        
        # Should NOT be hardcoded to a specific model like "gpt-4o"
        assert value == "example_model"
    
    def test_id_field_placeholder(self):
        """Test that id fields get appropriate placeholders."""
        prop = {"type": "string"}
        
        value = _get_example_value(prop, {}, "user_id")
        
        assert "id" in value.lower()
    
    def test_array_generates_single_item(self):
        """Test that arrays generate a single item example."""
        prop = {
            "type": "array",
            "items": {"type": "string"}
        }
        
        value = _get_example_value(prop, {}, "tags")
        
        assert isinstance(value, list)
        assert len(value) == 1
    
    def test_nested_object(self):
        """Test nested object generation."""
        prop = {
            "type": "object",
            "properties": {
                "street": {"type": "string"},
                "city": {"type": "string"},
            },
            "required": ["city"],
        }
        
        value = _get_example_value(prop, {}, "address")
        
        assert isinstance(value, dict)
        assert "city" in value  # Only required fields


class TestFormatRequiredStructure:
    """Test required structure formatting."""
    
    def test_shows_required_fields(self):
        """Test that required fields are clearly marked."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name", "email"],
        }
        spec = {"components": {"schemas": {}}}
        
        result = _format_required_structure(schema, spec)
        
        assert "REQUIRED" in result
        assert "name" in result
        assert "email" in result
    
    def test_handles_no_required_fields(self):
        """Test handling when no fields are required."""
        schema = {
            "type": "object",
            "properties": {"optional": {"type": "string"}},
        }
        spec = {}
        
        result = _format_required_structure(schema, spec)
        
        assert "No required fields" in result
    
    def test_shows_array_type_clearly(self):
        """Test that array types are shown clearly."""
        schema = {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/Message"}
                }
            },
            "required": ["messages"],
        }
        spec = {"components": {"schemas": {"Message": {"type": "object"}}}}
        
        result = _format_required_structure(schema, spec)
        
        assert "messages" in result
        assert "array" in result.lower()


class TestEndpointScoringDynamic:
    """Test that endpoint scoring works for any spec."""
    
    def test_scoring_uses_spec_metadata_not_hardcoded_lists(self):
        """Verify scoring logic doesn't use hardcoded resource lists."""
        # Import the function to inspect
        import inspect
        from integration_coworker.graph.nodes.understand_task import _build_understand_task_prompt
        
        source = inspect.getsource(_build_understand_task_prompt)
        
        # Should NOT contain hardcoded Stripe-specific resources
        assert "checkout" not in source.lower() or "common_resources" not in source
        assert "stripe" not in source.lower()
        assert "payment" not in source.lower() or "common_resources" not in source
        
        # Should use dynamic word extraction
        assert "task_words" in source
