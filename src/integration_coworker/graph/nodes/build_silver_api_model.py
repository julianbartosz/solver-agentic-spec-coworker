from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import (
    Endpoint, EndpointParameter, Schema, SchemaField, Entity, EntityRelationship
)

def build_silver_api_model(state: WorkflowState) -> WorkflowState:
    """
    Reads: openapi_spec
    Writes: endpoints, endpoint_parameters, schemas, schema_fields, entities, relationships
    """
    if not state.openapi_spec:
        state.errors.append("No openapi_spec to build Silver model from")
        state.completed_steps.append("build_silver_api_model")
        return state
    
    spec = state.openapi_spec
    
    try:
        # Extract endpoints from paths
        paths = spec.get("paths", {})
        for path, path_item in paths.items():
            for method, operation in path_item.items():
                if method.upper() not in ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]:
                    continue
                
                # Check if auth is required
                auth_required = "security" in operation or "security" in spec
                
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
                state.endpoints.append(endpoint)
                
                # Extract parameters
                for param in operation.get("parameters", []):
                    param_obj = EndpointParameter(
                        id=None,
                        endpoint_id=None,
                        name=param.get("name"),
                        location=param.get("in"),  # path, query, header, cookie
                        required=param.get("required", False),
                        schema_ref=param.get("schema", {}).get("type", "string"),
                        description=param.get("description"),
                    )
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
            state.schemas.append(schema)
            
            # Extract schema fields
            properties = schema_def.get("properties", {})
            for field_name, field_def in properties.items():
                field = SchemaField(
                    id=None,
                    schema_id=None,
                    name=field_name,
                    json_path=f"$.{field_name}",  # Simple JSON path
                    type=field_def.get("type", "string"),
                    format=field_def.get("format"),
                    required=field_name in schema_def.get("required", []),
                    description=field_def.get("description"),
                )
                state.schema_fields.append(field)
            
            # Heuristic: if schema has 'id' field, treat as entity
            if "id" in properties:
                entity = Entity(
                    id=None,
                    source_system_id=None,
                    name=schema_name,
                    schema_id=None,  # Will be linked after persistence
                    description=schema_def.get("description"),
                )
                state.entities.append(entity)
        
        # Simple relationship detection (Phase 2 - minimal)
        # Look for fields that reference other schema names
        for schema in state.schemas:
            props = schemas_dict.get(schema.name, {}).get("properties", {})
            for field_name, field_def in props.items():
                ref = field_def.get("$ref", "")
                if ref.startswith("#/components/schemas/"):
                    target_schema = ref.split("/")[-1]
                    relationship = EntityRelationship(
                        id=None,
                        source_system_id=None,
                        from_entity=schema.name,
                        to_entity=target_schema,
                        relationship_type="references",
                        cardinality="one-to-one",
                    )
                    state.relationships.append(relationship)
        
    except Exception as e:
        state.errors.append(f"Failed to build Silver API model: {str(e)}")
    
    state.completed_steps.append("build_silver_api_model")
    return state
