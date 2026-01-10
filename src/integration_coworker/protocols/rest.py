"""
REST/OpenAPI protocol adapter.

Converts OpenAPI/Swagger specifications to Operation IR.

Design: docs/PROTOCOL_SUPPORT_VNEXT.md Option B
Date: 2025-12-22
"""
import re
import logging
from typing import List, Dict, Any, Optional

from integration_coworker.domain.ir import (
    Operation,
    ProtocolType,
    CommunicationPattern,
    RestMetadata,
)
from .base import ProtocolAdapter

logger = logging.getLogger(__name__)


class RestAdapter(ProtocolAdapter):
    """
    Adapter for REST/OpenAPI specifications.
    
    Handles:
    - OpenAPI 2.0 (Swagger)
    - OpenAPI 3.0.x
    - OpenAPI 3.1.x
    """
    
    @property
    def protocol_type(self) -> ProtocolType:
        return ProtocolType.REST
    
    def can_handle(self, spec_data: Dict[str, Any], metadata: Dict[str, Any]) -> bool:
        """Check if spec is OpenAPI/Swagger (and NOT converted from another format)."""
        parsed_from = metadata.get("_parsed_from", "")
        
        # Reject if this is a conversion from another protocol
        if parsed_from in ("graphql", "asyncapi"):
            return False
        
        # Check for native OpenAPI markers
        if "openapi" in spec_data:
            return True
        if "swagger" in spec_data:
            return True
        
        # Check metadata hint for native OpenAPI
        if parsed_from in ("openapi", "swagger", ""):
            # Empty _parsed_from with paths = likely native OpenAPI
            if "paths" in spec_data and "openapi" not in metadata.get("_parsed_from", "asyncapi"):
                return True
        
        return False
    
    def convert(
        self,
        spec_data: Dict[str, Any],
        metadata: Dict[str, Any],
        source_uri: str,
    ) -> List[Operation]:
        """Convert OpenAPI spec to Operations."""
        operations: List[Operation] = []
        
        paths = spec_data.get("paths", {})
        
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
                
            for method, operation_data in path_item.items():
                # Skip non-HTTP methods (parameters, servers, $ref, etc.)
                if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                    continue
                
                if not isinstance(operation_data, dict):
                    continue
                
                op = self._extract_operation(
                    path, method, operation_data, spec_data, source_uri
                )
                operations.append(op)
        
        logger.info(f"Extracted {len(operations)} REST operations from {source_uri}")
        return operations
    
    def _extract_operation(
        self,
        path: str,
        method: str,
        operation_data: Dict[str, Any],
        spec: Dict[str, Any],
        source_uri: str,
    ) -> Operation:
        """Extract a single Operation from OpenAPI path item."""
        
        # Extract path parameters
        path_params = re.findall(r'\{(\w+)\}', path)
        
        # Determine input schema
        input_schema_ref = None
        request_body = operation_data.get("requestBody", {})
        if request_body and isinstance(request_body, dict):
            content = request_body.get("content", {})
            json_content = content.get("application/json", {})
            schema = json_content.get("schema", {})
            input_schema_ref = schema.get("$ref") if isinstance(schema, dict) else None
        
        # Determine output schema
        output_schema_ref = None
        responses = operation_data.get("responses", {})
        for status in ("200", "201", "202", "204"):
            if status in responses and isinstance(responses[status], dict):
                content = responses[status].get("content", {})
                json_content = content.get("application/json", {})
                schema = json_content.get("schema", {})
                output_schema_ref = schema.get("$ref") if isinstance(schema, dict) else None
                if output_schema_ref:
                    break
        
        # Determine auth requirement
        auth_required = "security" in operation_data or "security" in spec
        
        # Build operation name
        operation_id = operation_data.get("operationId")
        if not operation_id:
            # Generate from path and method
            clean_path = path.replace("/", "_").replace("{", "").replace("}", "").strip("_")
            operation_id = f"{method.lower()}_{clean_path}"
        
        return Operation(
            name=operation_id,
            operation_id=operation_id,
            summary=operation_data.get("summary"),
            description=operation_data.get("description"),
            tags=operation_data.get("tags", []),
            protocol=ProtocolType.REST,
            communication_pattern=CommunicationPattern.UNARY,
            input_schema_ref=input_schema_ref,
            output_schema_ref=output_schema_ref,
            auth_required=auth_required,
            metadata=RestMetadata(
                path=path,
                method=method.upper(),
                path_params=path_params,
            ),
            source_uri=source_uri,
            conversion_quality="deterministic",
        )
