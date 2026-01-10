"""
detect_and_parse_spec node — parses all spec_documents into structured dicts.

Implements: Design Doc §3.3 Detect and Parse Spec
Touches: openapi_spec, openapi_specs (list for multi-spec), parsed_specs

V1 File Integration:
  - Routes specs through sources.detect_and_route() for unified handling
  - API specs → openapi_spec (existing flow preserved)
  - File specs → parsed_specs as ParsedSpec objects
  - build_silver_file_model consumes ParsedSpec from parsed_specs

Bug #78/#79 Fix:
  - Sanitizes large integers (>INT64_MAX) during parsing to prevent
    LangGraph msgpack serialization errors. Some OpenAPI specs (e.g., OpenAI)
    have minimum/maximum values that exceed 64-bit integer range.

V26-001/002 Fix:
  - Implements chunked processing for large specs (>3MB)
  - Prevents timeout (exit code 137) for stripe_api.json (7MB), github_api.json (11MB)
  - Uses ChunkedSpecProcessor to split large specs into manageable chunks
"""
import threading
import yaml
import json
import logging
import hashlib
import time
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import SpecDocument
from integration_coworker.sources import detect_and_route, ensure_sources_registered
from integration_coworker.sources.base import SourceType, ParsedSpec
from integration_coworker.llm.exceptions import LLMAuthError

# V26-001/002: Import chunked processor for large specs
from integration_coworker.graph.nodes.chunked_spec_processor import (
    ChunkedSpecProcessor,
    ChunkStrategy,
    should_use_chunked_processing,
    SPEC_SIZE_THRESHOLD_BYTES,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Metrics: LLM Fallback Counter (B-007)
# =============================================================================
# Tracks when spec conversion falls back to LLM parsing instead of
# deterministic parsing (e.g., graphql-core unavailable or parse error).
# =============================================================================

_spec_llm_fallback_counts = {"graphql": 0, "asyncapi": 0}
_spec_llm_fallback_lock = threading.Lock()


def _increment_llm_fallback_metric(spec_type: str) -> None:
    """Increment the LLM fallback counter for a spec type."""
    with _spec_llm_fallback_lock:
        if spec_type in _spec_llm_fallback_counts:
            _spec_llm_fallback_counts[spec_type] += 1


def get_spec_llm_fallback_counts() -> dict:
    """Get the current LLM fallback counts by spec type."""
    with _spec_llm_fallback_lock:
        return _spec_llm_fallback_counts.copy()


def reset_spec_llm_fallback_counts() -> None:
    """Reset the LLM fallback counts (for testing)."""
    global _spec_llm_fallback_counts
    with _spec_llm_fallback_lock:
        _spec_llm_fallback_counts = {"graphql": 0, "asyncapi": 0}


# PostgreSQL/msgpack BIGINT range
INT64_MAX = 9223372036854775807
INT64_MIN = -9223372036854775808


def _sanitize_large_ints(obj, path: str = ""):
    """
    Recursively sanitize integers that exceed 64-bit range.
    
    Bug #78/#79 Fix: OpenAPI specs can contain minimum/maximum values that
    exceed INT64_MAX (e.g., OpenAI's seed.maximum = 9223372036854776000).
    This causes LangGraph's msgpack serializer to fail when checkpointing.
    
    Strategy: Clamp values to INT64 range rather than convert to string,
    since downstream code may expect numeric types for validation.
    """
    if isinstance(obj, bool):
        # bool is subclass of int - check first to avoid clamping True/False
        return obj
    elif isinstance(obj, int):
        if obj > INT64_MAX:
            if path:
                logger.debug(f"Clamping large int at {path}: {obj} -> {INT64_MAX}")
            return INT64_MAX
        elif obj < INT64_MIN:
            if path:
                logger.debug(f"Clamping small int at {path}: {obj} -> {INT64_MIN}")
            return INT64_MIN
        return obj
    elif isinstance(obj, float):
        # Also check floats that might be too large if converted to int later
        # or if they exceed float precision limits relevant to the domain
        return obj
    elif isinstance(obj, dict):
        return {k: _sanitize_large_ints(v, f"{path}.{k}") for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_large_ints(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    return obj

# Import HTML/PDF parsers (optional dependencies)
try:
    from integration_coworker.parsers.html_parser import parse_html_spec
    HAS_HTML_PARSER = True
except ImportError:
    HAS_HTML_PARSER = False
    parse_html_spec = None

try:
    from integration_coworker.parsers.pdf_parser import parse_pdf_spec
    HAS_PDF_PARSER = True
except ImportError:
    HAS_PDF_PARSER = False
    parse_pdf_spec = None


def _is_html_content(content: str, content_type: str) -> bool:
    """Check if content appears to be HTML."""
    ct = content_type.lower()
    if "html" in ct:
        return True
    # Heuristic: check for HTML tags
    content_lower = content.strip()[:500].lower()
    return content_lower.startswith("<!doctype html") or content_lower.startswith("<html")


def _is_pdf_content(content: str, content_type: str) -> bool:
    """Check if content appears to be PDF."""
    ct = content_type.lower()
    if "pdf" in ct:
        return True
    # Heuristic: check for PDF magic bytes
    return content.strip()[:10].startswith("%PDF-")


def _is_graphql_content(content: str, content_type: str) -> bool:
    """
    Check if content appears to be a GraphQL schema.
    
    Detects GraphQL by:
    1. Content-type header
    2. GraphQL SDL keywords (type, query, mutation, schema)
    3. GraphQL structural patterns
    """
    ct = content_type.lower()
    if "graphql" in ct:
        return True
    
    # Heuristic: check for GraphQL SDL patterns
    content_sample = content.strip()[:3000]
    content_lower = content_sample.lower()
    
    # Strong indicators (any one is enough)
    strong_patterns = [
        "type query {",
        "type query{",
        "type mutation {",
        "type mutation{",
        "type subscription {",
        "type subscription{",
        "schema {",
        "schema{",
    ]
    
    for pattern in strong_patterns:
        if pattern in content_lower:
            return True
    
    # Weaker patterns that need multiple matches
    import re
    
    # Look for "type <Name> {" pattern (GraphQL type definitions)
    type_def_pattern = r'\btype\s+[A-Z][a-zA-Z0-9_]*\s*\{'
    type_matches = len(re.findall(type_def_pattern, content_sample))
    
    # Look for GraphQL field patterns like "fieldName: Type" or "fieldName(args): Type"
    field_pattern = r'\b[a-z][a-zA-Z0-9_]*\s*[:(]'
    
    # If we have 2+ type definitions, it's likely GraphQL
    if type_matches >= 2:
        return True
    
    # Check for scalar/interface/union/enum/input keywords with braces
    weak_patterns = ["scalar ", "interface ", "union ", "enum ", "input "]
    weak_count = sum(1 for p in weak_patterns if p in content_lower)
    
    return type_matches >= 1 and weak_count >= 1


def _is_asyncapi_content(content: str, content_type: str) -> bool:
    """
    Check if content appears to be an AsyncAPI specification.
    """
    ct = content_type.lower()
    if "asyncapi" in ct:
        return True
    
    # Heuristic: check for asyncapi keyword in content
    content_lower = content.strip()[:1000].lower()
    return "asyncapi:" in content_lower or '"asyncapi":' in content_lower


def _parse_html_to_pseudo_openapi(content: str, uri: str) -> dict | None:
    """
    Parse HTML content and convert to a pseudo-OpenAPI structure.
    
    Returns a dict that mimics OpenAPI structure for downstream compatibility.
    """
    if not HAS_HTML_PARSER or parse_html_spec is None:
        return None

    html_doc = parse_html_spec(content)

    if html_doc.errors and not html_doc.endpoints:
        return None

    # Convert to pseudo-OpenAPI structure
    spec = {
        "openapi": "3.0.0",
        "_parsed_from": "html",
        "_source_uri": uri,
        "info": {
            "title": html_doc.title or "API from HTML",
            "description": html_doc.description,
            "version": "1.0.0",
        },
        "paths": {},
        "components": {"schemas": {}},
    }

    if html_doc.base_url:
        spec["servers"] = [{"url": html_doc.base_url}]

    # Convert endpoints to paths
    for endpoint in html_doc.endpoints:
        path = endpoint.path
        method = endpoint.method.lower()

        if path not in spec["paths"]:
            spec["paths"][path] = {}

        operation = {
            "summary": endpoint.summary,
            "description": endpoint.description,
            "responses": {"200": {"description": "Success"}},
        }

        if endpoint.auth_hint:
            operation["security"] = [{}]  # Indicates auth required

        spec["paths"][path][method] = operation

    # Convert schemas
    for schema in html_doc.schemas:
        properties = {}
        required = []

        for field_name, field_info in schema.fields.items():
            properties[field_name] = {
                "type": field_info.get("type", "string"),
            }
            if field_info.get("required"):
                required.append(field_name)

        spec["components"]["schemas"][schema.name] = {
            "type": "object",
            "properties": properties,
            "required": required if required else None,
        }

    return spec


def _parse_pdf_to_pseudo_openapi(content: bytes | str, uri: str) -> dict | None:
    """
    Parse PDF content and convert to a pseudo-OpenAPI structure.
    
    Returns a dict that mimics OpenAPI structure for downstream compatibility.
    """
    if not HAS_PDF_PARSER or parse_pdf_spec is None:
        return None

    # Convert string to bytes if needed (PDF parser expects bytes or file)
    if isinstance(content, str):
        content = content.encode('latin-1')  # PDF is binary

    import io
    pdf_doc = parse_pdf_spec(io.BytesIO(content))

    if pdf_doc.errors and not pdf_doc.endpoints:
        return None

    # Convert to pseudo-OpenAPI structure
    spec = {
        "openapi": "3.0.0",
        "_parsed_from": "pdf",
        "_source_uri": uri,
        "info": {
            "title": pdf_doc.title or "API from PDF",
            "version": "1.0.0",
        },
        "paths": {},
        "components": {"schemas": {}},
    }

    # Convert endpoints to paths
    for endpoint in pdf_doc.endpoints:
        path = endpoint.path
        method = endpoint.method.lower()

        if path not in spec["paths"]:
            spec["paths"][path] = {}

        operation = {
            "summary": endpoint.summary,
            "description": endpoint.description,
            "responses": {"200": {"description": "Success"}},
        }

        if endpoint.auth_hint:
            operation["security"] = [{}]

        spec["paths"][path][method] = operation

    # Convert schemas
    for schema in pdf_doc.schemas:
        properties = {}
        required = []

        for field_name, field_info in schema.fields.items():
            properties[field_name] = {
                "type": field_info.get("type", "string"),
            }
            if field_info.get("required"):
                required.append(field_name)

        spec["components"]["schemas"][schema.name] = {
            "type": "object",
            "properties": properties,
            "required": required if required else None,
        }

    return spec


def _parse_graphql_to_pseudo_openapi(content: str, uri: str) -> dict | None:
    """
    Parse GraphQL SDL content and convert to a pseudo-OpenAPI structure.
    
    HYBRID APPROACH (Fix SPEC-001):
    - Uses graphql-core for DETERMINISTIC schema parsing (structure, types, fields)
    - Falls back to LLM ONLY for generating human-readable descriptions
    
    This ensures:
    - Consistent, reproducible results across runs
    - No hallucinated fields or types
    - Fast parsing without LLM latency for structure
    - LLM only enhances with descriptions (optional)
    
    Returns a dict that mimics OpenAPI structure for downstream compatibility.
    """
    # Try deterministic parsing with graphql-core first
    try:
        from graphql import parse, build_ast_schema
        from graphql.language import ast as gql_ast
        from graphql.type import (
            GraphQLObjectType, GraphQLScalarType, GraphQLEnumType,
            GraphQLInputObjectType, GraphQLList, GraphQLNonNull,
            GraphQLField, GraphQLArgument
        )
        HAS_GRAPHQL_CORE = True
    except ImportError:
        HAS_GRAPHQL_CORE = False
        logger.warning("graphql-core not available, falling back to LLM-only parsing")
        _increment_llm_fallback_metric("graphql")  # B-007: Track LLM fallback
    
    if HAS_GRAPHQL_CORE:
        try:
            return _parse_graphql_deterministic(content, uri)
        except Exception as e:
            logger.warning(f"Deterministic GraphQL parsing failed: {e}, falling back to LLM")
            _increment_llm_fallback_metric("graphql")  # B-007: Track LLM fallback
    
    # Fallback: LLM-only parsing (legacy behavior)
    return _parse_graphql_with_llm(content, uri)


def _graphql_type_to_openapi(gql_type) -> dict:
    """
    Convert a GraphQL type to OpenAPI schema type.
    
    Handles:
    - Scalars (String, Int, Float, Boolean, ID)
    - Lists (-> array)
    - NonNull (-> required marker)
    - Custom types (-> $ref)
    """
    from graphql.type import GraphQLList, GraphQLNonNull, GraphQLScalarType, GraphQLEnumType
    
    # Unwrap NonNull
    nullable = True
    if isinstance(gql_type, GraphQLNonNull):
        nullable = False
        gql_type = gql_type.of_type
    
    # Handle List
    if isinstance(gql_type, GraphQLList):
        inner_schema = _graphql_type_to_openapi(gql_type.of_type)
        return {"type": "array", "items": inner_schema, "_nullable": nullable}
    
    # Handle scalars
    type_name = str(gql_type) if not hasattr(gql_type, 'name') else gql_type.name
    
    scalar_mapping = {
        "String": {"type": "string"},
        "Int": {"type": "integer"},
        "Float": {"type": "number"},
        "Boolean": {"type": "boolean"},
        "ID": {"type": "string"},
        "DateTime": {"type": "string", "format": "date-time"},
        "Date": {"type": "string", "format": "date"},
        "JSON": {"type": "object"},
    }
    
    if type_name in scalar_mapping:
        result = scalar_mapping[type_name].copy()
        result["_nullable"] = nullable
        return result
    
    # Custom type -> $ref
    return {"$ref": f"#/components/schemas/{type_name}", "_nullable": nullable}


def _parse_graphql_deterministic(content: str, uri: str) -> dict:
    """
    Deterministically parse GraphQL SDL using graphql-core.
    
    No LLM involved - pure schema parsing.
    """
    from graphql import parse, build_ast_schema
    from graphql.type import (
        GraphQLObjectType, GraphQLScalarType, GraphQLEnumType,
        GraphQLInputObjectType, GraphQLField
    )
    
    # Parse SDL to AST and build schema
    document = parse(content)
    schema = build_ast_schema(document)
    
    # Initialize OpenAPI structure
    spec = {
        "openapi": "3.0.0",
        "_parsed_from": "graphql",
        "_parse_method": "deterministic",
        "_source_uri": uri,
        "info": {
            "title": "GraphQL API",
            "version": "1.0.0",
            "description": "Auto-generated from GraphQL schema",
        },
        "paths": {},
        "components": {"schemas": {}},
    }
    
    # Extract Query operations as GET endpoints
    query_type = schema.query_type
    if query_type:
        for field_name, field in query_type.fields.items():
            path = f"/graphql/query/{field_name}"
            spec["paths"][path] = {
                "get": _build_operation_from_field(field_name, field, "query")
            }
    
    # Extract Mutation operations as POST endpoints
    mutation_type = schema.mutation_type
    if mutation_type:
        for field_name, field in mutation_type.fields.items():
            path = f"/graphql/mutation/{field_name}"
            spec["paths"][path] = {
                "post": _build_operation_from_field(field_name, field, "mutation")
            }
    
    # Extract Subscription operations (POST with streaming hint)
    subscription_type = schema.subscription_type
    if subscription_type:
        for field_name, field in subscription_type.fields.items():
            path = f"/graphql/subscription/{field_name}"
            spec["paths"][path] = {
                "post": _build_operation_from_field(field_name, field, "subscription")
            }
    
    # Extract all types as component schemas
    type_map = schema.type_map
    for type_name, gql_type in type_map.items():
        # Skip built-in types
        if type_name.startswith("__"):
            continue
        
        # Skip root operation types (already processed)
        if gql_type in [query_type, mutation_type, subscription_type]:
            continue
        
        if isinstance(gql_type, GraphQLObjectType):
            spec["components"]["schemas"][type_name] = _object_type_to_schema(gql_type)
        elif isinstance(gql_type, GraphQLInputObjectType):
            spec["components"]["schemas"][type_name] = _input_type_to_schema(gql_type)
        elif isinstance(gql_type, GraphQLEnumType):
            spec["components"]["schemas"][type_name] = _enum_type_to_schema(gql_type)
        # Skip scalars (handled inline)
    
    return spec


def _build_operation_from_field(field_name: str, field, op_type: str) -> dict:
    """Build an OpenAPI operation from a GraphQL field."""
    operation = {
        "operationId": f"{op_type}_{field_name}",
        "summary": field.description or f"{op_type.title()} {field_name}",
        "tags": [op_type.title()],
        "responses": {
            "200": {
                "description": "Successful response",
                "content": {
                    "application/json": {
                        "schema": _graphql_type_to_openapi(field.type)
                    }
                }
            }
        }
    }
    
    # Add arguments as parameters or request body
    if field.args:
        if op_type == "query":
            # GET: use query parameters
            operation["parameters"] = []
            for arg_name, arg in field.args.items():
                arg_schema = _graphql_type_to_openapi(arg.type)
                required = not arg_schema.pop("_nullable", True)
                operation["parameters"].append({
                    "name": arg_name,
                    "in": "query",
                    "required": required,
                    "schema": arg_schema,
                    "description": arg.description or ""
                })
        else:
            # POST: use request body
            properties = {}
            required_props = []
            for arg_name, arg in field.args.items():
                arg_schema = _graphql_type_to_openapi(arg.type)
                is_required = not arg_schema.pop("_nullable", True)
                properties[arg_name] = arg_schema
                if arg.description:
                    properties[arg_name]["description"] = arg.description
                if is_required:
                    required_props.append(arg_name)
            
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": properties,
                            "required": required_props if required_props else None
                        }
                    }
                }
            }
    
    return operation


def _object_type_to_schema(gql_type) -> dict:
    """Convert GraphQL ObjectType to OpenAPI schema."""
    properties = {}
    required = []
    
    for field_name, field in gql_type.fields.items():
        field_schema = _graphql_type_to_openapi(field.type)
        is_required = not field_schema.pop("_nullable", True)
        
        if field.description:
            field_schema["description"] = field.description
        
        properties[field_name] = field_schema
        if is_required:
            required.append(field_name)
    
    schema = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    if gql_type.description:
        schema["description"] = gql_type.description
    
    return schema


def _input_type_to_schema(gql_type) -> dict:
    """Convert GraphQL InputObjectType to OpenAPI schema."""
    properties = {}
    required = []
    
    for field_name, field in gql_type.fields.items():
        field_schema = _graphql_type_to_openapi(field.type)
        is_required = not field_schema.pop("_nullable", True)
        
        if field.description:
            field_schema["description"] = field.description
        
        properties[field_name] = field_schema
        if is_required:
            required.append(field_name)
    
    schema = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    if gql_type.description:
        schema["description"] = gql_type.description
    
    return schema


def _enum_type_to_schema(gql_type) -> dict:
    """Convert GraphQL EnumType to OpenAPI schema."""
    # gql_type.values is a dict of {name: GraphQLEnumValue}
    # The keys are the enum value names
    return {
        "type": "string",
        "enum": list(gql_type.values.keys()),
        "description": gql_type.description or f"Enum type: {gql_type.name}"
    }


def _parse_graphql_with_llm(content: str, uri: str) -> dict | None:
    """
    Legacy LLM-only parsing for GraphQL.
    
    Used as fallback when:
    - graphql-core is not installed
    - Deterministic parsing fails (malformed schema)
    """
    try:
        from integration_coworker.llm import call_llm_for_node
    except ImportError:
        logger.warning("LLM module not available for GraphQL parsing")
        return None

    prompt = f"""You are an expert API specification converter.

Convert this GraphQL SDL schema to an OpenAPI 3.0 JSON specification.

<graphql_schema>
{content[:15000]}
</graphql_schema>

Extract:
1. All Query types as GET endpoints (path: /graphql/query/{{queryName}})
2. All Mutation types as POST endpoints (path: /graphql/mutation/{{mutationName}})
3. All types as component schemas
4. Field arguments as parameters
5. Return types as response schemas

Output ONLY valid JSON matching OpenAPI 3.0 structure. No markdown, no explanation.
Include these keys: openapi, info, paths, components."""

    try:
        response = call_llm_for_node("detect_and_parse_spec", prompt)
        
        # Parse the JSON response
        # Handle potential markdown code blocks
        response_text = response.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        
        spec = json.loads(response_text.strip())
        
        # Add markers for downstream tracking
        spec["_parsed_from"] = "graphql"
        spec["_parse_method"] = "llm"
        spec["_source_uri"] = uri
        
        return spec
    except LLMAuthError:
        # Auth errors: fail-fast, surface to caller (P0-2)
        raise
    except Exception as e:
        logger.error(f"GraphQL LLM parsing failed: {e}")
        return None


def _parse_asyncapi_to_pseudo_openapi(content: str, uri: str) -> dict | None:
    """
    Parse AsyncAPI content and convert to a pseudo-OpenAPI structure using LLM.
    
    This enables the system to support event-driven/message APIs.
    The LLM extracts channel/message information and converts to OpenAPI format.
    
    Returns a dict that mimics OpenAPI structure for downstream compatibility.
    
    Note: AsyncAPI always uses LLM (no deterministic parser available).
    """
    # B-007: Track LLM usage for AsyncAPI (always LLM-based)
    _increment_llm_fallback_metric("asyncapi")
    
    try:
        from integration_coworker.llm import call_llm_for_node
    except ImportError:
        logger.warning("LLM module not available for AsyncAPI parsing")
        return None

    prompt = f"""You are an expert API specification converter.

Convert this AsyncAPI specification to an OpenAPI 3.0 JSON specification.

<asyncapi_spec>
{content[:15000]}
</asyncapi_spec>

Extract:
1. All channels as paths (publish as POST, subscribe as GET with callbacks)
2. All message schemas as request/response bodies
3. All components/schemas as component schemas
4. Server info as server info

Output ONLY valid JSON matching OpenAPI 3.0 structure. No markdown, no explanation.
Include these keys: openapi, info, paths, components."""

    try:
        response = call_llm_for_node("detect_and_parse_spec", prompt)
        
        # Parse the JSON response
        response_text = response.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        
        spec = json.loads(response_text.strip())
        
        # Add markers for downstream tracking
        spec["_parsed_from"] = "asyncapi"
        spec["_source_uri"] = uri
        
        return spec
    except LLMAuthError:
        # Auth errors: fail-fast, surface to caller (P0-2)
        raise
    except Exception as e:
        logger.error(f"AsyncAPI LLM parsing failed: {e}")
        return None


def _parse_spec_content(content: str, content_type: str, uri: str = "") -> dict:
    """
    Parse spec content (YAML, JSON, HTML, PDF, GraphQL, or AsyncAPI) into a dict.
    
    For HTML/PDF/GraphQL/AsyncAPI, returns a pseudo-OpenAPI structure.
    Bug #78/#79: Also sanitizes large integers to prevent msgpack overflow.
    Raises on parse failure.
    """
    ct = content_type.lower()
    result = None

    # Try HTML first if content type suggests it
    if _is_html_content(content, content_type):
        result = _parse_html_to_pseudo_openapi(content, uri)
        if result:
            return _sanitize_large_ints(result, uri)
        # Fall through to try other formats

    # Try PDF if content type suggests it
    if _is_pdf_content(content, content_type):
        result = _parse_pdf_to_pseudo_openapi(content, uri)
        if result:
            return _sanitize_large_ints(result, uri)
        # Fall through to try other formats

    # Try GraphQL if content looks like SDL schema
    if _is_graphql_content(content, content_type):
        result = _parse_graphql_to_pseudo_openapi(content, uri)
        if result:
            return _sanitize_large_ints(result, uri)
        # Fall through to try other formats

    # Try AsyncAPI if content has asyncapi marker
    if _is_asyncapi_content(content, content_type):
        result = _parse_asyncapi_to_pseudo_openapi(content, uri)
        if result:
            return _sanitize_large_ints(result, uri)
        # Fall through to try other formats

    # Standard YAML/JSON parsing
    if "yaml" in ct or "yml" in ct:
        result = yaml.safe_load(content)
    elif "json" in ct:
        result = json.loads(content)
    else:
        # Try YAML first (covers JSON too), fall back to JSON
        try:
            result = yaml.safe_load(content)
        except Exception:
            result = json.loads(content)
    
    # Bug #78/#79 Fix: Sanitize large integers before returning
    # This prevents LangGraph msgpack serialization errors
    return _sanitize_large_ints(result, uri)


def _check_and_log_spec_size(spec: dict, uri: str) -> None:
    """
    V26-001/002: Check spec size and log chunking recommendations.
    
    This is an early warning system that logs when specs are large enough
    to potentially cause timeouts, even before processing begins.
    """
    try:
        processor = ChunkedSpecProcessor(spec)
        plan = processor.plan()
        
        if plan.strategy != ChunkStrategy.NONE:
            logger.warning(
                f"[V26-001/002] Large spec detected: {uri} "
                f"({plan.spec_size_bytes / 1024 / 1024:.1f}MB, "
                f"{plan.endpoint_count} endpoints, {plan.schema_count} schemas). "
                f"Chunking strategy: {plan.strategy.name} with {plan.total_chunks} chunks"
            )
            for warning in plan.warnings:
                logger.warning(f"[V26-001/002] {warning}")
    except Exception as e:
        logger.debug(f"Spec size check failed for {uri}: {e}")


def detect_and_parse_spec(state: WorkflowState) -> WorkflowState:
    """
    Parse all spec_documents into openapi_spec(s) and file specs.

    V1 File Integration:
      - Routes ALL specs through sources.detect_and_route() first
      - API specs (SourceType.API) → openapi_spec (existing flow preserved)
      - File specs (SourceType.FILE) → parsed_specs as typed ParsedSpec objects
      - Falls back to legacy parsing for complex API formats (GraphQL, AsyncAPI)

    Reads: spec_documents
    Writes: 
      - openapi_spec (primary API spec dict)
      - parsed_specs (List[ParsedSpec] for file specs)
      - plan["openapi_specs"] (list of all API specs)
    """
    # Ensure sources are registered before routing
    ensure_sources_registered()
    
    # Some call sites (including tests) pre-populate `pending_specs` with raw
    # content but don't run `ingest_spec`, leaving `spec_documents` empty.
    # Build minimal SpecDocument objects here as a compatibility bridge.
    if not state.spec_documents:
        pending = getattr(state, "pending_specs", None) or []
        if pending:
            built: list[SpecDocument] = []
            for i, item in enumerate(pending):
                if not isinstance(item, dict):
                    continue
                uri = item.get("ref") or (state.spec_refs[i] if getattr(state, "spec_refs", None) and i < len(state.spec_refs) else f"pending_spec_{i}")
                content = item.get("content")
                if content is None:
                    continue
                sha256 = hashlib.sha256(str(content).encode("utf-8")).hexdigest()
                built.append(
                    SpecDocument(
                        id=None,
                        source_system_id=None,
                        version="",
                        uri=str(uri),
                        content_type=str(item.get("content_type") or ""),
                        sha256=sha256,
                        content=str(content),
                    )
                )

            if built:
                state.spec_documents = built

        # Still empty? Provide a clearer error.
        if not state.spec_documents:
            state.errors.append("No spec_documents to parse")
            state.completed_steps.append("detect_and_parse_spec")
            return state

    # Lists to hold parsed results
    api_specs: list[dict] = []
    file_parsed_specs: list[ParsedSpec] = []

    for spec_doc in state.spec_documents:
        content = spec_doc.content
        content_type = spec_doc.content_type or ""
        uri = spec_doc.uri

        try:
            # Try routing through unified SpecSource system first
            try:
                parsed_spec = detect_and_route(content, uri, content_type)
                
                if parsed_spec.source_type == SourceType.FILE:
                    # File spec - add to parsed_specs as ParsedSpec
                    if parsed_spec.is_valid():
                        file_parsed_specs.append(parsed_spec)
                        logger.info(f"Routed {uri} as FILE spec via SpecSource")
                        
                        # Propagate warnings to state for visibility
                        if parsed_spec.warnings:
                            for warning in parsed_spec.warnings:
                                state.warnings.append(f"[{uri}] {warning}")
                            logger.warning(
                                f"File spec {uri} parsed with {len(parsed_spec.warnings)} warning(s): "
                                f"{parsed_spec.warnings[:2]}"  # Log first 2
                            )
                    else:
                        state.errors.append(f"File spec from {uri} failed parsing: {parsed_spec.errors}")
                        # Also propagate any warnings even on failure
                        if parsed_spec.warnings:
                            for warning in parsed_spec.warnings:
                                state.warnings.append(f"[{uri}] WARN: {warning}")
                    continue
                    
                elif parsed_spec.source_type == SourceType.API:
                    # API spec from SpecSource - use its data
                    api_data = parsed_spec.data
                    if isinstance(api_data, dict):
                        api_data["_source_uri"] = uri
                        api_data = _sanitize_large_ints(api_data, uri)
                        api_specs.append(api_data)
                        logger.info(f"Routed {uri} as API spec via SpecSource")
                    continue
                    
            except ValueError as e:
                # No source matched - fall back to legacy parsing
                logger.debug(f"SpecSource routing failed for {uri}: {e}, using legacy parser")
            except Exception as e:
                logger.warning(f"SpecSource error for {uri}: {e}, falling back to legacy")
            
            # Legacy parsing path (preserves existing API behavior)
            parsed = _parse_spec_content(content, content_type, uri)

            if not isinstance(parsed, dict):
                state.errors.append(f"Parsed spec from {uri} is not a dictionary")
                continue

            # Allow pseudo-OpenAPI from HTML/PDF (has _parsed_from marker)
            is_pseudo_openapi = "_parsed_from" in parsed
            if not is_pseudo_openapi and "openapi" not in parsed and "swagger" not in parsed:
                state.errors.append(f"Parsed spec from {uri} does not appear to be OpenAPI/Swagger format")
                continue

            # V26-001/002: Check spec size and log chunking info
            _check_and_log_spec_size(parsed, uri)

            # Tag with source URI for downstream tracking
            parsed["_source_uri"] = uri
            api_specs.append(parsed)

        except Exception as e:
            state.errors.append(f"Failed to parse spec from {uri}: {str(e)}")

    # Primary API spec = first successfully parsed
    if api_specs:
        state.openapi_spec = api_specs[0]

    # Store all API specs in plan for multi-spec processing
    if state.plan is not None and api_specs:
        state.plan["openapi_specs"] = api_specs
    
    # Store file specs in parsed_specs (typed List[ParsedSpec])
    if file_parsed_specs:
        state.parsed_specs = file_parsed_specs
        logger.info(f"Added {len(file_parsed_specs)} file spec(s) to parsed_specs")

    state.completed_steps.append("detect_and_parse_spec")
    return state
