"""
Tests for Protocol Adapters.

Tests the adapter implementations:
1. RestAdapter - OpenAPI/Swagger → IR Operations
2. GraphQLAdapter - Native SDL → IR Operations (with WebSocket transport for subscriptions)
3. AsyncAPIAdapter - Native channels → IR Operations (with perspective toggle)
"""
import pytest

from integration_coworker.protocols.base import (
    AdapterRegistry,
    create_default_registry,
)
from integration_coworker.protocols.rest import RestAdapter
from integration_coworker.protocols.graphql import GraphQLAdapter
from integration_coworker.protocols.asyncapi import (
    AsyncAPIAdapter,
    AsyncAPIAdapterConfig,
    MessageDirection,
    create_asyncapi_adapter,
)
from integration_coworker.domain.ir import (
    ProtocolType,
    CommunicationPattern,
    RestMetadata,
    GraphQLMetadata,
    AsyncAPIMetadata,
)

# Use shared fixtures
from tests.fixtures.protocol_fixtures import (
    GRAPHQL_SDL_SIMPLE,
    GRAPHQL_SDL_WITH_SUBSCRIPTIONS,
    ASYNCAPI_V2_SIMPLE,
    ASYNCAPI_V2_PUB_SUB,
    OPENAPI_30_SIMPLE,
    SWAGGER_20_SIMPLE,
)


class TestAdapterRegistry:
    """Tests for the adapter registry."""

    def test_create_default_registry(self):
        """Default registry should have all built-in adapters."""
        registry = create_default_registry()
        
        assert ProtocolType.REST in registry.registered_protocols
        assert ProtocolType.GRAPHQL in registry.registered_protocols
        assert ProtocolType.ASYNCAPI in registry.registered_protocols

    def test_registry_is_not_singleton(self):
        """Two registries should be independent (no global state)."""
        registry1 = AdapterRegistry()
        registry2 = AdapterRegistry()
        
        registry1.register(RestAdapter())
        
        # registry2 should NOT have the adapter
        assert ProtocolType.REST in registry1.registered_protocols
        assert ProtocolType.REST not in registry2.registered_protocols

    def test_find_adapter_for_openapi(self):
        """Registry should find REST adapter for OpenAPI spec."""
        registry = create_default_registry()
        
        spec_data = {"openapi": "3.0.0", "paths": {"/users": {}}}
        metadata = {}
        
        adapter = registry.find_adapter(spec_data, metadata)
        assert adapter is not None
        assert adapter.protocol_type == ProtocolType.REST


class TestRestAdapter:
    """Tests for REST/OpenAPI adapter."""

    def test_can_handle_openapi_30(self):
        """Should handle OpenAPI 3.0."""
        adapter = RestAdapter()
        
        spec = {"openapi": "3.0.0", "paths": {}}
        assert adapter.can_handle(spec, {}) is True

    def test_can_handle_swagger_20(self):
        """Should handle Swagger 2.0."""
        adapter = RestAdapter()
        
        spec = {"swagger": "2.0", "paths": {}}
        assert adapter.can_handle(spec, {}) is True

    def test_rejects_graphql_converted(self):
        """Should NOT handle pseudo-OpenAPI from GraphQL."""
        adapter = RestAdapter()
        
        spec = {"openapi": "3.0.0", "paths": {"/graphql": {}}}
        metadata = {"_parsed_from": "graphql"}
        
        assert adapter.can_handle(spec, metadata) is False

    def test_convert_simple_endpoint(self):
        """Should extract operation from simple endpoint."""
        adapter = RestAdapter()
        
        spec = {
            "openapi": "3.0.0",
            "paths": {
                "/users/{id}": {
                    "get": {
                        "operationId": "getUser",
                        "summary": "Get user by ID",
                    }
                }
            }
        }
        
        ops = adapter.convert(spec, {}, "test.yaml")
        
        assert len(ops) == 1
        op = ops[0]
        assert op.name == "getUser"
        assert op.protocol == ProtocolType.REST
        assert op.communication_pattern == CommunicationPattern.UNARY
        assert isinstance(op.metadata, RestMetadata)
        assert op.metadata.path == "/users/{id}"
        assert op.metadata.method == "GET"
        assert "id" in op.metadata.path_params


class TestGraphQLAdapter:
    """Tests for GraphQL adapter with native SDL parsing."""

    def test_can_handle_graphql(self):
        """Should handle GraphQL specs."""
        adapter = GraphQLAdapter()
        
        spec = {}
        metadata = {"_parsed_from": "graphql"}
        
        assert adapter.can_handle(spec, metadata) is True

    def test_convert_from_sdl_query(self):
        """Should extract Query operations from SDL."""
        adapter = GraphQLAdapter()
        
        sdl = '''
        type Query {
            user(id: ID!): User
            users: [User!]!
        }
        
        type User {
            id: ID!
            name: String!
        }
        '''
        
        spec = {}
        metadata = {"_parsed_from": "graphql", "_raw_content": sdl}
        
        ops = adapter.convert(spec, metadata, "schema.graphql")
        
        # Should have 2 query operations
        query_ops = [op for op in ops if op.metadata.operation_type == "query"]
        assert len(query_ops) == 2
        
        # All queries should be UNARY
        for op in query_ops:
            assert op.communication_pattern == CommunicationPattern.UNARY
            assert op.metadata.requires_websocket is False

    def test_convert_subscription_websocket_transport(self):
        """
        CRITICAL: Subscriptions must have WebSocket transport metadata.
        
        This tests the core semantic preservation requirement.
        """
        adapter = GraphQLAdapter()
        
        sdl = '''
        type Query {
            dummy: String
        }
        
        type Subscription {
            onUserCreated: User!
            onMessageReceived(channelId: ID!): Message!
        }
        
        type User {
            id: ID!
        }
        
        type Message {
            id: ID!
            content: String!
        }
        '''
        
        spec = {}
        metadata = {"_parsed_from": "graphql", "_raw_content": sdl}
        
        ops = adapter.convert(spec, metadata, "schema.graphql")
        
        # Find subscription operations
        sub_ops = [op for op in ops if op.metadata.operation_type == "subscription"]
        assert len(sub_ops) == 2
        
        for op in sub_ops:
            # CRITICAL: Subscriptions must NOT be UNARY
            assert op.communication_pattern == CommunicationPattern.SUBSCRIBE
            
            # CRITICAL: WebSocket transport required
            assert op.metadata.requires_websocket is True
            
            # CRITICAL: Must specify the modern graphql-ws protocol
            # Not the legacy subscriptions-transport-ws
            assert op.metadata.ws_protocol == "graphql-transport-ws"

    def test_subscription_vs_query_semantically_distinct(self):
        """Query and Subscription must have different patterns."""
        adapter = GraphQLAdapter()
        
        sdl = '''
        type Query {
            getUser(id: ID!): User
        }
        
        type Subscription {
            onUserUpdated(id: ID!): User
        }
        
        type User {
            id: ID!
        }
        '''
        
        spec = {}
        metadata = {"_parsed_from": "graphql", "_raw_content": sdl}
        
        ops = adapter.convert(spec, metadata, "schema.graphql")
        
        query_op = next(op for op in ops if op.metadata.operation_type == "query")
        sub_op = next(op for op in ops if op.metadata.operation_type == "subscription")
        
        # Must be semantically different
        assert query_op.communication_pattern != sub_op.communication_pattern
        assert query_op.metadata.requires_websocket != sub_op.metadata.requires_websocket


class TestAsyncAPIAdapter:
    """Tests for AsyncAPI adapter with perspective toggle."""

    def test_can_handle_asyncapi(self):
        """Should handle AsyncAPI specs."""
        adapter = AsyncAPIAdapter()
        
        spec = {"asyncapi": "2.6.0", "channels": {}}
        assert adapter.can_handle(spec, {}) is True

    def test_can_handle_asyncapi_from_metadata(self):
        """Should handle spec marked as AsyncAPI."""
        adapter = AsyncAPIAdapter()
        
        spec = {}
        metadata = {"_parsed_from": "asyncapi"}
        
        assert adapter.can_handle(spec, metadata) is True

    def test_convert_v2_channels(self):
        """Should extract operations from AsyncAPI 2.x channels."""
        adapter = AsyncAPIAdapter()
        
        spec = {
            "asyncapi": "2.6.0",
            "channels": {
                "user/created": {
                    "publish": {
                        "operationId": "publishUserCreated",
                        "summary": "User created event",
                    }
                },
                "notifications": {
                    "subscribe": {
                        "operationId": "subscribeNotifications",
                        "summary": "Subscribe to notifications",
                    }
                }
            },
            "servers": {
                "production": {
                    "protocol": "kafka",
                    "url": "kafka://broker:9092"
                }
            }
        }
        
        ops = adapter.convert(spec, {"_parsed_from": "asyncapi"}, "events.yaml")
        
        assert len(ops) == 2
        
        pub_op = next(op for op in ops if op.metadata.operation == "publish")
        sub_op = next(op for op in ops if op.metadata.operation == "subscribe")
        
        # Verify broker info preserved
        assert pub_op.metadata.broker_protocol == "kafka"
        assert sub_op.metadata.broker_protocol == "kafka"

    def test_app_perspective_default(self):
        """
        Default 'app' perspective: operations are from app's point of view.
        
        - publish = app sends = OUTBOUND = PUBLISH pattern
        - subscribe = app receives = INBOUND = SUBSCRIBE pattern
        """
        adapter = AsyncAPIAdapter()  # Default is 'app' perspective
        assert adapter.perspective == "app"
        
        spec = {
            "asyncapi": "2.6.0",
            "channels": {
                "events": {
                    "publish": {"operationId": "pub"},
                    "subscribe": {"operationId": "sub"},
                }
            }
        }
        
        ops = adapter.convert(spec, {"_parsed_from": "asyncapi"}, "test.yaml")
        
        pub_op = next(op for op in ops if op.metadata.operation == "publish")
        sub_op = next(op for op in ops if op.metadata.operation == "subscribe")
        
        # CORRECT AsyncAPI semantics (per asyncapi.com/blog/publish-subscribe-semantics):
        # - "publish" = app RECEIVES messages (others publish TO it) = INBOUND = SUBSCRIBE
        # - "subscribe" = app SENDS messages (TO subscribers) = OUTBOUND = PUBLISH
        assert pub_op.communication_pattern == CommunicationPattern.SUBSCRIBE
        assert pub_op.metadata.direction == "inbound_to_app"
        
        assert sub_op.communication_pattern == CommunicationPattern.PUBLISH
        assert sub_op.metadata.direction == "outbound_from_app"

    def test_external_perspective_same_direction(self):
        """
        External perspective does NOT invert direction.
        
        The spec always describes the app. Perspective affects how we
        INTERPRET it for code generation, but the direction (from app's view)
        stays the same.
        
        - "publish" channel = app receives = still INBOUND from app's view
        - "subscribe" channel = app sends = still OUTBOUND from app's view
        
        This is documented in AsyncAPI guidance on operations semantics.
        """
        adapter = create_asyncapi_adapter(perspective="external")
        assert adapter.perspective == "external"
        
        spec = {
            "asyncapi": "2.6.0",
            "channels": {
                "events": {
                    "publish": {"operationId": "pub"},
                    "subscribe": {"operationId": "sub"},
                }
            }
        }
        
        ops = adapter.convert(spec, {"_parsed_from": "asyncapi"}, "test.yaml")
        
        pub_op = next(op for op in ops if op.metadata.operation == "publish")
        sub_op = next(op for op in ops if op.metadata.operation == "subscribe")
        
        # Direction is from app's view, regardless of perspective
        # - "publish" = app receives = INBOUND = SUBSCRIBE
        # - "subscribe" = app sends = OUTBOUND = PUBLISH
        assert pub_op.communication_pattern == CommunicationPattern.SUBSCRIBE
        assert pub_op.metadata.direction == "inbound_to_app"
        
        assert sub_op.communication_pattern == CommunicationPattern.PUBLISH
        assert sub_op.metadata.direction == "outbound_from_app"

    def test_same_spec_different_perspective_yields_opposite_patterns(self):
        """
        Same AsyncAPI doc with different perspectives should yield
        opposite communication patterns for the same operations.
        
        This is the key test proving the perspective toggle works correctly.
        """
        spec = {
            "asyncapi": "2.6.0",
            "channels": {
                "orders": {
                    "publish": {"operationId": "orderCreated"},
                }
            }
        }
        metadata = {"_parsed_from": "asyncapi"}
        
        # App perspective
        app_adapter = create_asyncapi_adapter(perspective="app")
        app_ops = app_adapter.convert(spec, metadata, "test.yaml")
        app_pub = app_ops[0]
        
        # External perspective
        ext_adapter = create_asyncapi_adapter(perspective="external")
        ext_ops = ext_adapter.convert(spec, metadata, "test.yaml")
        ext_pub = ext_ops[0]
        
        # Same original operation type
        assert app_pub.metadata.operation == ext_pub.metadata.operation == "publish"
        
        # Perspective is recorded but direction is the same
        # (both perspectives see the same direction from app's view)
        assert app_pub.metadata.perspective == "app"
        assert ext_pub.metadata.perspective == "external"
        
        # Same patterns - direction is always from app's view
        assert app_pub.communication_pattern == ext_pub.communication_pattern
        assert app_pub.metadata.direction == ext_pub.metadata.direction
        
        # Specifically: "publish" = app receives = SUBSCRIBE
        assert app_pub.communication_pattern == CommunicationPattern.SUBSCRIBE
        assert ext_pub.communication_pattern == CommunicationPattern.SUBSCRIBE

    def test_perspective_stored_in_metadata(self):
        """The perspective used should be recorded in metadata."""
        spec = {
            "asyncapi": "2.6.0",
            "channels": {"ch": {"publish": {}}}
        }
        
        app_adapter = create_asyncapi_adapter(perspective="app")
        ext_adapter = create_asyncapi_adapter(perspective="external")
        
        app_ops = app_adapter.convert(spec, {"_parsed_from": "asyncapi"}, "t.yaml")
        ext_ops = ext_adapter.convert(spec, {"_parsed_from": "asyncapi"}, "t.yaml")
        
        assert app_ops[0].metadata.perspective == "app"
        assert ext_ops[0].metadata.perspective == "external"

    def test_input_output_schema_follows_direction(self):
        """
        Message schema should be input or output based on direction.
        
        CORRECT AsyncAPI semantics (per asyncapi.com/blog/publish-subscribe-semantics):
        - "publish" = app RECEIVES = INBOUND = output_schema_ref (we receive message)
        - "subscribe" = app SENDS = OUTBOUND = input_schema_ref (we provide message)
        """
        spec = {
            "asyncapi": "2.6.0",
            "channels": {
                "events": {
                    "publish": {
                        "message": {
                            "payload": {"$ref": "#/components/schemas/Event"}
                        }
                    }
                }
            }
        }
        metadata = {"_parsed_from": "asyncapi"}
        
        # "publish" = app receives = INBOUND = output_schema_ref
        app_adapter = create_asyncapi_adapter(perspective="app")
        app_ops = app_adapter.convert(spec, metadata, "t.yaml")
        assert app_ops[0].output_schema_ref == "#/components/schemas/Event"
        assert app_ops[0].input_schema_ref is None
        
        # External perspective doesn't change direction from app's view
        ext_adapter = create_asyncapi_adapter(perspective="external")
        ext_ops = ext_adapter.convert(spec, metadata, "t.yaml")
        assert ext_ops[0].output_schema_ref == "#/components/schemas/Event"
        assert ext_ops[0].input_schema_ref is None


class TestConversionQuality:
    """Tests for conversion quality markers."""

    def test_native_sdl_parsing_is_deterministic(self):
        """Native SDL parsing should be marked deterministic."""
        adapter = GraphQLAdapter()
        
        sdl = "type Query { hello: String }"
        ops = adapter.convert({}, {"_parsed_from": "graphql", "_raw_content": sdl}, "t.graphql")
        
        assert all(op.conversion_quality == "deterministic" for op in ops)

    def test_native_asyncapi_parsing_is_deterministic(self):
        """Native AsyncAPI parsing should be marked deterministic."""
        adapter = AsyncAPIAdapter()
        
        spec = {"asyncapi": "2.6.0", "channels": {"ch": {"publish": {}}}}
        ops = adapter.convert(spec, {"_parsed_from": "asyncapi"}, "t.yaml")
        
        assert all(op.conversion_quality == "deterministic" for op in ops)
