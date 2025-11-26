"""
build_silver_api_model node — extracts Silver layer entities from parsed OpenAPI specs.

Implements: Design Doc §3.4 Build Silver API Model
Touches: endpoints, endpoint_parameters, schemas, schema_fields, entities, relationships
"""
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import (
    Endpoint, EndpointParameter, Schema, SchemaField, Entity, EntityRelationship
)


def _extract_from_spec(spec: dict, state: WorkflowState, source_uri: str) -> dict[str, str]:
    """
    Extract endpoints, schemas, entities from a single parsed OpenAPI spec.
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
            # Tag with source URI for multi-spec tracking
            endpoint._source_uri = source_uri
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

    Reads: openapi_spec, plan["openapi_specs"]
    Writes: endpoints, endpoint_parameters, schemas, schema_fields, entities, relationships
    """
    # Get all parsed specs from plan (set by detect_and_parse_spec)
    all_specs: list[dict] = []

    if state.plan and "openapi_specs" in state.plan:
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

        for spec in all_specs:
            source_uri = spec.get("_source_uri", "unknown")
            schema_uris = _extract_from_spec(spec, state, source_uri)
            all_schema_uris.update(schema_uris)

        # Store schema->uri mapping in plan for persistence layer
        if state.plan is not None:
            state.plan["schema_name_to_uri"] = all_schema_uris

    except Exception as e:
        state.errors.append(f"Failed to build Silver API model: {str(e)}")

    state.completed_steps.append("build_silver_api_model")
    return state
