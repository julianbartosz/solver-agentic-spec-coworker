# Bucket 2 Evidence Matrix - File Integration V1

This document provides the verification matrix for Bucket 2 (File Source handlers) of the File Integration V1 implementation.

## Summary

| Source Type | Source Tests | Adversarial Tests | Total |
|-------------|--------------|-------------------|-------|
| FixedWidthSource | 29 | 35 | 64 |
| ExcelSource | - | 24 | 24 |
| PDFGuideSource | - | 35 | 35 |
| Validation Codegen | - | 35 | 35 |
| **Total** | | | **158** |

All tests pass as of 2024-12-15.

---

## 1. FixedWidthSource

### Confidence Thresholds (fixed_width_config.py)

| Threshold | Value | Purpose |
|-----------|-------|---------|
| DETECT_MIN | 0.90 | Minimum confidence to accept as fixed-width |
| INFER_MIN | 0.60 | Minimum confidence for valid ParsedSpec |
| INFER_WARN | 0.85 | Below this, emit warnings but still proceed |
| BOUNDARY_SUPPORT_MIN | 0.80 | Minimum support for a boundary to be kept |
| INFER_NROWS | 100 | Number of rows to analyze for inference |

### Detection Signals

| Signal | Weight | Description |
|--------|--------|-------------|
| line_consistency | 35% | All lines have same length |
| delimiter_absence | 25% | No consistent delimiters found |
| boundary_stability | 25% | Whitespace runs at stable positions |
| extension_hint | 15% | File extension (.fw, .dat, .txt) |

### Hard Reject Conditions

- **Delimiter detected** (comma/tab/pipe at consistent positions) → confidence 0.0
- **Ragged lines** (varying line lengths) → INFER_MIN failure with remediation

### Key Contract Tests (test_fixed_width_adversarial.py)

| Test | What It Proves |
|------|----------------|
| `test_csv_not_detected_as_fixed_width` | CSV content gets 0.0 confidence |
| `test_csv_wins_over_low_confidence_fixed_width` | Router prefers CSV when delimiters found |
| `test_ragged_lines_produce_error` | Ragged files fail with actionable error |
| `test_infer_warn_gate_produces_warnings` | Borderline cases emit warnings |
| `test_detect_min_gate_is_enforced` | Detection gate prevents false positives |
| `test_hard_reject_still_returns_all_signals` | Hard reject includes all signals for debugging |
| `test_contiguous_inference_is_low_confidence` | Contiguous fields get low inference confidence |

### Files

- `src/integration_coworker/sources/fixed_width.py`
- `src/integration_coworker/parsers/fixed_width_parser.py`
- `src/integration_coworker/parsers/fixed_width_config.py`
- `tests/test_fixed_width_source.py` (29 tests)
- `tests/test_fixed_width_adversarial.py` (35 tests)

---

## 2. ExcelSource

### Confidence Thresholds (excel_config.py)

| Threshold | Value | Purpose |
|-----------|-------|---------|
| DETECT_MIN | 0.85 | Minimum confidence to claim content is Excel |
| INFER_MIN | 0.50 | Minimum confidence for schema inference |
| INFER_WARN | 0.70 | Below this, emit warnings |

### Detection Signals

| Signal | Weight | Description |
|--------|--------|-------------|
| zip_signature | 40% | ZIP magic bytes (PK\x03\x04) |
| content_type | 20% | Excel MIME type header |
| extension_hint | 25% | .xlsx or .xls extension |
| workbook_load | 15% | openpyxl can load the workbook |

### Hard Reject Conditions

- **Missing ZIP signature** → confidence capped at 0.3
- **CSV/TSV/JSON/XML extension** → immediate reject
- **Failed workbook load** → confidence 0.0

### Key Contract Tests (test_excel_adversarial.py)

| Test | What It Proves |
|------|----------------|
| `test_csv_renamed_xlsx_rejected` | CSV bytes with .xlsx extension rejected |
| `test_text_content_gets_zero` | Plain text gets 0.0 confidence |
| `test_empty_sheet_returns_warning` | Empty sheets produce actionable warnings |
| `test_multi_sheet_creates_multiple_specs` | Multi-sheet workbooks create one FileSpec per sheet |
| `test_ambiguous_header_produces_warning` | Numeric-heavy first row emits warning |

### Multi-Sheet Behavior

- Each sheet → one FileSpec
- "Summary" sheets with no data are warned, not errored
- Sheet names included in FileSpec.name

### Files

- `src/integration_coworker/sources/excel.py`
- `src/integration_coworker/parsers/excel_parser.py`
- `src/integration_coworker/parsers/excel_config.py`
- `tests/test_excel_adversarial.py` (24 tests)

---

## 3. PDFGuideSource

### Confidence Thresholds (pdf_guide_config.py)

| Threshold | Value | Purpose |
|-----------|-------|---------|
| DETECT_MIN | 0.85 | Minimum confidence to claim content is a parseable PDF guide |
| INFER_MIN | 0.50 | Minimum confidence for field extraction |
| INFER_WARN | 0.70 | Below this, emit warnings |

### Detection Signals

| Signal | Weight | Description |
|--------|--------|-------------|
| pdf_signature | 30% | %PDF magic bytes |
| text_extraction | 25% | Text can be extracted (not scanned) |
| guide_keywords | 20% | Keywords like "field name", "data type", "position" |
| field_table_pattern | 25% | Tabular field definition patterns detected |

### Hard Reject Conditions

- **Missing %PDF signature** → confidence 0.0
- **Scanned PDF** (< 50 chars/page average) → hard reject with remediation
- **Wrong extension** (.xlsx, .csv, .json) → immediate reject

### Key Contract Tests (test_pdf_guide_adversarial.py)

| Test | What It Proves |
|------|----------------|
| `test_non_pdf_bytes_rejected` | Random bytes get 0.0 confidence |
| `test_csv_content_renamed_pdf_rejected` | CSV content with .pdf extension rejected |
| `test_sparse_text_pdf_rejected` | Scanned/image PDFs are hard rejected |
| `test_blank_page_pdf_rejected` | Empty PDFs fail detection |
| `test_extract_fields_from_guide` | Valid guides extract field definitions |
| `test_low_confidence_emits_warning` | Borderline extractions warn |

### Field Extraction Strategies

1. **Table-based**: Pipe-delimited tables, position-based field definitions
2. **Pattern-based**: Type/length patterns like `VARCHAR(50)`, byte ranges
3. **Keyword-based**: Sections labeled "Field Name", "Required fields"

### Files

- `src/integration_coworker/sources/pdf_guide.py`
- `src/integration_coworker/parsers/pdf_guide_parser.py`
- `src/integration_coworker/parsers/pdf_guide_config.py`
- `tests/test_pdf_guide_adversarial.py` (35 tests)
- `tests/fixtures/file_specs/customer_field_guide.pdf`
- `tests/fixtures/file_specs/minimal_no_fields.pdf`
- `tests/fixtures/file_specs/sparse_text_scanned.pdf`
- `tests/fixtures/file_specs/multi_page_guide.pdf`
- `tests/fixtures/file_specs/blank_page.pdf`

---

## 4. Validation Code Generation

### Supported Rule Types

| Rule Type | Runtime | Codegen | Pydantic |
|-----------|---------|---------|----------|
| REQUIRED | ✅ | ✅ | ❌ (built-in) |
| LENGTH | ✅ | ✅ | ❌ |
| RANGE | ✅ | ❌ | ❌ |
| REGEX | ✅ | ✅ | ✅ |
| ENUM | ✅ | ✅ | ✅ |
| FORMAT | ✅ | ❌ | ❌ |
| UNIQUE | ✅ (file-level) | ❌ | ❌ |
| CROSS_FIELD | ✅ | ❌ | ❌ |

### Key Contract Tests (test_validation_codegen.py)

| Test | What It Proves |
|------|----------------|
| `test_valid_record_passes` | Valid records pass all rules |
| `test_missing_required_fails` | Required rule enforced |
| `test_unique_rule_detects_duplicates` | File-level unique validation works |
| `test_generated_validator_works` | Generated Python code actually validates |
| `test_generated_code_is_executable` | Code can be imported dynamically |

### Files

- `src/integration_coworker/codegen/validation_codegen.py` (main implementation)
- `src/integration_coworker/validation/__init__.py` (re-exports for backward compat)
- `tests/test_validation_codegen.py` (35 tests)

---

## 5. Postgres Idempotency

### Test Coverage (test_file_integration_postgres.py)

| Source Type | Test Class | Key Tests |
|-------------|------------|-----------|
| All | TestIdempotency | `test_file_spec_upsert_no_duplicates`, `test_file_field_upsert_no_duplicates`, `test_build_silver_file_model_idempotent` |
| RecordLayout | TestPostgresRecordLayouts | `test_record_layout_upsert_no_duplicates` |
| Excel | TestExcelSourceToPostgres | `test_excel_spec_upsert_no_duplicates` |
| PDF Guide | TestPDFGuideIdempotency | `test_pdf_guide_spec_upsert_no_duplicates`, `test_pdf_guide_field_update_preserves_id` |

### Idempotency Contract

All source types follow the same pattern:
1. First insert → creates new record, returns ID
2. Second insert (same source_system_id + name) → updates existing record, returns same ID
3. Row counts remain stable after re-import
4. Updated fields (description, confidence) are persisted

### Postgres Schema Requirements

```sql
-- file_specs unique constraint
UNIQUE (source_system_id, name)

-- file_fields unique constraint
UNIQUE (file_spec_id, name)
```

---

## 6. Router Integration

### Priority Order

| Priority | Source | Description |
|----------|--------|-------------|
| 90 | OpenAPISource | API specs (highest priority) |
| 70 | CSVSource | Delimited files |
| 65 | ExcelSource | Excel workbooks |
| 60 | FixedWidthSource | Fixed-width files |
| 40 | PDFGuideSource | PDF guides (lowest) |

### Routing Logic

1. All registered sources score the content
2. Highest-scoring source above DETECT_MIN wins
3. If no source meets threshold → ValueError raised
4. If multiple sources tie → higher-priority source wins

### Contract Tests

- `test_csv_wins_over_low_confidence_fixed_width` - CSV beats low-confidence FW
- `test_pdf_detected_before_plain_text` - PDF wins for PDF content
- `test_fixed_width_not_selected_when_confidence_is_zero` - Zero confidence excluded

---

## 7. Why Misrouting Is Prevented

### Fixed-Width vs CSV

| Scenario | Prevention Mechanism |
|----------|---------------------|
| CSV data to FixedWidthSource | Delimiter detection sets confidence to 0.0 |
| Fixed-width data to CSVSource | Consistent line length + no delimiters detected |

### Excel vs Other

| Scenario | Prevention Mechanism |
|----------|---------------------|
| CSV renamed .xlsx | ZIP signature check fails |
| True Excel to CSV | ZIP signature check succeeds, openpyxl loads |

### PDF vs Other

| Scenario | Prevention Mechanism |
|----------|---------------------|
| CSV renamed .pdf | %PDF signature missing |
| Scanned PDF | Text extraction threshold fails |
| Non-guide PDF | Guide keywords + field patterns missing |

---

## 8. Running the Tests

```bash
# All Bucket 2 tests (158 tests)
pytest tests/test_pdf_guide_adversarial.py tests/test_excel_adversarial.py \
       tests/test_fixed_width_source.py tests/test_fixed_width_adversarial.py \
       tests/test_validation_codegen.py -v

# Postgres integration tests (requires testcontainers)
pytest -m postgres tests/test_file_integration_postgres.py -v

# Quick smoke test
pytest tests/test_fixed_width_source.py::TestFixedWidthDetection -v
```

---

## 9. Ship/No-Ship Criteria

### Ship ✅

- [x] All 158 Bucket 2 tests pass
- [x] Detection confidence gates enforced
- [x] Warnings propagated to ParsedSpec
- [x] Postgres idempotency verified
- [x] Router priority order correct
- [x] Hard rejects prevent misrouting

### Known Limitations (Non-Blocking)

- PDF field extraction is heuristic-based (not 100% accurate)
- Cross-field validation uses `eval()` (safe for trusted config only)
- Generated Pydantic code requires Pydantic v2+
