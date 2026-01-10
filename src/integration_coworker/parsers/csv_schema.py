"""
CSV/TSV schema inference module.

Implements: V1 Gap Closure Plan P5 - CSV/EDI/Message Specs
Infers schema from CSV/TSV header rows and sample data.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class InferredField:
    """A field inferred from CSV data."""
    name: str
    inferred_type: str  # string, integer, number, boolean, date, datetime, email, uuid
    nullable: bool = True
    sample_values: list[str] = field(default_factory=list)
    description: Optional[str] = None


@dataclass
class CsvSchema:
    """Schema inferred from a CSV/TSV file."""
    fields: list[InferredField] = field(default_factory=list)
    row_count: int = 0
    has_header: bool = True
    delimiter: str = ","
    errors: list[str] = field(default_factory=list)


# Type inference patterns
TYPE_PATTERNS = {
    "uuid": re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE
    ),
    "email": re.compile(
        r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    ),
    "datetime": re.compile(
        r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    ),
    "date": re.compile(
        r"^\d{4}-\d{2}-\d{2}$"
    ),
    "boolean": re.compile(
        r"^(true|false|yes|no|0|1)$",
        re.IGNORECASE
    ),
    "integer": re.compile(
        r"^-?\d+$"
    ),
    "number": re.compile(
        r"^-?\d+\.?\d*$"
    ),
}


def infer_csv_schema(
    content: str,
    delimiter: Optional[str] = None,
    has_header: bool = True,
    sample_rows: int = 100
) -> CsvSchema:
    """
    Infer schema from CSV/TSV content.
    
    Analyzes column headers and sample data to infer field types.
    
    Args:
        content: CSV/TSV content string
        delimiter: Column delimiter (auto-detected if None)
        has_header: Whether first row contains headers
        sample_rows: Number of rows to sample for type inference
        
    Returns:
        CsvSchema with inferred field information
    """
    schema = CsvSchema()

    if not content or not content.strip():
        schema.errors.append("Empty content provided")
        return schema

    # Auto-detect delimiter if not provided
    if delimiter is None:
        delimiter = _detect_delimiter(content)

    schema.delimiter = delimiter
    schema.has_header = has_header

    try:
        reader = csv.reader(io.StringIO(content), delimiter=delimiter)
        rows = list(reader)
    except Exception as e:
        schema.errors.append(f"Failed to parse CSV: {str(e)}")
        return schema

    if not rows:
        schema.errors.append("No rows found in CSV")
        return schema

    schema.row_count = len(rows) - (1 if has_header else 0)

    # Get headers
    if has_header:
        headers = rows[0]
        data_rows = rows[1:sample_rows + 1]
    else:
        # Generate column names if no header
        headers = [f"column_{i}" for i in range(len(rows[0]))]
        data_rows = rows[:sample_rows]

    # Infer types for each column
    for col_idx, header in enumerate(headers):
        col_values = []
        for row in data_rows:
            if col_idx < len(row):
                col_values.append(row[col_idx])

        inferred_type = _infer_column_type(col_values)
        nullable = any(not v or v.lower() in ("null", "none", "") for v in col_values)

        # Get sample non-empty values
        samples = [v for v in col_values if v and v.lower() not in ("null", "none")][:5]

        schema.fields.append(InferredField(
            name=_normalize_column_name(header),
            inferred_type=inferred_type,
            nullable=nullable,
            sample_values=samples,
        ))

    return schema


def csv_schema_to_json_schema(csv_schema: CsvSchema) -> dict[str, Any]:
    """
    Convert CsvSchema to JSON Schema format.
    
    Args:
        csv_schema: Inferred CSV schema
        
    Returns:
        JSON Schema compatible dictionary
    """
    type_mapping = {
        "string": {"type": "string"},
        "integer": {"type": "integer"},
        "number": {"type": "number"},
        "boolean": {"type": "boolean"},
        "date": {"type": "string", "format": "date"},
        "datetime": {"type": "string", "format": "date-time"},
        "email": {"type": "string", "format": "email"},
        "uuid": {"type": "string", "format": "uuid"},
    }

    properties = {}
    required = []

    for field in csv_schema.fields:
        field_schema = type_mapping.get(
            field.inferred_type,
            {"type": "string"}
        ).copy()

        if field.description:
            field_schema["description"] = field.description

        if field.nullable:
            # Make nullable by allowing null
            if "type" in field_schema:
                field_schema["type"] = [field_schema["type"], "null"]
        else:
            required.append(field.name)

        properties[field.name] = field_schema

    json_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
    }

    if required:
        json_schema["required"] = required

    return json_schema


def _detect_delimiter(content: str) -> str:
    """Auto-detect CSV delimiter from content."""
    # Get first few lines
    lines = content.split('\n')[:5]
    sample = '\n'.join(lines)

    # Count potential delimiters
    delimiters = [',', '\t', ';', '|']
    counts = {}

    for d in delimiters:
        counts[d] = sample.count(d)

    # Return most common non-zero delimiter
    if not counts:
        return ','

    best = max(delimiters, key=lambda d: counts.get(d, 0))
    return best if counts.get(best, 0) > 0 else ','


def _normalize_column_name(name: str) -> str:
    """Normalize column name to valid identifier."""
    # Remove leading/trailing whitespace
    name = name.strip()

    # Replace spaces and special chars with underscores
    name = re.sub(r'[^\w]', '_', name)

    # Remove consecutive underscores
    name = re.sub(r'_+', '_', name)

    # Remove leading/trailing underscores
    name = name.strip('_')

    # Ensure starts with letter or underscore
    if name and name[0].isdigit():
        name = '_' + name

    return name.lower() or 'unnamed'


def _infer_column_type(values: list[str]) -> str:
    """Infer the type of a column from sample values."""
    if not values:
        return "string"

    # Filter out empty/null values
    non_empty = [v for v in values if v and v.lower() not in ("null", "none", "")]

    if not non_empty:
        return "string"

    # Try each type pattern in order of specificity
    type_order = ["uuid", "email", "datetime", "date", "boolean", "integer", "number"]

    for type_name in type_order:
        pattern = TYPE_PATTERNS[type_name]
        if all(pattern.match(v) for v in non_empty):
            return type_name

    return "string"
