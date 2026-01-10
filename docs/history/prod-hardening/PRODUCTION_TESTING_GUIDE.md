# Production Testing Guide

**Purpose**: Comprehensive guide for running production-themed tests to find and fix bugs before release.

**Last Updated**: 2025-01-17  
**Version**: 2.1

---

## Related Documents

| Document | Purpose |
|----------|---------|
| [PRODUCTION_TEST_MATRIX.md](./PRODUCTION_TEST_MATRIX.md) | Exact commands, env vars, expected outcomes |
| [ADR-0010](./decisions/adr-0010-production-testing-architecture.md) | Architectural decision for tiered testing |
| [TESTING_AND_OBSERVABILITY_AUDIT.md](./TESTING_AND_OBSERVABILITY_AUDIT.md) | Original gap analysis |
| `.github/workflows/ci.yml` | CI pipeline implementation |

---

## Test Summary (as of 2025-01-17)

| Category | Tests | Pass Rate | Runtime |
|----------|-------|-----------|---------|
| Collection | 3352 | 100% | ~4s |
| LLM/Codegen | 219 | 100% | ~16s |
| Redis Cache | 38 | 100% | ~0.1s |
| Config/Graph | 281 | 100% | ~11s |
| Postgres | 41 | 100%* | ~varies |
| E2E | 17 | 100% | ~5min |
| Parallel | 73 | 100% | ~2s |
| Sandbox | 40 | 100% | ~3s |
| HITL/Resume | 1 | 100% | ~10s |

*\* Requires Docker infrastructure*

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Environment Setup](#environment-setup)
3. [Test Categories](#test-categories)
4. [Feature Coverage Matrix](#feature-coverage-matrix)
5. [Step-by-Step Testing Workflow](#step-by-step-testing-workflow)
6. [LangSmith Tracing](#langsmith-tracing)
7. [Bug Tracking](#bug-tracking)
8. [Known Gaps](#known-gaps)

---

## Quick Start

```bash
# 1. Start infrastructure
docker compose up -d db redis

# 2. Set environment
export DATABASE_URL="postgresql://integration:integration@localhost:15432/integration_coworker"
export REDIS_URL="redis://localhost:6379"
export OPENAI_API_KEY="sk-..."  # or ANTHROPIC_API_KEY
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY="..."  # for LangSmith

# 3. Run production smoke tests
SMOKE_TEST_ENABLED=1 pytest tests/prod_readiness/ -v -s

# 4. Run E2E with real LLM
ENABLE_LIVE_LLM_TESTS=true pytest tests/e2e/ -m "e2e and live_llm" -v
```

---

## Environment Setup

### Required Services

| Service | Command | Health Check |
|---------|---------|--------------|
| **Postgres** | `docker compose up -d db` | `docker compose exec db pg_isready` |
| **Redis** | `docker compose up -d redis` | `docker compose exec redis redis-cli ping` |

### Environment Variables

```bash
# === REQUIRED FOR PRODUCTION TESTS ===

# Database (Postgres with pgvector)
export DATABASE_URL="postgresql://integration:integration@localhost:15432/integration_coworker"

# LLM Provider (at least one required)
export OPENAI_API_KEY="sk-..."           # For OpenAI
export ANTHROPIC_API_KEY="sk-ant-..."    # For Anthropic  
export GOOGLE_API_KEY="..."              # For Gemini

# === OPTIONAL BUT RECOMMENDED ===

# Redis Cache
export REDIS_URL="redis://localhost:6379"
export LLM_CACHE_ENABLED="true"

# LangSmith Tracing
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY="..."
export LANGCHAIN_PROJECT="integration-coworker-prod-test"

# === TEST CONTROL ===

# Enable production tests
export SMOKE_TEST_ENABLED="1"
export ENABLE_LIVE_LLM_TESTS="true"

# Budget controls
export SMOKE_LLM_BUDGET="10"           # Max LLM calls per smoke test
export PROD_E2E_MAX_LLM_CALLS="100"    # Global LLM budget
export PROD_E2E_SPEC_TIMEOUT="120"     # Timeout per spec (seconds)
export PROD_E2E_TOTAL_TIMEOUT="600"    # Total workflow timeout

# Codegen profile
export CODEGEN_PROFILE="production"    # Enable all quality gates
export ENABLE_SANDBOX_GATES="true"     # Run sandbox validation
```

---

## Test Categories

### 1. Production Smoke Tests (`tests/prod_readiness/`)

**What they test:**
- Real Postgres connection pool under concurrent load
- Real LLM API calls with budget caps
- Circuit breaker behavior under failure
- Mini workflow with real infrastructure

**How to run:**
```bash
SMOKE_TEST_ENABLED=1 \
DATABASE_URL="postgresql://..." \
OPENAI_API_KEY="sk-..." \
pytest tests/prod_readiness/test_smoke_prod.py -v -s
```

**Tests:**
| Test | Description | Infrastructure |
|------|-------------|----------------|
| `test_postgres_connection_pool_under_load` | 10 concurrent queries | Postgres |
| `test_postgres_pool_timeout_behavior` | Pool timeout config | Postgres |
| `test_real_llm_basic_call` | Single LLM call | OpenAI |
| `test_real_llm_budget_enforcement` | Budget config check | Config |
| `test_circuit_opens_on_repeated_failures` | Circuit breaker trips | None |
| `test_circuit_recovery_after_timeout` | Circuit recovers | None |
| `test_circuit_breaker_with_bad_base_url` | Bad endpoint handling | None |
| `test_mini_workflow_with_real_llm` | Full component check | All |

---

### 2. E2E Tests (`tests/e2e/`)

**What they test:**
- Full pipeline with Docker sandbox
- Real spec processing (Petstore, Twilio)
- TypeScript/Go code generation
- Postgres persistence

**How to run:**
```bash
# Mocked LLM (faster, cheaper)
pytest tests/e2e/ -m "e2e" -v --timeout=300

# Live LLM (real API calls)
ENABLE_LIVE_LLM_TESTS=true pytest tests/e2e/ -m "e2e and live_llm" -v
```

**Tests:**
| Test | Description | LLM Mode |
|------|-------------|----------|
| `test_full_pipeline_petstore_python_mocked_llm` | Full pipeline | Mock |
| `test_full_pipeline_petstore_live_llm` | Full pipeline | Real |
| `test_spec_checksum_integrity` | SHA256 pinning | Mock |
| `test_pinned_spec_postgres_persistence` | DB persistence | Mock |
| `test_provision_requires_lockfile` | Docker gate | N/A |
| `test_validate_runs_without_network` | Sandbox isolation | N/A |
| `test_type_error_detected_in_docker` | Type checking | N/A |

---

### 3. Postgres Tests (`-m postgres`)

**What they test:**
- Schema migrations
- Pool lifecycle
- Checkpoint persistence
- Streaming progress
- KG table structure

**How to run:**
```bash
DATABASE_URL="postgresql://..." pytest tests/ -m "postgres" -v
```

**Key tests:**
- `test_postgres_saver_roundtrip_with_fixture`
- `test_schema_version_table_created`
- `test_postgres_kg_tables_exist`
- `test_stream_chunks_to_silver_idempotent_postgres`
- `test_pool_lifecycle.py` - Connection pool management

---

### 4. Parallel Execution Tests

**What they test:**
- Concurrent LLM calls
- State reducers
- Fan-out/fan-in patterns

**How to run:**
```bash
pytest tests/test_parallel_execution.py tests/graph/test_parallel_*.py -v
```

---

### 5. Sandbox Tests

**What they test:**
- Docker sandbox execution
- Multi-language validation (TypeScript, Go, Python)
- Tier configuration (exp vs prod)

**How to run:**
```bash
# Unit tests (no Docker required)
pytest tests/codegen/test_sandbox_multilang_docker_wiring.py -v

# Integration (requires Docker)
pytest tests/codegen/gates/test_sandbox_integration.py -v -m docker
```

---

### 6. Recovery/Resume Tests

**What they test:**
- Checkpoint/resume flow
- HITL interrupt/continue
- Artifact-backed state

**How to run:**
```bash
# Script-based (more thorough)
python scripts/verify_prod_hitl_resume.py --mock-llm

# Unit tests
pytest tests/test_chunk_checkpoint_resume.py -v
```

---

## Feature Coverage Matrix

| Feature | Test File(s) | Marker | Production Ready |
|---------|--------------|--------|------------------|
| **Postgres Persistence** | `tests/persistence/`, `tests/prod_readiness/` | `postgres` | ✅ |
| **Redis Cache** | `tests/test_llm_cache.py` | - | ✅ |
| **Parallel LLM Calls** | `tests/test_parallel_execution.py` | - | ✅ |
| **Circuit Breaker** | `tests/prod_readiness/`, `tests/llm/` | - | ✅ |
| **Sandbox (Python)** | `tests/test_codegen_sandbox.py` | - | ✅ |
| **Sandbox (TypeScript)** | `tests/codegen/test_sandbox_multilang_*.py` | `docker` | ✅ |
| **Sandbox (Go)** | `tests/e2e/test_go_e2e.py` | `e2e` | ✅ |
| **HITL Interrupt/Resume** | `scripts/verify_prod_hitl_resume.py` | - | ✅ |
| **Checkpoint/Recovery** | `tests/test_chunk_checkpoint_resume.py` | - | ✅ |
| **LangSmith Tracing** | *No dedicated tests* | - | ⚠️ Gap |
| **Multi-Provider LLM** | `tests/test_multi_provider_llm.py` | - | ✅ |
| **Real LLM E2E** | `tests/e2e/test_typescript_e2e.py` | `live_llm` | ✅ |
| **Semantic Validation** | `tests/codegen/test_semantic_validator.py` | - | ✅ |
| **Dynamic Patterns** | `tests/kg/test_pattern_*.py` | - | ✅ |
| **SSRF Protection** | `tests/security/test_ssrf.py` | - | ✅ |
| **Log Redaction** | `tests/security/test_log_redaction.py` | - | ✅ |

---

## Step-by-Step Testing Workflow

### Phase 1: Infrastructure Validation (5 min)

```bash
# 1. Start services
docker compose up -d db redis
sleep 10

# 2. Verify connectivity
docker compose exec db pg_isready
docker compose exec redis redis-cli ping

# 3. Initialize schema
python scripts/init_db_postgres.py

# 4. Run quick config check
coworker check
```

### Phase 2: Unit Tests (10 min)

```bash
# Fast unit tests - should all pass
pytest tests/ -m "not (slow or e2e or postgres or integration_live)" -x -q --timeout=30

# Check for failures
echo "Exit code: $?"
```

### Phase 3: Postgres Tests (15 min)

```bash
# With real Postgres
DATABASE_URL="postgresql://integration:integration@localhost:15432/integration_coworker" \
pytest tests/ -m "postgres" -v --timeout=60
```

### Phase 4: Sandbox Tests (20 min)

```bash
# Requires Docker
pytest tests/codegen/test_sandbox_multilang_docker_wiring.py \
       tests/codegen/gates/test_sandbox_integration.py \
       tests/test_codegen_sandbox.py -v --timeout=120
```

### Phase 5: E2E with Mocked LLM (30 min)

```bash
# Full E2E without API costs
USE_MOCK_LLM=true \
DATABASE_URL="postgresql://..." \
pytest tests/e2e/ -m "e2e" -v --timeout=300
```

### Phase 6: Production Smoke Tests (15 min)

```bash
# Real infrastructure tests
SMOKE_TEST_ENABLED=1 \
DATABASE_URL="postgresql://integration:integration@localhost:15432/integration_coworker" \
OPENAI_API_KEY="sk-..." \
SMOKE_LLM_BUDGET=10 \
pytest tests/prod_readiness/ -v -s
```

### Phase 7: Live LLM E2E (30 min, costs money)

```bash
# Real LLM calls - budget controlled
ENABLE_LIVE_LLM_TESTS=true \
DATABASE_URL="postgresql://..." \
OPENAI_API_KEY="sk-..." \
LANGCHAIN_TRACING_V2=true \
LANGCHAIN_API_KEY="..." \
PROD_E2E_MAX_LLM_CALLS=50 \
pytest tests/e2e/ -m "e2e and live_llm" -v --timeout=600
```

### Phase 8: HITL/Recovery Validation (20 min)

```bash
# Full HITL verification
DATABASE_URL="postgresql://..." \
python scripts/verify_prod_hitl_resume.py --verbose

# Quick mode with mock
python scripts/verify_prod_hitl_resume.py --mock-llm --verbose
```

---

## LangSmith Tracing

### Setup

```bash
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY="lsv2_pt_..."
export LANGCHAIN_PROJECT="integration-coworker-prod-test"
```

### What to Look For

1. **Trace Duration**: Real LLM calls should show >0.5s duration
2. **Error Rates**: Watch for 4xx/5xx in traces
3. **Token Usage**: Check tokens in/out for budget tracking
4. **Node Flow**: Verify correct node execution order
5. **Payload Sizes**: Large payloads may indicate excluded fields leaking

### Viewing Traces

1. Go to [smith.langchain.com](https://smith.langchain.com)
2. Select your project
3. Filter by:
   - `run_type: chain` for workflows
   - `error: true` for failures
   - Time range of your test run

### Example Trace Analysis

```python
# After running tests, inspect traces:
python scripts/inspect_langsmith_traces.py --run-id <run_id>
```

---

## Bug Tracking

### Using bd (Beads)

```bash
# After finding a bug:
bd create "Bug description" -t bug -p 1 --json

# After fixing:
bd close <id> --reason "Fixed in <commit>" --json

# View open bugs:
bd ready --json
```

### Bug Report Format

```markdown
### Bug: [Title]

**Severity**: Critical / High / Medium / Low

**Symptom**: What happened

**Repro Command**:
```bash
<command that reproduces>
```

**Expected**: What should happen

**Actual**: What actually happened

**Log File**: `logs/verify-prod/<timestamp>/`

**Suspected Cause**: Module/function

**Proposed Fix**: Brief description
```

---

## Known Gaps

### 1. Redis Cache Tests (✅ RESOLVED)

**Status**: 38 tests now pass in `tests/test_llm_cache.py` using fakeredis.

**Coverage**:
- Cache roundtrip (set/get)
- TTL expiration
- Statistics tracking
- Clear/reset operations
- Availability checks

**Real Redis Testing** (optional):
```bash
docker compose up -d redis
REDIS_URL=redis://localhost:6379 pytest tests/test_llm_cache.py -v
```

### 2. LangSmith Integration Tests (⚠️ No dedicated tests)

**Impact**: Trace correctness is assumed but not verified.

**Recommended Test**:
```python
# tests/test_langsmith_tracing.py
def test_langsmith_trace_structure():
    """Verify traces have correct structure."""
    # 1. Run workflow with tracing enabled
    # 2. Query LangSmith API for trace
    # 3. Verify node names match WORKFLOW_NODE_ORDER
```

### 3. Multi-Language Code Quality (⚠️ Partial coverage)

**Impact**: TypeScript/Go validation less thorough than Python.

**Gap**: No type-level validation for generated TS/Go code.

### 4. Concurrent Recovery (⚠️ Edge case)

**Impact**: Resuming while another run is in-progress untested.

**Risk**: Possible state corruption or deadlock.

---

## Appendix: Complete Test Commands

```bash
# === ALL PRODUCTION TESTS (Full Suite) ===

# Set all env vars
source .env.prod  # or set manually

# Run in order
pytest tests/ -m "not (slow or e2e or postgres or integration_live)" -x -q
DATABASE_URL="..." pytest tests/ -m "postgres" -v
pytest tests/codegen/test_sandbox_*.py -v
USE_MOCK_LLM=true pytest tests/e2e/ -m "e2e" -v
SMOKE_TEST_ENABLED=1 pytest tests/prod_readiness/ -v -s
ENABLE_LIVE_LLM_TESTS=true pytest tests/e2e/ -m "live_llm" -v
python scripts/verify_prod_hitl_resume.py

# === SINGLE COMMAND (Brave Mode) ===
# Warning: Takes 2+ hours, costs $$ in API calls

SMOKE_TEST_ENABLED=1 \
ENABLE_LIVE_LLM_TESTS=true \
DATABASE_URL="..." \
OPENAI_API_KEY="..." \
LANGCHAIN_TRACING_V2=true \
pytest tests/ -v --timeout=3600
```

---

*Document generated by production audit. Keep updated as new tests are added.*
