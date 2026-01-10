# Production Hardening Implementation Plan

**Date**: 2025-12-20  
**Status**: READY FOR IMPLEMENTATION  
**Branch**: copilot/prod-readiness-v4

---

## Executive Summary

This document provides a no-room-for-interpretation implementation plan for production hardening. Each issue has:
- Evidence (file:line references)
- Approach chosen with rejected alternatives
- Exact symbols to change
- Test plan
- Rollout/migration notes

---

## Issue Tracker

| ID | Severity | Status | File(s) | Symbol(s) | Approach |
|----|----------|--------|---------|-----------|----------|
| C-1 | Critical | ✅ RESOLVED | `llm/client.py:1264` | `cache_key` | api_key_hash already included |
| C-2 | Critical | ✅ DONE | `llm/circuit_breaker.py`, `llm/async_client.py`, `llm/client.py` | `CircuitBreaker`, `_retry_async()`, `with_retry()` | Internal circuit breaker |
| C-3 | Critical | ✅ DONE | `llm/concurrency.py` | `_semaphores`, `get_llm_semaphore()` | WeakKeyDictionary per-loop |
| C-4 | Critical | ✅ DONE | `health/server.py` | `HealthServer`, `start_health_server()` | stdlib HTTP server |
| H-1 | High | 🔧 TODO | `llm/cache.py` | `get()`, `set()` | Add metrics counters |
| H-2 | High | 🔧 TODO | `persistence/postgres.py` | `get_pool()` | Env var configuration |
| H-3 | High | 🔧 TODO | `llm/client.py:1717` | ThreadPoolExecutor | Shared module executor |
| H-4 | High | 🔧 TODO | `config/__init__.py:34` | `_ARCHETYPE_CACHE` | LRU cache with maxsize |
| H-5 | High | 🔧 TODO | `graph/nodes/ingest_spec.py:100` | `_http_client` | Register with ShutdownManager |
| H-6 | High | 📋 AUDIT | Multiple files | `except Exception:` | Tighten exception types |

---

## Dependency Graph

```
┌─────────────────────────────────────────────────────────────────┐
│                    Can be parallelized                          │
├─────────────────────────────────────────────────────────────────┤
│  C-3 (Semaphore)   │  C-4 (Health)   │  H-4 (Archetype)        │
│  H-2 (Pool Config) │  H-5 (HTTP)     │  H-1 (Redis Metrics)    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              Depends on C-3 (Semaphore fix)                     │
├─────────────────────────────────────────────────────────────────┤
│  C-2 (Circuit Breaker) - integrates with concurrency layer      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              Can be done after all above                        │
├─────────────────────────────────────────────────────────────────┤
│  H-3 (ThreadPool) │  H-6 (Exception Audit)                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Detailed Implementation Plans

### C-2: Circuit Breaker

**Evidence:**
- `src/integration_coworker/llm/client.py:140-180` - `with_retry()` decorator
- `src/integration_coworker/llm/async_client.py:107-193` - `_retry_async()` function
- No circuit breaker exists (confirmed via `rg "circuit.?breaker"`)

**Approach Chosen: B (Internal State Machine)**

**Rejected Alternatives:**
- A (pybreaker library): External dependency, async integration unclear
- C (Retry-only): No cost protection during outages

**Implementation:**

1. Create `src/integration_coworker/llm/circuit_breaker.py`:
```python
@dataclass
class CircuitBreakerState:
    state: Literal["closed", "open", "half-open"] = "closed"
    failure_count: int = 0
    last_failure_time: float = 0.0
    success_count_in_half_open: int = 0
    
    # Configuration
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 3
    
    # Metrics
    total_opens: int = 0
    total_short_circuits: int = 0
    
    def record_success(self) -> None: ...
    def record_failure(self, exc: Exception) -> None: ...
    def should_allow_request(self) -> bool: ...
    def is_transient_failure(self, exc: Exception) -> bool: ...
```

2. Integrate with `_retry_async()`:
```python
async def _retry_async(...):
    circuit = get_circuit_breaker()
    if not circuit.should_allow_request():
        raise CircuitOpenError(f"Circuit breaker open, {circuit.time_until_half_open}s until retry")
    try:
        result = await fn(*args, **kwargs)
        circuit.record_success()
        return result
    except Exception as e:
        if circuit.is_transient_failure(e):
            circuit.record_failure(e)
        raise
```

**Test Plan:**
- Unit tests for state machine transitions
- Integration test: simulate 5 failures → circuit opens
- Integration test: wait recovery_timeout → half-open → success → closed
- Integration test: auth errors do NOT open circuit

**Env Vars:**
- `LLM_CIRCUIT_FAILURE_THRESHOLD` (default: 5)
- `LLM_CIRCUIT_RECOVERY_TIMEOUT` (default: 60)

---

### C-3: Per-Event-Loop Semaphore

**Evidence:**
- `src/integration_coworker/llm/concurrency.py:57` - `_semaphore: Optional[asyncio.Semaphore] = None`
- `src/integration_coworker/llm/concurrency.py:143-161` - `get_llm_semaphore()`
- Tests fail with cross-loop errors in pytest

**Approach Chosen: A (WeakKeyDictionary)**

**Rejected Alternatives:**
- B (Full DI): Too much plumbing for this fix
- C (TaskGroup): Overkill architecture change

**Implementation:**

```python
import weakref

# Replace global semaphore with per-loop dictionary
_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()
_configs: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, ConcurrencyConfig] = weakref.WeakKeyDictionary()

def get_llm_semaphore() -> asyncio.Semaphore:
    """Get or create semaphore for the current event loop."""
    loop = asyncio.get_running_loop()
    if loop not in _semaphores:
        config = _get_or_create_config(loop)
        _semaphores[loop] = asyncio.Semaphore(config.max_concurrent)
        logger.info(f"Created semaphore for loop {id(loop)} with max_concurrent={config.max_concurrent}")
    return _semaphores[loop]

def _get_or_create_config(loop: asyncio.AbstractEventLoop) -> ConcurrencyConfig:
    if loop not in _configs:
        _configs[loop] = _load_config()
    return _configs[loop]
```

**Test Plan:**
- Test: create semaphore in loop A, run `asyncio.run()` twice → no error
- Test: concurrent tasks in same loop share semaphore
- Test: metrics are per-loop but can be aggregated

**Migration Notes:**
- `reset_llm_semaphore()` should clear all loops or just current
- Existing tests should pass with no changes

---

### C-4: HTTP Health Endpoints

**Evidence:**
- No HTTP endpoints exist (confirmed via `rg "/health|/ready"`)
- CLI has `health_check` command at `cli.py:1336` but not HTTP

**Approach Chosen: C (stdlib HTTP server)**

**Rejected Alternatives:**
- A (FastAPI): Not a current dependency
- B (CLI only): Can't serve K8s probes

**Implementation:**

Create `src/integration_coworker/health/server.py`:
```python
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import threading
from typing import Optional, Dict, Any

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self._respond_json(200, {"status": "ok"})
        elif self.path == "/readyz":
            checks = self._run_readiness_checks()
            status = 200 if all(c["ok"] for c in checks.values()) else 503
            self._respond_json(status, {"status": "ready" if status == 200 else "not_ready", "checks": checks})
        else:
            self.send_error(404)
    
    def _run_readiness_checks(self) -> Dict[str, Any]:
        checks = {}
        # DB check
        try:
            from integration_coworker.persistence.db import get_connection
            with get_connection() as conn:
                conn.execute("SELECT 1")
            checks["database"] = {"ok": True}
        except Exception as e:
            checks["database"] = {"ok": False, "error": str(e)}
        return checks
    
    def _respond_json(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def log_message(self, format, *args):
        pass  # Suppress default logging

def start_health_server(port: int = 8080) -> HTTPServer:
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
```

Add to CLI:
```python
@app.command()
def serve_health(port: int = 8080):
    """Start HTTP health server for K8s probes."""
    from integration_coworker.health.server import start_health_server
    server = start_health_server(port)
    typer.echo(f"Health server running on http://0.0.0.0:{port}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
```

**Test Plan:**
- Unit test: `/healthz` returns 200
- Unit test: `/readyz` returns 200 when DB available
- Unit test: `/readyz` returns 503 when DB unavailable
- Integration test: server starts in background thread

**Env Vars:**
- `HEALTH_SERVER_PORT` (default: 8080)

---

### H-1: Redis Cache Metrics

**Evidence:**
- `src/integration_coworker/llm/cache.py:120-130` - silent degradation

**Implementation:**
Add counters to `LLMCache`:
```python
@dataclass
class LLMCacheMetrics:
    hits: int = 0
    misses: int = 0
    errors: int = 0
    bypassed: int = 0  # When Redis unavailable
```

**Test Plan:**
- Test: cache hit increments `hits`
- Test: cache miss increments `misses`
- Test: Redis error increments `errors` and `bypassed`

---

### H-2: DB Pool Configuration

**Evidence:**
- `src/integration_coworker/persistence/postgres.py` - hardcoded pool size

**Implementation:**
```python
def get_pool() -> ConnectionPool:
    settings = get_settings()
    pool_size = int(os.environ.get("POSTGRES_POOL_SIZE", "10"))
    pool_max = int(os.environ.get("POSTGRES_POOL_MAX", "20"))
    return ConnectionPool(
        conninfo=settings.database.url,
        min_size=pool_size,
        max_size=pool_max,
        ...
    )
```

**Env Vars:**
- `POSTGRES_POOL_SIZE` (default: 10)
- `POSTGRES_POOL_MAX` (default: 20)

---

### H-3: Shared ThreadPoolExecutor

**Evidence:**
- `src/integration_coworker/llm/client.py:1717` - per-call executor
- Multiple instances in `async_client.py`

**Implementation:**
```python
# Module-level shared executor
_executor: Optional[ThreadPoolExecutor] = None

def get_shared_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm-sync")
        # Register for shutdown
        import atexit
        atexit.register(_executor.shutdown, wait=True)
    return _executor
```

Replace per-call executors with `get_shared_executor()`.

---

### H-4: Bounded Archetype Cache

**Evidence:**
- `src/integration_coworker/config/__init__.py:34` - `_ARCHETYPE_CACHE: Dict`

**Implementation:**
```python
from functools import lru_cache

@lru_cache(maxsize=100)
def _load_archetype_config(node_name: str) -> Dict[str, Any]:
    ...
```

**Test Plan:**
- Test: cache respects maxsize
- Test: LRU eviction works correctly

---

### H-5: HTTP Client Shutdown Registration

**Evidence:**
- `src/integration_coworker/graph/nodes/ingest_spec.py:100` - `_http_client`
- `cleanup_http_client()` exists at line 123 but not registered

**Implementation:**
```python
def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(...)
        # Register cleanup
        from integration_coworker.shutdown import get_shutdown_manager
        manager = get_shutdown_manager()
        if manager:
            manager.register_cleanup(cleanup_http_client)
        else:
            import atexit
            atexit.register(cleanup_http_client)
    return _http_client
```

---

### H-6: Exception Handler Audit

**Evidence:**
- `rg -c "except Exception:" src/` shows 50+ bare handlers

**Top Offenders:**
1. `repo/detection.py` - 8 handlers
2. `repo/providers/local.py` - 4 handlers
3. `persistence/db.py` - 4 handlers
4. `graph/runtime.py` - 3 handlers

**Strategy:**
- Review each handler
- Tighten to specific exception types where possible
- Ensure errors are logged or converted to typed ErrorClass
- Add tests to verify failures are visible

---

## Test Suite Organization

### Suite Split (for per-suite timeouts)

The test suite is split into tiers to enable focused CI runs with appropriate timeouts:

| Suite | Command | Timeout | Tests | Description |
|-------|---------|---------|-------|-------------|
| **unit** | `pytest tests/llm tests/health tests/graph tests/security tests/kg` | 90s | ~450 | Pure unit tests, no I/O |
| **infra** | `pytest tests/persistence tests/retrieval tests/repo tests/sources tests/codegen` | 120s | ~400 | Infrastructure/storage tests |
| **e2e** | `pytest tests/e2e tests/integration` | 180s | ~50 | Docker-based integration tests |
| **smoke** | `pytest tests/test_production*.py tests/test_trusted*.py` | 120s | ~15 | Production validation proofs |

### Definition of Green

A commit is **production-ready** when ALL of these pass:

```bash
# 1. Unit tests (must pass, no skips except platform-specific)
pytest tests/llm tests/health tests/graph tests/security tests/kg -q --tb=line

# 2. Infrastructure tests (must pass)
pytest tests/persistence tests/retrieval tests/repo tests/sources tests/codegen -q --tb=line

# 3. E2E/Integration (must pass, Docker required)
pytest tests/e2e tests/integration -q --tb=line

# 4. Production smoke tests (must pass)
pytest tests/test_production*.py tests/test_trusted*.py -q --tb=line
```

**Excluded from "green" definition** (run separately):
- `tests/test_langgraph_checkpointing.py` - environment-specific checks
- `@pytest.mark.integration` tests requiring live services
- Load/stress tests marked `@pytest.mark.slow`

### CI Configuration

```yaml
# Recommended .github/workflows/ci.yml structure
jobs:
  unit:
    timeout-minutes: 3
    run: pytest tests/llm tests/health tests/graph tests/security tests/kg
    
  infra:
    timeout-minutes: 3
    run: pytest tests/persistence tests/retrieval tests/repo tests/sources tests/codegen
    
  e2e:
    timeout-minutes: 5
    needs: [unit, infra]
    run: pytest tests/e2e tests/integration
```

---

## Validation Plan

After implementation:
1. Run **unit** suite - should be 100% green
2. Run **infra** suite - should be 100% green  
3. Run **e2e** suite - should be 100% green (Docker required)
4. Run production demo: `scripts/demo-final-showcase.sh`
5. Run with Postgres: `DATABASE_URL=... python -m pytest tests/persistence/`

---

## Rollout Notes

1. **No breaking changes** - all env vars have sensible defaults
2. **Backwards compatible** - existing configurations continue to work
3. **Feature flags** - circuit breaker can be disabled via `LLM_CIRCUIT_ENABLED=false`
4. **Migration** - no data migration needed

---

## Next Steps

1. ✅ Phase 0: Baseline captured
2. ✅ Phase 1: Design debates complete
3. ✅ Phase 2: Implementation plan written (this doc)
4. 🔧 Phase 3: Execute changes
5. 🔧 Phase 4: Production validation
6. 🔧 Phase 5: Optimal alternative analysis
7. 🔧 Phase 6: Final ranking
