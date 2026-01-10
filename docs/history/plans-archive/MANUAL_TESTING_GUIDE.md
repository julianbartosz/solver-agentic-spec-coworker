# Manual Testing & Production Usage Guide

> **Goal:** Comprehensive guide for manually testing the Integration Co-Worker, understanding its capabilities, and validating production readiness.

---

## Table of Contents

1. [What is the Integration Co-Worker?](#1-what-is-the-integration-co-worker)
2. [Production User Workflow](#2-production-user-workflow)
3. [Available Tools for Testing & Monitoring](#3-available-tools-for-testing--monitoring)
4. [Manual Testing Scenarios](#4-manual-testing-scenarios)
5. [Using Streamlit UI](#5-using-streamlit-ui)
6. [Using LangSmith for Debugging](#6-using-langsmith-for-debugging)
7. [Production Readiness Checklist](#7-production-readiness-checklist)
8. [Troubleshooting Guide](#8-troubleshooting-guide)

---

## 1. What is the Integration Co-Worker?

The Integration Co-Worker is an **AI-powered code generation system** that:

### Core Capabilities

| Capability | Description |
|------------|-------------|
| **Spec Parsing** | Reads OpenAPI 3.x specs (YAML/JSON) from files or URLs |
| **API Understanding** | Extracts endpoints, schemas, entities, relationships (Silver Model) |
| **Task Planning** | Uses AI to understand your integration task and match to known patterns |
| **Code Generation** | Produces Python/TypeScript/Go client code, flows, and tests |
| **Repository Integration** | Wires generated code into your existing project structure |
| **Knowledge Learning** | Remembers patterns and improves over time via feedback |

### Supported Input Specs

| Format | Status | Notes |
|--------|--------|-------|
| OpenAPI 3.0/3.1 | ✅ Full | Primary supported format |
| Swagger 2.0 | ⚠️ Partial | Auto-converts to OpenAPI 3 |
| AsyncAPI | 🔬 Experimental | Event-driven APIs |
| GraphQL | 🔬 Experimental | Schema introspection |

### Generated Output

| Artifact Type | Example |
|---------------|---------|
| **Client Code** | `integrations/clients/stripe.py` |
| **Flow/Orchestration** | `integrations/flows/stripe_checkout.py` |
| **Unit Tests** | `tests/integrations/test_stripe_checkout.py` |
| **Markdown Report** | Summary of what was generated and why |

---

## 2. Production User Workflow

### 2.1 Typical Usage Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  1. Provide     │────▶│  2. AI Plans &   │────▶│  3. Review &    │
│  API Spec +     │     │  Generates Code  │     │  Integrate      │
│  Task Desc      │     │                  │     │                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

### 2.2 Step-by-Step Production Usage

#### Step 1: Setup Environment

```bash
# Activate Python environment
source .venv311/bin/activate

# Start infrastructure
docker-compose up -d

# Set required API keys
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."  # For planning/codegen

# Optional: Enable tracing
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY="lsv2_..."
export LANGCHAIN_PROJECT="my-project"
```

#### Step 2: Run Integration Generation

**Option A: CLI (Most Common)**
```bash
# Generate integration for Stripe
python -m integration_coworker.cli run \
  --spec-ref specs/stripe_api.json \
  --task "Create a checkout session with line items and redirect URLs" \
  --provider stripe \
  --repo-root /path/to/your/project \
  --verbose
```

**Option B: Streamlit UI (Visual)**
```bash
python -m integration_coworker.cli ui
# Opens browser at http://localhost:8501
```

**Option C: Python API (Programmatic)**
```python
from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions

result = design_and_generate_integration(
    spec_refs=["specs/stripe_api.json"],
    task_description="Create a checkout session with line items",
    provider_code="stripe",
    repo_root="/path/to/your/project",
    options=IntegrationOptions(
        dry_run=False,           # Actually write files
        repo_integration_enabled=True,
    )
)

print(f"Generated {len(result.code_artifacts)} artifacts")
```

#### Step 3: Review Output

After a successful run, you'll see:

```
✅ Integration completed! Run ID: run_abc123xyz

📊 Summary:
  Provider: stripe
  Task: create_checkout_session
  Endpoints: 3
  Schemas: 8
  Artifacts: 3

📁 Files Created:
  ➕ integrations/clients/stripe.py
  ➕ integrations/flows/stripe_checkout.py
  ➕ tests/integrations/test_stripe_checkout.py

🔗 LangSmith: https://smith.langchain.com (project: my-project)
```

#### Step 4: Provide Feedback (Optional but Recommended)

```bash
# Rate the output quality
python -m integration_coworker.cli feedback \
  --run-id run_abc123xyz \
  --rating 8 \
  --comment "Good code structure, but missed pagination"
```

This feedback improves future generations via the Knowledge Graph.

---

## 3. Available Tools for Testing & Monitoring

### 3.1 CLI Commands Reference

| Command | Purpose | Example |
|---------|---------|---------|
| `run` | Main workflow execution | `python -m integration_coworker.cli run -s spec.yaml -t "task"` |
| `demo` | Quick demo with mock spec | `python -m integration_coworker.cli demo` |
| `demo-v1` | Golden path demo with timing | `python -m integration_coworker.cli demo-v1` |
| `ui` | Launch Streamlit UI | `python -m integration_coworker.cli ui` |
| `status` | Show config and DB status | `python -m integration_coworker.cli status` |
| `health` | Health check all components | `python -m integration_coworker.cli health --verbose` |
| `init-db` | Initialize schema and seed KG | `python -m integration_coworker.cli init-db` |
| `resume` | Resume interrupted run | `python -m integration_coworker.cli resume --run-id <id>` |
| `feedback` | Record human feedback | `python -m integration_coworker.cli feedback --run-id <id> --rating 8` |
| `feedback-sync` | Sync feedback from LangSmith | `python -m integration_coworker.cli feedback-sync` |
| `kg-dump` | Export knowledge graph | `python -m integration_coworker.cli kg-dump -o kg.json` |
| `kg-query` | Query knowledge graph | `python -m integration_coworker.cli kg-query -e "payment"` |
| `kg-confidence` | Display pattern confidence | `python -m integration_coworker.cli kg-confidence` |
| `cache-stats` | Show LLM cache statistics | `python -m integration_coworker.cli cache-stats` |
| `cache-clear` | Clear LLM response cache | `python -m integration_coworker.cli cache-clear` |

### 3.2 Health Check

```bash
```bash
# Quick health check
python -m integration_coworker.cli health

# Verbose with LLM connectivity test
python -m integration_coworker.cli health --verbose --check-llm
```
```

**Expected Output:**
```
Integration Co-Worker Health Check
==================================

Database:
  ✅ PostgreSQL connected
  ✅ Schema initialized
  ✅ pgvector extension enabled

LLM Providers:
  ✅ OpenAI: API key configured
  ✅ Anthropic: API key configured

Cache:
  ✅ Redis: Connected (localhost:6379)

LangSmith:
  ✅ Tracing enabled (project: my-project)

Overall: HEALTHY ✅
```

### 3.3 Status Command

```bash
python -m integration_coworker.cli status
```

Shows current configuration, database connection, and recent runs.

---

## 4. Manual Testing Scenarios

### 4.1 Quick Smoke Test (No API Keys)

```bash
# Uses mock LLM and SQLite - tests the full pipeline
USE_SQLITE=true USE_MOCK_LLM=true python -m integration_coworker.cli demo-v1
```

**Expected:** Complete run with timing table, no errors.

### 4.2 Real LLM Test (Requires API Keys)

```bash
# Minimal real test
export OPENAI_API_KEY="sk-..."
python -m integration_coworker.cli run \
  -s tests/fixtures/mock_payments_openapi.yaml \
  -t "Create a checkout session" \
  --dry-run \
  --verbose
```

### 4.3 Full Production Test (All Features)

```bash
# Start infrastructure
docker-compose up -d

# Set all keys
export DATABASE_URL="postgresql://integration:integration@localhost:15432/integration_coworker"
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY="lsv2_..."

# Run against a real spec
python -m integration_coworker.cli run \
  -s specs/stripe_api.json \
  -t "Create a payment intent with amount and currency" \
  --provider stripe \
  --verbose
```

### 4.4 Multi-Spec Test

```bash
# Test handling multiple specs
python -m integration_coworker.cli run \
  -s specs/stripe_api.json \
  -s specs/twilio_messaging_v1.json \
  -t "Create a payment and send confirmation SMS" \
  --verbose
```

### 4.5 Repository Integration Test

```bash
# Create a test target repo
mkdir -p /tmp/test-repo/src/integrations
cd /tmp/test-repo && git init

# Run with repo integration
python -m integration_coworker.cli run \
  -s specs/petstore_v3.json \
  -t "List pets and create a new pet" \
  --provider petstore \
  --repo-root /tmp/test-repo \
  --verbose

# Check generated files
find /tmp/test-repo -name "*.py" -type f
```

---

## 5. Using Streamlit UI

### 5.1 Launch UI

```bash
python -m integration_coworker.cli ui
# Opens http://localhost:8501
```

### 5.2 UI Features

| Tab | Purpose |
|-----|---------|
| **📊 Run Status** | View current/last run results, Silver/Gold model metrics |
| **📁 Artifacts** | Browse generated code with syntax highlighting, download files |
| **🔀 Graph Trace** | Visualize workflow execution order and node status |
| **⚠️ Errors & Recovery** | View errors, retry/skip/restart failed runs |

### 5.3 UI Workflow

1. **Configure Inputs** (Sidebar):
   - Choose spec source (URL/File/Demo)
   - Enter task description
   - Set options (dry run, verbose, etc.)

2. **Run Integration**:
   - Click "🚀 Run Integration"
   - Watch progress spinner

3. **Review Results**:
   - Check Run Status tab for metrics
   - Browse Artifacts tab for code
   - Download individual files

4. **Handle Errors**:
   - If failed, go to Errors tab
   - Click Retry, Skip, or Restart

### 5.4 UI Screenshot Walkthrough

```
┌─────────────────────────────────────────────────────────────────────┐
│  🔧 Agentic Integration Co-Worker                                   │
├──────────────┬──────────────────────────────────────────────────────┤
│ SIDEBAR      │  📊 Run Status | 📁 Artifacts | 🔀 Graph | ⚠️ Errors │
│              │ ──────────────────────────────────────────────────── │
│ Configuration│  Run: run_abc123                                     │
│              │                                                      │
│ API Spec:    │  ┌────────────┬───────────┬───────────┐             │
│ [URL    ▼]   │  │ Provider   │ Task      │ Artifacts │             │
│              │  │ stripe     │ checkout  │ 3         │             │
│ Task:        │  └────────────┴───────────┴───────────┘             │
│ [Create a    │                                                      │
│  checkout..] │  📦 Silver API Model                                 │
│              │  ┌────────────┬───────────┬───────────┐             │
│ Options:     │  │ Endpoints  │ Schemas   │ Entities  │             │
│ ☑ Dry Run    │  │ 5          │ 12        │ 3         │             │
│ ☐ Verbose    │  └────────────┴───────────┴───────────┘             │
│              │                                                      │
│ [🚀 Run]     │  ✅ Completed Steps: plan_run → ingest_spec → ...    │
└──────────────┴──────────────────────────────────────────────────────┘
```

---

## 6. Using LangSmith for Debugging

### 6.1 Setup LangSmith

```bash
# Enable tracing
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY="lsv2_pt_..."  # From smith.langchain.com
export LANGCHAIN_PROJECT="integration-coworker-testing"
```

### 6.2 View Traces

1. **Open LangSmith**: https://smith.langchain.com
2. **Select Project**: Choose your project from dropdown
3. **View Runs**: See list of recent runs with status

### 6.3 What to Look For in Traces

| Aspect | What to Check |
|--------|---------------|
| **Latency** | Which nodes are slow? (>10s may indicate issues) |
| **Token Usage** | Are prompts too large? (>8K tokens may cause truncation) |
| **LLM Outputs** | Did the model follow instructions? Any hallucinations? |
| **Errors** | Where did failures occur? What was the input/output? |
| **Retries** | Is the circuit breaker triggering? |

### 6.4 Debugging with Traces

**Scenario: Code generation is poor quality**

1. Open the run in LangSmith
2. Navigate to `generate_code_and_tests` node
3. Expand to see:
   - Input prompt (task description, endpoints, policies)
   - LLM output (generated code)
   - Timing and token counts
4. Check:
   - Is the prompt missing context?
   - Is the model hallucinating endpoint names?
   - Are there JSON parsing errors?

### 6.5 Syncing Feedback from LangSmith

If you've rated runs in LangSmith UI:

```bash
# Sync last 24 hours of feedback to local KG
python -m integration_coworker.cli feedback-sync

# Sync specific time range
python -m integration_coworker.cli feedback-sync --hours 168  # Last week
```

---

## 7. Production Readiness Checklist

### 7.1 Infrastructure Checklist

| Component | Check | Command |
|-----------|-------|---------|
| ☐ PostgreSQL | Running with pgvector | `docker-compose ps db` |
| ☐ Redis (optional) | Running for LLM cache | `redis-cli -p 6379 ping` |
| ☐ Schema initialized | Tables exist | `python -m integration_coworker status` |
| ☐ API keys set | OpenAI + Anthropic | `python -m integration_coworker health` |

### 7.2 Functional Checklist

| Test | Command | Pass Criteria |
|------|---------|---------------|
| ☐ Mock demo | `USE_MOCK_LLM=true python -m integration_coworker.cli demo-v1` | Completes without error |
| ☐ Real LLM | `python -m integration_coworker.cli demo` | Generates valid code |
| ☐ DB persistence | `python -m integration_coworker.cli run -s spec.yaml -t "task"` | `persisted_ids` not empty |
| ☐ Repo integration | `python -m integration_coworker.cli run ... --repo-root /path` | Files created in repo |
| ☐ Resume from crash | Kill mid-run, then `resume` | Picks up where left off |

### 7.3 Long-Running Production Readiness

| Test | How to Run | Pass Criteria |
|------|------------|---------------|
| ☐ Soak test (30 min) | `./scripts/soak.sh 30` | No memory leaks, threads stable |
| ☐ 100-request burst | `pytest tests/integration/test_concurrency_stress.py` | All complete, no deadlocks |
| ☐ Circuit breaker | Set bad API key, run | Opens after 3 failures |
| ☐ Cache effectiveness | Run same spec twice | Second run shows cache hits |

### 7.4 Monitoring Checklist

| Item | Verification |
|------|--------------|
| ☐ LangSmith traces visible | Check https://smith.langchain.com |
| ☐ Prometheus metrics (if enabled) | `/metrics` endpoint returns data |
| ☐ Log output readable | `--verbose` produces structured logs |
| ☐ Error messages actionable | Errors include context and suggestions |

---

## 8. Troubleshooting Guide

### 8.1 Common Issues

| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| "No module named 'integration_coworker'" | Not installed | `pip install -e .` |
| "Failed to connect to database" | Postgres not running | `docker-compose up -d db` |
| "Anthropic API returned 401" | Invalid API key | Check `ANTHROPIC_API_KEY` |
| "LLM output failed AST validation" | LLM generated invalid code | Check LangSmith trace, may need retry |
| "CircuitOpenError" | Too many LLM failures | Wait for recovery timeout, check API key |
| "No endpoints extracted" | Spec parsing failed | Check spec is valid OpenAPI 3.x |
| LangSmith shows 0.00s | Mock mode enabled | `unset USE_MOCK_LLM` |

### 8.2 Debug Commands

```bash
# Full verbose logging
python -m integration_coworker.cli run -s spec.yaml -t "task" --verbose 2>&1 | tee debug.log

# Check specific components
grep -E "error|fail|warn" debug.log
grep -E "endpoint|schema|entity" debug.log  # Extraction issues
grep -E "circuit|retry|fallback" debug.log  # LLM issues

# Database debugging
docker-compose exec db psql -U integration -d integration_coworker
\dt  # List tables
SELECT COUNT(*) FROM workflow_runs;
SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT 5;
```

### 8.3 Getting Help

1. **Check logs**: Run with `--verbose`
2. **Check LangSmith**: View the trace for detailed LLM interactions
3. **Check status**: `python -m integration_coworker.cli status`
4. **Check health**: `python -m integration_coworker.cli health --verbose --check-llm`

---

## Summary: Quick Reference Commands

```bash
# === Setup ===
source .venv311/bin/activate
docker-compose up -d
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."

# === Testing ===
# Quick test (no API keys)
USE_SQLITE=true USE_MOCK_LLM=true python -m integration_coworker.cli demo-v1

# Real test (with API keys)
python -m integration_coworker.cli demo --verbose

# Full test with LangSmith
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY="lsv2_..."
python -m integration_coworker.cli run -s specs/stripe_api.json -t "Create checkout" --verbose

# === Monitoring ===
python -m integration_coworker.cli health --verbose
python -m integration_coworker.cli status
python -m integration_coworker.cli cache-stats

# === UI ===
python -m integration_coworker.cli ui

# === After Running ===
python -m integration_coworker.cli feedback --run-id <id> --rating 8
python -m integration_coworker.cli feedback-sync
```
