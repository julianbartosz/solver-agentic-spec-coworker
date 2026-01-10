"""
build_silver_api_model node — extracts Silver layer entities from parsed OpenAPI specs.

Implements: Design Doc §3.4 Build Silver API Model
Touches: endpoints, endpoint_parameters, schemas, schema_fields, entities, relationships

V1 Implementation:
  - Converts parsed OpenAPI (including HTML/PDF pseudo-OpenAPI) to Silver domain models
  - Multi-spec support: iterates state.openapi_specs for each source

V1.1 Spec Caching (FT-001):
  - Fast path when state.cache_hit=True: hydrate from DB instead of re-parsing
  - Queries existing endpoints/schemas/entities linked to cached spec_document

API-002: Multi-Spec Source Reference Handling
  - Links all extracted entities to their SourceRef
  - Uses state.source_refs for traceability
  - Enables multi-provider integrations with proper provenance

Protocol vNext:
  - Uses AdapterRegistry for native GraphQL/AsyncAPI parsing
  - Avoids pseudo-OpenAPI conversion for non-REST specs
  - Outputs List[Operation] to state.operations

V24-002: Shutdown-Aware Execution
  - Added shutdown checkpoints between major phases
  - Enables graceful interruption during long parsing operations
  - Uses ShutdownAwareLoop for multi-spec iteration
"""
import logging
from typing import Dict, List, Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.shutdown_aware import (
    shutdown_check_point,
    ShutdownAwareLoop,
    ShutdownInterruptError,
)
from integration_coworker.domain.models import (
    Endpoint, EndpointParameter, Schema, SchemaField, Entity, EntityRelationship, SourceRef
)
from integration_coworker.domain.ir import Operation
from integration_coworker.protocols import AdapterRegistry
from integration_coworker.sources.base import ParsedSpec, SourceType

logger = logging.getLogger(__name__)


def _hydrate_from_cache(state: WorkflowState) -> bool:
    """
    V1.1 FT-001: Hydrate Silver model from DB when cache_hit=True.
    
    Queries endpoints, schemas, entities linked to the cached spec_document_id.
    Returns True if hydration succeeded, False if fallback to parsing needed.
    
    V23-012: Validates cache_hit claim before attempting hydration.
    This prevents slow fallback paths when database was truncated but cache_hit
    flags persist in stale checkpoints.
    """
    if not state.spec_documents or not state.spec_documents[0].id:
        logger.debug("No cached spec_document_id, cannot hydrate")
        return False
    
    spec_doc_id = state.spec_documents[0].id
    
    # V23-012: Validate that cache_hit claim is backed by actual DB data
    # This catches the case where database was truncated but checkpoints have stale cache_hit=True
    try:
        from integration_coworker.persistence.cache_consistency import validate_cache_hit_claim
        if not validate_cache_hit_claim(spec_doc_id, state.provider_code):
            logger.warning(
                f"V23-012: Stale cache detected - spec_document_id={spec_doc_id} "
                f"has cache_hit=True but no data in database. Forcing re-parse."
            )
            # Clear the stale cache_hit flag to prevent repeated hydration attempts
            state.cache_hit = False
            return False
    except ImportError:
        pass  # Module not available, skip validation
    except Exception as e:
        logger.debug(f"V23-012: Cache validation error (non-fatal): {e}")
    
    try:
        from integration_coworker.persistence import db
        from integration_coworker.persistence.db import get_engine_type
        
        db.init_schema()
        # V27-003 Fix: Use context manager to prevent connection leaks
        with db.get_connection() as conn:
            engine = get_engine_type()
            cur = conn.cursor()
            
            # Ensure the destination lists exist and start clean. Hydration must
            # be deterministic per run; if we append into an already-populated
            # state we can get confusing/incorrect counts.
            if state.endpoints is None:
                state.endpoints = []
            else:
                state.endpoints.clear()
            if state.schemas is None:
                state.schemas = []
            else:
                state.schemas.clear()
            if state.entities is None:
                state.entities = []
            else:
                state.entities.clear()

            # Hydrate endpoints
            if engine == "postgres":
                cur.execute("""
                    SELECT id, source_system_id, path, method, operation_id, summary,
                           description, request_schema_id, response_schema_id, auth_required,
                           pagination_style, rate_limit_bucket
                    FROM spec_silver.endpoints
                    WHERE spec_document_id = %s
                """, (spec_doc_id,))
            else:
                cur.execute("""
                    SELECT id, source_system_id, path, method, operation_id, summary,
                           description, request_schema_id, response_schema_id, auth_required,
                           pagination_style, rate_limit_bucket
                    FROM endpoints
                    WHERE spec_document_id = ?
                """, (spec_doc_id,))
            
            endpoint_rows = cur.fetchall()
            for row in endpoint_rows:
                endpoint = Endpoint(
                    id=row[0],
                    source_system_id=row[1],
                    spec_document_id=spec_doc_id,
                    path=row[2],
                    method=row[3],
                    operation_id=row[4],
                    summary=row[5],
                    description=row[6],
                    request_schema_id=row[7],
                    response_schema_id=row[8],
                    auth_required=row[9],
                    pagination_style=row[10],
                    rate_limit_bucket=row[11],
                )
                state.endpoints.append(endpoint)
            
            # Determine source_system_id for this cached spec, then hydrate all
            # schema/entity rows for that source system.
            if engine == "postgres":
                cur.execute("""
                    SELECT source_system_id
                    FROM spec_silver.spec_documents
                    WHERE id = %s
                """, (spec_doc_id,))
            else:
                cur.execute("""
                    SELECT source_system_id
                    FROM spec_documents
                    WHERE id = ?
                """, (spec_doc_id,))

            row = cur.fetchone()
            source_system_id = row[0] if row else None
            if not source_system_id:
                logger.warning("Cache hydration: missing source_system_id for cached spec_document")
                return False

            # V38-004: Load base_url from source_systems and restore to state
            if engine == "postgres":
                cur.execute("""
                    SELECT base_url
                    FROM spec_silver.source_systems
                    WHERE id = %s
                """, (source_system_id,))
            else:
                cur.execute("""
                    SELECT base_url
                    FROM source_systems
                    WHERE id = ?
                """, (source_system_id,))
            base_url_row = cur.fetchone()
            if base_url_row and base_url_row[0]:
                state.api_base_url = base_url_row[0]
                logger.debug(f"V38-004: Cache hydration restored api_base_url={state.api_base_url}")

            # Hydrate schemas
            if engine == "postgres":
                cur.execute("""
                    SELECT id, source_system_id, name, ref
                    FROM spec_silver.schemas
                    WHERE source_system_id = %s
                """, (source_system_id,))
            else:
                cur.execute("""
                    SELECT id, source_system_id, name, ref
                    FROM schemas
                    WHERE source_system_id = ?
                """, (source_system_id,))
            
            schema_rows = cur.fetchall()
            for row in schema_rows:
                schema = Schema(
                    id=row[0],
                    source_system_id=row[1],
                    name=row[2],
                    ref=row[3],
                )
                state.schemas.append(schema)
            
            # Hydrate entities
            if engine == "postgres":
                cur.execute("""
                    SELECT id, source_system_id, name, schema_id, description
                    FROM spec_silver.entities
                    WHERE source_system_id = %s
                """, (source_system_id,))
            else:
                cur.execute("""
                    SELECT id, source_system_id, name, schema_id, description
                    FROM entities
                    WHERE source_system_id = ?
                """, (source_system_id,))
            
            entity_rows = cur.fetchall()
            for row in entity_rows:
                entity = Entity(
                    id=row[0],
                    source_system_id=row[1],
                    name=row[2],
                    schema_id=row[3],
                    description=row[4],
                )
                state.entities.append(entity)
        
        logger.info(f"Cache hydration: {len(state.endpoints)} endpoints, {len(state.schemas)} schemas, {len(state.entities)} entities")
        return True
        
    except Exception as e:
        logger.warning(f"Cache hydration failed, falling back to parsing: {e}")
        return False


def _get_source_ref_for_uri(state: WorkflowState, uri: str) -> Optional[SourceRef]:
    """Find the SourceRef matching a given URI."""
    for source_ref in state.source_refs:
        # Handle both SourceRef objects and legacy string refs
        if hasattr(source_ref, 'uri') and source_ref.uri == uri:
            return source_ref
    return None


def _convert_parsed_specs_to_operations(
    parsed_specs: List[ParsedSpec],
    registry: Optional[AdapterRegistry] = None,
) -> List[Operation]:
    """
    Protocol vNext: Convert ParsedSpec objects to protocol-agnostic Operations.
    
    Uses AdapterRegistry.convert_spec() — the SINGLE ENTRY POINT.
    This avoids pseudo-OpenAPI conversion for GraphQL/AsyncAPI specs.
    
    Args:
        parsed_specs: List of ParsedSpec from parsing step
        registry: Optional AdapterRegistry (created if not provided)
        
    Returns:
        List of Operation objects for all specs
    """
    from integration_coworker.protocols import create_default_registry
    
    if not parsed_specs:
        return []
    
    if registry is None:
        registry = create_default_registry()
    
    all_operations: List[Operation] = []
    
    for spec in parsed_specs:
        try:
            # SINGLE ENTRY POINT: registry.convert_spec(ParsedSpec)
            # All metadata extraction happens inside the registry
            operations = registry.convert_spec(spec)
            all_operations.extend(operations)
            
            if operations:
                logger.info(f"Converted {spec.source_uri}: {len(operations)} operations")
            
        except Exception as e:
            logger.warning(f"Adapter conversion failed for {spec.source_uri}: {e}")
            # Non-fatal: continue with other specs
    
    return all_operations


def _extract_required_headers(operation: dict, path: str) -> dict | None:
    """
    V38-005: Extract required headers for beta/special endpoints.
    
    Uses a multi-strategy approach to extract headers from OpenAPI specs:
    1. Parse curl examples in x-oaiMeta.examples (most reliable)
    2. Check explicit header parameters marked as required with defaults
    3. Detect vendor-specific beta flags and infer headers from API group
    
    This approach is spec-driven and doesn't hardcode specific endpoints,
    making it work across different API providers.
    
    Args:
        operation: OpenAPI operation dict
        path: API endpoint path
        
    Returns:
        Dict of header name -> value, or None if no required headers
    """
    import re
    
    headers: dict[str, str] = {}
    
    # Strategy 1: Extract headers from curl examples (most reliable source of truth)
    # Many APIs document required headers in their curl examples
    x_oai_meta = operation.get("x-oaiMeta", {})
    if isinstance(x_oai_meta, dict):
        examples = x_oai_meta.get("examples", {})
        if isinstance(examples, dict):
            curl_example = examples.get("request", {}).get("curl", "")
            if curl_example:
                # Parse -H "Header-Name: value" patterns from curl
                # Using a more robust pattern that handles malformed entries
                # by stopping at quotes, newlines, or next -H flag
                header_pattern = r'-H\s+"([^:]+):\s*([^"\n\\]+)'
                for match in re.finditer(header_pattern, curl_example):
                    header_name, header_value = match.groups()
                    header_value = header_value.strip().rstrip('"')
                    # Skip common headers that are handled elsewhere
                    if header_name.lower() not in ("content-type", "authorization", "accept"):
                        headers[header_name] = header_value
    
    # Strategy 2: Check explicit header parameters marked as required
    for param in operation.get("parameters", []):
        if param.get("in") == "header" and param.get("required") is True:
            param_name = param.get("name", "")
            # Skip auth headers (handled separately by auth policies)
            if param_name.lower() not in ("authorization", "x-api-key", "api-key"):
                # Try to get default value from schema
                schema = param.get("schema", {})
                default_value = schema.get("default") or schema.get("enum", [None])[0]
                if default_value:
                    headers[param_name] = str(default_value)
    
    # Strategy 3: Detect vendor-specific beta markers and infer headers
    # This is a fallback when headers aren't in curl examples
    if isinstance(x_oai_meta, dict) and x_oai_meta.get("beta") is True:
        # Check if we already extracted the beta header from curl
        if "OpenAI-Beta" not in headers:
            # Infer header value from API group metadata
            group = x_oai_meta.get("group", "")
            if group:
                # OpenAI convention: group name becomes header value with version
                # e.g., group="assistants" -> "OpenAI-Beta: assistants=v2"
                headers["OpenAI-Beta"] = f"{group}=v2"
    
    # Strategy 4: Check for other vendor extensions that indicate required headers
    # x-required-headers is a common extension pattern
    x_required_headers = operation.get("x-required-headers", {})
    if isinstance(x_required_headers, dict):
        for header_name, header_value in x_required_headers.items():
            if header_name.lower() not in ("authorization", "content-type"):
                headers[header_name] = str(header_value)
    
    return headers if headers else None


def _extract_from_spec(spec: dict, state: WorkflowState, source_uri: str, source_ref: Optional[SourceRef] = None) -> dict[str, str]:
    """
    Extract endpoints, schemas, entities from a single parsed OpenAPI spec.
    
    API-002: All extracted entities are linked to their SourceRef for traceability.
    
    Returns a mapping of schema_name -> source_uri for relationship detection.
    """
    endpoint_schema_names: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    schema_name_to_uri: dict[str, str] = {}

    # Extract endpoints from paths
    paths = spec.get("paths", {})
    for path, path_item in paths.items():
        for method, operation in path_item.items():
            if method.upper() not in ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]:
                continue

            auth_required = "security" in operation or "security" in spec

            # V38-005: Extract required headers for beta/special endpoints
            required_headers = _extract_required_headers(operation, path)

            # Extract request schema name from requestBody
            request_schema_name = None
            request_body = operation.get("requestBody", {})
            if request_body:
                content = request_body.get("content", {})
                json_content = content.get("application/json", {})
                if json_content:
                    schema_def = json_content.get("schema", {})
                    ref = schema_def.get("$ref", "")
                    if ref.startswith("#/components/schemas/"):
                        request_schema_name = ref.split("/")[-1]

            # Extract response schema name from responses
            response_schema_name = None
            responses = operation.get("responses", {})
            for status_code in ["200", "201", "202", "204"]:
                if status_code in responses:
                    response_def = responses[status_code]
                    content = response_def.get("content", {})
                    json_content = content.get("application/json", {})
                    if json_content:
                        schema_def = json_content.get("schema", {})
                        ref = schema_def.get("$ref", "")
                        if ref.startswith("#/components/schemas/"):
                            response_schema_name = ref.split("/")[-1]
                            break

            endpoint = Endpoint(
                id=None,
                source_system_id=None,
                spec_document_id=None,
                path=path,
                method=method.upper(),
                operation_id=operation.get("operationId"),
                summary=operation.get("summary"),
                description=operation.get("description"),
                request_schema_id=None,
                response_schema_id=None,
                auth_required=auth_required,
                pagination_style=None,
                rate_limit_bucket=None,
                required_headers=required_headers if required_headers else None,
            )
            # API-002: Tag with source URI and SourceRef for traceability
            endpoint._source_uri = source_uri
            if source_ref:
                endpoint._source_ref = source_ref
            state.endpoints.append(endpoint)

            if request_schema_name or response_schema_name:
                endpoint_schema_names[(method.upper(), path)] = (request_schema_name, response_schema_name)

            # Extract parameters
            for param in operation.get("parameters", []):
                param_obj = EndpointParameter(
                    id=None,
                    endpoint_id=None,
                    name=param.get("name"),
                    location=param.get("in"),
                    required=param.get("required", False),
                    schema_ref=param.get("schema", {}).get("type", "string"),
                    description=param.get("description"),
                )
                param_obj._source_uri = source_uri
                state.endpoint_parameters.append(param_obj)

    # Extract schemas from components
    components = spec.get("components", {})
    schemas_dict = components.get("schemas", {})

    for schema_name, schema_def in schemas_dict.items():
        if not isinstance(schema_def, dict):
            continue

        schema = Schema(
            id=None,
            source_system_id=None,
            name=schema_name,
            ref=f"#/components/schemas/{schema_name}",
        )
        schema._source_uri = source_uri
        state.schemas.append(schema)
        schema_name_to_uri[schema_name] = source_uri

        # Extract schema fields
        properties = schema_def.get("properties", {})
        for field_name, field_def in properties.items():
            field = SchemaField(
                id=None,
                schema_id=None,
                name=field_name,
                json_path=f"$.{field_name}",
                type=field_def.get("type", "string"),
                format=field_def.get("format"),
                required=field_name in schema_def.get("required", []),
                description=field_def.get("description"),
            )
            field._source_uri = source_uri
            state.schema_fields.append(field)

        # Heuristic: if schema has 'id' field, treat as entity
        if "id" in properties:
            entity = Entity(
                id=None,
                source_system_id=None,
                name=schema_name,
                schema_id=None,
                description=schema_def.get("description"),
            )
            entity._source_uri = source_uri
            state.entities.append(entity)

    # Link endpoint request/response schemas by name
    schema_name_map = {schema.name: schema for schema in state.schemas}

    for endpoint in state.endpoints:
        key = (endpoint.method, endpoint.path)
        if key in endpoint_schema_names:
            req_name, resp_name = endpoint_schema_names[key]
            if req_name and req_name in schema_name_map:
                endpoint._request_schema_name = req_name
            if resp_name and resp_name in schema_name_map:
                endpoint._response_schema_name = resp_name

    # Relationship detection
    for schema in state.schemas:
        if hasattr(schema, "_source_uri") and schema._source_uri == source_uri:
            props = schemas_dict.get(schema.name, {}).get("properties", {})
            for field_name, field_def in props.items():
                ref = field_def.get("$ref", "")
                if ref.startswith("#/components/schemas/"):
                    relationship = EntityRelationship(
                        id=None,
                        source_system_id=None,
                        source_entity_id=None,
                        target_entity_id=None,
                        relationship_type="references",
                    )
                    relationship._source_uri = source_uri
                    state.relationships.append(relationship)

    return schema_name_to_uri


def build_silver_api_model(state: WorkflowState) -> WorkflowState:
    """
    Build Silver API model from all parsed OpenAPI specs (primary + supporting).

    V1.1 Spec Caching (FT-001):
    - Fast path when cache_hit=True: hydrate from DB, skip expensive parsing
    - Reduces latency for repeated specs from ~2s to ~100ms

    V2 Section 3.12: Multi-spec support
    - Uses pending_specs and parsed_specs for spec tracking
    - Falls back to openapi_spec/plan["openapi_specs"] for compatibility

    API-002: Links all extracted entities to their SourceRef for traceability.

    Reads: openapi_spec, plan["openapi_specs"], pending_specs, parsed_specs, source_refs, cache_hit
    Writes: endpoints, endpoint_parameters, schemas, schema_fields, entities, relationships
    """
    # V1.1 FT-001: Fast path for cache hits
    if state.cache_hit:
        logger.info("Cache hit detected, attempting DB hydration")
        if _hydrate_from_cache(state):
            # V1.2: Validate hydration actually produced endpoints
            # If hydration succeeded but returned no data, fall back to parsing
            if state.endpoints:
                logger.info("Successfully hydrated Silver model from cache")
                
                # V38-003: Extract api_base_url BEFORE clearing openapi_spec
                # This fixes URL bug where cache hits didn't preserve the full URL
                if not state.api_base_url:
                    from integration_coworker.codegen.paths import derive_base_url
                    state.api_base_url = derive_base_url(state)
                    logger.debug(f"V38-003: Cache hit - preserved api_base_url={state.api_base_url}")
                
                # V3 State Slimming: Clear large spec data even on cache hit
                state.openapi_spec = None
                state.parsed_specs = []
                if state.plan is not None and "openapi_specs" in state.plan:
                    state.plan["openapi_specs"] = []
                state.completed_steps.append("build_silver_api_model")
                return state
            else:
                logger.warning("Cache hydration returned 0 endpoints, falling back to parsing")
        else:
            logger.warning("Cache hydration failed, falling back to parsing")
    
    # Get all parsed specs - V2 prefers parsed_specs, falls back to legacy
    all_specs: list[dict] = []

    # V2 path: use parsed_specs if available
    if state.parsed_specs:
        all_specs = state.parsed_specs
    elif state.plan and "openapi_specs" in state.plan:
        all_specs = state.plan["openapi_specs"]
    elif state.openapi_spec:
        # Fallback: only primary spec available
        all_specs = [state.openapi_spec]

    if not all_specs:
        state.errors.append("No openapi_spec to build Silver model from")
        state.completed_steps.append("build_silver_api_model")
        return state

    # V24-002: Shutdown checkpoint before heavy parsing
    try:
        shutdown_check_point("build_silver_api_model: before spec parsing")
    except ShutdownInterruptError:
        logger.warning("V24-002: Shutdown requested, aborting Silver model build")
        state.errors.append("Build interrupted: shutdown requested")
        state.completed_steps.append("build_silver_api_model")
        return state

    # Protocol vNext: Convert ParsedSpec to Operations using native adapters
    # This runs BEFORE legacy OpenAPI extraction to populate state.operations
    # Works for typed ParsedSpec objects (GraphQL, AsyncAPI, REST)
    parsed_spec_objects = [
        s for s in all_specs 
        if isinstance(s, ParsedSpec)
    ]
    if parsed_spec_objects:
        try:
            operations = _convert_parsed_specs_to_operations(parsed_spec_objects)
            state.operations.extend(operations)
            logger.info(f"Protocol vNext: {len(operations)} operations from {len(parsed_spec_objects)} specs")
        except Exception as e:
            # Non-fatal: log and continue with legacy extraction
            logger.warning(f"Protocol vNext conversion failed: {e}")
            state.warnings.append(f"Protocol vNext conversion failed: {e}")

    try:
        all_schema_uris: dict[str, str] = {}

        for idx, spec in enumerate(all_specs):
            # V24-002: Check shutdown between specs (for multi-spec processing)
            if idx > 0 and idx % 5 == 0:
                try:
                    shutdown_check_point(f"build_silver_api_model: processing spec {idx}/{len(all_specs)}")
                except ShutdownInterruptError:
                    logger.warning(f"V24-002: Shutdown requested after processing {idx} specs")
                    state.errors.append(f"Build interrupted: shutdown requested after {idx} specs")
                    break

            # Protocol vNext: Handle both ParsedSpec objects and legacy dicts
            if isinstance(spec, ParsedSpec):
                # Extract dict data and URI from ParsedSpec
                spec_dict = spec.data if isinstance(spec.data, dict) else {}
                source_uri = spec.source_uri or "unknown"
            else:
                # Legacy dict path
                spec_dict = spec
                source_uri = spec_dict.get("_source_uri", "unknown")
            
            # V2: Enrich with pending_specs info if available
            if idx < len(state.pending_specs):
                spec_info = state.pending_specs[idx]
                if source_uri == "unknown":
                    source_uri = spec_info.get("ref", "unknown")
                # Tag endpoints with provider_code for multi-spec
                provider_code = spec_info.get("provider_code", state.provider_code)
            else:
                provider_code = state.provider_code
            
            # API-002: Get the SourceRef for this URI
            source_ref = _get_source_ref_for_uri(state, source_uri)
            
            # Only extract from OpenAPI-compatible dicts (has paths key)
            if isinstance(spec_dict, dict) and "paths" in spec_dict:
                schema_uris = _extract_from_spec(spec_dict, state, source_uri, source_ref=source_ref)
                all_schema_uris.update(schema_uris)
            
            # V2: Tag all extracted items with provider_code
            for endpoint in state.endpoints:
                if hasattr(endpoint, "_source_uri") and endpoint._source_uri == source_uri:
                    endpoint._provider_code = provider_code

        # Store schema->uri mapping in plan for persistence layer
        if state.plan is not None:
            state.plan["schema_name_to_uri"] = all_schema_uris

    except Exception as e:
        state.errors.append(f"Failed to build Silver API model: {str(e)}")

    # V23-CACHE: Propagate spec_document_id to all endpoints BEFORE state_gc clears spec_documents
    # This ensures endpoints have valid FK when persisted later
    spec_document_id = state.primary_spec_document_id
    if not spec_document_id and state.spec_documents:
        spec_document_id = state.spec_documents[0].id
    if spec_document_id:
        endpoints_without_fk = [e for e in state.endpoints if not e.spec_document_id]
        if endpoints_without_fk:
            logger.debug(f"V23-CACHE: Setting spec_document_id={spec_document_id} on {len(endpoints_without_fk)} endpoints")
            for endpoint in endpoints_without_fk:
                endpoint.spec_document_id = spec_document_id
    elif state.endpoints:
        logger.warning(f"V23-CACHE: No spec_document_id available for {len(state.endpoints)} endpoints")

    # V38-002: Extract and preserve API base URL BEFORE clearing openapi_spec
    # This ensures derive_base_url() can return correct URL during code generation
    if not state.api_base_url:
        from integration_coworker.codegen.paths import derive_base_url
        state.api_base_url = derive_base_url(state)
        logger.debug(f"V38-002: Preserved api_base_url={state.api_base_url} before spec cleanup")

    # V3 State Slimming: Clear large spec data after extraction
    # The data is now in endpoints, schemas, entities - no need to carry it forward
    # This prevents LangSmith payload size errors for large specs (Stripe: 7MB -> 300MB+)
    state.openapi_spec = None
    state.parsed_specs = []
    if state.plan is not None and "openapi_specs" in state.plan:
        state.plan["openapi_specs"] = []  # Clear from plan too
    logger.debug("Cleared openapi_spec, parsed_specs, and plan['openapi_specs'] after Silver model extraction")

    # V22-MEM: Use state_gc for aggressive cleanup after silver model is built
    # This addresses LangGraph state copy overhead for large specs
    try:
        from integration_coworker.graph.state_gc import cleanup_after_silver
        cleanup_after_silver(state)
    except Exception as e:
        logger.debug(f"state_gc cleanup_after_silver failed (non-fatal): {e}")

    state.completed_steps.append("build_silver_api_model")
    return state
