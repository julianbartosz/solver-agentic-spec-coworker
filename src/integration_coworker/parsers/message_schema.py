"""
Message schema parser module.

Implements: V1 Gap Closure Plan P5 - CSV/EDI/Message Specs
Parses JSON Schema, Avro, and YAML topic descriptors.
"""
import json
from dataclasses import dataclass, field
from typing import Optional, Any, Union

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class MessageSchemaField:
    """A field in a message schema."""
    name: str
    type: str
    description: Optional[str] = None
    required: bool = False
    default: Optional[Any] = None
    nested_fields: list["MessageSchemaField"] = field(default_factory=list)


@dataclass
class MessageSchema:
    """Parsed message schema from JSON Schema, Avro, or YAML."""
    name: str
    schema_type: str  # "json_schema", "avro", "yaml_topic"
    description: Optional[str] = None
    fields: list[MessageSchemaField] = field(default_factory=list)
    version: Optional[str] = None
    namespace: Optional[str] = None
    raw_schema: Optional[dict] = None
    errors: list[str] = field(default_factory=list)


@dataclass
class TopicDescriptor:
    """Kafka/SQS topic descriptor from YAML."""
    topic_name: str
    message_schema: Optional[MessageSchema] = None
    producer: Optional[str] = None
    consumers: list[str] = field(default_factory=list)
    retention_ms: Optional[int] = None
    partitions: Optional[int] = None
    replication_factor: Optional[int] = None
    config: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def parse_message_schema(content: Union[str, dict]) -> MessageSchema:
    """
    Parse a message schema from JSON Schema or Avro format.
    
    Automatically detects the schema format and parses accordingly.
    
    Args:
        content: Schema as string (JSON/YAML) or dict
        
    Returns:
        MessageSchema with parsed field information
    """
    if isinstance(content, str):
        try:
            schema_dict = json.loads(content)
        except json.JSONDecodeError:
            if HAS_YAML:
                try:
                    schema_dict = yaml.safe_load(content)
                except Exception as e:
                    return MessageSchema(
                        name="unknown",
                        schema_type="unknown",
                        errors=[f"Failed to parse schema: {str(e)}"]
                    )
            else:
                return MessageSchema(
                    name="unknown",
                    schema_type="unknown",
                    errors=["Failed to parse JSON and YAML not available"]
                )
    else:
        schema_dict = content

    if not isinstance(schema_dict, dict):
        return MessageSchema(
            name="unknown",
            schema_type="unknown",
            errors=["Schema must be a dictionary"]
        )

    # Detect schema type
    if "type" in schema_dict and schema_dict.get("type") == "record":
        # Avro schema
        return _parse_avro_schema(schema_dict)
    elif "$schema" in schema_dict or "properties" in schema_dict or "type" in schema_dict:
        # JSON Schema
        return _parse_json_schema(schema_dict)
    else:
        return MessageSchema(
            name=schema_dict.get("name", "unknown"),
            schema_type="unknown",
            raw_schema=schema_dict,
            errors=["Unknown schema format"]
        )


def parse_json_schema(content: Union[str, dict]) -> MessageSchema:
    """
    Parse a JSON Schema.
    
    Args:
        content: JSON Schema as string or dict
        
    Returns:
        MessageSchema with parsed field information
    """
    if isinstance(content, str):
        try:
            schema_dict = json.loads(content)
        except json.JSONDecodeError as e:
            return MessageSchema(
                name="unknown",
                schema_type="json_schema",
                errors=[f"Invalid JSON: {str(e)}"]
            )
    else:
        schema_dict = content

    return _parse_json_schema(schema_dict)


def parse_avro_schema(content: Union[str, dict]) -> MessageSchema:
    """
    Parse an Avro schema.
    
    Args:
        content: Avro schema as string or dict
        
    Returns:
        MessageSchema with parsed field information
    """
    if isinstance(content, str):
        try:
            schema_dict = json.loads(content)
        except json.JSONDecodeError as e:
            return MessageSchema(
                name="unknown",
                schema_type="avro",
                errors=[f"Invalid JSON: {str(e)}"]
            )
    else:
        schema_dict = content

    return _parse_avro_schema(schema_dict)


def parse_yaml_topic_descriptor(content: str) -> TopicDescriptor:
    """
    Parse a YAML topic descriptor for Kafka/SQS.
    
    Args:
        content: YAML content string
        
    Returns:
        TopicDescriptor with parsed configuration
    """
    if not HAS_YAML:
        return TopicDescriptor(
            topic_name="unknown",
            errors=["PyYAML not installed. Install with: pip install pyyaml"]
        )

    try:
        data = yaml.safe_load(content)
    except Exception as e:
        return TopicDescriptor(
            topic_name="unknown",
            errors=[f"Failed to parse YAML: {str(e)}"]
        )

    if not isinstance(data, dict):
        return TopicDescriptor(
            topic_name="unknown",
            errors=["YAML must be a dictionary"]
        )

    topic_name = data.get("topic", data.get("name", data.get("topic_name", "unknown")))

    descriptor = TopicDescriptor(
        topic_name=topic_name,
        producer=data.get("producer"),
        consumers=data.get("consumers", []),
        retention_ms=data.get("retention_ms", data.get("retention")),
        partitions=data.get("partitions"),
        replication_factor=data.get("replication_factor", data.get("replication")),
        config=data.get("config", {}),
    )

    # Parse message schema if present
    schema_data = data.get("schema", data.get("message_schema"))
    if schema_data:
        descriptor.message_schema = parse_message_schema(schema_data)

    return descriptor


def _parse_json_schema(schema: dict) -> MessageSchema:
    """Parse a JSON Schema dictionary."""
    result = MessageSchema(
        name=schema.get("title", schema.get("$id", "unnamed")),
        schema_type="json_schema",
        description=schema.get("description"),
        version=schema.get("$schema"),
        raw_schema=schema,
    )

    # Parse properties
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    for name, prop_schema in properties.items():
        field = _json_schema_property_to_field(name, prop_schema, name in required)
        result.fields.append(field)

    return result


def _json_schema_property_to_field(
    name: str,
    prop_schema: dict,
    required: bool = False
) -> MessageSchemaField:
    """Convert a JSON Schema property to a MessageSchemaField."""
    # Handle type (could be string or array for nullable)
    type_value = prop_schema.get("type", "any")
    if isinstance(type_value, list):
        # Filter out null for display
        type_value = [t for t in type_value if t != "null"]
        type_value = type_value[0] if type_value else "any"

    # Add format if present
    if prop_schema.get("format"):
        type_value = f"{type_value}:{prop_schema['format']}"

    field = MessageSchemaField(
        name=name,
        type=type_value,
        description=prop_schema.get("description"),
        required=required,
        default=prop_schema.get("default"),
    )

    # Handle nested objects
    if type_value == "object" and "properties" in prop_schema:
        nested_required = set(prop_schema.get("required", []))
        for nested_name, nested_schema in prop_schema["properties"].items():
            nested_field = _json_schema_property_to_field(
                nested_name,
                nested_schema,
                nested_name in nested_required
            )
            field.nested_fields.append(nested_field)

    # Handle array items
    if type_value == "array" and "items" in prop_schema:
        items_schema = prop_schema["items"]
        if isinstance(items_schema, dict) and items_schema.get("type") == "object":
            items_required = set(items_schema.get("required", []))
            for nested_name, nested_schema in items_schema.get("properties", {}).items():
                nested_field = _json_schema_property_to_field(
                    nested_name,
                    nested_schema,
                    nested_name in items_required
                )
                field.nested_fields.append(nested_field)

    return field


def _parse_avro_schema(schema: dict) -> MessageSchema:
    """Parse an Avro schema dictionary."""
    result = MessageSchema(
        name=schema.get("name", "unnamed"),
        schema_type="avro",
        description=schema.get("doc"),
        namespace=schema.get("namespace"),
        raw_schema=schema,
    )

    # Parse fields for record types
    if schema.get("type") == "record":
        for avro_field in schema.get("fields", []):
            field = _avro_field_to_message_field(avro_field)
            result.fields.append(field)

    return result


def _avro_field_to_message_field(avro_field: dict) -> MessageSchemaField:
    """Convert an Avro field to a MessageSchemaField."""
    name = avro_field.get("name", "unnamed")
    avro_type = avro_field.get("type")

    # Handle union types (nullable)
    type_str = _avro_type_to_string(avro_type)
    required = not _is_nullable_avro_type(avro_type)

    field = MessageSchemaField(
        name=name,
        type=type_str,
        description=avro_field.get("doc"),
        required=required,
        default=avro_field.get("default"),
    )

    # Handle nested record types
    if isinstance(avro_type, dict) and avro_type.get("type") == "record":
        for nested_avro in avro_type.get("fields", []):
            nested_field = _avro_field_to_message_field(nested_avro)
            field.nested_fields.append(nested_field)

    # Handle union with record
    if isinstance(avro_type, list):
        for union_type in avro_type:
            if isinstance(union_type, dict) and union_type.get("type") == "record":
                for nested_avro in union_type.get("fields", []):
                    nested_field = _avro_field_to_message_field(nested_avro)
                    field.nested_fields.append(nested_field)

    return field


def _avro_type_to_string(avro_type: Any) -> str:
    """Convert Avro type to string representation."""
    if isinstance(avro_type, str):
        return avro_type
    elif isinstance(avro_type, list):
        # Union type - filter out null
        types = [_avro_type_to_string(t) for t in avro_type if t != "null"]
        return types[0] if types else "null"
    elif isinstance(avro_type, dict):
        type_name = avro_type.get("type", "unknown")
        if type_name == "record":
            return avro_type.get("name", "record")
        elif type_name == "array":
            items_type = _avro_type_to_string(avro_type.get("items", "unknown"))
            return f"array<{items_type}>"
        elif type_name == "map":
            values_type = _avro_type_to_string(avro_type.get("values", "unknown"))
            return f"map<{values_type}>"
        elif type_name == "enum":
            return avro_type.get("name", "enum")
        else:
            return type_name
    return "unknown"


def _is_nullable_avro_type(avro_type: Any) -> bool:
    """Check if Avro type is nullable (union with null)."""
    if isinstance(avro_type, list):
        return "null" in avro_type
    return False
