# File Guide / Data Transmission Pipeline

## End-to-end flow (ingest → KG)
- Ingest bytes + URI + optional content-type → routed by `detect_and_route` (`src/integration_coworker/sources/__init__.py` lines 55-165).
- Router scores registered sources, highest score (tie-break by priority) wins (`src/integration_coworker/sources/__init__.py` lines 94-165).
- `detect_and_parse_spec` calls `ensure_sources_registered()` then routes each `SpecDocument`; FILE `ParsedSpec`s accumulate in `state.parsed_specs`, API specs in `api_specs` (`src/integration_coworker/graph/nodes/detect_and_parse_spec.py` lines 780-900).
- `build_silver_file_model` filters `state.parsed_specs` for `SourceType.FILE`, stamps `source_system_id`, and persists file specs/fields; then attempts KG persistence (`src/integration_coworker/graph/nodes/build_silver_file_model.py` lines 40-145, 148-388).
- DB persistence uses Postgres (ON CONFLICT) or SQLite branching (`src/integration_coworker/graph/nodes/build_silver_file_model.py` lines 148-258). KG persistence is Postgres-only and skipped on other engines (`src/integration_coworker/graph/nodes/build_silver_file_model.py` lines 295-325).

## Router decision table (priority & collision avoidance)
| Source | Priority | Positive signals | Hard rejects | Notes |
| --- | --- | --- | --- | --- |
| OpenAPISource | 90 | Registered first | n/a | Highest priority so API wins on any score (`src/integration_coworker/sources/__init__.py` lines 209-239). |
| CSVSource | 70 | `.csv/.tsv`, `text/csv`, delimiter heuristics (`src/integration_coworker/sources/csv_source.py` lines 34-64) | none specific beyond score | Lower priority than OpenAPI. |
| ExcelSource | 65 | Registered after CSV | Rejects non-Excel extensions (see class docstring, registered order `src/integration_coworker/sources/__init__.py` lines 223-228). |
| FixedWidthSource | 60 | Line-length stability, no delimiters; detects via config (`src/integration_coworker/sources/fixed_width.py` lines 46-92) | Rejects `.csv/.tsv/.json/.xml/.yaml/.yml` early (`src/integration_coworker/sources/fixed_width.py` lines 68-71) | Avoids stealing delimited inputs. |
| PDFGuideSource | 40 | `%PDF` magic + guide/table signals (`src/integration_coworker/sources/pdf_guide.py` lines 61-116) | Non-PDF extensions, missing `%PDF` signature (`src/integration_coworker/sources/pdf_guide.py` lines 85-91) | Lowest priority; only used when others score 0. |

## ParsedSpec contract (API vs FILE)
- Defined in `src/integration_coworker/sources/base.py` lines 18-79.
- Fields: `source_type`, `source_uri`, `data`, `metadata`, `errors`, `warnings`, `confidence`, `raw_content`; `is_valid()` requires `data` and no `errors` (`base.py` lines 56-79).
- API `data`: OpenAPI/Swagger dict (per docstring `base.py` lines 40-45).
- FILE `data`: per-source dicts like `{"file_spec": FileSpec, "fields": List[FileField], ...}` (same docstring `base.py` lines 40-45); consumers expect `ParsedSpec.data` to be a dict (`build_silver_file_model.py` lines 88-115).

## Stage details
- **Routing**: Registers sources lazily via `ensure_sources_registered` then scores and selects best (`sources/__init__.py` lines 94-165, 246-252).
- **State fan-out**: FILE specs go to `state.parsed_specs`; API specs to `api_specs` (`detect_and_parse_spec.py` lines 842-878).
- **Silver persistence**: Postgres path uses `INSERT ... ON CONFLICT` for `spec_silver.file_specs` and `spec_silver.file_fields`; SQLite path uses `INSERT OR REPLACE` (`build_silver_file_model.py` lines 165-258).
- **KG persistence**: Postgres-only; creates FILE_SPEC/FILE_FIELD nodes and edges, optionally embeddings (`build_silver_file_model.py` lines 295-388). Skipped if engine != postgres (lines 322-325).

## Optional dependencies & failure behavior
- CSV: Missing `parsers.csv_schema` returns `ParsedSpec.errors` and `data=None` (`csv_source.py` lines 79-88).
- Fixed-width: Missing parser/config returns `ParsedSpec.errors` with no data (`fixed_width.py` lines 125-138).
- PDF guide: Missing parser/config returns `ParsedSpec.errors` (`pdf_guide.py` lines 151-160). Detection also returns 0 if parser missing (`pdf_guide.py` lines 93-122).
- Excel: Similar optional dependency checks (see registration order; detection rejects non-Excel extensions `sources/__init__.py` lines 223-228). 

## Postgres-only notes (pgvector)
- IVFFlat index on `spec_silver.spec_chunks.embedding` with `lists = 100` (`src/integration_coworker/persistence/postgres.py` lines 332-348).
- IVFFlat index on `kg.nodes.embedding` with `lists = 100` (`src/integration_coworker/persistence/postgres.py` lines 687-691).
- Tradeoff (pgvector IVFFlat): `lists` controls inverted lists; `probes` (set at query time) controls how many lists are searched—higher probes = better recall, more latency (per pgvector docs). This repo sets `lists`; probes are query-time tunables.
- SQLite path skips vector indexes entirely (`build_silver_file_model.py` lines 322-325 show KG/pgvector skipped off Postgres).

## Typer callback behavior
- CLI callback `_configure_logging_once` is registered with `@app.callback()` and runs before any command, wiring logging once per invocation (`src/integration_coworker/cli.py` lines 312-327). Typer callbacks run prior to subcommands and can define top-level options (Typer docs: callback executes before commands).

## How to validate (do not run)
- **SQLite-safe**: `USE_SQLITE=true pytest -q tests/test_logging_wiring.py::test_cli_callback_does_not_duplicate_handlers` (test exists; `tests/test_logging_wiring.py` lines 96-119).
- **SQLite-safe**: `USE_SQLITE=true pytest -q tests/test_fixed_width_adversarial.py::TestConfidenceGatesContract::test_detect_and_route_integration` (routes FILE specs through `detect_and_route`; `tests/test_fixed_width_adversarial.py` lines 597-616).
- **SQLite-safe**: `USE_SQLITE=true pytest -q tests/production_bug_hunt.py::test_spec_type_detection` (OpenAPI detection logic in test; `tests/production_bug_hunt.py` lines 116-170).
- **SQLite-safe**: `USE_SQLITE=true pytest -q tests/test_kg_provenance.py::TestFindMatchingFileFields::test_find_matching_file_fields_skips_vector_on_sqlite` (explicit SQLite branch; `tests/test_kg_provenance.py` lines 544-558).
- **Postgres-required**: `pytest -m postgres -q tests/test_file_integration_postgres.py::TestPostgresSchemaInit::test_schema_created` (file is explicitly Postgres + testcontainers; `tests/test_file_integration_postgres.py` lines 1-9, 22-49).

## External references
- Typer callback semantics (callback + context): https://typer.tiangolo.com/tutorial/options/callback-and-context/
- Typer callback override (subcommands): https://typer.tiangolo.com/tutorial/subcommands/callback-override/
- pytest changelog (deprecations incl. returning non-None from tests): https://docs.pytest.org/en/stable/changelog.html
- pgvector: https://github.com/pgvector/pgvector
- pgvector docs (IVFFlat + probes/lists concepts): https://www.crunchydata.com/blog/optimize-performance-with-pgvector-indexing
