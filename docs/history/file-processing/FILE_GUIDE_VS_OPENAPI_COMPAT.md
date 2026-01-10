# File Guides vs OpenAPI Compatibility

## Non-interference (what’s shared vs isolated)
- Shared entrypoint only: `detect_and_parse_spec` routes all specs; API specs go to `api_specs`, file specs to `parsed_specs` (`src/integration_coworker/graph/nodes/detect_and_parse_spec.py` lines 780-900).
- API path: `parsed_spec.source_type == API` flows into API list and legacy OpenAPI handling (same lines 842-878); downstream processing is in API builder (not touched by file flow).
- File path: `source_type == FILE` appended to `state.parsed_specs`; later consumed solely by `build_silver_file_model` (`detect_and_parse_spec.py` lines 842-878; `build_silver_file_model.py` lines 40-145).
- Persistence is separated: API persistence elsewhere; file specs/fields persisted via `_persist_file_specs` (`build_silver_file_model.py` lines 148-258). KG for files is optional/Postgres-only (`build_silver_file_model.py` lines 295-388).

## Collision scenarios & mitigations
- **API PDF mistaken for file guide**: Router priority favors OpenAPISource (90) over PDFGuide (40) (`sources/__init__.py` lines 209-239), and PDFGuide hard-rejects non-PDF signatures/extensions (`pdf_guide.py` lines 85-91).
- **CSV vs Fixed-width**: FixedWidthSource rejects `.csv/.tsv` extensions (`fixed_width.py` lines 68-71); CSVSource positively detects delimiters (`csv_source.py` lines 34-64).
- **Excel vs CSV**: Excel registered below CSV and rejects non-Excel extensions (order in `sources/__init__.py` lines 223-228 plus fixed-width rejects textual delimited patterns).
- **Scanned/no-text PDFs**: PDFGuide returns 0 when no `%PDF` signature or extraction fails (`pdf_guide.py` lines 85-122), causing router to skip it.
- **Tie scores**: Router sorts by score then priority (`sources/__init__.py` lines 94-165), so OpenAPI wins ties.

## Acceptance checklist for new sources
- Set conservative `detect` with explicit hard rejects for API-like inputs (pattern: FixedWidth early rejects `.csv/.tsv/.json/.xml/.yaml/.yml` at `fixed_width.py` lines 68-71).
- Keep priority below OpenAPISource unless intentionally overriding (`sources/__init__.py` lines 209-239).
- Populate `ParsedSpec.source_type` correctly and ensure `is_valid()` gate; builder requires dict `data` with `file_spec`/`fields` (`build_silver_file_model.py` lines 88-115).
- Emit warnings for low confidence; set `errors` when deps missing (CSV/FW/PDF guide ImportError branches at `csv_source.py` lines 79-88; `fixed_width.py` lines 125-138; `pdf_guide.py` lines 151-160).
- Add regression tests for: routing precedence, hard rejects, and confidence thresholds (see existing patterns in `tests/test_fixed_width_adversarial.py`, `tests/test_file_integration_postgres.py`).

## How to validate (do not run)
- **OpenAPI detection (SQLite-safe)**: `USE_SQLITE=true pytest -q tests/production_bug_hunt.py::test_spec_type_detection` (test exists; `tests/production_bug_hunt.py` lines 116-170).
- **Routing produces FILE ParsedSpec (SQLite-safe)**: `USE_SQLITE=true pytest -q tests/test_fixed_width_adversarial.py::TestIntegration::test_detect_and_route_integration` (`tests/test_fixed_width_adversarial.py` lines 597-616).
- **Confidence gate contract tests (SQLite-safe)**: `USE_SQLITE=true pytest -q tests/test_fixed_width_adversarial.py::TestConfidenceGatesContract` (class exists; `tests/test_fixed_width_adversarial.py` lines 623-629).
- **Postgres-only file persistence (requires Postgres)**: `pytest -m postgres -q tests/test_file_integration_postgres.py::TestPostgresSchemaInit::test_schema_created` (`tests/test_file_integration_postgres.py` lines 1-9, 22-49).

## External references
- Typer callback semantics (callback + context): https://typer.tiangolo.com/tutorial/options/callback-and-context/
- Typer callback override (subcommands): https://typer.tiangolo.com/tutorial/subcommands/callback-override/
- pytest changelog (deprecations incl. returning non-None from tests): https://docs.pytest.org/en/stable/changelog.html
- pgvector: https://github.com/pgvector/pgvector
- pgvector docs (IVFFlat + probes/lists concepts): https://www.crunchydata.com/blog/optimize-performance-with-pgvector-indexing
