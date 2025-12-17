# Maintenance + Historical Notes

> **Status**: Active (Contributor-facing)
>
> **Last Updated**: December 2025
>
> This page is intentionally short. It exists to keep the published docs surface small while preserving stable landing pages (via redirects) for older one-off audits and plans.

---

## What belongs here

- Short, **actionable maintenance notes** that remain true over time.
- Pointers to the canonical references where the actual system is described.
- A lightweight index of **historical docs** that have been archived out of `docs/`.

If you’re looking for the current architecture description, start here:

- [Architecture Reference](architecture.md)

---

## Architecture docs: keeping truth in one place

The canonical published architecture overview is:

- `development/architecture.md`

Historical audits and rewrite plans were used to improve accuracy, but they are not maintained references.

### P0 architecture gaps (from audits)

The most important “drift fixes” to keep in mind when editing/reading architecture docs:

- **Workflow node count**: the graph registers **21 nodes** (the “22 nodes” phrasing came from counting files, including backups).
- **Conditional routing**: there are **two** conditionals (repo routing and post-validation error routing).
- **Checkpointing**: there is a **dual-layer** mechanism (LangGraph native + application-level checkpoints) used for resume.
- **Parallel workflow**: a parallel graph exists behind `PARALLEL_WORKFLOW=true`.

These points are documented in more detail in `development/architecture.md`.

---

## Deprecations + tech debt

- Canonical long-lived register: [Technical debt register](../decisions/TECHNICAL_DEBT_REGISTER.md)

When a deprecation cleanup or large refactor is completed, capture only the **durable outcome** here (what changed + what contributors should stop doing) rather than keeping full audit logs in the published nav.

---

## Production realism / spec sweeps

Occasional production evaluation sweeps are useful, but they tend to go stale quickly.

If you want to keep a published trace of prior sweeps, prefer:

- a short “what we tested + what we learned + what still fails” summary here, and
- archive the full run logs / tables.

---

## Archived historical docs (redirect targets)

The following pages were previously published under *Development* nav, but have since been archived to keep the published docs minimal.

They should redirect to this page or to `development/architecture.md`.

- Architecture audits (P0/P1/P2)
- Architecture audit question set
- Architecture rewrite plan
- Deprecation cleanup audit
- Production spec sweep

