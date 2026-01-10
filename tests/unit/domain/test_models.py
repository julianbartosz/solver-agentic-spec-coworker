"""
Tests for domain model constants and types.

Tests NodeType constants and other domain model utilities.
"""
import pytest

from integration_coworker.domain.models import NodeType


class TestNodeType:
    """Tests for NodeType constants."""

    def test_all_node_types_defined(self):
        """All expected node types are defined as constants."""
        assert hasattr(NodeType, "START")
        assert hasattr(NodeType, "END")
        assert hasattr(NodeType, "API_CALL")
        assert hasattr(NodeType, "PARSE_FILE")
        assert hasattr(NodeType, "TRANSFORM")
        assert hasattr(NodeType, "VALIDATION")
        assert hasattr(NodeType, "DECISION")

    def test_node_type_values_are_strings(self):
        """Node type values are lowercase strings."""
        assert NodeType.START == "start"
        assert NodeType.END == "end"
        assert NodeType.API_CALL == "api_call"
        assert NodeType.PARSE_FILE == "parse_file"
        assert NodeType.TRANSFORM == "transform"
        assert NodeType.VALIDATION == "validation"
        assert NodeType.DECISION == "decision"

    def test_parse_file_node_type_for_file_integrations(self):
        """PARSE_FILE node type exists for file-based integrations."""
        assert NodeType.PARSE_FILE == "parse_file"

    def test_node_types_usable_in_comparisons(self):
        """Node types can be used in string comparisons."""
        node_type = "api_call"
        assert node_type == NodeType.API_CALL
        
        node_type = "parse_file"
        assert node_type == NodeType.PARSE_FILE
