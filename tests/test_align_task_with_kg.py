"""
Tests for align_task_with_kg node - workflow template matching.

NOTE: These tests use USE_IN_MEMORY_KG_FALLBACK=1 AND USE_LEGACY_TEMPLATES=1 to test 
against the legacy in-memory templates. Production usage should query the real KG.

M5 Update: As of M5, legacy templates are OFF by default (USE_LEGACY_TEMPLATES=0).
These tests explicitly enable them to verify backwards compatibility.
"""
import os
import pytest

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.nodes.align_task_with_kg import align_task_with_kg
from integration_coworker.domain.models import IntegrationTask


@pytest.fixture(autouse=True)
def enable_kg_fallback(monkeypatch):
    """Enable in-memory KG fallback AND legacy templates for all tests in this module."""
    monkeypatch.setenv("USE_IN_MEMORY_KG_FALLBACK", "1")
    monkeypatch.setenv("USE_LEGACY_TEMPLATES", "1")  # M5: Legacy templates now off by default


def test_get_checkout_session_template_match():
    """
    P2.3: Test that get_checkout_session task matches the new workflow template.
    """
    state = WorkflowState(
        source_refs=[],
        spec_refs=[],
        task_description="Retrieve an existing checkout session by ID",
        provider_code="mock_payments",
        integration_task=IntegrationTask(
            id=None,
            source_system_id=None,
            task_slug="get_checkout_session",
            provider_code="mock_payments",
            description="Retrieve an existing checkout session by ID",
        ),
    )
    
    result = align_task_with_kg(state)
    
    # Should find exactly one matching template
    assert "candidate_templates" in result.plan
    assert len(result.plan["candidate_templates"]) == 1
    
    template = result.plan["candidate_templates"][0]
    assert template["template_id"] == "mock_get_session_v1"
    assert "Get Checkout Session" in template["name"]
    
    # Should build workflow nodes
    assert len(result.workflow_nodes) == 5  # start, validate, call, transform, end
    node_keys = [n.node_key for n in result.workflow_nodes]
    assert node_keys == ["start", "validate_input", "call_get_session", "transform_response", "end"]
    
    # Should build edges
    assert len(result.workflow_edges) == 4  # 4 transitions between 5 nodes
    
    # Should mark step as completed
    assert "align_task_with_kg" in result.completed_steps


def test_create_checkout_session_still_works():
    """
    Regression: Ensure existing create_checkout_session template still matches.
    """
    state = WorkflowState(
        source_refs=[],
        spec_refs=[],
        task_description="Create a new checkout session",
        provider_code="mock_payments",
        integration_task=IntegrationTask(
            id=None,
            source_system_id=None,
            task_slug="create_checkout_session",
            provider_code="mock_payments",
            description="Create a new checkout session",
        ),
    )
    
    result = align_task_with_kg(state)
    
    assert len(result.plan["candidate_templates"]) == 1
    template = result.plan["candidate_templates"][0]
    assert template["template_id"] == "mock_checkout_v1"
    assert len(result.workflow_nodes) == 5


def test_unknown_task_fallback():
    """
    Test that unknown tasks get fallback 4-step workflow.
    """
    state = WorkflowState(
        source_refs=[],
        spec_refs=[],
        task_description="Some unknown task",
        provider_code="mock_payments",
        integration_task=IntegrationTask(
            id=None,
            source_system_id=None,
            task_slug="unknown_operation",
            provider_code="mock_payments",
            description="Some unknown task",
        ),
    )
    
    result = align_task_with_kg(state)
    
    # Should have no matching templates
    assert len(result.plan["candidate_templates"]) == 0
    
    # But should still build fallback workflow
    assert len(result.workflow_nodes) == 4  # start, validate, call, end (no transform)
    node_keys = [n.node_key for n in result.workflow_nodes]
    assert "start" in node_keys
    assert "end" in node_keys
