"""
Tests for Section 3.10: Plan Integration Flow DAG Validation

Tests the _validate_dag_structure function and infer_provider_code enhancements.
"""
import pytest

from integration_coworker.graph.nodes.plan_integration_flow import _validate_dag_structure
from integration_coworker.graph.nodes.plan_run import infer_provider_code
from integration_coworker.domain.models import IntegrationFlowNode, IntegrationFlowEdge


def make_node(key: str, node_type: str, position: int = 0) -> IntegrationFlowNode:
    """Helper to create test nodes."""
    return IntegrationFlowNode(
        id=None,
        task_id=None,
        node_key=key,
        node_type=node_type,
        position=position,
        config={},
    )


def make_edge(from_key: str, to_key: str) -> IntegrationFlowEdge:
    """Helper to create test edges."""
    return IntegrationFlowEdge(
        id=None,
        task_id=None,
        from_node_key=from_key,
        to_node_key=to_key,
        condition=None,
    )


class TestValidateDagStructure:
    """Tests for _validate_dag_structure function."""

    def test_valid_linear_flow(self):
        """Valid linear flow: start -> api_call -> end."""
        nodes = [
            make_node("start_1", "start", 0),
            make_node("call_1", "api_call", 1),
            make_node("end_1", "end", 2),
        ]
        edges = [
            make_edge("start_1", "call_1"),
            make_edge("call_1", "end_1"),
        ]
        
        is_valid, errors = _validate_dag_structure(nodes, edges)
        
        assert is_valid is True
        assert errors == []

    def test_valid_diamond_flow(self):
        """Valid diamond flow with fork and join."""
        nodes = [
            make_node("start_1", "start", 0),
            make_node("call_a", "api_call", 1),
            make_node("call_b", "api_call", 1),
            make_node("merge", "merge", 2),
            make_node("end_1", "end", 3),
        ]
        edges = [
            make_edge("start_1", "call_a"),
            make_edge("start_1", "call_b"),
            make_edge("call_a", "merge"),
            make_edge("call_b", "merge"),
            make_edge("merge", "end_1"),
        ]
        
        is_valid, errors = _validate_dag_structure(nodes, edges)
        
        assert is_valid is True
        assert errors == []

    def test_valid_multiple_end_nodes(self):
        """Valid flow with multiple end nodes."""
        nodes = [
            make_node("start_1", "start", 0),
            make_node("decision", "decision", 1),
            make_node("end_success", "end", 2),
            make_node("end_failure", "end", 2),
        ]
        edges = [
            make_edge("start_1", "decision"),
            make_edge("decision", "end_success"),
            make_edge("decision", "end_failure"),
        ]
        
        is_valid, errors = _validate_dag_structure(nodes, edges)
        
        assert is_valid is True
        assert errors == []

    def test_no_start_node(self):
        """Should fail when no start node exists."""
        nodes = [
            make_node("call_1", "api_call", 0),
            make_node("end_1", "end", 1),
        ]
        edges = [
            make_edge("call_1", "end_1"),
        ]
        
        is_valid, errors = _validate_dag_structure(nodes, edges)
        
        assert is_valid is False
        assert any("exactly one start node" in e for e in errors)

    def test_multiple_start_nodes(self):
        """Should fail when multiple start nodes exist."""
        nodes = [
            make_node("start_1", "start", 0),
            make_node("start_2", "start", 0),
            make_node("end_1", "end", 1),
        ]
        edges = [
            make_edge("start_1", "end_1"),
            make_edge("start_2", "end_1"),
        ]
        
        is_valid, errors = _validate_dag_structure(nodes, edges)
        
        assert is_valid is False
        assert any("exactly one start node" in e for e in errors)

    def test_no_end_node(self):
        """Should fail when no end node exists."""
        nodes = [
            make_node("start_1", "start", 0),
            make_node("call_1", "api_call", 1),
        ]
        edges = [
            make_edge("start_1", "call_1"),
        ]
        
        is_valid, errors = _validate_dag_structure(nodes, edges)
        
        assert is_valid is False
        assert any("at least one end node" in e for e in errors)

    def test_unreachable_node(self):
        """Should fail when a node is not reachable from start."""
        nodes = [
            make_node("start_1", "start", 0),
            make_node("call_1", "api_call", 1),
            make_node("orphan", "api_call", 1),  # No incoming edge
            make_node("end_1", "end", 2),
        ]
        edges = [
            make_edge("start_1", "call_1"),
            make_edge("call_1", "end_1"),
            make_edge("orphan", "end_1"),  # Orphan connects to end but unreachable
        ]
        
        is_valid, errors = _validate_dag_structure(nodes, edges)
        
        assert is_valid is False
        assert any("not reachable from start" in e for e in errors)

    def test_node_cannot_reach_end(self):
        """Should fail when a node cannot reach any end node."""
        nodes = [
            make_node("start_1", "start", 0),
            make_node("call_1", "api_call", 1),
            make_node("dead_end", "api_call", 1),  # No path to end
            make_node("end_1", "end", 2),
        ]
        edges = [
            make_edge("start_1", "call_1"),
            make_edge("start_1", "dead_end"),  # Goes to dead_end
            make_edge("call_1", "end_1"),
            # dead_end has no outgoing edge to end
        ]
        
        is_valid, errors = _validate_dag_structure(nodes, edges)
        
        assert is_valid is False
        assert any("cannot reach any end node" in e for e in errors)

    def test_cycle_detection(self):
        """Should fail when a cycle exists in the graph."""
        nodes = [
            make_node("start_1", "start", 0),
            make_node("call_a", "api_call", 1),
            make_node("call_b", "api_call", 2),
            make_node("end_1", "end", 3),
        ]
        edges = [
            make_edge("start_1", "call_a"),
            make_edge("call_a", "call_b"),
            make_edge("call_b", "call_a"),  # Cycle: call_a -> call_b -> call_a
            make_edge("call_b", "end_1"),
        ]
        
        is_valid, errors = _validate_dag_structure(nodes, edges)
        
        assert is_valid is False
        assert any("Cycle detected" in e for e in errors)

    def test_self_loop_cycle(self):
        """Should detect self-loop as a cycle."""
        nodes = [
            make_node("start_1", "start", 0),
            make_node("call_1", "api_call", 1),
            make_node("end_1", "end", 2),
        ]
        edges = [
            make_edge("start_1", "call_1"),
            make_edge("call_1", "call_1"),  # Self-loop
            make_edge("call_1", "end_1"),
        ]
        
        is_valid, errors = _validate_dag_structure(nodes, edges)
        
        assert is_valid is False
        assert any("Cycle detected" in e for e in errors)

    def test_empty_nodes(self):
        """Should fail with empty nodes list."""
        is_valid, errors = _validate_dag_structure([], [])
        
        assert is_valid is False
        assert any("No workflow nodes" in e for e in errors)


class TestInferProviderCodePriority:
    """Tests for infer_provider_code priority cascade."""

    def test_priority_1_explicit_override(self):
        """Override takes highest priority."""
        result = infer_provider_code(
            spec_ref="https://api.stripe.com/openapi.yaml",
            parsed_spec={"info": {"x-provider-code": "custom_provider", "title": "Stripe API"}},
            override="my_override",
        )
        assert result == "my_override"

    def test_priority_2_x_provider_code_extension(self):
        """x-provider-code extension beats server URL and title."""
        result = infer_provider_code(
            spec_ref="https://api.stripe.com/openapi.yaml",
            parsed_spec={
                "info": {
                    "x-provider-code": "custom_provider",
                    "title": "Stripe API",
                },
                "servers": [{"url": "https://api.stripe.com/v1"}],
            },
        )
        assert result == "custom_provider"

    def test_priority_2_x_provider_code_normalized(self):
        """x-provider-code gets normalized (lowercase, underscore)."""
        result = infer_provider_code(
            spec_ref="/path/to/spec.yaml",
            parsed_spec={
                "info": {
                    "x-provider-code": "My Custom Provider v2.0",
                },
            },
        )
        assert result == "my_custom_provider_v2_0"

    def test_priority_3_server_url(self):
        """Server URL takes priority over title when no x-provider-code."""
        result = infer_provider_code(
            spec_ref="/path/to/spec.yaml",
            parsed_spec={
                "info": {"title": "Some API"},
                "servers": [{"url": "https://api.github.com/v3"}],
            },
        )
        assert result == "github"

    def test_priority_4_title(self):
        """Title is used when no x-provider-code or servers."""
        result = infer_provider_code(
            spec_ref="/path/to/spec.yaml",
            parsed_spec={
                "info": {"title": "Acme Widget Service"},
            },
        )
        assert result == "acme_widget"

    def test_priority_5_spec_url(self):
        """Spec URL is used when no parsed_spec content helps."""
        result = infer_provider_code(
            spec_ref="https://petstore.swagger.io/v2/swagger.json",
            parsed_spec=None,
        )
        assert result == "petstore"

    def test_priority_6_filepath(self):
        """Filepath is last resort."""
        result = infer_provider_code(
            spec_ref="/local/specs/mock_payments_openapi.yaml",
            parsed_spec={},
        )
        assert result == "mock_payments"

    def test_override_empty_string(self):
        """Empty override should be ignored."""
        result = infer_provider_code(
            spec_ref="/path/to/spec.yaml",
            parsed_spec={"info": {"x-provider-code": "from_extension"}},
            override="",
        )
        # Empty override is falsy, should fall through
        assert result == "from_extension"

    def test_x_provider_code_non_string_ignored(self):
        """Non-string x-provider-code should be ignored."""
        result = infer_provider_code(
            spec_ref="/path/to/spec.yaml",
            parsed_spec={
                "info": {
                    "x-provider-code": 12345,  # Not a string
                    "title": "Fallback Title API",
                },
            },
        )
        assert result == "fallback_title"


class TestMockResponseRemoval:
    """Tests to verify mock response detection was removed."""

    def test_toon_parse_failure_keeps_schema_mappings(self):
        """
        When TOON parsing fails, we should keep schema-based mappings
        instead of checking for 'Mock response' string.
        """
        # This is more of a behavioral test - the mock detection removal
        # means the code path no longer checks for "Mock response" prefix
        # We verify this by checking the code doesn't special-case mock
        import inspect
        from integration_coworker.graph.nodes import plan_integration_flow
        
        source = inspect.getsource(plan_integration_flow)
        
        # Should NOT contain the old mock detection pattern
        assert 'startswith("Mock response")' not in source
        assert 'if "Mock response"' not in source
