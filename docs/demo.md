# Production Demo Guide

This document describes how to run a real end-to-end demo of the Integration Co-Worker with actual LLM-powered code generation.

## Prerequisites

### 1. Database Setup (Postgres + pgvector)

```bash
# Option A: Docker
docker run -d --name integration-coworker-db \
  -e POSTGRES_USER=coworker \
  -e POSTGRES_PASSWORD=coworker \
  -e POSTGRES_DB=integration_coworker \
  -p 5432:5432 \
  ankane/pgvector

# Set DATABASE_URL
export DATABASE_URL="postgresql://coworker:coworker@localhost:5432/integration_coworker"
```

Or use an existing Postgres with pgvector extension installed.

### 2. API Keys

```bash
# Required: OpenAI API Key for LLM codegen
export OPENAI_API_KEY="sk-..."

# Optional: Model overrides
export LLM_MODEL="gpt-4"  # or gpt-4-turbo, gpt-3.5-turbo
export EMBEDDING_MODEL="text-embedding-3-small"
```

### 3. Verify Mock Mode is OFF

```bash
# DO NOT set these for production demo
# USE_MOCK_LLM=true  <- do not set
# USE_SQLITE=true    <- do not set
```

## Demo Command

### Basic Demo with Stripe OpenAPI Spec

```bash
python -m integration_coworker.cli run \
  -s "https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json" \
  -t "Create a customer and charge their payment method" \
  --provider stripe \
  --repo-root ./demo-output \
  --persist
```

### What Happens

1. **Spec Ingestion**: Downloads and parses the Stripe OpenAPI spec
2. **Silver Model Building**: Extracts endpoints, schemas, entities
3. **Task Understanding**: LLM analyzes the task description
4. **Workflow Planning**: Creates a Gold workflow graph
5. **LLM Codegen**: 
   - Generates client code (spec-driven naming from operationId)
   - Generates flow code (task-driven naming)
   - Generates test code
   - All names derived from spec, not hardcoded
6. **Repo Integration**: Writes files according to RepoProfile layout
7. **Persistence**: Saves Silver/Gold models to Postgres
8. **KG Learning**: Updates the GraphRAG knowledge graph

### Expected Output

```
demo-output/
├── src/
│   └── integrations/
│       ├── clients/
│       │   └── stripe.py           # StripeClient with spec-driven methods
│       └── flows/
│           └── stripe_create_customer_charge.py  # Task-driven flow
└── tests/
    └── integrations/
        └── test_stripe_create_customer_charge.py
```

## Demo with Mock Payments (Local Testing)

For quick local testing without external API spec:

```bash
python -m integration_coworker.cli demo \
  --repo-root ./demo-output \
  --persist
```

This uses the bundled `mock_payments_openapi.yaml` fixture.

## Verifying LLM Is Active

Look for these log messages:

```
# GOOD: LLM is generating code
INFO - Using LLM-generated body for client 'MockPaymentsClient'
INFO - Using LLM-generated body for flow 'create_checkout_session_flow'

# BAD: Falling back to templates (check your API key)
WARNING - Falling back to template skeleton for client '...' because: ...
```

## Troubleshooting

### "Codegen is running with mock LLM" Warning

If you see:
```
WARNING - Codegen is running with mock LLM; generated code is for testing only...
```

This means `OPENAI_API_KEY` is not set. Set it:
```bash
export OPENAI_API_KEY="sk-..."
```

### LLM Output Falls Back to Template

Check the logs for the specific reason:
- `LLM returned empty response` - API issue
- `LLM returned mock/placeholder response` - Mock mode active
- `LLM output failed AST syntax validation` - LLM produced invalid code
- `LLM output missing expected class/function` - LLM didn't follow skeleton

### Database Connection Issues

Verify your DATABASE_URL:
```bash
python -c "from integration_coworker.persistence.db import get_engine; print(get_engine())"
```

## What Makes This "Production-Grade"

1. **Spec-Driven Naming**: All names come from OpenAPI `operationId`, paths, and schemas—not hardcoded "checkout" assumptions.

2. **RepoProfile Layout**: File paths respect the target repo's conventions (via layout_hints).

3. **LLM Validation**: Generated code is validated for:
   - Python syntax (AST parsing)
   - Expected class/function presence
   - Non-mock content detection

4. **Fallback Safety**: If LLM fails validation, clean template code is used.

5. **GraphRAG Learning**: Successful runs populate the knowledge graph for improved future runs.

## Proof Points

Run these tests to verify production-readiness:

```bash
# Prove LLM is on the hot path
pytest tests/test_llm_codegen_path.py -v

# Prove generated code is valid and spec-driven
pytest tests/test_codegen_validation.py -v

# Full integration test
pytest tests/test_end_to_end_integration.py -v
```

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes (for real LLM) | OpenAI API key |
| `DATABASE_URL` | Yes (for persistence) | Postgres connection string |
| `LLM_MODEL` | No | Override LLM model (default: gpt-4) |
| `EMBEDDING_MODEL` | No | Override embedding model |
| `USE_MOCK_LLM` | No | Set to `true` for testing only |
| `USE_SQLITE` | No | Set to `true` for SQLite fallback |
| `LANGCHAIN_TRACING_V2` | No | Set to `true` for LangSmith tracing |
| `LANGCHAIN_API_KEY` | No | LangSmith API key |
| `LANGCHAIN_PROJECT` | No | LangSmith project name |

## Observability with LangSmith

Integration Co-Worker supports [LangSmith](https://smith.langchain.com/) for full observability of LangGraph workflow execution and LLM calls.

### Setting Up LangSmith

1. Create an account at [smith.langchain.com](https://smith.langchain.com/)

2. Get your API key from Settings → API Keys

3. Configure environment variables:

```bash
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY="lsv2_pt_..."
export LANGCHAIN_PROJECT="integration-coworker"  # Optional, defaults to "default"
```

### What's Traced

With LangSmith enabled, you can see:

- **Full workflow execution**: Each LangGraph node appears as a span
- **LLM calls**: Prompts, responses, token counts, and latency
- **Embeddings**: Vector embedding operations
- **Node inputs/outputs**: The WorkflowState at each step
- **Error traces**: Stack traces when nodes fail

### Viewing Traces

1. Run an integration:
```bash
python -m integration_coworker.cli demo --persist
```

2. Open [smith.langchain.com](https://smith.langchain.com/) → Projects → integration-coworker

3. Click on the latest run to see the trace

### Trace Features

- **Timeline view**: See execution order and parallelism
- **Token usage**: Track costs per run
- **Feedback**: Mark runs as good/bad for evaluation
- **Datasets**: Export runs for regression testing

### Debugging with LangSmith

Common debugging scenarios:

1. **Slow runs**: Check the timeline to find bottleneck nodes
2. **LLM failures**: View the exact prompt and response
3. **Parsing errors**: See input/output at each node
4. **Workflow routing**: Trace which edges were taken

### Disabling LangSmith

Unset the environment variables or set:
```bash
export LANGCHAIN_TRACING_V2="false"
```

## Advanced Troubleshooting

### Health Check Command

Run a quick health check before starting:

```bash
# Quick check
python -m integration_coworker.cli health

# Detailed output
python -m integration_coworker.cli health --verbose

# JSON for scripts/CI
python -m integration_coworker.cli health --json
```

Expected output:
```
Integration Co-Worker Health Check
========================================

✓ Database
   PostgreSQL connection successful

✓ Pgvector
   pgvector extension available

✓ Llm
   Configured with gpt-4

✓ Packages
   All 4 required packages installed

✓ All health checks passed
```

### Verbose Logging

Enable verbose debug logging to see internal execution:

```bash
# For the run command
python -m integration_coworker.cli run \
  -s ./spec.yaml \
  -t "Create checkout" \
  --verbose

# For the demo command
python -m integration_coworker.cli demo --verbose
```

This shows:
- Workflow node entry/exit
- LLM prompt summaries
- Database operations
- File system operations

### Common Issues

#### pgvector Not Installed

```
⚠ pgvector not found (run CREATE EXTENSION vector)
```

**Solution:**
```bash
# Connect to Postgres and run:
CREATE EXTENSION IF NOT EXISTS vector;
```

#### psycopg Not Installed

```
✗ psycopg not installed
```

**Solution:**
```bash
pip install 'psycopg[binary]' psycopg_pool
```

#### LLM Returning Empty Responses

If you see:
```
WARNING - LLM returned empty response
```

**Possible causes:**
1. API rate limits hit - wait and retry
2. API key invalid - verify `OPENAI_API_KEY`
3. Model not available - try a different `LLM_MODEL`

#### Generated Code Fails Syntax Check

```
WARNING - LLM output failed AST syntax validation
```

**The system automatically falls back to template code.** This is a safety feature.

To investigate:
1. Enable verbose logging: `--verbose`
2. Check LangSmith traces for the raw LLM output
3. Consider using a more capable model like gpt-4

#### Database Connection Timeout

```
✗ Failed to connect to PostgreSQL
```

**Solutions:**
1. Verify Postgres is running: `pg_isready -h localhost -p 5432`
2. Check connection string format: `postgresql://user:pass@host:port/db`
3. Test connection: `psql $DATABASE_URL -c "SELECT 1"`

#### Knowledge Graph Is Empty

```
⚠ KG is empty. Run an integration with --persist first.
```

**Solution:**
Run an integration with persistence enabled:
```bash
python -m integration_coworker.cli demo --persist
```

Then query the KG:
```bash
python -m integration_coworker.cli kg-dump
```

### Getting Help

1. **Check status**: `python -m integration_coworker.cli status`
2. **Run health check**: `python -m integration_coworker.cli health`
3. **Enable verbose mode**: Add `--verbose` to any command
4. **View LangSmith traces**: Check the web UI for detailed execution info
5. **Run tests**: `pytest tests/ -v` to verify setup
