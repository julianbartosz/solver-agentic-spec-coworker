# Production Readiness — Current State (evidence-backed)

> **Date**: 2025-12-16
>
> This doc is the *current-state* snapshot for production readiness. It’s intentionally evidence-heavy.
> For gaps and proposed changes, see `docs/development/PROD_READINESS_GAPS_AND_OPTIONS.md`.
>
> **Legend**
> - **Wired**: Implemented and exercised in validations in this repo.
> - **Scaffolding**: Code exists but isn’t yet validated end-to-end, lacks enforcement, or has known correctness/safety gaps.
> - **Missing**: Not implemented.

## Executive snapshot

- **Docs gates**: Wired (Validation A) — strict MkDocs + docs-focused pytest pass.
- **Postgres+pgvector retrieval**: Wired (Validation B) — pgvector search tests pass after scoring fix.
- **Real-LLM safety**: Scaffolding with a **blocker** — logs currently emit credential fragments on 401 and workflows can proceed with fallback skeleton outputs (BUG-0003, BUG-0005).
- **Repo detection + layout inference**: Wired for heuristic defaults (Validation D) — produces deterministic `RepoProfile`, but ergonomics can be confusing in ad-hoc scripts (BUG-0004).
- **End-to-end (dry-run)**: Wired (Validation E) — returns a concrete `RepoChangeSet` and does not modify the repo in dry-run; still impacted by the Real-LLM safety blocker.

## Capability matrix

| Capability | Status | Evidence |
|---|---:|---|
| Strict docs build (MkDocs) | Wired | `tests/test_mkdocs.py` passing (Validation A). |
| Docs audit generator | Wired | `scripts/docs_audit.py` generates `docs/development/docs_audit_deliverable.md` (from prior phases). |
| Public API entrypoint exists (`design_and_generate_integration`) | Wired | `src/integration_coworker/api/entrypoint.py` defines and returns `IntegrationResult`. |
| Options normalization and safety against unknown option keys | Wired | `design_and_generate_integration()` filters dict options to known dataclass fields (`IntegrationOptions.__dataclass_fields__`). |
| Postgres backend selection (pgvector path) | Wired | `src/integration_coworker/retrieval/semantic_search.py` chooses pgvector path when `db.get_engine_type() == "postgres"`. |
| KG template hybrid scoring (graph + semantic weights) | Wired | Fixed in `src/integration_coworker/retrieval/semantic_search.py` per BUG-0002; verified by `tests/test_pgvector_search.py`. |
| Repo profile detection (DetectedProfile) | Wired | `src/integration_coworker/repo/detection.py:detect_repo_profile()` returns `DetectedProfile` with `language/confidence/evidence`. Validation D confirms expected fields. |
| Effective repo profile inference (RepoProfile) | Wired | `build_effective_repo_profile()` populates `RepoProfile.*_root` for integrations/tests (Validation D). |
| End-to-end dry-run produces repo edit plan (RepoChangeSet) | Wired | Validation E returned `IntegrationResult.repo_changes` with 3 planned file paths; repo working tree unchanged in dry-run. |
| Secret-safe logs on provider auth failures | **Scaffolding (Blocker)** | BUG-0003 and BUG-0005: 401 payload messages include key fragments (`sk-…`). |
| Fail-fast semantics in REAL mode for 401/403 | **Scaffolding (Blocker)** | BUG-0003 and BUG-0005: workflows continue and generate fallbacks despite failed credentials. |
| DB connection lifecycle on error paths | Scaffolding | Observed `ConnectionWrapper was garbage collected without being closed` warning during failing runs (BUG-0003/0005). |

## Validation evidence snapshots (what we actually observed)

### Validation D — repo scans (synthetic repos)

Observed outputs (summary only):

- `python-pkg`:
  - `DetectedProfile.language=python`, `confidence=0.6`, `detected_paths` contains `pyproject_toml`.
  - `RepoProfile.name=inferred_python_project`, `integrations_root=src/integrations`, `tests_root=tests/integrations`.
- `monorepo`:
  - `DetectedProfile.language=javascript`, `confidence=0.6`, `detected_paths` contains `package_json`.
  - `RepoProfile.integrations_root=packages/api/integrations` (heuristic found nested service).
- `docs-src-split`:
  - `DetectedProfile.language=python`, `confidence=0.3` with low-confidence warning.
  - `RepoProfile.name=generic_python_project`, `profile_source=heuristic_fallback`.

Related issue captured as BUG-0004.

### Validation E — safe end-to-end (dry-run)

Dry-run against `/tmp/icw-e2e-minrepo` returned a `RepoChangeSet` containing 3 planned files:

- `integrations/clients/httpbin.py`
- `integrations/flows/httpbin_add_a_minimal.py`
- `tests/integrations/test_httpbin_add_a_minimal.py`

The git working tree remained clean after the dry-run (no files written).

**However**: the run also re-surfaced the “credential fragment in logs” issue and fallback-on-401 behavior (BUG-0005).

## Bottom line

- The system is **close** to being production-usable for controlled environments.
- The main *production blocker* right now is **log safety + REAL-mode semantics on auth failures**.
- Repo detection and dry-run repo planning are working, but the workflow should fail earlier and more deterministically when critical dependencies (LLM/embeddings) aren’t usable.
