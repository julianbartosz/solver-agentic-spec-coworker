"""
GraphQL protocol adapter.

Converts NATIVE GraphQL SDL schemas to Operation IR, preserving:
- Query operations → UNARY
- Mutation operations → UNARY  
- Subscription operations → SUBSCRIBE with transport metadata

CRITICAL: This adapter parses GraphQL SDL directly using graphql-core.
It does NOT extract from lossy pseudo-OpenAPI conversions.

TRANSPORT SEMANTICS (per graphql.org/learn/subscriptions):
GraphQL subscriptions are TRANSPORT-AGNOSTIC. The GraphQL spec does not
mandate any particular transport; servers choose their implementation.

Common transports include:
- WebSocket with graphql-ws protocol (subprotocol "graphql-transport-ws")
- WebSocket with legacy subscriptions-transport-ws (subprotocol "graphql-ws")
- Server-Sent Events (SSE)

This adapter DEFAULTS to WebSocket + graphql-transport-ws because it's the
most common, but the transport is CONFIGURABLE via GraphQLAdapterConfig.

References:
- GraphQL subscriptions (transport-agnostic): https://graphql.org/learn/subscriptions/
- graphql-ws protocol spec: https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md
- graphql-ws PROTOCOL.md (subprotocol name): https://unpkg.com/graphql-ws/PROTOCOL.md
- Apollo migration guide: https://www.apollographql.com/docs/react/data/subscriptions

Design: docs/PROTOCOL_SUPPORT_VNEXT.md Option B
Date: 2025-12-22
"""
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Literal

from integration_coworker.domain.ir import (
    Operation,
    ProtocolType,
    CommunicationPattern,
    GraphQLMetadata,
)
from .base import ProtocolAdapter

logger = logging.getLogger(__name__)

# Check if graphql-core is available
try:
    from graphql import parse, build_ast_schema
    from graphql.type import (
        GraphQLObjectType,
        GraphQLField,
        GraphQLNonNull,
        GraphQLList,
    )
    HAS_GRAPHQL_CORE = True
except ImportError:
    HAS_GRAPHQL_CORE = False
    logger.warning("graphql-core not installed. GraphQL SDL parsing will be limited.")


SubscriptionTransport = Literal["websocket", "sse", "http"]


@dataclass
class GraphQLAdapterConfig:
    """
    Configuration for GraphQL adapter.
    
    Attributes:
        subscription_transport: Transport for subscriptions ("websocket", "sse", "http")
        ws_subprotocol: WebSocket subprotocol when transport is "websocket"
            - "graphql-transport-ws" (modern, recommended)
            - "graphql-ws" (legacy Apollo subscriptions-transport-ws)
        endpoint_url: Default GraphQL endpoint URL
    """
    subscription_transport: SubscriptionTransport = "websocket"
    ws_subprotocol: str = "graphql-transport-ws"
    endpoint_url: str = "/graphql"


class GraphQLAdapter(ProtocolAdapter):
    """
    Adapter for GraphQL specifications.
    
    Parses NATIVE GraphQL SDL and converts to Operations with proper
    semantic preservation:
    
    - Queries → UNARY communication (HTTP POST to endpoint)
    - Mutations → UNARY communication (HTTP POST to endpoint)
    - Subscriptions → SUBSCRIBE communication (transport-dependent)
    
    TRANSPORT IS CONFIGURABLE (GraphQL spec is transport-agnostic):
    - Default: WebSocket with subprotocol "graphql-transport-ws"
    - Override via GraphQLAdapterConfig for legacy or SSE setups
    
    The graphql-ws library uses subprotocol "graphql-transport-ws".
    Legacy Apollo subscriptions-transport-ws used "graphql-ws" (confusing!).
    """
    
    def __init__(self, config: Optional[GraphQLAdapterConfig] = None):
        self._config = config or GraphQLAdapterConfig()
    
    @property
    def config(self) -> GraphQLAdapterConfig:
        """Get current configuration."""
        return self._config
    
    @property
    def protocol_type(self) -> ProtocolType:
        return ProtocolType.GRAPHQL
    
    def can_handle(self, spec_data: Dict[str, Any], metadata: Dict[str, Any]) -> bool:
        """Check if spec is GraphQL."""
        parsed_from = metadata.get("_parsed_from", "")
        
        # Native GraphQL SDL marker
        if parsed_from == "graphql":
            return True
        
        # Check for SDL content in raw_content
        raw_content = metadata.get("_raw_content", "")
        if isinstance(raw_content, str) and self._looks_like_graphql_sdl(raw_content):
            return True
        
        return False
    
    def _looks_like_graphql_sdl(self, content: str) -> bool:
        """Heuristic check for GraphQL SDL content."""
        if not content:
            return False
        sample = content[:2000].lower()
        indicators = ["type query", "type mutation", "type subscription", "schema {"]
        return any(ind in sample for ind in indicators)
    
    def convert(
        self,
        spec_data: Dict[str, Any],
        metadata: Dict[str, Any],
        source_uri: str,
    ) -> List[Operation]:
        """
        Convert GraphQL schema to Operations.
        
        Requires either:
        1. Raw SDL content in metadata._raw_content (preferred)
        2. Already-parsed pseudo-OpenAPI structure (lossy fallback)
        """
        operations: List[Operation] = []
        
        # Prefer native SDL parsing
        raw_content = metadata.get("_raw_content", "")
        if HAS_GRAPHQL_CORE and isinstance(raw_content, str) and raw_content.strip():
            try:
                operations = self._convert_from_sdl(raw_content, source_uri)
                logger.info(f"Parsed {len(operations)} GraphQL operations from native SDL")
                return operations
            except Exception as e:
                logger.warning(f"Native SDL parsing failed: {e}, falling back to pseudo-OpenAPI")
        
        # Fallback: extract from pseudo-OpenAPI (lossy)
        if "paths" in spec_data:
            operations = self._convert_from_pseudo_openapi(spec_data, source_uri)
            # Mark as lossy
            for op in operations:
                op.conversion_quality = "best_effort"
            logger.warning(f"Extracted {len(operations)} GraphQL operations from pseudo-OpenAPI (lossy)")
        
        return operations
    
    def _convert_from_sdl(self, sdl_content: str, source_uri: str) -> List[Operation]:
        """
        Parse GraphQL SDL using graphql-core and extract operations.
        
        This is the NATIVE parsing path that preserves full semantics.
        """
        if not HAS_GRAPHQL_CORE:
            raise ImportError("graphql-core required for native SDL parsing")
        
        operations: List[Operation] = []
        
        # Parse SDL and build schema
        document = parse(sdl_content)
        schema = build_ast_schema(document)
        
        # Extract Query operations
        query_type = schema.query_type
        if query_type:
            for field_name, field in query_type.fields.items():
                op = self._field_to_operation(
                    field_name, field, "query", source_uri
                )
                operations.append(op)
        
        # Extract Mutation operations
        mutation_type = schema.mutation_type
        if mutation_type:
            for field_name, field in mutation_type.fields.items():
                op = self._field_to_operation(
                    field_name, field, "mutation", source_uri
                )
                operations.append(op)
        
        # Extract Subscription operations (CRITICAL: preserve streaming semantics)
        subscription_type = schema.subscription_type
        if subscription_type:
            for field_name, field in subscription_type.fields.items():
                op = self._subscription_to_operation(
                    field_name, field, source_uri
                )
                operations.append(op)
        
        return operations
    
    def _field_to_operation(
        self,
        field_name: str,
        field: "GraphQLField",
        operation_type: str,  # "query" or "mutation"
        source_uri: str,
    ) -> Operation:
        """Convert a GraphQL Query/Mutation field to an Operation."""
        return Operation(
            name=field_name,
            operation_id=f"{operation_type}_{field_name}",
            summary=field.description,
            protocol=ProtocolType.GRAPHQL,
            communication_pattern=CommunicationPattern.UNARY,
            output_schema_ref=self._type_to_ref(field.type),
            metadata=GraphQLMetadata(
                operation_type=operation_type,
                field_name=field_name,
                endpoint_url=self._config.endpoint_url,
                requires_websocket=False,
            ),
            source_uri=source_uri,
            conversion_quality="deterministic",
        )
    
    def _subscription_to_operation(
        self,
        field_name: str,
        field: "GraphQLField",
        source_uri: str,
    ) -> Operation:
        """
        Convert a GraphQL Subscription field to an Operation.
        
        TRANSPORT IS CONFIGURABLE (GraphQL spec is transport-agnostic):
        - Default: WebSocket with subprotocol "graphql-transport-ws"
        - Override via GraphQLAdapterConfig for legacy or SSE setups
        
        Common subprotocols:
        - "graphql-transport-ws" (modern graphql-ws library, recommended)
        - "graphql-ws" (legacy Apollo subscriptions-transport-ws, deprecated)
        
        References:
        - https://graphql.org/learn/subscriptions/ (transport-agnostic spec)
        - https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md
        - https://www.apollographql.com/docs/react/data/subscriptions/
        """
        transport = self._config.subscription_transport
        requires_websocket = transport == "websocket"
        ws_protocol = self._config.ws_subprotocol if requires_websocket else None
        
        return Operation(
            name=field_name,
            operation_id=f"subscription_{field_name}",
            summary=field.description,
            protocol=ProtocolType.GRAPHQL,
            communication_pattern=CommunicationPattern.SUBSCRIBE,  # NOT UNARY!
            output_schema_ref=self._type_to_ref(field.type),
            metadata=GraphQLMetadata(
                operation_type="subscription",
                field_name=field_name,
                endpoint_url=self._config.endpoint_url,
                requires_websocket=requires_websocket,
                ws_protocol=ws_protocol,
            ),
            source_uri=source_uri,
            conversion_quality="deterministic",
        )
    
    def _type_to_ref(self, gql_type) -> Optional[str]:
        """Convert GraphQL type to a schema reference string."""
        if not HAS_GRAPHQL_CORE:
            return None
        
        # Unwrap NonNull and List
        while isinstance(gql_type, (GraphQLNonNull, GraphQLList)):
            gql_type = gql_type.of_type
        
        type_name = getattr(gql_type, "name", str(gql_type))
        
        # Skip built-in scalars
        if type_name in ("String", "Int", "Float", "Boolean", "ID"):
            return None
        
        return f"#/components/schemas/{type_name}"
    
    def _convert_from_pseudo_openapi(
        self,
        spec_data: Dict[str, Any],
        source_uri: str,
    ) -> List[Operation]:
        """
        Extract GraphQL operations from pseudo-OpenAPI structure.
        
        This is a LOSSY fallback when native SDL is not available.
        Operations extracted this way have conversion_quality="best_effort".
        """
        operations: List[Operation] = []
        
        for path, path_item in spec_data.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            
            # Infer GraphQL operation type from pseudo-OpenAPI path
            op_type = self._infer_operation_type(path)
            
            for method, op_data in path_item.items():
                if method not in ("get", "post"):
                    continue
                if not isinstance(op_data, dict):
                    continue
                
                field_name = path.split("/")[-1]
                
                # Determine communication pattern based on operation type
                if op_type == "subscription":
                    comm_pattern = CommunicationPattern.SUBSCRIBE
                    requires_websocket = True
                    ws_protocol = "graphql-transport-ws"
                else:
                    comm_pattern = CommunicationPattern.UNARY
                    requires_websocket = False
                    ws_protocol = None
                
                op = Operation(
                    name=op_data.get("operationId", field_name),
                    operation_id=op_data.get("operationId"),
                    summary=op_data.get("summary"),
                    description=op_data.get("description"),
                    tags=op_data.get("tags", []),
                    protocol=ProtocolType.GRAPHQL,
                    communication_pattern=comm_pattern,
                    metadata=GraphQLMetadata(
                        operation_type=op_type,
                        field_name=field_name,
                        requires_websocket=requires_websocket,
                        ws_protocol=ws_protocol,
                    ),
                    source_uri=source_uri,
                    conversion_quality="best_effort",  # Lossy!
                )
                operations.append(op)
        
        return operations
    
    def _infer_operation_type(self, path: str) -> str:
        """Infer GraphQL operation type from pseudo-OpenAPI path."""
        path_lower = path.lower()
        if "/subscription/" in path_lower or path_lower.startswith("/graphql/subscription"):
            return "subscription"
        elif "/mutation/" in path_lower or path_lower.startswith("/graphql/mutation"):
            return "mutation"
        else:
            return "query"
