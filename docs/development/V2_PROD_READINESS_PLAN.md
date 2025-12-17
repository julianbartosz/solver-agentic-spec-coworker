# V2 Production Readiness Plan (evidence-first)

> **Intent**: define what “V2 production readiness” means for this repo, in a way that is measurable and backed by proof artifacts (tests, scripts, logs, and reproducible runs).
>
> This plan is written against **current repo reality** (code + scripts + bug log), not historical “V2/V3” labels in older docs.

## What changes from the V1 bar

V1 readiness (as defined in `docs/development/V1_PROD_READINESS_AUDIT.md`) is primarily: “single-host Compose + Postgres + operator docs gates + runnable workflow”, with known blockers around log redaction + REAL-mode fail-fast.

V2 production readiness raises the bar in three ways:

1. **Truthful execution semantics**
   - REAL LLM runs must be reliable signals (fail fast on auth failures; no misleading skeleton success).
2. **Safety invariants are enforced**
   - Secrets must never appear in logs; provider error payloads must be safely rendered.
3. **Operational evidence becomes first-class**
   - Timing, resource envelopes, and DB behavior are proven via repeatable scripts and recorded artifacts.

## V2 definition (what “production” means)

For V2, “production-ready” means:

- A **single-host** deployment remains the reference (Compose + Postgres), but we require *stronger* operator guarantees.
- The workflow is safe to run with REAL provider credentials in a controlled environment.
- We can show **proof artifacts** for: correctness, safety, reliability, and performance.

> Out of scope for V2 unless explicitly added later: multi-tenant hosted service, horizontal scaling, SaaS authn/z.

## V2 contract (tiny spec)

### Inputs

- Spec refs: local file paths OR remote URLs
- Task string (natural language)
- Optional repo root path for repo-aware changes
- Postgres connection (`DATABASE_URL`)
- LLM credentials via env vars

### Outputs

- Persisted Silver/Gold records in Postgres when `dry_run=false`
- A deterministic run report (`report_markdown`)
- Repo changes applied only when repo integration is explicitly enabled
- Machine-parseable run outcome surface (CLI JSON)

### Error modes (must be explicit)

- Auth failures (401/403): terminal failure in REAL mode
- Timeouts/rate limits: retryable where possible; still must be redacted
- DB failures: must fail with actionable message and clean connection shutdown

## Milestones & acceptance criteria

### M0 — Baseline evidence refresh (1–2 days)

**Goal**: ensure the V1 evidence surface is up-to-date and scripts remain runnable.

Acceptance criteria:

- `python scripts/production_demo.py --specs httpbin --verbose` passes with:
  - `CODEGEN_PROFILE=production`
  - `PARALLEL_WORKFLOW=true`
  - Postgres configured
- `python scripts/test_production_postgres.py` passes (5/5)

Proof artifacts:

- Redacted log excerpt saved as a text artifact under `logs/` or referenced in bug log.

### M1 — Safety contract: global log redaction (P0)

**Goal**: make it *impossible* to leak credential fragments in logs.

Acceptance criteria:

- A global log redaction filter exists and is installed in all entrypoints:
  - CLI (`integration_coworker.cli`)
  - Python API entrypoint (`design_and_generate_integration`)
  - Any worker/background entrypoints (if relevant)
- A test demonstrates that representative secret patterns are scrubbed:
  - OpenAI `sk-...`
  - Anthropic key prefix patterns
  - LangSmith keys
  - Basic-auth URL credentials

Proof artifacts:

- Unit tests under `tests/prod_readiness/` that assert redaction in log records.

### M2 — Truthful REAL-mode semantics (P0)

**Goal**: REAL mode is a trustworthy production validation signal.

Acceptance criteria:

- Auth failures (401/403) abort the run quickly:
  - No repeated provider calls after first auth failure
  - No fallback “success-ish” report
  - Errors are redacted

- Optional but recommended: single-shot preflight check that validates credentials
  before expensive workflow steps.

Proof artifacts:

- A scripted repro (with intentionally invalid credentials) that fails fast and is redacted.

### M3 — DB correctness & lifecycle (P0/P1)

**Goal**: Postgres behavior is correct under success and failure.

Acceptance criteria:

- No “returned connection in transaction” warnings during happy-path runs.
- DB seeding does not emit “current transaction is aborted” during init.
- On failure paths, connections are closed/returned cleanly.

Proof artifacts:

- Repeatable DB init + seed script run with clean output.
- A regression test or a minimal integration test for seeding transaction handling.

### M4 — Performance & resource envelope (P1)

**Goal**: demonstrate the design-doc operational target with proof.

Acceptance criteria:

- End-to-end run time measured and recorded for at least:
  - httpbin (small)
  - stripe (medium)
- Target: < 5 minutes for the typical “small/medium” spec run on a dev laptop.

Proof artifacts:

- A “timing harness” script that prints:
  - total duration
  - per-node timings (if available)
  - peak memory (optional)

### M5 — Repo integration safety (P1)

**Goal**: repo writes are predictable, reversible, and validated.

Acceptance criteria:

- Repo changes are always on a new branch.
- A summary of changes is produced.
- At least one reference repo profile is validated end-to-end (diff + tests).

Proof artifacts:

- A CI-ish script that:
  - clones/sets up a disposable target repo
  - runs integration
  - runs repo tests

## Recommended work plan (sequenced)

1. **Close the remaining P0 safety gaps** (`SEC-log-redaction-global`, `LLM-real-mode-fail-fast-policy`).
2. **Stabilize DB lifecycle** (seeding transactions, connection rollback warnings).
3. **Add timing harness** + record evidence for the <5 min target.
4. **Harden repo integration** (branching, diffs, tests).

## Evidence & reporting rules

- Every milestone must be backed by at least one:
  - runnable script in `scripts/`
  - unit/integration test in `tests/`
  - and a redacted excerpt in `docs/development/PROD_BUG_LOG.md` or a dated log artifact under `logs/`

- No secrets in docs/logs. Always redact values.

## Current status snapshot (as of 2025-12-16)

- Parallel workflow concurrency issue on `file_specs`: fixed (production demo passes).
- Production Postgres gate script: passing (5/5) after making the “valid code” gate non-brittle.
- Remaining known P0s (from bug log/backlog):
  - REAL-mode log redaction
  - REAL-mode fail-fast policy on auth errors
  - DB seeding transaction warnings (needs triage)
  - KG delta accounting anomaly (negative deltas observed)
