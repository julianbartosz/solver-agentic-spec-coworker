# Testing Guide

Comprehensive guide to testing Integration Co-Worker.

## Quick Start

```bash
# Run all tests with mock LLM and SQLite
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/ -v

# Run with coverage
pytest --cov=integration_coworker --cov-report=html tests/
```

## Test Configuration

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `USE_SQLITE` | Use SQLite instead of PostgreSQL | `false` |
| `USE_MOCK_LLM` | Use mock LLM responses | `false` |
| `DATABASE_URL` | PostgreSQL connection string | - |

### pytest.ini Settings

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
markers =
    no_db: Tests that don't require database
    perf_smoke: Performance smoke tests
```

## Test Categories

### Unit Tests

Test individual functions and classes:

```python
# tests/test_build_silver_api_model.py
def test_extract_endpoints():
    spec = {"paths": {"/users": {"get": {"operationId": "getUsers"}}}}
    endpoints = extract_endpoints(spec)
    assert len(endpoints) == 1
    assert endpoints[0].path == "/users"
```

### Integration Tests

Test component interactions:

```python
# tests/test_end_to_end_integration.py
def test_end_to_end_dry_run():
    result = design_and_generate_integration(
        spec_refs=["tests/fixtures/petstore.yaml"],
        task_description="List pets",
        options=IntegrationOptions(dry_run=True)
    )
    assert result.status == "SUCCESS"
    assert len(result.artifacts) == 3
```

### Performance Tests

Test timing constraints:

```python
@pytest.mark.perf_smoke
def test_large_spec_performance():
    start = time.time()
    result = process_large_spec()
    elapsed = time.time() - start
    assert elapsed < 30.0, f"Too slow: {elapsed}s"
```

## Test Fixtures

### Shared Fixtures

```python
# tests/conftest.py
@pytest.fixture
def sample_spec():
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {}
    }

@pytest.fixture
def mock_state(sample_spec):
    return WorkflowState(
        spec_refs=["test.yaml"],
        task_description="Test task",
        openapi_spec=sample_spec
    )
```

### Database Fixtures

```python
@pytest.fixture
def db_session():
    # Setup
    session = get_test_session()
    yield session
    # Teardown
    session.rollback()
    session.close()
```

## Testing Specific Features

### LLM Cache Tests

```python
# tests/test_llm_cache.py
def test_cache_hit():
    cache = get_llm_cache()
    cache.set("openai", "gpt-4", "test", "hash123", "response")
    
    result = cache.get("openai", "gpt-4", "test", "hash123")
    assert result == "response"
```

### Parallel Execution Tests

```python
# tests/test_parallel_execution.py
def test_parallel_graph_structure():
    graph = build_parallel_graph()
    # Verify parallel branches exist
    assert "embed_spec_chunks" in graph.nodes
    assert "understand_task" in graph.nodes
    assert "sync_embed_task" in graph.nodes
```

### Node Tests

```python
# tests/test_understand_task.py
def test_understand_task_normalizes_slug():
    state = WorkflowState(
        spec_refs=["test.yaml"],
        task_description="Create a NEW user!!!"
    )
    result = understand_task(state)
    assert result.integration_task.task_slug == "create_new_user"
```

## Mocking

### Mock LLM Client

```python
from unittest.mock import patch

def test_with_mock_llm():
    with patch("integration_coworker.llm.client.get_llm_client") as mock:
        mock.return_value.complete.return_value = "mocked response"
        result = run_llm_task()
        assert result == "mocked response"
```

### Mock Database

```python
def test_without_db(monkeypatch):
    monkeypatch.setenv("USE_SQLITE", "true")
    # Test runs with SQLite in-memory
```

## Running Tests

### All Tests

```bash
pytest tests/ -v
```

### Specific File

```bash
pytest tests/test_build_silver_api_model.py -v
```

### Specific Test

```bash
pytest tests/test_build_silver_api_model.py::test_extract_endpoints -v
```

### By Marker

```bash
# Skip database tests
pytest -m "not no_db" tests/ -v

# Only performance tests
pytest -m perf_smoke tests/ -v
```

### With Coverage

```bash
pytest --cov=integration_coworker --cov-report=html tests/
open htmlcov/index.html
```

## Debugging Tests

### Verbose Output

```bash
pytest tests/test_failing.py -v --tb=long
```

### Drop into Debugger

```bash
pytest tests/test_failing.py --pdb
```

### Print Statements

```python
def test_with_output(capsys):
    print("Debug info")
    result = some_function()
    captured = capsys.readouterr()
    print(captured.out)
```

## CI/CD Integration

Tests run automatically on:

- Push to `main`
- Pull requests
- Manual workflow dispatch

```yaml
# .github/workflows/test.yml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]"
      - run: pytest tests/ -v
```

---

[Back to Contributing](contributing.md) | [Changelog →](changelog.md)
