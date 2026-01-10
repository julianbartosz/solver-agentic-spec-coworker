"""
Spec normalization layer.

Provides pre-processing and normalization for OpenAPI/AsyncAPI specs
to ensure consistent handling across different providers and versions.

Normalization passes:
1. Integer sanitization (clamp to INT64 range) - Bug #78/79
2. $ref resolution (inline frequently-used refs)
3. Security scheme standardization
4. Schema normalization (handle oneOf/anyOf/allOf)
5. Path parameter extraction
6. Server URL normalization
"""
import logging
import copy
import re
from typing import Dict, Any, Optional, List, Set, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

INT64_MAX = 9223372036854775807
INT64_MIN = -9223372036854775808

# Common auth scheme mappings
AUTH_TYPE_MAPPING = {
    "bearer": "bearer",
    "basic": "basic",
    "apiKey": "api_key",
    "oauth2": "oauth2",
    "openIdConnect": "oidc",
    "http": "http",
}


# =============================================================================
# Main Normalization Entry Point
# =============================================================================

def normalize_spec(spec: Dict[str, Any], options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Apply all normalization passes to an OpenAPI/AsyncAPI specification.
    
    This is the main entry point for spec normalization. All passes are
    applied in order, with earlier passes potentially enabling later ones.
    
    Args:
        spec: Parsed OpenAPI/AsyncAPI spec dictionary
        options: Optional configuration for normalization passes
            - sanitize_ints: bool (default: True) - Clamp large integers
            - resolve_refs: bool (default: False) - Inline common $refs
            - normalize_auth: bool (default: True) - Standardize auth schemes
            - normalize_schemas: bool (default: True) - Handle complex schemas
            - normalize_servers: bool (default: True) - Clean up server URLs
            
    Returns:
        Normalized spec dictionary (deep copy, original is not modified)
        
    Example:
        >>> raw_spec = yaml.safe_load(spec_content)
        >>> normalized = normalize_spec(raw_spec)
    """
    if options is None:
        options = {}
    
    # Always work on a deep copy to avoid mutating original
    normalized = copy.deepcopy(spec)
    
    # Track which passes were applied for logging
    applied_passes: List[str] = []
    
    # Pass 1: Integer sanitization (Bug #78/79 fix)
    if options.get("sanitize_ints", True):
        normalized = sanitize_large_ints(normalized)
        applied_passes.append("sanitize_ints")
    
    # Pass 2: Security scheme normalization
    if options.get("normalize_auth", True):
        normalized = normalize_security_schemes(normalized)
        applied_passes.append("normalize_auth")
    
    # Pass 3: Schema normalization
    if options.get("normalize_schemas", True):
        normalized = normalize_schemas(normalized)
        applied_passes.append("normalize_schemas")
    
    # Pass 4: Server URL normalization
    if options.get("normalize_servers", True):
        normalized = normalize_servers(normalized)
        applied_passes.append("normalize_servers")
    
    # Pass 5: Optional $ref resolution (can be expensive for large specs)
    if options.get("resolve_refs", False):
        normalized = resolve_common_refs(normalized)
        applied_passes.append("resolve_refs")
    
    # Pass 6: Path parameter extraction
    normalized = extract_path_parameters(normalized)
    applied_passes.append("extract_path_params")
    
    logger.debug(f"Applied normalization passes: {applied_passes}")
    return normalized


# =============================================================================
# Pass 1: Integer Sanitization
# =============================================================================

def sanitize_large_ints(obj: Any, path: str = "") -> Any:
    """
    Clamp integers exceeding INT64 range to prevent serialization errors.
    
    Bug #78/79 fix: Some specs (e.g., OpenAI) have integer constraints
    like seed.minimum/maximum that exceed INT64_MAX. This causes msgpack
    serialization failures in LangGraph checkpointing.
    
    Args:
        obj: Any JSON-like value (dict, list, primitive)
        path: Current path for logging (for debugging)
        
    Returns:
        Sanitized value with clamped integers
    """
    if isinstance(obj, bool):
        # Booleans must be checked before int (bool is subclass of int)
        return obj
    elif isinstance(obj, int):
        if obj > INT64_MAX:
            logger.debug(f"Clamping large int at {path}: {obj} -> {INT64_MAX}")
            return INT64_MAX
        elif obj < INT64_MIN:
            logger.debug(f"Clamping large int at {path}: {obj} -> {INT64_MIN}")
            return INT64_MIN
        return obj
    elif isinstance(obj, dict):
        return {k: sanitize_large_ints(v, f"{path}.{k}") for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_large_ints(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    return obj


# =============================================================================
# Pass 2: Security Scheme Normalization
# =============================================================================

def normalize_security_schemes(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Standardize security scheme definitions across OpenAPI 2.x and 3.x.
    
    Adds a normalized `_auth_type` field to each security scheme for
    consistent downstream processing.
    
    Args:
        spec: OpenAPI spec dictionary
        
    Returns:
        Spec with normalized security schemes
    """
    # OpenAPI 3.x
    if "components" in spec and "securitySchemes" in spec["components"]:
        for name, scheme in spec["components"]["securitySchemes"].items():
            scheme["_normalized"] = True
            scheme["_auth_type"] = _classify_auth_scheme(scheme)
    
    # OpenAPI 2.x / Swagger
    if "securityDefinitions" in spec:
        for name, scheme in spec["securityDefinitions"].items():
            scheme["_normalized"] = True
            scheme["_auth_type"] = _classify_auth_scheme(scheme)
    
    return spec


def _classify_auth_scheme(scheme: Dict[str, Any]) -> str:
    """Classify a security scheme into a standard type."""
    scheme_type = scheme.get("type", "").lower()
    
    # Direct mapping
    if scheme_type in AUTH_TYPE_MAPPING:
        return AUTH_TYPE_MAPPING[scheme_type]
    
    # HTTP scheme subtypes
    if scheme_type == "http":
        http_scheme = scheme.get("scheme", "").lower()
        if http_scheme == "bearer":
            return "bearer"
        elif http_scheme == "basic":
            return "basic"
        return "http"
    
    # API key location
    if scheme_type == "apikey":
        in_location = scheme.get("in", "header").lower()
        return f"api_key_{in_location}"
    
    return "unknown"


# =============================================================================
# Pass 3: Schema Normalization
# =============================================================================

def normalize_schemas(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize complex schema definitions (oneOf, anyOf, allOf).
    
    For schemas with complex composition, adds a `_flattened_properties`
    field containing all possible properties for easier processing.
    
    Args:
        spec: OpenAPI spec dictionary
        
    Returns:
        Spec with normalized schemas
    """
    schemas = {}
    
    # OpenAPI 3.x
    if "components" in spec and "schemas" in spec["components"]:
        schemas = spec["components"]["schemas"]
    # OpenAPI 2.x
    elif "definitions" in spec:
        schemas = spec["definitions"]
    
    for name, schema in schemas.items():
        _normalize_single_schema(schema, name)
    
    return spec


def _normalize_single_schema(schema: Dict[str, Any], name: str) -> None:
    """Normalize a single schema definition in-place."""
    if not isinstance(schema, dict):
        return
    
    # Already normalized
    if schema.get("_normalized"):
        return
    
    schema["_normalized"] = True
    
    # Handle allOf - merge properties from all schemas
    if "allOf" in schema:
        merged_props = {}
        merged_required = []
        for sub in schema["allOf"]:
            if isinstance(sub, dict):
                merged_props.update(sub.get("properties", {}))
                merged_required.extend(sub.get("required", []))
        if merged_props:
            schema["_flattened_properties"] = merged_props
            schema["_flattened_required"] = list(set(merged_required))
    
    # Handle oneOf/anyOf - collect all possible properties
    for composition_key in ["oneOf", "anyOf"]:
        if composition_key in schema:
            all_props = {}
            for variant in schema[composition_key]:
                if isinstance(variant, dict):
                    all_props.update(variant.get("properties", {}))
            if all_props:
                schema[f"_{composition_key}_properties"] = all_props
    
    # Recursively normalize nested schemas
    if "properties" in schema:
        for prop_name, prop_schema in schema["properties"].items():
            if isinstance(prop_schema, dict):
                _normalize_single_schema(prop_schema, f"{name}.{prop_name}")
    
    if "items" in schema and isinstance(schema["items"], dict):
        _normalize_single_schema(schema["items"], f"{name}[]")


# =============================================================================
# Pass 4: Server URL Normalization
# =============================================================================

def normalize_servers(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize server URLs and extract base paths.
    
    Handles:
    - Trailing slash removal
    - Variable extraction
    - Base path identification
    
    Args:
        spec: OpenAPI spec dictionary
        
    Returns:
        Spec with normalized server definitions
    """
    # OpenAPI 3.x
    if "servers" in spec:
        for server in spec["servers"]:
            if "url" in server:
                url = server["url"]
                # Remove trailing slash
                server["url"] = url.rstrip("/")
                # Extract variables from URL template
                server["_variables"] = _extract_url_variables(url)
                # Mark as normalized
                server["_normalized"] = True
    
    # OpenAPI 2.x - construct from host/basePath/schemes
    elif "host" in spec:
        schemes = spec.get("schemes", ["https"])
        host = spec["host"]
        base_path = spec.get("basePath", "")
        
        # Create synthetic servers entry
        spec["servers"] = [
            {
                "url": f"{scheme}://{host}{base_path}".rstrip("/"),
                "_normalized": True,
                "_synthetic": True,
            }
            for scheme in schemes
        ]
    
    return spec


def _extract_url_variables(url: str) -> List[str]:
    """Extract variable names from URL template (e.g., {version})."""
    return re.findall(r"\{([^}]+)\}", url)


# =============================================================================
# Pass 5: $ref Resolution
# =============================================================================

def resolve_common_refs(spec: Dict[str, Any], max_inline: int = 5) -> Dict[str, Any]:
    """
    Inline commonly referenced schemas to reduce $ref lookups.
    
    Only inlines schemas that are referenced fewer than `max_inline` times
    and are small (< 10 properties) to avoid spec bloat.
    
    Args:
        spec: OpenAPI spec dictionary
        max_inline: Maximum reference count for inlining
        
    Returns:
        Spec with inlined common refs
    """
    # Count refs
    ref_counts: Dict[str, int] = {}
    _count_refs(spec, ref_counts)
    
    # Identify candidates for inlining
    candidates = {
        ref for ref, count in ref_counts.items()
        if count <= max_inline
    }
    
    if not candidates:
        return spec
    
    # Get schema definitions
    schemas = {}
    if "components" in spec and "schemas" in spec["components"]:
        schemas = spec["components"]["schemas"]
    elif "definitions" in spec:
        schemas = spec["definitions"]
    
    # Filter to small schemas only
    small_candidates = set()
    for ref in candidates:
        schema_name = ref.split("/")[-1]
        if schema_name in schemas:
            schema = schemas[schema_name]
            if isinstance(schema, dict):
                prop_count = len(schema.get("properties", {}))
                if prop_count < 10:
                    small_candidates.add(ref)
    
    # Inline the candidates
    if small_candidates:
        logger.debug(f"Inlining {len(small_candidates)} small schema refs")
        spec = _inline_refs(spec, small_candidates, schemas)
    
    return spec


def _count_refs(obj: Any, counts: Dict[str, int]) -> None:
    """Recursively count $ref occurrences."""
    if isinstance(obj, dict):
        if "$ref" in obj:
            ref = obj["$ref"]
            counts[ref] = counts.get(ref, 0) + 1
        for value in obj.values():
            _count_refs(value, counts)
    elif isinstance(obj, list):
        for item in obj:
            _count_refs(item, counts)


def _inline_refs(obj: Any, refs_to_inline: Set[str], schemas: Dict[str, Any]) -> Any:
    """Recursively inline specified $refs."""
    if isinstance(obj, dict):
        if "$ref" in obj and obj["$ref"] in refs_to_inline:
            ref = obj["$ref"]
            schema_name = ref.split("/")[-1]
            if schema_name in schemas:
                # Return a copy of the schema instead of the ref
                inlined = copy.deepcopy(schemas[schema_name])
                inlined["_inlined_from"] = ref
                return inlined
        return {k: _inline_refs(v, refs_to_inline, schemas) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_inline_refs(item, refs_to_inline, schemas) for item in obj]
    return obj


# =============================================================================
# Pass 6: Path Parameter Extraction
# =============================================================================

def extract_path_parameters(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract and normalize path parameters from all paths.
    
    Adds `_path_params` to each path item with extracted parameter names.
    
    Args:
        spec: OpenAPI spec dictionary
        
    Returns:
        Spec with extracted path parameters
    """
    paths = spec.get("paths", {})
    
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        
        # Extract parameters from path template
        params = re.findall(r"\{([^}]+)\}", path)
        if params:
            path_item["_path_params"] = params
            path_item["_path_template"] = path
    
    return spec


# =============================================================================
# Utility Functions
# =============================================================================

def get_spec_info(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract summary information from a spec.
    
    Returns:
        Dictionary with:
        - title: API title
        - version: API version
        - openapi_version: OpenAPI spec version
        - endpoint_count: Number of endpoints
        - schema_count: Number of schemas
        - has_auth: Whether auth is defined
    """
    info = spec.get("info", {})
    
    # Count endpoints
    paths = spec.get("paths", {})
    endpoint_count = sum(
        1 for path_item in paths.values()
        if isinstance(path_item, dict)
        for method in ["get", "post", "put", "patch", "delete", "head", "options"]
        if method in path_item
    )
    
    # Count schemas
    schema_count = 0
    if "components" in spec and "schemas" in spec["components"]:
        schema_count = len(spec["components"]["schemas"])
    elif "definitions" in spec:
        schema_count = len(spec["definitions"])
    
    # Check auth
    has_auth = bool(
        spec.get("security") or
        spec.get("components", {}).get("securitySchemes") or
        spec.get("securityDefinitions")
    )
    
    return {
        "title": info.get("title", "Unknown API"),
        "version": info.get("version", "unknown"),
        "openapi_version": spec.get("openapi", spec.get("swagger", "unknown")),
        "endpoint_count": endpoint_count,
        "schema_count": schema_count,
        "has_auth": has_auth,
    }


def validate_spec_structure(spec: Dict[str, Any]) -> List[str]:
    """
    Validate basic OpenAPI spec structure.
    
    Returns:
        List of validation warnings (empty if valid)
    """
    warnings = []
    
    # Must have openapi or swagger version
    if "openapi" not in spec and "swagger" not in spec:
        warnings.append("Missing 'openapi' or 'swagger' version field")
    
    # Must have info
    if "info" not in spec:
        warnings.append("Missing 'info' section")
    elif not isinstance(spec["info"], dict):
        warnings.append("'info' should be an object")
    else:
        if "title" not in spec["info"]:
            warnings.append("Missing 'info.title'")
        if "version" not in spec["info"]:
            warnings.append("Missing 'info.version'")
    
    # Must have paths
    if "paths" not in spec:
        warnings.append("Missing 'paths' section")
    elif not isinstance(spec["paths"], dict):
        warnings.append("'paths' should be an object")
    
    return warnings
