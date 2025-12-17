# Ops Runbook: Getting Started

This page is the **operator/runbook** entrypoint for running Integration Co-Worker in real environments (local Docker Compose or production-ish deployments).

It is **not** the end-user onboarding tutorial. For installation and quickstart, use the MkDocs **Getting Started** section (Installation / Quickstart / Configuration).

> **Status**: Active (Production-critical)
>
> **Last Updated**: December 2025

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Database Setup](#database-setup)
4. [LLM Configuration](#llm-configuration)
5. [Quick Demo](#quick-demo)
6. [Production Runbook](#production-runbook)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- **Python 3.11.x** (required - 3.12/3.13 have package compatibility issues with LangGraph)
- **Docker** (optional, for Postgres)
- **OpenAI API Key** (for embeddings and report generation)
- **Anthropic API Key** (for task understanding, planning, code generation)

---

## Installation

### Recommended: Use Setup Script

The setup script ensures a consistent environment with compatible package versions:

```bash
# Clone the repository
git clone <repo-url>
cd solver-agentic-spec-coworker

# Run automated setup (creates .venv311 with Python 3.11)
./scripts/setup_env.sh

# Activate environment
source .venv311/bin/activate
```

### Validate Environment

```bash
# Check that environment is healthy
./scripts/setup_env.sh --check
```

Expected output:
```
[SUCCESS] Python version: Python 3.11.x
[SUCCESS] Single Python version in site-packages
[SUCCESS] langgraph: 1.0.4
[SUCCESS] langgraph-checkpoint: 3.0.1
[SUCCESS] langchain: 1.1.3
[SUCCESS] All critical imports work
[SUCCESS] Environment validation passed!
```

### Fix Broken Environment

If you encounter import errors or `AttributeError: 'JsonPlusSerializer' object has no attribute 'dumps'`:

```bash
# Clean and recreate
./scripts/setup_env.sh --clean
```

### Manual Installation (Not Recommended)

If you must install manually, ensure Python 3.11:

```bash
# macOS
brew install python@3.11

# Create venv with specific Python version
python3.11 -m venv .venv311
source .venv311/bin/activate

# Install from lock file for exact versions
pip install -r requirements.lock.txt
pip install -e ".[dev,postgres]"
```

⚠️ **Warning**: Using `python -m venv .venv` without specifying Python 3.11 may create an environment with incompatible packages. The LangGraph stack requires careful version alignment.

---

## Database Setup

The Integration Co-Worker supports two database backends:

| Backend | Use Case | Vector Search |
|---------|----------|---------------|
| **SQLite** | Local dev, testing, single-user | Python cosine similarity |
| **Postgres + pgvector** | Production, team deployment | Native vector operators (`<=>`) |

---

## Spec files: `specs/` vs `data/spec_sweep/`

This repo contains two places you may see API specs:

- `specs/` — **curated, checked-in** specs used for demos, fixtures, and long-lived reference.
- `data/spec_sweep/` — **reproducible local snapshots** downloaded for a specific evaluation run (e.g., production sweeps). Treat this as a cache of remote specs.

We keep them separate on purpose:

- `specs/` stays stable, reviewable, and can be referenced in docs/tests.
- `data/spec_sweep/` can be refreshed/overwritten without worrying about breaking unrelated docs/tests.

If we later find we want a single unified location, the safest approach is to standardize on `specs/` as the canonical folder and make `data/spec_sweep/` a generated/cache directory.

### SQLite (Development)

SQLite is the default. No setup required.

```bash
# Data stored at .data/integration_coworker.sqlite3
USE_SQLITE=true python -m integration_coworker.cli demo
```

### PostgreSQL with pgvector (Production)

For the canonical Postgres runbook, see:

- [Operations → Postgres + pgvector](operations/db-postgres.md)

#### Option 1: Docker (Recommended)

```bash
# Single Docker command
docker run -d \
  --name integration-coworker-db \
  -e POSTGRES_USER=integration \
  -e POSTGRES_PASSWORD=integration \
  -e POSTGRES_DB=integration_coworker \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# Enable the vector extension
docker exec -it integration-coworker-db psql -U integration -d integration_coworker \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

#### Option 2: Docker Compose

Use the repo’s compose file(s) directly:

- `docker-compose.yml` (dev-ish)
- `docker-compose.prod.yml` (prod-ish)

```bash
docker-compose up -d db
```

#### Environment Variables

```bash
# Required for Postgres mode
export DATABASE_URL="postgresql://integration:integration@localhost:5432/integration_coworker"

# Optional: Force SQLite for tests
# export USE_SQLITE=true
```

#### Initialize Schema

Use the dedicated Postgres init script:

```bash
python scripts/init_db_postgres.py
python scripts/init_db_postgres.py --verify
```

#### Verify Setup

```bash
# Check status
PYTHONPATH=src python -m integration_coworker.cli status

# Check pgvector manually
docker exec -it integration-coworker-db psql -U integration -d integration_coworker -c "\dx"
```

Expected output:
```
  Name   | Version |   Schema   |         Description
---------+---------+------------+------------------------------
 plpgsql | 1.0     | pg_catalog | PL/pgSQL procedural language
 vector  | 0.7.0   | public     | vector data type
```

---

## LLM Configuration

The system uses a multi-provider LLM architecture:

| Purpose | Provider | Model |
|---------|----------|-------|
| Task understanding & planning | Anthropic | claude-sonnet-4 |
| Code generation | Anthropic | claude-sonnet-4 |
| Report summarization | OpenAI | gpt-4o-mini |
| Embeddings | OpenAI | text-embedding-3-small |

### Required Environment Variables

```bash
# OpenAI (embeddings + build_report)
export OPENAI_API_KEY="sk-..."

# Anthropic (planning + codegen)
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Optional: LangSmith Tracing

```bash
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY="lsv2_pt_..."
export LANGCHAIN_PROJECT="integration-coworker"
```

### Common LLM Misconfigurations

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Nodes fall back to heuristics" | Missing API key | Set `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` |
| "Anthropic API returns 404" | Wrong model name | Check archetype YAML files |
| LangSmith shows 0.00s for all nodes | Mock mode enabled | `unset USE_MOCK_LLM` |
| "LLM output failed AST validation" | LLM produced invalid code | Template fallback activated; check logs |

---

## Quick Demo

Verify the system works end-to-end:

```bash
# 1. Mock mode (no API keys needed)
USE_SQLITE=true USE_MOCK_LLM=true python -m integration_coworker.cli demo

# 2. Real LLM mode
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
python -m integration_coworker.cli demo --persist

# 3. Custom spec
python -m integration_coworker.cli run \
  -s path/to/openapi.yaml \
  -t "Create a checkout session" \
  --provider my_provider \
  --dry-run
```

### Expected Output

A successful run produces:
- `demo-output/src/integrations/clients/<provider>.py` — Client code
- `demo-output/src/integrations/flows/<task>.py` — Workflow code
- `demo-output/tests/integrations/test_<task>.py` — Test code

---

## Production Runbook

For canonical operator runbooks, see:

- [Operations → Deployment](operations/deployment.md)
- [Operations → Validation](operations/validation.md)

### How to Restart

```bash
# Restart database
docker-compose restart db

# Restart from clean state
docker-compose down && docker-compose up -d
python scripts/init_db_postgres.py
```

### Where to Look When It's Broken

| Issue | First Check | Command |
|-------|-------------|---------|
| DB connection issues | Container running? | `docker ps \| grep integration-coworker-db` |
| LLM failures | API keys set? | `echo $OPENAI_API_KEY $ANTHROPIC_API_KEY` |
| Missing schema | Schema initialized? | `python -m integration_coworker.cli status` |
| Trace visibility | LangSmith configured? | Check [smith.langchain.com](https://smith.langchain.com/) |

### Critical Health Checks

```bash
# Full health check
python -m integration_coworker.cli health

# Verbose health check (includes LLM connectivity)
python -m integration_coworker.cli health --verbose --check-llm

# Database connectivity
docker exec -it integration-coworker-db pg_isready -U integration
```

### Logs

```bash
# Run with verbose logging
python -m integration_coworker.cli demo --verbose 2>&1 | tee run.log

# Check for LLM fallbacks
grep -E "fallback|heuristic" run.log
```

---

## Troubleshooting

### "AttributeError: 'JsonPlusSerializer' object has no attribute 'dumps'"

This is caused by mixed Python versions in the virtual environment (e.g., Python 3.12 venv with some 3.13 packages). Fix:

```bash
./scripts/setup_env.sh --clean
source .venv311/bin/activate
```

### Multiple Python Versions in site-packages

If you see packages from multiple Python versions:
```bash
ls .venv/lib/
# Shows: python3.12  python3.13  <-- BAD!
```

This causes import errors. Delete the venv and use the setup script:
```bash
rm -rf .venv
./scripts/setup_env.sh
```

### "Postgres is configured but required dependencies are missing"

```bash
pip install 'psycopg[binary]' psycopg_pool
```

### "Failed to connect to Postgres database"

1. Check Postgres is running: `docker ps | grep integration-coworker-db`
2. Verify connection: `docker exec -it integration-coworker-db pg_isready -U integration`
3. Check DATABASE_URL is set correctly
4. Ensure port 5432 is not blocked

### "pgvector extension not found"

Ensure you're using the `pgvector/pgvector` Docker image, not plain `postgres`.

### "Vector dimension mismatch"

Ensure embeddings are generated with the correct model (OpenAI text-embedding-3-small produces 1536 dims).

### Test Mode (SQLite)

For running tests without Postgres:

```bash
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/ -v
```

**Note**: SQLite mode does not support pgvector embeddings. Vector columns are stored as JSON text.

---

## Advanced CLI Commands

Beyond the core commands (`run`, `demo`, `status`, `init-db`, `health`), the following are available for debugging and knowledge graph operations:

| Command | Purpose |
|---------|---------|
| `kg-dump` | Export the knowledge graph to JSON for inspection |
| `kg-query` | Run a query against the knowledge graph |
| `kg-confidence` | Display confidence scores for templates and patterns |
| `feedback` | Submit feedback on a run for learning purposes |
| `feedback-sync` | Synchronize feedback records with the database |
| `ui` | Launch a local web interface for visual exploration |

### Example Usage

```bash
# Dump knowledge graph
python -m integration_coworker.cli kg-dump --output kg-export.json

# Query the graph
python -m integration_coworker.cli kg-query "payment processing"

# Check template confidence
python -m integration_coworker.cli kg-confidence
```

