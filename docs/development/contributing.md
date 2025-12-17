# Contributing

Thank you for your interest in contributing to Integration Co-Worker!

## Development Setup

### Prerequisites

- Python 3.11+
- Docker (for PostgreSQL and Redis)
- Git

### Clone and Install

```bash
git clone https://github.com/julianbartosz/solver-agentic-spec-coworker.git
cd solver-agentic-spec-coworker

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dev dependencies
pip install -e ".[dev,postgres,cache,docs]"
```

### Start Services

```bash
docker-compose up -d
```

### Run Tests

```bash
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/ -v
```

## Code Style

### Linting

We use `ruff` for linting:

```bash
ruff check src/
ruff format src/
```

### Type Hints

All public functions should have type hints:

```python
def process_spec(spec_path: str, options: dict | None = None) -> SpecDocument:
    ...
```

### Docstrings

Use Google-style docstrings:

```python
def extract_endpoints(spec: dict) -> list[Endpoint]:
    """Extract API endpoints from an OpenAPI specification.
    
    Args:
        spec: Parsed OpenAPI document as dictionary.
        
    Returns:
        List of Endpoint objects extracted from the spec.
        
    Raises:
        ValueError: If spec is missing required fields.
    """
```

## Project Structure

### Adding a New Node

1. Create file in `src/integration_coworker/graph/nodes/`
2. Implement node function with signature `def node_name(state: WorkflowState) -> WorkflowState`
3. Add to `WORKFLOW_NODE_ORDER` in `runtime.py`
4. Add tests in `tests/`

### Adding a New Feature

1. Create feature branch: `git checkout -b feature/my-feature`
2. Implement with tests
3. Update documentation
4. Submit pull request

## Testing Guidelines

### Test Categories

| Category | Directory | Purpose |
|----------|-----------|---------|
| Unit | `tests/test_*.py` | Single function/class |
| Integration | `tests/test_*_integration.py` | Multiple components |
| End-to-End | `tests/test_end_to_end_*.py` | Full workflow |

### Test Markers

```python
@pytest.mark.no_db
def test_without_database():
    """Tests that don't need database."""
    
@pytest.mark.perf_smoke
def test_performance():
    """Performance tests with timing assertions."""
```

### Running Specific Tests

```bash
# Single test
pytest tests/test_build_silver_api_model.py::test_extract_endpoints -v

# By marker
pytest -m "not no_db" tests/ -v

# With coverage
pytest --cov=integration_coworker tests/
```

## Pull Request Process

1. **Create Branch**: `git checkout -b feature/description`
2. **Make Changes**: Implement feature with tests
3. **Run Tests**: `pytest tests/ -v`
4. **Lint**: `ruff check src/ && ruff format src/`
5. **Commit**: Use conventional commit messages
6. **Push**: `git push origin feature/description`
7. **PR**: Open pull request with description

### Commit Messages

Use conventional commits:

```
feat: add LLM response caching
fix: handle empty spec documents
docs: update CLI reference
test: add parallel execution tests
chore: update dependencies
```

## Documentation

### Building Docs

```bash
pip install -e ".[docs]"
mkdocs serve
```

### Adding Pages

1. Create markdown file in `docs/`
2. Add to `nav` section in `mkdocs.yml`
3. Link from related pages

## Questions?

- Open an issue for bugs or feature requests
- Check existing issues before creating new ones
- Use discussions for questions

---

[Back to Code Tour](code-tour.md) | [Testing →](testing.md)
