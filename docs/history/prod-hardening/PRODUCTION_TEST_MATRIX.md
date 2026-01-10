# Production Test Matrix

**Document Version**: 2.0  
**Last Updated**: 2025-01-17  
**Status**: Living Document - Updated per test run results

---

## Executive Summary

This document provides a comprehensive test matrix for the Integration Co-Worker project. It catalogs all test categories, their exact commands, environment requirements, and expected outcomes.

| Category | Tests | Pass Rate | Last Run | Critical Path |
|----------|-------|-----------|----------|---------------|
| Collection Sanity | 3352 | 100% | 2025-01-17 | ✅ Blocking |
| Unit Fast | ~2800 | ~98% | 2025-01-17 | ✅ Blocking |
| LLM/Codegen | 219 | 100% | 2025-01-17 | ✅ Blocking |
| Redis Cache | 38 | 100% | 2025-01-17 | ⚠️ Non-blocking |
| Postgres | 41 | 100%* | 2025-01-17 | ⚠️ Non-blocking |
| E2E | 17 | 100% | 2025-01-17 | ✅ Blocking |
| Parallel Execution | 73 | 100% | 2025-01-17 | ✅ Blocking |
| Sandbox | 40 | 100% | 2025-01-17 | ✅ Blocking |
| Prod Smoke | 4 | 100%* | 2025-01-17 | ⚠️ Secrets-gated |
| HITL/Resume | 1 | 100% | 2025-01-17 | ⚠️ Non-blocking |

*Note: Postgres and Prod Smoke tests require infrastructure (Docker services or secrets)

---

## Test Categories

### 1. Collection Sanity (Pre-flight Gate)

**Purpose**: Ensure all test files import cleanly without errors.

```bash
# Command
pytest --collect-only tests/

# Environment
USE_SQLITE=true

# Expected Output
"X tests collected" (no import errors)

# Current Status
3352 tests collected in ~4s
```

**Pass Criteria**: Zero collection errors. Any import failure blocks all subsequent tests.

---

### 2. Unit Fast (Core Quality Gate)

**Purpose**: Fast unit tests with no external dependencies. Target: <60s.

```bash
# Command
pytest tests/ \
  -m "not (postgres or e2e or slow or integration_live or docker)" \
  --timeout=60 \
  -x -q

# Environment
USE_SQLITE=true
USE_MOCK_LLM=true
VALIDATION_PROFILE=offline

# Expected Output
~2800 tests pass, <60s total runtime
```

**CI Job**: `unit-fast` (runs on Python 3.11, 3.12, 3.13)

---

### 3. LLM & Codegen Tests

**Purpose**: Validate LLM client, caching, and code generation pipelines.

```bash
# Command
pytest tests/llm tests/codegen tests/prod_readiness -v --timeout=60

# Environment
USE_SQLITE=true
USE_MOCK_LLM=true

# Expected Output
219 passed, 17 skipped (prod_smoke tests skip without secrets)
```

**Key Test Files**:
- `tests/llm/test_exceptions.py` - Error handling
- `tests/llm/test_budget.py` - Call budget enforcement
- `tests/codegen/test_sandbox*.py` - Sandbox execution
- `tests/prod_readiness/test_smoke_prod.py` - Production smoke

---

### 4. Redis Cache Tests

**Purpose**: Validate LLM response caching layer.

```bash
# Command
pytest tests/test_llm_cache.py -v

# Environment
USE_SQLITE=true
# Uses fakeredis - no real Redis needed

# Expected Output
38 passed
```

**Real Redis Testing** (optional):
```bash
# Start Redis
docker compose up -d redis

# Run with real Redis
REDIS_URL=redis://localhost:6379 pytest tests/test_llm_cache.py -v
```

---

### 5. Postgres Integration Tests

**Purpose**: Validate PostgreSQL persistence layer, knowledge graph, pattern learning.

```bash
# Prerequisites
docker compose up -d db
make db-wait

# Command
DATABASE_URL=postgresql://integration:integration@localhost:15432/integration_coworker \
POSTGRES_TESTS_REQUIRED=true \
pytest tests/test_pattern_learning_postgres.py -v -m postgres

# Expected Output
41 passed, 15 skipped
```

**Extended Postgres Tests**:
```bash
# Full Postgres test suite
pytest tests/ -m postgres -v --timeout=120
```

**Key Test Files**:
- `tests/test_pattern_learning_postgres.py` - Pattern persistence
- `tests/test_postgres_integration.py` - Connection/pool management
- `tests/test_file_integration_postgres.py` - File persistence with Postgres

---

### 6. E2E Tests (Docker Required)

**Purpose**: End-to-end workflow tests with real infrastructure.

```bash
# Prerequisites
docker compose up -d  # Start all services

# Command
USE_MOCK_LLM=true \
pytest tests/e2e -m "e2e and docker" -v --timeout=300

# Expected Output
17 passed
```

**E2E Test Files**:
- `tests/e2e/test_public_spec_e2e.py` - Public API spec processing
- `tests/e2e/test_typescript_e2e.py` - TypeScript output generation
- `tests/e2e/test_go_e2e.py` - Go output generation

---

### 7. Parallel Execution Tests

**Purpose**: Validate concurrent workflow execution and thread safety.

```bash
# Command
USE_SQLITE=true \
pytest tests/test_parallel_execution.py -v

# Expected Output
73 passed
```

**Environment Variables for Parallel Mode**:
```bash
PARALLEL_WORKFLOW=true      # Enable parallel node execution
PARALLEL_TIMEOUT=300        # Max seconds for parallel branches
```

---

### 8. Sandbox Validation Tests

**Purpose**: Test code execution sandbox with all 5 gates.

```bash
# Command
USE_SQLITE=true \
pytest tests/test_codegen_sandbox.py -v

# Expected Output
40 passed
```

**Sandbox Gates Tested**:
1. Ruff (lint + format)
2. Mypy (type checking)
3. Bandit (security scan)
4. Pytest (unit tests)
5. Coverage (60% threshold)

---

### 9. Prod Smoke Tests (Secrets Required)

**Purpose**: Real infrastructure tests with actual LLM calls.

```bash
# Prerequisites
# - OPENAI_API_KEY set
# - Docker Postgres running

# Command
SMOKE_TEST_ENABLED=true \
OPENAI_API_KEY=sk-xxx \
DATABASE_URL=postgresql://integration:integration@localhost:15432/integration_coworker \
pytest tests/prod_readiness -m prod_smoke -v

# Expected Output
4 passed, 5 skipped (or all 9 pass with full setup)
```

**CI Trigger**: Manual workflow dispatch with `run_prod_smoke=true`

---

### 10. HITL / Resume Tests

**Purpose**: Validate human-in-the-loop interrupt/resume functionality.

```bash
# Command
USE_SQLITE=true \
python scripts/verify_prod_hitl_resume.py --mock-llm --test hitl-approve

# Expected Output
"✓ All tests passed"
```

**HITL Test Scenarios**:
- `hitl-approve` - Human approval flow
- `hitl-reject` - Human rejection flow
- `interrupt-resume` - Workflow interrupt and resume

---

### 11. Soak Tests (Optional, Extended Duration)

**Purpose**: Detect resource leaks over extended runtime.

```bash
# Command (30 minute default)
./scripts/soak.sh

# Quick 5 minute check
./scripts/soak.sh 5 --quick

# Environment
USE_MOCK_LLM=true
USE_SQLITE=true
SOAK_PARALLEL_RUNS=2
SOAK_INTERVAL_SECONDS=30
```

**Leak Detection Thresholds**:
- Thread count: < 50
- File descriptors: < 200
- Memory growth: < 100MB

---

## CI Pipeline Mapping

### GitHub Actions Jobs (`ci.yml`)

| Job | Trigger | Tests Run | Blocking |
|-----|---------|-----------|----------|
| `lint` | Always | ruff + bandit | ✅ Yes |
| `unit-fast` | Always | Unit tests (no deps) | ✅ Yes |
| `integration` | Always | Integration (mocked) | ✅ Yes |
| `e2e` | Always | Docker + Postgres | ✅ Yes |
| `record` | Manual | Cassette refresh | ❌ No |
| `live` | Manual | Live API calls | ❌ No |
| `prod-smoke` | Manual | Real Postgres + LLM | ❌ No |

### Required Environment Variables by Category

| Variable | Unit | Integration | E2E | Prod Smoke |
|----------|------|-------------|-----|------------|
| `USE_SQLITE` | true | true | - | false |
| `USE_MOCK_LLM` | true | true | true | false |
| `DATABASE_URL` | - | - | auto | required |
| `OPENAI_API_KEY` | - | - | - | required |
| `VALIDATION_PROFILE` | offline | offline | offline | live |

---

## Test Markers Reference

All markers defined in `pyproject.toml`:

| Marker | Description | Command |
|--------|-------------|---------|
| `postgres` | Requires PostgreSQL | `-m postgres` |
| `e2e` | End-to-end tests | `-m e2e` |
| `docker` | Requires Docker daemon | `-m docker` |
| `slow` | Long-running tests | `-m slow` |
| `live_llm` | Real LLM API calls | `-m live_llm` |
| `prod_smoke` | Production smoke tests | `-m prod_smoke` |
| `integration` | Integration tests | `-m integration` |
| `integration_live` | Live service integration | `-m integration_live` |
| `contract` | Contract/schema tests | `-m contract` |
| `vcr` | VCR cassette tests | `-m vcr` |

---

## Quick Reference Commands

```bash
# Full local test (no Docker)
USE_SQLITE=true pytest tests/ -m "not (postgres or e2e or docker)" -v

# With Postgres
docker compose up -d db && make db-wait
DATABASE_URL=postgresql://... pytest tests/ -m postgres -v

# Full E2E (all services)
docker compose up -d
pytest tests/e2e -v

# Quick bug hunt
python scripts/bug_hunt.py

# Production validation
./scripts/demo-final-showcase.sh --quick
```

---

## Known Issues & Workarounds

### Issue: Postgres tests skip instead of fail
**Workaround**: Set `POSTGRES_TESTS_REQUIRED=true` to convert skips to failures.

### Issue: E2E tests timeout
**Workaround**: Increase `--timeout=600` for slow machines.

### Issue: Prod smoke tests need secrets
**Workaround**: Use manual workflow dispatch in GitHub Actions.

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2025-01-17 | 2.0 | Comprehensive rewrite with exact commands |
| 2025-01-15 | 1.0 | Initial draft |
