"""
Request/Response Field Mapping Utilities.

P3: Request/Response Mappings

This module provides utilities for generating field mappings between:
- Function parameters → API request fields (request_mapping)
- API response fields → Return values (response_mapping)

Uses schema analysis to deterministically map fields based on:
- Field names and types from Schema/SchemaField
- Parameter locations (path, query, header, body)
- Common field naming patterns
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

from integration_coworker.domain.models import (
    Endpoint,
    EndpointParameter,
    Schema,
    SchemaField,
)

logger = logging.getLogger(__name__)


@dataclass
class FieldMapping:
    """A single field mapping."""
    source: str  # Source field name
    target: str  # Target field name
    type: Optional[str] = None  # Data type
    required: bool = False
    transform: Optional[str] = None  # Optional transformation hint


@dataclass
class RequestMapping:
    """Complete request mapping for an endpoint."""
    path_params: List[FieldMapping] = field(default_factory=list)
    query_params: List[FieldMapping] = field(default_factory=list)
    header_params: List[FieldMapping] = field(default_factory=list)
    body_fields: List[FieldMapping] = field(default_factory=list)
    content_type: str = "application/json"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "path_params": [
                {"source": m.source, "target": m.target, "type": m.type, "required": m.required}
                for m in self.path_params
            ],
            "query_params": [
                {"source": m.source, "target": m.target, "type": m.type, "required": m.required}
                for m in self.query_params
            ],
            "header_params": [
                {"source": m.source, "target": m.target, "type": m.type}
                for m in self.header_params
            ],
            "body_fields": [
                {"source": m.source, "target": m.target, "type": m.type, "required": m.required}
                for m in self.body_fields
            ],
            "content_type": self.content_type,
        }


@dataclass
class ResponseMapping:
    """Complete response mapping for an endpoint."""
    extract_fields: List[FieldMapping] = field(default_factory=list)
    status_field: Optional[str] = None
    error_field: Optional[str] = None
    pagination_fields: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        result = {
            "extract_fields": [
                {"source": m.source, "target": m.target, "type": m.type}
                for m in self.extract_fields
            ],
        }
        if self.status_field:
            result["status_field"] = self.status_field
        if self.error_field:
            result["error_field"] = self.error_field
        if self.pagination_fields:
            result["pagination_fields"] = self.pagination_fields
        return result


def to_snake_case(name: str) -> str:
    """Convert camelCase or PascalCase to snake_case.
    
    Handles None input gracefully by returning empty string.
    """
    if name is None:
        return ""
    # Insert underscore before uppercase letters
    s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', name)
    # Insert underscore before sequences of uppercase letters
    s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
    # Handle kebab-case
    s3 = s2.replace('-', '_')
    return s3.lower()


def to_camel_case(name: str) -> str:
    """Convert snake_case to camelCase.
    
    Handles None input gracefully by returning empty string.
    """
    if name is None:
        return ""
    components = name.split('_')
    return components[0] + ''.join(x.title() for x in components[1:])


def generate_request_mapping(
    endpoint: Endpoint,
    parameters: List[EndpointParameter],
    request_schema: Optional[Schema],
    schema_fields: List[SchemaField],
) -> RequestMapping:
    """
    Generate request mapping from endpoint parameters and schema.
    
    Args:
        endpoint: The endpoint being called
        parameters: Endpoint parameters (path, query, header)
        request_schema: Request body schema (if any)
        schema_fields: All schema fields
        
    Returns:
        RequestMapping with populated field mappings
    """
    mapping = RequestMapping()

    # 1. Map endpoint parameters by location
    for param in parameters:
        if param.endpoint_id is not None and param.endpoint_id != endpoint.id:
            continue  # Skip parameters from other endpoints

        # Generate source name (snake_case for Python params)
        source_name = to_snake_case(param.name)

        field_mapping = FieldMapping(
            source=source_name,
            target=param.name,
            type=param.schema_ref,
            required=param.required,
        )

        if param.location == "path":
            mapping.path_params.append(field_mapping)
        elif param.location == "query":
            mapping.query_params.append(field_mapping)
        elif param.location == "header":
            mapping.header_params.append(field_mapping)

    # 2. Map request body fields from schema
    if request_schema:
        body_fields = [f for f in schema_fields if f.schema_id == request_schema.id]
        for schema_field in body_fields:
            source_name = to_snake_case(schema_field.name)
            mapping.body_fields.append(FieldMapping(
                source=source_name,
                target=schema_field.name,
                type=schema_field.field_type or schema_field.type,
                required=schema_field.required,
            ))

    # 3. Infer body fields from path if no schema (common patterns)
    if not mapping.body_fields and endpoint.method.upper() in ("POST", "PUT", "PATCH"):
        # Try to infer from path pattern
        inferred_fields = _infer_body_fields_from_path(endpoint.path, endpoint.method)
        mapping.body_fields.extend(inferred_fields)

    return mapping


def generate_response_mapping(
    endpoint: Endpoint,
    response_schema: Optional[Schema],
    schema_fields: List[SchemaField],
) -> ResponseMapping:
    """
    Generate response mapping from endpoint response schema.
    
    Args:
        endpoint: The endpoint being called
        response_schema: Response body schema (if any)
        schema_fields: All schema fields
        
    Returns:
        ResponseMapping with populated field mappings
    """
    mapping = ResponseMapping()

    # 1. Map response fields from schema
    if response_schema:
        resp_fields = [f for f in schema_fields if f.schema_id == response_schema.id]
        for schema_field in resp_fields:
            target_name = to_snake_case(schema_field.name)
            mapping.extract_fields.append(FieldMapping(
                source=schema_field.name,
                target=target_name,
                type=schema_field.field_type or schema_field.type,
            ))

            # Detect common status/error fields
            name_lower = schema_field.name.lower()
            if name_lower in ("status", "status_code", "statuscode"):
                mapping.status_field = schema_field.name
            elif name_lower in ("error", "errors", "error_message", "errormessage"):
                mapping.error_field = schema_field.name

    # 2. Detect pagination fields
    pagination_patterns = _detect_pagination_fields(
        [f.source for f in mapping.extract_fields]
    )
    if pagination_patterns:
        mapping.pagination_fields = pagination_patterns

    # 3. Infer from endpoint path if no schema
    if not mapping.extract_fields:
        inferred_fields = _infer_response_fields_from_path(endpoint.path, endpoint.method)
        mapping.extract_fields.extend(inferred_fields)

    return mapping


def generate_field_mappings(
    endpoint: Endpoint,
    parameters: List[EndpointParameter],
    schemas: List[Schema],
    schema_fields: List[SchemaField],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Generate request and response mappings for an endpoint.
    
    This is the main entry point for field mapping generation.
    
    Args:
        endpoint: The endpoint to generate mappings for
        parameters: All endpoint parameters
        schemas: All schemas
        schema_fields: All schema fields
        
    Returns:
        Tuple of (request_mapping_dict, response_mapping_dict)
    """
    # Find relevant schemas
    request_schema = None
    response_schema = None

    if endpoint.request_schema_id:
        request_schema = next(
            (s for s in schemas if s.id == endpoint.request_schema_id),
            None
        )

    if endpoint.response_schema_id:
        response_schema = next(
            (s for s in schemas if s.id == endpoint.response_schema_id),
            None
        )

    # Filter parameters for this endpoint
    endpoint_params = [
        p for p in parameters
        if p.endpoint_id is None or p.endpoint_id == endpoint.id
    ]

    # Generate mappings
    request_mapping = generate_request_mapping(
        endpoint, endpoint_params, request_schema, schema_fields
    )
    response_mapping = generate_response_mapping(
        endpoint, response_schema, schema_fields
    )

    return request_mapping.to_dict(), response_mapping.to_dict()


def _infer_body_fields_from_path(path: str, method: str) -> List[FieldMapping]:
    """
    Infer likely body fields from URL path patterns.
    
    Examples:
        /v1/customers -> [name, email, ...]
        /v1/payments -> [amount, currency, ...]
        /v1/orders -> [items, total, ...]
    """
    if path is None:
        return []
    
    mappings = []

    # Extract resource name from path
    path_parts = path.strip("/").split("/")
    resource = path_parts[-1] if path_parts else ""

    # Remove version prefix
    if resource.startswith("v") and len(resource) <= 3:
        resource = path_parts[-1] if len(path_parts) > 1 else ""

    # Common field patterns by resource type
    resource_patterns = {
        "customers": ["name", "email", "phone", "address"],
        "users": ["username", "email", "password", "name"],
        "payments": ["amount", "currency", "source", "description"],
        "charges": ["amount", "currency", "source", "customer"],
        "orders": ["items", "total", "shipping_address", "customer_id"],
        "products": ["name", "description", "price", "sku"],
        "subscriptions": ["customer", "plan", "quantity"],
        "invoices": ["customer", "amount", "due_date"],
        "sessions": ["customer", "line_items", "success_url", "cancel_url"],
    }

    # Match resource to patterns
    resource_lower = resource.lower().rstrip("s")  # Singularize
    for pattern_key, fields in resource_patterns.items():
        if resource_lower in pattern_key or pattern_key.startswith(resource_lower):
            for field in fields:
                mappings.append(FieldMapping(
                    source=field,
                    target=to_camel_case(field),
                    type="string",
                    required=False,
                ))
            break

    return mappings


def _infer_response_fields_from_path(path: str, method: str) -> List[FieldMapping]:
    """
    Infer likely response fields from URL path and method.
    """
    mappings = []

    # Standard response fields
    mappings.append(FieldMapping(
        source="id",
        target="id",
        type="string",
    ))

    if method.upper() == "GET":
        # List endpoints typically return items/data
        if path.endswith("s") or "list" in path.lower():
            mappings.append(FieldMapping(
                source="data",
                target="data",
                type="array",
            ))
            mappings.append(FieldMapping(
                source="has_more",
                target="has_more",
                type="boolean",
            ))

    # Common metadata fields
    mappings.extend([
        FieldMapping(source="created", target="created", type="integer"),
        FieldMapping(source="object", target="object", type="string"),
    ])

    return mappings


def _detect_pagination_fields(field_names: List[str]) -> Optional[Dict[str, str]]:
    """
    Detect pagination field patterns in response.
    """
    pagination = {}

    field_names_lower = [f.lower() for f in field_names]

    # Cursor-based pagination
    if "next_cursor" in field_names_lower or "cursor" in field_names_lower:
        pagination["type"] = "cursor"
        pagination["cursor_field"] = (
            "next_cursor" if "next_cursor" in field_names_lower else "cursor"
        )

    # Offset-based pagination
    elif "offset" in field_names_lower or "limit" in field_names_lower:
        pagination["type"] = "offset"
        if "total" in field_names_lower:
            pagination["total_field"] = "total"

    # Page-based pagination
    elif "page" in field_names_lower or "page_number" in field_names_lower:
        pagination["type"] = "page"
        if "total_pages" in field_names_lower:
            pagination["total_pages_field"] = "total_pages"

    # Has more pattern
    if "has_more" in field_names_lower:
        pagination["has_more_field"] = "has_more"

    return pagination if pagination else None


def merge_mappings(
    schema_mapping: Dict[str, Any],
    llm_mapping: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge schema-derived and LLM-generated mappings.
    
    LLM mappings take precedence for descriptions and transformations,
    but schema mappings are authoritative for types and field names.
    """
    result = schema_mapping.copy()

    if not llm_mapping:
        return result

    # Merge descriptions
    if "description" in llm_mapping:
        result["description"] = llm_mapping["description"]

    # Merge any transformation hints
    if "transforms" in llm_mapping:
        result["transforms"] = llm_mapping["transforms"]

    return result
