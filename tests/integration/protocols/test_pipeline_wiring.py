"""
Integration tests for Protocol vNext pipeline wiring.

Verifies that AdapterRegistry.convert() is actually called during
build_silver_api_model and that state.operations is populated.
"""

import pytest
from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.nodes.build_silver_api_model import (
    build_silver_api_model,
    _convert_parsed_specs_to_operations,
)
from integration_coworker.sources.base import ParsedSpec, SourceType
from integration_coworker.domain.ir import ProtocolType, CommunicationPattern


class TestPipelineWiring:
    """Verify adapters are wired into the pipeline."""

    def test_operations_field_exists_on_state(self):
        """State must have operations field."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
        )
        assert hasattr(state, "operations")
        assert isinstance(state.operations, list)

    def test_graphql_spec_produces_operations(self):
        """GraphQL ParsedSpec should produce Operations via adapter."""
        graphql_sdl = """
        type Query {
            user(id: ID!): User
        }
        type User {
            id: ID!
            name: String
        }
        """
        spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="test://schema.graphql",
            data=graphql_sdl,
            metadata={"_parsed_from": "graphql"},  # Must use _parsed_from, not detected_protocol
            raw_content=graphql_sdl,  # Provide raw SDL for native parsing
        )

        operations = _convert_parsed_specs_to_operations([spec])

        assert len(operations) == 1
        op = operations[0]
        assert op.protocol == ProtocolType.GRAPHQL
        assert op.operation_id == "query_user"  # GraphQL adapter prefixes with operation type
        assert op.communication_pattern == CommunicationPattern.UNARY

    def test_rest_spec_produces_operations(self):
        """OpenAPI ParsedSpec should produce Operations via adapter."""
        openapi_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0"},
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "listUsers",
                        "summary": "List all users",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="test://openapi.yaml",
            data=openapi_spec,
        )

        operations = _convert_parsed_specs_to_operations([spec])

        assert len(operations) == 1
        op = operations[0]
        assert op.protocol == ProtocolType.REST
        assert op.operation_id == "listUsers"
        assert op.communication_pattern == CommunicationPattern.UNARY

    def test_asyncapi_spec_produces_operations(self):
        """AsyncAPI ParsedSpec should produce Operations via adapter."""
        asyncapi_spec = {
            "asyncapi": "2.6.0",
            "info": {"title": "Events API", "version": "1.0"},
            "channels": {
                "user/created": {
                    "publish": {
                        "operationId": "userCreated",
                        "message": {"contentType": "application/json"},
                    }
                }
            },
        }
        spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="test://asyncapi.yaml",
            data=asyncapi_spec,
        )

        operations = _convert_parsed_specs_to_operations([spec])

        assert len(operations) == 1
        op = operations[0]
        assert op.protocol == ProtocolType.ASYNCAPI
        assert op.operation_id == "userCreated"
        # publish = app receives = SUBSCRIBE (from app's perspective)
        assert op.communication_pattern == CommunicationPattern.SUBSCRIBE

    def test_build_silver_populates_state_operations(self):
        """End-to-end: build_silver_api_model should populate state.operations."""
        graphql_sdl = """
        type Query {
            health: String
        }
        """
        parsed_spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="test://health.graphql",
            data=graphql_sdl,
            metadata={"_parsed_from": "graphql"},  # Must use _parsed_from
            raw_content=graphql_sdl,
        )

        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
            parsed_specs=[parsed_spec],
        )

        # Call the node
        result = build_silver_api_model(state)

        # Verify operations were populated
        assert len(result.operations) == 1
        assert result.operations[0].operation_id == "query_health"  # GraphQL adapter prefixes
        assert result.operations[0].protocol == ProtocolType.GRAPHQL

    def test_mixed_specs_produce_mixed_operations(self):
        """Multiple protocols should all produce operations."""
        graphql_sdl = "type Query { health: String }"
        graphql_spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="test://schema.graphql",
            data=graphql_sdl,
            metadata={"_parsed_from": "graphql"},
            raw_content=graphql_sdl,
        )
        rest_spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="test://openapi.yaml",
            data={
                "openapi": "3.0.0",
                "info": {"title": "Test", "version": "1.0"},
                "paths": {
                    "/ping": {
                        "get": {
                            "operationId": "ping",
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            },
        )

        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
            parsed_specs=[graphql_spec, rest_spec],
        )

        result = build_silver_api_model(state)

        # Should have 2 operations (1 GraphQL + 1 REST)
        assert len(result.operations) == 2
        protocols = {op.protocol for op in result.operations}
        assert ProtocolType.GRAPHQL in protocols
        assert ProtocolType.REST in protocols

    def test_legacy_dict_specs_still_extract_endpoints(self):
        """Legacy dict specs should still produce endpoints (backward compat)."""
        openapi_dict = {
            "openapi": "3.0.0",
            "info": {"title": "Legacy", "version": "1.0"},
            "paths": {
                "/legacy": {
                    "get": {
                        "operationId": "legacyEndpoint",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
            "_source_uri": "test://legacy.yaml",
        }

        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
            openapi_spec=openapi_dict,  # Legacy path
        )

        result = build_silver_api_model(state)

        # Should still extract endpoints via legacy path
        assert len(result.endpoints) == 1
        assert result.endpoints[0].path == "/legacy"
        # No operations from legacy dict (no ParsedSpec wrapper)
        assert len(result.operations) == 0


class TestAdapterFailureHandling:
    """Verify adapter failures are non-fatal."""

    def test_invalid_spec_logs_warning_continues(self):
        """Invalid spec content should not crash the pipeline."""
        bad_spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="test://bad.txt",
            data="this is not valid anything",
        )
        good_sdl = "type Query { ok: Boolean }"
        good_spec = ParsedSpec(
            source_type=SourceType.API,
            source_uri="test://good.graphql",
            data=good_sdl,
            metadata={"_parsed_from": "graphql"},
            raw_content=good_sdl,
        )

        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
            parsed_specs=[bad_spec, good_spec],
        )

        result = build_silver_api_model(state)

        # Should still have the good operation
        assert len(result.operations) == 1
        assert result.operations[0].operation_id == "query_ok"  # GraphQL adapter prefixes
