# V1 Production Readiness Backlog (evidence-first)

This backlog is derived from:

- The V1 readiness audit (docs/code/tests evidence).
- The project’s stated v1 success criteria and non-goals in the design doc.
- The repo’s production-level validation results recorded in `docs/development/PROD_BUG_LOG.md`.

Every item includes **evidence citations**.

> **Hard rule**: do not mark anything “done” here without a direct evidence citation (PR/commit/test/proof).

## Priority scale

- **P0** = blocks any meaningful “prod-ready” claim for v1 Compose deployment
- **P1** = high value / reduces operator risk substantially
- **P2** = improves robustness / reduces confusion

## Backlog items (prioritized)

## Cluster: exceeds V1 expectations (keep, but make safe + explicit)

These items represent capabilities that appear to be implemented in the current codebase (sometimes labeled “V2/V2.1” in comments/help text), but they are not required to claim basic v1 readiness per the design doc. They are still valuable, but they must be:

1) documented as *best-effort vs guaranteed*,
2) backed by contractual tests, and
3) integrated into the operator runbook.

### EXCEEDS-recovery-checkpointing-and-resume (P1)

**Problem**: Checkpoint persistence + resume/auto-resume appear implemented and are useful for iterative workflows, but v1 readiness shouldn’t depend on them unless we define explicit guarantees and tests.

- **Evidence**:
  - CLI `run` includes `--auto-resume`/`--resume` and executes a resume flow using persisted checkpoints. [A6]
  - Checkpoint persistence code exists for Postgres and SQLite (`run_checkpoints`). [A5]
  - Design doc defines v1 goals around local runs + persistence + codegen; it does not require checkpoint-based recovery as a must-have to claim v1 viability. [B7]

**Impact**: Without a contract, operators may assume durability semantics that aren’t guaranteed (especially on process crash).

**Options**:
1. Document this as “best-effort resume” and add tests to lock behavior (recommended).
2. Tighten semantics to be crash-safe and claim a stronger guarantee (heavier lift).

**Best ignoring sunk cost**: keep the capability and add contractual tests + docs so it’s safe and predictable.

**Best fit for V1**: treat as P1 improvement; don’t gate v1 readiness on it until contracts exist.

**Concrete plan**:
- Add Phase 2 tests that simulate checkpoints and verify `--resume` behavior.
- Update ops docs: document when resume is expected to work and when it’s not.

---

### EXCEEDS-prompt-safety-foundations (P1)

**Problem**: The codebase contains concrete prompt-safety building blocks (input sanitization + system prompt hardening), which are valuable beyond the v1 bar — but we do not yet have an explicit “safety contract” that guarantees:

- these controls are always applied at the right boundaries, and
- logs never leak credential fragments (including provider error payloads).

- **Evidence**:
  - Sanitization utilities exist (`sanitize_task_description`, `sanitize_spec_content`) along with injection-pattern detection. [A8]
  - System prompt hardening exists (`harden_system_prompt`) and is used in both sync and async LLM clients (“Harden system prompt” comments).
    - Sync: `src/integration_coworker/llm/client.py` (A9)
    - Async: `src/integration_coworker/llm/async_client.py` (A10-async; see Sources Index)
  - The design doc’s v1 success criteria explicitly include “Logging + redaction” as a baseline pattern. [B7]
  - The production bug log records credential fragment leakage in logs during REAL-mode failures (blocker). [B9]

**Impact**: This is an “exceeds-v1” capability bundle that can materially improve safety and reliability — but without enforcement, it can create false confidence.

**Options**:
1. Define a minimal “prompt safety contract” and add enforcement tests (recommended).
2. Keep as best-effort only and do not cite it as a v1 capability.
3. Extend to a broader security policy engine (larger redesign; not needed for v1).

**Best ignoring sunk cost**: finish the safety story: make it contractually enforced and observable (tests + explicit log filtering).

**Best fit for V1**: treat this as P1 “iteration win” that we can advertise only after it has minimal enforcement: (a) redaction guarantee, (b) hardening always applied, (c) fail-fast policy on auth errors.

**Concrete plan**:
- Add Phase 2 tests (under `tests/prod_readiness/`) that:
  - assert `harden_system_prompt()` is applied in the LLM client call path for at least one node
  - assert representative secret patterns are scrubbed from log records (OpenAI `sk-...` and similar)
- Add a short operator note in `docs/operations/validation.md`: “REAL-mode safety contract” (redaction + fail-fast). Reference the bug log.

---

### ALIGN-v1-definition-to-design-doc (P0)

**Problem**: “V1 production-ready” must be defined by current project goals (design doc) and observed behavior (code/tests/bug log), not by stale labels like “V2/V3” in help strings.

- **Evidence**:
  - Design doc success criteria explicitly define v1 outcomes (integration code + tests + optional repo wiring + Postgres persistence, run time targets) and also define v1 non-goals (no hosted production deployment stack). [B7]
  - The public entrypoint `design_and_generate_integration()` generates a run_id early and runs the LangGraph workflow via `run_workflow(state)`, returning a structured `IntegrationResult` including persistence artifacts, doc chunks, and errors. [B8]

**Impact**: Without a shared “what counts as V1 prod-ready,” docs and backlog items get interpreted against the wrong bar.

**Options**:
1. Define V1 readiness in terms of: single-host runnable workflow + Postgres persistence + operator gates + safety invariants + repo-integration correctness on a reference repo.
2. Keep the current audit definition but explicitly tie each checklist item to design doc success criteria.

**Best ignoring sunk cost**: rewrite the audit/backlog definition around the design doc’s v1 success criteria so “V2/V3” labels don’t mislead.

**Best fit for V1**: keep Compose/ops gates as necessary operator criteria, but anchor “done” to design doc success criteria; allow “exceeds expectations” notes when code implements more than the v1 bar.

**Concrete plan**:
- Update `docs/development/V1_PROD_READINESS_AUDIT.md` to add an explicit “V1 goals (from design doc)” subsection and map acceptance checks to those bullets. [B7]
- Update this backlog to use the same vocabulary (Silver/Gold persistence, repo wiring, <5 min target) for every P0/P1 item. [B7]

---

### DOCS-recovery-adr-vs-code-reconcile (P0)

**Problem**: The repo contains active runtime code for checkpoint persistence / resume / skip (labeled “V2” in some help strings), but ADR-0009 and the debt register assert v1 has none of these.

- **Evidence**:
  - ADR-0009 states “None of these exist in v1” and “Skip falls back to retry. No checkpoint persistence.” (see v1 constraints and “Problem” sections). [B4]
  - Runtime CLI includes `--auto-resume` and `--resume` and implements checkpoint loading + `run_from_node(...)` resume flow in the `run` command. (Also labeled “V2” in help text.) [A6]
  - Runtime persistence implements `save_checkpoint`/`load_checkpoint` against Postgres `integration_gold.run_checkpoints` and SQLite `run_checkpoints`. [A5]
  - Runtime recovery implements `skip_failing_step` computing a dependency cascade, not just falling back to retry. [A7]
  - Debt register claims `REC-001..REC-004` are placeholders / absent in v1 (and points at `api/recovery.py` and `graph/runtime.py`). [B5]

**Impact**: Operators and contributors cannot tell which recovery semantics are supported or supported-without-guarantees; docs and ADRs can’t both be true.

**Options**:
1. Declare the recovery features “present but experimental/V2-labeled” and document exact guarantees + failure modes for V1.
2. Update ADR-0009 status/wording to reflect current code reality (or split ADR into “desired v1” vs “implemented”).
3. (Later, code change) Align runtime behavior to ADR (e.g., disable skip/resume) — **not in this docs-only phase**.

**Best ignoring sunk cost**: treat runtime behavior as truth; update ADR/docs to match what the CLI does today, while clearly labeling guarantees and adding tests.

**Best fit for V1**: update user-facing docs (ops + audit) to state exactly what resume/skip/checkpoints do today, mark “V2-labeled” where applicable, and add Phase 2 tests to lock expected semantics.

**Concrete plan** (docs-only in Phase 1; tests in Phase 2):
- Docs-only now:
  - Add a short “Recovery semantics (what happens in practice)” section under `docs/operations/validation.md` linking ADR-0009 and explicitly noting contradictions. [B4]
  - Add explicit cross-links from `docs/ARCHITECTURE.md` recovery sections back to ADR-0009. [B6]
- Phase 2 tests later:
  - Add tests under `tests/prod_readiness/` to assert:
    - checkpoints are written when Postgres/SQLite engines are enabled
    - `--resume` continues from the next node given existing checkpoints
    - skip behavior matches the documented semantics

---

### SEC-log-redaction-global (P0)

**Problem**: The prod bug log records that Real LLM mode emitted partial credential fragments in logs and continued with fallback skeleton outputs instead of failing fast.

- **Evidence**:
  - BUG-0003: “Real LLM mode leaks key fragments in logs + does not fail fast on invalid credentials” (includes observed behavior and recommended fixes). [B9]
  - BUG-0005: repeats the issue in an end-to-end run and additionally reports a DB lifecycle warning about unclosed connections on error paths. [B9]
  - The design doc’s security requirements include “No sensitive response logging” and centralized secret handling patterns as v1 requirements. [B7]

**Impact**: This blocks any credible “prod-ready” claim (secrets in logs) and also undermines the semantics of REAL mode runs.

**Options**:
1. Add a global log filter that scrubs known token patterns and credential-bearing URLs from *all* log records.
2. Add provider adapter error mapping that guarantees safe rendering (typed errors + safe messages).
3. Add both: global filter as belt-and-suspenders + typed errors as the long-term fix.

**Best ignoring sunk cost**: global redaction filter + typed provider errors.

**Best fit for V1**: global redaction filter (fastest safety net) + fail-fast policy for 401/403 in REAL mode (see next item).

**Concrete plan** (code + tests; can be staged):
- Add a logging filter under `src/integration_coworker/llm/` (or shared logging module) and attach it during CLI/API initialization.
- Add tests that assert redaction for representative patterns (OpenAI `sk-...`, Anthropic key patterns, LangSmith keys, basic auth URLs).

---

### LLM-real-mode-fail-fast-policy (P0)

**Problem**: REAL-mode runs continue after provider auth failure and produce fallback skeleton outputs, creating misleading “success-ish” output.

- **Evidence**:
  - BUG-0003 and BUG-0005 describe repeated 401 errors with continued execution and skeleton fallbacks. [B9]
  - Design doc reliability requirements include “Write partial artifacts on failure” and log errors per node; but continuing as if successful after a credential failure violates operator expectations for an authenticated mode. [B7]

**Impact**: Any “real LLM readiness” validation signal becomes unreliable.

**Options**:
1. Fail fast on 401/403 when REAL mode is selected.
2. Add a preflight credential check once at startup and abort before workflow.
3. Make policy explicit via a single `LLMFailurePolicy` (fail-fast vs fallback).

**Best ignoring sunk cost**: explicit policy + preflight.

**Best fit for V1**: fail fast on 401/403 + optionally preflight; keep fallback behavior for transient non-auth errors (timeouts) if desired.

**Concrete plan**:
- Implement a single place where provider exceptions are classified and mapped to “terminal” vs “retryable”.
- Add a Phase 2 validation scenario in `tests/prod_readiness/README.md` that intentionally runs with invalid creds and asserts a redacted, terminal failure.

---

### RETRIEVAL-pgvector-score-contract (P1)

**Problem**: The bug log records a scoring contract mismatch for KG template retrieval in Postgres pgvector mode.

- **Evidence**:
  - BUG-0002 documents failing expectations for `graph_score` baseline and combined-weight formula and records that a minimal patch was applied and verified (`33 passed`). [B9]

**Impact**: Retrieval quality and test stability depend on a stable scoring contract; drifting weights silently changes behavior.

**Options**:
1. Keep the fixed weights as a stable v1 contract and document them.
2. Make weights configurable but lock with tests + defaults.

**Best ignoring sunk cost**: central scoring module + golden tests.

**Best fit for V1**: document the current contract and keep tests enforcing it.

**Concrete plan**:
- Add a short “Retrieval scoring contract” note in ops/dev docs (or move to an ADR) and ensure tests cover both Python and pgvector paths.

---

### OPS-compose-service-name (P2)

**Problem**: Instructions expect `docker compose up -d postgres` but the gap backlog reports that the compose service is named `db` (so `postgres` fails), while other docs already use `db`.

- **Evidence**:
  - Prod matrix uses `docker compose up -d postgres`. [B1]
  - Gap backlog reports `docker compose up -d postgres` → `no such service: postgres`; says service is named `db`. [B2]
  - Postgres ops doc uses `docker-compose up -d db`. [B3]

**Impact**: operator friction at first step; undermines “production posture checks are runnable” claim.

**Options** (choose 1+):
1. Normalize docs/reports to use `db` everywhere.
2. Add a `postgres` alias service in compose (more indirection; risk of drift).

**Best ignoring sunk cost**: keep code/compose as-is and normalize docs to match the actual compose service name. (docs-only change).

**Best fit for V1**: normalize docs/test-matrix text to `db` and ensure validation docs point to the same commands. [B3]

**Concrete plan**:
- Update `archive/docs/reports/PROD_TEST_MATRIX.md` to use `db` for startup, or copy its relevant tables into non-archive docs and treat archive as historical.
- Add a tiny “compose service name” note under `docs/operations/db-postgres.md` troubleshooting.
- Add/extend a docs invariant test (future work) that flags references to nonexistent compose services.

---

### DEPS-postgres-extras (P1)

**Problem**: Postgres path isn’t usable in a base environment without extra dependencies; gap backlog reports missing `psycopg[binary]`, `psycopg_pool`, and `langgraph-checkpoint-postgres`.

- **Evidence**:
  - Gap backlog reports import errors for missing Postgres deps and `langgraph.checkpoint.postgres`. [B2]
  - Postgres ops doc states Postgres extras should be installed via `pip install -e ".[postgres]"`. [B3]

**Impact**: unclear default installation path; operators may believe “production requires Postgres” but be unable to run it from a fresh install.

**Options**:
1. Make Postgres deps part of the default install (largest footprint).
2. Keep as extras but: (a) ensure docs consistently point to the extras install path, (b) ensure CLI errors are crisp, and (c) ensure CI has a Postgres job that installs extras.

**Best ignoring sunk cost**: default install includes Postgres deps if v1 production is Compose-based with Postgres anyway.

**Best fit for V1**: keep extras but make them *hard* prerequisites in deployment docs and validation docs; ensure CI’s Postgres workflow installs them. [B3]

**Concrete plan**:
- Audit `requirements.txt` / `pyproject.toml` vs documented extras.
- Ensure `docs/operations/deployment.md` contains a canonical “install with Postgres extras” step.
- Add a dedicated “Postgres mode” CI job requirement to mirror the prod matrix Postgres run (without running real LLM).

---

### REC-clarity (P0)

**Problem**: ADR-0009 explicitly states v1 has *no checkpoint persistence* and “skip is not implemented” (falls back to retry). This must be reconciled with any runtime UX/CLI docs that imply resumability/checkpointing.

- **Evidence**:
  - ADR says skip requires checkpoint persistence and “None of these exist in v1.” [B4]
  - ADR lists v1 constraints: checkpoint persistence = none; resume capability limited. [B4]

**Impact**: risk of misleading “resume” claims; unclear operational recovery story in production incidents.

**Options**:
1. Update all user-facing docs to match ADR-0009 (v1 = retry-only; no durable resume).
2. Update ADR status/wording if implementation diverged (if code truly has checkpoint persistence/resume, ADR becomes stale).

**Best ignoring sunk cost**: make runtime match ADR (but this is a code change, therefore **not** for the current audit phase).

**Best fit for V1** (audit-phase deliverable): clarify messaging in docs and CLI help text so that operators don’t assume durable resume if it’s not guaranteed.

**Concrete plan**:
- Inventory CLI/docs mentions of “resume”, “auto-resume”, “checkpoint”.
- Add an explicit “V1 recovery semantics” section to `docs/operations/validation.md` and/or a dedicated ops page.
- (Later) Add tests defining expected behavior: “Skip falls back to retry” and “process exit loses state”.

---

### LLM-real-mode-gating (P0)

**Problem**: Real LLM mode is a “production posture check” in the prod test matrix, but the gap backlog explicitly records that it was blocked due to invalid API key and suggests gating.

- **Evidence**:
  - Prod matrix includes “Real LLM mode (cost-capped)” instructions and calls out API keys as required. [B1]
  - Gap backlog: “Real LLM execution blocked by invalid API key” and suggests gating real-LLM suite behind secrets check. [B2]

**Impact**: CI / operators may run “prod” tests without credentials and see failures; ambiguous pass/fail signal.

**Options**:
1. Keep real-LLM paths as manual-only checks (documented but not in CI).
2. Gate real-LLM tests behind explicit env flags + secrets present check.

**Best ignoring sunk cost**: real-LLM suite should never run by default without secrets.

**Best fit for V1**: explicit gating + clear documentation; keep CI default on mock mode.

**Concrete plan**:
- Add a marker/skip condition to real-LLM tests when creds missing.
- Document the gating in production matrix and validation docs.

---

## Sources Index

[B1] `archive/docs/reports/PROD_TEST_MATRIX.md` (lines 1-60): Postgres persistence section and real LLM mode checklist.

[B2] `archive/docs/reports/V1_PROD_GAP_BACKLOG.md` (lines 1-63): ranked gaps including invalid API key blocker, missing Postgres deps, and compose service mismatch.

[B3] `docs/operations/db-postgres.md` (lines 1-60): compose DB startup uses `db`, Postgres+pgvector positioning, and Postgres extras install note.

[B4] `docs/decisions/adr-0009-workflow-recovery-strategy.md` (lines 31-60; 205-232): “skip not implemented”, “none exist in v1”, and v1 constraints table.

[B5] `docs/decisions/TECHNICAL_DEBT_REGISTER.md` (lines 60-140; 150-220): recovery & checkpointing debt items (REC-001..REC-004) and security/prompt safety debt items (SEC-001..SEC-004).

[B6] `docs/ARCHITECTURE.md` (lines 35-90): positions Postgres as production persistence and lists CLI commands/options including `resume` and `--auto-resume`.

[A5] `src/integration_coworker/persistence/checkpoints.py` (lines 1-200): checkpoint serialization/truncation, Postgres+SQLite checkpoint persistence, and completed node queries.

[A6] `src/integration_coworker/cli.py` (lines 320-430): `--auto-resume` / `--resume` flags; resume flow loads checkpoints and continues the graph.

[A7] `src/integration_coworker/api/recovery.py` (lines 1-190): `resume_run`; `skip_failing_step` with dependency cascade.

[B7] `archive/docs/design/design-doc.md` (lines 1-120; 60-120; 130-210): v1 success criteria, operational targets (<5 min), v1 non-goals, and explicit security/reliability requirements.

[B8] `src/integration_coworker/api/entrypoint.py` (lines 1-120): public API entrypoint contract; `IntegrationOptions`; early run_id generation; `run_workflow(state)`.

[B9] `docs/development/PROD_BUG_LOG.md` (lines 1-260): BUG-0002 (pgvector scoring contract), BUG-0003/BUG-0005 (credential fragments in logs; fail-fast semantics; connection lifecycle warning).

[A8] `src/integration_coworker/llm/sanitizer.py` (lines 1-200): suspicious pattern detection and sanitization helpers for user-provided task/spec inputs.

[A9] `src/integration_coworker/llm/client.py` (lines 1-260): sync LLM client hardens system prompts (SEC-001) and documents mode behavior.

- Async LLM client: `src/integration_coworker/llm/async_client.py` (lines 1-320): hardens system prompts before provider calls; includes retry/cache/replay plumbing.
