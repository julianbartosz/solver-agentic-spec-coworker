# ADR 0001: Initial Architecture

## Status

Accepted

## Context

We need to build an agentic co-worker that auto-discovers data and API processing requirements from source system specs (public API specs, data transmission guides). The system should be implemented using LangChain and LangGraph and be callable as a runtime workflow for any new data source.

## Decision

We will implement a modular architecture with the following components:

### Core Structure
- **LangGraph Workflow**: A state-based workflow that orchestrates the spec discovery process through four main stages: ingest → analyze → schema → validation
- **Modular Agents**: Specialized agents for each stage of the workflow
- **Reusable Tools**: Common utilities for spec loading, vector storage, knowledge graph, and API interactions

### Technology Stack
- Python 3.11+
- LangChain for agent implementations
- LangGraph for workflow orchestration
- pytest for testing
- OpenAI and Anthropic for LLM capabilities

### Module Organization
- `solver_coworker.graphs`: LangGraph workflow definitions
- `solver_coworker.agents`: Agent implementations for each processing stage
- `solver_coworker.tools`: Shared utilities and tools
- `solver_coworker.config`: Configuration management
- `solver_coworker.logging`: Logging utilities

### Agent Responsibilities
1. **Ingestion Agent**: Loads and parses API specifications from various sources
2. **Analysis Agent**: Extracts endpoints, methods, and payload shapes using LLM
3. **Schema Agent**: Maps analysis output to internal silver/gold schema concepts
4. **Validation Agent**: Validates against live APIs and data quality rules

## Consequences

### Positive
- Clear separation of concerns
- Easy to extend with new agents or tools
- Testable and maintainable
- Follows LangChain/LangGraph best practices

### Negative
- Initial setup complexity
- Requires coordination between multiple components
- Performance overhead from workflow orchestration

## Alternatives Considered

1. **Monolithic approach**: Single module handling all processing - rejected due to maintainability concerns
2. **Direct LLM calls without agents**: Simpler but less flexible and harder to extend
