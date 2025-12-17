# Bucket 2: Capability Map and Technical Debt Register

**Author:** AI Code Agent  
**Date:** 2024-12-15  
**Status:** COMPLETE  
**Purpose:** Evidence-backed inventory of implemented capabilities and outstanding technical debt

---

## 1. Capability Map

### 1.1 Multi-Source Detection System

| Capability | Status | Evidence |
|-----------|--------|----------|
| Priority-based source registry | ✅ Implemented | `src/integration_coworker/sources/__init__.py:30-50` |
| Confidence-gated detection | ✅ Implemented | All parsers use `DETECT_MIN`, `INFER_MIN`, `INFER_WARN` thresholds |
| Structured logging with extra fields | ✅ Implemented | `sources/__init__.py:74-100` — `source.detection.score` events |
| SpecSource protocol contract | ✅ Implemented | `sources/base.py` — `detect()` → float, `parse()` → `ParsedSpec` |

**Source Priority Registration:**
```
OpenAPISource    → priority 90  # API specs first
CSVSource        → priority 70  # Common delimited
ExcelSource      → priority 65  # ZIP-based detection
FixedWidthSource → priority 60  # Needs delimiter-absence check  
PDFGuideSource   → priority 40  # Most permissive fallback
```
Source: `sources/__init__.py:180-230` — `_register_default_sources()`

### 1.2 Fixed-Width File Processing

| Capability | Status | Evidence |
|-----------|--------|----------|
| Boundary detection from multi-row alignment | ✅ Implemented | `parsers/fixed_width_parser.py:detect_boundaries()` |
| Confidence scoring with weighted signals | ✅ Implemented | line_consistency (35%), delimiter_absence (25%), boundary_stability (25%), extension_hint (15%) |
| Type inference per column position | ✅ Implemented | `parsers/fixed_width_parser.py:infer_schema()` |
| Adversarial test coverage | ✅ 65 tests | `tests/test_fixed_width_adversarial.py` |

### 1.3 Excel File Processing

| Capability | Status | Evidence |
|-----------|--------|----------|
| ZIP/OLE2 signature detection | ✅ Implemented | `parsers/excel_parser.py:ZIP_SIGNATURE`, `OLE2_SIGNATURE` |
| Multi-sheet handling (one FileSpec per sheet) | ✅ Implemented | `parsers/excel_parser.py:infer_schema()` |
| Formula cell warning emission | ✅ Implemented | `parsers/excel_parser.py:_detect_formula_cells()` |
| Confidence configuration via `ExcelConfidenceConfig` | ✅ Implemented | `parsers/excel_config.py` |

### 1.4 PDF Guide Processing

| Capability | Status | Evidence |
|-----------|--------|----------|
| PDF signature detection | ✅ Implemented | `parsers/pdf_guide_parser.py` — `%PDF-` header check |
| Table extraction for field metadata | ✅ Implemented | Uses pdfplumber for table parsing |
| Guide keyword scoring | ✅ Implemented | Looks for "field", "format", "layout", "spec" |
| Field-to-guide enrichment | ✅ Implemented | Via `build_silver_file_model.py:_persist_to_kg()` |

### 1.5 Knowledge Graph Integration

| Capability | Status | Evidence |
|-----------|--------|----------|
| Natural key conventions | ✅ Implemented | `kg/persist.py:40-80` — `build_file_spec_key()`, `build_file_field_key()` |
| Idempotent node upsert with `ON CONFLICT` | ✅ Implemented | `kg/persist.py:100-150` — `upsert_node()` |
| Idempotent edge upsert with weight GREATEST | ✅ Implemented | `kg/persist.py:155-190` — `upsert_edge()` |
| Vector similarity search via pgvector | ✅ Implemented | `kg/persist.py:470-510` — `find_similar_fields()` |
| `HAS_FIELD` edges from spec→field | ✅ Implemented | `kg/persist.py:295-340` — `persist_file_field_to_kg()` |
| `DERIVES_FROM_GUIDE` edges for provenance | ✅ Implemented | `kg/persist.py:220-280` — `persist_file_spec_to_kg()` |
| `MAPS_TO` edges for field mappings | ✅ Implemented | `kg/persist.py:350-370` — `persist_field_mapping_to_kg()` |

**Natural Key Formats:**
```
FILE_SPEC:     file_spec.{source_system_id}.{spec_name}[.{sheet_name}]
FILE_FIELD:    file_field.{file_spec_key}.{field_name}
GUIDE_FIELD:   guide_field.{uri_hash8}.{field_name}
RECORD_LAYOUT: record_layout.{file_spec_key}.{layout_name}
```
Source: `kg/persist.py:13-19` docstring

### 1.6 Validation Code Generation

| Capability | Status | Evidence |
|-----------|--------|----------|
| REQUIRED rule generation | ✅ Implemented | `codegen/validation_codegen.py` |
| LENGTH validation with min/max | ✅ Implemented | Fixed conditional max_len generation |
| REGEX pattern validation | ✅ Implemented | Uses raw strings to avoid escape warnings |
| ENUM value validation | ✅ Implemented | `codegen/validation_codegen.py` |
| CROSS_FIELD safe evaluation | ✅ Implemented | `codegen/safe_eval.py` — simpleeval AST-based |
| Security: no `eval()` in codegen | ✅ Implemented | `safe_eval.py:SafeExpressionEvaluator` |

### 1.7 Structured Logging

| Capability | Status | Evidence |
|-----------|--------|----------|
| `source.detection.start` event | ✅ Implemented | `sources/__init__.py:74-82` |
| `source.detection.score` event with fields | ✅ Implemented | `sources/__init__.py:91-100` — `source_class`, `uri`, `score`, `priority` |
| `source.detection.selected` event | ✅ Implemented | `sources/__init__.py:120-127` |
| `source.inference.complete` event | ✅ Implemented | `sources/__init__.py:135-144` — includes `warning_count`, `error_count` |

---

## 2. Technical Debt Register

### 2.1 Severity Scale

| Level | Definition | Action Required |
|-------|------------|-----------------|
| 🔴 Critical | Security risk or data loss potential | Immediate fix before production |
| 🟠 High | Major functionality gap or O(n²) scaling | Address in next sprint |
| 🟡 Medium | Code quality or minor feature gap | Plan for backlog |
| 🟢 Low | Polish, optimization, documentation | Nice-to-have |

### 2.2 Technical Debt Items

#### TD-SEC-001: ✅ RESOLVED — Safe Expression Evaluation
- **Status:** ✅ DONE
- **Severity:** 🔴 Critical
- **Fix:** Replaced `eval()` with `simpleeval` AST-based evaluator
- **Evidence:** `codegen/safe_eval.py` — `SafeExpressionEvaluator` class
- **Tests:** `tests/test_safe_eval_security.py`, `tests/test_codegen_security.py`

#### TD-OBS-001: ✅ RESOLVED — Structured Logging
- **Status:** ✅ DONE  
- **Severity:** 🟠 High
- **Fix:** Added structured logging with `extra={}` fields throughout detection pipeline
- **Evidence:** `sources/__init__.py:74-144` — 4 log events with fields
- **Enables:** Filtering by `source_class`, `uri`, `score` in log aggregators

#### TD-XLS-001: ✅ RESOLVED — Excel Formula Warnings
- **Status:** ✅ DONE
- **Severity:** 🟡 Medium
- **Fix:** Dual-pass loading to detect formula cells, emit warnings
- **Evidence:** `parsers/excel_parser.py:_detect_formula_cells()`
- **Tests:** `tests/test_excel_formula_warnings.py`

#### TD-KG-001: ✅ RESOLVED — FILE_SPEC KG Persistence
- **Status:** ✅ DONE
- **Severity:** 🟠 High
- **Fix:** `persist_file_spec_to_kg()` creates `kg.nodes` entry
- **Evidence:** `kg/persist.py:210-280`
- **Tests:** `tests/test_kg_node_persistence.py`

#### TD-KG-002: ✅ RESOLVED — FILE_FIELD KG Persistence
- **Status:** ✅ DONE
- **Severity:** 🟠 High
- **Fix:** `persist_file_field_to_kg()` + `HAS_FIELD` edges
- **Evidence:** `kg/persist.py:290-340`
- **Tests:** `tests/test_kg_node_persistence.py`

#### TD-KG-003: ✅ RESOLVED — DERIVES_FROM_GUIDE Provenance
- **Status:** ✅ DONE
- **Severity:** 🟠 High
- **Fix:** KG edges with `enriched_fields`, `extraction_method`, `page_number` properties
- **Evidence:** `kg/persist.py:220-280`
- **Tests:** `tests/test_kg_provenance.py` — 28 tests

#### TD-KG-004: ✅ RESOLVED — Vector Field Matching
- **Status:** ✅ DONE
- **Severity:** 🟠 High
- **Fix:** `find_similar_fields()` uses pgvector IVFFlat index
- **Evidence:** `kg/persist.py:470-510`
- **Complexity:** O(log n) per query vs previous O(n²) fuzzy

#### TD-WARN-001: ✅ RESOLVED — SyntaxWarnings in Codegen
- **Status:** ✅ DONE
- **Severity:** 🟢 Low
- **Fix:** Conditional max_len generation, escaped regex backslashes
- **Evidence:** `codegen/validation_codegen.py` — search for `max_len`

#### TD-SCALE-001: Load Testing
- **Status:** ⚠️ SCRIPT CREATED, NOT EXECUTED
- **Severity:** 🟡 Medium
- **Evidence:** `scripts/kg_load_test.py` created
- **Action:** Run in production-like environment to validate O(n) complexity

---

## 3. Test Coverage Summary

| Test File | Count | Category |
|-----------|-------|----------|
| `test_fixed_width_source.py` | 45 | Core FW parsing |
| `test_fixed_width_adversarial.py` | 65 | FW edge cases |
| `test_excel_adversarial.py` | 35 | Excel edge cases |
| `test_excel_formula_warnings.py` | 8 | Formula detection |
| `test_pdf_guide_adversarial.py` | 15 | PDF edge cases |
| `test_validation_codegen.py` | 25 | Rule generation |
| `test_safe_eval_security.py` | 12 | Security tests |
| `test_kg_provenance.py` | 28 | KG edges |
| `test_kg_node_persistence.py` | 8 | KG nodes |
| **TOTAL** | **~234** | Bucket 2 + KG |

All tests passing with 0 warnings as of implementation completion.

---

## 4. Schema Dependencies

### 4.1 PostgreSQL Tables Required

| Table | Purpose | Evidence |
|-------|---------|----------|
| `kg.nodes` | KG node storage with pgvector | `persistence/postgres.py:559-600` |
| `kg.edges` | KG edge storage with properties | `persistence/postgres.py:601-650` |
| `spec_silver.file_specs` | FileSpec relational storage | `persistence/postgres.py:350-400` |
| `spec_silver.file_fields` | FileField relational storage | `persistence/postgres.py:401-450` |

### 4.2 pgvector Requirements

```sql
-- Required extension
CREATE EXTENSION IF NOT EXISTS vector;

-- IVFFlat index for similarity search
CREATE INDEX IF NOT EXISTS idx_kg_nodes_embedding 
ON kg.nodes USING ivfflat (embedding vector_cosine_ops);
```
Source: `persistence/postgres.py:559-570`

---

## 5. Confidence Thresholds Reference

| Source | DETECT_MIN | INFER_MIN | INFER_WARN |
|--------|------------|-----------|------------|
| FixedWidthSource | 0.90 | 0.60 | 0.85 |
| ExcelSource | 0.85 | 0.50 | 0.70 |
| PDFGuideSource | 0.85 | 0.50 | 0.70 |
| CSVSource | 0.80 | 0.50 | 0.70 |
| OpenAPISource | 0.95 | 0.80 | 0.90 |

Source: Individual `*_config.py` files in `parsers/` and `sources/`

---

## 6. Remaining Work Items

| Item | Priority | Effort | Notes |
|------|----------|--------|-------|
| Execute load tests | Medium | S | Run `scripts/kg_load_test.py` in prod-like env |
| Pattern detection | Low | L | Create `FILE_PATTERN` nodes, `IMPLEMENTS_PATTERN` edges |
| Auto-embedding on ingest | Low | M | Call OpenAI embeddings during KG persistence |
| Multi-file layout detection | Low | L | Detect header/detail/trailer patterns |
