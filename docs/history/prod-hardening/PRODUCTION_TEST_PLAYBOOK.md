# Production Test Playbook

> **Goal:** Systematically exercise all production-grade features to find and eliminate bugs before long-running production use.

This playbook documents every production test scenario and how to run it.

---

## Quick Reference: Test Categories

| Test Category | Command | Prerequisites | Est. Duration |
|---------------|---------|---------------|---------------|
| Unit Tests | `make test` | None | ~2 min |
| Postgres Tests | `make test-postgres` | Docker | ~3 min |
| E2E Tests | `pytest -m e2e` | Docker | ~5 min |
| Live LLM Tests | `ENABLE_LIVE_LLM_TESTS=true pytest -m live_llm` | API Key | ~5 min |
| Production Smoke | `SMOKE_TEST_ENABLED=1 pytest tests/prod_readiness/` | Docker + API Key | ~3 min |
| Soak Tests | `./scripts/soak.sh 30` | None | 30 min |
| Full Demo Harness | `./scripts/demo-final-showcase.sh` | Docker + API Key | ~20 min |

---

## 1. Infrastructure Setup

### 1.1 Start Docker Services

```bash
# Start Postgres (port 15432) and Redis (port 6379)
docker-compose up -d

# Wait for services to be healthy
make db-wait

# Verify services
docker-compose ps
```

**Expected Output:**
```
NAME                           STATUS              PORTS
integration-coworker-db-1      healthy             0.0.0.0:15432->5432/tcp
integration-coworker-redis-1   healthy             0.0.0.0:6379->6379/tcp
```

### 1.2 Environment Variables Reference

```bash
# Core Database
export DATABASE_URL="postgresql://integration:integration@localhost:15432/integration_coworker"
export REDIS_URL="redis://localhost:6379"

# SQLite Alternative (no Docker needed)
export USE_SQLITE=true
export SQLITE_PATH="/tmp/test.db"

# LLM Configuration
export OPENAI_API_KEY="sk-..."
export USE_MOCK_LLM="false"  # Set to "true" for deterministic tests

# LangSmith Tracing (optional but recommended)
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY="lsv2_..."
export LANGCHAIN_PROJECT="integration-coworker-prod-tests"

# Validation Profiles
export VALIDATION_PROFILE="offline"  # or "record" or "live"
export ALLOW_LIVE="1"  # Required for VALIDATION_PROFILE=live

# Test Gates
export SMOKE_TEST_ENABLED="1"
export ENABLE_LIVE_LLM_TESTS="true"
export POSTGRES_TESTS_REQUIRED="true"
```

---

## 2. Test Scenarios by Feature

### 2.1 Real Postgres Persistence (43 Tests)

Tests database operations with real PostgreSQL.

```bash
# Start Postgres first
make db-up && make db-wait

# Run all postgres-marked tests
DATABASE_URL=postgresql://integration:integration@localhost:15432/integration_coworker \
POSTGRES_TESTS_REQUIRED=true \
pytest tests/ -m postgres -v

# Run specific pattern learning tests
DATABASE_URL=postgresql://integration:integration@localhost:15432/integration_coworker \
pytest tests/test_pattern_learning_postgres.py -v
```

**What to look for:**
- Connection pool exhaustion errors
- Transaction deadlocks
- Schema migration issues
- JSON field serialization errors

### 2.2 Real LLM Calls with LangSmith Tracing

Tests live LLM integration with full observability.

```bash
# Enable live LLM tests
export ENABLE_LIVE_LLM_TESTS=true
export OPENAI_API_KEY="sk-..."
export USE_MOCK_LLM=false

# Enable LangSmith tracing for debugging
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY="lsv2_..."
export LANGCHAIN_PROJECT="prod-test-$(date +%Y%m%d)"

# Run live LLM tests
pytest tests/ -m live_llm -v -s

# Run E2E tests with live LLM
pytest tests/e2e/test_typescript_e2e.py::TestFullPipelineE2E::test_full_pipeline_petstore_live_llm -v -s
```

**LangSmith Dashboard:** After tests complete, view traces at https://smith.langchain.com

**What to look for:**
- API rate limiting errors
- Token count anomalies
- Hallucination patterns in generated code
- Latency spikes

### 2.3 Redis Cache Integration

Tests LLM response caching.

```bash
# Ensure Redis is running
docker-compose up -d redis

# Run with cache enabled
export REDIS_URL="redis://localhost:6379"

# Test cache hit/miss behavior
pytest tests/test_llm_cache.py -v

# Verify cache operations
redis-cli -p 6379 KEYS "*llm*" | head -10
redis-cli -p 6379 INFO stats | grep -E "hits|misses"
```

**What to look for:**
- Cache key collisions
- Serialization errors on complex responses
- Cache eviction under memory pressure

### 2.4 Parallel Execution Benchmarks

Tests concurrent code generation with semaphore-based rate limiting.

```bash
# Run concurrency stress tests
pytest tests/integration/test_concurrency_stress.py -v

# Run with timing assertions
pytest tests/ -m perf_smoke -v

# Run 100-request burst test (from test_concurrency_stress.py)
DATABASE_URL=postgresql://integration:integration@localhost:15432/integration_coworker \
pytest tests/integration/test_concurrency_stress.py::test_concurrent_persist_100_requests -v -s
```

**What to look for:**
- Semaphore exhaustion (max concurrent = 5 by default)
- Connection pool timeout under load
- Memory growth during parallel operations

### 2.5 Code Quality Validation (Sandbox)

Tests generated code passes all quality gates.

```bash
# Run sandbox validation tests
pytest tests/ -k "sandbox" -v

# Run multilang sandbox tests (Python/TypeScript/Go)
pytest tests/test_sandbox_multilang_docker_wiring.py -v
```

**Quality Gates Checked:**
| Gate | Tool | Hard Fail |
|------|------|-----------|
| Lint | ruff | ✓ |
| Types | mypy | ✓ |
| Security | bandit | ✓ |
| Tests | pytest | Configurable |
| Coverage | pytest-cov | Configurable (default 60%) |

### 2.6 Recovery/Resume Testing

Tests checkpoint persistence and crash recovery.

```bash
# Run checkpoint resume tests
pytest tests/test_chunk_checkpoint_resume.py -v

# Test with cancellation simulation
pytest tests/test_chunk_checkpoint_resume.py::TestCancellationDuringStreaming -v
```

**What to look for:**
- Progress checkpoints saved correctly
- Resume skips already-persisted chunks
- Idempotency on restart

### 2.7 Multi-Language Code Generation

Tests TypeScript, Go, and other language output.

```bash
# Run TypeScript E2E tests
pytest tests/e2e/test_typescript_e2e.py -v -m docker

# Run multi-language sandbox tests
pytest tests/test_sandbox_multilang_docker_wiring.py -v

# Run with live LLM for realistic output
ENABLE_LIVE_LLM_TESTS=true pytest tests/e2e/test_typescript_e2e.py::TestDockerGatesUnit -v
```

**Languages Supported:**
- Python (ruff, mypy, bandit, pytest)
- TypeScript (tsc, eslint, jest)
- Go (go build, golangci-lint, go test)

---

## 3. Full Production Test Suites

### 3.1 Production Smoke Tests

Real infrastructure validation with budget caps.

```bash
# Set required environment
export SMOKE_TEST_ENABLED=1
export DATABASE_URL="postgresql://integration:integration@localhost:15432/integration_coworker"
export OPENAI_API_KEY="sk-..."
export SMOKE_LLM_BUDGET=5     # Max LLM calls
export SMOKE_TIMEOUT=120      # Max seconds

# Run smoke tests
pytest tests/prod_readiness/test_smoke_prod.py -v -s

# Run specific test classes
pytest tests/prod_readiness/test_smoke_prod.py::TestRealPostgresConcurrency -v -s
pytest tests/prod_readiness/test_smoke_prod.py::TestRealLLMWithBudget -v -s
pytest tests/prod_readiness/test_smoke_prod.py::TestCircuitBreakerFailure -v -s
```

**Tests Include:**
- `TestRealPostgresConcurrency`: Pool under concurrent load
- `TestRealLLMWithBudget`: Real API calls with cost tracking
- `TestCircuitBreakerFailure`: Failure recovery mechanisms
- `TestMiniWorkflow`: End-to-end mini workflow

### 3.2 Soak Test (Resource Leak Detection)

Extended runtime test for memory/thread/FD leaks.

```bash
# 30-minute soak test (default)
./scripts/soak.sh

# Quick 5-minute check
./scripts/soak.sh 5 --quick

# 60-minute extended soak
./scripts/soak.sh 60

# With parallel runs
SOAK_PARALLEL_RUNS=4 ./scripts/soak.sh 30
```

**Thresholds Monitored:**
| Metric | Threshold | Alert If Exceeded |
|--------|-----------|-------------------|
| Thread count | 50 | Resource leak |
| File descriptors | 200 | FD leak |
| Memory growth | 100MB | Memory leak |

### 3.3 Full Demo Harness

Production-grade demonstration with all features.

```bash
# Set API key
export OPENAI_API_KEY="sk-..."

# Full demo (15 specs, all features)
./scripts/demo-final-showcase.sh

# Quick demo (3 specs)
./scripts/demo-final-showcase.sh --quick

# Fresh start (clear all caches)
./scripts/demo-final-showcase.sh --fresh

# Skip per-spec validation (faster, less safe)
./scripts/demo-final-showcase.sh --no-per-spec-validation
```

**Features Demonstrated:**
1. API Spec Processing - All 15 specs in `specs/`
2. Workflow Visualization - 21-node LangGraph
3. KG Learning - Template reuse across runs
4. Code Generation - Real output to target repo
5. Sandbox Validation - Per-spec validation with all 5 gates

---

## 4. Specialized Test Scenarios

### 4.1 Contract Testing (Schemathesis + Prism)

Validate generated code against OpenAPI contracts.

```bash
# Install Prism CLI (if not installed)
npm install -g @stoplight/prism-cli

# Run contract tests
pytest tests/ -m contract -v

# Manual contract test
prism mock specs/petstore_v3.yaml -p 4010 &
schemathesis run specs/petstore_v3.yaml --base-url http://localhost:4010
```

### 4.2 Live Integration Tests

Tests against real external APIs (requires API keys).

```bash
# Enable live integration tests
export VALIDATION_PROFILE=live
export ALLOW_LIVE=1
export LIVE_HOST_ALLOWLIST="api.stripe.com,api.twilio.com"

# Run live integration tests
pytest tests/ -m integration_live -v
```

### 4.3 Performance Regression

Timing assertions for critical paths.

```bash
# Run performance smoke tests
pytest tests/ -m perf_smoke -v --tb=short

# Run with timing output
pytest tests/ -m perf_smoke -v --durations=20
```

---

## 5. CI/CD Test Matrix

| Job | Markers | Infra Required | Secrets Required |
|-----|---------|----------------|------------------|
| `unit-tests` | `not (postgres or e2e)` | None | None |
| `postgres-tests` | `postgres` | Postgres service | None |
| `e2e-tests` | `e2e` | Docker | None |
| `live-llm-tests` | `live_llm` | Docker | `OPENAI_API_KEY` |
| `prod-smoke` | N/A | Postgres, Redis | All API keys |

---

## 6. Debugging Failed Tests

### 6.1 LangSmith Trace Analysis

1. Set `LANGCHAIN_TRACING_V2=true` before running tests
2. After failure, go to https://smith.langchain.com
3. Filter by project name
4. Examine trace for:
   - Token counts
   - Latency per step
   - Input/output for each LLM call
   - Error messages

### 6.2 Database Debugging

```bash
# Connect to test database
docker-compose exec db psql -U integration -d integration_coworker

# Check tables
\dt

# Check recent runs
SELECT id, spec_uri, status, created_at FROM workflow_runs ORDER BY created_at DESC LIMIT 5;

# Check checkpoints
SELECT COUNT(*) FROM checkpoints;
```

### 6.3 Redis Cache Debugging

```bash
# Connect to Redis
redis-cli -p 6379

# Check cache keys
KEYS "*llm*"

# Check memory usage
INFO memory

# Clear cache (use with caution)
FLUSHDB
```

---

## 7. Bug Hunting Checklist

Run these scenarios when looking for bugs:

- [ ] **Concurrency:** Run 100-request burst test multiple times
- [ ] **Memory:** Run 30-minute soak test, check for growth
- [ ] **Recovery:** Kill process mid-workflow, verify resume
- [ ] **Cache:** Run same spec twice, verify cache hit
- [ ] **Circuit Breaker:** Set invalid API key, verify circuit opens
- [ ] **Multi-language:** Generate TypeScript + Go, verify both compile
- [ ] **Large Spec:** Use `stripe_api.json` (large spec), check memory
- [ ] **Parallel:** Run 3 specs simultaneously, check for race conditions

---

## 8. Environment Validation Script

Run this before starting production tests:

```bash
#!/bin/bash
echo "=== Environment Validation ==="

# Check Docker
docker info >/dev/null 2>&1 && echo "✓ Docker running" || echo "✗ Docker not running"

# Check Postgres
docker-compose exec db pg_isready >/dev/null 2>&1 && echo "✓ Postgres healthy" || echo "✗ Postgres not ready"

# Check Redis
redis-cli -p 6379 ping >/dev/null 2>&1 && echo "✓ Redis healthy" || echo "✗ Redis not ready"

# Check API Key
[ -n "$OPENAI_API_KEY" ] && echo "✓ OPENAI_API_KEY set" || echo "✗ OPENAI_API_KEY missing"

# Check LangSmith
[ -n "$LANGCHAIN_API_KEY" ] && echo "✓ LANGCHAIN_API_KEY set" || echo "○ LANGCHAIN_API_KEY not set (optional)"

# Check Python
python --version 2>&1

# Check installed packages
pip show langchain langgraph pytest >/dev/null 2>&1 && echo "✓ Core packages installed" || echo "✗ Missing packages"

echo "=== Test Collection ==="
pytest --collect-only -q 2>/dev/null | tail -3
```

---

## Summary

For comprehensive production bug hunting:

1. **Start with Docker services:** `docker-compose up -d`
2. **Run unit tests first:** `make test`
3. **Run Postgres tests:** `make test-postgres`
4. **Run smoke tests:** `SMOKE_TEST_ENABLED=1 pytest tests/prod_readiness/`
5. **Run soak test:** `./scripts/soak.sh 30`
6. **Run full demo:** `./scripts/demo-final-showcase.sh --quick`

Total estimated time: ~1 hour for complete validation.
