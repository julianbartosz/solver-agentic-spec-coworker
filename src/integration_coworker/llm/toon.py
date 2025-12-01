"""
TOON (Token-Oriented Object Notation) Serialization

A compact, token-efficient format for structured data sent to LLMs.
Reduces token usage by 25-40% compared to JSON by eliminating:
- Redundant quotes around keys
- Excessive whitespace
- Verbose punctuation

Format specification:
- key=value for simple values
- key.nested=value for nested objects
- key=[item1,item2] for arrays
- key=[{field1,field2},...] for arrays of objects (schema hint)
- bool values: true/false (no quotes)
- null represented as empty or omitted

Example:
    JSON: {"task_slug": "create_session", "constraints": {"required": true}}
    TOON: task_slug=create_session
          constraints.required=true
"""
import re
from typing import Any, Dict, List, Optional


def to_toon(obj: Dict[str, Any], prefix: str = "") -> str:
    """
    Convert a dictionary to TOON notation.
    
    Args:
        obj: Dictionary to serialize
        prefix: Key prefix for nested objects
    
    Returns:
        TOON-formatted string
    
    Example:
        >>> to_toon({"name": "test", "config": {"enabled": True}})
        'name=test\\nconfig.enabled=true'
    """
    lines = []

    for key, value in obj.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if value is None:
            # Skip None values (implicit null)
            continue
        elif isinstance(value, bool):
            lines.append(f"{full_key}={'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{full_key}={value}")
        elif isinstance(value, str):
            # Escape newlines and equals signs in values
            escaped = value.replace("\\", "\\\\").replace("\n", "\\n").replace("=", "\\=")
            lines.append(f"{full_key}={escaped}")
        elif isinstance(value, list):
            if not value:
                lines.append(f"{full_key}=[]")
            elif all(isinstance(item, dict) for item in value):
                # Array of objects - serialize each inline
                items = []
                for item in value:
                    item_parts = [f"{k}:{_format_value(v)}" for k, v in item.items() if v is not None]
                    items.append("{" + ",".join(item_parts) + "}")
                lines.append(f"{full_key}=[{','.join(items)}]")
            else:
                # Simple array
                formatted = [_format_value(item) for item in value]
                lines.append(f"{full_key}=[{','.join(formatted)}]")
        elif isinstance(value, dict):
            # Recursively handle nested dicts
            nested = to_toon(value, full_key)
            if nested:
                lines.append(nested)
        else:
            # Fallback: stringify
            lines.append(f"{full_key}={str(value)}")

    return "\n".join(lines)


def _format_value(value: Any) -> str:
    """Format a single value for TOON notation."""
    if value is None:
        return ""
    elif isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, (int, float)):
        return str(value)
    elif isinstance(value, str):
        # Escape special characters
        escaped = value.replace("\\", "\\\\").replace(",", "\\,").replace("]", "\\]")
        return escaped
    else:
        return str(value)


def from_toon(text: str) -> Dict[str, Any]:
    """
    Parse TOON notation back to a dictionary.
    
    Args:
        text: TOON-formatted string
    
    Returns:
        Parsed dictionary
    
    Example:
        >>> from_toon("name=test\\nconfig.enabled=true")
        {'name': 'test', 'config': {'enabled': True}}
    """
    result: Dict[str, Any] = {}

    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Find the first unescaped equals sign
        eq_pos = _find_unescaped(line, "=")
        if eq_pos == -1:
            continue

        key = line[:eq_pos]
        value_str = line[eq_pos + 1:]

        # Parse the value
        value = _parse_value(value_str)

        # Handle nested keys (dot notation)
        _set_nested(result, key.split("."), value)

    return result


def _find_unescaped(text: str, char: str) -> int:
    """Find the first unescaped occurrence of a character."""
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            i += 2  # Skip escaped character
            continue
        if text[i] == char:
            return i
        i += 1
    return -1


def _parse_value(value_str: str) -> Any:
    """Parse a TOON value string to Python type."""
    value_str = value_str.strip()

    # Boolean
    if value_str == "true":
        return True
    if value_str == "false":
        return False

    # Empty/null
    if value_str == "" or value_str == "null":
        return None

    # Array
    if value_str.startswith("[") and value_str.endswith("]"):
        return _parse_array(value_str[1:-1])

    # Number
    if re.match(r"^-?\d+$", value_str):
        return int(value_str)
    if re.match(r"^-?\d+\.\d+$", value_str):
        return float(value_str)

    # String - unescape
    return value_str.replace("\\n", "\n").replace("\\=", "=").replace("\\\\", "\\")


def _parse_array(array_str: str) -> List[Any]:
    """Parse a TOON array string."""
    if not array_str.strip():
        return []

    items = []
    current = ""
    depth = 0
    i = 0

    while i < len(array_str):
        char = array_str[i]

        # Handle escape sequences
        if char == "\\" and i + 1 < len(array_str):
            current += char + array_str[i + 1]
            i += 2
            continue

        # Track brace depth for nested objects
        if char == "{":
            depth += 1
            current += char
        elif char == "}":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            # Top-level comma - end of item
            items.append(_parse_array_item(current.strip()))
            current = ""
        else:
            current += char

        i += 1

    # Don't forget the last item
    if current.strip():
        items.append(_parse_array_item(current.strip()))

    return items


def _parse_array_item(item_str: str) -> Any:
    """Parse a single array item."""
    item_str = item_str.strip()

    # Object in array: {key1:value1,key2:value2}
    if item_str.startswith("{") and item_str.endswith("}"):
        obj = {}
        inner = item_str[1:-1]

        # Split on commas (respecting nested structures)
        parts = _split_object_parts(inner)

        for part in parts:
            colon_pos = part.find(":")
            if colon_pos != -1:
                k = part[:colon_pos].strip()
                v = part[colon_pos + 1:].strip()
                obj[k] = _parse_value(v)

        return obj

    # Simple value
    return _parse_value(item_str)


def _split_object_parts(inner: str) -> List[str]:
    """Split object parts on commas, respecting nested structures."""
    parts = []
    current = ""
    depth = 0

    for char in inner:
        if char == "{":
            depth += 1
            current += char
        elif char == "}":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char

    if current:
        parts.append(current)

    return parts


def _set_nested(obj: Dict[str, Any], keys: List[str], value: Any) -> None:
    """Set a value at a nested key path."""
    for key in keys[:-1]:
        if key not in obj:
            obj[key] = {}
        obj = obj[key]
    obj[keys[-1]] = value


# Schema hint templates for common structures
def toon_schema_hint(schema: Dict[str, str]) -> str:
    """
    Generate a TOON schema hint for documentation.
    
    Args:
        schema: Dict mapping field names to type descriptions
    
    Returns:
        TOON-formatted schema hint
    
    Example:
        >>> toon_schema_hint({"name": "string", "count": "int"})
        '{name:string,count:int}'
    """
    parts = [f"{k}:{v}" for k, v in schema.items()]
    return "{" + ",".join(parts) + "}"


def toon_response_format(fields: Dict[str, str], nested: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    """
    Generate a TOON response format specification for prompts.
    
    This creates a compact format hint that tells the LLM what structure to return.
    
    Args:
        fields: Dict of field names to type/description
        nested: Optional nested object specifications
    
    Returns:
        Multi-line TOON format specification
    
    Example:
        >>> print(toon_response_format(
        ...     {"task_slug": "snake_case_name", "count": "int"},
        ...     {"constraints": {"required": "bool", "timeout": "int"}}
        ... ))
        task_slug=snake_case_name
        count=int
        constraints.required=bool
        constraints.timeout=int
    """
    lines = []

    for field, type_hint in fields.items():
        lines.append(f"{field}={type_hint}")

    if nested:
        for parent, children in nested.items():
            for field, type_hint in children.items():
                lines.append(f"{parent}.{field}={type_hint}")

    return "\n".join(lines)
