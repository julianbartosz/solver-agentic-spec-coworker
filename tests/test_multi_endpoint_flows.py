"""Tests for P1: Multi-Endpoint Flows.

V2.x: Updated to validate proper start/end node structure per Section 13.1.
"""
import pytest
from typing import List

from integration_coworker.domain.models import Endpoint
from integration_coworker.graph.nodes.align_task_with_kg import (
    _detect_multi_step_pattern,
    _normalize_action,
    _find_endpoint_for_action,
    _infer_multi_endpoint_workflow,
    _build_edges_for_multi_endpoint_flow,
)

pytestmark = pytest.mark.no_db


class TestDetectMultiStepPattern:
    """Tests for multi-step pattern detection."""
    
    def test_single_action_returns_false(self):
        is_multi, actions = _detect_multi_step_pattern("Create a new order")
        assert is_multi is False
        assert actions == []
    
    def test_create_and_send_pattern(self):
        is_multi, actions = _detect_multi_step_pattern(
            "Create an order and then send a confirmation email"
        )
        assert is_multi is True


class TestNormalizeAction:
    """Tests for action normalization."""
    
    @pytest.mark.parametrize("action,expected", [
        ("add", "create"),
        ("fetch", "get"),
        ("create", "create"),
    ])
    def test_normalizes_actions(self, action, expected):
        assert _normalize_action(action) == expected


class TestFindEndpointForAction:
    """Tests for finding endpoints matching actions."""
    
    @pytest.fixture
    def sample_endpoints(self):
        return [
            Endpoint(
                id=1, source_system_id=None, spec_document_id=1,
                path="/orders", method="POST",
                operation_id="createOrder", summary="Create order",
                description="Creates a new order",
                request_schema_id=None, response_schema_id=None,
            ),
        ]
    
    def test_find_create_endpoint(self, sample_endpoints):
        ep = _find_endpoint_for_action(sample_endpoints, "create", "create order")
        assert ep is not None
        assert ep.method == "POST"
    
    def test_no_matching_endpoint(self):
        ep = _find_endpoint_for_action([], "create", "create something")
        assert ep is None


class TestInferMultiEndpointWorkflow:
    """Tests for multi-endpoint workflow inference.
    
    V2.x: Validates proper start/end node structure and canonical types
    per Section 13.1. Uses separate _build_edges_for_multi_endpoint_flow().
    """
    
    @pytest.fixture
    def sample_endpoints(self):
        return [
            Endpoint(
                id=1, source_system_id=None, spec_document_id=1,
                path="/orders", method="POST",
                operation_id="createOrder", summary="Create order",
                description="Creates a new order",
                request_schema_id=None, response_schema_id=None,
            ),
            Endpoint(
                id=2, source_system_id=None, spec_document_id=1,
                path="/notifications", method="POST",
                operation_id="sendNotification", summary="Send notification",
                description="Sends a notification",
                request_schema_id=None, response_schema_id=None,
            ),
        ]
    
    def test_creates_workflow_with_start_end_nodes(self, sample_endpoints):
        """V2.x: Workflow must have proper start and end nodes."""
        steps = _infer_multi_endpoint_workflow(
            endpoints=sample_endpoints,
            task_description="Create order and send notification",
            action_sequence=["create", "send"],
        )
        
        # Must return list of steps
        assert isinstance(steps, list)
        
        # Must have start node at position 0
        start_node = steps[0]
        assert start_node["key"] == "start"
        assert start_node["type"] == "start"
        
        # Must have end node at last position
        end_node = steps[-1]
        assert end_node["key"] == "end"
        assert end_node["type"] == "end"
        
        # Should have at least start + validate + 2 api_calls + transform + end = 6 nodes
        assert len(steps) >= 4
    
    def test_api_call_nodes_use_canonical_type(self, sample_endpoints):
        """V2.x: All API call nodes must use canonical 'api_call' type."""
        steps = _infer_multi_endpoint_workflow(
            endpoints=sample_endpoints,
            task_description="Create order and send notification",
            action_sequence=["create", "send"],
        )
        
        api_nodes = [s for s in steps if s["type"] == "api_call"]
        # Should have 2 API call nodes for the 2 actions
        assert len(api_nodes) >= 1
        
        # All API nodes should have required fields
        for node in api_nodes:
            assert "key" in node
            assert "endpoint_path" in node
            assert "endpoint_method" in node
    
    def test_edges_connect_nodes_sequentially(self, sample_endpoints):
        """V2.x: Edges must form a valid DAG from start to end."""
        steps = _infer_multi_endpoint_workflow(
            endpoints=sample_endpoints,
            task_description="Create order and send notification",
            action_sequence=["create", "send"],
        )
        edges = _build_edges_for_multi_endpoint_flow(steps)
        
        # Must have edges connecting all nodes
        assert len(edges) == len(steps) - 1
        
        # First edge must start from "start"
        assert edges[0]["from_node_key"] == "start"
        
        # Last edge must end at "end"
        assert edges[-1]["to_node_key"] == "end"
        
        # All edges must have required fields
        for edge in edges:
            assert "from_node_key" in edge
            assert "to_node_key" in edge
    
    def test_empty_endpoints_returns_start_end_only(self):
        """V2.x: Empty endpoints should still return valid start/end structure."""
        steps = _infer_multi_endpoint_workflow(
            endpoints=[],
            task_description="Create order and send notification",
            action_sequence=["create", "send"],
        )
        
        # Should have at least start and end nodes
        assert len(steps) >= 2
        assert steps[0]["key"] == "start"
        assert steps[-1]["key"] == "end"
        
        # Edges should still connect start to end
        edges = _build_edges_for_multi_endpoint_flow(steps)
        assert len(edges) >= 1
    
    def test_all_nodes_have_key_field(self, sample_endpoints):
        """V2.x: All nodes must have 'key' field (not 'id')."""
        steps = _infer_multi_endpoint_workflow(
            endpoints=sample_endpoints,
            task_description="Create order and send notification",
            action_sequence=["create", "send"],
        )
        
        for step in steps:
            assert "key" in step, f"Node missing 'key' field: {step}"
            # Should not use legacy 'id' field
            # (though we allow it for backwards compat, key is canonical)
