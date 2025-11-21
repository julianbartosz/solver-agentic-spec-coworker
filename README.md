# solver-agentic-spec-coworker

Agentic co-worker to auto-discover data/API requirements from source specs using LangChain + LangGraph.

## Phase 2 Demo (Current Milestone)

The Phase 2 vertical slice demonstrates a complete end-to-end workflow for the **mock_payments** provider with the **"Create checkout session"** task.

### Running the Demo

```bash
PYTHONPATH=src python -m integration_coworker.cli \
  --spec-ref tests/fixtures/mock_payments_openapi.yaml \
  --task "Create checkout session" \
  --dry-run
```

**Optional: With repository integration:**

```bash
PYTHONPATH=src python -m integration_coworker.cli \
  --spec-ref tests/fixtures/mock_payments_openapi.yaml \
  --task "Create checkout session" \
  --repo-root /path/to/your/fastapi-repo \
  --repo-profile subatomic_mock
```

### What to Expect

After running the demo, you will see:

- **Comprehensive Markdown Report** including:
  - Spec ingestion details (1 document, 5 chunks)
  - Silver model extraction (2 endpoints, schemas, fields)
  - Integration workflow (4 nodes: validate → call → transform → return)
  - Policy summary (5 policies: AUTH, RETRY, LOGGING, IDEMPOTENCY, RATE_LIMIT)
  - Generated code artifacts (3 files: client, flow, test)

- **Code Artifacts Generated**:
  - `integrations/clients/mock_payments.py` - MockPaymentsClient class
  - `integrations/flows/mock_payments_checkout.py` - Checkout flow orchestration
  - `tests/integrations/test_mock_payments_checkout.py` - pytest tests

- **Repository Changes**:
  - In `--dry-run` mode: Summary of what would be created/updated
  - Without `--dry-run`: Actual files written to repo with proper directory structure

- **Exit Status**:
  - `0` on success (all nodes completed, no critical errors)
  - Non-zero on failure (spec not found, parsing errors, validation failures)

### JSON Output Mode

For automation and CI integration:

```bash
PYTHONPATH=src python -m integration_coworker.cli \
  --spec-ref tests/fixtures/mock_payments_openapi.yaml \
  --task "Create checkout session" \
  --dry-run \
  --json-output
```

This outputs a valid JSON `IntegrationResult` to stdout with all debug logs suppressed or sent to stderr.

### Testing the Vertical Slice

Run the complete test suite:

```bash
PYTHONPATH=src pytest -v
```

All 9 tests should pass, covering:
- End-to-end dry-run workflow
- Repository integration with file writing
- Provider inference
- Silver model extraction
- Workflow creation
- Policy attachment
- Error handling

See `docs/PHASE2_NOTES.md` for detailed implementation notes.

---

## Features

- **LangGraph Workflow**: Implements a `spec_discovery_graph` with the following stages:
  - **Ingest**: Read and parse API specifications
  - **Analyze**: Analyze specifications to identify requirements
  - **Schema**: Extract and generate schema from analysis
  - **Validation**: Validate the generated schema and analysis

- **Modular Architecture**:
  - `solver_coworker.graphs`: LangGraph workflow definitions
  - `solver_coworker.agents`: LangChain agent implementations
    - `ingestion_agent`: Load API specs from local files
    - `analysis_agent`: Extract endpoints and schemas using LLM
    - `schema_agent`: Map to silver/gold schema concepts
    - `validation_agent`: Validate against APIs and quality rules
  - `solver_coworker.tools`: Agent tools and utilities
    - `spec_loader`: Read specs from various formats (JSON, YAML, Markdown)
    - `vector_store`: RAG capabilities for spec querying
    - `graph_store`: Knowledge graph for entity relationships
    - `api_runner`: Execute HTTP requests from discovered specs
  - `solver_coworker.config`: Configuration management
  - `solver_coworker.logging`: Logging utilities

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

## Configuration

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

Required environment variables:
- `OPENAI_API_KEY`: Your OpenAI API key
- `ANTHROPIC_API_KEY`: Your Anthropic API key

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

### Running from Command Line

You can also run the workflow directly:

```bash
python -m solver_coworker.graphs.spec_discovery_graph
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
