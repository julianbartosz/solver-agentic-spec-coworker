# Testing & Observability Audit Report

**Date**: 2025-01-15  
**Purpose**: Inventory production testing infrastructure, identify gaps, and recommend improvements

---

## Executive Summary

The repository has a **comprehensive testing infrastructure** already in place. Key findings:

| Category | Status | Score |
|----------|--------|-------|
| **Test Coverage Infrastructure** | ✅ Strong | 9/10 |
| **Validation Profiles** | ✅ Strong | 9/10 |
| **CI/CD Pipeline** | ✅ Strong | 8/10 |
| **LangSmith Tracing** | ✅ Integrated | 8/10 |
| **Streamlit UI** | ✅ Working | 7/10 |
| **Production Smoke Tests** | ✅ Available | 8/10 |
| **Soak Testing** | ✅ Available | 7/10 |
| **E2E with Live LLM** | ⚠️ Needs Activation | 6/10 |
| **Bug-finding Tools** | ⚠️ Gap | 5/10 |

**Overall**: The infrastructure is production-ready but needs **activation** and **regular use** to find bugs.

---

## 1. What Already Exists

### 1.1 Test Suite Structure

```
tests/
├── conftest.py          # Central fixtures, validation profiles, network safety
├── e2e/                 # End-to-end tests (Docker, Postgres, live LLM)
├── prod_readiness/      # Production smoke tests
├── contract/            # API contract tests with VCR cassettes
├── integration/         # Integration tests (mocked external deps)
├── codegen/             # Code generation tests
├── graph/               # Workflow graph tests
├── kg/                  # Knowledge graph tests
├── llm/                 # LLM client tests
├── persistence/         # Database persistence tests
├── security/            # Security tests
└── perf/                # Performance tests
```

**Test Count**: ~3,300+ tests collected

### 1.2 Validation Profiles (Defense-in-Depth)

Located in `tests/conftest.py`:

| Profile | Description | Network | Use Case |
|---------|-------------|---------|----------|
| `offline` (default) | All sockets blocked, replay-only | ❌ Blocked | CI, local dev |
| `record` | Re-record cassettes | ✅ Localhost only | Cassette refresh |
| `live` | Live service access | ✅ Allowlist | Integration testing |

**To use**:
```bash
VALIDATION_PROFILE=offline pytest tests/       # Default - safe
VALIDATION_PROFILE=record ALLOW_RECORD=1 pytest tests/contract  # Re-record
VALIDATION_PROFILE=live ALLOW_LIVE=1 LIVE_HOST_ALLOWLIST=api.openai.com pytest tests/
```

### 1.3 CI/CD Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | Push/PR | Lint, unit, integration tests |
| `test-postgres.yml` | Push/PR | Postgres-specific tests |
| `e2e.yml` (in ci.yml) | After unit | Docker + testcontainers |
| `nightly-e2e.yml` | Cron 2AM UTC | Live LLM E2E tests |
| `showcase.yml` | Manual | Demo scenarios |

### 1.4 LangSmith Integration ✅

**Already integrated** in multiple places:

1. **CLI** (`cli.py:433-437`): Shows LangSmith trace URL at run start
2. **Runtime** (`runtime.py:1003`): Uses `@traceable` decorator when enabled
3. **State** (`state_v2.py:255`): Excludes large fields to avoid payload limits
4. **Database**: `langsmith_run_id` column in `run_outcomes` table
5. **Feedback**: `kg.feedback_records` table syncs from LangSmith

**To enable**:
```bash
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY=your-key
export LANGCHAIN_PROJECT=integration-coworker

# Then run the CLI
ic run specs/petstore.yaml "Create checkout" --verbose
```

**Health check**:
```bash
ic health check  # Shows LangSmith status
```

### 1.5 Streamlit UI ✅

**Location**: `src/integration_coworker/ui/`

**Features**:
- Multi-panel interface (Inputs / Run View / Artifacts / Logs)
- Real-time run status
- Error capture with Retry/Skip/Restart actions
- Artifact browser with syntax highlighting
- Graph trace visualization

**To launch**:
```bash
ic ui              # Default port 8501
ic ui --port 8080  # Custom port
```

### 1.6 Production Smoke Tests

**Location**: `tests/prod_readiness/test_smoke_prod.py`

Tests:
- Real Postgres connection pool under concurrency
- Real LLM calls with budget caps
- Circuit breaker behavior

**To run**:
```bash
SMOKE_TEST_ENABLED=1 \
DATABASE_URL=postgresql://... \
OPENAI_API_KEY=sk-... \
pytest tests/prod_readiness/test_smoke_prod.py -v -s
```

### 1.7 Soak Testing

**Location**: `scripts/soak.sh`

Monitors:
- Thread leaks (threshold: 50)
- File descriptor leaks (threshold: 200)
- Memory growth (threshold: 100MB)
- Deadlocks (timeout detection)

**To run**:
```bash
./scripts/soak.sh 30       # Run for 30 minutes
./scripts/soak.sh 5 --quick  # Quick 5-minute check
```

### 1.8 E2E Testing Infrastructure

**Location**: `tests/e2e/`

Features:
- Repo cloning with caching and SHA pinning
- Docker runner for Tier.PROD validation
- LLM budget guards (`LLMBudgetGuard` class)
- TypeScript/Go project fixtures

---

## 2. How to Use for Bug Finding

### 2.1 Quick Bug Hunt Workflow

```bash
# 1. Run quick unit tests
source .venv311/bin/activate
pytest tests/ -m "not (slow or integration_live or e2e or docker or postgres)" \
  --timeout=60 -x -q

# 2. Run integration tests with coverage
pytest tests/ -m "integration and not (integration_live or e2e or docker or postgres)" \
  --cov=src --cov-report=term-missing --cov-fail-under=60 -x

# 3. Run E2E with Docker (needs Docker running)
pytest tests/e2e -m "e2e and docker" -v --timeout=600

# 4. Run production smoke tests (needs real DB + API keys)
SMOKE_TEST_ENABLED=1 DATABASE_URL=... OPENAI_API_KEY=... \
  pytest tests/prod_readiness -v -s
```

### 2.2 Using LangSmith for Debugging

1. **Enable tracing**:
   ```bash
   export LANGCHAIN_TRACING_V2=true
   export LANGCHAIN_API_KEY=your-key
   export LANGCHAIN_PROJECT=debug-session-$(date +%Y%m%d)
   ```

2. **Run problematic workflow**:
   ```bash
   ic run specs/stripe_api.json "Create checkout session" --verbose
   ```

3. **Check LangSmith dashboard**: https://smith.langchain.com
   - View LLM inputs/outputs
   - Check token usage
   - Identify slow nodes
   - Inspect intermediate state

### 2.3 Using Streamlit for Interactive Debugging

```bash
ic ui
```

Then:
1. Configure spec and task in sidebar
2. Enable "Verbose Logs"
3. Click "Run Integration"
4. Check "Errors & Recovery" tab for failures
5. Use "Retry" / "Skip" / "Restart" for iterative debugging

---

## 3. Identified Gaps

### 3.1 Gap: No Automated Bug-Finding Tool

**Issue**: While tests exist, there's no systematic tool to:
- Run all test tiers automatically
- Aggregate failures
- Track flaky tests
- Generate bug reports

**Recommendation**: Create `scripts/bug_hunt.py`:
```python
"""
Automated bug hunting script.

Runs tests in tiers, captures failures, generates report.
"""
```

### 3.2 Gap: Production Load Testing

**Issue**: Soak test exists but doesn't test actual production workloads.

**Recommendation**: Create `scripts/load_test.py` that:
- Runs N concurrent workflow executions
- Uses real specs (Stripe, GitHub)
- Measures throughput and error rates
- Reports P50/P95/P99 latencies

### 3.3 Gap: Regression Test Suite

**Issue**: No explicit regression tests for fixed bugs.

**Recommendation**: Create `tests/regression/` with:
- One test file per bug number
- Clear documentation of what was fixed
- Ensures bugs don't reappear

### 3.4 Gap: Flaky Test Detection

**Issue**: No mechanism to identify and quarantine flaky tests.

**Recommendation**: 
- Add `--reruns 2` to CI for flaky detection
- Create `tests/flaky/` for known flaky tests
- Track flaky tests in issues

### 3.5 Gap: Live LLM Tests Not Regularly Run

**Issue**: `nightly-e2e.yml` requires `ENABLE_NIGHTLY_LLM=true` repo variable.

**Recommendation**: Enable the variable and monitor costs.

---

## 4. Strengths

### 4.1 Strong Network Isolation

The pytest-socket + respx combination provides defense-in-depth:
- **Layer 1**: pytest-socket blocks all sockets by default
- **Layer 2**: respx mocks HTTP, fails on unmocked requests

### 4.2 Excellent Validation Profile System

The offline/record/live profiles make it easy to:
- Run safely in CI (offline)
- Refresh cassettes when needed (record)
- Test against real services (live)

### 4.3 Comprehensive CI Matrix

Testing on Python 3.11, 3.12, 3.13 catches compatibility issues early.

### 4.4 Built-in Cost Controls

LLM budget guards prevent runaway costs in E2E tests.

### 4.5 Good Observability Integration

LangSmith tracing is wired throughout the codebase.

---

## 5. Recommended Improvements

### 5.1 Priority 0: Enable Nightly LLM Tests

```bash
# In GitHub repo settings, add repository variable:
ENABLE_NIGHTLY_LLM=true
```

This activates the `nightly-e2e.yml` workflow.

### 5.2 Priority 1: Create Bug Hunt Script

Create `scripts/bug_hunt.py`:

```python
#!/usr/bin/env python3
"""
Automated bug hunting script.

Usage:
    python scripts/bug_hunt.py                    # Full hunt
    python scripts/bug_hunt.py --tier unit       # Only unit tests
    python scripts/bug_hunt.py --report bugs.md  # Generate report
"""

import subprocess
import json
import sys
from dataclasses import dataclass
from typing import List

@dataclass
class TestResult:
    name: str
    outcome: str  # passed, failed, error, skipped
    duration: float
    error: str = ""

def run_tier(tier: str, markers: str) -> List[TestResult]:
    """Run tests for a tier and collect results."""
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/", "-m", markers,
        "--timeout=120",
        "--json-report", "--json-report-file=.test_results.json",
        "-q"
    ]
    subprocess.run(cmd, capture_output=True)
    
    with open(".test_results.json") as f:
        data = json.load(f)
    
    return [
        TestResult(
            name=t["nodeid"],
            outcome=t["outcome"],
            duration=t.get("duration", 0),
            error=t.get("longrepr", "")
        )
        for t in data.get("tests", [])
    ]

def main():
    tiers = {
        "unit": "not (slow or integration or e2e or docker or postgres)",
        "integration": "integration and not (integration_live or e2e)",
        "e2e": "e2e and not integration_live",
    }
    
    all_failures = []
    for tier, markers in tiers.items():
        print(f"\n{'='*60}")
        print(f"Running tier: {tier}")
        print(f"{'='*60}")
        
        results = run_tier(tier, markers)
        failures = [r for r in results if r.outcome in ("failed", "error")]
        all_failures.extend(failures)
        
        print(f"  Passed: {len([r for r in results if r.outcome == 'passed'])}")
        print(f"  Failed: {len(failures)}")
    
    if all_failures:
        print(f"\n{'='*60}")
        print(f"FAILURES: {len(all_failures)}")
        print(f"{'='*60}")
        for f in all_failures:
            print(f"  ❌ {f.name}")

if __name__ == "__main__":
    main()
```

### 5.3 Priority 2: Add Regression Test Directory

Create `tests/regression/README.md`:

```markdown
# Regression Tests

This directory contains tests for previously fixed bugs.
Each file is named after the bug it prevents from recurring.

## Naming Convention

- `test_bug_NNN.py` - Test for bug #NNN
- `test_issue_NNN.py` - Test for GitHub issue #NNN

## Adding a New Regression Test

1. Create test file: `test_bug_NNN.py`
2. Document the bug in docstring
3. Add specific assertion that would have caught the bug
```

### 5.4 Priority 3: Flaky Test Detection

Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "-v --tb=short --strict-markers --reruns 1 --reruns-delay 1"
```

And install: `pip install pytest-rerunfailures`

### 5.5 Priority 4: Production Load Test Script

Create `scripts/load_test.py` for measuring:
- Concurrent workflow capacity
- LLM rate limit handling
- Database connection pool behavior
- Memory usage under load

---

## 6. Quick Reference Commands

```bash
# == Testing ==
pytest tests/ -x -v --tb=short                    # Quick run, stop on first failure
pytest tests/ -m postgres -v                      # Only Postgres tests
pytest tests/ -m "not slow" --timeout=60          # Skip slow tests
pytest tests/ --cov=src --cov-report=html         # With coverage report

# == Production Testing ==
ic health check                                   # Health check all services
./scripts/soak.sh 5 --quick                       # Quick soak test
SMOKE_TEST_ENABLED=1 ... pytest tests/prod_readiness  # Production smoke

# == Debugging ==
export LANGCHAIN_TRACING_V2=true                  # Enable LangSmith
ic ui                                             # Launch Streamlit debugger
ic run spec.yaml "task" --verbose --dry-run       # Verbose dry run

# == CI Simulation ==
ruff check src tests                              # Lint check
bandit -c pyproject.toml -r src tests             # Security scan
pytest tests/ -m "not (e2e or postgres)" --cov    # Integration tests
```

---

## 7. Conclusion

The testing infrastructure is **production-ready and comprehensive**. The main gaps are:

1. **Activation**: Enable nightly LLM tests
2. **Tooling**: Create bug hunt / load test scripts
3. **Process**: Establish regression test discipline

The existing tools (LangSmith, Streamlit, validation profiles) provide excellent observability - they just need to be actively used during development and debugging sessions.
