"""
Smoke tests for the solver_coworker package.

Basic tests to verify that the package and its main components can be imported
and instantiated correctly.
"""



def test_package_imports():
    """Verify that the solver_coworker package imports successfully."""
    import solver_coworker

    assert solver_coworker is not None
    assert hasattr(solver_coworker, "__version__")


def test_spec_discovery_graph_import():
    """Verify that spec_discovery_graph can be imported."""
    from solver_coworker.graphs.spec_discovery_graph import (
        build_graph,
        spec_discovery_graph,
    )

    assert spec_discovery_graph is not None
    assert build_graph is not None


def test_build_graph_returns_graph():
    """Verify that build_graph() returns a graph object."""
    from solver_coworker.graphs.spec_discovery_graph import build_graph

    graph = build_graph()
    assert graph is not None
    assert callable(graph.invoke)


def test_config_module_imports():
    """Verify that config module imports and has expected attributes."""
    from solver_coworker.config import Settings, get_settings, settings

    assert Settings is not None
    assert settings is not None
    assert get_settings() is not None
    assert isinstance(settings, Settings)


def test_logging_module_imports():
    """Verify that logging module imports and get_logger works."""
    from solver_coworker.logging import get_logger

    logger = get_logger(__name__)
    assert logger is not None
    assert hasattr(logger, "info")
    assert hasattr(logger, "error")
    assert hasattr(logger, "warning")


def test_agents_import():
    """Verify that all agent modules can be imported."""
    from solver_coworker.agents import (
        analysis_agent,
        ingestion_agent,
        schema_agent,
        validation_agent,
    )

    assert ingestion_agent is not None
    assert analysis_agent is not None
    assert schema_agent is not None
    assert validation_agent is not None


def test_tools_import():
    """Verify that all tool modules can be imported."""
    from solver_coworker.tools import api_runner, graph_store, spec_loader, vector_store

    assert spec_loader is not None
    assert vector_store is not None
    assert graph_store is not None
    assert api_runner is not None


def test_vector_store_instantiation():
    """Verify that SpecVectorStore can be instantiated."""
    from solver_coworker.tools.vector_store import SpecVectorStore

    store = SpecVectorStore()
    assert store is not None
    assert store.count() == 0


def test_graph_store_instantiation():
    """Verify that GraphStore can be instantiated."""
    from solver_coworker.tools.graph_store import GraphStore

    store = GraphStore()
    assert store is not None
    assert store.count_endpoints() == 0
    assert store.count_relationships() == 0


def test_api_request_instantiation():
    """Verify that APIRequest can be instantiated."""
    from solver_coworker.tools.api_runner import APIRequest

    request = APIRequest(method="GET", url="https://api.example.com/users")
    assert request is not None
    assert request.method == "GET"
    assert request.url == "https://api.example.com/users"
