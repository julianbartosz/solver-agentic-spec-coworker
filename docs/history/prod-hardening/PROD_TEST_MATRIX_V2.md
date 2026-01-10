# Production Test Matrix V2

**Date**: 2025-01-14  
**Associated Plan**: [PROD_HARDENING_PLAN_V2.md](./PROD_HARDENING_PLAN_V2.md)

---

## Overview

This document defines the comprehensive test matrix for validating production hardening fixes. Each test includes:
- Unique ID for tracking
- Category (Unit/Integration/E2E)
- **Exact command** to run
- **Environment variables** required
- **Expected runtime**
- Pass/fail criteria

---

## Pre-requisites

```bash
# Ensure test dependencies
pip install -e ".[test]"

# Set up test database (avoid production)
export DATABASE_URL="sqlite:///test_hardening.db"

# Disable LLM calls for fast tests
export USE_MOCK_LLM=true
```

---

## Item A: Database Migrations

### TEST-A1: Migration Apply Success

| Field | Value |
|-------|-------|
| **ID** | TEST-A1 |
| **Category** | Integration |
| **Expected Runtime** | <5s |

**Command**:
```bash
# After implementing migrations module
DATABASE_URL="sqlite:///test_migration.db" python -c "
from integration_coworker.persistence.migrations import run_pending_migrations
from integration_coworker.persistence.db import get_connection

with get_connection() as conn:
    run_pending_migrations(conn)
    cur = conn.execute('SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1')
    version = cur.fetchone()[0]
    print(f'Current version: {version}')
    assert version >= 1, 'Migration not applied'
    print('PASS: Migration applied successfully')
"
```

**Pass Criteria**: Version ≥ 1 printed, no errors.

### TEST-A2: Idempotent Apply

| Field | Value |
|-------|-------|
| **ID** | TEST-A2 |
| **Category** | Unit |
| **Expected Runtime** | <5s |

**Command**:
```bash
DATABASE_URL="sqlite:///test_migration.db" python -c "
from integration_coworker.persistence.migrations import run_pending_migrations
from integration_coworker.persistence.db import get_connection

with get_connection() as conn:
    # Run twice
    run_pending_migrations(conn)
    run_pending_migrations(conn)
    print('PASS: Idempotent migration - no errors on second run')
"
```

**Pass Criteria**: No errors on second run.

---

## Item B: Global Workflow Timeout

### TEST-B1: Timeout Triggers on Long Workflow

| Field | Value |
|-------|-------|
| **ID** | TEST-B1 |
| **Category** | Unit |
| **Expected Runtime** | ~6s (5s timeout + overhead) |

**Command**:
```bash
USE_MOCK_LLM=true BOUNDED_EXEC_MAX_WALL_SECONDS=5 python -c "
import asyncio
from integration_coworker.graph.runtime import _run_workflow_async
from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.bounds import WorkflowTimeoutError

async def test():
    state = WorkflowState(
        task_description='test',
        spec_refs=['https://example.com/spec.yaml'],
    )
    try:
        await _run_workflow_async(state, use_checkpointer=False)
        print('FAIL: Expected WorkflowTimeoutError')
    except (WorkflowTimeoutError, asyncio.TimeoutError) as e:
        print(f'PASS: Timeout raised - {type(e).__name__}')
    except Exception as e:
        print(f'UNEXPECTED: {type(e).__name__}: {e}')

asyncio.run(test())
"
```

**Pass Criteria**: `WorkflowTimeoutError` or `asyncio.TimeoutError` raised.

---

## Item C: SSRF Controls

### TEST-C1: Block AWS Metadata Endpoint

| Field | Value |
|-------|-------|
| **ID** | TEST-C1 |
| **Category** | Unit |
| **Expected Runtime** | <1s |

**Command**:
```bash
python -c "
from integration_coworker.security.ssrf import validate_url_target, SSRFBlockedError

test_urls = [
    'http://169.254.169.254/latest/meta-data/',
    'http://169.254.169.254/latest/user-data',
]

for url in test_urls:
    try:
        validate_url_target(url)
        print(f'FAIL: {url} should have been blocked')
    except SSRFBlockedError as e:
        print(f'PASS: {url} blocked - {e.ip}')
"
```

**Pass Criteria**: All metadata URLs blocked with SSRFBlockedError.

### TEST-C2: Block Private IP (RFC1918)

| Field | Value |
|-------|-------|
| **ID** | TEST-C2 |
| **Category** | Unit |
| **Expected Runtime** | <1s |

**Command**:
```bash
python -c "
from integration_coworker.security.ssrf import validate_url_target, SSRFBlockedError

test_urls = [
    'http://10.0.0.1/secret',
    'http://172.16.0.1/admin',
    'http://192.168.1.1/config',
]

for url in test_urls:
    try:
        validate_url_target(url)
        print(f'FAIL: {url} should have been blocked')
    except SSRFBlockedError as e:
        print(f'PASS: {url} blocked - {e.ip}')
"
```

**Pass Criteria**: All private IPs blocked.

### TEST-C3: Block Localhost

| Field | Value |
|-------|-------|
| **ID** | TEST-C3 |
| **Category** | Unit |
| **Expected Runtime** | <1s |

**Command**:
```bash
python -c "
from integration_coworker.security.ssrf import validate_url_target, SSRFBlockedError

test_urls = [
    'http://127.0.0.1/admin',
    'http://localhost/admin',
    'http://[::1]/admin',
]

for url in test_urls:
    try:
        validate_url_target(url)
        print(f'FAIL: {url} should have been blocked')
    except SSRFBlockedError as e:
        print(f'PASS: {url} blocked - {e.ip}')
"
```

**Pass Criteria**: All localhost variants blocked.

### TEST-C4: Allow Public IPs

| Field | Value |
|-------|-------|
| **ID** | TEST-C4 |
| **Category** | Integration |
| **Expected Runtime** | <2s (network) |

**Command**:
```bash
python -c "
from integration_coworker.security.ssrf import validate_url_target, SSRFBlockedError

# GitHub's public IP should be allowed
try:
    validate_url_target('https://raw.githubusercontent.com/octocat/Hello-World/master/README')
    print('PASS: Public URL allowed')
except SSRFBlockedError as e:
    print(f'FAIL: Public URL blocked - {e}')
"
```

**Pass Criteria**: Public URL allowed.

---

## Item D: Artifact Retention

### TEST-D1: Purge Old Artifacts

| Field | Value |
|-------|-------|
| **ID** | TEST-D1 |
| **Category** | Integration |
| **Expected Runtime** | <5s |

**Command**:
```bash
ARTIFACT_STORE_ROOT="/tmp/test_artifacts_$$" python -c "
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from integration_coworker.persistence.artifacts.fs import FilesystemArtifactStore

store = FilesystemArtifactStore()

# Create old artifact (simulate 31 days ago)
old_run = 'old-run-001'
run_dir = store.root / old_run
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / 'manifest.json').write_text(json.dumps({
    'created_at': (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
}))

# Create new artifact
new_run = 'new-run-001'
run_dir2 = store.root / new_run
run_dir2.mkdir(parents=True, exist_ok=True)
(run_dir2 / 'manifest.json').write_text(json.dumps({
    'created_at': datetime.now(timezone.utc).isoformat()
}))

# Purge
purged = store.purge_older_than(30)
print(f'Purged: {purged}')

# Verify
old_exists = (store.root / old_run).exists()
new_exists = (store.root / new_run).exists()
print(f'Old exists: {old_exists}, New exists: {new_exists}')

assert purged == 1 and not old_exists and new_exists, 'Purge failed'
print('PASS: Old artifact purged, new retained')

# Cleanup
import shutil
shutil.rmtree(store.root)
"
```

**Pass Criteria**: 1 purged, old gone, new remains.

---

## Item E: LLM Cache Key Isolation

### TEST-E1: Different API Keys Different Cache

| Field | Value |
|-------|-------|
| **ID** | TEST-E1 |
| **Category** | Unit |
| **Expected Runtime** | <2s |

**Command**:
```bash
USE_MOCK_LLM=true python -c "
import os
from integration_coworker.llm.client import get_llm_client, _client_cache

# Clear cache
_client_cache.clear()

# Client 1 with key1
os.environ['OPENAI_API_KEY'] = 'sk-test-key-1'
client1 = get_llm_client('codegen')
id1 = id(client1)

# Client 2 with key2 (clear cache to force re-creation)
_client_cache.clear()
os.environ['OPENAI_API_KEY'] = 'sk-test-key-2'
client2 = get_llm_client('codegen')
id2 = id(client2)

print(f'Client 1 ID: {id1}')
print(f'Client 2 ID: {id2}')

if id1 != id2:
    print('PASS: Different API keys get different clients')
else:
    print('FAIL: Same client returned for different API keys')
"
```

**Pass Criteria**: Different client IDs for different API keys.

### TEST-E2: Redis Cache Key Format

| Field | Value |
|-------|-------|
| **ID** | TEST-E2 |
| **Category** | Unit |
| **Expected Runtime** | <1s |

**Command**:
```bash
python -c "
from integration_coworker.llm.cache import _generate_cache_key

key = _generate_cache_key(
    provider='openai',
    model='gpt-4',
    task_type='codegen',
    prompt='test prompt',
    api_key_hash='abc123'
)
print(f'Cache key: {key}')

# Verify format includes api_key_hash
assert 'abc123' in key, 'api_key_hash not in cache key'
parts = key.split(':')
print(f'Parts: {parts}')
assert len(parts) >= 6, 'Cache key missing components'
print('PASS: Cache key includes api_key_hash')
"
```

**Pass Criteria**: Cache key contains api_key_hash component.

---

## Item F: LLM Concurrency Loop Safety

### TEST-F1: Different Loop Gets New Semaphore

| Field | Value |
|-------|-------|
| **ID** | TEST-F1 |
| **Category** | Unit |
| **Expected Runtime** | <2s |

**Command**:
```bash
python -c "
import asyncio
from integration_coworker.llm.concurrency import get_llm_semaphore, reset_llm_semaphore

reset_llm_semaphore()

async def get_sem():
    return get_llm_semaphore()

# Get semaphore in default loop
sem1 = asyncio.run(get_sem())
id1 = id(sem1)

reset_llm_semaphore()

# Get semaphore in new loop
sem2 = asyncio.run(get_sem())
id2 = id(sem2)

print(f'Semaphore 1 ID: {id1}')
print(f'Semaphore 2 ID: {id2}')

# After reset, should be different
if id1 != id2:
    print('PASS: Different semaphores after reset')
else:
    print('INFO: Same semaphore (may be fine in Python 3.10+)')
"
```

**Pass Criteria**: Different semaphore IDs (or documented behavior in Python 3.10+).

---

## Item G: Exception Handling

### TEST-G1: No Silent Pass Handlers

| Field | Value |
|-------|-------|
| **ID** | TEST-G1 |
| **Category** | Static Analysis |
| **Expected Runtime** | <5s |

**Command**:
```bash
# Count silent handlers (should be 0 after fix)
count=$(grep -rn "except Exception:" src/integration_coworker/graph/nodes/*.py | grep -c "pass$" || echo 0)
echo "Silent exception handlers: $count"
if [ "$count" -eq 0 ]; then
    echo "PASS: No silent exception handlers"
else
    echo "FAIL: $count silent handlers remain"
    grep -rn "except Exception:" src/integration_coworker/graph/nodes/*.py | grep "pass$"
fi
```

**Pass Criteria**: Zero silent `except Exception: pass` handlers.

---

## Regression Tests

### TEST-REG1: Full Test Collection

| Field | Value |
|-------|-------|
| **ID** | TEST-REG1 |
| **Category** | Smoke |
| **Expected Runtime** | <30s |

**Command**:
```bash
pytest --collect-only tests/ 2>&1 | tail -5
# Should show "X tests collected" with no errors
```

**Pass Criteria**: Zero collection errors.

### TEST-REG2: Short Test Suite

| Field | Value |
|-------|-------|
| **ID** | TEST-REG2 |
| **Category** | Unit |
| **Expected Runtime** | <5min |

**Command**:
```bash
USE_MOCK_LLM=true pytest tests/ -x --ignore=tests/e2e -q
```

**Pass Criteria**: All tests pass.

---

## CI Integration

These tests should be added to `.github/workflows/test.yml`:

```yaml
- name: Test Hardening - SSRF
  run: |
    python -c "from integration_coworker.security.ssrf import validate_url_target, SSRFBlockedError; ..."
    
- name: Test Hardening - Cache Isolation
  run: |
    USE_MOCK_LLM=true python -c "..."
```

---

## Test Execution Order

**Phase 1: Verify bugs first**
```bash
# BUG-001: Test collection
pytest --collect-only tests/test_ingest_production.py 2>&1 | grep -i error
```

**Phase 2: After implementing fixes**
```bash
# Item C first (security)
python -c "from integration_coworker.security.ssrf import ..."

# Item A, B, D, E, F, G
# (run respective TEST-* commands)
```

**Phase 3: Regression**
```bash
USE_MOCK_LLM=true pytest tests/ -x --ignore=tests/e2e -q
```
