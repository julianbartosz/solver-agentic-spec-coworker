"""
AsyncAPI codegen strategy.

Generates:
- Publisher: For operations where the APP SENDS messages (outbound)
- Subscriber: For operations where the APP RECEIVES messages (inbound)

SEMANTIC MAPPING (CRITICAL):
AsyncAPI uses app-centric terminology where the perspective is from the
APPLICATION described by the spec, not the external consumer.

Per AsyncAPI v2 specification section on "Operation Object":
https://v2.asyncapi.com/docs/reference/specification/v2.6.0#operationObject

- "publish" operation: The app RECEIVES messages (inbound from user's perspective)
  → Maps to CommunicationPattern.SUBSCRIBE in our IR
  → Generates a Subscriber artifact

- "subscribe" operation: The app SENDS messages (outbound from user's perspective)
  → Maps to CommunicationPattern.PUBLISH in our IR
  → Generates a Publisher artifact

This counter-intuitive mapping is explained in the official AsyncAPI docs:
https://www.asyncapi.com/docs/tutorials/getting-started/coming-from-openapi

"publish means the application is the one receiving the message,
 subscribe means the application is the one sending the message"
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from integration_coworker.codegen.protocol_dispatch import (
    ArtifactType,
    CodegenContext,
    GeneratedArtifact,
)
from integration_coworker.domain.ir import (
    CommunicationPattern,
    Operation,
    ProtocolType,
)

logger = logging.getLogger(__name__)


@dataclass
class AsyncAPICodegenStrategy:
    """
    Code generation strategy for AsyncAPI operations.
    
    SEMANTIC MAPPING (CRITICAL - Read carefully!):
    
    AsyncAPI uses app-centric terminology. The "app" is the system described
    by the spec. Operations describe what the APP does, not what consumers do.
    
    Per AsyncAPI v2 spec (https://v2.asyncapi.com/docs/reference/specification/v2.6.0):
    
    - "publish" operation = APP RECEIVES messages
      → We generate a SUBSCRIBER (inbound handler)
      → Our IR: CommunicationPattern.SUBSCRIBE
    
    - "subscribe" operation = APP SENDS messages  
      → We generate a PUBLISHER (outbound sender)
      → Our IR: CommunicationPattern.PUBLISH
    
    This is the opposite of what you might expect! The AsyncAPI "Coming from OpenAPI"
    tutorial explicitly clarifies this:
    https://www.asyncapi.com/docs/tutorials/getting-started/coming-from-openapi
    
    Quote: "publish means the application is the one receiving the message"
    """
    
    @property
    def protocol_type(self) -> ProtocolType:
        return ProtocolType.ASYNCAPI
    
    def _get_channel(self, operation: Operation) -> str:
        """Extract channel from operation metadata."""
        if operation.metadata and hasattr(operation.metadata, 'channel'):
            return operation.metadata.channel
        return operation.name or "unknown"
    
    def generate_client(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """
        Generate AsyncAPI client based on operation direction.
        
        DIRECTION SEMANTICS:
        - CommunicationPattern.PUBLISH → APP SENDS → Generate Publisher
        - CommunicationPattern.SUBSCRIBE → APP RECEIVES → Generate Subscriber
        """
        if operation.communication_pattern == CommunicationPattern.PUBLISH:
            return self._generate_publisher(operation, context)
        else:
            return self._generate_subscriber(operation, context)
    
    def _generate_publisher(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """
        Generate publisher for outbound messages.
        
        In AsyncAPI terms: This handles "subscribe" operations.
        The APP SENDS messages that consumers subscribe to.
        """
        code = self._generate_publisher_code(operation, context)
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.PUBLISHER,
            filename=f"{context.provider_code}_{self._to_method_name(operation.name or 'publisher')}_publisher.py",
            code=code,
            language=context.language,
            imports=["asyncio", "json"],
            dependencies=self._get_transport_dependencies(context),
            protocol=ProtocolType.ASYNCAPI,
            operation_id=operation.operation_id,
            metadata={
                "asyncapi_operation": "subscribe",  # APP sends
                "direction": "outbound",
                "channel": self._get_channel(operation),
                "transport": context.asyncapi_broker,
            },
        )
    
    def _generate_subscriber(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """
        Generate subscriber for inbound messages.
        
        In AsyncAPI terms: This handles "publish" operations.
        The APP RECEIVES messages that publishers send.
        """
        code = self._generate_subscriber_code(operation, context)
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.SUBSCRIBER,
            filename=f"{context.provider_code}_{self._to_method_name(operation.name or 'subscriber')}_subscriber.py",
            code=code,
            language=context.language,
            imports=["asyncio", "json"],
            dependencies=self._get_transport_dependencies(context),
            protocol=ProtocolType.ASYNCAPI,
            operation_id=operation.operation_id,
            metadata={
                "asyncapi_operation": "publish",  # APP receives
                "direction": "inbound",
                "channel": self._get_channel(operation),
                "transport": context.asyncapi_broker,
            },
        )
    
    def generate_flow(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """Generate AsyncAPI integration flow."""
        flow_name = f"{context.provider_code}_async_{self._to_method_name(operation.name or 'flow')}"
        direction = "outbound" if operation.communication_pattern == CommunicationPattern.PUBLISH else "inbound"
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.FLOW,
            filename=f"{flow_name}.py",
            code=f"# TODO: Implement AsyncAPI {direction} flow for {operation.name}",
            language=context.language,
            protocol=ProtocolType.ASYNCAPI,
            operation_id=operation.operation_id,
        )
    
    def generate_test(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """Generate AsyncAPI test."""
        is_publisher = operation.communication_pattern == CommunicationPattern.PUBLISH
        test_code = self._generate_publisher_test(operation, context) if is_publisher else self._generate_subscriber_test(operation, context)
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.TEST,
            filename=f"test_{context.provider_code}_async.py",
            code=test_code,
            language=context.language,
            dependencies=["pytest", "pytest-asyncio"],
            protocol=ProtocolType.ASYNCAPI,
            operation_id=operation.operation_id,
        )
    
    def build_prompt(
        self,
        operation: Operation,
        context: CodegenContext,
        artifact_type: ArtifactType,
        skeleton_code: str,
    ) -> str:
        """Build AsyncAPI-specific LLM prompt."""
        is_publisher = operation.communication_pattern == CommunicationPattern.PUBLISH
        direction = "outbound (APP SENDS)" if is_publisher else "inbound (APP RECEIVES)"
        asyncapi_op = "subscribe" if is_publisher else "publish"
        channel = self._get_channel(operation)
        
        return f"""You are generating an AsyncAPI {artifact_type.value} in {context.language}.

## OPERATION DETAILS
- Channel: {channel}
- Direction: {direction}
- AsyncAPI Operation Type: {asyncapi_op}
- Summary: {operation.summary or 'N/A'}

## ASYNCAPI SEMANTIC MAPPING (CRITICAL)
This operation is from the APP's perspective, not the consumer's!

In AsyncAPI:
- "publish" = APP RECEIVES messages (inbound)
- "subscribe" = APP SENDS messages (outbound)

Reference: https://v2.asyncapi.com/docs/reference/specification/v2.6.0#operationObject

## TRANSPORT
- Transport: {context.asyncapi_broker}
- Dependencies: {', '.join(self._get_transport_dependencies(context))}

## SKELETON CODE
```{context.language}
{skeleton_code}
```

## REQUIREMENTS
1. Use appropriate async messaging pattern
2. Handle connection lifecycle properly
3. Include proper type hints
4. Add docstrings with Google style
5. Implement error handling and reconnection logic

Return ONLY the completed code, no explanations.
"""
    
    def _to_method_name(self, name: str) -> str:
        """Convert operation name to Python method name."""
        clean = "".join(c if c.isalnum() else "_" for c in name)
        return clean.lower().strip("_")
    
    def _get_transport_dependencies(self, context: CodegenContext) -> list:
        """Get dependencies based on transport."""
        transport_deps = {
            "kafka": ["aiokafka"],
            "amqp": ["aio-pika"],
            "mqtt": ["asyncio-mqtt"],
            "websocket": ["websockets"],
            "http": ["httpx"],
        }
        return transport_deps.get(context.asyncapi_broker, ["asyncio"])
    
    def _generate_publisher_code(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> str:
        """
        Generate publisher code (outbound - APP SENDS).
        
        This corresponds to AsyncAPI "subscribe" operations.
        """
        channel = self._get_channel(operation)
        return f'''"""
AsyncAPI Publisher for {operation.name or channel}.

Auto-generated by Integration Co-Worker.

DIRECTION: OUTBOUND (APP SENDS)
ASYNCAPI OPERATION TYPE: subscribe
CHANNEL: {channel}

In AsyncAPI terms, this is a "subscribe" operation because the APP
sends messages that external consumers subscribe to receive.

Reference: https://v2.asyncapi.com/docs/reference/specification/v2.6.0#operationObject
"""
import asyncio
import json
from typing import Any, Dict, Optional
from dataclasses import dataclass


@dataclass
class {context.provider_code.title()}Publisher:
    """
    Publisher for {channel} channel.
    
    This publisher SENDS messages from the application to external consumers.
    
    AsyncAPI perspective: Handles "subscribe" operations (app is the producer).
    """
    
    broker_url: str
    channel: str = "{channel}"
    
    async def connect(self) -> None:
        """Establish connection to message broker."""
        # TODO: Implement connection logic for {context.asyncapi_broker}
        pass
    
    async def publish(
        self,
        message: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Publish message to {channel}.
        
        {operation.summary or ""}
        
        DIRECTION: OUTBOUND (this app is SENDING)
        
        Args:
            message: Message payload to publish
            headers: Optional message headers
        """
        # TODO: Implement publish logic for {context.asyncapi_broker}
        payload = json.dumps(message)
        # await self._producer.send(self.channel, payload)
        pass
    
    async def close(self) -> None:
        """Close connection to broker."""
        pass
'''
    
    def _generate_subscriber_code(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> str:
        """
        Generate subscriber code (inbound - APP RECEIVES).
        
        This corresponds to AsyncAPI "publish" operations.
        """
        channel = self._get_channel(operation)
        return f'''"""
AsyncAPI Subscriber for {operation.name or channel}.

Auto-generated by Integration Co-Worker.

DIRECTION: INBOUND (APP RECEIVES)
ASYNCAPI OPERATION TYPE: publish
CHANNEL: {channel}

In AsyncAPI terms, this is a "publish" operation because external
publishers send messages that this APP receives and processes.

Reference: https://v2.asyncapi.com/docs/reference/specification/v2.6.0#operationObject
"""
import asyncio
import json
from typing import Any, Callable, Dict, Optional
from dataclasses import dataclass


@dataclass  
class {context.provider_code.title()}Subscriber:
    """
    Subscriber for {channel} channel.
    
    This subscriber RECEIVES messages from external publishers.
    
    AsyncAPI perspective: Handles "publish" operations (app is the consumer).
    """
    
    broker_url: str
    channel: str = "{channel}"
    
    async def connect(self) -> None:
        """Establish connection to message broker."""
        # TODO: Implement connection logic for {context.asyncapi_broker}
        pass
    
    async def subscribe(
        self,
        handler: Callable[[Dict[str, Any]], None],
    ) -> None:
        """
        Subscribe to {channel} and process incoming messages.
        
        {operation.summary or ""}
        
        DIRECTION: INBOUND (this app is RECEIVING)
        
        Args:
            handler: Callback function to process each message
        """
        # TODO: Implement subscribe logic for {context.asyncapi_broker}
        # async for message in self._consumer:
        #     payload = json.loads(message.value)
        #     handler(payload)
        pass
    
    async def close(self) -> None:
        """Close connection to broker."""
        pass
'''
    
    def _generate_publisher_test(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> str:
        """Generate test for publisher (outbound)."""
        channel = self._get_channel(operation)
        return f'''"""
Tests for {operation.name or channel} AsyncAPI publisher.

Auto-generated by Integration Co-Worker.

DIRECTION: OUTBOUND (APP SENDS)
ASYNCAPI OPERATION: subscribe
"""
import pytest
from unittest.mock import AsyncMock, patch


class TestAsyncAPI{(operation.name or "Publisher").title().replace("_", "")}:
    """
    Tests for {operation.summary or operation.name or "publisher"}.
    
    These tests verify outbound message publishing behavior.
    Direction: APP → External consumers
    """
    
    @pytest.mark.asyncio
    async def test_publisher_connects(self):
        """Test connection to message broker."""
        # TODO: Implement
        pass
    
    @pytest.mark.asyncio
    async def test_publisher_sends_message(self):
        """Test message publication."""
        # TODO: Verify message is sent with correct format
        pass
    
    @pytest.mark.asyncio
    async def test_publisher_metadata_indicates_outbound(self):
        """Verify metadata correctly indicates outbound direction."""
        # The generated artifact should have:
        # - asyncapi_operation: "subscribe"
        # - direction: "outbound"
        pass
'''
    
    def _generate_subscriber_test(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> str:
        """Generate test for subscriber (inbound)."""
        channel = self._get_channel(operation)
        return f'''"""
Tests for {operation.name or channel} AsyncAPI subscriber.

Auto-generated by Integration Co-Worker.

DIRECTION: INBOUND (APP RECEIVES)
ASYNCAPI OPERATION: publish
"""
import pytest
from unittest.mock import AsyncMock, patch


class TestAsyncAPI{(operation.name or "Subscriber").title().replace("_", "")}:
    """
    Tests for {operation.summary or operation.name or "subscriber"}.
    
    These tests verify inbound message subscription behavior.
    Direction: External publishers → APP
    """
    
    @pytest.mark.asyncio
    async def test_subscriber_connects(self):
        """Test connection to message broker."""
        # TODO: Implement
        pass
    
    @pytest.mark.asyncio
    async def test_subscriber_receives_message(self):
        """Test message reception."""
        # TODO: Mock incoming message and verify handler is called
        pass
    
    @pytest.mark.asyncio
    async def test_subscriber_metadata_indicates_inbound(self):
        """Verify metadata correctly indicates inbound direction."""
        # The generated artifact should have:
        # - asyncapi_operation: "publish"
        # - direction: "inbound"
        pass
'''
