"""
AsyncAPI protocol adapter.

Converts NATIVE AsyncAPI specifications to Operation IR, preserving:
- Channel names
- Publish/Subscribe distinction with explicit direction
- Broker protocol information
- Message schemas

ASYNCAPI v2 SEMANTICS:
Per AsyncAPI v2 specification Operation Object:
https://v2.asyncapi.com/docs/reference/specification/v2.6.0#operationObject

AsyncAPI documents describe an APPLICATION. The operations are from
that application's point of view:

- "publish" = the described app RECEIVES messages published to the channel
  (i.e., someone else publishes, the app consumes)
- "subscribe" = the described app SENDS messages to subscribers of the channel
  (i.e., the app produces, someone else subscribes)

This is counter-intuitive but documented in the official "Coming from OpenAPI" tutorial:
https://www.asyncapi.com/docs/tutorials/getting-started/coming-from-openapi

Quote: "publish means the application is the one receiving the message"

The adapter stores:
- metadata.operation: Original operation type ("publish" or "subscribe")
- metadata.direction: Resolved direction ("inbound_to_app" or "outbound_from_app")
- metadata.perspective: Which perspective was used ("app" or "external")

PERSPECTIVE TOGGLE:
- "app" (default): From the described application's view
    publish → app receives (INBOUND_TO_APP)
    subscribe → app sends (OUTBOUND_FROM_APP)

- "external": From an external client's view (INVERSE)
    publish → client sends to app (still INBOUND_TO_APP from app's view)
    subscribe → client receives from app (still OUTBOUND_FROM_APP from app's view)

Design: docs/PROTOCOL_SUPPORT_VNEXT.md Option B
Date: 2025-12-22

References (authoritative):
- AsyncAPI v2 Spec: https://v2.asyncapi.com/docs/reference/specification/v2.6.0#operationObject
- Official Tutorial: https://www.asyncapi.com/docs/tutorials/getting-started/coming-from-openapi
- Concepts: https://www.asyncapi.com/docs/concepts/asyncapi-document/operations
"""
import logging
from typing import List, Dict, Any, Optional, Literal
from dataclasses import dataclass
from enum import Enum

from integration_coworker.domain.ir import (
    Operation,
    ProtocolType,
    CommunicationPattern,
    AsyncAPIMetadata,
)
from .base import ProtocolAdapter

logger = logging.getLogger(__name__)


class MessageDirection(str, Enum):
    """
    Direction of message flow relative to the described application.
    
    This removes ambiguity from "publish" vs "subscribe" which depends
    on perspective.
    """
    INBOUND_TO_APP = "inbound_to_app"      # Messages flowing INTO the app
    OUTBOUND_FROM_APP = "outbound_from_app"  # Messages flowing OUT OF the app


AsyncAPIPerspective = Literal["app", "external"]


@dataclass
class AsyncAPIAdapterConfig:
    """
    Configuration for AsyncAPI adapter.
    
    Attributes:
        perspective: How to interpret operations.
            - "app": Operations are from the described app's view (default)
            - "external": Operations are from an external client's view (inverse)
    """
    perspective: AsyncAPIPerspective = "app"


class AsyncAPIAdapter(ProtocolAdapter):
    """
    Adapter for AsyncAPI specifications.
    
    Preserves event-driven semantics with EXPLICIT direction:
    - Converts publish/subscribe to direction (inbound/outbound)
    - Stores broker protocol metadata
    - Supports perspective toggling for client code generation
    
    The IR stores:
    - communication_pattern: PUBLISH or SUBSCRIBE (the raw operation type)
    - metadata.operation: "publish" or "subscribe" (original AsyncAPI operation)
    - metadata.direction: "inbound_to_app" or "outbound_from_app" (resolved direction)
    
    This allows downstream code to reason about:
    1. What the original spec said (metadata.operation)
    2. What direction messages flow (metadata.direction)
    3. What perspective was used (metadata.perspective)
    """
    
    def __init__(self, config: Optional[AsyncAPIAdapterConfig] = None):
        self._config = config or AsyncAPIAdapterConfig()
    
    @property
    def protocol_type(self) -> ProtocolType:
        return ProtocolType.ASYNCAPI
    
    @property
    def perspective(self) -> AsyncAPIPerspective:
        """Current perspective setting."""
        return self._config.perspective
    
    @perspective.setter
    def perspective(self, value: AsyncAPIPerspective):
        """Set perspective for direction interpretation."""
        self._config.perspective = value
    
    def can_handle(self, spec_data: Dict[str, Any], metadata: Dict[str, Any]) -> bool:
        """Check if spec is AsyncAPI."""
        parsed_from = metadata.get("_parsed_from", "")
        
        # Explicit AsyncAPI marker
        if parsed_from == "asyncapi":
            return True
        
        # Native AsyncAPI structure
        if "asyncapi" in spec_data:
            return True
        
        # Has channels (AsyncAPI 2.x)
        if "channels" in spec_data and "asyncapi" not in metadata.get("_parsed_from", "graphql"):
            return True
        
        # Has operations (AsyncAPI 3.x)
        if "operations" in spec_data and "asyncapi" in str(spec_data.get("asyncapi", "")):
            return True
        
        return False
    
    def convert(
        self,
        spec_data: Dict[str, Any],
        metadata: Dict[str, Any],
        source_uri: str,
    ) -> List[Operation]:
        """
        Convert AsyncAPI spec to Operations.
        
        Supports both AsyncAPI 2.x (channels) and 3.x (operations).
        
        DETERMINISTIC BEHAVIOR:
        1. If spec_data already contains channels/operations, use it
        2. Else if raw_content exists, parse YAML and use that
        3. Only return empty list if both are missing/invalid
        """
        operations: List[Operation] = []
        
        # Try to use spec_data first, then fall back to parsing raw_content
        working_spec = spec_data
        if not working_spec.get("channels") and not working_spec.get("operations"):
            # Try parsing from raw_content (same pattern as GraphQL SDL parsing)
            raw_content = metadata.get("_raw_content", "")
            if raw_content:
                parsed = self._parse_yaml_content(raw_content)
                if parsed:
                    working_spec = parsed
                    logger.debug(f"Parsed AsyncAPI from raw_content for {source_uri}")
        
        # Extract broker info from servers
        broker_info = self._extract_broker_info(working_spec)
        
        # Detect AsyncAPI version
        version = str(working_spec.get("asyncapi", "2.0.0"))
        is_v3 = version.startswith("3.")
        
        if is_v3:
            # AsyncAPI 3.x: operations are top-level
            operations = self._convert_v3(working_spec, source_uri, broker_info)
        elif "channels" in working_spec:
            # AsyncAPI 2.x: operations are inside channels
            operations = self._convert_v2_channels(working_spec, source_uri, broker_info)
        elif "paths" in working_spec:
            # Pseudo-OpenAPI fallback (lossy)
            operations = self._convert_from_pseudo_openapi(working_spec, source_uri, broker_info)
            for op in operations:
                op.conversion_quality = "best_effort"
        
        logger.info(
            f"Extracted {len(operations)} AsyncAPI operations from {source_uri} "
            f"(perspective={self.perspective})"
        )
        return operations
    
    def _parse_yaml_content(self, content: str) -> Optional[Dict[str, Any]]:
        """
        Parse YAML content to dict. Returns None on failure.
        
        This is deterministic (no LLM) — yaml.safe_load is pure.
        """
        try:
            import yaml
            result = yaml.safe_load(content)
            if isinstance(result, dict):
                return result
        except Exception as e:
            logger.debug(f"YAML parsing failed: {e}")
        return None
    
    def _extract_broker_info(self, spec_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract broker protocol and URL from servers."""
        servers = spec_data.get("servers", {})
        if not servers:
            return {}
        
        # Use first server as primary
        if isinstance(servers, dict):
            server_name = list(servers.keys())[0]
            server = servers[server_name]
        else:
            return {}
        
        if not isinstance(server, dict):
            return {}
        
        return {
            "broker_protocol": server.get("protocol"),
            "broker_url": server.get("url") or server.get("host"),
        }
    
    def _resolve_direction(
        self,
        operation_type: str,  # "publish" or "subscribe"
    ) -> MessageDirection:
        """
        Resolve message direction based on operation type and perspective.
        
        CORRECT ASYNCAPI SEMANTICS (per asyncapi.com/blog/publish-subscribe-semantics):
        
        From APP perspective (describing what the app does):
        - "publish" = app RECEIVES messages (someone else publishes TO it)
        - "subscribe" = app SENDS messages (it produces FOR subscribers)
        
        From EXTERNAL perspective (what a client sees):
        - "publish" = client sends TO the app = still INBOUND from app's view
        - "subscribe" = client receives FROM the app = still OUTBOUND from app's view
        
        Both perspectives resolve to the same direction from the app's viewpoint.
        The perspective toggle affects how we INTERPRET the spec, not the direction.
        """
        if self.perspective == "app":
            # App perspective: spec describes what the app does
            # publish = app receives (external publishes TO it) = INBOUND
            # subscribe = app sends (TO external subscribers) = OUTBOUND
            if operation_type == "publish":
                return MessageDirection.INBOUND_TO_APP
            else:
                return MessageDirection.OUTBOUND_FROM_APP
        else:
            # External perspective: spec still describes the app, but we're
            # thinking from a client's view wanting to interact with it
            # If app has "publish" channel, client should send to it = still INBOUND to app
            # If app has "subscribe" channel, client receives from it = still OUTBOUND from app
            if operation_type == "publish":
                return MessageDirection.INBOUND_TO_APP
            else:
                return MessageDirection.OUTBOUND_FROM_APP
    
    def _direction_to_pattern(self, direction: MessageDirection) -> CommunicationPattern:
        """
        Map direction to communication pattern.
        
        INBOUND_TO_APP → SUBSCRIBE (receiving/consuming)
        OUTBOUND_FROM_APP → PUBLISH (sending/producing)
        """
        if direction == MessageDirection.INBOUND_TO_APP:
            return CommunicationPattern.SUBSCRIBE
        else:
            return CommunicationPattern.PUBLISH
    
    def _convert_v2_channels(
        self,
        spec_data: Dict[str, Any],
        source_uri: str,
        broker_info: Dict[str, Any],
    ) -> List[Operation]:
        """Convert AsyncAPI 2.x channels to Operations."""
        operations = []
        
        for channel_name, channel in spec_data.get("channels", {}).items():
            if not isinstance(channel, dict):
                continue
            
            # Publish operation
            if "publish" in channel:
                pub = channel["publish"]
                if isinstance(pub, dict):
                    op = self._create_operation(
                        channel_name, "publish", pub, source_uri, broker_info
                    )
                    operations.append(op)
            
            # Subscribe operation
            if "subscribe" in channel:
                sub = channel["subscribe"]
                if isinstance(sub, dict):
                    op = self._create_operation(
                        channel_name, "subscribe", sub, source_uri, broker_info
                    )
                    operations.append(op)
        
        return operations
    
    def _convert_v3(
        self,
        spec_data: Dict[str, Any],
        source_uri: str,
        broker_info: Dict[str, Any],
    ) -> List[Operation]:
        """Convert AsyncAPI 3.x operations to Operations."""
        operations = []
        
        for op_name, op_data in spec_data.get("operations", {}).items():
            if not isinstance(op_data, dict):
                continue
            
            action = op_data.get("action", "")  # "send" or "receive"
            channel_ref = op_data.get("channel", {})
            
            # Map v3 actions to v2-style operation types
            if action == "send":
                op_type = "publish"
            elif action == "receive":
                op_type = "subscribe"
            else:
                continue
            
            # Extract channel name from reference
            channel_name = op_name  # Fallback to operation name
            if isinstance(channel_ref, dict) and "$ref" in channel_ref:
                ref = channel_ref["$ref"]
                if "#/channels/" in ref:
                    channel_name = ref.split("#/channels/")[-1]
            
            op = self._create_operation(
                channel_name, op_type, op_data, source_uri, broker_info
            )
            operations.append(op)
        
        return operations
    
    def _create_operation(
        self,
        channel: str,
        operation_type: str,  # "publish" or "subscribe"
        op_data: Dict[str, Any],
        source_uri: str,
        broker_info: Dict[str, Any],
    ) -> Operation:
        """Create an Operation from a channel operation."""
        operation_id = op_data.get("operationId", f"{operation_type}_{channel.replace('/', '_')}")
        
        # Resolve direction based on perspective
        direction = self._resolve_direction(operation_type)
        
        # Map to communication pattern
        comm_pattern = self._direction_to_pattern(direction)
        
        # Get message schema
        message = op_data.get("message", {})
        if isinstance(message, dict):
            payload = message.get("payload", {})
            payload_ref = payload.get("$ref") if isinstance(payload, dict) else None
            message_name = message.get("name")
        else:
            payload_ref = None
            message_name = None
        
        # Direction determines input vs output:
        # OUTBOUND = we're sending = input_schema
        # INBOUND = we're receiving = output_schema
        if direction == MessageDirection.OUTBOUND_FROM_APP:
            input_schema_ref = payload_ref
            output_schema_ref = None
        else:
            input_schema_ref = None
            output_schema_ref = payload_ref
        
        return Operation(
            name=operation_id,
            operation_id=operation_id,
            summary=op_data.get("summary"),
            description=op_data.get("description"),
            tags=op_data.get("tags", []),
            protocol=ProtocolType.ASYNCAPI,
            communication_pattern=comm_pattern,
            input_schema_ref=input_schema_ref,
            output_schema_ref=output_schema_ref,
            metadata=AsyncAPIMetadata(
                channel=channel,
                operation=operation_type,  # Original: "publish" or "subscribe"
                direction=direction.value,  # Resolved: "inbound_to_app" or "outbound_from_app"
                perspective=self.perspective,  # "app" or "external"
                broker_protocol=broker_info.get("broker_protocol"),
                broker_url=broker_info.get("broker_url"),
                message_name=message_name,
            ),
            source_uri=source_uri,
            conversion_quality="deterministic",
        )
    
    def _convert_from_pseudo_openapi(
        self,
        spec_data: Dict[str, Any],
        source_uri: str,
        broker_info: Dict[str, Any],
    ) -> List[Operation]:
        """
        Extract from pseudo-OpenAPI structure (lossy fallback).
        
        This path infers operation type from HTTP method:
        - POST → publish (sending)
        - GET → subscribe (receiving)
        
        This is LOSSY because HTTP methods don't capture AsyncAPI semantics.
        """
        operations = []
        
        for path, path_item in spec_data.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            
            # Infer channel from path
            if path.startswith("/channels/"):
                channel = path[len("/channels/"):]
            else:
                channel = path.strip("/")
            
            for method, op_data in path_item.items():
                if method not in ("get", "post"):
                    continue
                if not isinstance(op_data, dict):
                    continue
                
                # Infer operation type from HTTP method
                # POST typically means "send/publish", GET means "receive/subscribe"
                op_type = "publish" if method == "post" else "subscribe"
                
                op = self._create_operation(
                    channel, op_type, op_data, source_uri, broker_info
                )
                op.conversion_quality = "best_effort"  # Mark as lossy
                operations.append(op)
        
        return operations


# Factory function for creating adapter with specific perspective
def create_asyncapi_adapter(
    perspective: AsyncAPIPerspective = "app"
) -> AsyncAPIAdapter:
    """
    Create an AsyncAPI adapter with a specific perspective.
    
    Args:
        perspective: 
            - "app": Interpret from described app's view (default)
            - "external": Interpret from external client's view (inverse)
    
    Returns:
        Configured AsyncAPIAdapter
    """
    return AsyncAPIAdapter(AsyncAPIAdapterConfig(perspective=perspective))
