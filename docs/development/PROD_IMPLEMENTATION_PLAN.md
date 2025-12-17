# Production Readiness — Implementation Plan (no-interpretation)

> **Date**: 2025-12-16
>
> **Goal**: Provide a straight-line execution plan to reach a credible “V1 production-ready (Compose)” claim.
> This is deliberately **procedural**: tasks, commands, acceptance criteria, and artifacts.
>
> **Do not** mark tasks complete without one of: a test proving it, a gate passing, or a traceable code review link.
>
> Inputs:
> - `docs/development/PROD_BUG_LOG.md` (authoritative observed failures)
> - `docs/development/V1_PROD_READINESS_BACKLOG.md` (evidence-first backlog)
> - `docs/development/PROD_READINESS_CURRENT_STATE.md` (capability matrix)
> - `docs/development/PROD_READINESS_GAPS_AND_OPTIONS.md` (gap → options)

## Stop-the-line criteria (must fix before any prod-ready claim)

These are P0 items (block any credible claim):

1) **No secrets in logs**
   - Evidence: BUG-0003, BUG-0005.
2) **REAL-mode auth failure semantics** (fail fast; no “success-ish” run)
   - Evidence: BUG-0003, BUG-0005.
3) **Docs/ops gates runnable and green**
   - Evidence: `make docs-verify` must pass.

## Phase 0 — Keep gates green (docs + audit)

### Task 0.1 — Strict docs build stays clean

**Command(s)**:

```bash
make docs-verify
```

**Accept**:
- Exit code 0.
- No mkdocs strict-mode aborts.

**Notes / known gotchas confirmed**:
- MkDocs strict can abort on mkdocs-autorefs broken xrefs in *non-nav* docs.
- Example bug: `docs/development/V1_PROD_READINESS_BACKLOG.md` had an unresolved `[A10-async]` key and caused strict abort; fixed by removing the bracket-style key and using plain text.

### Task 0.2 — Docs audit output refreshed

**Command(s)**:

```bash
.venv311/bin/python scripts/docs_audit.py
```

**Accept**:
- `docs/development/docs_audit_deliverable.md` rewritten.

## Phase 1 — Log redaction contract (P0)

### Task 1.1 — Add global log redaction filter

**Purpose**: Ensure credential-like strings do not appear in logs even when provider SDKs include them.

**Inputs**:
- BUG-0003 / BUG-0005 show repeated 401 payloads containing partial `sk-…` fragments.

**Work items**:
- Add a logging filter (or formatter wrapper) that scrubs:
  - OpenAI keys: `sk-...`
  - Anthropic keys (if used)
  - LangSmith keys
  - Basic-auth URLs (user:pass@host)

**Accept**:
- New unit test that emits a log record containing representative secrets and asserts output is scrubbed.
- Re-run the minimal “invalid creds” scenario and confirm logs contain no credential fragments.

**Suggested test location**:
- `tests/prod_readiness/test_log_redaction.py` (new)

### Task 1.2 — Provider error rendering policy

**Purpose**: Prevent raw provider payloads from being logged.

**Work items**:
- Identify the single place where provider exceptions are converted to strings/logged.
- Replace with safe typed error (status code + safe message).

**Accept**:
- Representative provider exception text never prints key-like patterns.
- Tests cover this path.

## Phase 2 — REAL-mode fail-fast policy (P0)

### Task 2.1 — Classify auth failures as terminal

**Inputs**:
- BUG-0003 / BUG-0005: workflow continues after 401 and emits skeleton fallbacks.

**Work items**:
- Add a classification function for LLM + embedding exceptions:
  - 401/403 → terminal in REAL
  - timeouts/5xx/rate-limit → retryable per existing policy

**Accept**:
- With invalid creds, `design_and_generate_integration(..., REAL ...)` returns an `IntegrationResult` with non-empty `errors` and a failure marker (and does **not** proceed to codegen fallback).

### Task 2.2 — Optional credential preflight (recommended)

**Purpose**: Fail before running the workflow to save time/cost.

**Work items**:
- On startup (CLI and API entrypoint), run a minimal provider call (or model list / embed call) and abort fast on 401/403.

**Accept**:
- A failing preflight prevents the full workflow from executing.

## Phase 3 — DB lifecycle warnings on error paths (P0/P1)

### Task 3.1 — Eliminate unclosed ConnectionWrapper warnings

**Inputs**:
- BUG-0003 / BUG-0005: `ConnectionWrapper was garbage collected without being closed`.

**Work items**:
- Identify the exact code path that creates a DB connection without a context manager.
- Convert to `with db.get_connection() as conn:` or equivalent.

**Accept**:
- Re-run the failing invalid-keys scenario: warning does not appear.
- Add regression test that fails if warning is emitted.

## Phase 4 — Recovery semantics reconciliation (docs + tests)

### Task 4.1 — Decide source of truth: ADR vs code

**Confirmed mismatch**:
- ADR-0009 says “Skip not implemented, no checkpoint persistence in v1”.
- CLI exposes `--auto-resume/--resume` and loads checkpoints via `integration_coworker.persistence.checkpoints`.
- Checkpoint persistence code exists (`save_checkpoint`, etc.).

**Work items**:
- Update ADR-0009 status and/or wording to reflect the current implementation OR explicitly mark current behavior as “post-v1 implementation” with guarantees TBD.

**Accept**:
- No direct contradictions remain between ADR and user-facing behavior.

### Task 4.2 — Contract tests for resume/auto-resume

**Work items**:
- Add tests that:
  - write checkpoints
  - resume from next node deterministically

**Accept**:
- Tests pass on both SQLite and Postgres paths (as applicable).

## Phase 5 — Repo detection ergonomics (P2)

### Task 5.1 — Add ergonomic helper for profiles

**Inputs**:
- BUG-0004: ad-hoc scripts misread fields; arg order for `build_effective_repo_profile()` is easy to misuse.

**Work items**:
- Add `get_repo_profile(repo_root: str, *, use_llm_refinement: bool=False) -> RepoProfile` helper.
- Add docstring/examples showing correct field printing.

**Accept**:
- Running a short snippet prints language/confidence/evidence and a populated `RepoProfile` without ambiguity.

## Final production-ready gate (what “done” means)

A “V1 production-ready (Compose)” claim requires:

1) **Security**
   - Log redaction tests pass.
   - Invalid creds in REAL mode fail fast and do not leak keys.

2) **Reliability**
   - No DB lifecycle warnings during failure paths.
   - Postgres pgvector tests stay green.

3) **Operator gates**
   - `make docs-verify` PASS.
   - Postgres-mode tests PASS.

4) **Repo integration**
   - End-to-end dry-run produces a stable `RepoChangeSet`.
   - Apply-run produces scoped edits and rollback is clean (git clean after revert).
