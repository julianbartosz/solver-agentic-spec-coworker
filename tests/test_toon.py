"""
Tests for TOON (Token-Oriented Object Notation) serialization.

TOON is a token-efficient format for structured data sent to LLMs.
"""
import pytest
from integration_coworker.llm.toon import (
    to_toon,
    from_toon,
    toon_schema_hint,
    toon_response_format,
)


# Mark all tests in this module to skip database setup
pytestmark = pytest.mark.no_db


class TestToToon:
    """Tests for to_toon() serialization."""
    
    def test_simple_string(self):
        """Test basic string value."""
        result = to_toon({"name": "test"})
        assert result == "name=test"
    
    def test_integer(self):
        """Test integer value."""
        result = to_toon({"count": 42})
        assert result == "count=42"
    
    def test_float(self):
        """Test float value."""
        result = to_toon({"price": 19.99})
        assert result == "price=19.99"
    
    def test_boolean_true(self):
        """Test boolean true value."""
        result = to_toon({"enabled": True})
        assert result == "enabled=true"
    
    def test_boolean_false(self):
        """Test boolean false value."""
        result = to_toon({"enabled": False})
        assert result == "enabled=false"
    
    def test_none_skipped(self):
        """Test that None values are skipped."""
        result = to_toon({"name": "test", "value": None})
        assert result == "name=test"
    
    def test_nested_dict(self):
        """Test nested dictionary."""
        result = to_toon({"config": {"enabled": True, "timeout": 30}})
        lines = result.split("\n")
        assert "config.enabled=true" in lines
        assert "config.timeout=30" in lines
    
    def test_simple_array(self):
        """Test simple array."""
        result = to_toon({"tags": ["a", "b", "c"]})
        assert result == "tags=[a,b,c]"
    
    def test_empty_array(self):
        """Test empty array."""
        result = to_toon({"items": []})
        assert result == "items=[]"
    
    def test_array_of_objects(self):
        """Test array of objects."""
        result = to_toon({
            "operations": [
                {"method": "GET", "path": "/api"},
                {"method": "POST", "path": "/api"}
            ]
        })
        assert "operations=[{method:GET,path:/api},{method:POST,path:/api}]" in result
    
    def test_multiple_fields(self):
        """Test multiple top-level fields."""
        result = to_toon({
            "task_slug": "create_session",
            "count": 5,
            "enabled": True
        })
        lines = result.split("\n")
        assert "task_slug=create_session" in lines
        assert "count=5" in lines
        assert "enabled=true" in lines
    
    def test_escape_newline(self):
        """Test that newlines in values are escaped."""
        result = to_toon({"text": "line1\nline2"})
        assert result == r"text=line1\nline2"
    
    def test_escape_equals(self):
        """Test that equals signs in values are escaped."""
        result = to_toon({"equation": "x=5"})
        assert result == r"equation=x\=5"


class TestFromToon:
    """Tests for from_toon() parsing."""
    
    def test_simple_string(self):
        """Test parsing simple string."""
        result = from_toon("name=test")
        assert result == {"name": "test"}
    
    def test_integer(self):
        """Test parsing integer."""
        result = from_toon("count=42")
        assert result == {"count": 42}
    
    def test_float(self):
        """Test parsing float."""
        result = from_toon("price=19.99")
        assert result == {"price": 19.99}
    
    def test_boolean_true(self):
        """Test parsing boolean true."""
        result = from_toon("enabled=true")
        assert result == {"enabled": True}
    
    def test_boolean_false(self):
        """Test parsing boolean false."""
        result = from_toon("enabled=false")
        assert result == {"enabled": False}
    
    def test_empty_value(self):
        """Test parsing empty value as None."""
        result = from_toon("value=")
        assert result == {"value": None}
    
    def test_nested_key(self):
        """Test parsing nested key with dot notation."""
        result = from_toon("config.enabled=true")
        assert result == {"config": {"enabled": True}}
    
    def test_deeply_nested(self):
        """Test parsing deeply nested keys."""
        result = from_toon("a.b.c=value")
        assert result == {"a": {"b": {"c": "value"}}}
    
    def test_simple_array(self):
        """Test parsing simple array."""
        result = from_toon("tags=[a,b,c]")
        assert result == {"tags": ["a", "b", "c"]}
    
    def test_empty_array(self):
        """Test parsing empty array."""
        result = from_toon("items=[]")
        assert result == {"items": []}
    
    def test_array_of_objects(self):
        """Test parsing array of objects."""
        result = from_toon("ops=[{method:GET,path:/api},{method:POST,path:/data}]")
        assert result == {
            "ops": [
                {"method": "GET", "path": "/api"},
                {"method": "POST", "path": "/data"}
            ]
        }
    
    def test_multiline(self):
        """Test parsing multiline TOON."""
        toon = """task_slug=create_session
count=5
enabled=true"""
        result = from_toon(toon)
        assert result == {
            "task_slug": "create_session",
            "count": 5,
            "enabled": True
        }
    
    def test_skip_comments(self):
        """Test that comment lines are skipped."""
        toon = """# This is a comment
name=test
# Another comment
value=123"""
        result = from_toon(toon)
        assert result == {"name": "test", "value": 123}
    
    def test_skip_empty_lines(self):
        """Test that empty lines are skipped."""
        toon = """name=test

value=123"""
        result = from_toon(toon)
        assert result == {"name": "test", "value": 123}
    
    def test_unescape_newline(self):
        """Test unescaping newlines."""
        result = from_toon(r"text=line1\nline2")
        assert result == {"text": "line1\nline2"}
    
    def test_unescape_equals(self):
        """Test unescaping equals signs."""
        result = from_toon(r"equation=x\=5")
        assert result == {"equation": "x=5"}


class TestRoundTrip:
    """Tests for to_toon() -> from_toon() round-trip."""
    
    def test_simple_roundtrip(self):
        """Test simple object round-trip."""
        original = {"name": "test", "count": 42}
        toon = to_toon(original)
        result = from_toon(toon)
        assert result == original
    
    def test_nested_roundtrip(self):
        """Test nested object round-trip."""
        original = {
            "task_slug": "create_session",
            "constraints": {
                "idempotency_required": True,
                "requires_webhooks": False
            }
        }
        toon = to_toon(original)
        result = from_toon(toon)
        assert result == original
    
    def test_array_roundtrip(self):
        """Test array round-trip."""
        original = {"entities": ["User", "Order", "Product"]}
        toon = to_toon(original)
        result = from_toon(toon)
        assert result == original


class TestSchemaHints:
    """Tests for schema hint helpers."""
    
    def test_toon_schema_hint(self):
        """Test generating schema hint."""
        result = toon_schema_hint({"name": "string", "count": "int"})
        assert result == "{name:string,count:int}"
    
    def test_toon_response_format(self):
        """Test generating response format specification."""
        result = toon_response_format(
            {"task_slug": "snake_case_name", "count": "int"},
            {"constraints": {"required": "bool", "timeout": "int"}}
        )
        lines = result.split("\n")
        assert "task_slug=snake_case_name" in lines
        assert "count=int" in lines
        assert "constraints.required=bool" in lines
        assert "constraints.timeout=int" in lines


class TestRealWorldExamples:
    """Tests with real-world TOON examples from understand_task."""
    
    def test_understand_task_response(self):
        """Test parsing a realistic understand_task response."""
        toon = """task_slug=create_checkout_session
input_entities=[Customer,Product]
output_entities=[CheckoutSession]
constraints.idempotency_required=true
constraints.requires_webhooks=false
target_operations=[{operation_id:createCheckoutSession,method:POST,path:/v1/checkout/sessions,reason:main endpoint for task}]"""
        
        result = from_toon(toon)
        
        assert result["task_slug"] == "create_checkout_session"
        assert result["input_entities"] == ["Customer", "Product"]
        assert result["output_entities"] == ["CheckoutSession"]
        assert result["constraints"]["idempotency_required"] is True
        assert result["constraints"]["requires_webhooks"] is False
        assert len(result["target_operations"]) == 1
        assert result["target_operations"][0]["method"] == "POST"
    
    def test_plan_integration_response(self):
        """Test parsing a realistic plan_integration response."""
        toon = """request_mapping.description=Maps payload to checkout session request
request_mapping.path_params=[]
request_mapping.query_params=[]
request_mapping.body_fields=[customer,line_items,success_url,cancel_url]
response_mapping.description=Extracts session ID and URL
response_mapping.extract_fields=[id,url,status]"""
        
        result = from_toon(toon)
        
        assert "request_mapping" in result
        assert result["request_mapping"]["body_fields"] == ["customer", "line_items", "success_url", "cancel_url"]
        assert "response_mapping" in result
        assert result["response_mapping"]["extract_fields"] == ["id", "url", "status"]
