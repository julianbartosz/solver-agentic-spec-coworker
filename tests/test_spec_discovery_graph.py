"""
Smoke tests for spec_discovery_graph.
"""

from solver_coworker.graphs.spec_discovery_graph import (
    SpecState,
    create_spec_discovery_graph,
    spec_discovery_graph,
)


def test_spec_discovery_graph_exists():
    """Smoke test: Verify spec_discovery_graph is importable and callable."""
    assert spec_discovery_graph is not None
    assert callable(spec_discovery_graph.invoke)


def test_create_spec_discovery_graph():
    """Test that create_spec_discovery_graph returns a compiled graph."""
    graph = create_spec_discovery_graph()
    assert graph is not None


def test_spec_discovery_graph_workflow():
    """Test the complete workflow with sample input."""
    initial_state: SpecState = {
        "spec_content": "Sample API spec content",
        "analysis": "",
        "schema": "",
        "validation_result": "",
        "errors": [],
    }

    result = spec_discovery_graph.invoke(initial_state)

    # Verify all stages completed
    assert "analysis" in result
    assert "schema" in result
    assert "validation_result" in result
    assert result["spec_content"] == "Sample API spec content"
    assert "Analysis of spec:" in result["analysis"]
    assert "Schema derived from:" in result["schema"]
    assert "Validation passed" in result["validation_result"]


def test_spec_discovery_graph_empty_input():
    """Test workflow with empty spec content."""
    initial_state: SpecState = {
        "spec_content": "",
        "analysis": "",
        "schema": "",
        "validation_result": "",
        "errors": [],
    }

    result = spec_discovery_graph.invoke(initial_state)

    # Verify error handling
    assert "errors" in result
    assert len(result["errors"]) > 0
    assert "No spec content provided" in result["errors"]
    assert "Validation failed" in result["validation_result"]


def test_spec_state_typing():
    """Test that SpecState has the correct structure."""
    state: SpecState = {
        "spec_content": "test",
        "analysis": "test",
        "schema": "test",
        "validation_result": "test",
        "errors": [],
    }
    assert isinstance(state["spec_content"], str)
    assert isinstance(state["analysis"], str)
    assert isinstance(state["schema"], str)
    assert isinstance(state["validation_result"], str)
    assert isinstance(state["errors"], list)
