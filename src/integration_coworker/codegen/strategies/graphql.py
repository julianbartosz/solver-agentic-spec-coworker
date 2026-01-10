"""
GraphQL codegen strategy.

Generates:
- Query/Mutation: Standard GraphQL client with httpx
- Subscription: WebSocket client with graphql-ws protocol

TRANSPORT SEMANTICS:
GraphQL subscriptions are transport-agnostic per the GraphQL spec.
However, the dominant transport is WebSocket using the graphql-ws library's protocol.

The WebSocket subprotocol string is: "graphql-transport-ws"

References (authoritative):
- GraphQL Subscriptions spec: https://spec.graphql.org/October2021/#sec-Subscription
- graphql-ws PROTOCOL.md: https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md
  (defines "graphql-transport-ws" as the Sec-WebSocket-Protocol value)
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
class GraphQLCodegenStrategy:
    """
    Code generation strategy for GraphQL operations.
    
    Generates:
    - Query/Mutation: Standard GraphQL client with httpx
    - Subscription: WebSocket client with graphql-ws protocol
    
    CRITICAL: Subscriptions are NOT POST endpoints!
    They require WebSocket transport with graphql-transport-ws subprotocol.
    
    Reference: https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md
    """
    
    @property
    def protocol_type(self) -> ProtocolType:
        return ProtocolType.GRAPHQL
    
    def generate_client(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """Generate GraphQL client."""
        is_subscription = operation.communication_pattern == CommunicationPattern.SUBSCRIBE
        
        if is_subscription:
            return self._generate_subscription_client(operation, context)
        else:
            return self._generate_query_mutation_client(operation, context)
    
    def _generate_query_mutation_client(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """Generate query/mutation client."""
        op_type = "mutation" if operation.metadata and operation.metadata.get("operation_type") == "mutation" else "query"
        
        code = self._generate_graphql_http_code(operation, context, op_type)
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.CLIENT,
            filename=f"{context.provider_code}_graphql_client.py",
            code=code,
            language=context.language,
            imports=["httpx", "typing"],
            dependencies=["httpx"],
            protocol=ProtocolType.GRAPHQL,
            operation_id=operation.operation_id,
            metadata={"operation_type": op_type},
        )
    
    def _generate_subscription_client(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """
        Generate WebSocket subscription client.
        
        Uses graphql-transport-ws protocol per:
        https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md
        """
        code = self._generate_subscription_code(operation, context)
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.SUBSCRIBER,
            filename=f"{context.provider_code}_subscription_client.py",
            code=code,
            language=context.language,
            imports=["websockets", "json", "asyncio"],
            dependencies=["websockets"],
            protocol=ProtocolType.GRAPHQL,
            operation_id=operation.operation_id,
            metadata={
                "transport": context.graphql_transport,
                "ws_subprotocol": context.graphql_ws_subprotocol,
                "requires_websocket": True,
            },
        )
    
    def generate_flow(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """Generate GraphQL flow."""
        flow_name = f"{context.provider_code}_graphql_{self._to_method_name(operation.name or 'flow')}"
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.FLOW,
            filename=f"{flow_name}.py",
            code=f"# TODO: Implement GraphQL flow for {operation.name}",
            language=context.language,
            protocol=ProtocolType.GRAPHQL,
            operation_id=operation.operation_id,
        )
    
    def generate_test(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """Generate GraphQL test."""
        is_subscription = operation.communication_pattern == CommunicationPattern.SUBSCRIBE
        test_code = self._generate_subscription_test(operation, context) if is_subscription else self._generate_query_test(operation, context)
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.TEST,
            filename=f"test_{context.provider_code}_graphql.py",
            code=test_code,
            language=context.language,
            dependencies=["pytest", "pytest-asyncio"],
            protocol=ProtocolType.GRAPHQL,
            operation_id=operation.operation_id,
        )
    
    def build_prompt(
        self,
        operation: Operation,
        context: CodegenContext,
        artifact_type: ArtifactType,
        skeleton_code: str,
    ) -> str:
        """Build GraphQL-specific LLM prompt."""
        is_subscription = operation.communication_pattern == CommunicationPattern.SUBSCRIBE
        op_type = "subscription" if is_subscription else (
            "mutation" if operation.metadata and operation.metadata.get("operation_type") == "mutation" else "query"
        )
        
        transport_note = ""
        if is_subscription:
            transport_note = f"""
## TRANSPORT REQUIREMENTS (CRITICAL)
This is a GraphQL SUBSCRIPTION - it requires WebSocket transport!
- Transport: {context.graphql_transport}
- WebSocket Subprotocol: {context.graphql_ws_subprotocol}
- DO NOT use HTTP POST for subscriptions
- Use websockets library with proper protocol handshake
- Reference: https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md
"""
        
        return f"""You are generating a GraphQL {op_type} client in {context.language}.

## OPERATION DETAILS
- Operation Type: {op_type}
- Operation Name: {operation.name}
- Summary: {operation.summary or 'N/A'}
{transport_note}

## SKELETON CODE
```{context.language}
{skeleton_code}
```

## REQUIREMENTS
1. {"Use websockets for real-time connection" if is_subscription else "Use httpx for HTTP transport"}
2. Handle errors appropriately
3. Include proper type hints
4. Add docstrings with Google style
{"5. Implement proper WebSocket lifecycle (connect, subscribe, receive, close)" if is_subscription else ""}

Return ONLY the completed code, no explanations.
"""
    
    def _to_method_name(self, name: str) -> str:
        """Convert operation name to Python method name."""
        clean = "".join(c if c.isalnum() else "_" for c in name)
        return clean.lower().strip("_")
    
    def _generate_graphql_http_code(
        self,
        operation: Operation,
        context: CodegenContext,
        op_type: str,
    ) -> str:
        """Generate GraphQL HTTP client code for queries/mutations."""
        return f'''"""
GraphQL {op_type} client for {context.provider_code}.

Auto-generated by Integration Co-Worker.
"""
import httpx
from typing import Any, Dict, Optional


class {context.provider_code.title()}GraphQLClient:
    """GraphQL client for {context.provider_code}."""
    
    def __init__(self, endpoint: str, api_key: Optional[str] = None):
        self.endpoint = endpoint
        self.api_key = api_key
    
    async def {self._to_method_name(operation.name or "execute")}(
        self,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute {operation.name} {op_type}.
        
        {operation.summary or ""}
        
        Args:
            variables: GraphQL variables
        
        Returns:
            GraphQL response data
        """
        query = """
        {op_type} {operation.name or "Operation"} {{
            # TODO: Add fields
        }}
        """
        
        headers = {{"Authorization": f"Bearer {{self.api_key}}"}} if self.api_key else {{}}
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.endpoint,
                json={{"query": query, "variables": variables or {{}}}},
                headers=headers,
            )
            response.raise_for_status()
            result = response.json()
            
            if "errors" in result:
                raise Exception(f"GraphQL errors: {{result['errors']}}")
            
            return result.get("data", {{}})
'''
    
    def _generate_subscription_code(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> str:
        """
        Generate WebSocket subscription client.
        
        Uses graphql-transport-ws protocol as per:
        https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md
        """
        return f'''"""
GraphQL Subscription client for {context.provider_code}.

Auto-generated by Integration Co-Worker.

TRANSPORT: WebSocket with {context.graphql_ws_subprotocol} subprotocol.
This is NOT an HTTP endpoint - subscriptions require persistent connections.

Protocol Reference: https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md
"""
import asyncio
import json
from typing import Any, AsyncIterator, Callable, Dict, Optional
import websockets
from websockets.client import WebSocketClientProtocol


class {context.provider_code.title()}SubscriptionClient:
    """
    GraphQL Subscription client using WebSocket transport.
    
    Uses the graphql-transport-ws protocol.
    The Sec-WebSocket-Protocol header value is: {context.graphql_ws_subprotocol}
    
    Reference: https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md
    """
    
    SUBPROTOCOL = "{context.graphql_ws_subprotocol}"
    
    def __init__(
        self,
        ws_endpoint: str,
        api_key: Optional[str] = None,
    ):
        """
        Initialize subscription client.
        
        Args:
            ws_endpoint: WebSocket endpoint (ws:// or wss://)
            api_key: Optional API key for authentication
        """
        self.ws_endpoint = ws_endpoint
        self.api_key = api_key
        self._ws: Optional[WebSocketClientProtocol] = None
        self._subscription_id = 0
    
    async def connect(self) -> None:
        """
        Establish WebSocket connection and initialize protocol.
        """
        connection_params = {{}}
        if self.api_key:
            connection_params["Authorization"] = f"Bearer {{self.api_key}}"
        
        self._ws = await websockets.connect(
            self.ws_endpoint,
            subprotocols=[self.SUBPROTOCOL],
        )
        
        # Send connection_init per protocol spec
        await self._ws.send(json.dumps({{
            "type": "connection_init",
            "payload": connection_params,
        }}))
        
        # Wait for connection_ack
        response = await self._ws.recv()
        msg = json.loads(response)
        if msg.get("type") != "connection_ack":
            raise Exception(f"Connection failed: {{msg}}")
    
    async def subscribe_{self._to_method_name(operation.name or "events")}(
        self,
        variables: Optional[Dict[str, Any]] = None,
        on_data: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Subscribe to {operation.name}.
        
        {operation.summary or ""}
        
        Args:
            variables: Subscription variables
            on_data: Optional callback for each data event
        
        Yields:
            Subscription data events
        """
        if not self._ws:
            await self.connect()
        
        self._subscription_id += 1
        sub_id = str(self._subscription_id)
        
        subscription_query = """
        subscription {operation.name or "Subscription"} {{
            # TODO: Add subscription fields
        }}
        """
        
        # Send subscribe message per protocol spec
        await self._ws.send(json.dumps({{
            "id": sub_id,
            "type": "subscribe",
            "payload": {{
                "query": subscription_query,
                "variables": variables or {{}},
            }},
        }}))
        
        # Receive events
        try:
            async for raw_message in self._ws:
                msg = json.loads(raw_message)
                msg_type = msg.get("type")
                
                if msg_type == "next" and msg.get("id") == sub_id:
                    data = msg.get("payload", {{}}).get("data", {{}})
                    if on_data:
                        on_data(data)
                    yield data
                elif msg_type == "error" and msg.get("id") == sub_id:
                    raise Exception(f"Subscription error: {{msg.get('payload')}}")
                elif msg_type == "complete" and msg.get("id") == sub_id:
                    break
        finally:
            # Send complete to clean up server-side
            if self._ws:
                await self._ws.send(json.dumps({{
                    "id": sub_id,
                    "type": "complete",
                }}))
    
    async def close(self) -> None:
        """Close WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._ws = None
'''
    
    def _generate_query_test(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> str:
        """Generate test for query/mutation."""
        return f'''"""
Tests for {operation.name} GraphQL operation.

Auto-generated by Integration Co-Worker.
"""
import pytest
from unittest.mock import AsyncMock, patch


class TestGraphQL{(operation.name or "Query").title().replace("_", "")}:
    """Tests for {operation.summary or operation.name}."""
    
    @pytest.mark.asyncio
    async def test_query_success(self):
        """Test successful query execution."""
        # TODO: Implement test with mocked httpx
        pass
    
    @pytest.mark.asyncio
    async def test_query_graphql_error(self):
        """Test GraphQL error handling."""
        # TODO: Implement test
        pass
'''
    
    def _generate_subscription_test(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> str:
        """Generate test for subscription."""
        return f'''"""
Tests for {operation.name} GraphQL subscription.

Auto-generated by Integration Co-Worker.

IMPORTANT: Subscription tests must mock WebSocket, not HTTP!
Protocol: {context.graphql_ws_subprotocol}
"""
import pytest
from unittest.mock import AsyncMock, patch


class TestGraphQLSubscription{(operation.name or "Events").title().replace("_", "")}:
    """
    Tests for {operation.summary or operation.name} subscription.
    
    These tests verify WebSocket-based subscription behavior.
    """
    
    @pytest.mark.asyncio
    async def test_subscription_connects_with_correct_subprotocol(self):
        """Verify WebSocket uses graphql-transport-ws subprotocol."""
        # TODO: Mock websockets.connect and verify subprotocols=["{context.graphql_ws_subprotocol}"]
        pass
    
    @pytest.mark.asyncio
    async def test_subscription_sends_connection_init(self):
        """Verify connection_init message is sent per protocol spec."""
        # TODO: Verify protocol handshake
        pass
    
    @pytest.mark.asyncio
    async def test_subscription_yields_data_events(self):
        """Test receiving subscription data."""
        # TODO: Mock WebSocket messages and verify yielded data
        pass
    
    @pytest.mark.asyncio
    async def test_subscription_handles_error(self):
        """Test subscription error handling."""
        # TODO: Verify error type handling
        pass
'''
