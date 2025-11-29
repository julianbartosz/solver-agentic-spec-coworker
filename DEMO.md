# Demo Guide: Agentic Integration Co-Worker

This guide walks you through demonstrating the Agentic Integration Co-Worker — a system that transforms OpenAPI specs and natural language tasks into production-quality Python integration code.

## Prerequisites

- **Python**: 3.11+
- **Virtual Environment**: Created and activated
- **Dependencies**: Installed via `pip install -e .`

### Quick Setup

```bash
# Clone and enter the repo
cd solver-agentic-spec-coworker

# Create virtualenv (if not already done)
python -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .
```

## Environment Variables

| Variable | Purpose | Demo Value |
|----------|---------|------------|
| `USE_SQLITE` | Use SQLite instead of Postgres | `true` |
| `USE_MOCK_LLM` | Use mock LLM instead of OpenAI | `true` |
| `OPENAI_API_KEY` | For real LLM calls | (optional) |
| `DATABASE_URL` | Postgres connection | (optional) |

For demos without external dependencies:
```bash
export USE_SQLITE=true
export USE_MOCK_LLM=true
```

---

## Demo Paths

### 1. Verify Tests Pass (30 seconds)

```bash
USE_SQLITE=true USE_MOCK_LLM=true pytest tests -v --tb=short | tail -20
```

**Expected output:**
```
======================== 135 passed, 1 warning in 5.68s ========================
```

### 2. Check System Status (15 seconds)

```bash
USE_SQLITE=true USE_MOCK_LLM=true python -m integration_coworker.cli status
```

**Expected output:**
```
Integration Co-Worker Status
========================================

📦 Database:
   Engine: SQLite (test mode)
   Path: .data/integration_coworker.sqlite3

🤖 LLM:
   Mode: Mock (USE_MOCK_LLM=true)

📊 Embeddings:
   Model: text-embedding-3-small
   Dimensions: 1536
   Batch Size: 100
```

### 3. Dry-Run Demo (1 minute)

Show the full workflow without persisting changes:

```bash
USE_SQLITE=true USE_MOCK_LLM=true \
  python -m integration_coworker.cli demo --dry-run
```

**What you should see:**
- Spec ingestion (1 document, 5 chunks)
- Silver API Model extraction (2 endpoints, 2 schemas)
- Workflow planning (4 nodes, 3 edges)
- Policy attachment (auth, retry, logging, idempotency, rate_limit)
- Code artifact generation (client, flow, test)

**Key talking points:**
- "We extracted 2 endpoints from the OpenAPI spec"
- "Built a 4-step workflow: start → validate → api_call → end"
- "Generated 3 files: client, flow, and test"

### 4. Generate Code into a Repo (2 minutes)

Create a demo repo and generate real files:

```bash
# Create demo directory
mkdir -p demo_repo/src/integrations demo_repo/tests/integrations

# Run with repo integration
USE_SQLITE=true USE_MOCK_LLM=true \
  python -m integration_coworker.cli run \
    -s tests/fixtures/mock_payments_openapi.yaml \
    -t "Create checkout session" \
    --repo-root demo_repo \
    --provider mock_payments
```

**Inspect generated files:**

```bash
# Client code
cat demo_repo/src/integrations/clients/mock_payments.py | head -50

# Flow code
cat demo_repo/src/integrations/flows/mock_payments_*.py | head -40

# Test code
cat demo_repo/tests/integrations/test_mock_payments_*.py | head -30
```

**Key talking points:**
- "Client class uses our battle-tested IntegrationHttpClient"
- "Flow function has proper validation and error handling"
- "Tests are auto-generated with mocking patterns"

### 5. Show Database Contents (30 seconds)

Query the SQLite database to show persistence:

```bash
# Show integration tasks
sqlite3 .data/integration_coworker.sqlite3 \
  "SELECT id, provider_code, task_slug FROM integration_tasks;"

# Show endpoints extracted
sqlite3 .data/integration_coworker.sqlite3 \
  "SELECT id, path, method FROM endpoints;"

# Show KG nodes
sqlite3 .data/integration_coworker.sqlite3 \
  "SELECT node_type, key, name FROM kg_nodes LIMIT 10;"
```

---

## What Makes This Different from ChatGPT

| Feature | ChatGPT | This System |
|---------|---------|-------------|
| **Repeatability** | Different every time | Same input → same output |
| **Persistence** | No history | Full Silver/Gold/KG database |
| **Learning** | One-shot | KG templates improve over runs |
| **Repo-Aware** | Generic output | Framework-specific placement |
| **Testable** | You write tests | Tests auto-generated |

---

## Troubleshooting

### "Module not found" errors
```bash
# Ensure PYTHONPATH includes src
export PYTHONPATH=src
# Or run with prefix
PYTHONPATH=src python -m integration_coworker.cli ...
```

### Database not initialized
```bash
USE_SQLITE=true python -m integration_coworker.cli init-db
```

### Want real LLM output
```bash
# Set your OpenAI API key
export OPENAI_API_KEY=sk-...
# Remove mock mode
unset USE_MOCK_LLM
python -m integration_coworker.cli demo --dry-run
```

---

## Full 5-Minute Demo Script

1. **"Let me show you the system status"** → Run `status` command
2. **"Here's our test OpenAPI spec"** → `cat tests/fixtures/mock_payments_openapi.yaml | head -20`
3. **"Watch the workflow run"** → Run `demo --dry-run`
4. **"Now let's generate real code"** → Run with `--repo-root demo_repo`
5. **"Look at what we generated"** → Show client, flow, test files
6. **"All tests pass"** → `pytest tests -v | tail -10`

---

## Cleanup

```bash
# Remove demo repo
rm -rf demo_repo

# Clear database (optional)
rm -f .data/integration_coworker.sqlite3
```

---

*Generated by Integration Co-Worker Demo Guide*
