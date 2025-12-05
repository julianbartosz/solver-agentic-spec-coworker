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
"""
import logging
from typing import Dict, Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import (
    Endpoint, EndpointParameter, Schema, SchemaField, Entity, EntityRelationship, SourceRef
)

logger = logging.getLogger(__name__)


def _hydrate_from_cache(state: WorkflowState) -> bool:
    """
    V1.1 FT-001: Hydrate Silver model from DB when cache_hit=True.
    
    Queries endpoints, schemas, entities linked to the cached spec_document_id.
    Returns True if hydration succeeded, False if fallback to parsing needed.
    """
    if not state.spec_documents or not state.spec_documents[0].id:
        logger.debug("No cached spec_document_id, cannot hydrate")
        return False
    
    spec_doc_id = state.spec_documents[0].id
    
    try:
        from integration_coworker.persistence import db
        from integration_coworker.persistence.db import get_engine_type
        
        db.init_schema()
        conn = db.get_connection()
        engine = get_engine_type()
        cur = conn.cursor()
        
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
        
        # Hydrate schemas
        if engine == "postgres":
            cur.execute("""
                SELECT id, source_system_id, name, ref
                FROM spec_silver.schemas
                WHERE source_system_id = (
                    SELECT source_system_id FROM spec_silver.spec_documents WHERE id = %s
                )
            """, (spec_doc_id,))
        else:
            cur.execute("""
                SELECT id, source_system_id, name, ref
                FROM schemas
                WHERE source_system_id = (
                    SELECT source_system_id FROM spec_documents WHERE id = ?
                )
            """, (spec_doc_id,))
        
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
                WHERE source_system_id = (
                    SELECT source_system_id FROM spec_silver.spec_documents WHERE id = %s
                )
            """, (spec_doc_id,))
        else:
            cur.execute("""
                SELECT id, source_system_id, name, schema_id, description
                FROM entities
                WHERE source_system_id = (
                    SELECT source_system_id FROM spec_documents WHERE id = ?
                )
            """, (spec_doc_id,))
        
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
        
        conn.close()
        
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
            logger.info("Successfully hydrated Silver model from cache")
            state.completed_steps.append("build_silver_api_model")
            return state
        else:
            logger.warning("Cache hydration failed, falling back to parsing")
            # Continue with normal parsing below
    
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

    try:
        all_schema_uris: dict[str, str] = {}

        for idx, spec in enumerate(all_specs):
            # Determine source_uri and provider_code
            source_uri = spec.get("_source_uri", "unknown")
            
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
            
            schema_uris = _extract_from_spec(spec, state, source_uri, source_ref=source_ref)
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

    state.completed_steps.append("build_silver_api_model")
    return state
