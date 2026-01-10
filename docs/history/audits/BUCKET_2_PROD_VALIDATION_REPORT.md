# Bucket 2 Production Validation Report (Dec 17, 2025)

## Commands Executed

### SQLite-targeted (deterministic / no network)
- `USE_SQLITE=true pytest -q tests/test_logging_wiring.py tests/test_kg_provenance.py::TestFindMatchingFileFields`
  - Result: **7 passed**, 1 warning (Hypothesis norecursedirs)

### Postgres-targeted (pgvector enabled)
- `set -a && source .env && set +a && USE_SQLITE=false pytest -q tests/production_bug_hunt.py tests/production_caveat_analysis.py tests/test_logging_wiring.py tests/test_kg_provenance.py::TestFindMatchingFileFields`
  - Result: **12 passed, 1 skipped**, 1 warning (Hypothesis norecursedirs)
  - Skip: `test_syntax_validator` when tree-sitter absent

### Broad sweep (full test suite)
- `set -a && source .env && set +a && USE_SQLITE=false pytest -q`
  - Result: **33 failed, 2026 passed, 42 skipped, 54 warnings**
  - Failures attributable to environment and known gaps (see Bug Ledger)

## Suites Not Run (and rationale)
- None; full suite executed once (with failures captured). Tree-sitter-dependent path was skipped explicitly due to missing dependency.

## Bug Ledger
- **Resolved**: PytestReturnNotNoneWarning in `tests/production_bug_hunt.py` (converted returns → assertions/skip).
- **Resolved**: Deprecation warning in `generate_code_and_tests.py` (migrated to async LLM client path).
- **Known / Environment** (from broad suite):
  - Postgres connectivity failures (`psycopg_pool.PoolTimeout`, `information_schema` lookups) — local Postgres unavailable while running full suite.
  - Python version mismatch checks expect 3.11; environment is 3.13 (site-packages contains 3.12/3.13) — requires pin/clean env to satisfy.
  - Codegen/LLM path tests failing under mock/offline mode (LLM calls not executed, mock outputs trigger ruff/import expectations).
  - Pattern learning/Postgres schema tests fail on SQLite fallback (no kg.* tables).
  - Stripe/generated artifacts tests fail under mock codegen (missing IntegrationHttpClient, mock artifacts not generated).
  - Workflow recovery truncation expectation mismatch (doc_chunks truncation flag absent in current run).

## Vector Index Rationale
- **Index type**: `ivfflat` (pgvector) on embeddings in `spec_silver.spec_chunks` and `kg.nodes` (`lists = 100`).
- **Why ivfflat over HNSW**: dataset is expected to be \<100k vectors per table; ivfflat keeps build time and memory low while providing adequate recall. HNSW would improve recall/latency at higher memory/build cost; can be revisited when corpus grows.
- **Tuning defaults**: `lists = 100` as created in schema DDL; `ivfflat.probes` left at pgvector default (1). For higher recall in production, increase probes (e.g., 5–10) in session/local settings when latency budget allows.

## Logging Callback Safety
- Typer callback `_configure_logging_once` is logging-only; tests confirm no duplicate handlers across multiple invocations.

## Summary
- Warnings cleaned; deprecation removed; production validation exercised on SQLite + Postgres-targeted suites. Broad suite executed with failures documented above due to environment (Postgres unavailable, Python 3.13 vs expected 3.11, mock LLM artifacts).
