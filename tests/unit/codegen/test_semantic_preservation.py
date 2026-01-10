"""
Semantic preservation tests for protocol-aware codegen.

These tests verify that protocol-specific semantics are PRESERVED through
the codegen pipeline and NOT lost/corrupted:

1. GraphQL subscriptions → WebSocket transport (NOT HTTP POST)
2. AsyncAPI pub/sub → Correct direction (publish=receive, subscribe=send)
3. REST → Standard HTTP semantics

Per Phase 2 requirements:
- "subscriptions aren't POST endpoints"
- "pub/sub direction isn't lost"
"""
import pytest
from typing import Dict, Any

from integration_coworker.codegen.protocol_dispatch import (
    ArtifactType,
    CodegenContext,
    GeneratedArtifact,
    StrategyDispatcher,
    create_default_dispatcher,
)
from integration_coworker.codegen.strategies import (
    AsyncAPICodegenStrategy,
    GraphQLCodegenStrategy,
    RESTCodegenStrategy,
)
from integration_coworker.domain.ir import (
    CommunicationPattern,
    Operation,
    ProtocolType,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def rest_operation() -> Operation:
    """Create a REST operation for testing."""
    return Operation(
        name="getUser",
        operation_id="get_user",
        summary="Get user by ID",
        protocol=ProtocolType.REST,
        communication_pattern=CommunicationPattern.UNARY,
        metadata={"method": "GET", "path": "/users/{id}"},
    )


@pytest.fixture
def graphql_query_operation() -> Operation:
    """Create a GraphQL query operation."""
    return Operation(
        name="getUser",
        operation_id="Query.getUser",
        summary="Get user by ID",
        protocol=ProtocolType.GRAPHQL,
        communication_pattern=CommunicationPattern.UNARY,
        metadata={"operation_type": "query"},
    )


@pytest.fixture
def graphql_mutation_operation() -> Operation:
    """Create a GraphQL mutation operation."""
    return Operation(
        name="createUser",
        operation_id="Mutation.createUser",
        summary="Create a new user",
        protocol=ProtocolType.GRAPHQL,
        communication_pattern=CommunicationPattern.UNARY,
        metadata={"operation_type": "mutation"},
    )


@pytest.fixture
def graphql_subscription_operation() -> Operation:
    """Create a GraphQL subscription operation."""
    return Operation(
        name="userCreated",
        operation_id="Subscription.userCreated",
        summary="Subscribe to user creation events",
        protocol=ProtocolType.GRAPHQL,
        communication_pattern=CommunicationPattern.SUBSCRIBE,
        metadata={
            "operation_type": "subscription",
            "requires_websocket": True,
        },
    )


@pytest.fixture
def asyncapi_publish_operation() -> Operation:
    """
    Create an AsyncAPI operation where app RECEIVES (publish channel).
    
    In AsyncAPI v2: "publish" = others publish TO the app = app RECEIVES.
    """
    return Operation(
        name="user.created",
        operation_id="user.created.receive",
        summary="Receive user created events",
        protocol=ProtocolType.ASYNCAPI,
        communication_pattern=CommunicationPattern.SUBSCRIBE,  # App receives = SUBSCRIBE pattern
        metadata={
            "asyncapi_operation": "publish",  # AsyncAPI v2 publish channel
            "channel": "user.created",
        },
    )


@pytest.fixture
def asyncapi_subscribe_operation() -> Operation:
    """
    Create an AsyncAPI operation where app SENDS (subscribe channel).
    
    In AsyncAPI v2: "subscribe" = others subscribe FROM the app = app SENDS.
    """
    return Operation(
        name="notification.send",
        operation_id="notification.send.publish",
        summary="Send notification events",
        protocol=ProtocolType.ASYNCAPI,
        communication_pattern=CommunicationPattern.PUBLISH,  # App sends = PUBLISH pattern
        metadata={
            "asyncapi_operation": "subscribe",  # AsyncAPI v2 subscribe channel
            "channel": "notification.send",
        },
    )


@pytest.fixture
def codegen_context() -> CodegenContext:
    """Create a standard codegen context."""
    return CodegenContext(
        language="python",
        provider_code="test",
        use_async=True,
    )


@pytest.fixture
def dispatcher() -> StrategyDispatcher:
    """Create a fully-configured dispatcher."""
    return create_default_dispatcher()


# =============================================================================
# GraphQL Subscription Semantic Preservation Tests
# =============================================================================

class TestGraphQLSubscriptionSemantics:
    """
    Tests verifying GraphQL subscription semantics are preserved.
    
    CRITICAL: Subscriptions are NOT HTTP POST endpoints!
    They require WebSocket transport.
    """
    
    def test_subscription_generates_websocket_client(
        self,
        graphql_subscription_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """Subscription codegen must produce WebSocket-based client."""
        artifact = dispatcher.generate(
            graphql_subscription_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.SUBSCRIBER
        assert "websockets" in artifact.dependencies
        assert artifact.metadata.get("requires_websocket") is True
    
    def test_subscription_code_uses_websockets_not_httpx(
        self,
        graphql_subscription_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """Subscription code must use websockets, not HTTP client."""
        artifact = dispatcher.generate(
            graphql_subscription_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        assert artifact is not None
        # Must use websockets
        assert "websockets" in artifact.code.lower() or "websocket" in artifact.code.lower()
        # Must NOT use httpx for the subscription itself
        assert "httpx.post" not in artifact.code.lower()
    
    def test_subscription_uses_correct_subprotocol(
        self,
        graphql_subscription_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """Subscription must use graphql-transport-ws subprotocol."""
        artifact = dispatcher.generate(
            graphql_subscription_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        assert artifact is not None
        assert "graphql-transport-ws" in artifact.code
        assert artifact.metadata.get("ws_subprotocol") == "graphql-transport-ws"
    
    def test_subscription_prompt_mentions_websocket(
        self,
        graphql_subscription_operation: Operation,
        codegen_context: CodegenContext,
    ):
        """LLM prompt for subscriptions must mention WebSocket requirement."""
        strategy = GraphQLCodegenStrategy()
        prompt = strategy.build_prompt(
            graphql_subscription_operation,
            codegen_context,
            ArtifactType.CLIENT,
            skeleton_code="# skeleton",
        )
        
        assert "WebSocket" in prompt or "websocket" in prompt.lower()
        assert "graphql-transport-ws" in prompt
        assert "NOT" in prompt and "POST" in prompt  # Warning about not using POST
    
    def test_query_mutation_use_http_transport(
        self,
        graphql_query_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """Queries and mutations should use HTTP transport."""
        artifact = dispatcher.generate(
            graphql_query_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.CLIENT
        assert "httpx" in artifact.dependencies
        # Should NOT require websockets for queries
        assert "websockets" not in artifact.dependencies
    
    def test_subscription_vs_query_semantically_distinct(
        self,
        graphql_query_operation: Operation,
        graphql_subscription_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """Subscriptions and queries must generate different artifact types."""
        query_artifact = dispatcher.generate(
            graphql_query_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        sub_artifact = dispatcher.generate(
            graphql_subscription_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        assert query_artifact.artifact_type == ArtifactType.CLIENT
        assert sub_artifact.artifact_type == ArtifactType.SUBSCRIBER
        assert query_artifact.dependencies != sub_artifact.dependencies


# =============================================================================
# AsyncAPI Direction Semantic Preservation Tests
# =============================================================================

class TestAsyncAPIDirectionSemantics:
    """
    Tests verifying AsyncAPI pub/sub direction semantics are preserved.
    
    CRITICAL per AsyncAPI v2 spec:
    - "publish" channel = Application RECEIVES (inbound)
    - "subscribe" channel = Application SENDS (outbound)
    
    This is counter-intuitive but correct!
    Reference: https://www.asyncapi.com/blog/publish-subscribe-semantics
    """
    
    def test_publish_channel_generates_subscriber(
        self,
        asyncapi_publish_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """
        AsyncAPI 'publish' channel = app RECEIVES = generate subscriber.
        
        When others publish TO the app, the app is a subscriber/consumer.
        """
        artifact = dispatcher.generate(
            asyncapi_publish_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.SUBSCRIBER
        assert artifact.metadata.get("direction") == "inbound"
    
    def test_subscribe_channel_generates_publisher(
        self,
        asyncapi_subscribe_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """
        AsyncAPI 'subscribe' channel = app SENDS = generate publisher.
        
        When others subscribe FROM the app, the app is a publisher.
        """
        artifact = dispatcher.generate(
            asyncapi_subscribe_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.PUBLISHER
        assert artifact.metadata.get("direction") == "outbound"
    
    def test_direction_preserved_in_code_comments(
        self,
        asyncapi_publish_operation: Operation,
        asyncapi_subscribe_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """Generated code must document the correct direction."""
        pub_artifact = dispatcher.generate(
            asyncapi_publish_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        sub_artifact = dispatcher.generate(
            asyncapi_subscribe_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        # Publish channel code should mention "receives" or "inbound"
        assert "RECEIVES" in pub_artifact.code or "INBOUND" in pub_artifact.code
        
        # Subscribe channel code should mention "sends" or "outbound"
        assert "SENDS" in sub_artifact.code or "OUTBOUND" in sub_artifact.code
    
    def test_prompt_includes_direction_warning(
        self,
        asyncapi_publish_operation: Operation,
        codegen_context: CodegenContext,
    ):
        """LLM prompt must include direction semantics to prevent confusion."""
        strategy = AsyncAPICodegenStrategy()
        prompt = strategy.build_prompt(
            asyncapi_publish_operation,
            codegen_context,
            ArtifactType.CLIENT,
            skeleton_code="# skeleton",
        )
        
        # Prompt must explain the counter-intuitive semantics
        assert "publish" in prompt.lower()
        assert "RECEIVES" in prompt or "receives" in prompt
        assert "asyncapi.com" in prompt.lower() or "Reference" in prompt
    
    def test_publish_and_subscribe_are_opposite(
        self,
        asyncapi_publish_operation: Operation,
        asyncapi_subscribe_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """Publish and subscribe channels must produce opposite handlers."""
        pub_artifact = dispatcher.generate(
            asyncapi_publish_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        sub_artifact = dispatcher.generate(
            asyncapi_subscribe_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        # Must be opposite types
        assert pub_artifact.artifact_type != sub_artifact.artifact_type
        
        # Must have opposite directions
        assert pub_artifact.metadata.get("direction") != sub_artifact.metadata.get("direction")


# =============================================================================
# REST Semantic Preservation Tests
# =============================================================================

class TestRESTSemantics:
    """Tests verifying REST semantics are preserved."""
    
    def test_rest_generates_http_client(
        self,
        rest_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """REST operations generate HTTP client code."""
        artifact = dispatcher.generate(
            rest_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.CLIENT
        assert artifact.protocol == ProtocolType.REST
        assert "httpx" in artifact.dependencies
    
    def test_rest_preserves_method_and_path(
        self,
        rest_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """REST codegen preserves HTTP method and path."""
        artifact = dispatcher.generate(
            rest_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        assert artifact is not None
        assert artifact.metadata.get("method") == "GET"
        assert artifact.metadata.get("path") == "/users/{id}"
        # Code should contain the method
        assert "GET" in artifact.code


# =============================================================================
# Dispatcher Tests
# =============================================================================

class TestStrategyDispatcher:
    """Tests for the strategy dispatcher."""
    
    def test_dispatcher_routes_by_protocol(
        self,
        rest_operation: Operation,
        graphql_query_operation: Operation,
        asyncapi_publish_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """Dispatcher routes operations to correct strategy by protocol."""
        rest_artifact = dispatcher.generate(
            rest_operation, codegen_context, ArtifactType.CLIENT
        )
        graphql_artifact = dispatcher.generate(
            graphql_query_operation, codegen_context, ArtifactType.CLIENT
        )
        asyncapi_artifact = dispatcher.generate(
            asyncapi_publish_operation, codegen_context, ArtifactType.CLIENT
        )
        
        assert rest_artifact.protocol == ProtocolType.REST
        assert graphql_artifact.protocol == ProtocolType.GRAPHQL
        assert asyncapi_artifact.protocol == ProtocolType.ASYNCAPI
    
    def test_dispatcher_returns_none_for_unknown_protocol(
        self,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """Dispatcher returns None for unsupported protocols."""
        # Create operation with unsupported protocol
        unknown_op = Operation(
            name="test",
            protocol=ProtocolType.GRPC,  # Not registered
        )
        
        artifact = dispatcher.generate(
            unknown_op, codegen_context, ArtifactType.CLIENT
        )
        
        assert artifact is None
    
    def test_create_default_dispatcher_has_all_strategies(self):
        """Default dispatcher has REST, GraphQL, and AsyncAPI strategies."""
        dispatcher = create_default_dispatcher()
        
        assert ProtocolType.REST in dispatcher.supported_protocols
        assert ProtocolType.GRAPHQL in dispatcher.supported_protocols
        assert ProtocolType.ASYNCAPI in dispatcher.supported_protocols


# =============================================================================
# End-to-End Semantic Tests
# =============================================================================

class TestEndToEndSemanticPreservation:
    """
    End-to-end tests verifying semantics flow from Operation IR to generated code.
    """
    
    def test_graphql_subscription_end_to_end(
        self,
        graphql_subscription_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """
        Full pipeline: GraphQL subscription → WebSocket client with correct protocol.
        
        Verifies:
        1. Artifact type is SUBSCRIBER (not generic CLIENT)
        2. Uses websockets dependency
        3. Uses graphql-transport-ws subprotocol
        4. Code does NOT use HTTP
        """
        artifact = dispatcher.generate(
            graphql_subscription_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        # Type check
        assert artifact.artifact_type == ArtifactType.SUBSCRIBER
        
        # Dependency check
        assert "websockets" in artifact.dependencies
        assert "httpx" not in artifact.dependencies
        
        # Protocol check
        assert "graphql-transport-ws" in artifact.code
        assert artifact.metadata.get("ws_subprotocol") == "graphql-transport-ws"
        
        # Anti-pattern check: no HTTP POST for subscriptions
        assert ".post(" not in artifact.code
    
    def test_asyncapi_direction_end_to_end(
        self,
        asyncapi_publish_operation: Operation,
        asyncapi_subscribe_operation: Operation,
        codegen_context: CodegenContext,
        dispatcher: StrategyDispatcher,
    ):
        """
        Full pipeline: AsyncAPI operations → correct direction handlers.
        
        Verifies the counter-intuitive but correct semantics:
        - "publish" channel → Subscriber (app receives)
        - "subscribe" channel → Publisher (app sends)
        """
        # Generate both
        pub_artifact = dispatcher.generate(
            asyncapi_publish_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        sub_artifact = dispatcher.generate(
            asyncapi_subscribe_operation,
            codegen_context,
            ArtifactType.CLIENT,
        )
        
        # Verify types are OPPOSITE
        assert pub_artifact.artifact_type == ArtifactType.SUBSCRIBER
        assert sub_artifact.artifact_type == ArtifactType.PUBLISHER
        
        # Verify directions are OPPOSITE
        assert pub_artifact.metadata.get("direction") == "inbound"
        assert sub_artifact.metadata.get("direction") == "outbound"
        
        # Verify asyncapi_operation is preserved
        assert pub_artifact.metadata.get("asyncapi_operation") == "publish"
        assert sub_artifact.metadata.get("asyncapi_operation") == "subscribe"
