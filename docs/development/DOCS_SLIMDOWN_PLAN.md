# DOCS SLIMDOWN PLAN (v1)

This plan is written **before** any destructive docs moves. It is driven by:

- `mkdocs.yml` nav (canonical published surface)
- `docs/development/docs_audit_deliverable.md` (inventory, inbound/outbound, nav presence)

Constraints / invariants:

- `mkdocs build --strict` must remain enabled and pass with **no warnings**.
- Keep `GETTING_STARTED.md` visible in nav as **Ops Runbook → Getting Started (Ops)**.
- Do **not** redirect `GETTING_STARTED.md` (neither source nor destination in `redirect_maps`).
- Docs-only scope: changes limited to `docs/`, `mkdocs.yml`, docs tooling scripts/tests, CI workflow, Makefile, `pyproject.toml`.

---

## 1) Canon Map (final docs taxonomy)

Published docs under `docs/` should be a small, stable, canonical tree:

- `docs/index.md` — front door
- `docs/GETTING_STARTED.md` — **Ops Runbook entrypoint** (kept top-level by requirement)
- `docs/getting-started/**` — installation/quickstart/configuration for devs
- `docs/operations/**` — operational runbooks (DB/deploy/validation)
- `docs/user-guide/**` — user-facing usage (CLI, specs, workflows, web UI)
- `docs/api-reference/**` — mkdocstrings API reference pages
- `docs/features/**` — feature narratives
- `docs/development/**` — contributing, testing, architecture, docs tooling
- `docs/decisions/**` — ADRs / decision log (not currently in nav, but intentionally retained)
- `docs/design/**` — (future) design docs if we decide to publish them; for now, keep `docs/design doc/**` out of scope for publishing

Everything else that is primarily:

- history snapshots
- milestone notes
- implementation plans
- audits / scratch

…should be moved out of `docs/` entirely into `archive/docs/**`.

---

## 2) Redirect policy

We use three mechanisms, in priority order:

1. **Redirect (preferred)** via `mkdocs-redirects` when a page moved/renamed and we want to preserve inbound URLs.
   - Works even if the old markdown file is removed from `docs/`.
2. **Stub page** only when we expect humans to open files in-repo and we want a short “this moved” pointer.
   - Stubs should be tiny and should not duplicate content.
3. **Keep as-is** only when the page is canonical or is actively linked/in-use and not a duplicate.

Special case:

- `GETTING_STARTED.md` must **not** be redirected.

---

## 3) Risky links to double-check after moves

High-inbound or nav-visible pages that must not break:

- `docs/GETTING_STARTED.md` (nav: yes, inbound: 3)
- `docs/development/architecture.md` (nav: yes, inbound: 5)
- `docs/development/code-tour.md` (nav: yes, inbound: 3)
- `docs/user-guide/cli-reference.md` (nav: yes, inbound: 4)
- `docs/getting-started/quickstart.md` (nav: yes, inbound: 3)
- `docs/api-reference/entrypoint.md` (nav: yes, inbound: 3)

Also note the existing redirects that are already relied upon:

- `ARCHITECTURE.md → development/architecture.md`
- `CODE_TOUR.md → development/code-tour.md`
- `db_setup_postgres.md → operations/db-postgres.md`
- `PROD_VALIDATION_PLAYBOOK.md → operations/validation.md`
- `PROD_TEST_MATRIX.md → operations/validation.md`
- `AZURE_DEPLOYMENT.md → operations/deployment.md`
- `DEPLOYMENT_PLAN.md → operations/deployment.md`
- `DISTRIBUTION_STRATEGY.md → operations/deployment.md`

---

## 4) Proposed move / merge / delete table

Legend:

- **move**: `git mv` to new canonical location
- **merge**: content merged into a canonical page; source becomes redirect or stub
- **delete**: remove empty/dead files (prefer redirect/stub when inbound>0)
- **stub**: keep a tiny markdown at old location pointing to the canonical version

> Paths below are repo-relative. Redirect paths refer to **MkDocs doc paths relative to `docs/`**.

| Old Path | New Canonical Path | Action | Redirect Needed? | Notes |
|---|---|---:|---:|---|
| `docs/history/**` | `archive/docs/history/**` | move | no | Completed: moved out of `docs/` entirely. These pages are no longer served by MkDocs. |
| `docs/plans/**` | `archive/docs/plans/**` | move | no | Completed: moved out of `docs/` entirely. These pages are no longer served by MkDocs. |
| `docs/design doc/**` | `archive/docs/design/**` | move | no | Completed: moved out of `docs/` entirely. These pages are no longer served by MkDocs. |
| `docs/ARCHITECTURE.md` | `docs/development/architecture.md` | merge (already canonical elsewhere) | yes (already) | Existing redirect already present. After slimdown, consider deleting `docs/ARCHITECTURE.md` entirely (redirect keeps URL). |
| `docs/architecture_overview.md` | `docs/development/architecture.md` | merge | yes | It overlaps with architecture reference; fold any unique content into canonical dev architecture or delete if redundant. |
| `docs/CODE_TOUR.md` | `docs/development/code-tour.md` | merge (already canonical elsewhere) | yes (already) | Existing redirect already present. Candidate to delete after confirming no unique content required. |
| `docs/db_setup_postgres.md` | `docs/operations/db-postgres.md` | merge (already canonical elsewhere) | yes (already) | Existing redirect already present. Candidate to delete after confirming no unique content required. |
| `docs/AZURE_DEPLOYMENT.md` | `docs/operations/deployment.md` | merge | yes (already) | Existing redirect already present. Candidate to delete. |
| `docs/DEPLOYMENT_PLAN.md` | `docs/operations/deployment.md` | merge | yes (already) | Existing redirect already present. Candidate to delete. |
| `docs/DISTRIBUTION_STRATEGY.md` | `docs/operations/deployment.md` | merge | yes (already) | Existing redirect already present. Candidate to delete. |
| `docs/PROD_VALIDATION_PLAYBOOK.md` | `docs/operations/validation.md` | merge | yes (already) | Existing redirect already present. Candidate to delete. |
| `docs/PROD_TEST_MATRIX.md` | `docs/operations/validation.md` | merge | yes (already) | Existing redirect already present. Candidate to delete. |
| `docs/PROD_TEST_MATRIX.md` | `docs/operations/validation.md` | stub (optional) | no | Only if we want a human-friendly pointer when browsing repo files. |
| `docs/DATA_ETL_DESIGN.md` | — | delete | no | Audit flags it as `deprecated(empty)` (0 bytes). Safe to remove. |
| `docs/MULTI_SPEC_GAP_REPORT.md` | — | delete | no | Audit flags it as `deprecated(empty)` (0 bytes). Safe to remove. |
| `docs/history/audits/AGENTIC_BEHAVIOR_BOUNDS.md` | `archive/docs/history/audits/AGENTIC_BEHAVIOR_BOUNDS.md` | delete | no | 0 bytes placeholder inside history. Remove during move. |
| `docs/history/implementation/V1_GAP_CLOSURE_PLAN.md` | `archive/docs/history/implementation/V1_GAP_CLOSURE_PLAN.md` | delete | no | 0 bytes placeholder inside history. Remove during move. |
| `docs/history/implementation/FUTURE_PLANS.md` | `archive/docs/history/implementation/FUTURE_PLANS.md` | move | yes | inbound=1; provide redirect from `history/implementation/FUTURE_PLANS.md` to archived copy? Prefer instead to de-link from canonical docs and skip redirect; but inbound suggests something still links. We’ll find the linker before deciding. |
| `docs/history/implementation/LLM_CONFIG_GUIDE.md` | `archive/docs/history/implementation/LLM_CONFIG_GUIDE.md` | move | yes | inbound=1; same policy as above: find current inbound sources, then decide redirect vs fix link to canonical equivalent. |
| `docs/history/milestones/DEMO_GUIDE.md` | `archive/docs/history/milestones/DEMO_GUIDE.md` | move | yes | inbound=1; likely linked from history index, but confirm. |
| `docs/history/implementation/V2_IMPLEMENTATION_PLAN.md` | `archive/docs/history/implementation/V2_IMPLEMENTATION_PLAN.md` | move | yes | inbound=2; likely linked from other docs/history. Find inbound sources; decide redirect vs adjust links to new archive location. |
| `docs/history/implementation/V2_CRITICAL_REFLECTION_AUDIT.md` | `archive/docs/history/implementation/V2_CRITICAL_REFLECTION_AUDIT.md` | move | yes | inbound=1; same approach. |

### Notes on redirects to archived content

MkDocs redirects can only point to pages that still exist under `docs/`.

We are using **Strategy A**: archive pages are *not* served by MkDocs.

Implication: we should avoid linking from canonical docs into `archive/docs/**` (and we do **not** add `redirect_maps` entries into archive pages).

---

## 5) Implementation checklist (next PR stage)

1. Create branch `docs/slimdown-v1`.
2. Move `docs/history/**` → `archive/docs/history/**` (and same for `plans/`, `design doc/`).
3. Add redirects for moved/merged canonical pages (non-archive).
4. Remove empty placeholders.
5. Update internal links in the remaining canonical docs (and README if needed).
6. Run `make docs-verify` until green.

---

## Appendix: Derived inputs (source of truth)

- Audit: `docs/development/docs_audit_deliverable.md`
- Nav + redirects: `mkdocs.yml`
