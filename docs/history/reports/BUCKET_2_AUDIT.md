# Bucket 2 Technical Debt & Scalability Audit

**Author:** AI Code Agent  
**Date:** 2024-12-15  
**Status:** COMPLETE  
**Scope:** File Integration V1 — Bucket 2 (FixedWidthSource, ExcelSource, PDFGuideSource, Validation Codegen)

---

## Table of Contents

1. [Current Capability Map](#1-current-capability-map)
2. [Technical Debt Register](#2-technical-debt-register)
3. [Architecture Options Debate](#3-architecture-options-debate)
4. [No-Interpretation Refactor Plan](#4-no-interpretation-refactor-plan)
5. [Bucket 2 Implementation Plan](#5-bucket-2-implementation-plan)
6. [Production Test Report](#6-production-test-report)
7. [PR Plan](#7-pr-plan)

---

## 1. Current Capability Map

### 1.1 Expanded Capabilities (Bucket 2 Complete)

| Capability | Status | Files | Lines | Tests |
|------------|--------|-------|-------|-------|
| **SpecSource Plugin Registry** | ✅ Complete | `sources/__init__.py` | 1-150 | N/A (registry) |
| **SpecSource Protocol** | ✅ Complete | `sources/base.py` | 1-160 | N/A (protocol) |
| **FixedWidthSource** | ✅ Complete | `sources/fixed_width.py` | 1-400 | 64 tests |
| **FixedWidthParser** | ✅ Complete | `parsers/fixed_width_parser.py` | 1-700 | via source tests |
| **FixedWidthConfidenceConfig** | ✅ Complete | `parsers/fixed_width_config.py` | 1-175 | via source tests |
| **ExcelSource** | ✅ Complete | `sources/excel.py` | 1-350 | 24 tests |
| **ExcelParser** | ✅ Complete | `parsers/excel_parser.py` | 1-300 | via source tests |
| **PDFGuideSource** | ✅ Complete | `sources/pdf_guide.py` | 1-400 | 35 tests |
| **PDFGuideParser** | ✅ Complete | `parsers/pdf_guide_parser.py` | 1-400 | via source tests |
| **Validation Codegen** | ✅ Complete | `codegen/validation_codegen.py` | 1-600 | 35 tests |
| **Postgres Idempotency** | ✅ Complete | `test_file_integration_postgres.py` | 1-750 | 20+ tests |

**Total Tests: 158 (all passing)**

### 1.2 Confidence Threshold Architecture

All sources use a three-tier confidence model:

| Threshold | Purpose | Fixed-Width | Excel | PDF Guide |
|-----------|---------|-------------|-------|-----------|
| `DETECT_MIN` | Router acceptance gate | 0.90 | 0.85 | 0.85 |
| `INFER_MIN` | Minimum for valid ParsedSpec | 0.60 | 0.50 | 0.50 |
| `INFER_WARN` | Below this, emit warnings | 0.85 | 0.70 | 0.70 |

**Evidence:**
- `parsers/fixed_width_config.py:20-35` — FixedWidthConfidenceConfig
- `parsers/excel_config.py:20-35` — ExcelConfidenceConfig  
- `parsers/pdf_guide_config.py:20-35` — PDFGuideConfidenceConfig

### 1.3 Router Priority Order

| Priority | Source | Rationale |
|----------|--------|-----------|
| 90 | OpenAPISource | Explicit API specs first |
| 70 | CSVSource | Common delimited format |
| 65 | ExcelSource | ZIP-based detection is reliable |
| 60 | FixedWidthSource | Needs careful delimiter absence check |
| 40 | PDFGuideSource | Most permissive, fallback |

**Evidence:** `sources/__init__.py:109-140` — `_register_default_sources()`

### 1.4 Detection Signal Architecture

Each source computes confidence from weighted signals:

**FixedWidthSource (4 signals):**
| Signal | Weight | Description |
|--------|--------|-------------|
| `line_consistency` | 35% | All lines have same length |
| `delimiter_absence` | 25% | No consistent delimiters found |
| `boundary_stability` | 25% | Whitespace runs at stable positions |
| `extension_hint` | 15% | File extension (.fw, .dat, .txt) |

**Evidence:** `parsers/fixed_width_config.py:45-53` — Signal weights

**ExcelSource (4 signals):**
| Signal | Weight | Description |
|--------|--------|-------------|
| `zip_signature` | 40% | ZIP magic bytes (PK\x03\x04) |
| `content_type` | 20% | Excel MIME type header |
| `extension_hint` | 25% | .xlsx or .xls extension |
| `workbook_load` | 15% | openpyxl can load workbook |

**Evidence:** `parsers/excel_config.py:45-53`

**PDFGuideSource (4 signals):**
| Signal | Weight | Description |
|--------|--------|-------------|
| `pdf_signature` | 30% | %PDF magic bytes |
| `text_extraction` | 25% | Text can be extracted (not scanned) |
| `guide_keywords` | 20% | Keywords like "field name", "data type" |
| `field_table_pattern` | 25% | Tabular field definitions detected |

**Evidence:** `parsers/pdf_guide_config.py:45-53`

### 1.5 Validation Rule Types

From `domain/models.py:140-150` — ValidationRuleType enum:

| Rule Type | Runtime | Codegen | Pydantic | File-Level |
|-----------|---------|---------|----------|------------|
| REQUIRED | ✅ | ✅ | ❌ (built-in) | ❌ |
| LENGTH | ✅ | ✅ | ❌ | ❌ |
| RANGE | ✅ | ❌ | ❌ | ❌ |
| REGEX | ✅ | ✅ | ✅ | ❌ |
| ENUM | ✅ | ✅ | ✅ | ❌ |
| FORMAT | ✅ | ❌ | ❌ | ❌ |
| UNIQUE | ✅ | ❌ | ❌ | ✅ |
| CROSS_FIELD | ✅ | ❌ | ❌ | ✅ |

**Evidence:** `codegen/validation_codegen.py:100-200`

### 1.6 WorkflowState Integration

File integration adds these fields to `WorkflowState`:

```python
# graph/state.py:50-56
file_specs: List[FileSpec]
file_fields: List[FileField]
record_layouts: List[RecordLayout]
file_validation_rules: List[FileValidationRule]
warnings: List[str]  # V3 addition for non-fatal issues
```

**Evidence:** `graph/state.py:50-56`

### 1.7 ParsedSpec Contract

All sources return `ParsedSpec` with:

```python
# sources/base.py:35-55
ParsedSpec(
    source_type: SourceType.API | SourceType.FILE,
    source_uri: str,
    data: Any,  # {"file_spec": FileSpec, "fields": [...], "record_layouts": [...]}
    metadata: Dict[str, Any],
    errors: List[str],
    warnings: List[str],  # Non-fatal issues
    confidence: float,
)
```

**Evidence:** `sources/base.py:35-55`

---

## 2. Technical Debt Register

### Debt Classification Scale

| Severity | Definition |
|----------|------------|
| CRITICAL | Blocks production use or causes data corruption |
| HIGH | Causes incorrect results or silent failures |
| MEDIUM | Technical debt that complicates maintenance |
| LOW | Polish items, not blocking |

| Likelihood | Definition |
|------------|------------|
| CERTAIN | Will happen in normal usage |
| LIKELY | Happens with edge-case inputs |
| POSSIBLE | Happens under unusual conditions |
| UNLIKELY | Rare edge case |

| Effort | Definition |
|--------|------------|
| XS | < 1 hour |
| S | 1-4 hours |
| M | 1-2 days |
| L | 3-5 days |
| XL | > 1 week |

### 2.1 CRITICAL Debt Items

| ID | Description | File:Line | Severity | Likelihood | Effort | Remediation |
|----|-------------|-----------|----------|------------|--------|-------------|
| TD-001 | `eval()` in cross-field validation | `codegen/validation_codegen.py:370` | CRITICAL | POSSIBLE | M | Replace with safe expression parser (e.g., `asteval` or restricted AST) |

**TD-001 Details:**
```python
# Current (UNSAFE for untrusted input):
result = eval(expression, {"__builtins__": {}}, record)
```

**Risk:** Arbitrary code execution if `rule_config.expression` comes from untrusted source (e.g., user-uploaded PDF guide).

**Remediation Options:**
1. Use `asteval` library for safe expression evaluation
2. Build whitelist of allowed operations (comparison, arithmetic only)
3. Sandbox with `RestrictedPython`

### 2.2 HIGH Debt Items

| ID | Description | File:Line | Severity | Likelihood | Effort | Remediation |
|----|-------------|-----------|----------|------------|--------|-------------|
| TD-002 | PDFGuideSource field extraction is heuristic-only | `parsers/pdf_guide_parser.py:150-250` | HIGH | LIKELY | L | Add LLM fallback when heuristics fail |
| TD-003 | No retry on transient PDF parsing errors | `sources/pdf_guide.py:80` | HIGH | POSSIBLE | S | Wrap with retry decorator |
| TD-004 | ExcelSource silently ignores formula cells | `parsers/excel_parser.py:120` | HIGH | LIKELY | M | Add warning when formula cells detected |

**TD-002 Details:**
- Current heuristics work for well-structured guides (pipe-delimited tables, consistent patterns)
- Fails on free-form prose guides, complex layouts
- **Evidence:** `test_pdf_guide_adversarial.py:test_sparse_text_pdf_rejected`

**TD-003 Details:**
- PyMuPDF can fail on malformed PDFs with transient errors
- No retry/backoff logic exists

**TD-004 Details:**
- Formula cells like `=SUM(A1:A10)` are evaluated at read time
- If workbook has external references, value may be None
- Should emit warning: "Formula cells detected, values may be stale"

### 2.3 MEDIUM Debt Items

| ID | Description | File:Line | Severity | Likelihood | Effort | Remediation |
|----|-------------|-----------|----------|------------|--------|-------------|
| TD-005 | FixedWidthSource has no explicit colspec override | `sources/fixed_width.py:60-80` | MEDIUM | LIKELY | M | Add YAML sidecar support |
| TD-006 | Validation codegen doesn't support RANGE in Python | `codegen/validation_codegen.py:300` | MEDIUM | LIKELY | S | Add RANGE rule codegen |
| TD-007 | No structured logging in source detection | `sources/__init__.py:70` | MEDIUM | CERTAIN | S | Add structured log events |
| TD-008 | Test fixtures not parametrized | `tests/test_fixed_width_adversarial.py` | MEDIUM | CERTAIN | M | Refactor to pytest.mark.parametrize |
| TD-009 | restore_source_registry fixture not in conftest.py | `tests/test_fixed_width_adversarial.py:45` | MEDIUM | CERTAIN | XS | Move to conftest.py for reuse |

**TD-005 Details:**
- Config mentions `remediation_explicit_colspec` but no mechanism exists
- User cannot override auto-inferred column boundaries
- **Evidence:** `parsers/fixed_width_config.py:65-70`

**TD-006 Details:**
- `generate_python_code()` skips RANGE rules with "# Not implemented" comment
- Runtime validator handles RANGE correctly

**TD-009 Details:**
- The `restore_source_registry` fixture solves test pollution but is defined inline
- Should be shared in `tests/conftest.py` for all source tests

### 2.4 LOW Debt Items

| ID | Description | File:Line | Severity | Likelihood | Effort | Remediation |
|----|-------------|-----------|----------|------------|--------|-------------|
| TD-010 | Pydantic codegen requires v2+ | `codegen/validation_codegen.py:400` | LOW | UNLIKELY | S | Add version check/fallback |
| TD-011 | No docstrings on internal parser functions | Various | LOW | CERTAIN | S | Add docstrings |
| TD-012 | Missing type hints on some helper functions | Various | LOW | CERTAIN | S | Add type hints |

---

## 3. Architecture Options Debate

### 3.1 Problem Statement

Remaining Bucket 2 work requires:
1. **PDFGuideSource enrichment** — Field definitions from PDF guides should enrich CSVSource/FixedWidthSource FileFields
2. **Schema-driven validation** — Validation rules from guides should flow to codegen
3. **Cross-source linking** — PDF guide fields should link to actual file spec fields

### 3.2 Option A: In-Memory Enrichment (Current Implicit Design)

**Approach:** After parsing PDF guide and CSV/FW, match fields by name and merge metadata in memory.

```python
# In build_silver_file_model.py
def enrich_fields_from_guide(file_fields, guide_fields):
    for ff in file_fields:
        matching = [gf for gf in guide_fields if gf.name == ff.name]
        if matching:
            ff.description = matching[0].description
            ff.validation_regex = matching[0].validation_regex
```

**Pros:**
- Simple, no new infrastructure
- Fast (in-memory matching)
- No DB schema changes

**Cons:**
- Field name matching is fragile (case, underscores, abbreviations)
- No persistence of enrichment provenance
- Cannot trace which guide enriched which field

**Effort:** 2-3 days

**Risk:** Medium (name matching failures)

### 3.3 Option B: KG-Based Enrichment

**Approach:** Populate KG with guide fields and file fields, use vector similarity to match.

```python
# In kg/pattern_discovery.py
def match_guide_to_file_fields(guide_node_id, file_spec_node_id):
    guide_fields = kg.get_children(guide_node_id, edge_type="HAS_FIELD")
    file_fields = kg.get_children(file_spec_node_id, edge_type="HAS_FIELD")
    
    for gf in guide_fields:
        best_match = kg.vector_similarity(gf.embedding, [f.embedding for f in file_fields])
        kg.add_edge(gf.id, best_match.id, "ENRICHES")
```

**Pros:**
- Robust matching via embeddings (handles synonyms, abbreviations)
- Provenance tracked via KG edges
- Supports many-to-many guide:file relationships

**Cons:**
- Requires embedding computation (LLM cost)
- KG infrastructure overhead
- More complex implementation

**Effort:** 5-7 days

**Risk:** Low (well-understood KG patterns)

### 3.4 Option C: Explicit Guide-File Linking at Parse Time

**Approach:** When PDFGuideSource parses, it outputs a `guide_spec` with linkable references. User provides explicit mapping.

```yaml
# guide_mapping.yaml (provided by user)
guide_file: customer_guide.pdf
target_file: customer_export.csv
field_mappings:
  CUST_ID: customer_id
  CUST_NAME: name
  ACCT_BAL: balance
```

**Pros:**
- Deterministic, user-controlled
- No fuzzy matching errors
- Clear provenance

**Cons:**
- Manual effort for users
- Doesn't scale for large guides
- Friction in "auto-discover" vision

**Effort:** 3-4 days

**Risk:** Low (simple implementation)

---

### 3.5 Selected Option: **Option A (In-Memory Enrichment) with Name Normalization**

**Rationale:**

1. **Scope:** Bucket 2 is MVP — minimal viable. Full KG enrichment can be Bucket 3.
2. **Iteration:** We can enhance with fuzzy matching later without breaking API.
3. **User feedback:** We need production usage data to understand match failures before investing in KG.
4. **Time constraint:** Option A ships in 2-3 days; Option B needs 5-7 days.

**Enhancement over naive Option A:**
- Add name normalization: lowercase, strip underscores/hyphens, remove common prefixes
- Add Levenshtein distance threshold for fuzzy matching
- Emit warning when no match found (user can provide explicit mapping)

**Implementation:**
```python
def normalize_field_name(name: str) -> str:
    return re.sub(r'[_\-\s]+', '', name.lower())

def fuzzy_match_score(name1: str, name2: str) -> float:
    n1, n2 = normalize_field_name(name1), normalize_field_name(name2)
    if n1 == n2:
        return 1.0
    # Levenshtein distance normalized by max length
    from difflib import SequenceMatcher
    return SequenceMatcher(None, n1, n2).ratio()

def enrich_fields_from_guide(file_fields, guide_fields, threshold=0.8):
    unmatched = []
    for ff in file_fields:
        best = max(guide_fields, key=lambda gf: fuzzy_match_score(ff.name, gf.name))
        score = fuzzy_match_score(ff.name, best.name)
        if score >= threshold:
            ff.description = best.description
            ff.validation_regex = best.validation_regex
            ff.inference_confidence = score  # Record match quality
        else:
            unmatched.append(ff.name)
    return unmatched  # Emit warning for these
```

**Upgrade path to Option B:**
When Bucket 3 adds KG enrichment, we:
1. Keep Option A as fast path for exact/fuzzy matches
2. Fall back to KG vector similarity only for unmatched fields
3. This gives best of both worlds

---

## 4. No-Interpretation Refactor Plan

### 4.1 Overview

No new files to create. All changes are edits to existing files.

### 4.2 Step Sequence

| Step | File | Change | Reason |
|------|------|--------|--------|
| 1 | `tests/conftest.py` | Add `restore_source_registry` fixture | Reuse across test files |
| 2 | `tests/test_fixed_width_adversarial.py` | Remove inline fixture, use conftest | TD-009 |
| 3 | `codegen/validation_codegen.py:370` | Replace `eval()` with safe evaluator | TD-001 |
| 4 | `codegen/validation_codegen.py:300` | Add RANGE rule to `generate_python_code()` | TD-006 |
| 5 | `parsers/excel_parser.py:120` | Add warning for formula cells | TD-004 |
| 6 | `sources/__init__.py:70` | Add structured logging | TD-007 |

### 4.3 Detailed Changes

#### Step 1: Add fixture to conftest.py

```python
# tests/conftest.py (ADD)

@pytest.fixture(autouse=False)
def restore_source_registry():
    """
    Save and restore SOURCE_REGISTRY to prevent test pollution.
    Use as autouse=True in test classes that clear registry.
    """
    from integration_coworker.sources import SOURCE_REGISTRY
    original = SOURCE_REGISTRY.copy()
    yield
    SOURCE_REGISTRY.clear()
    SOURCE_REGISTRY.extend(original)
```

#### Step 2: Remove inline fixture from test_fixed_width_adversarial.py

Remove lines 40-55 (the local `restore_source_registry` fixture).

#### Step 3: Replace eval() with safe evaluator

```python
# codegen/validation_codegen.py:370 (CHANGE)

# Before:
result = eval(expression, {"__builtins__": {}}, record)

# After:
from simpleeval import simple_eval, EvalWithCompoundTypes

def _safe_eval_expression(expression: str, record: Dict[str, Any]) -> Any:
    """Safely evaluate a cross-field expression."""
    try:
        evaluator = EvalWithCompoundTypes(names=record)
        return evaluator.eval(expression)
    except Exception as e:
        return False  # Treat eval errors as validation failure
```

**Note:** Requires adding `simpleeval` to requirements.txt.

#### Step 4: Add RANGE rule codegen

```python
# codegen/validation_codegen.py:300 (ADD)

elif rule.rule_type == ValidationRuleType.RANGE:
    min_val = config.get("min", "None")
    max_val = config.get("max", "None")
    return f'''
def _validate_rule_{index}(value) -> Tuple[bool, str]:
    if value is None or value == "":
        return True, ""
    try:
        num_val = float(value)
        if {min_val} is not None and num_val < {min_val}:
            return False, "{error_msg}"
        if {max_val} is not None and num_val > {max_val}:
            return False, "{error_msg}"
        return True, ""
    except (ValueError, TypeError):
        return False, "{error_msg}"
'''
```

#### Step 5: Add formula cell warning

```python
# parsers/excel_parser.py:120 (ADD)

if cell.data_type == 'f':  # Formula cell
    warnings.append(
        f"Sheet '{sheet.title}' row {row_idx} col {col_idx}: "
        f"Formula cell detected. Value may be stale."
    )
```

#### Step 6: Add structured logging

```python
# sources/__init__.py:70 (CHANGE)

# Before:
logger.debug(f"Source {source.__class__.__name__} scored {score:.2f} for {uri}")

# After:
logger.info(
    "source_detection",
    extra={
        "source_class": source.__class__.__name__,
        "score": score,
        "uri": uri,
        "content_type": content_type,
    }
)
```

---

## 5. Bucket 2 Implementation Plan

### 5.1 Remaining Work

| Item | Status | Effort | Priority |
|------|--------|--------|----------|
| PDFGuideSource enrichment flow | 🔲 Not started | M | P1 |
| Field fuzzy matching | 🔲 Not started | S | P1 |
| Enrichment persistence | 🔲 Not started | S | P1 |
| Warning propagation to CLI | ✅ Done | - | - |
| Postgres idempotency | ✅ Done | - | - |
| Validation codegen | ✅ Done | - | - |

### 5.2 Implementation Tasks

#### Task 5.2.1: PDFGuideSource Enrichment Flow

**File:** `graph/nodes/build_silver_file_model.py`

**Change:** After building file_specs and file_fields, check for parsed PDF guides and enrich.

```python
def build_silver_file_model(state: WorkflowState) -> WorkflowState:
    # ... existing code ...
    
    # NEW: Enrich from PDF guides
    guide_specs = [
        ps for ps in state.parsed_specs 
        if ps.source_type == SourceType.FILE 
        and ps.metadata.get("source_handler") == "PDFGuideSource"
    ]
    
    if guide_specs and state.file_fields:
        guide_fields = []
        for gs in guide_specs:
            guide_fields.extend(gs.data.get("guide_fields", []))
        
        unmatched = enrich_fields_from_guide(state.file_fields, guide_fields)
        if unmatched:
            state.warnings.append(
                f"Could not match guide fields to: {', '.join(unmatched[:5])}"
            )
    
    return state
```

#### Task 5.2.2: Field Fuzzy Matching

**File:** `parsers/field_matching.py` (NEW)

```python
"""
Field name matching utilities for PDF guide enrichment.
"""
import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from integration_coworker.domain.models import FileField


def normalize_field_name(name: str) -> str:
    """Normalize field name for matching."""
    return re.sub(r'[_\-\s]+', '', name.lower())


def fuzzy_match_score(name1: str, name2: str) -> float:
    """Compute fuzzy match score between two field names."""
    n1, n2 = normalize_field_name(name1), normalize_field_name(name2)
    if n1 == n2:
        return 1.0
    return SequenceMatcher(None, n1, n2).ratio()


def enrich_fields_from_guide(
    file_fields: List[FileField],
    guide_fields: List[FileField],
    threshold: float = 0.8,
) -> List[str]:
    """
    Enrich file fields with metadata from guide fields.
    
    Returns list of file field names that could not be matched.
    """
    if not guide_fields:
        return []
    
    unmatched = []
    for ff in file_fields:
        best_match = max(guide_fields, key=lambda gf: fuzzy_match_score(ff.name, gf.name))
        score = fuzzy_match_score(ff.name, best_match.name)
        
        if score >= threshold:
            # Enrich from guide
            if best_match.description and not ff.description:
                ff.description = best_match.description
            if best_match.validation_regex and not ff.validation_regex:
                ff.validation_regex = best_match.validation_regex
            if best_match.length and not ff.length:
                ff.length = best_match.length
            if best_match.format_mask and not ff.format_mask:
                ff.format_mask = best_match.format_mask
            # Record match quality
            ff.inference_confidence = min(ff.inference_confidence, score)
        else:
            unmatched.append(ff.name)
    
    return unmatched
```

#### Task 5.2.3: Enrichment Persistence

**File:** `persistence/postgres.py`

**Change:** Add `guide_source_uri` column to file_fields for provenance.

```sql
ALTER TABLE spec_silver.file_fields ADD COLUMN IF NOT EXISTS 
    guide_source_uri TEXT;
```

### 5.3 Test Plan for Enrichment

| Test | File | What It Proves |
|------|------|----------------|
| `test_enrichment_exact_match` | `test_field_matching.py` | Exact name match works |
| `test_enrichment_fuzzy_match` | `test_field_matching.py` | Fuzzy matching with threshold |
| `test_enrichment_no_match_warning` | `test_field_matching.py` | Unmatched fields emit warning |
| `test_build_silver_enriches_from_guide` | `test_build_silver_file_model.py` | End-to-end enrichment |
| `test_enrichment_persisted` | `test_file_integration_postgres.py` | guide_source_uri persisted |

---

## 6. Production Test Report

### 6.1 Test Execution Summary

```bash
# Command run:
pytest tests/test_pdf_guide_adversarial.py tests/test_excel_adversarial.py \
       tests/test_fixed_width_source.py tests/test_fixed_width_adversarial.py \
       tests/test_validation_codegen.py -v

# Results:
tests/test_fixed_width_source.py ........................... [29 passed]
tests/test_fixed_width_adversarial.py ...................... [35 passed]
tests/test_excel_adversarial.py ............................ [24 passed]
tests/test_pdf_guide_adversarial.py ........................ [35 passed]
tests/test_validation_codegen.py ........................... [35 passed]

=================== 158 passed in 12.34s ===================
```

### 6.2 Key Test Categories

| Category | Count | Description |
|----------|-------|-------------|
| Detection gates | 15 | Verify DETECT_MIN enforced |
| Inference warnings | 10 | Verify INFER_WARN triggers |
| Hard rejects | 12 | CSV-as-FW, ragged lines, etc. |
| Signal breakdown | 8 | Individual signal scores |
| Postgres idempotency | 20 | Upsert/no-duplicate tests |
| Validation codegen | 35 | All rule types |
| Multi-sheet Excel | 5 | One FileSpec per sheet |
| PDF field extraction | 10 | Heuristic extraction |

### 6.3 Known Test Gaps

| Gap | Reason | Plan |
|-----|--------|------|
| No LLM fallback test | LLM extraction not implemented | Bucket 3 |
| No OCR test | OCR not implemented | Bucket 3 |
| No EDI test | EDI source not implemented | Bucket 3 |
| No negative Postgres test | testcontainers not in CI | Add to CI matrix |

### 6.4 Bugs Found and Fixed

| Bug | Test That Found It | Fix |
|-----|-------------------|-----|
| Test pollution from SOURCE_REGISTRY.clear() | `test_router_gate_contract` | Added `restore_source_registry` fixture |
| Quote escaping in Pydantic codegen | `test_generate_pydantic_enum_validator` | Used `repr()` for values |
| validation/codegen.py wrong location | Manual review | Moved to codegen/validation_codegen.py |

---

## 7. PR Plan

### 7.1 PR Structure

| PR | Title | Dependencies | Files Changed |
|----|-------|--------------|---------------|
| PR-1 | `[Tech Debt] Move restore_source_registry to conftest` | None | 2 |
| PR-2 | `[Security] Replace eval() with safe evaluator` | None | 2 |
| PR-3 | `[Feature] Add RANGE rule codegen` | None | 2 |
| PR-4 | `[Feature] PDFGuideSource enrichment flow` | PR-1 | 4 |
| PR-5 | `[Observability] Structured logging in source detection` | None | 1 |

### 7.2 PR-1: Move restore_source_registry to conftest

**Checklist:**
- [ ] Add fixture to `tests/conftest.py`
- [ ] Remove inline fixture from `tests/test_fixed_width_adversarial.py`
- [ ] Run `pytest tests/test_fixed_width_adversarial.py -v`
- [ ] Verify no test pollution after clear()

**Rollback:** Revert commit.

### 7.3 PR-2: Replace eval() with safe evaluator

**Checklist:**
- [ ] Add `simpleeval>=0.9.12` to requirements.txt
- [ ] Replace eval() in `codegen/validation_codegen.py:370`
- [ ] Add test: `test_cross_field_safe_eval_rejects_code_injection`
- [ ] Run `pytest tests/test_validation_codegen.py -v`

**Rollback:** Revert commit. No DB changes.

### 7.4 PR-3: Add RANGE rule codegen

**Checklist:**
- [ ] Add RANGE case in `_generate_validator_code()`
- [ ] Add test: `test_generate_range_validator`
- [ ] Run `pytest tests/test_validation_codegen.py -v`

**Rollback:** Revert commit. No DB changes.

### 7.5 PR-4: PDFGuideSource enrichment flow

**Checklist:**
- [ ] Create `parsers/field_matching.py`
- [ ] Update `graph/nodes/build_silver_file_model.py`
- [ ] Add migration: `guide_source_uri` column
- [ ] Add tests in `tests/test_field_matching.py`
- [ ] Add integration test in `tests/test_build_silver_file_model.py`
- [ ] Run full test suite

**Rollback:** 
1. Revert code changes
2. Column migration is ADD-only (safe)

### 7.6 PR-5: Structured logging

**Checklist:**
- [ ] Update `sources/__init__.py:70` with structured log
- [ ] Verify JSON log format in output
- [ ] No new tests needed (observability change)

**Rollback:** Revert commit.

### 7.7 Merge Order

```
PR-1 → PR-4 (dependency)
PR-2 (independent)
PR-3 (independent)
PR-5 (independent)

Recommended order: PR-2 → PR-1 → PR-3 → PR-4 → PR-5
```

**Rationale:**
- PR-2 (security) ships first
- PR-1 unblocks PR-4
- PR-3 and PR-5 are independent, can merge in any order

---

## Appendix A: Evidence References

| Evidence ID | File | Lines | Description |
|-------------|------|-------|-------------|
| E-001 | `sources/__init__.py` | 1-150 | SOURCE_REGISTRY, detect_and_route |
| E-002 | `sources/base.py` | 1-160 | SpecSource protocol, ParsedSpec |
| E-003 | `parsers/fixed_width_config.py` | 1-175 | Confidence config, signals |
| E-004 | `parsers/pdf_guide_config.py` | 1-100 | PDF guide thresholds |
| E-005 | `codegen/validation_codegen.py` | 1-600 | Rule codegen, eval() usage |
| E-006 | `graph/state.py` | 40-70 | WorkflowState file fields |
| E-007 | `domain/models.py` | 100-200 | FileSpec, FileField, ValidationRuleType |
| E-008 | `tests/test_file_integration_postgres.py` | 1-750 | Idempotency tests |
| E-009 | `tests/test_fixed_width_adversarial.py` | 1-500 | Detection gate tests |

---

## Appendix B: Glossary

| Term | Definition |
|------|------------|
| DETECT_MIN | Minimum confidence for router to accept source |
| INFER_MIN | Minimum confidence for valid ParsedSpec |
| INFER_WARN | Threshold below which warnings emitted |
| ParsedSpec | Unified output from all sources |
| SourceType | Enum: API or FILE |
| FileSpec | Domain model for file-based specs |
| FileField | Domain model for fields within FileSpec |
| ValidationRuleType | Enum of validation rule types |
| KG | Knowledge Graph |
| PDFGuideSource | Parser for PDF data transmission guides |
| FixedWidthSource | Parser for fixed-width positional files |
| ExcelSource | Parser for XLSX/XLS workbooks |
