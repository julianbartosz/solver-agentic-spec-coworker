# V1 Production Readiness Audit (evidence-first)

> **Intent**: capture what “V1 production readiness” means for this repo, what is evidenced today (docs/code/tests), and what is *not* evidenced yet.
>
> **Hard rule for this doc**: any non-trivial claim must be backed by a repo citation (file + line range) in the **Sources Index**.

## Definition (what “production” means for V1)

For V1, “production” is **evidence-limited to the repo’s documented single-host model** (Docker Compose + Postgres), plus the repo’s operator validation gates.

Evidence:

- The validation doc defines a CI-equivalent `make docs-verify` gate and explicitly lists the steps: `mkdocs build --strict`, `pytest tests/test_mkdocs.py`, and `python scripts/docs_audit.py`. (S1)
- The architecture doc positions Postgres + pgvector as “production persistence” (and SQLite as local/tests), and documents `DATABASE_URL` as the “Real LLM + Postgres (production)” mode. (S2)

### V1 readiness acceptance checklist (testable)

This audit treats V1 as “production-ready” only if the following are true:

1. **Docs gate is runnable and strict**
	- `make docs-verify` exists and runs the three sub-steps documented in `docs/operations/validation.md`. (S1)
2. **Operator DB verification is runnable**
	- `scripts/init_db_postgres.py --verify` is documented as the verification command for the Compose Postgres deployment. (S1)
3. **Runtime health surface exists**
	- Operator health checks are documented via `python -m integration_coworker.cli health` and `status`. (S1)
4. **Doc-vs-runtime drift is explicitly called out**
	- If a capability is described in ADRs/docs but disagrees with code, the audit records the mismatch as **documentation drift** (vs runtime) with citations and creates a backlog item (see key findings and backlog reference). (S3, S4)

## Scope

### In scope

- Operator validation gates (docs build, mkdocs invariants test, docs audit).
- Database readiness guidance (Postgres + pgvector, schema init/verify, configuration).
- Runtime health checks via CLI (`health`, `status`).
- Evidence about recovery/resume semantics vs intended v1 behavior (especially ADR-0009).
- Evidence about “production posture” via the archived production test matrix and gap backlog reports.

### Out of scope (for V1 audit)

- Verifying that “real LLM mode” works end-to-end in this environment (requires valid provider credentials; this audit only cites existing repo evidence). (S2)
- Proving Postgres schema init/verify passes on a clean machine; this audit only cites the operator docs and reports (implementation verification belongs to Phase 2). (S1)
- Any runtime code changes or refactors (tracked as backlog items instead). (This repo workflow constraint is the premise of this audit.)

### Explicit non-goals for this audit

- This document does **not** assert that “V2-labeled” features are absent from runtime code; it only records what is evidenced and what conflicts. (S3, S4)

## V1 goals (from the design doc) → evidence today

This section treats the design doc as the authoritative statement of **what v1 is supposed to be**, and then summarizes what we can currently evidence in the repo.

Legend:

- **MET**: evidence exists that the goal is achieved (docs + code + tests/verified output)
- **EXCEEDS**: evidence suggests the repo implements beyond the v1 bar (still needs contracts/tests/docs)
- **GAP**: no evidence, or bug log indicates failures

| V1 design goal | Status | Evidence |
|---|---:|---|
| Single Python function entrypoint + CLI entrypoint | MET | Design goal states “Single Python function + CLI entrypoint”. (S10) The entrypoint exists as `design_and_generate_integration()` returning a structured `IntegrationResult`. (S11)
| Store Silver + Gold in Postgres | MET (with caveats) | Design goal states “Store Silver + Gold in Postgres”. (S10) Postgres+pgvector is positioned as production persistence in the architecture doc. (S2) Postgres pgvector retrieval tests are explicitly recorded as passing after a bugfix (`33 passed`). (S12)
| Baseline patterns include logging + redaction | GAP (blocker) | Design v1 success criteria include “Logging + redaction” as a baseline pattern. (S10) Bug log records credential fragments in logs during REAL-mode failures and calls this unacceptable. (S12)
| “Real LLM mode” behaves as a truthful validation signal | GAP (blocker) | Bug log records repeated 401 errors with continued execution and fallback skeleton outputs (misleading “REAL” semantics) and recommends fail-fast/gating. (S12)
| Repo-aware code updates for a reference repo | MET (fixtures) | Design success criteria include repo-aware updates for a reference repo. (S10) The Phase 2 plan references existing repo file-write tests as the current evidence surface (see `tests/test_repo_file_writes.py` referenced from the production test matrix). (S12)
| Operational target: end-to-end run < 5 minutes | Unknown (needs measurement) | Design doc states “<5 minutes per run”. (S10) This audit does not yet include a recorded timing run with captured proof artifacts; Phase 2 plan is where we record this.

## Capability matrix (evidence)

Legend:

- **Doc** = documented in repo docs
- **Code** = evidence exists in `src/` (runtime surface)
- **Tests** = evidence exists in `tests/` (enforcement)
- **No enforcement** = capability exists in code/docs, but no direct test evidence is cited here (must become backlog)

| Capability | What V1 claims | Evidence present? | Primary evidence |
|---|---|---:|---|
| Docs gate (CI-equivalent) | `make docs-verify` runs mkdocs strict + pytest mkdocs invariants + docs audit | ✅ Doc | `docs/operations/validation.md` (S1)
| Runtime health checks (operator surface) | Operator checks documented: `python -m integration_coworker.cli health` / `status` | ✅ Doc | `docs/operations/validation.md` (S1)
| Recovery: checkpoint persistence | **ADR claims**: none in v1 | ⚠️ Doc drift (ADR vs code) | ADR: `docs/decisions/adr-0009-workflow-recovery-strategy.md` (S4); Code: `src/integration_coworker/persistence/checkpoints.py` (S5)
| Recovery: resume / auto-resume | **Architecture claims**: `resume` command + `--auto-resume` option exist | ✅ Doc + ✅ Code | Doc: `docs/ARCHITECTURE.md` (S2); Code: `src/integration_coworker/cli.py` (S6)
| Recovery: skip | **ADR claims**: skip falls back to retry | ⚠️ Doc drift (ADR vs code) | ADR: `docs/decisions/adr-0009-workflow-recovery-strategy.md` (S4); Code: `src/integration_coworker/api/recovery.py` (S7)
| Prompt injection posture (sanitization) | Sanitizer exists for user input (task) and bounded sanitization for specs | ✅ Code (No enforcement) | Code: `src/integration_coworker/llm/sanitizer.py` (S8)
| Prompt injection posture (system prompt hardening) | Safety preamble is prepended to all system prompts (if called by client) | ✅ Code (No enforcement) | Code: `src/integration_coworker/llm/safety.py` (S9)

## Key findings (evidence-backed)

1) **The operator “docs gate” is explicitly defined and intended to be runnable locally** via `make docs-verify` (mkdocs strict + pytest + docs audit). (S1)

2) **The canonical architecture doc advertises a `resume` command and `--auto-resume` for `run`**, but the repo also contains ADR-0009 which states “none of these exist in v1” for checkpoint persistence/skip. (S2, S4)

3) **Runtime code persists checkpoints to Postgres and SQLite** (`save_checkpoint`/`load_checkpoint` against `run_checkpoints` tables), contradicting ADR-0009’s “none in v1” statement. (S4, S5)

4) **Runtime code implements resume/auto-resume logic in the CLI path**, labeled “V2” in help text but present in the main `run` command implementation. (S6)

5) **Runtime code implements a non-placeholder `skip_failing_step` that computes a dependency cascade**, contradicting ADR-0009’s illustrative placeholder where skip always falls back to retry. (S4, S7)

## PRR checklist (Production Readiness Review)

Each checklist item is either:
- **EVIDENCED** (links to a source), or
- **BACKLOG** (must be addressed in `V1_PROD_READINESS_BACKLOG.md`).

| Item | Status | Evidence / backlog pointer |
|---|---|---|
| Documented validation gate exists (“docs verify”) | EVIDENCED | `docs/operations/validation.md` (S1)
| Documented operator validation gates exist | EVIDENCED | docs/operations/validation.md
| Documented Postgres setup steps exist | EVIDENCED | docs/operations/db-postgres.md
| Schema init/verify is documented and referenced from validation | EVIDENCED | docs/operations/validation.md, docs/operations/db-postgres.md
| “Real LLM” gating (secrets required) is explicitly acknowledged | EVIDENCED | archive/docs/reports/PROD_TEST_MATRIX.md, archive/docs/reports/V1_PROD_GAP_BACKLOG.md
| Clarified recovery semantics (skip/checkpoint/resume) in user-facing docs | BACKLOG | See `docs/development/V1_PROD_READINESS_BACKLOG.md` (REC-clarity)
| Resolved instruction mismatch for Compose service name (`db` vs `postgres`) | BACKLOG | See backlog (OPS-compose-service-name)
| Confirmed default dependency set supports Postgres mode (or documented extras) | BACKLOG | See backlog (DEPS-postgres-extras)

## Sources Index

[S1] `docs/operations/validation.md` (lines 1-47): docs verification gates; DB readiness verification; CLI health/status checks.

[S2] `docs/ARCHITECTURE.md` (lines 1-120): canonical architecture pointer; “Real LLM + Postgres (production)” env; CLI command list includes `resume`; `run` options include `--auto-resume`.

[S3] `README.md` (lines 1-120; 150-210): demo commands; “production use (real LLM)” block; test invocation; checkpoint stack pins.

[S4] `docs/decisions/adr-0009-workflow-recovery-strategy.md` (lines 1-91; 93-120; 205-232): skip not implemented in v1; “none of these exist in v1”; v1 constraints table.

[S5] `src/integration_coworker/persistence/checkpoints.py` (lines 1-200): `_serialize_state` truncation; `save_checkpoint`/`load_checkpoint`; Postgres+SQLite implementations; `get_completed_nodes`.

[S6] `src/integration_coworker/cli.py` (lines 320-430): `--auto-resume` / `--resume` flags; resume logic loads checkpoints and continues graph via `run_from_node`.

[S7] `src/integration_coworker/api/recovery.py` (lines 1-190): `resume_run`; `skip_failing_step` with dependency cascade.

[S8] `src/integration_coworker/llm/sanitizer.py` (lines 1-140): suspicious pattern detection + sanitization policy for tasks/specs.

[S9] `src/integration_coworker/llm/safety.py` (lines 1-80): `SAFETY_PREAMBLE` and `harden_system_prompt()`.

[S10] `archive/docs/design/design-doc.md` (lines 20-85): v1 success criteria (baseline patterns incl. logging+redaction), operational targets (<5 min), and “single python function + CLI entrypoint”.

[S11] `src/integration_coworker/api/entrypoint.py` (lines 1-90): `design_and_generate_integration()` public API entrypoint and returned `IntegrationResult` fields.

[S12] `docs/development/PROD_BUG_LOG.md` (lines 40-200): BUG-0002 pgvector test fix verification; BUG-0003 credential fragments + fail-fast gap.
