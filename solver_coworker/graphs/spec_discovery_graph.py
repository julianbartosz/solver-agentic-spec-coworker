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


# Factory function for LangGraph configuration
def build_graph() -> StateGraph:
    """
    Factory function to build the spec discovery graph.

    This function is referenced in langgraph.json and used by the LangGraph
    runtime to instantiate the workflow.

    Returns:
        Compiled StateGraph ready for execution
    """
    return create_spec_discovery_graph()


# Create the compiled graph instance
spec_discovery_graph = create_spec_discovery_graph()


# Command-line interface for testing
if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Add parent directory to path to allow imports
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from solver_coworker.config import get_settings

    settings = get_settings()

    # Example initial state
    initial_state: SpecState = {
        "spec_content": (
            "GET /api/users - Returns list of users\n"
            "POST /api/users - Create a new user"
        ),
        "analysis": "",
        "schema": "",
        "validation_result": "",
        "errors": [],
    }

    print("Running spec discovery graph with example data...")
    print(f"API specs directory: {settings.api_specs_dir}")
    print("\nInitial state:")
    print(f"  Spec content: {initial_state['spec_content'][:50]}...")
    print("\nExecuting workflow...")

    result = spec_discovery_graph.invoke(initial_state)

    print("\nWorkflow completed!")
    print(f"  Analysis: {result['analysis']}")
    print(f"  Schema: {result['schema']}")
    print(f"  Validation: {result['validation_result']}")
    if result['errors']:
        print(f"  Errors: {result['errors']}")
