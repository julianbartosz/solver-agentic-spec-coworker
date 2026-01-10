# Production Readiness Implementation Plan v4 (Locked)

## Why This Exists

This document captures the **final, locked implementation plan** for closing production-readiness gaps identified during the comprehensive audit conducted December 2024. It exists to:

1. Prevent regression by documenting exact requirements and acceptance criteria
2. Provide a single source of truth for implementation decisions
3. Enable code review against explicit acceptance criteria
4. Serve as an audit trail for architectural decisions

## Change Control

> **Any deviation from this plan requires:**
> 1. Updating this document with the change and rationale
> 2. Adding an ADR (Architecture Decision Record) in `docs/decisions/` for significant changes
> 3. Approval from code owner before merging

---

## Reference Notes

This plan cites the following authoritative sources:

| Topic | Reference | Key Point |
|-------|-----------|-----------|
| Logging `exc_text` caching | [Python logging docs](https://docs.python.org/3/library/logging.html) | Must clear `record.exc_text` between formatters to avoid cached exception text leaking |
| `functools.lru_cache` thread safety | [Python functools docs](https://docs.python.org/3/library/functools.html) | lru_cache has internal locking; thread-safe for concurrent access |
| Tenacity predicate retries | [Tenacity API docs](https://tenacity.readthedocs.io/en/stable/api.html) | Use `retry_if_exception(predicate)` for conditional retry based on exception type |
| Cachetools thread safety | [Cachetools docs](https://cachetools.readthedocs.io/) | Cachetools caches are NOT thread-safe; why we chose lru_cache instead |
| Multi-handler exception formatting | [CPython issue #137790](https://github.com/python/cpython/issues/137790) | `Formatter.format`'s `exc_text` cache breaks multiple handler scenarios |

---

## Critical Fixes Applied

| Issue | Problem | Solution |
|-------|---------|----------|
| Auth bypass | Nodes can catch + return fallback, wrapper never sees error | **Nodes MUST re-raise; wrapper owns all fallback decisions** |
| LogRecord mutation | Multi-handler sees mutated record; exc_text caching breaks redaction | **Snapshot/restore fields + clear exc_text** |
| Cache design | Can't construct client without API key in cache key | **Option A: cache session/factory, inject key per-call** |
| Prune SQL | `INTERVAL '$2 days'` won't parameterize; no actual locking | **CTE + advisory lock; correct param syntax** |
| Shutdown | Missing main-thread constraint docs | **Explicit platform + thread constraints** |
| Auth E2E | Real API call is flaky/costly | **Mock HTTP layer; gate real-LLM tests** |

---

## P0/P1 Issue List (Validated)

| ID | Issue | Severity | Status |
|----|-------|----------|--------|
| **P0-1** | Log credential redaction | CRITICAL | ✅ DONE |
| **P0-2** | LLM auth fail-fast (typed exceptions + Tenacity) | CRITICAL | ✅ DONE |
| **P0-3** | Connection lifecycle (async checkpointer) | CRITICAL | ✅ DONE |
| **P0-4** | Graceful shutdown (platform-aware signals) | CRITICAL | ✅ DONE |
| **P0-5** | Error routing policy (centralized in wrapper) | CRITICAL | ✅ DONE |
| **P0-6** | Recovery semantics (checkpoint versioning) | CRITICAL | ✅ DONE |
| **P1-1** | Async retry (Tenacity) | HIGH | ✅ DONE (Part of P0-2) |
| **P1-2** | Cache bounds (session-based, thread-safe) | HIGH | ✅ DONE |
| **P1-3** | Checkpoint growth (retention + pruning) | HIGH | ✅ DONE |

---

## Phase 0: Remove Node Fallback Code (PREREQUISITE)

### Problem

Nodes currently catch exceptions and return fallback skeleton code, bypassing the wrapper's ability to enforce FATAL classification.

### Discovery Commands

```bash
rg -n "except\s+Exception|fallback|skeleton" src/integration_coworker/graph/nodes --type py
rg -n "state\.errors\.append|return\s+\{.*code" src/integration_coworker/graph/nodes --type py
```

### Nodes Requiring Refactor

| Node File | Current Behavior | Required Change |
|-----------|------------------|-----------------|
| `generate_code_and_tests.py` | Catches `Exception`, returns skeleton | Remove fallback; re-raise with context |
| `embed_spec_chunks.py` | Catches for backoff retry | Replace with Tenacity; let FATAL propagate |
| `apply_repo_integration_changes.py` | Catches write errors broadly | Narrow to specific IO exceptions only |

### Node Contract (Enforced)

```python
# ALLOWED in nodes:
try:
    result = await llm_call(...)
except LLMAuthError:
    raise  # MUST re-raise FATAL errors
except LLMRateLimitError as e:
    raise  # Let wrapper/Tenacity handle retry
except SomeSpecificRecoverableError as e:
    logger.warning(f"Recoverable error: {e}")
    raise  # Still raise; wrapper decides policy

# FORBIDDEN in nodes:
except Exception as e:
    return {"code": "# Fallback skeleton", ...}  # NO! Wrapper decides this
```

### Wrapper Ownership

```python
# In runtime.py wrapper - ONLY place fallback can occur:
async def wrapper(state: GraphState) -> GraphState:
    try:
        return await node_func(state)
    except Exception as e:
        error_class = _classify_error(e)
        
        if error_class == ErrorClass.FATAL:
            # NEVER fallback for FATAL
            logger.error(f"FATAL: {node_func.__name__}: {e}")
            raise
        
        if error_policy == ErrorPolicy.FAIL_FIRST:
            raise
        
        if error_policy == ErrorPolicy.COLLECT:
            # Only HERE can we produce fallback
            return _create_fallback_state(state, e)
```

---

## Phase 1: LLM Exceptions + Tenacity Retry (P0-2)

### New File: `src/integration_coworker/llm/exceptions.py`

```python
"""LLM exception hierarchy for fail-fast semantics."""

class LLMError(Exception):
    """Base class for LLM errors."""
    pass

class LLMAuthError(LLMError):
    """Authentication/authorization failure (401/403). Non-retryable, FATAL."""
    pass

class LLMRateLimitError(LLMError):
    """Rate limit hit (429). Retryable with backoff."""
    pass

class LLMTransientError(LLMError):
    """Transient server error (5xx, timeout). Retryable."""
    pass
```

### Wiring Points

- `src/integration_coworker/llm/__init__.py` - Export exceptions
- `src/integration_coworker/llm/client.py` - Import + raise in error detection
- `src/integration_coworker/llm/async_client.py` - Same pattern
- `src/integration_coworker/graph/runtime.py` - Import for wrapper classification

### Tenacity Integration

```python
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)
from integration_coworker.llm.exceptions import LLMAuthError, LLMRateLimitError

def _should_retry(exc: BaseException) -> bool:
    """Predicate: retry transient errors, NEVER retry auth errors."""
    if isinstance(exc, LLMAuthError):
        return False  # FATAL - no retry
    if isinstance(exc, LLMRateLimitError):
        return True   # Retryable
    return _is_retryable_error_pattern(exc)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_should_retry),
    reraise=True,
)
def _call_llm_with_retry(client, messages, **kwargs):
    """Retry wrapper using Tenacity. Auth errors fail immediately."""
    try:
        return client.chat.completions.create(messages=messages, **kwargs)
    except Exception as e:
        raise _classify_llm_exception(e) from e
```

---

## Phase 2: Runtime Error Classification + Wrapper Enforcement (P0-5)

### Error Classes and Policies

```python
from enum import Enum

class ErrorClass(Enum):
    FATAL = "fatal"          # LLMAuthError, config errors → always fail
    RETRYABLE = "retryable"  # Rate limits, transient → Tenacity handles
    RECOVERABLE = "recoverable"  # Partial failures → policy-dependent

class ErrorPolicy(Enum):
    FAIL_FIRST = "fail_first"    # First error fails run
    COLLECT = "collect"          # Collect errors, continue with fallback
    FAIL_AFTER_N = "fail_after_n"  # Fail after N errors

def _classify_error(exc: Exception) -> ErrorClass:
    """Classify exception. Called BEFORE any node fallback."""
    if isinstance(exc, LLMAuthError):
        return ErrorClass.FATAL
    if isinstance(exc, (LLMRateLimitError, LLMTransientError)):
        return ErrorClass.RETRYABLE
    if isinstance(exc, (IOError, TimeoutError)):
        return ErrorClass.RECOVERABLE
    # Unknown errors are FATAL by default (fail-safe)
    return ErrorClass.FATAL
```

---

## Phase 3: Logging Redaction (P0-1)

### RedactingFormatter (Exception-Cache-Safe)

```python
import re
import logging

class RedactingFormatter(logging.Formatter):
    """Formatter that redacts secrets. Handles exception caching correctly."""
    
    REDACT_PATTERNS = [
        re.compile(r'sk-[a-zA-Z0-9]{20,}'),
        re.compile(r'Bearer\s+[a-zA-Z0-9._\-]+', re.IGNORECASE),
        re.compile(r'api[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_\-]+', re.IGNORECASE),
        re.compile(r'token["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_\-]+', re.IGNORECASE),
        re.compile(r'password["\']?\s*[:=]\s*["\']?[^\s"\']+', re.IGNORECASE),
        re.compile(r'secret["\']?\s*[:=]\s*["\']?[^\s"\']+', re.IGNORECASE),
        re.compile(r'x-api-key:\s*[a-zA-Z0-9_\-]+', re.IGNORECASE),
        re.compile(r'Authorization:\s*[^\s]+', re.IGNORECASE),
    ]
    
    def _redact(self, text: str) -> str:
        if not isinstance(text, str):
            text = str(text)
        for pattern in self.REDACT_PATTERNS:
            text = pattern.sub('[REDACTED]', text)
        return text
    
    def format(self, record: logging.LogRecord) -> str:
        # CRITICAL: Snapshot original fields to avoid affecting other handlers
        original_msg = record.msg
        original_args = record.args
        original_exc_text = record.exc_text
        
        try:
            # Clear exc_text to force formatException() to run fresh
            record.exc_text = None
            
            # Redact message and args
            record.msg = self._redact(str(record.msg))
            if record.args:
                record.args = tuple(
                    self._redact(str(arg)) if isinstance(arg, str) else arg
                    for arg in record.args
                )
            
            # Format with redacted fields
            result = super().format(record)
            
            # Final defense: redact entire output
            return self._redact(result)
        finally:
            # CRITICAL: Restore original fields for other handlers
            record.msg = original_msg
            record.args = original_args
            record.exc_text = original_exc_text
    
    def formatException(self, exc_info) -> str:
        """Override to redact exception text."""
        result = super().formatException(exc_info)
        return self._redact(result)
```

---

## Phase 4: Shutdown + Async Checkpointer (P0-3, P0-4)

### Signal Handling (Platform-Aware)

```python
import sys
import signal
import asyncio

async def _setup_shutdown_handlers(shutdown_event: asyncio.Event) -> None:
    """
    Setup signal handlers for graceful shutdown.
    
    Platform constraints:
    - Unix: loop.add_signal_handler() (requires main thread)
    - Windows: signal.signal() + call_soon_threadsafe (NotImplementedError for loop handlers)
    
    Thread constraint:
    - Signal handlers must be registered from main thread (Python limitation)
    """
    loop = asyncio.get_running_loop()
    
    def _request_shutdown():
        if not shutdown_event.is_set():
            logger.info("Shutdown signal received")
            shutdown_event.set()
    
    if sys.platform != "win32":
        # Unix: use loop signal handlers (main thread required)
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, _request_shutdown)
            logger.debug("Unix signal handlers registered")
        except ValueError as e:
            logger.warning(f"Cannot register signal handlers (not main thread?): {e}")
    else:
        # Windows: loop.add_signal_handler raises NotImplementedError
        def _win_handler(signum, frame):
            loop.call_soon_threadsafe(shutdown_event.set)
        
        signal.signal(signal.SIGTERM, _win_handler)
        signal.signal(signal.SIGINT, _win_handler)
        logger.debug("Windows signal handlers registered")
```

---

## Phase 5: Resume Semantics + Versioning (P0-6)

### Workflow Version (Cached)

```python
import functools
import subprocess
from pathlib import Path

@functools.lru_cache(maxsize=1)
def get_workflow_version() -> str:
    """Get workflow version. Cached for process lifetime."""
    try:
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / ".git").exists():
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True, text=True, cwd=parent, timeout=5,
                )
                if result.returncode == 0:
                    return f"git:{result.stdout.strip()[:12]}"
                break
    except Exception:
        pass
    
    try:
        import importlib.metadata
        return f"pkg:{importlib.metadata.version('integration_coworker')}"
    except Exception:
        return "unknown"
```

---

## Phase 6: LLM Client Cache (P1-2)

### Session-Based Caching (Option A)

```python
import functools
from dataclasses import dataclass
import httpx

@dataclass(frozen=True)
class LLMSessionKey:
    """Cache key for HTTP session (no secrets)."""
    provider: str
    base_url: str
    timeout: int
    max_retries: int

@functools.lru_cache(maxsize=32)
def _get_http_session(key: LLMSessionKey) -> httpx.Client:
    """Get or create cached HTTP session. Thread-safe via lru_cache."""
    return httpx.Client(base_url=key.base_url, timeout=key.timeout)

def get_llm_client(config: LLMConfig) -> LLMClient:
    """Get LLM client with cached session + per-call auth."""
    session_key = LLMSessionKey(
        provider=config.provider,
        base_url=config.base_url or _default_base_url(config.provider),
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
    session = _get_http_session(session_key)
    return LLMClient(session=session, api_key=config.api_key, model=config.model)
```

---

## Phase 7: Checkpoint Pruning (P1-3)

### Correct Postgres Pruning (psycopg3 driver)

The repo uses **psycopg 3** (not asyncpg). Parameter style is `%s` positional or `%(name)s` named.

```python
import hashlib
from contextlib import contextmanager

def prune_checkpoints(
    conn,  # psycopg.Connection (sync) or use with get_connection()
    retention_days: int = 7,
    retention_count: int = 10,
) -> int:
    """
    Prune old checkpoints. Concurrency-safe via advisory lock.
    Uses psycopg3 parameter style (%s) and fetchall() for RETURNING.
    """
    lock_key = int(hashlib.md5(b"checkpoint_prune").hexdigest()[:15], 16)
    
    with conn.cursor() as cur:
        # Try advisory lock (non-blocking)
        cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        lock_acquired = cur.fetchone()[0]
        
        if not lock_acquired:
            logger.info("Checkpoint prune already running, skipping")
            return 0
        
        try:
            # CTE + DELETE RETURNING with correct psycopg3 syntax
            cur.execute("""
                WITH ranked AS (
                    SELECT 
                        checkpoint_id,
                        thread_id,
                        created_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY thread_id 
                            ORDER BY created_at DESC
                        ) as rn
                    FROM checkpoints
                ),
                candidates AS (
                    SELECT checkpoint_id
                    FROM ranked
                    WHERE rn > %s
                      AND created_at < NOW() - make_interval(days => %s)
                )
                DELETE FROM checkpoints
                WHERE checkpoint_id IN (SELECT checkpoint_id FROM candidates)
                RETURNING checkpoint_id
            """, (retention_count, retention_days))
            
            rows = cur.fetchall()
            deleted_count = len(rows)
            if deleted_count > 0:
                logger.info(f"Pruned {deleted_count} checkpoints")
            conn.commit()
            return deleted_count
        finally:
            # Always release advisory lock
            cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
```

---

## Test Matrix

| Test | Type | Backend | Mock | Gate | Acceptance |
|------|------|---------|------|------|------------|
| `test_llm_auth_error_typed` | Unit | - | HTTP | - | 401 → `LLMAuthError` |
| `test_llm_auth_error_no_retry` | Unit | - | HTTP | - | 0 retries with Tenacity |
| `test_llm_auth_wrapper_fatal` | Unit | - | HTTP | - | Wrapper classifies FATAL |
| `test_redaction_msg` | Unit | - | - | - | `sk-xxx` redacted |
| `test_redaction_traceback` | Unit | - | - | - | Exception text redacted |
| `test_redaction_exc_text_cached` | Unit | - | - | - | Multi-handler safe |
| `test_redaction_restore_record` | Unit | - | - | - | Original record unchanged |
| `test_shutdown_sigterm_unix` | Integration | - | - | `platform!=win32` | Clean exit |
| `test_session_cache_bounded` | Unit | - | Mock | - | Eviction at maxsize |
| `test_session_cache_no_secrets` | Unit | - | Mock | - | No api_key in key |
| `test_checkpoint_version_stored` | Integration | SQLite | - | - | Version in checkpoint |
| `test_resume_version_mismatch` | Integration | SQLite | Mock | - | Error without --force |
| `test_prune_retention_count` | Integration | Postgres | - | - | Keeps last N |
| `test_prune_concurrent_safe` | Integration | Postgres | - | - | Advisory lock works |
| `test_auth_failure_e2e_mocked` | E2E | Postgres | HTTP | - | 401→FAILED, no leak |
| `test_production_bounded_e2e` | E2E | Postgres | Real | `REAL_LLM_TESTS=1` | Bounded run works |

---

## Acceptance Criteria

### Auth Fail-Fast
- [ ] `LLMAuthError` raised for 401/403
- [ ] Tenacity predicate returns `False` for `LLMAuthError` (0 retries)
- [ ] Wrapper classifies `LLMAuthError` as `ErrorClass.FATAL`
- [ ] FATAL bypasses `ErrorPolicy.COLLECT`
- [ ] **Nodes cannot bypass**: no fallback code in nodes
- [ ] Run status is `FAILED` with clear error

### Logging Redaction
- [ ] Snapshot/restore `record.msg`, `record.args`, `record.exc_text`
- [ ] Clear `record.exc_text = None` before formatting
- [ ] Multi-handler: original record unchanged after format
- [ ] `formatException()` returns redacted traceback
- [ ] Test: `api_key=secret` in traceback is redacted

### Cache Design
- [ ] Session cached by `LLMSessionKey` (no secrets)
- [ ] API key NOT in cache key
- [ ] API key injected per-request
- [ ] `lru_cache(maxsize=32)` provides thread-safe caching

### Resume Safety
- [ ] `workflow_version` stored in every checkpoint
- [ ] Version cached per-process (not per-checkpoint)
- [ ] Resume validates version; fails if mismatch
- [ ] `--force` allows mismatch with warning

### Checkpoint Pruning
- [ ] Advisory lock prevents concurrent races
- [ ] Uses `fetch()` for `DELETE RETURNING` (not `fetchval`)
- [ ] Correct asyncpg `$1, $2` parameter style
- [ ] Test: retention by count + age work

### Shutdown
- [ ] Unix: `loop.add_signal_handler()`
- [ ] Windows: `signal.signal()` + `call_soon_threadsafe`
- [ ] Non-main-thread: logs warning, doesn't crash

---

## Implementation Order

1. **Phase 0**: Remove node fallback code
2. **P0-2**: LLM exceptions + Tenacity
3. **P0-5**: Runtime error classification + wrapper enforcement
4. **P0-1**: Logging redaction
5. **P0-3/P0-4**: Shutdown + async checkpointer
6. **P0-6**: Resume semantics + versioning
7. **P1-2**: Session-based cache
8. **P1-3**: Checkpoint pruning

---

*Document created: 2024-12-19*
*Last updated: 2024-12-19*
*Status: LOCKED - ready for implementation*
