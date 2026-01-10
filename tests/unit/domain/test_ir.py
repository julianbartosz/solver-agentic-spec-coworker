"""
Tests for Protocol IR models.

Tests the core semantic preservation requirements:
1. GraphQL subscriptions → SUBSCRIBE pattern + WebSocket metadata
2. AsyncAPI channels → PUBLISH/SUBSCRIBE patterns + direction preservation
3. ProtocolType enum includes FILE for data integrations
"""
import pytest

from integration_coworker.domain.ir import (
    Operation,
    ProtocolType,
    CommunicationPattern,
    RestMetadata,
    GraphQLMetadata,
    AsyncAPIMetadata,
    graphql_subscription_to_operation,
    asyncapi_channel_to_operation,
)


class TestProtocolType:
    """Tests for ProtocolType enum."""

    def test_protocol_type_file_exists(self):
        """ProtocolType.FILE exists for data integrations."""
        assert hasattr(ProtocolType, "FILE")
        assert ProtocolType.FILE.value == "file"

    def test_all_protocol_types_present(self):
        """All expected protocol types are defined."""
        expected = {"rest", "graphql", "grpc", "asyncapi", "websocket", "soap", "file"}
        actual = {p.value for p in ProtocolType}
        assert actual == expected

    def test_file_protocol_is_string_enum(self):
        """FILE protocol value can be compared with string."""
        # ProtocolType inherits from str, so == comparison works
        assert ProtocolType.FILE == "file"
        # .value gives the string value
        assert ProtocolType.FILE.value == "file"


class TestCommunicationPatterns:
    """Tests for communication pattern semantics."""

    def test_unary_is_not_streaming(self):
        """UNARY pattern should not be classified as streaming."""
        op = Operation(
            name="getUser",
            protocol=ProtocolType.REST,
            communication_pattern=CommunicationPattern.UNARY,
        )
        assert op.is_streaming() is False
        assert op.is_event_driven() is False

    def test_subscribe_is_streaming(self):
        """SUBSCRIBE pattern should be classified as streaming."""
        op = Operation(
            name="onUserCreated",
            protocol=ProtocolType.GRAPHQL,
            communication_pattern=CommunicationPattern.SUBSCRIBE,
        )
        assert op.is_streaming() is True
        assert op.is_event_driven() is True

    def test_publish_is_event_driven_not_streaming(self):
        """PUBLISH is event-driven but not streaming (fire-and-forget)."""
        op = Operation(
            name="publishEvent",
            protocol=ProtocolType.ASYNCAPI,
            communication_pattern=CommunicationPattern.PUBLISH,
        )
        assert op.is_streaming() is False
        assert op.is_event_driven() is True


class TestGraphQLSubscriptionSemantics:
    """
    Tests for GraphQL subscription semantic preservation.
    
    CRITICAL: Subscriptions require WebSocket transport, NOT HTTP POST.
    The pseudo-OpenAPI conversion loses this semantic.
    """

    def test_subscription_requires_websocket(self):
        """GraphQL subscriptions must have requires_websocket=True."""
        op = graphql_subscription_to_operation(
            field_name="onMessageReceived",
            field_description="New message notification",
            return_type_ref="#/components/schemas/Message",
            source_uri="schema.graphql",
        )
        
        assert op.protocol == ProtocolType.GRAPHQL
        assert op.communication_pattern == CommunicationPattern.SUBSCRIBE
        assert isinstance(op.metadata, GraphQLMetadata)
        assert op.metadata.requires_websocket is True
        assert op.metadata.operation_type == "subscription"

    def test_subscription_has_ws_protocol(self):
        """Subscription should specify WebSocket subprotocol."""
        op = graphql_subscription_to_operation(
            field_name="onOrderUpdated",
            field_description=None,
            return_type_ref=None,
            source_uri="api.graphql",
        )
        
        # Modern GraphQL uses graphql-transport-ws protocol
        assert op.metadata.ws_protocol == "graphql-transport-ws"

    def test_subscription_not_unary(self):
        """
        CRITICAL: Subscription must NOT be UNARY.
        
        This is the semantic loss that happens when GraphQL is
        converted to pseudo-OpenAPI POST endpoints.
        """
        op = graphql_subscription_to_operation(
            field_name="onUserJoined",
            field_description=None,
            return_type_ref=None,
            source_uri="test.graphql",
        )
        
        assert op.communication_pattern != CommunicationPattern.UNARY
        assert op.communication_pattern == CommunicationPattern.SUBSCRIBE

    def test_query_vs_subscription_distinction(self):
        """Query (UNARY) and Subscription (SUBSCRIBE) must be distinct."""
        query = Operation(
            name="getUser",
            protocol=ProtocolType.GRAPHQL,
            communication_pattern=CommunicationPattern.UNARY,
            metadata=GraphQLMetadata(
                operation_type="query",
                field_name="getUser",
                requires_websocket=False,
            ),
        )
        
        subscription = graphql_subscription_to_operation(
            field_name="onUserUpdate",
            field_description=None,
            return_type_ref=None,
            source_uri="test.graphql",
        )
        
        # These must be semantically different
        assert query.communication_pattern != subscription.communication_pattern
        assert query.metadata.requires_websocket != subscription.metadata.requires_websocket


class TestAsyncAPIDirectionality:
    """
    Tests for AsyncAPI pub/sub direction preservation.
    
    CORRECT AsyncAPI semantics (per asyncapi.com/blog/publish-subscribe-semantics):
    - "publish" = app RECEIVES messages (others publish TO it) = SUBSCRIBE pattern
    - "subscribe" = app SENDS messages (to subscribers) = PUBLISH pattern
    
    The pseudo-OpenAPI conversion (POST for publish, GET for subscribe)
    loses this explicit semantic.
    """

    def test_publish_direction_preserved(self):
        """
        Publish operations must preserve 'publish' in metadata.
        
        CORRECT: "publish" = app receives = SUBSCRIBE pattern
        """
        op = asyncapi_channel_to_operation(
            channel_name="user/created",
            operation_type="publish",
            message_schema_ref="#/components/schemas/UserCreatedEvent",
            broker_protocol="kafka",
            source_uri="events.asyncapi.yaml",
        )
        
        assert op.protocol == ProtocolType.ASYNCAPI
        # CORRECT: publish = app receives = SUBSCRIBE pattern
        assert op.communication_pattern == CommunicationPattern.SUBSCRIBE
        assert isinstance(op.metadata, AsyncAPIMetadata)
        assert op.metadata.operation == "publish"  # Original operation preserved!
        assert op.metadata.channel == "user/created"

    def test_subscribe_direction_preserved(self):
        """
        Subscribe operations must preserve 'subscribe' in metadata.
        
        CORRECT: "subscribe" = app sends = PUBLISH pattern
        """
        op = asyncapi_channel_to_operation(
            channel_name="notifications",
            operation_type="subscribe",
            message_schema_ref="#/components/schemas/Notification",
            broker_protocol="amqp",
            source_uri="events.asyncapi.yaml",
        )
        
        # CORRECT: subscribe = app sends = PUBLISH pattern
        assert op.communication_pattern == CommunicationPattern.PUBLISH
        assert op.metadata.operation == "subscribe"  # Original operation preserved!

    def test_publish_vs_subscribe_are_distinct(self):
        """
        CRITICAL: Publish and Subscribe must be semantically distinct.
        
        In pseudo-OpenAPI conversion:
        - publish → POST (sender)
        - subscribe → GET (receiver)
        
        But HTTP method doesn't convey the message direction intent.
        The IR must preserve this.
        """
        publish_op = asyncapi_channel_to_operation(
            channel_name="orders",
            operation_type="publish",
            message_schema_ref=None,
            broker_protocol="kafka",
            source_uri="test.yaml",
        )
        
        subscribe_op = asyncapi_channel_to_operation(
            channel_name="orders",
            operation_type="subscribe",
            message_schema_ref=None,
            broker_protocol="kafka",
            source_uri="test.yaml",
        )
        
        # Same channel, but opposite directions
        assert publish_op.metadata.channel == subscribe_op.metadata.channel
        assert publish_op.metadata.operation != subscribe_op.metadata.operation
        assert publish_op.communication_pattern != subscribe_op.communication_pattern

    def test_broker_protocol_preserved(self):
        """Broker protocol (kafka, amqp, mqtt) must be preserved."""
        for broker in ["kafka", "amqp", "mqtt", "redis"]:
            op = asyncapi_channel_to_operation(
                channel_name="events",
                operation_type="publish",
                message_schema_ref=None,
                broker_protocol=broker,
                source_uri="test.yaml",
            )
            assert op.metadata.broker_protocol == broker

    def test_input_output_schema_direction(self):
        """
        CORRECT AsyncAPI semantics:
        - "publish" = app RECEIVES = output_schema (we receive the message)
        - "subscribe" = app SENDS = input_schema (we provide the message)
        """
        publish_op = asyncapi_channel_to_operation(
            channel_name="events",
            operation_type="publish",
            message_schema_ref="#/components/schemas/Event",
            broker_protocol=None,
            source_uri="test.yaml",
        )
        
        subscribe_op = asyncapi_channel_to_operation(
            channel_name="events",
            operation_type="subscribe",
            message_schema_ref="#/components/schemas/Event",
            broker_protocol=None,
            source_uri="test.yaml",
        )
        
        # CORRECT: publish = receive = output, subscribe = send = input
        assert publish_op.output_schema_ref == "#/components/schemas/Event"
        assert publish_op.input_schema_ref is None
        
        assert subscribe_op.input_schema_ref == "#/components/schemas/Event"
        assert subscribe_op.output_schema_ref is None


class TestConversionQuality:
    """Tests for conversion quality tracking."""

    def test_native_parsing_is_deterministic(self):
        """Operations from native parsing should be 'deterministic'."""
        op = graphql_subscription_to_operation(
            field_name="test",
            field_description=None,
            return_type_ref=None,
            source_uri="test.graphql",
        )
        assert op.conversion_quality == "deterministic"

    def test_lossy_conversion_should_be_best_effort(self):
        """Operations from lossy pseudo-OpenAPI should be 'best_effort'."""
        # Simulating extraction from pseudo-OpenAPI (lossy)
        op = Operation(
            name="onUserCreated",
            protocol=ProtocolType.GRAPHQL,
            communication_pattern=CommunicationPattern.SUBSCRIBE,
            conversion_quality="best_effort",  # Lost semantic fidelity
        )
        assert op.conversion_quality == "best_effort"
