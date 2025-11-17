"""
Spec Discovery Graph

A LangGraph workflow that processes API specifications through:
ingest → analyze → schema → validation
"""

from typing import TypedDict

from langgraph.graph import END, StateGraph


class SpecState(TypedDict):
    """State for the spec discovery workflow."""

    spec_content: str
    analysis: str
    schema: str
    validation_result: str
    errors: list[str]


def ingest_node(state: SpecState) -> SpecState:
    """
    Ingest node: Read and parse API specification.

    Args:
        state: Current workflow state

    Returns:
        Updated state with ingested spec content
    """
    spec_content = state.get("spec_content", "")
    if not spec_content:
        state["errors"] = state.get("errors", []) + ["No spec content provided"]
    return state


def analyze_node(state: SpecState) -> SpecState:
    """
    Analyze node: Analyze the ingested specification to identify requirements.

    Args:
        state: Current workflow state

    Returns:
        Updated state with analysis results
    """
    spec_content = state.get("spec_content", "")
    analysis = f"Analysis of spec: {len(spec_content)} characters"
    state["analysis"] = analysis
    return state


def schema_node(state: SpecState) -> SpecState:
    """
    Schema node: Extract and generate schema from analysis.

    Args:
        state: Current workflow state

    Returns:
        Updated state with schema information
    """
    analysis = state.get("analysis", "")
    schema = f"Schema derived from: {analysis}"
    state["schema"] = schema
    return state


def validation_node(state: SpecState) -> SpecState:
    """
    Validation node: Validate the generated schema and analysis.

    Args:
        state: Current workflow state

    Returns:
        Updated state with validation results
    """
    schema = state.get("schema", "")
    errors = state.get("errors", [])

    if errors:
        validation_result = f"Validation failed with {len(errors)} error(s)"
    else:
        validation_result = f"Validation passed for schema: {schema}"

    state["validation_result"] = validation_result
    return state


def create_spec_discovery_graph() -> StateGraph:
    """
    Create the spec discovery workflow graph.

    Returns:
        StateGraph configured with ingest → analyze → schema → validation
    """
    workflow = StateGraph(SpecState)

    # Add nodes
    workflow.add_node("ingest", ingest_node)
    workflow.add_node("analyze", analyze_node)
    workflow.add_node("schema", schema_node)
    workflow.add_node("validation", validation_node)

    # Define edges: ingest → analyze → schema → validation → END
    workflow.set_entry_point("ingest")
    workflow.add_edge("ingest", "analyze")
    workflow.add_edge("analyze", "schema")
    workflow.add_edge("schema", "validation")
    workflow.add_edge("validation", END)

    return workflow.compile()


# Create the compiled graph instance
spec_discovery_graph = create_spec_discovery_graph()
