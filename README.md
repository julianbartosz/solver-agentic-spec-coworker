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

### Project Structure

```
solver-agentic-spec-coworker/
├── docs/
│   ├── design/           # Design documents (placeholders)
│   └── decisions/        # Architecture Decision Records
├── data/
│   ├── raw/api_specs/    # Input API specifications
│   ├── processed/        # Processed data
│   └── models/           # Trained models
├── solver_coworker/
│   ├── agents/           # Agent implementations
│   ├── graphs/           # LangGraph workflow definitions
│   ├── tools/            # Utility tools
│   ├── config.py         # Configuration management
│   └── logging.py        # Logging utilities
└── tests/                # Test suite
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















---

## Implementation Checklist

### 1. Local Environment

* [ ] Install Python 3.11+
* [ ] Create virtualenv and install core deps:

  * `langchain`, `langgraph`, `pydantic`, `requests`
  * `psycopg2` or `asyncpg`
  * `pgvector` client or chosen vector store client
* [ ] Start Postgres in Docker with pgvector extension

---

### 2. Database Schemas

**Silver schema (`spec_silver`)**

* [ ] Tables:

  * `source_systems`
  * `spec_documents`, `spec_sections`
  * `endpoints`, `endpoint_parameters`
  * `schemas`, `fields`
  * `entities`, `entity_fields`, `entity_relationships`
  * `endpoint_entities`
  * `lineage_spec_links`
* [ ] Indexes:

  * `endpoints(path, http_method)`
  * `entities(name, source_system_id)`

**Gold schema (`requirements_gold`)**

* [ ] Tables:

  * `modeled_tables`, `modeled_columns`
  * `load_strategies`
  * `data_quality_rules`, `data_quality_results`
  * `data_contracts`

**Graph + vector**

* [ ] Graph storage:

  * `graph_nodes`, `graph_edges` or views over Silver tables
* [ ] Vector storage:

  * pgvector table for embeddings with metadata columns
  * Index on `(source_system_id, spec_document_id, spec_section_id)`

---

### 3. Core Domain Models (Python)

* [ ] Typed dicts or Pydantic models for:

  * `SourceSystemRef`, `SpecDocumentRef`
  * `EndpointDraft`, `FieldDraft`
  * `EntityDraft`, `RelationshipDraft`
  * `ModeledTableDraft`, `ModeledColumnDraft`
  * `LoadStrategyDraft`, `DataQualityRuleDraft`, `DataContractDraft`
* [ ] `WorkflowState` TypedDict with sections:

  * Context: `source_ref`, `spec_ref`
  * Spec content: `doc_chunks`
  * Silver drafts: `endpoint_drafts`, `entity_drafts`, `relationship_drafts`, `field_drafts`, `graph_nodes`, `graph_edges`
  * Gold drafts: `modeled_table_drafts`, `modeled_column_drafts`, `load_strategy_drafts`, `dq_rule_drafts`, `contract_drafts`
  * Control: `plan`, `completed_steps`, `errors`, `persisted_*`, `report_markdown`

Use LangGraph reducers for list fields.

---

### 4. LangGraph Workflow

**Nodes to implement**

* [ ] `plan_run`
* [ ] `ingest_spec`
* [ ] `detect_and_parse_spec`
* [ ] `extract_endpoints_and_schemas`
* [ ] `extract_entities_and_relationships`
* [ ] `build_graph`
* [ ] `design_gold_model`
* [ ] `design_load_strategies`
* [ ] `design_quality_and_contracts`
* [ ] `generate_code` (optional)
* [ ] `persist_results`
* [ ] `build_report`

**Graph wiring**

* [ ] Build `StateGraph[WorkflowState]`

* [ ] Add nodes and linear edges:

  `plan_run → ingest_spec → detect_and_parse_spec → extract_endpoints_and_schemas → extract_entities_and_relationships → build_graph → design_gold_model → design_load_strategies → design_quality_and_contracts → generate_code → persist_results → build_report`

* [ ] Entry node: `plan_run`

* [ ] Finish node: `build_report`

* [ ] Conditional edge after `detect_and_parse_spec` to route unsupported media types to an error node

---

### 5. Tooling and RAG

**Spec ingestion**

* [ ] HTTP fetcher for URLs
* [ ] File loader for local paths
* [ ] OpenAPI parser (JSON/YAML)
* [ ] HTML parser
* [ ] PDF loader and text extractor
* [ ] Chunker that populates `spec_sections` + `doc_chunks`

**Vector retrieval**

* [ ] Embedding function (e.g., OpenAI or Azure embeddings)
* [ ] Index builder that writes embeddings and metadata to pgvector
* [ ] Retriever that:

  * Filters by metadata (source, section_type, entity, endpoint)
  * Runs similarity search in the filtered set

**GraphRAG**

* [ ] `build_graph` that maps Silver drafts to `graph_nodes` and `graph_edges`
* [ ] Helper functions:

  * Get local neighborhood for an entity or endpoint
  * Map neighborhood back to related text chunks for prompts

---

### 6. Public Interfaces

**Python API**

* [ ] Implement:

  ```python
  def discover_requirements(
      spec_ref: SpecDocumentRef,
      source_ref: Optional[SourceSystemRef] = None,
      options: Optional[DiscoveryOptions] = None,
  ) -> DiscoveryResult:
      ...
  ```

* [ ] Return:

  * IDs for source, spec, Silver/Gold commits
  * Path or ID for the report

**CLI**

* [ ] Thin wrapper command, for example:

  ```bash
  ai-co-worker discover \
    --spec-url "https://api.stripe.com/openapi.json" \
    --source-code "stripe" \
    --out-dir "./artifacts/stripe"
  ```

* [ ] Write:

  * Report (Markdown or HTML)
  * Exported YAML/JSON contracts
  * Generated code stubs if present

---

### 7. Testing

**Unit tests**

* [ ] OpenAPI parsing helpers
* [ ] Type normalization and JSON path flattening
* [ ] Mapping entities → modeled tables
* [ ] Mapping fields → modeled columns

**Integration tests**

* [ ] Node-level tests for `ingest_spec`, `extract_endpoints_and_schemas`, `design_gold_model` with small fixtures and a test Postgres DB

**End-to-end tests**

* [ ] Prepare at least ten public API specs (Stripe, Twilio, GitHub, Slack, Shopify, Notion, HubSpot, SendGrid, OpenAI, plus one larger spec)
* [ ] For each:

  * Run `discover_requirements` against test Postgres
  * Check key entities, endpoints, and tables exist and have reasonable grain and keys

---

### 8. Milestones (v1)

* [ ] **M1**: Minimal pipeline

  * Ingest one OpenAPI spec → Silver tables + simple report

* [ ] **M2**: Full Silver + basic Gold

  * Entities, relationships, tables, columns, load patterns for three specs

* [ ] **M3**: GraphRAG + evaluation

  * Graph construction
  * RAG + GraphRAG in design nodes
  * Evaluation across the ten-spec corpus

* [ ] **M4**: Codegen + refinement

  * Code generation node
  * Prompt and heuristic tuning based on test results and review
MIT
