# Demo Playbook

## Purpose

This playbook documents the **real demo path** for the Agentic Integration Designer & Code Generator. It covers environment configuration, sanity checks, and a step-by-step demo script.

---

## 1. Environment Variables — Canonical Table

| Variable                    | Purpose                                           | Real Demo Value                              | Test/Dev Fallback          |
| --------------------------- | ------------------------------------------------- | -------------------------------------------- | -------------------------- |
| `DATABASE_URL`              | PostgreSQL connection string                      | `postgresql://user:pass@host:5432/db`        | *(not set)*                |
| `USE_SQLITE`                | Force SQLite mode (for testing)                   | *(not set or `false`)*                       | `true`                     |
| `OPENAI_API_KEY`            | OpenAI API key for LLM + embeddings               | `sk-...`                                     | *(not set)*                |
| `LLM_BASE_URL`              | Override LLM base URL (e.g., Azure OpenAI)        | *(optional)*                                 | *(not set)*                |
| `LLM_MODEL`                 | LLM model name                                    | `gpt-4o` or `gpt-4-turbo`                    | *(uses default)*           |
| `USE_MOCK_LLM`              | Force mock LLM (no real API calls)                | *(not set or `false`)*                       | `true`                     |
| `USE_IN_MEMORY_KG_FALLBACK` | Enable legacy in-memory templates (bypass DB KG) | **Must be `0` or unset** — never in demo!   | `1` *(only in unit tests)* |
| `EMBEDDING_MODEL`           | Embedding model name                              | `text-embedding-3-small`                     | *(not needed)*             |
| `HTTP_TIMEOUT`              | HTTP client timeout (seconds)                     | `30`                                         | `30`                       |
| `HTTP_MAX_RETRIES`          | HTTP client max retries                           | `3`                                          | `3`                        |
| `HTTP_RETRY_BACKOFF`        | HTTP client retry backoff (seconds)               | `1.0`                                        | `1.0`                      |

### Key Points

- **Real Demo**: `DATABASE_URL` + `OPENAI_API_KEY` must be set; all `USE_*` flags unset or `false`.
- **Test/Dev**: `USE_SQLITE=true` + `USE_MOCK_LLM=true` for local testing without external dependencies.
- **Never in Demo**: `USE_IN_MEMORY_KG_FALLBACK=1` bypasses the DB-backed KG entirely.

---

## 2. Path Verification — What the CLI Uses

### 2.1 `integration-coworker run`

```bash
integration-coworker run \
  --spec-ref <path-or-url> \
  --task "Create a checkout session for payment processing"
```

**Execution path**:

1. `cli.py::run_integration()` → `api.entrypoint.design_and_generate_integration()`
2. `graph.runtime.run_workflow()` executes nodes in order:
   - `understand_task` → `align_task_with_kg` → `plan_integration_flow` → ... → `persist_kg_learning`
3. **KG interaction**:
   - `align_task_with_kg` calls `kg.query_workflow_templates()` (DB-backed GraphRAG)
   - `persist_kg_learning` writes to `kg_nodes`, `kg_edges`, `kg_workflow_steps`

### 2.2 `integration-coworker demo`

```bash
integration-coworker demo --persist  # Writes to DB
integration-coworker demo            # Dry-run (default)
```

Uses `tests/fixtures/mock_payments_openapi.yaml` as the spec.

### 2.3 `integration-coworker status`

```bash
integration-coworker status
```

Displays current config: DB engine, LLM mode, pgvector status, warnings.

### 2.4 `integration-coworker init-db`

```bash
DATABASE_URL="postgresql://..." integration-coworker init-db
```

Creates all schemas: `spec_silver`, `integration_gold`, `repo_meta`, `kg`.

---

## 3. KG Learning and GraphRAG Interaction

### 3.1 Write Path (`persist_kg_learning`)

After a successful run (not dry-run), the `persist_kg_learning` node:

1. Upserts **provider node** → `kg_nodes` (type=`provider`)
2. Upserts **task node** → `kg_nodes` (type=`task`)
3. Upserts **workflow_template node** → `kg_nodes` (type=`workflow_template`)
4. Upserts **endpoint nodes** → `kg_nodes` (type=`endpoint`)
5. Upserts **entity nodes** → `kg_nodes` (type=`entity`)
6. Creates **edges** → `kg_edges` (e.g., `belongs_to_provider`, `uses_endpoint`)
7. Upserts **workflow steps** → `kg_workflow_steps`
8. Computes **embeddings** (if `OPENAI_API_KEY` set and `USE_MOCK_LLM!=true`)

### 3.2 Read Path (`align_task_with_kg`)

The `align_task_with_kg` node:

1. Calls `kg.query_workflow_templates(provider_code, task_description, ...)`
2. **Graph filtering**: Filter by `provider_code`, `node_type='workflow_template'`
3. **Graph scoring** (40%): Boost templates with edges to known entities/endpoints
4. **Embedding scoring** (40%): Cosine similarity (if embeddings available)
5. **Exact match bonus** (20%): +0.3 if task_slug appears in template key
6. **Base score**: +0.1 to ensure all graph-filtered templates are considered
7. Returns templates with `final_score > similarity_threshold` (default: 0.2)

### 3.3 Scoring Formula

```
final_score = (graph_score × 0.4) + (embedding_score × 0.4) + exact_match_bonus + 0.1
```

- **With real embeddings**: Scores typically 0.5–0.9
- **With mock LLM** (no embeddings): Max score = 0.4 (graph) + 0.0 (embedding) + 0.3 (exact) + 0.1 = 0.8
- **similarity_threshold**: 0.2 (low to accommodate mock mode)

---

## 4. Pre-Demo Sanity Checks

### 4.1 Database Check

```bash
# Check Postgres is running and accessible
psql $DATABASE_URL -c "SELECT 1"

# Check pgvector extension
psql $DATABASE_URL -c "SELECT * FROM pg_extension WHERE extname = 'vector'"

# If missing, install it:
psql $DATABASE_URL -c "CREATE EXTENSION IF NOT EXISTS vector"
```

### 4.2 Schema Check

```bash
# Initialize or verify schema
integration-coworker init-db

# Verify KG tables exist
psql $DATABASE_URL -c "\dt kg.*"
```

### 4.3 LLM Check

```bash
# Verify OpenAI API key works
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  | jq '.data[0].id'
```

### 4.4 Config Status

```bash
# Show current configuration
integration-coworker status
```

Expected output (for real demo):

```
📦 Database:
   Engine: PostgreSQL + pgvector
   Connection: ✓ Connected
   pgvector: ✓ Available

🤖 LLM:
   Mode: Real (gpt-4o)
   API Key: sk-...

✓ All systems configured for production use
```

---

## 5. Demo Script

### Step 1: Verify Environment

```bash
export DATABASE_URL="postgresql://user:pass@localhost:5432/demo_db"
export OPENAI_API_KEY="sk-..."
unset USE_SQLITE USE_MOCK_LLM USE_IN_MEMORY_KG_FALLBACK

integration-coworker status
```

### Step 2: Initialize Database

```bash
integration-coworker init-db
```

### Step 3: First Run — KG Learning

```bash
integration-coworker demo --persist
```

This run:
- Parses the mock payments spec
- Builds Silver API model
- Plans integration workflow
- **Writes learned facts to KG** (via `persist_kg_learning`)

### Step 4: Verify KG Population

```bash
psql $DATABASE_URL -c "SELECT node_type, COUNT(*) FROM kg_nodes GROUP BY node_type"
psql $DATABASE_URL -c "SELECT relation_type, COUNT(*) FROM kg_edges GROUP BY relation_type"
psql $DATABASE_URL -c "SELECT COUNT(*) FROM kg_workflow_steps"
```

Expected:
- Provider, task, workflow_template, endpoint, entity nodes
- Edges connecting them
- Workflow steps for templates

### Step 5: Second Run — GraphRAG Retrieval

```bash
integration-coworker run \
  --spec-ref tests/fixtures/mock_payments_openapi.yaml \
  --task "Create a subscription checkout session" \
  --persist
```

This run:
- `align_task_with_kg` queries KG for templates
- Finds the template from Step 3 via GraphRAG
- Uses it to plan the workflow

### Step 6: Show Template Reuse

Check logs or output for:
```
align_task_with_kg: selected template=mock_payments_checkout_v1 with 5 steps
```

---

## 6. Troubleshooting

### "KG returned no templates"

**Cause**: KG is empty or provider doesn't match.

**Fix**:
1. Verify KG has data: `psql $DATABASE_URL -c "SELECT * FROM kg_nodes LIMIT 5"`
2. Verify provider matches: Check `provider_code` in nodes
3. Run a non-dry-run first to populate KG

### "Using in-memory fallback"

**Cause**: `USE_IN_MEMORY_KG_FALLBACK=1` is set.

**Fix**: Unset it! This should NEVER be set in demo.
```bash
unset USE_IN_MEMORY_KG_FALLBACK
```

### "psycopg not installed"

**Cause**: Postgres dependencies missing.

**Fix**:
```bash
pip install 'psycopg[binary]' psycopg_pool
```

### "Failed to compute embedding"

**Cause**: Mock LLM mode or no API key.

**Fix**: Ensure `OPENAI_API_KEY` is set and `USE_MOCK_LLM` is not set.

### Low similarity scores

**Cause**: Embeddings unavailable (mock mode).

**Note**: This is expected with mock LLM. Scores will be lower but should still exceed the 0.2 threshold for matching templates.

---

## 7. Test Coverage Summary

The GraphRAG system has 8 integration tests in `tests/test_graphrag_integration.py`:

| Test | Verifies |
| ---- | -------- |
| `test_first_run_populates_kg` | KG is empty → run → KG has templates |
| `test_second_run_uses_kg_templates` | Second run finds templates from KG |
| `test_kg_learns_from_multiple_runs` | Multiple tasks all learned |
| `test_dry_run_does_not_populate_kg` | dry_run=True skips KG writes |
| `test_no_in_memory_fallback_by_default` | Fallback disabled by default |
| `test_kg_nodes_table_populated` | Verifies kg_nodes has all node types |
| `test_kg_edges_table_populated` | Verifies kg_edges has relationships |
| `test_kg_workflow_steps_table_populated` | Verifies kg_workflow_steps has steps |

Run all tests:
```bash
USE_SQLITE=true USE_MOCK_LLM=true PYTHONPATH=src \
python -m pytest tests/test_graphrag_integration.py -v
```

---

## 8. Quick Reference — Demo Commands

```bash
# Full demo sequence
export DATABASE_URL="postgresql://user:pass@localhost:5432/demo"
export OPENAI_API_KEY="sk-..."
unset USE_SQLITE USE_MOCK_LLM USE_IN_MEMORY_KG_FALLBACK

integration-coworker status           # Verify config
integration-coworker init-db          # Create schema
integration-coworker demo --persist   # First run (populates KG)
integration-coworker demo --persist   # Second run (uses KG via GraphRAG)
```

---

*Last updated: Based on source analysis of `config/__init__.py`, `cli.py`, `kg/__init__.py`, `align_task_with_kg.py`, `persist_kg_learning.py`, and `test_graphrag_integration.py`.*
