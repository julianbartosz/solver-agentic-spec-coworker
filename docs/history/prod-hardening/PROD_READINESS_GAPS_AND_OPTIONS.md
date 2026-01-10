# Production Readiness — Gaps and Options (evidence-backed)

> **Date**: 2025-12-16
>
> This doc turns validation findings (A–E) into an actionable backlog.
> It’s meant to be used as an engineering execution plan.

## Backlog (single table)

| ID | Gap (what’s missing / unsafe) | Evidence | Risk | Options (2–4) | Proposed choice |
|---:|---|---|---|---|---|
| GAP-01 | **Credential fragments appear in logs on provider 401/403** (even partially masked fragments) | `docs/development/PROD_BUG_LOG.md` BUG-0003, BUG-0005; observed 401 payloads include `sk-…` fragments | **Blocker (secrets safety)** | (1) Global logging redaction filter (scrub token patterns) (2) Centralize provider error rendering (typed errors) (3) Both (filter + typed errors) | (3) Both; filter gives immediate safety, typed errors prevents future regressions |
| GAP-02 | **REAL mode does not fail fast on auth failure**; workflow continues and emits fallback skeleton outputs that look “successful” | BUG-0003, BUG-0005; logs show “Falling back to python skeleton …” after 401 | **Blocker (misleading outputs, silent failure)** | (1) Policy: fail-fast on 401/403 in REAL (2) Add provider preflight step before workflow (3) Introduce explicit `LLMFailurePolicy` with modes | (2)+(1) preflight + fail-fast on auth errors |
| GAP-03 | **DB connection lifecycle warnings on error paths** | BUG-0003/0005 note `ConnectionWrapper was garbage collected without being closed` | High (resource leaks, pool exhaustion) | (1) Audit context managers around db connections (2) Add lint/test that fails on warnings (3) Improve db wrapper to auto-close in finalizer | (1) + (2) — fix root cause, then enforce |
| GAP-04 | **Repo detection ergonomics**: easy to misread fields and mis-call `build_effective_repo_profile()` | BUG-0004; `build_effective_repo_profile(detected, repo_root)` signature is not intuitive | Medium (DX + validation confusion) | (1) Add `get_repo_profile(repo_root, ...)` wrapper (2) Reorder args with backwards-compatible shim (3) Provide example docs/tests | (1) + (3) — smallest change and better examples |
| GAP-05 | **Heuristic detection for monorepos lacks strong TS signal** in sparse repos | Validation D: monorepo detected `language=javascript` at root, though `integrations_root` was inferred under `packages/api/…` | Medium (wrong file extensions, wrong conventions) | (1) Enhance `_detect_language_by_config_files` to treat workspaces as TS if any `packages/**/tsconfig.json` or `.ts` exists (2) Always upgrade to TS if workspaces detected (3) Make repo config required for monorepos | (1) (plus keep config override escape hatch) |
| GAP-06 | **Dry-run still depends on external services** (LLM/embeddings) for full-quality output; ends up falling back | Validation E output shows embedding + LLM errors influence multiple steps | Medium (unreliable in offline/locked-down envs) | (1) Make “offline planning” mode that avoids embedding/LLM calls entirely (2) Cache/warm embeddings and run on cached-only (3) Strict: require LLM in REAL and abort otherwise | (1) — explicit offline mode is easiest to reason about |

## Chosen approach (recommended rollout)

### Phase 1 — Safety patches (ship first)

1) **Global redaction filter**
   - Scope: all logs (workflow + providers + embeddings).
   - Goal: no token-like strings ever reach logs.

2) **Credential preflight + fail-fast policy**
   - If provider auth fails, stop early.
   - Ensure the final `IntegrationResult.errors` clearly indicates failure.

3) **DB lifecycle on error paths**
   - Convert any raw connection usage to context-managed usage.
   - Add a regression test that asserts no “garbage collected without being closed” warning occurs.

### Phase 2 — Detection ergonomics + monorepo improvements

- Add `get_repo_profile()` helper and docs.
- Strengthen TS detection in nested workspaces.

### Phase 3 — Explicit offline mode

- Introduce an option (e.g., `offline=True` or `no_llm=True`) that ensures:
  - no LLM calls
  - no embeddings
  - deterministic fallback behavior

## Acceptance criteria (production bar)

- **No secrets in logs** under any provider failures.
- In REAL mode, **auth failures are terminal** (no partial “success”).
- End-to-end dry-run and apply-run are **deterministic** and **rollback-clean**.
- Repo profile detection is **explainable** (evidence) and **easy to consume**.
