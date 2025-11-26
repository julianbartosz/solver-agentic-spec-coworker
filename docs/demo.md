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
