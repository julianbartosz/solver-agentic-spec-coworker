"""Integration tests for Protocol vNext wiring.

Verifies that GraphQL and AsyncAPI specs produce Operations
through the build_silver_api_model node.
"""
import pytest
from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.nodes.build_silver_api_model import build_silver_api_model
from integration_coworker.domain.ir import ProtocolType, CommunicationPattern
from integration_coworker.sources.base import ParsedSpec, SourceType

# Use shared fixtures
from tests.fixtures.protocol_fixtures import (
    GRAPHQL_SDL_FULL as GRAPHQL_SDL,
    ASYNCAPI_YAML_SIMPLE as ASYNCAPI_YAML,
    OPENAPI_30_SIMPLE,
)


def make_state(**kwargs) -> WorkflowState:
    """Factory for test WorkflowState with required fields."""
    defaults = {
        "source_refs": [],
        "spec_refs": [],
        "task_description": "Test task",
        "provider_code": "test_provider",
    }
    defaults.update(kwargs)
    return WorkflowState(**defaults)


class TestOperationsWiring:
    """Test that adapters are wired into the pipeline."""

    def test_graphql_spec_produces_operations(self):
        """GraphQL SDL creates Operation objects in state.operations."""
        # GraphQL adapter detects via _parsed_from="graphql" in metadata  
        # or by SDL content in _raw_content
        spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="graphql://test/schema.graphql",
            data={},  # Empty dict - adapter uses raw_content for native parsing
            metadata={"_parsed_from": "graphql"},
            raw_content=GRAPHQL_SDL.encode("utf-8"),
        )
        
        state = make_state(openapi_spec=spec)
        result = build_silver_api_model(state)
        
        # Should have operations from GraphQL adapter
        assert len(result.operations) > 0, "No operations produced from GraphQL spec"
        
        # Check protocol types
        protocols = {op.protocol for op in result.operations}
        assert ProtocolType.GRAPHQL in protocols
        
        # Check communication patterns (queries/mutations use UNARY, subscriptions use SUBSCRIBE)
        patterns = {op.communication_pattern for op in result.operations}
        assert CommunicationPattern.UNARY in patterns  # Queries and mutations
        assert CommunicationPattern.SUBSCRIBE in patterns  # Subscriptions

    def test_asyncapi_spec_produces_operations(self):
        """AsyncAPI YAML creates Operation objects in state.operations."""
        # AsyncAPI adapter expects parsed data with channels in spec_data
        asyncapi_data = {
            "asyncapi": "2.6.0",
            "info": {"title": "User Events", "version": "1.0.0"},
            "channels": {
                "user/created": {
                    "publish": {
                        "operationId": "onUserCreated",
                        "message": {
                            "payload": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "name": {"type": "string"},
                                }
                            }
                        }
                    }
                }
            }
        }
        spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="asyncapi://test/events.yaml",
            data=asyncapi_data,
            metadata={"_parsed_from": "asyncapi"},
            raw_content=ASYNCAPI_YAML.encode("utf-8"),
        )
        
        state = make_state(openapi_spec=spec)
        result = build_silver_api_model(state)
        
        # Should have operations from AsyncAPI adapter
        assert len(result.operations) > 0, "No operations produced from AsyncAPI spec"
        
        # Check protocol type
        protocols = {op.protocol for op in result.operations}
        assert ProtocolType.ASYNCAPI in protocols

    def test_openapi_spec_produces_operations(self):
        """OpenAPI spec creates Operation objects with REST protocol type."""
        openapi_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "getUsers",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="openapi://test/api.yaml",
            data=openapi_spec,
        )
        
        state = make_state(openapi_spec=spec)
        result = build_silver_api_model(state)
        
        # Should have operations
        assert len(result.operations) > 0, "No operations produced from OpenAPI spec"
        
        # Check protocol type
        protocols = {op.protocol for op in result.operations}
        assert ProtocolType.REST in protocols
        
        # Check communication pattern - REST uses UNARY for request-response
        patterns = {op.communication_pattern for op in result.operations}
        assert CommunicationPattern.UNARY in patterns

    def test_operations_have_required_fields(self):
        """Operations have all required semantic fields populated."""
        spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="graphql://test/schema.graphql",
            data={"schema": GRAPHQL_SDL.strip()},
            raw_content=GRAPHQL_SDL.encode("utf-8"),
        )
        
        state = make_state(openapi_spec=spec)
        result = build_silver_api_model(state)
        
        for op in result.operations:
            # Required fields
            assert op.name, f"Operation missing name: {op}"
            assert op.protocol is not None, f"Operation missing protocol: {op}"
            assert op.communication_pattern is not None, f"Operation missing pattern: {op}"
            # Source tracking
            assert op.source_uri, f"Operation missing source_uri: {op}"

    def test_legacy_dict_specs_still_work(self):
        """Legacy dict specs without ParsedSpec wrapper still work."""
        legacy_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Legacy API", "version": "1.0.0"},
            "paths": {
                "/legacy": {
                    "post": {
                        "operationId": "legacyEndpoint",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        
        state = make_state(openapi_spec=legacy_spec)
        result = build_silver_api_model(state)
        
        # Legacy extraction should still work - endpoints are extracted
        assert len(result.endpoints) > 0 or result.openapi_spec is None
        # But no Operations for legacy dicts (only ParsedSpec)
        # This is intentional - Protocol vNext requires ParsedSpec


class TestEdgeCases:
    """Edge cases for protocol wiring."""

    def test_empty_graphql_schema_graceful(self):
        """Empty GraphQL schema doesn't crash."""
        spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="graphql://test/empty.graphql",
            data={"schema": ""},
            raw_content=b"",
        )
        
        state = make_state(openapi_spec=spec)
        
        # Should not raise
        result = build_silver_api_model(state)
        # May have 0 operations or warning, but no crash
        assert result is not None

    def test_unknown_source_type_graceful(self):
        """Unknown source type doesn't crash."""
        spec = ParsedSpec(
            source_type=SourceType.UNKNOWN,
            source_uri="unknown://test/spec.xyz",
            data={"foo": "bar"},
        )
        
        state = make_state(openapi_spec=spec)
        
        # Should not raise
        result = build_silver_api_model(state)
        assert result is not None
