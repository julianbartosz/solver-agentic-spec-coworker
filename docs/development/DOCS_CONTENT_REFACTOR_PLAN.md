# DOCS CONTENT REFACTOR PLAN (v1)

> **Scope**: Docs-only refactor.
>
> Allowed changes: `docs/**`, `mkdocs.yml`, and docs tooling/scripts/tests that enforce docs invariants.
>
> **Hard invariants**:
> - `GETTING_STARTED.md` must remain visible in nav as **Ops Runbook → Getting Started (Ops)**.
> - Never redirect `GETTING_STARTED.md`.
> - `mkdocs build --strict` must have **zero warnings**.
> - Keep inbound links working via `mkdocs-redirects` `redirect_maps` when we merge/delete pages.

This plan is evidence-driven: it is based on the generated inventory in `docs/development/docs_audit_deliverable.md` (regenerate with `python scripts/docs_audit.py`).

---

## Canonical Surface (published pages to keep)

These are the pages we want as the *small, stable, published surface area*. All are currently in MkDocs nav (except where noted) and should remain the primary targets for internal links and inbound redirects.

### Front door

- `index.md`

### Operations (canonical runbooks)

- `operations/index.md`
- `operations/db-postgres.md`
- `operations/deployment.md`
- `operations/validation.md`

### Ops Runbook (top-level invariant)

- `GETTING_STARTED.md` (must stay in nav, must not be redirected)

### Getting Started (end-user onboarding)

- `getting-started/installation.md`
- `getting-started/quickstart.md`
- `getting-started/configuration.md`

### User guide

- `user-guide/cli-reference.md` (generated)
- `user-guide/web-ui.md`
- `user-guide/specs.md`
- `user-guide/workflows.md`

### API reference

- `api-reference/entrypoint.md`
- `api-reference/types.md`
- `api-reference/workflow-state.md`

### Features

- `features/knowledge-graph.md`
- `features/llm-cache.md`
- `features/parallel-execution.md`
- `features/recovery.md`

### Development (keep, but slim and de-duplicate)

Keep only what is needed to:

1) understand architecture at a stable/high level,
2) contribute/tests,
3) maintain docs/tooling.

Canonical targets in this section:

- `development/architecture.md`
- `development/code-tour.md`
- `development/contributing.md`
- `development/testing.md`
- `development/changelog.md`
- `development/dependency_bumps.md`
- `development/docs_tooling.md`
- `development/docs_audit_deliverable.md` (generated; keep published for transparency)

Non-canonical / plan-only (remain **unpublished**):

- `development/DOCS_SLIMDOWN_PLAN.md` (currently nav:no)
- `development/DOCS_CONTENT_REFACTOR_PLAN.md` (this document; we can keep it unpublished or publish later—default: unpublished)

---

## Merge/Delete Candidates (evidence-backed)

This table is the working set for content reduction.

The “nav?” column is based on the latest `docs_audit_deliverable.md` inventory.

> Note: “ARCHIVE” here means **move out of `docs/`** (to `archive/docs/`) so it won’t be published/served. If a file is still under `docs/` today, ARCHIVE is allowed as an action; if it is already outside `docs/`, we don’t use ARCHIVE as an action.

| doc path | nav? | why it’s outdated/duplicative (evidence) | action | redirect needed? | risk notes |
|---|---:|---|---|---|---|
| `docs/GETTING_STARTED.md` | yes | Overlaps with `getting-started/*` (audit overlap section). Contains Docker Compose YAML snippet that may drift vs repo `docker-compose.yml`. | KEEP (Batch B: precision + de-dupe) | no | **High risk**: ops critical + explicit invariant; don’t redirect/remove. |
| `docs/getting-started/installation.md` | yes | Duplicates portions of `GETTING_STARTED.md` (audit overlap). May describe optional extras not meant for ops runbook. | KEEP (tighten scope) | no | Medium risk: referenced by users; keep stable URLs. |
| `docs/getting-started/quickstart.md` | yes | Duplicates demo commands in `GETTING_STARTED.md` headings. | KEEP (link to ops commands where needed) | no | Low-medium. |
| `docs/getting-started/configuration.md` | yes | Duplicates DB setup and LLM config sections from `GETTING_STARTED.md` headings. | KEEP (canonical for env vars) | no | Medium: referenced by other pages. |
| `docs/operations/db-postgres.md` | yes | Currently has “legacy source” notes (previously linked to deleted `db_setup_postgres.md`). Needs to become the canonical Postgres runbook and avoid archive links. | KEEP (Batch A: operations truth) | no | High: ops doc; strict build must remain warning-free. |
| `docs/operations/deployment.md` | yes | Previously had links to archived deployment docs; now needs stronger canonical content (no archive links). | KEEP (Batch A) | no | Medium. |
| `docs/operations/validation.md` | yes | Previously referenced archived validation playbooks; needs canonical checks only. | KEEP (Batch A) | no | Medium. |
| `docs/development/ARCHITECTURE_AUDIT_P0_REST.md` | yes | Large one-off audit Q set (30KB) with nav:yes but not a maintained reference. | MERGE-INTO `development/architecture.md` (summary) then ARCHIVE | yes (`development/ARCHITECTURE_AUDIT_P0_REST.md` → `development/architecture.md`) | **Risk**: currently published; may have inbound links even if audit shows in0. |
| `docs/development/ARCHITECTURE_AUDIT_P1.md` | yes | Same class as above (audit artifact, not stable docs). | MERGE-INTO `development/architecture.md` (summary) then ARCHIVE | yes (`development/ARCHITECTURE_AUDIT_P1.md` → `development/architecture.md`) | Same. |
| `docs/development/ARCHITECTURE_AUDIT_P2.md` | yes | Same class as above. | MERGE-INTO `development/architecture.md` (summary) then ARCHIVE | yes (`development/ARCHITECTURE_AUDIT_P2.md` → `development/architecture.md`) | Same. |
| `docs/development/ARCHITECTURE_AUDIT_QUESTIONS.md` | yes | “Question set” is meta-process; may be useful for internal review, but not for published docs. | MERGE-INTO new `development/maintenance.md` (see Batch D) then ARCHIVE | yes (`development/ARCHITECTURE_AUDIT_QUESTIONS.md` → `development/maintenance.md`) | Medium (published today). |
| `docs/development/ARCHITECTURE_AUDIT_RUNTIME_DATA.md` | yes | Audit notes with runtime/state views; overlap with `development/architecture.md` “Data Flow / State Management”. | MERGE-INTO `development/architecture.md` then ARCHIVE | yes (`development/ARCHITECTURE_AUDIT_RUNTIME_DATA.md` → `development/architecture.md`) | Medium. |
| `docs/development/ARCHITECTURE_REWRITE_PLAN.md` | yes | Plan doc, not reference. | MERGE-INTO `development/maintenance.md` (decision log entry) then ARCHIVE | yes (`development/ARCHITECTURE_REWRITE_PLAN.md` → `development/maintenance.md`) | Medium. |
| `docs/development/ASYNC_MIGRATION_PLAN.md` | yes | Potentially superseded; needs content check vs current implementation. | KEEP or ARCHIVE (decision in Batch D after review) | maybe | Unknown until reviewed; treat as medium risk. |
| `docs/development/DEPRECATION_CLEANUP_AUDIT.md` | yes | One-off audit report; should be archived after extracting actionable maintenance notes. | MERGE-INTO `development/maintenance.md` then ARCHIVE | yes (`development/DEPRECATION_CLEANUP_AUDIT.md` → `development/maintenance.md`) | Medium. |
| `docs/development/PRODUCTION_SPEC_SWEEP.md` | yes | One-off evaluation artifact; likely belongs in archive. | ARCHIVE (or MERGE summary into `operations/validation.md`) | yes (to `operations/validation.md` **or** `development/maintenance.md`) | Medium. |
| `docs/development/docs_audit_deliverable.md` | yes | Generated and useful for invariants. | KEEP | no | Low.
| `docs/development/docs_tooling.md` | yes | Canonical for docs workflows; may absorb other process docs. | KEEP (may expand) | no | Low.
| `docs/development/dependency_bumps.md` | yes | Good, small, actionable. | KEEP | no | Low.
| `docs/gen_cli_reference.py` | no | Tooling source file; not a doc page. | KEEP (tooling) | no | Low; ensure not referenced in nav.
| `docs/user-guide/cli-reference.md` | yes | Generated. | KEEP | no | Low.

### Decisions section note

The inventory excerpt in `docs_audit_deliverable.md` didn’t include `docs/decisions/**` rows, even though `mkdocs.yml` includes Decisions in `nav`. Before Batch C, we will regenerate inventory and **verify** what decision files exist under `docs/decisions/` and which are still accurate.

---

## Doc Hygiene Policy (enforceable)

### Where content belongs

- **Operations (`docs/operations/*`)**: canonical runbooks for operators.
  - Must be command-accurate and minimal.
  - Must not link to `archive/`.
  - Prefer referencing repo scripts by stable paths (e.g. `./scripts/setup_env.sh`).

- **Ops Runbook (`docs/GETTING_STARTED.md`)**: the single ops entrypoint.
  - Must remain in nav as “Ops Runbook”.
  - Should *link out* to canonical Operations pages rather than duplicating them.
  - Must include a “Last Updated” stamp and be reviewed for freshness whenever operational scripts change.

- **Development (`docs/development/*`)**: contributor docs + maintenance playbooks.
  - Keep stable references (architecture, code tour, contributing, testing).
  - Plans/audits are not published docs: summarize into the canonical references and move the originals to `archive/docs/`.

- **Decisions (`docs/decisions/*`)**: ADRs and a small number of current registers.
  - Superseded decisions must be marked with a short deprecation header and pointer to the newer ADR.
  - Prefer “KEEP with superseded header” over deletion unless the content is harmful/confusing.

### One-off reports

- One-off reports (sweeps, audits, assessments, “final demo” docs) must live under `archive/docs/`.
- Published docs may include an “Archived sources” note but **must not include live links** into `archive/`.

### Preventing drift

- Every ops-critical page must have:
  - **Owner**: a team/person/role (or “Repository Maintainers”).
  - **Last Updated** month/year.
  - A short “Review triggers” list (e.g., changing `scripts/setup_env.sh`, `docker-compose.yml`, CLI commands).

- If a doc contains a CLI command, it should be stable and ideally covered by:
  - an existing docs test (`tests/test_mkdocs.py`) OR
  - a docs script reference that is kept in sync.

---

## Execution approach (batches)

We will implement changes in small batches with a commit per batch:

1) **Batch A: Operations truth**
2) **Batch B: GETTING_STARTED precision**
3) **Batch C: Decisions pruning**
4) **Batch D: Development slimming**

After each batch (and stop on first failure):

1. `mkdocs build --strict`
2. `python -m pytest tests/test_mkdocs.py -q`
3. `python scripts/docs_audit.py`

---

## Status / What changed (Dec 2025)

This refactor plan has been **executed through Batch D**.

### Completed batches

- **Batch A (Operations truth):** `docs/operations/*` are now the canonical operator runbooks.
- **Batch B (GETTING_STARTED precision):** `docs/GETTING_STARTED.md` was de-duplicated and kept as the invariant Ops entrypoint.
- **Batch C (Decisions pruning):** `docs/decisions/adr-0001-initial-architecture.md` is marked superseded (referencing newer ADRs).
- **Batch D (Development slimming):** one-off dev audit/plan artifacts were moved out of `docs/` into `archive/docs/development/`, and old URLs are preserved via redirects to canonical dev pages.

### Redirects added / maintained

Redirects are configured in `mkdocs.yml` via `redirect_maps`.

- `ARCHITECTURE.md` → `development/architecture.md`
- Legacy top-level ops pages → canonical ops runbooks:
  - `db_setup_postgres.md` → `operations/db-postgres.md`
  - `PROD_VALIDATION_PLAYBOOK.md` → `operations/validation.md`
  - `PROD_TEST_MATRIX.md` → `operations/validation.md`
  - `AZURE_DEPLOYMENT.md` → `operations/deployment.md`
  - `DEPLOYMENT_PLAN.md` → `operations/deployment.md`
  - `DISTRIBUTION_STRATEGY.md` → `operations/deployment.md`
- Dev slimdown redirects (old published URLs maintained; no redirects into `archive/`):
  - `development/ARCHITECTURE_AUDIT_P0_REST.md` → `development/architecture.md`
  - `development/ARCHITECTURE_AUDIT_P1.md` → `development/architecture.md`
  - `development/ARCHITECTURE_AUDIT_P2.md` → `development/architecture.md`
  - `development/ARCHITECTURE_AUDIT_RUNTIME_DATA.md` → `development/architecture.md`
  - `development/ARCHITECTURE_AUDIT_QUESTIONS.md` → `development/maintenance.md`
  - `development/ARCHITECTURE_REWRITE_PLAN.md` → `development/maintenance.md`
  - `development/DEPRECATION_CLEANUP_AUDIT.md` → `development/maintenance.md`
  - `development/PRODUCTION_SPEC_SWEEP.md` → `development/maintenance.md`
  - `development/ASYNC_MIGRATION_PLAN.md` → `development/maintenance.md`

### Current docs surface size

- Markdown files under `docs/`: **44** (as of 2025-12-16).

### Invariants check

- `GETTING_STARTED.md` remains in nav as **Ops Runbook → Getting Started (Ops)**.
- `GETTING_STARTED.md` is **not redirected**.
