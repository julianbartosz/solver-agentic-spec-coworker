# solver-agentic-spec-coworker

Agentic co-worker to auto-discover data/API requirements from source specs using LangChain + LangGraph.

## Features

- **LangGraph Workflow**: Implements a `spec_discovery_graph` with the following stages:
  - **Ingest**: Read and parse API specifications
  - **Analyze**: Analyze specifications to identify requirements
  - **Schema**: Extract and generate schema from analysis
  - **Validation**: Validate the generated schema and analysis

- **Modular Architecture**:
  - `solver_coworker.graphs`: LangGraph workflow definitions
  - `solver_coworker.agents`: LangChain agent implementations
  - `solver_coworker.tools`: Agent tools and utilities

## Requirements

- Python 3.11 or higher
- LangChain >= 0.1.0
- LangGraph >= 0.0.20

## Installation

```bash
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

## Usage

```python
from solver_coworker import spec_discovery_graph

# Process an API specification
result = spec_discovery_graph.invoke({
    'spec_content': 'GET /api/users - Returns list of users',
    'analysis': '',
    'schema': '',
    'validation_result': '',
    'errors': []
})

print(result['validation_result'])
```

## Development

### Running Tests

```bash
pytest
```

### Linting

```bash
ruff check solver_coworker tests
```

### Coverage

```bash
pytest --cov=solver_coworker --cov-report=term-missing
```

## CI/CD

GitHub Actions workflow runs tests on Python 3.11 and 3.12:
- Linting with ruff
- Tests with pytest
- Coverage reporting

## License

MIT
