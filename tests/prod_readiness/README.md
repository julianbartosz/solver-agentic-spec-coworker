# prod_readiness (Phase 2 plan)

This folder is reserved for **production-readiness validations** that are directly tied to:

- `docs/development/V1_PROD_READINESS_AUDIT.md`
- `docs/development/V1_PROD_READINESS_BACKLOG.md`
- The project’s v1 goals in `archive/docs/design/design-doc.md`
- Known production-facing bugs recorded in `docs/development/PROD_BUG_LOG.md`

This README is a **runnable Phase 2 plan** (commands + expected outputs + where to store proof). It does **not** add tests yet.

## Phase 2: what “V1 prod-ready” means (practical bar)

Based on the design doc, v1 is a **local developer-run workflow** that:

- parses spec(s), builds Silver/Gold, generates code + tests, and can optionally wire into a reference repo
- persists Silver/Gold to Postgres (SQLite is allowed for local/tests)
- does not require a hosted deployment stack (explicit non-goal)

Phase 2 validations should therefore prove (with recorded artifacts) that:

1. Operator validation gates run (docs strict, mkdocs invariants, docs audit)
2. Postgres mode runs and persists expected tables/rows (pgvector included)
3. Repo integration works on representative repo shapes (at least the fixtures)
4. “REAL” LLM mode is safe and honest:
	- **no secrets in logs** (even partial fragments)
	- **auth failures fail fast** (do not proceed with “success-ish” skeleton outputs)

## Where results go (proof artifacts)

Create a dated folder under `tests/prod_readiness/results/` and store:

- `env.txt` (names only; no values)
- `commands.txt` (exact commands run, with secrets redacted)
- `outputs/` (captured stdout/stderr excerpts; **redacted**)
- `db_proof.sql` (queries used) and `db_proof.txt` (query outputs)

Suggested naming: `tests/prod_readiness/results/YYYY-MM-DD_<short-label>/`.

## Phase 2 runbook (minimal, repeatable)

### A) Docs gate parity (CI-equivalent)

Goal: prove the operator checklist in `docs/operations/validation.md` is runnable.

Run:

```bash
make docs-verify
```

Expected:

- `mkdocs build --strict` succeeds
- `python -m pytest tests/test_mkdocs.py -q` succeeds
- `python scripts/docs_audit.py` succeeds

Record:

- `commands.txt`: the full command
- `outputs/`: the terminal output (redacted if needed)

### B) Postgres + pgvector (persistence + retrieval)

Goal: verify the “production persistence” posture for v1 (design doc + architecture doc), using the smallest test surface that exercises pgvector retrieval.

Prep:

```bash
docker-compose up -d db
```

Run:

```bash
export DATABASE_URL='postgresql://integration:integration@localhost:5432/integration_coworker'
export POSTGRES_TESTS_REQUIRED=true
PYTHONPATH=src .venv311/bin/python -m pytest tests/test_pgvector_search.py -q
```

Expected:

- pass signal consistent with BUG-0002’s “33 passed” verification note in `PROD_BUG_LOG.md`

Record:

- test output
- follow-up DB proof queries if needed (table existence + representative rows)

### C) Repo integration (fixture archetypes)

Goal: verify repo detection + file placement behaviors on known archetypes (no real external repo required).

Run (examples; keep stable names aligned to existing tests):

```bash
PYTHONPATH=src USE_SQLITE=true .venv311/bin/python -m pytest tests/test_repo_file_writes.py -q
```

Record:

- test output
- any temp repo paths printed by the tests

### D) REAL-mode safety and honesty (credential handling)

Goal: validate the bug-log blockers are resolved:

- no credential fragments in logs
- invalid credentials fail fast

Important: do this only in a disposable environment. Never store real keys in the repo.

Two sub-scenarios:

1) **Invalid credentials** (expected failure)

```bash
export LLM_MODE=REAL
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-4o-mini
# export OPENAI_API_KEY='<intentionally invalid/redacted>'
python scripts/real_llm_smoke.py
```

Expected:

- process terminates early with a clear error
- logs contain **no key fragments** (including prefixes like `sk-...`)
- no “success-ish” skeleton outputs reported as a successful run

2) **Valid credentials** (manual signoff only)

- Only run when you have valid keys and an explicit budget.
- Record pass/fail signal and redaction posture.

## What belongs here (when we start adding tests)

- docs/ops invariants tests (example: “compose service names referenced in docs exist”)
- smoke tests that **mirror operator commands** (not unit tests)
- “policy” tests that enforce safety contracts (redaction + fail-fast semantics)

## What does *not* belong here

- broad unit tests (keep in existing `tests/` layout)
- benchmarks/perf suites (use `tests/perf/`)

## Current status

Plan only. No tests have been added yet.
