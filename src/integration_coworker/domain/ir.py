"""
Protocol-agnostic Intermediate Representation (IR) models.

This module defines the unified data model for representing API operations
across all supported protocols (REST, GraphQL, gRPC, AsyncAPI, WebSocket).

CRITICAL: Adapters must parse from NATIVE spec formats (SDL, AsyncAPI YAML),
NOT from lossy pseudo-OpenAPI conversions.

Design: docs/PROTOCOL_SUPPORT_VNEXT.md Option B
Date: 2025-12-22
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List


class ProtocolType(str, Enum):
    """
    Supported protocol or integration kinds.
    
    Includes API protocols (REST, GraphQL, gRPC, AsyncAPI, WebSocket, SOAP)
    and data integration kinds (FILE for CSV/Excel/fixed-width parsing).
    """
    REST = "rest"
    GRAPHQL = "graphql"
    GRPC = "grpc"
    ASYNCAPI = "asyncapi"
    WEBSOCKET = "websocket"
    SOAP = "soap"
    FILE = "file"  # CSV, Excel, fixed-width file parsing


class CommunicationPattern(str, Enum):
    """
    Communication patterns for operations.
    
    These capture the semantic meaning that gets lost in pseudo-OpenAPI:
    - SUBSCRIBE: GraphQL subscriptions, AsyncAPI subscribe (NOT POST!)
    - PUBLISH: AsyncAPI publish channels (fire-and-forget)
    - SERVER_STREAMING: SSE, gRPC server streams
    - BIDIRECTIONAL: WebSocket, gRPC bidi streams
    """
    UNARY = "unary"              # Request → Response (REST GET/POST, GraphQL query/mutation)
    SERVER_STREAMING = "server"  # Request → Stream of Responses (SSE, gRPC server stream)
    CLIENT_STREAMING = "client"  # Stream of Requests → Response (gRPC client stream)
    BIDIRECTIONAL = "bidi"       # Stream ↔ Stream (WebSocket, gRPC bidi)
    PUBLISH = "publish"          # Fire and forget (AsyncAPI publish)
    SUBSCRIBE = "subscribe"      # Receive events (AsyncAPI subscribe, GraphQL subscription)


# =============================================================================
# Protocol-Specific Metadata Classes (minimal, no big to_dict helpers)
# =============================================================================

@dataclass
class RestMetadata:
    """REST/OpenAPI-specific metadata."""
    path: str                              # e.g., "/v1/users/{id}"
    method: str                            # GET, POST, PUT, PATCH, DELETE
    path_params: List[str] = field(default_factory=list)
    content_type: str = "application/json"


@dataclass
class GraphQLMetadata:
    """
    GraphQL-specific metadata.
    
    CRITICAL: requires_websocket=True for subscriptions.
    This is the semantic that gets lost when converting to POST endpoints.
    """
    operation_type: str                    # "query", "mutation", "subscription"
    field_name: str                        # The root field name
    endpoint_url: str = "/graphql"         # Usually single endpoint
    requires_websocket: bool = False       # True for subscriptions
    ws_protocol: Optional[str] = None      # "graphql-transport-ws" or "subscriptions-transport-ws"


@dataclass
class AsyncAPIMetadata:
    """
    AsyncAPI-specific metadata for event-driven APIs.
    
    CRITICAL: operation captures "publish" vs "subscribe" direction.
    This is the semantic that gets lost when converting to GET/POST.
    
    The direction field captures the resolved message flow relative to the
    described application, independent of the perspective used during conversion.
    
    Attributes:
        channel: The channel name (e.g., "user/signedup")
        operation: Original AsyncAPI operation type ("publish" or "subscribe")
        direction: Resolved direction ("inbound_to_app" or "outbound_from_app")
        perspective: Perspective used during conversion ("app" or "external")
        broker_protocol: Broker protocol ("kafka", "amqp", "mqtt", etc.)
        broker_url: Connection URL
        message_name: Named message type
    """
    channel: str                           # e.g., "user/signedup"
    operation: str                         # "publish" or "subscribe" - MUST preserve this
    direction: Optional[str] = None        # "inbound_to_app" or "outbound_from_app"
    perspective: Optional[str] = None      # "app" or "external" - perspective used
    broker_protocol: Optional[str] = None  # "kafka", "amqp", "mqtt", "redis", etc.
    broker_url: Optional[str] = None       # Connection URL
    message_name: Optional[str] = None     # Named message type


@dataclass
class GrpcMetadata:
    """gRPC-specific metadata."""
    package: str                           # e.g., "myapp.v1"
    service: str                           # e.g., "UserService"
    method: str                            # e.g., "GetUser"
    streaming_mode: CommunicationPattern = CommunicationPattern.UNARY
    proto_file: Optional[str] = None       # Source .proto file path


@dataclass
class WebSocketMetadata:
    """WebSocket-specific metadata."""
    url: str                               # WebSocket URL (ws:// or wss://)
    message_types: List[str] = field(default_factory=list)
    subprotocol: Optional[str] = None


# =============================================================================
# Core IR Entity: Operation
# =============================================================================

@dataclass
class Operation:
    """
    Protocol-agnostic representation of an API operation.
    
    This is the core IR entity that captures semantics lost in pseudo-OpenAPI:
    - GraphQL subscriptions → communication_pattern=SUBSCRIBE, metadata.requires_websocket=True
    - AsyncAPI pub/sub → communication_pattern=PUBLISH/SUBSCRIBE, metadata.operation="publish"/"subscribe"
    
    The `metadata` field contains protocol-specific details.
    """
    # Identity
    name: str
    operation_id: Optional[str] = None
    
    # Documentation
    summary: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    
    # Protocol classification - THE KEY SEMANTICS
    protocol: ProtocolType = ProtocolType.REST
    communication_pattern: CommunicationPattern = CommunicationPattern.UNARY
    
    # Schema references (JSON Schema $ref or component path)
    input_schema_ref: Optional[str] = None
    output_schema_ref: Optional[str] = None
    
    # Security
    auth_required: bool = True
    
    # Protocol-specific metadata (discriminated by `protocol`)
    metadata: Optional[Any] = None  # RestMetadata | GraphQLMetadata | AsyncAPIMetadata | etc.
    
    # Traceability
    source_uri: Optional[str] = None
    conversion_quality: str = "deterministic"  # "deterministic", "llm_assisted", "best_effort"
    
    def is_streaming(self) -> bool:
        """Check if operation involves streaming."""
        return self.communication_pattern in (
            CommunicationPattern.SERVER_STREAMING,
            CommunicationPattern.CLIENT_STREAMING,
            CommunicationPattern.BIDIRECTIONAL,
            CommunicationPattern.SUBSCRIBE,
        )
    
    def is_event_driven(self) -> bool:
        """Check if operation is event-driven (pub/sub)."""
        return self.communication_pattern in (
            CommunicationPattern.PUBLISH,
            CommunicationPattern.SUBSCRIBE,
        )


# =============================================================================
# Native Parser Integration Points
# 
# These functions extract Operations from NATIVE spec formats,
# NOT from lossy pseudo-OpenAPI conversions.
# =============================================================================

def graphql_subscription_to_operation(
    field_name: str,
    field_description: Optional[str],
    return_type_ref: Optional[str],
    source_uri: str,
) -> Operation:
    """
    Create an Operation from a native GraphQL subscription field.
    
    CRITICAL: This preserves WebSocket transport semantics that are LOST
    when GraphQL is converted to pseudo-OpenAPI POST endpoints.
    """
    return Operation(
        name=field_name,
        operation_id=f"subscription_{field_name}",
        summary=field_description,
        protocol=ProtocolType.GRAPHQL,
        communication_pattern=CommunicationPattern.SUBSCRIBE,  # NOT UNARY!
        output_schema_ref=return_type_ref,
        metadata=GraphQLMetadata(
            operation_type="subscription",
            field_name=field_name,
            requires_websocket=True,  # WebSocket transport required
            ws_protocol="graphql-transport-ws",  # Modern protocol
        ),
        source_uri=source_uri,
        conversion_quality="deterministic",
    )


def asyncapi_channel_to_operation(
    channel_name: str,
    operation_type: str,  # "publish" or "subscribe"
    message_schema_ref: Optional[str],
    broker_protocol: Optional[str],
    source_uri: str,
) -> Operation:
    """
    Create an Operation from a native AsyncAPI channel.
    
    CRITICAL: This preserves pub/sub direction semantics that are LOST
    when AsyncAPI is converted to pseudo-OpenAPI GET/POST.
    
    CORRECT AsyncAPI semantics (per asyncapi.com/blog/publish-subscribe-semantics):
    - "publish" = app RECEIVES messages (others publish TO it) = SUBSCRIBE pattern
    - "subscribe" = app SENDS messages (to subscribers) = PUBLISH pattern
    """
    # CORRECT mapping based on AsyncAPI semantics
    if operation_type == "publish":
        # App receives = SUBSCRIBE pattern, output_schema (we receive)
        comm_pattern = CommunicationPattern.SUBSCRIBE
        input_schema = None
        output_schema = message_schema_ref
    else:
        # App sends = PUBLISH pattern, input_schema (we provide)
        comm_pattern = CommunicationPattern.PUBLISH
        input_schema = message_schema_ref
        output_schema = None
    
    return Operation(
        name=f"{operation_type}_{channel_name.replace('/', '_')}",
        operation_id=f"{operation_type}_{channel_name.replace('/', '_')}",
        protocol=ProtocolType.ASYNCAPI,
        communication_pattern=comm_pattern,
        input_schema_ref=input_schema,
        output_schema_ref=output_schema,
        metadata=AsyncAPIMetadata(
            channel=channel_name,
            operation=operation_type,  # "publish" or "subscribe" - preserved!
            broker_protocol=broker_protocol,
        ),
        source_uri=source_uri,
        conversion_quality="deterministic",
    )
