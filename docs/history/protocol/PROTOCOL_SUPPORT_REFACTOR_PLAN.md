# Protocol Support vNext: Refactor & Impact Plan

**Parent Document**: [PROTOCOL_SUPPORT_VNEXT.md](PROTOCOL_SUPPORT_VNEXT.md)  
**Date**: 2025-12-22  
**Status**: Implementation-Ready

---

## CRITICAL CORRECTIONS (2025-12-22)

### Reality Check Findings

Per repo audit:
1. **No Alembic** — repo uses `CREATE TABLE IF NOT EXISTS` pattern (inline DDL)
2. **No `spec_silver.*` persistence** — Silver entities are in-memory dataclasses only
3. **No `migrations/` directory with active migration system**

### Phase 1 Constraints

- **NO DATABASE MIGRATIONS** — Silver entities live in memory, no tables needed
- **NO `DeprecationWarning` in Endpoint** — too noisy for hot paths
- **Adapters MUST parse native formats** — NOT extract from lossy pseudo-OpenAPI

### Removed from Original Plan

| Item | Reason |
|------|--------|
| `migrations/002_add_operations_table.sql` | Silver is memory-only |
| `Endpoint.__post_init__` deprecation warning | Too noisy |
| Large `to_dict()` helpers | Not repo idiom |

---

## Overview

This document provides **exact file-by-file changes** for implementing Option B (Protocol-Native IR with Adapter Plugins) from the design document.

---

## Phase 1: Foundation (Week 1-2)

### 1.1 NEW FILE: `src/integration_coworker/domain/ir.py`

**Purpose**: Protocol-agnostic Intermediate Representation models.

```python
"""
Protocol-agnostic Intermediate Representation (IR) models.

This module defines the unified data model for representing API operations
across all supported protocols (REST, GraphQL, gRPC, AsyncAPI, WebSocket).

Design: PROTOCOL_SUPPORT_VNEXT.md Option B
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, Union, List


class ProtocolType(str, Enum):
    """Supported API protocol types."""
    REST = "rest"
    GRAPHQL = "graphql"
    GRPC = "grpc"
    ASYNCAPI = "asyncapi"
    WEBSOCKET = "websocket"
    SOAP = "soap"


class CommunicationPattern(str, Enum):
    """Communication patterns for operations."""
    UNARY = "unary"              # Request → Response (most REST, GraphQL queries)
    SERVER_STREAMING = "server"  # Request → Stream of Responses (SSE, gRPC server stream)
    CLIENT_STREAMING = "client"  # Stream of Requests → Response (gRPC client stream)
    BIDIRECTIONAL = "bidi"       # Stream ↔ Stream (WebSocket, gRPC bidi)
    PUBLISH = "publish"          # Fire and forget (AsyncAPI publish)
    SUBSCRIBE = "subscribe"      # Receive events (AsyncAPI subscribe, GraphQL subscription)


# =============================================================================
# Protocol-Specific Metadata Classes
# =============================================================================

@dataclass
class ProtocolMetadata:
    """Base class for protocol-specific metadata. Subclass per protocol."""
    pass


@dataclass
class RestMetadata(ProtocolMetadata):
    """REST/OpenAPI-specific metadata."""
    path: str                              # e.g., "/v1/users/{id}"
    method: str                            # GET, POST, PUT, PATCH, DELETE
    headers: Dict[str, str] = field(default_factory=dict)
    query_params: Dict[str, Any] = field(default_factory=dict)
    path_params: List[str] = field(default_factory=list)
    content_type: str = "application/json"
    accept: str = "application/json"


@dataclass
class GraphQLMetadata(ProtocolMetadata):
    """GraphQL-specific metadata."""
    operation_type: str                    # "query", "mutation", "subscription"
    field_name: str                        # The root field name
    field_path: Optional[str] = None       # Dot-path for nested selections
    variables: Dict[str, Any] = field(default_factory=dict)
    endpoint_url: str = "/graphql"         # Usually single endpoint
    requires_websocket: bool = False       # True for subscriptions


@dataclass
class GrpcMetadata(ProtocolMetadata):
    """gRPC-specific metadata."""
    package: str                           # e.g., "myapp.v1"
    service: str                           # e.g., "UserService"
    method: str                            # e.g., "GetUser"
    streaming_mode: CommunicationPattern = CommunicationPattern.UNARY
    proto_file: Optional[str] = None       # Source .proto file path


@dataclass
class AsyncAPIMetadata(ProtocolMetadata):
    """AsyncAPI-specific metadata for event-driven APIs."""
    channel: str                           # e.g., "user/signedup"
    operation: str                         # "publish" or "subscribe"
    broker_protocol: Optional[str] = None  # "kafka", "amqp", "mqtt", "redis", etc.
    broker_url: Optional[str] = None       # Connection URL
    message_name: Optional[str] = None     # Named message type
    bindings: Dict[str, Any] = field(default_factory=dict)  # Protocol-specific bindings


@dataclass
class WebSocketMetadata(ProtocolMetadata):
    """WebSocket-specific metadata."""
    url: str                               # WebSocket URL (ws:// or wss://)
    message_types: List[str] = field(default_factory=list)  # Expected message types
    subprotocol: Optional[str] = None      # WebSocket subprotocol
    heartbeat_interval: Optional[int] = None  # Ping interval in seconds


@dataclass
class SoapMetadata(ProtocolMetadata):
    """SOAP/WSDL-specific metadata."""
    wsdl_url: str
    operation_name: str
    port_type: str
    binding: str
    soap_action: Optional[str] = None
    soap_version: str = "1.1"              # "1.1" or "1.2"


# Type alias for all metadata types
OperationMetadata = Union[
    RestMetadata, 
    GraphQLMetadata, 
    GrpcMetadata, 
    AsyncAPIMetadata, 
    WebSocketMetadata,
    SoapMetadata
]


# =============================================================================
# Core IR Entity: Operation
# =============================================================================

@dataclass
class Operation:
    """
    Protocol-agnostic representation of an API operation.
    
    This replaces the HTTP-centric Endpoint model as the universal
    representation of an API operation. It can represent:
    - REST endpoints (GET /users/{id})
    - GraphQL operations (query getUser, subscription onUserCreated)
    - gRPC methods (UserService.GetUser with streaming modes)
    - AsyncAPI channels (publish to user/created, subscribe to notifications)
    - WebSocket messages
    - SOAP operations
    
    The `metadata` field contains protocol-specific details.
    """
    # Identity
    id: Optional[int] = None
    name: str = ""                         # Operation name (e.g., "getUser", "CreateCheckoutSession")
    operation_id: Optional[str] = None     # Unique ID (e.g., OpenAPI operationId)
    
    # Documentation
    summary: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    
    # Protocol classification
    protocol: ProtocolType = ProtocolType.REST
    communication_pattern: CommunicationPattern = CommunicationPattern.UNARY
    
    # Schema references (JSON Schema $ref or component path)
    input_schema_ref: Optional[str] = None   # Request body / input type
    output_schema_ref: Optional[str] = None  # Response body / return type
    error_schema_refs: List[str] = field(default_factory=list)  # Error types
    
    # Security
    auth_required: bool = True
    auth_schemes: List[str] = field(default_factory=list)  # e.g., ["bearer", "api_key"]
    
    # Protocol-specific metadata (discriminated by `protocol`)
    metadata: Optional[OperationMetadata] = None
    
    # Traceability (API-002: Multi-Spec Source Reference)
    source_uri: Optional[str] = None
    source_system_id: Optional[int] = None
    spec_document_id: Optional[int] = None
    
    # Quality tracking
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
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence/serialization."""
        result = {
            "id": self.id,
            "name": self.name,
            "operation_id": self.operation_id,
            "summary": self.summary,
            "description": self.description,
            "tags": self.tags,
            "protocol": self.protocol.value,
            "communication_pattern": self.communication_pattern.value,
            "input_schema_ref": self.input_schema_ref,
            "output_schema_ref": self.output_schema_ref,
            "error_schema_refs": self.error_schema_refs,
            "auth_required": self.auth_required,
            "auth_schemes": self.auth_schemes,
            "source_uri": self.source_uri,
            "source_system_id": self.source_system_id,
            "spec_document_id": self.spec_document_id,
            "conversion_quality": self.conversion_quality,
        }
        
        # Serialize metadata based on type
        if self.metadata:
            if isinstance(self.metadata, RestMetadata):
                result["metadata"] = {
                    "type": "rest",
                    "path": self.metadata.path,
                    "method": self.metadata.method,
                    "headers": self.metadata.headers,
                    "query_params": self.metadata.query_params,
                    "path_params": self.metadata.path_params,
                    "content_type": self.metadata.content_type,
                    "accept": self.metadata.accept,
                }
            elif isinstance(self.metadata, GraphQLMetadata):
                result["metadata"] = {
                    "type": "graphql",
                    "operation_type": self.metadata.operation_type,
                    "field_name": self.metadata.field_name,
                    "field_path": self.metadata.field_path,
                    "variables": self.metadata.variables,
                    "endpoint_url": self.metadata.endpoint_url,
                    "requires_websocket": self.metadata.requires_websocket,
                }
            elif isinstance(self.metadata, GrpcMetadata):
                result["metadata"] = {
                    "type": "grpc",
                    "package": self.metadata.package,
                    "service": self.metadata.service,
                    "method": self.metadata.method,
                    "streaming_mode": self.metadata.streaming_mode.value,
                    "proto_file": self.metadata.proto_file,
                }
            elif isinstance(self.metadata, AsyncAPIMetadata):
                result["metadata"] = {
                    "type": "asyncapi",
                    "channel": self.metadata.channel,
                    "operation": self.metadata.operation,
                    "broker_protocol": self.metadata.broker_protocol,
                    "broker_url": self.metadata.broker_url,
                    "message_name": self.metadata.message_name,
                    "bindings": self.metadata.bindings,
                }
            elif isinstance(self.metadata, WebSocketMetadata):
                result["metadata"] = {
                    "type": "websocket",
                    "url": self.metadata.url,
                    "message_types": self.metadata.message_types,
                    "subprotocol": self.metadata.subprotocol,
                    "heartbeat_interval": self.metadata.heartbeat_interval,
                }
            elif isinstance(self.metadata, SoapMetadata):
                result["metadata"] = {
                    "type": "soap",
                    "wsdl_url": self.metadata.wsdl_url,
                    "operation_name": self.metadata.operation_name,
                    "port_type": self.metadata.port_type,
                    "binding": self.metadata.binding,
                    "soap_action": self.metadata.soap_action,
                    "soap_version": self.metadata.soap_version,
                }
        
        return result


# =============================================================================
# Conversion Functions: Endpoint <-> Operation
# =============================================================================

def endpoint_to_operation(endpoint: "Endpoint") -> Operation:
    """
    Convert legacy Endpoint model to Operation IR.
    
    Used for backward compatibility during migration.
    """
    from integration_coworker.domain.models import Endpoint
    
    # Extract path params from path
    import re
    path_params = re.findall(r'\{(\w+)\}', endpoint.path)
    
    return Operation(
        id=endpoint.id,
        name=endpoint.operation_id or f"{endpoint.method.lower()}_{endpoint.path.replace('/', '_').strip('_')}",
        operation_id=endpoint.operation_id,
        summary=endpoint.summary,
        description=endpoint.description,
        protocol=ProtocolType.REST,
        communication_pattern=CommunicationPattern.UNARY,
        input_schema_ref=f"#/components/schemas/{endpoint.request_schema_id}" if endpoint.request_schema_id else None,
        output_schema_ref=f"#/components/schemas/{endpoint.response_schema_id}" if endpoint.response_schema_id else None,
        auth_required=endpoint.auth_required,
        metadata=RestMetadata(
            path=endpoint.path,
            method=endpoint.method,
            path_params=path_params,
        ),
        source_system_id=endpoint.source_system_id,
        spec_document_id=endpoint.spec_document_id,
        conversion_quality="deterministic",
    )


def operation_to_endpoint(operation: Operation) -> "Endpoint":
    """
    Convert Operation IR back to legacy Endpoint model.
    
    Used for backward compatibility with existing code that expects Endpoint.
    Only works for REST operations.
    
    Raises:
        ValueError: If operation is not REST protocol
    """
    from integration_coworker.domain.models import Endpoint
    
    if operation.protocol != ProtocolType.REST:
        raise ValueError(f"Cannot convert {operation.protocol} operation to Endpoint (REST-only)")
    
    metadata = operation.metadata
    if not isinstance(metadata, RestMetadata):
        raise ValueError("Operation metadata must be RestMetadata")
    
    return Endpoint(
        id=operation.id,
        source_system_id=operation.source_system_id,
        spec_document_id=operation.spec_document_id,
        path=metadata.path,
        method=metadata.method,
        operation_id=operation.operation_id,
        summary=operation.summary,
        description=operation.description,
        auth_required=operation.auth_required,
    )
```

### 1.2 NEW FILE: `src/integration_coworker/protocols/__init__.py`

```python
"""
Protocol adapters for converting spec formats to unified IR.

Each adapter handles a specific protocol (REST, GraphQL, gRPC, AsyncAPI)
and converts its native representation to the protocol-agnostic Operation model.
"""
from .base import ProtocolAdapter, AdapterRegistry
from .rest import RestAdapter

__all__ = [
    "ProtocolAdapter",
    "AdapterRegistry", 
    "RestAdapter",
]
```

### 1.3 NEW FILE: `src/integration_coworker/protocols/base.py`

```python
"""
Base protocol adapter interface and registry.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Type
import logging

from integration_coworker.domain.ir import Operation, ProtocolType
from integration_coworker.sources.base import ParsedSpec

logger = logging.getLogger(__name__)


class ProtocolAdapter(ABC):
    """
    Abstract base class for protocol adapters.
    
    Each adapter converts a protocol-specific specification
    to a list of protocol-agnostic Operation objects.
    """
    
    @property
    @abstractmethod
    def protocol_type(self) -> ProtocolType:
        """Return the protocol type this adapter handles."""
        pass
    
    @abstractmethod
    def can_handle(self, parsed_spec: ParsedSpec) -> bool:
        """
        Check if this adapter can handle the given parsed spec.
        
        Args:
            parsed_spec: The parsed specification to check
            
        Returns:
            True if this adapter can convert the spec
        """
        pass
    
    @abstractmethod
    def convert(self, parsed_spec: ParsedSpec) -> List[Operation]:
        """
        Convert a parsed spec to Operation IR objects.
        
        Args:
            parsed_spec: The parsed specification
            
        Returns:
            List of Operation objects extracted from the spec
        """
        pass


class AdapterRegistry:
    """
    Registry for protocol adapters.
    
    Discovers the appropriate adapter for a given spec
    and routes conversion requests.
    """
    
    def __init__(self):
        self._adapters: Dict[ProtocolType, ProtocolAdapter] = {}
    
    def register(self, adapter: ProtocolAdapter) -> None:
        """Register an adapter for its protocol type."""
        self._adapters[adapter.protocol_type] = adapter
        logger.info(f"Registered adapter for {adapter.protocol_type.value}")
    
    def get_adapter(self, protocol: ProtocolType) -> Optional[ProtocolAdapter]:
        """Get adapter for a specific protocol type."""
        return self._adapters.get(protocol)
    
    def find_adapter(self, parsed_spec: ParsedSpec) -> Optional[ProtocolAdapter]:
        """
        Find the appropriate adapter for a parsed spec.
        
        Checks each registered adapter's can_handle() method.
        """
        for adapter in self._adapters.values():
            if adapter.can_handle(parsed_spec):
                return adapter
        return None
    
    def convert(self, parsed_spec: ParsedSpec) -> List[Operation]:
        """
        Convert a parsed spec to Operations using the appropriate adapter.
        
        Args:
            parsed_spec: The parsed specification
            
        Returns:
            List of Operation objects
            
        Raises:
            ValueError: If no adapter can handle the spec
        """
        adapter = self.find_adapter(parsed_spec)
        if adapter is None:
            raise ValueError(f"No adapter found for spec from {parsed_spec.source_uri}")
        
        logger.info(f"Converting {parsed_spec.source_uri} with {adapter.protocol_type.value} adapter")
        return adapter.convert(parsed_spec)


# Global registry instance
_registry: Optional[AdapterRegistry] = None


def get_adapter_registry() -> AdapterRegistry:
    """Get or create the global adapter registry."""
    global _registry
    if _registry is None:
        _registry = AdapterRegistry()
        # Auto-register built-in adapters
        from .rest import RestAdapter
        _registry.register(RestAdapter())
        # GraphQL, AsyncAPI, gRPC adapters registered when available
    return _registry
```

### 1.4 NEW FILE: `src/integration_coworker/protocols/rest.py`

```python
"""
REST/OpenAPI protocol adapter.

Converts OpenAPI/Swagger specifications to Operation IR.
"""
import re
import logging
from typing import List, Dict, Any

from integration_coworker.domain.ir import (
    Operation, ProtocolType, CommunicationPattern,
    RestMetadata
)
from integration_coworker.sources.base import ParsedSpec
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
    
    def can_handle(self, parsed_spec: ParsedSpec) -> bool:
        """Check if spec is OpenAPI/Swagger."""
        if not parsed_spec.data:
            return False
        
        data = parsed_spec.data
        
        # Check for OpenAPI 3.x
        if "openapi" in data:
            return True
        
        # Check for Swagger 2.0
        if "swagger" in data:
            return True
        
        # Check metadata hint
        parsed_from = parsed_spec.metadata.get("_parsed_from", "")
        if parsed_from in ("openapi", "swagger"):
            return True
        
        return False
    
    def convert(self, parsed_spec: ParsedSpec) -> List[Operation]:
        """Convert OpenAPI spec to Operations."""
        operations: List[Operation] = []
        data = parsed_spec.data
        
        paths = data.get("paths", {})
        
        for path, path_item in paths.items():
            for method, operation_data in path_item.items():
                # Skip non-HTTP methods (parameters, servers, etc.)
                if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                    continue
                
                op = self._extract_operation(
                    path, method, operation_data, data, parsed_spec
                )
                operations.append(op)
        
        logger.info(f"Extracted {len(operations)} REST operations from {parsed_spec.source_uri}")
        return operations
    
    def _extract_operation(
        self,
        path: str,
        method: str,
        operation_data: Dict[str, Any],
        spec: Dict[str, Any],
        parsed_spec: ParsedSpec
    ) -> Operation:
        """Extract a single Operation from OpenAPI path item."""
        
        # Extract path parameters
        path_params = re.findall(r'\{(\w+)\}', path)
        
        # Extract query parameters
        query_params = {}
        for param in operation_data.get("parameters", []):
            if param.get("in") == "query":
                query_params[param["name"]] = param.get("schema", {}).get("type", "string")
        
        # Determine input schema
        input_schema_ref = None
        request_body = operation_data.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            json_content = content.get("application/json", {})
            schema = json_content.get("schema", {})
            input_schema_ref = schema.get("$ref")
        
        # Determine output schema
        output_schema_ref = None
        responses = operation_data.get("responses", {})
        for status in ("200", "201", "202", "204"):
            if status in responses:
                content = responses[status].get("content", {})
                json_content = content.get("application/json", {})
                schema = json_content.get("schema", {})
                output_schema_ref = schema.get("$ref")
                break
        
        # Determine auth requirement
        auth_required = "security" in operation_data or "security" in spec
        
        # Build operation name
        operation_id = operation_data.get("operationId")
        if not operation_id:
            operation_id = f"{method.lower()}_{path.replace('/', '_').strip('_')}"
        
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
                query_params=query_params,
            ),
            source_uri=parsed_spec.source_uri,
            conversion_quality="deterministic",
        )
```

---

## Phase 2: GraphQL Native (Week 3-4)

### 2.1 NEW FILE: `src/integration_coworker/protocols/graphql.py`

```python
"""
GraphQL protocol adapter.

Converts GraphQL SDL schemas to Operation IR, preserving:
- Query operations
- Mutation operations  
- Subscription operations (with proper streaming semantics)
"""
import logging
from typing import List, Dict, Any, Optional

from integration_coworker.domain.ir import (
    Operation, ProtocolType, CommunicationPattern,
    GraphQLMetadata
)
from integration_coworker.sources.base import ParsedSpec
from .base import ProtocolAdapter

logger = logging.getLogger(__name__)


class GraphQLAdapter(ProtocolAdapter):
    """
    Adapter for GraphQL specifications.
    
    Handles GraphQL SDL schemas and converts them to Operations
    with proper semantic preservation:
    - Queries → UNARY communication
    - Mutations → UNARY communication
    - Subscriptions → SUBSCRIBE communication (WebSocket)
    """
    
    @property
    def protocol_type(self) -> ProtocolType:
        return ProtocolType.GRAPHQL
    
    def can_handle(self, parsed_spec: ParsedSpec) -> bool:
        """Check if spec is GraphQL."""
        parsed_from = parsed_spec.metadata.get("_parsed_from", "")
        return parsed_from == "graphql"
    
    def convert(self, parsed_spec: ParsedSpec) -> List[Operation]:
        """Convert GraphQL schema to Operations."""
        operations: List[Operation] = []
        data = parsed_spec.data
        
        # If it was converted to pseudo-OpenAPI, extract from paths
        if "paths" in data:
            operations.extend(self._extract_from_pseudo_openapi(data, parsed_spec))
        
        logger.info(f"Extracted {len(operations)} GraphQL operations from {parsed_spec.source_uri}")
        return operations
    
    def _extract_from_pseudo_openapi(
        self, 
        data: Dict[str, Any], 
        parsed_spec: ParsedSpec
    ) -> List[Operation]:
        """Extract GraphQL operations from pseudo-OpenAPI structure."""
        operations = []
        
        for path, path_item in data.get("paths", {}).items():
            # Determine GraphQL operation type from path
            op_type = self._infer_operation_type(path)
            
            for method, op_data in path_item.items():
                if method not in ("get", "post"):
                    continue
                
                field_name = path.split("/")[-1]
                
                # Determine communication pattern based on operation type
                if op_type == "subscription":
                    comm_pattern = CommunicationPattern.SUBSCRIBE
                    requires_websocket = True
                else:
                    comm_pattern = CommunicationPattern.UNARY
                    requires_websocket = False
                
                op = Operation(
                    name=op_data.get("operationId", field_name),
                    operation_id=op_data.get("operationId"),
                    summary=op_data.get("summary"),
                    description=op_data.get("description"),
                    tags=op_data.get("tags", []),
                    protocol=ProtocolType.GRAPHQL,
                    communication_pattern=comm_pattern,
                    input_schema_ref=self._get_request_schema(op_data),
                    output_schema_ref=self._get_response_schema(op_data),
                    auth_required="security" in op_data,
                    metadata=GraphQLMetadata(
                        operation_type=op_type,
                        field_name=field_name,
                        requires_websocket=requires_websocket,
                    ),
                    source_uri=parsed_spec.source_uri,
                    conversion_quality="best_effort" if parsed_spec.metadata.get("_parse_method") == "llm" else "deterministic",
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
    
    def _get_request_schema(self, op_data: Dict[str, Any]) -> Optional[str]:
        """Extract request schema ref from operation."""
        body = op_data.get("requestBody", {})
        content = body.get("content", {})
        json_content = content.get("application/json", {})
        return json_content.get("schema", {}).get("$ref")
    
    def _get_response_schema(self, op_data: Dict[str, Any]) -> Optional[str]:
        """Extract response schema ref from operation."""
        responses = op_data.get("responses", {})
        for status in ("200", "201"):
            if status in responses:
                content = responses[status].get("content", {})
                json_content = content.get("application/json", {})
                return json_content.get("schema", {}).get("$ref")
        return None
```

---

## Phase 3: AsyncAPI Native (Week 5)

### 3.1 NEW FILE: `src/integration_coworker/protocols/asyncapi.py`

```python
"""
AsyncAPI protocol adapter.

Converts AsyncAPI specifications to Operation IR, preserving:
- Channel names
- Publish/Subscribe distinction
- Broker protocol information
- Message schemas
"""
import logging
from typing import List, Dict, Any, Optional

from integration_coworker.domain.ir import (
    Operation, ProtocolType, CommunicationPattern,
    AsyncAPIMetadata
)
from integration_coworker.sources.base import ParsedSpec
from .base import ProtocolAdapter

logger = logging.getLogger(__name__)


class AsyncAPIAdapter(ProtocolAdapter):
    """
    Adapter for AsyncAPI specifications.
    
    Preserves event-driven semantics:
    - Publish operations → PUBLISH pattern
    - Subscribe operations → SUBSCRIBE pattern
    - Broker metadata (Kafka, AMQP, MQTT, etc.)
    """
    
    @property
    def protocol_type(self) -> ProtocolType:
        return ProtocolType.ASYNCAPI
    
    def can_handle(self, parsed_spec: ParsedSpec) -> bool:
        """Check if spec is AsyncAPI."""
        parsed_from = parsed_spec.metadata.get("_parsed_from", "")
        if parsed_from == "asyncapi":
            return True
        
        # Check for native AsyncAPI structure
        data = parsed_spec.data or {}
        return "asyncapi" in data or "channels" in data
    
    def convert(self, parsed_spec: ParsedSpec) -> List[Operation]:
        """Convert AsyncAPI spec to Operations."""
        operations: List[Operation] = []
        data = parsed_spec.data
        
        # Extract broker info from servers
        broker_info = self._extract_broker_info(data)
        
        # If converted to pseudo-OpenAPI, extract from paths
        if "_parsed_from" in parsed_spec.metadata and "paths" in data:
            operations.extend(self._extract_from_pseudo_openapi(data, parsed_spec, broker_info))
        
        # Native AsyncAPI structure
        elif "channels" in data:
            operations.extend(self._extract_from_native(data, parsed_spec, broker_info))
        
        logger.info(f"Extracted {len(operations)} AsyncAPI operations from {parsed_spec.source_uri}")
        return operations
    
    def _extract_broker_info(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract broker protocol and URL from servers."""
        servers = data.get("servers", {})
        if not servers:
            return {}
        
        # Use first server as primary
        server_name = list(servers.keys())[0]
        server = servers[server_name]
        
        return {
            "broker_protocol": server.get("protocol"),
            "broker_url": server.get("url"),
        }
    
    def _extract_from_native(
        self,
        data: Dict[str, Any],
        parsed_spec: ParsedSpec,
        broker_info: Dict[str, Any]
    ) -> List[Operation]:
        """Extract from native AsyncAPI structure."""
        operations = []
        
        for channel_name, channel in data.get("channels", {}).items():
            # Publish operation
            if "publish" in channel:
                pub = channel["publish"]
                op = self._create_operation(
                    channel_name, "publish", pub, parsed_spec, broker_info
                )
                operations.append(op)
            
            # Subscribe operation
            if "subscribe" in channel:
                sub = channel["subscribe"]
                op = self._create_operation(
                    channel_name, "subscribe", sub, parsed_spec, broker_info
                )
                operations.append(op)
        
        return operations
    
    def _create_operation(
        self,
        channel: str,
        op_type: str,
        op_data: Dict[str, Any],
        parsed_spec: ParsedSpec,
        broker_info: Dict[str, Any]
    ) -> Operation:
        """Create an Operation from channel operation."""
        operation_id = op_data.get("operationId", f"{op_type}_{channel.replace('/', '_')}")
        
        # Determine communication pattern
        comm_pattern = CommunicationPattern.PUBLISH if op_type == "publish" else CommunicationPattern.SUBSCRIBE
        
        # Get message schema
        message = op_data.get("message", {})
        payload_ref = message.get("payload", {}).get("$ref")
        
        return Operation(
            name=operation_id,
            operation_id=operation_id,
            summary=op_data.get("summary"),
            description=op_data.get("description"),
            tags=op_data.get("tags", []),
            protocol=ProtocolType.ASYNCAPI,
            communication_pattern=comm_pattern,
            input_schema_ref=payload_ref if op_type == "publish" else None,
            output_schema_ref=payload_ref if op_type == "subscribe" else None,
            metadata=AsyncAPIMetadata(
                channel=channel,
                operation=op_type,
                broker_protocol=broker_info.get("broker_protocol"),
                broker_url=broker_info.get("broker_url"),
                message_name=message.get("name"),
                bindings=op_data.get("bindings", {}),
            ),
            source_uri=parsed_spec.source_uri,
            conversion_quality="deterministic",
        )
    
    def _extract_from_pseudo_openapi(
        self,
        data: Dict[str, Any],
        parsed_spec: ParsedSpec,
        broker_info: Dict[str, Any]
    ) -> List[Operation]:
        """Extract from pseudo-OpenAPI structure (lossy conversion)."""
        operations = []
        
        for path, path_item in data.get("paths", {}).items():
            # Infer channel from path
            if path.startswith("/channels/"):
                channel = path[len("/channels/"):]
            else:
                channel = path.strip("/")
            
            for method, op_data in path_item.items():
                if method not in ("get", "post"):
                    continue
                
                # Infer operation type from method (POST=publish, GET=subscribe)
                # This is lossy - original spec may have been different
                op_type = "publish" if method == "post" else "subscribe"
                
                op = self._create_operation(
                    channel, op_type, op_data, parsed_spec, broker_info
                )
                op.conversion_quality = "best_effort"  # Mark as lossy
                operations.append(op)
        
        return operations
```

---

## Modified Files

### 4.1 MODIFY: `src/integration_coworker/domain/models.py`

**Changes**:
1. Add deprecation warning to `Endpoint` class
2. Import IR types for interop

```python
# Add at top of file after existing imports
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from integration_coworker.domain.ir import Operation

# Modify Endpoint class docstring
@dataclass
class Endpoint:
    """
    An API endpoint extracted from a specification.
    
    .. deprecated:: 2.0.0
        Use :class:`integration_coworker.domain.ir.Operation` instead.
        Endpoint is maintained for backward compatibility with REST/OpenAPI
        workflows but will be removed in v3.0.0.
    """
    id: Optional[int]
    # ... rest unchanged ...
    
    def __post_init__(self):
        warnings.warn(
            "Endpoint is deprecated, use Operation from domain.ir instead",
            DeprecationWarning,
            stacklevel=2
        )
    
    def to_operation(self) -> "Operation":
        """Convert this Endpoint to an Operation."""
        from integration_coworker.domain.ir import endpoint_to_operation
        return endpoint_to_operation(self)
```

### 4.2 MODIFY: `src/integration_coworker/sources/base.py`

**Changes**:
1. Add `protocol_hint` to `ParsedSpec`

```python
@dataclass
class ParsedSpec:
    """Result of parsing a specification."""
    source_type: SourceType
    source_uri: str
    data: dict
    metadata: dict
    errors: list
    warnings: list
    confidence: float
    
    # NEW: Protocol hint for adapter routing
    protocol_hint: Optional[str] = None  # "openapi", "graphql", "asyncapi", "grpc", etc.
```

### 4.3 MODIFY: `src/integration_coworker/graph/nodes/build_silver_api_model.py`

**Changes**:
1. Add Operation extraction alongside Endpoint (parallel output)
2. Use adapter registry for protocol-specific extraction

```python
# Add to imports
from integration_coworker.domain.ir import Operation
from integration_coworker.protocols.base import get_adapter_registry

# Add new function
def _extract_operations_from_spec(
    parsed_spec: ParsedSpec,
    state: WorkflowState
) -> List[Operation]:
    """
    Extract Operations using protocol-specific adapters.
    
    This is the new IR-based extraction path that preserves
    protocol-specific semantics.
    """
    registry = get_adapter_registry()
    
    try:
        return registry.convert(parsed_spec)
    except ValueError as e:
        logger.warning(f"No adapter for spec: {e}")
        return []

# Modify build_silver_api_model to call both paths
def build_silver_api_model(state: WorkflowState) -> WorkflowState:
    """
    Build Silver API model from all parsed specs.
    
    Outputs both:
    - state.endpoints (legacy, deprecated)
    - state.operations (new IR-based)
    """
    # ... existing Endpoint extraction code ...
    
    # NEW: Also extract Operations via adapters
    if not hasattr(state, 'operations'):
        state.operations = []
    
    for spec in all_specs:
        parsed_spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri=spec.get("_source_uri", "unknown"),
            data=spec,
            metadata={k: v for k, v in spec.items() if k.startswith("_")},
            errors=[],
            warnings=[],
            confidence=1.0,
        )
        
        operations = _extract_operations_from_spec(parsed_spec, state)
        state.operations.extend(operations)
    
    logger.info(f"Extracted {len(state.operations)} Operations (IR) alongside {len(state.endpoints)} Endpoints (legacy)")
    
    # ... rest of function ...
```

### 4.4 MODIFY: `src/integration_coworker/graph/state.py`

**Changes**:
1. Add `operations` list to WorkflowState

```python
# Add to WorkflowState class
@dataclass
class WorkflowState:
    # ... existing fields ...
    
    # NEW: Protocol-agnostic operations (IR)
    operations: List["Operation"] = field(default_factory=list)
```

### 4.5 MODIFY: `src/integration_coworker/codegen/context.py`

**Changes**:
1. Add protocol to CodegenContext
2. Update path generation for non-REST protocols

```python
@dataclass(frozen=True)
class CodegenContext:
    # ... existing fields ...
    
    # NEW: Protocol information
    protocol: str = "rest"  # "rest", "graphql", "grpc", "asyncapi"
    communication_pattern: str = "unary"  # From CommunicationPattern
    
    # NEW: Operation reference (preferred over endpoint)
    operation: Optional["Operation"] = None
    
    def is_streaming(self) -> bool:
        """Check if code generation should handle streaming."""
        return self.communication_pattern in ("server", "client", "bidi", "subscribe")
```

---

## Database Migration

### 5.1 NEW FILE: `migrations/002_add_operations_table.sql`

```sql
-- Migration: Add operations table for Protocol IR
-- Date: 2025-01-10
-- Design: PROTOCOL_SUPPORT_VNEXT.md

-- New operations table (Silver layer, protocol-agnostic)
CREATE TABLE IF NOT EXISTS spec_silver.operations (
    id SERIAL PRIMARY KEY,
    source_system_id INTEGER REFERENCES spec_silver.source_systems(id),
    spec_document_id INTEGER REFERENCES spec_silver.spec_documents(id),
    
    -- Identity
    name VARCHAR(255) NOT NULL,
    operation_id VARCHAR(255),
    
    -- Documentation
    summary TEXT,
    description TEXT,
    tags JSONB DEFAULT '[]',
    
    -- Protocol classification
    protocol VARCHAR(50) NOT NULL DEFAULT 'rest',
    communication_pattern VARCHAR(50) NOT NULL DEFAULT 'unary',
    
    -- Schema references
    input_schema_ref VARCHAR(255),
    output_schema_ref VARCHAR(255),
    error_schema_refs JSONB DEFAULT '[]',
    
    -- Security
    auth_required BOOLEAN DEFAULT TRUE,
    auth_schemes JSONB DEFAULT '[]',
    
    -- Protocol-specific metadata (discriminated by protocol)
    metadata JSONB,
    
    -- Traceability
    source_uri VARCHAR(2048),
    
    -- Quality tracking
    conversion_quality VARCHAR(50) DEFAULT 'deterministic',
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_operations_protocol ON spec_silver.operations(protocol);
CREATE INDEX idx_operations_source_system ON spec_silver.operations(source_system_id);
CREATE INDEX idx_operations_spec_document ON spec_silver.operations(spec_document_id);
CREATE INDEX idx_operations_name ON spec_silver.operations(name);

-- Migrate existing endpoints to operations (REST only)
INSERT INTO spec_silver.operations (
    source_system_id, 
    spec_document_id, 
    name, 
    operation_id,
    summary, 
    description,
    protocol, 
    communication_pattern, 
    input_schema_ref, 
    output_schema_ref,
    auth_required, 
    metadata, 
    source_uri
)
SELECT 
    e.source_system_id,
    e.spec_document_id,
    COALESCE(e.operation_id, CONCAT(LOWER(e.method), '_', REPLACE(e.path, '/', '_'))),
    e.operation_id,
    e.summary,
    e.description,
    'rest',
    'unary',
    CASE WHEN e.request_schema_id IS NOT NULL 
         THEN CONCAT('#/components/schemas/', e.request_schema_id) 
         ELSE NULL 
    END,
    CASE WHEN e.response_schema_id IS NOT NULL 
         THEN CONCAT('#/components/schemas/', e.response_schema_id) 
         ELSE NULL 
    END,
    e.auth_required,
    jsonb_build_object(
        'type', 'rest',
        'path', e.path,
        'method', e.method,
        'pagination_style', e.pagination_style,
        'rate_limit_bucket', e.rate_limit_bucket
    ),
    NULL
FROM spec_silver.endpoints e;

-- Comment for future: endpoints table will be deprecated in v3.0
COMMENT ON TABLE spec_silver.endpoints IS 
    'DEPRECATED: Use operations table instead. Will be removed in v3.0.';
```

---

## Test Files

### 6.1 NEW FILE: `tests/unit/domain/test_ir.py`

```python
"""Unit tests for IR models."""
import pytest

from integration_coworker.domain.ir import (
    Operation, ProtocolType, CommunicationPattern,
    RestMetadata, GraphQLMetadata, AsyncAPIMetadata,
    endpoint_to_operation, operation_to_endpoint
)
from integration_coworker.domain.models import Endpoint


class TestOperation:
    """Tests for Operation model."""
    
    def test_create_rest_operation(self):
        """Test creating a REST operation."""
        op = Operation(
            name="getUser",
            protocol=ProtocolType.REST,
            communication_pattern=CommunicationPattern.UNARY,
            metadata=RestMetadata(path="/users/{id}", method="GET"),
        )
        
        assert op.protocol == ProtocolType.REST
        assert op.is_streaming() is False
        assert op.metadata.path == "/users/{id}"
    
    def test_create_graphql_subscription(self):
        """Test creating a GraphQL subscription (streaming)."""
        op = Operation(
            name="onUserCreated",
            protocol=ProtocolType.GRAPHQL,
            communication_pattern=CommunicationPattern.SUBSCRIBE,
            metadata=GraphQLMetadata(
                operation_type="subscription",
                field_name="onUserCreated",
                requires_websocket=True,
            ),
        )
        
        assert op.protocol == ProtocolType.GRAPHQL
        assert op.is_streaming() is True
        assert op.is_event_driven() is True
        assert op.metadata.requires_websocket is True
    
    def test_to_dict_serialization(self):
        """Test Operation serialization to dict."""
        op = Operation(
            name="publishEvent",
            protocol=ProtocolType.ASYNCAPI,
            communication_pattern=CommunicationPattern.PUBLISH,
            metadata=AsyncAPIMetadata(
                channel="user/created",
                operation="publish",
                broker_protocol="kafka",
            ),
        )
        
        d = op.to_dict()
        
        assert d["protocol"] == "asyncapi"
        assert d["communication_pattern"] == "publish"
        assert d["metadata"]["type"] == "asyncapi"
        assert d["metadata"]["channel"] == "user/created"


class TestConversion:
    """Tests for Endpoint <-> Operation conversion."""
    
    def test_endpoint_to_operation(self):
        """Test converting Endpoint to Operation."""
        endpoint = Endpoint(
            id=1,
            source_system_id=1,
            spec_document_id=1,
            path="/v1/users/{id}",
            method="GET",
            operation_id="getUser",
            summary="Get a user",
            auth_required=True,
        )
        
        op = endpoint_to_operation(endpoint)
        
        assert op.protocol == ProtocolType.REST
        assert op.communication_pattern == CommunicationPattern.UNARY
        assert op.metadata.path == "/v1/users/{id}"
        assert op.metadata.method == "GET"
        assert "id" in op.metadata.path_params
    
    def test_operation_to_endpoint_rest_only(self):
        """Test that only REST operations can convert to Endpoint."""
        graphql_op = Operation(
            name="getUser",
            protocol=ProtocolType.GRAPHQL,
            communication_pattern=CommunicationPattern.UNARY,
            metadata=GraphQLMetadata(operation_type="query", field_name="getUser"),
        )
        
        with pytest.raises(ValueError, match="REST-only"):
            operation_to_endpoint(graphql_op)
```

---

## Impact Summary

### Files Created (8)
1. `src/integration_coworker/domain/ir.py` - Core IR models
2. `src/integration_coworker/protocols/__init__.py` - Package init
3. `src/integration_coworker/protocols/base.py` - Adapter interface
4. `src/integration_coworker/protocols/rest.py` - REST adapter
5. `src/integration_coworker/protocols/graphql.py` - GraphQL adapter
6. `src/integration_coworker/protocols/asyncapi.py` - AsyncAPI adapter
7. `migrations/002_add_operations_table.sql` - DB migration
8. `tests/unit/domain/test_ir.py` - IR unit tests

### Files Modified (5)
1. `src/integration_coworker/domain/models.py` - Deprecation, conversion method
2. `src/integration_coworker/sources/base.py` - Add protocol_hint
3. `src/integration_coworker/graph/nodes/build_silver_api_model.py` - Dual output
4. `src/integration_coworker/graph/state.py` - Add operations field
5. `src/integration_coworker/codegen/context.py` - Add protocol fields

### Downstream Breaks (Mitigated)
| Component | Break Risk | Mitigation |
|-----------|------------|------------|
| Persistence layer | Low | New table, endpoints unchanged |
| Code generation | Medium | Feature flag for new generators |
| Knowledge Graph | Low | Operations parallel to Endpoints |
| Tests | Low | Endpoints still work, new tests for Operations |

---

## Execution Order

1. **Week 1**: Create IR models (`ir.py`), adapter base (`protocols/base.py`)
2. **Week 2**: Create REST adapter, modify `build_silver_api_model.py` for dual output
3. **Week 3**: Create GraphQL adapter with subscription preservation
4. **Week 4**: Integrate GraphQL adapter, add tests
5. **Week 5**: Create AsyncAPI adapter, integrate
6. **Week 6**: DB migration, deprecation warnings, documentation

---

## Verification Commands

```bash
# Run unit tests for IR
pytest tests/unit/domain/test_ir.py -v

# Run protocol adapter tests
pytest tests/unit/protocols/ -v

# Run integration tests for dual output
pytest tests/integration/test_build_silver_api_model.py -v

# Verify backward compatibility
pytest tests/integration/test_endpoint_backward_compat.py -v

# Run full test suite
pytest tests/ -v --tb=short
```
