# Production Readiness — Final Steps Checklist (commands + expected results)

> **Date**: 2025-12-16
>
> This is the “runbook checklist” for getting to a credible V1 production-ready claim.
> It’s intentionally mechanical: run X, expect Y.

## Preconditions

- Use Python 3.11 venv: `.venv311`
- Do **not** paste secrets into terminals/logs.

## Step 1 — Docs gates

### 1.1 MkDocs strict + docs pytest + docs audit

```bash
make docs-verify
```

**Expect**:
- Exit code 0.

## Step 2 — Postgres (Compose) validation

### 2.1 Start DB

```bash
docker-compose up -d db
```

**Expect**:
- Container `db` is healthy.

### 2.2 Init schema

```bash
.venv311/bin/python scripts/init_db_postgres.py
```

**Expect**:
- Schema initializes successfully.

### 2.3 Run Postgres/pgvector tests

```bash
.venv311/bin/python -m pytest tests/test_pgvector_search.py -q
```

**Expect**:
- PASS.

## Step 3 — End-to-end safety check (dry-run)

> This verifies scoped edits without writing to disk.

```bash
.venv311/bin/python - <<'PY'
from pathlib import Path
from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions

repo_root = Path('/tmp/icw-e2e-minrepo')

result = design_and_generate_integration(
  spec_refs=['specs/httpbin_api.json'],
  task_description='Add a minimal httpbin integration example. Keep changes small.',
  repo_root=repo_root,
  options=IntegrationOptions(dry_run=True),
)

rcs = result.repo_changes
paths = [c.rel_path for c in (rcs.changes if rcs else [])]

print('change_count=', len(paths))
print('paths=')
for p in paths:
  print(' -', p)
print('errors_count=', len(result.errors or []))
PY
```

**Expect**:
- A small, bounded file list.
- Repo working tree remains clean.

## Step 4 — REAL-mode “invalid creds” guardrail test (must be safe)

**Goal**: With invalid creds, the system should:
- fail fast
- redact logs
- avoid “skeleton fallback success” semantics

**Current status**: FAIL (BUG-0003, BUG-0005). This is a P0 blocker until fixed.

Once fixed, re-run the scenario and expect:
- exit/failure indicated clearly
- logs contain no tokens

## Step 5 — Recovery semantics alignment

**Goal**: Docs/ADR must match reality:
- If `--resume/--auto-resume` is real and supported, ADR-0009 cannot say “none exist in v1” without qualification.

**Current status**: mismatch exists (confirmed).

## What to update when a step is completed

- Update `docs/development/PROD_BUG_LOG.md` (close or downgrade severity as tests land)
- Update `docs/development/V1_PROD_READINESS_BACKLOG.md` (mark item done only with evidence)
- Keep `docs/development/PROD_IMPLEMENTATION_PLAN.md` as the canonical execution plan
