# Demo Guide

> **Status**: Archived – for historical demo flows and talk tracks.
>
> For the current minimal demo, see [`docs/GETTING_STARTED.md#quick-demo`](../../GETTING_STARTED.md#quick-demo).
>
> **Last Updated**: December 2025

---

## Table of Contents

1. [Quick Demo](#quick-demo)
2. [Environment Setup](#environment-setup)
3. [Demo Commands](#demo-commands)
4. [What Happens During a Run](#what-happens-during-a-run)
5. [Observability with LangSmith](#observability-with-langsmith)
6. [Talk Track for Stakeholders](#talk-track-for-stakeholders)
7. [Troubleshooting](#troubleshooting)

---

## Quick Demo

```bash
# Minimal demo (mock LLM, no external dependencies)
USE_SQLITE=true USE_MOCK_LLM=true python -m integration_coworker.cli demo

# Production demo (real LLM, Postgres)
export DATABASE_URL="postgresql://user:pass@localhost:5432/demo_db"
export OPENAI_API_KEY="sk-..."
python -m integration_coworker.cli demo --persist
```

---

## Environment Setup

### Required Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes (for real LLM) | OpenAI API key |
| `DATABASE_URL` | Yes (for persistence) | Postgres connection string |
| `LLM_MODEL` | No | Override LLM model (default: gpt-4) |
| `USE_MOCK_LLM` | No | Set to `true` for testing only |
| `USE_SQLITE` | No | Set to `true` for SQLite fallback |

### Production Demo Configuration

```bash
export DATABASE_URL="postgresql://user:pass@localhost:5432/demo_db"
export OPENAI_API_KEY="sk-..."
unset USE_SQLITE USE_MOCK_LLM USE_IN_MEMORY_KG_FALLBACK

# Verify configuration
integration-coworker status
```

---

## Demo Commands

### Initialize Database

```bash
integration-coworker init-db
```

### Run Demo (Built-in Spec)

```bash
# Dry run (no DB writes)
integration-coworker demo

# With persistence
integration-coworker demo --persist
```

### Run Custom Spec

```bash
integration-coworker run \
  --spec-ref path/to/openapi.yaml \
  --task "Create a checkout session" \
  --provider my_provider \
  --persist
```

### Health Check

```bash
integration-coworker health
integration-coworker health --verbose
integration-coworker health --check-llm
```

---

## What Happens During a Run

1. **Spec Ingestion**: Downloads and parses the OpenAPI spec
2. **Silver Model Building**: Extracts endpoints, schemas, entities
3. **Task Understanding**: LLM analyzes the task description
4. **Workflow Planning**: Creates a Gold workflow graph
5. **LLM Codegen**: 
   - Generates client code (spec-driven naming)
   - Generates flow code (task-driven naming)
   - Generates test code
6. **Repo Integration**: Writes files according to RepoProfile layout
7. **Persistence**: Saves Silver/Gold models to database
8. **KG Learning**: Updates the GraphRAG knowledge graph

### Expected Output

```
demo-output/
├── src/
│   └── integrations/
│       ├── clients/
│       │   └── stripe.py           # Client with spec-driven methods
│       └── flows/
│           └── stripe_create_customer_charge.py  # Task-driven flow
└── tests/
    └── integrations/
        └── test_stripe_create_customer_charge.py
```

---

## Observability with LangSmith

Integration Co-Worker supports [LangSmith](https://smith.langchain.com/) for full observability.

### Setup

```bash
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY="lsv2_pt_..."
export LANGCHAIN_PROJECT="integration-coworker"
```

### What's Traced

- Full workflow execution (each node as a span)
- LLM calls (prompts, responses, tokens, latency)
- Node inputs/outputs
- Error traces

### Viewing Traces

1. Run an integration: `python -m integration_coworker.cli demo --persist`
2. Open [smith.langchain.com](https://smith.langchain.com/) → Projects → integration-coworker
3. Click on the latest run

---

## Talk Track for Stakeholders

### The Problem (1 min)

- Manual integration development is slow and inconsistent
- Each developer interprets API specs differently
- No reusable patterns or templates
- Testing is ad-hoc, error handling varies

### The Solution (2 min)

- Input: OpenAPI spec + plain-English task
- Output: Working Python client code, workflow function, and tests
- **Speed**: Generate working integration in seconds vs. hours
- **Consistency**: Same patterns across all integrations
- **Quality**: Generated code includes tests, error handling, idempotency

### Evidence It Works (2 min)

- **919 tests passing** (100%)
- Generated code is **executed in tests** (not just syntax checked)
- **Idempotent operations**: Safe to re-run

### What's Different from ChatGPT?

1. **Repeatability**: Same input → same output (deterministic templates)
2. **Database-backed**: Every design decision stored, queryable
3. **Template reuse**: Patterns stored in knowledge graph
4. **Repo-aware**: Framework-specific file placement
5. **Traceable**: Run IDs, error logs, audit trail

---

## Troubleshooting

### "Codegen is running with mock LLM" Warning

```bash
# Set your API key
export OPENAI_API_KEY="sk-..."
```

### LLM Output Falls Back to Template

Check logs for the specific reason:
- `LLM returned empty response` - API issue
- `LLM returned mock/placeholder response` - Mock mode active
- `LLM output failed AST syntax validation` - LLM produced invalid code

### "KG returned no templates"

Run a non-dry-run first to populate KG:
```bash
integration-coworker demo --persist
```

### "Using in-memory fallback"

```bash
# This should NEVER be set in demo
unset USE_IN_MEMORY_KG_FALLBACK
```

### Verbose Logging

```bash
python -m integration_coworker.cli demo --verbose
```

