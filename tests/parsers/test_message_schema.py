"""
Tests for message_schema module.

Tests JSON Schema, Avro, and YAML topic descriptor parsing.
"""
import pytest
from integration_coworker.parsers.message_schema import (
    parse_message_schema,
    parse_json_schema,
    parse_avro_schema,
    parse_yaml_topic_descriptor,
    MessageSchema,
    MessageSchemaField,
    TopicDescriptor,
)


@pytest.mark.no_db
class TestParseJsonSchema:
    """Test JSON Schema parsing."""
    
    def test_parse_simple_json_schema(self):
        """Test parsing a simple JSON Schema."""
        schema_json = {
            "title": "Person",
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"}
            },
            "required": ["name"]
        }
        
        schema = parse_json_schema(schema_json)
        
        assert schema.name == "Person"
        assert schema.schema_type == "json_schema"
        assert len(schema.fields) == 2
        
        name_field = next(f for f in schema.fields if f.name == "name")
        assert name_field.type == "string"
        assert name_field.required is True
        
        age_field = next(f for f in schema.fields if f.name == "age")
        assert age_field.required is False
    
    def test_parse_json_schema_from_string(self):
        """Test parsing JSON Schema from string."""
        schema_str = '{"title": "Test", "properties": {"id": {"type": "integer"}}}'
        
        schema = parse_json_schema(schema_str)
        
        assert schema.name == "Test"
        assert len(schema.fields) == 1
    
    def test_parse_json_schema_with_formats(self):
        """Test parsing JSON Schema with format specifiers."""
        schema_json = {
            "properties": {
                "email": {"type": "string", "format": "email"},
                "created_at": {"type": "string", "format": "date-time"},
                "birth_date": {"type": "string", "format": "date"}
            }
        }
        
        schema = parse_json_schema(schema_json)
        
        email_field = next(f for f in schema.fields if f.name == "email")
        assert email_field.type == "string:email"
        
        created_field = next(f for f in schema.fields if f.name == "created_at")
        assert created_field.type == "string:date-time"
    
    def test_parse_json_schema_with_description(self):
        """Test parsing JSON Schema with descriptions."""
        schema_json = {
            "title": "User",
            "description": "A user account",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Unique identifier"
                }
            }
        }
        
        schema = parse_json_schema(schema_json)
        
        assert schema.description == "A user account"
        id_field = next(f for f in schema.fields if f.name == "id")
        assert id_field.description == "Unique identifier"
    
    def test_parse_json_schema_with_defaults(self):
        """Test parsing JSON Schema with default values."""
        schema_json = {
            "properties": {
                "active": {
                    "type": "boolean",
                    "default": True
                }
            }
        }
        
        schema = parse_json_schema(schema_json)
        
        active_field = next(f for f in schema.fields if f.name == "active")
        assert active_field.default is True
    
    def test_parse_json_schema_nullable_type(self):
        """Test parsing JSON Schema with nullable types."""
        schema_json = {
            "properties": {
                "nickname": {"type": ["string", "null"]}
            }
        }
        
        schema = parse_json_schema(schema_json)
        
        nickname_field = next(f for f in schema.fields if f.name == "nickname")
        assert nickname_field.type == "string"
    
    def test_parse_json_schema_nested_object(self):
        """Test parsing JSON Schema with nested objects."""
        schema_json = {
            "properties": {
                "address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string"},
                        "city": {"type": "string"}
                    },
                    "required": ["city"]
                }
            }
        }
        
        schema = parse_json_schema(schema_json)
        
        address_field = next(f for f in schema.fields if f.name == "address")
        assert address_field.type == "object"
        assert len(address_field.nested_fields) == 2
        
        city_field = next(f for f in address_field.nested_fields if f.name == "city")
        assert city_field.required is True
    
    def test_parse_json_schema_array_with_items(self):
        """Test parsing JSON Schema with array items."""
        schema_json = {
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            }
        }
        
        schema = parse_json_schema(schema_json)
        
        tags_field = next(f for f in schema.fields if f.name == "tags")
        assert tags_field.type == "array"
    
    def test_parse_invalid_json_string(self):
        """Test parsing invalid JSON string."""
        schema = parse_json_schema("not valid json")
        
        assert len(schema.errors) > 0


@pytest.mark.no_db
class TestParseAvroSchema:
    """Test Avro schema parsing."""
    
    def test_parse_simple_avro_schema(self):
        """Test parsing a simple Avro schema."""
        avro_schema = {
            "type": "record",
            "name": "User",
            "namespace": "com.example",
            "fields": [
                {"name": "id", "type": "long"},
                {"name": "name", "type": "string"}
            ]
        }
        
        schema = parse_avro_schema(avro_schema)
        
        assert schema.name == "User"
        assert schema.schema_type == "avro"
        assert schema.namespace == "com.example"
        assert len(schema.fields) == 2
    
    def test_parse_avro_from_string(self):
        """Test parsing Avro schema from string."""
        avro_str = '{"type": "record", "name": "Test", "fields": [{"name": "id", "type": "int"}]}'
        
        schema = parse_avro_schema(avro_str)
        
        assert schema.name == "Test"
        assert len(schema.fields) == 1
    
    def test_parse_avro_nullable_field(self):
        """Test parsing Avro schema with nullable (union) field."""
        avro_schema = {
            "type": "record",
            "name": "Person",
            "fields": [
                {"name": "middle_name", "type": ["null", "string"]}
            ]
        }
        
        schema = parse_avro_schema(avro_schema)
        
        middle_name_field = next(f for f in schema.fields if f.name == "middle_name")
        assert middle_name_field.required is False
        assert middle_name_field.type == "string"
    
    def test_parse_avro_with_doc(self):
        """Test parsing Avro schema with doc fields."""
        avro_schema = {
            "type": "record",
            "name": "Event",
            "doc": "An event record",
            "fields": [
                {"name": "timestamp", "type": "long", "doc": "Event timestamp"}
            ]
        }
        
        schema = parse_avro_schema(avro_schema)
        
        assert schema.description == "An event record"
        timestamp_field = next(f for f in schema.fields if f.name == "timestamp")
        assert timestamp_field.description == "Event timestamp"
    
    def test_parse_avro_with_default(self):
        """Test parsing Avro schema with default values."""
        avro_schema = {
            "type": "record",
            "name": "Config",
            "fields": [
                {"name": "enabled", "type": "boolean", "default": True}
            ]
        }
        
        schema = parse_avro_schema(avro_schema)
        
        enabled_field = next(f for f in schema.fields if f.name == "enabled")
        assert enabled_field.default is True
    
    def test_parse_avro_array_type(self):
        """Test parsing Avro array type."""
        avro_schema = {
            "type": "record",
            "name": "Container",
            "fields": [
                {"name": "items", "type": {"type": "array", "items": "string"}}
            ]
        }
        
        schema = parse_avro_schema(avro_schema)
        
        items_field = next(f for f in schema.fields if f.name == "items")
        assert "array" in items_field.type
    
    def test_parse_avro_map_type(self):
        """Test parsing Avro map type."""
        avro_schema = {
            "type": "record",
            "name": "Metadata",
            "fields": [
                {"name": "properties", "type": {"type": "map", "values": "string"}}
            ]
        }
        
        schema = parse_avro_schema(avro_schema)
        
        props_field = next(f for f in schema.fields if f.name == "properties")
        assert "map" in props_field.type
    
    def test_parse_avro_enum_type(self):
        """Test parsing Avro enum type."""
        avro_schema = {
            "type": "record",
            "name": "Order",
            "fields": [
                {
                    "name": "status",
                    "type": {
                        "type": "enum",
                        "name": "OrderStatus",
                        "symbols": ["PENDING", "SHIPPED", "DELIVERED"]
                    }
                }
            ]
        }
        
        schema = parse_avro_schema(avro_schema)
        
        status_field = next(f for f in schema.fields if f.name == "status")
        assert status_field.type == "OrderStatus"


@pytest.mark.no_db
class TestParseMessageSchema:
    """Test auto-detection of schema format."""
    
    def test_auto_detect_json_schema(self):
        """Test auto-detection of JSON Schema."""
        schema_dict = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"id": {"type": "integer"}}
        }
        
        schema = parse_message_schema(schema_dict)
        
        assert schema.schema_type == "json_schema"
    
    def test_auto_detect_avro_schema(self):
        """Test auto-detection of Avro schema."""
        schema_dict = {
            "type": "record",
            "name": "Test",
            "fields": [{"name": "id", "type": "int"}]
        }
        
        schema = parse_message_schema(schema_dict)
        
        assert schema.schema_type == "avro"
    
    def test_auto_detect_from_string(self):
        """Test auto-detection from JSON string."""
        schema_str = '{"type": "record", "name": "Test", "fields": []}'
        
        schema = parse_message_schema(schema_str)
        
        assert schema.schema_type == "avro"


@pytest.mark.no_db
class TestParseYamlTopicDescriptor:
    """Test YAML topic descriptor parsing."""
    
    def test_parse_simple_topic_descriptor(self):
        """Test parsing a simple topic descriptor."""
        yaml_content = """
topic: orders
producer: order-service
consumers:
  - analytics-service
  - notification-service
partitions: 12
"""
        
        descriptor = parse_yaml_topic_descriptor(yaml_content)
        
        assert descriptor.topic_name == "orders"
        assert descriptor.producer == "order-service"
        assert len(descriptor.consumers) == 2
        assert descriptor.partitions == 12
    
    def test_parse_topic_with_schema(self):
        """Test parsing topic descriptor with embedded schema."""
        yaml_content = """
topic: events
schema:
  type: record
  name: Event
  fields:
    - name: id
      type: string
"""
        
        descriptor = parse_yaml_topic_descriptor(yaml_content)
        
        assert descriptor.topic_name == "events"
        assert descriptor.message_schema is not None
        assert descriptor.message_schema.name == "Event"
    
    def test_parse_topic_with_retention(self):
        """Test parsing topic with retention config."""
        yaml_content = """
topic: logs
retention_ms: 86400000
replication_factor: 3
"""
        
        descriptor = parse_yaml_topic_descriptor(yaml_content)
        
        assert descriptor.retention_ms == 86400000
        assert descriptor.replication_factor == 3
    
    def test_parse_topic_with_config(self):
        """Test parsing topic with custom config."""
        yaml_content = """
topic: custom
config:
  compression.type: gzip
  cleanup.policy: compact
"""
        
        descriptor = parse_yaml_topic_descriptor(yaml_content)
        
        assert descriptor.config.get("compression.type") == "gzip"
        assert descriptor.config.get("cleanup.policy") == "compact"
    
    def test_parse_topic_name_variants(self):
        """Test parsing with different topic name keys."""
        for key in ["topic", "name", "topic_name"]:
            yaml_content = f"{key}: test-topic"
            descriptor = parse_yaml_topic_descriptor(yaml_content)
            assert descriptor.topic_name == "test-topic"
    
    def test_parse_invalid_yaml(self):
        """Test parsing invalid YAML."""
        yaml_content = "not: valid: yaml: here"
        
        descriptor = parse_yaml_topic_descriptor(yaml_content)
        
        # Should have errors or parse as best effort
        assert isinstance(descriptor, TopicDescriptor)


@pytest.mark.no_db
class TestMessageSchemaDataclass:
    """Test MessageSchema dataclass."""
    
    def test_create_message_schema(self):
        """Test creating a MessageSchema."""
        schema = MessageSchema(
            name="TestSchema",
            schema_type="json_schema",
            description="A test schema"
        )
        
        assert schema.name == "TestSchema"
        assert schema.schema_type == "json_schema"
        assert schema.description == "A test schema"
    
    def test_message_schema_defaults(self):
        """Test MessageSchema default values."""
        schema = MessageSchema(name="Test", schema_type="avro")
        
        assert schema.fields == []
        assert schema.version is None
        assert schema.namespace is None
        assert schema.errors == []


@pytest.mark.no_db
class TestMessageSchemaFieldDataclass:
    """Test MessageSchemaField dataclass."""
    
    def test_create_message_schema_field(self):
        """Test creating a MessageSchemaField."""
        field = MessageSchemaField(
            name="id",
            type="integer",
            description="Unique ID",
            required=True
        )
        
        assert field.name == "id"
        assert field.type == "integer"
        assert field.description == "Unique ID"
        assert field.required is True
    
    def test_message_schema_field_defaults(self):
        """Test MessageSchemaField default values."""
        field = MessageSchemaField(name="test", type="string")
        
        assert field.description is None
        assert field.required is False
        assert field.default is None
        assert field.nested_fields == []


@pytest.mark.no_db
class TestTopicDescriptorDataclass:
    """Test TopicDescriptor dataclass."""
    
    def test_create_topic_descriptor(self):
        """Test creating a TopicDescriptor."""
        descriptor = TopicDescriptor(
            topic_name="my-topic",
            producer="producer-app",
            consumers=["consumer-1", "consumer-2"]
        )
        
        assert descriptor.topic_name == "my-topic"
        assert descriptor.producer == "producer-app"
        assert len(descriptor.consumers) == 2
    
    def test_topic_descriptor_defaults(self):
        """Test TopicDescriptor default values."""
        descriptor = TopicDescriptor(topic_name="test")
        
        assert descriptor.message_schema is None
        assert descriptor.producer is None
        assert descriptor.consumers == []
        assert descriptor.retention_ms is None
        assert descriptor.partitions is None
        assert descriptor.config == {}
        assert descriptor.errors == []
