"""
Solver Agentic Spec Coworker

An agentic co-worker that reads API specs and infers data/API requirements
using LangChain + LangGraph.
"""

__version__ = "0.1.0"

from solver_coworker.graphs.spec_discovery_graph import spec_discovery_graph

__all__ = ["spec_discovery_graph"]
