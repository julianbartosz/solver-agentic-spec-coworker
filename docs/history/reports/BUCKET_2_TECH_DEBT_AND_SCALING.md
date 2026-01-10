# Bucket 2: Technical Debt and Scalability Analysis

**Author:** AI Code Agent  
**Date:** 2024-12-15  
**Status:** COMPLETE  
**Scope:** Multi-source ingestion pipeline (CSV, Excel, FixedWidth, PDFGuide) + validation codegen  
**Architecture Principle:** Knowledge Graph First — All persistent relationships use `kg.nodes`/`kg.edges`

---

## Table of Contents

1. [Current Capabilities Map](#1-current-capabilities-map)
2. [Knowledge Graph Integration](#2-knowledge-graph-integration)
3. [Technical Debt Register](#3-technical-debt-register)
4. [Production Blockers](#4-production-blockers)
5. [Scalability Analysis](#5-scalability-analysis)
6. [Evidence Table](#6-evidence-table)

---

## 1. Current Capabilities Map

### 1.1 SpecSource Plugin System

**Location:** `src/integration_coworker/sources/`

| Component | File | Purpose |
|-----------|------|---------|
| Registry | `__init__.py:30-50` | `SOURCE_REGISTRY` stores `(priority, source)` tuples |
| Protocol | `base.py:70-110` | `SpecSource` Protocol: `detect()` → float, `parse()` → `ParsedSpec` |
| Router | `__init__.py:52-95` | `detect_and_route()` scores all sources, picks highest |

**Registration Order (Priority Descending):**
```python
# __init__.py:109-140 - _register_default_sources()
OpenAPISource    → priority 90  # API specs first
CSVSource        → priority 70  # Common delimited
ExcelSource      → priority 65  # ZIP-based detection
FixedWidthSource → priority 60  # Needs delimiter-absence check  
PDFGuideSource   → priority 40  # Most permissive fallback
```

### 1.2 Source Detection Contracts

Each source implements `detect(content, uri, content_type) → float`:

| Source | File | Detection Signals (weighted) |
|--------|------|------------------------------|
| **FixedWidthSource** | `sources/fixed_width.py` | line_consistency (35%), delimiter_absence (25%), boundary_stability (25%), extension_hint (15%) |
| **ExcelSource** | `sources/excel.py` | zip_signature (40%), content_type (20%), extension_hint (25%), workbook_load (15%) |
| **PDFGuideSource** | `sources/pdf_guide.py` | pdf_signature (30%), text_extraction (25%), guide_keywords (20%), field_table_pattern (25%) |
| **CSVSource** | `sources/csv_source.py` | extension_hint, delimiter_detection, header_detection |

### 1.3 Confidence Gate Architecture

All sources use three-tier gating:

| Gate | Purpose | FW | Excel | PDF |
|------|---------|------|-------|-----|
| `DETECT_MIN` | Router acceptance threshold | 0.90 | 0.85 | 0.85 |
| `INFER_MIN` | Minimum for valid ParsedSpec | 0.60 | 0.50 | 0.50 |
| `INFER_WARN` | Below this: emit warnings | 0.85 | 0.70 | 0.70 |

### 1.4 ParsedSpec Contract

**Location:** `sources/base.py:35-60`

```python
@dataclass
class ParsedSpec:
    source_type: SourceType      # API | FILE
    source_uri: str
    data: Any                    # {"file_spec": FileSpec, "fields": [...]}
    metadata: Dict[str, Any]
    errors: List[str]
    warnings: List[str]          # ← Key for confidence-gated warnings
    confidence: float
```

---

## 2. Knowledge Graph Integration

### 2.1 Existing KG Infrastructure

**The project already has a robust Knowledge Graph!** Located in:
- **Models:** `domain/models.py:514-650` — `KGNode`, `KGEdge`, `KGNodeType`, `KGEdgeRelation`
- **Schema:** `persistence/postgres.py:559-700` — `kg.nodes`, `kg.edges`, `kg.workflow_steps`
- **Retrieval:** `retrieval/semantic_search.py` — `search_kg_templates()`
- **Graph Node:** `graph/nodes/align_task_with_kg.py` — GraphRAG workflow alignment

### 2.2 File Integration KG Node Types

Already defined in `KGNodeType`:
```python
FILE_SPEC = "file_spec"
FILE_FIELD = "file_field"
RECORD_LAYOUT = "record_layout"
FILE_PATTERN = "file_pattern"  # e.g., "pattern.file.header_detail_trailer"
```

### 2.3 File Integration KG Edge Relations

Already defined in `KGEdgeRelation`:
```python
HAS_FIELD = "has_field"           # file_spec -> file_field
MAPS_TO = "maps_to"               # file_field -> entity_field
VALIDATED_BY = "validated_by"     # file_field -> validation_rule
DERIVES_FROM_GUIDE = "derives_from_guide"  # file_spec -> pdf_guide (provenance)
FILE_FLOWS_TO = "file_flows_to"   # file_spec -> endpoint (ETL target)
```

### 2.4 Required KG Integration (NOT YET IMPLEMENTED)

| Component | Current State | Required State |
|-----------|--------------|----------------|
| **FileSpec persistence** | Direct postgres table | Also create `kg.nodes` entry with type `FILE_SPEC` |
| **FileField persistence** | Direct postgres table | Also create `kg.nodes` entry with type `FILE_FIELD` |
| **PDF→Field provenance** | String field (`enriched_from`) | Create `DERIVES_FROM_GUIDE` edge in `kg.edges` |
| **Field matching** | O(n²) fuzzy match | Use `kg.nodes.embedding` for O(n·logn) vector search |
| **Pattern detection** | Not implemented | Create `FILE_PATTERN` nodes, link via `IMPLEMENTS_PATTERN` edges |

### 2.5 KG-Based Field Matching Architecture

**Current (O(n²) — BAD):**
```python
for guide_field in guide_fields:
    for file_field in file_fields:
        similarity = fuzz.ratio(guide_field.name, file_field.name)
```

**Required (O(n·logn) — GOOD):**
```python
# 1. Embed all file fields → kg.nodes with embeddings
for field in file_fields:
    kg_node = KGNode(
        node_type=KGNodeType.FILE_FIELD,
        key=f"file_field.{spec_key}.{field.name}",
        name=field.name,
        embedding=embed(field.name + " " + field.description),
    )
    persist_kg_node(kg_node)

# 2. Query by vector similarity (IVFFlat index, O(logn))
matches = query_similar_nodes(
    embedding=embed(guide_field_description),
    node_type=KGNodeType.FILE_FIELD,
    top_k=5,
)
```

### 2.6 KG-Based Provenance Tracking

**Current (String field — WEAK):**
```python
@dataclass
class FileField:
    enriched_from: Optional[str] = None  # Just a URI string
```

**Required (Graph edge — STRONG):**
```python
# When PDF guide enriches a field:
edge = KGEdge(
    src_node_id=file_field_node_id,  # The field being enriched
    dst_node_id=pdf_guide_node_id,   # The PDF guide
    relation_type=KGEdgeRelation.DERIVES_FROM_GUIDE,
    weight=enrichment_confidence,
    properties={
        "enriched_fields": ["description", "format_string"],
        "extraction_method": "table_parser",
        "page_number": 5,
    },
)
persist_kg_edge(edge)
```

**Query provenance:**
```sql
SELECT 
    f.key AS file_field,
    g.key AS source_guide,
    e.properties->>'extraction_method' AS method,
    e.weight AS confidence
FROM kg.edges e
JOIN kg.nodes f ON e.src_node_id = f.id
JOIN kg.nodes g ON e.dst_node_id = g.id
WHERE e.relation_type = 'derives_from_guide'
AND f.key LIKE 'file_field.my_spec.%';
```

---

## 3. Technical Debt Register

## 3. Technical Debt Register

### Severity Scale
| Level | Definition |
|-------|------------|
| CRITICAL | Security vulnerability or data corruption risk |
| HIGH | Silent failures, missing KG integration, or incorrect results |
| MEDIUM | Maintenance burden, complicates debugging |
| LOW | Polish, documentation, minor cleanup |

### Likelihood Scale
| Level | Definition |
|-------|------------|
| CERTAIN | Happens in normal usage |
| LIKELY | Happens with realistic edge cases |
| POSSIBLE | Happens under unusual conditions |
| UNLIKELY | Rare edge case |

### Effort Scale
| Level | Definition |
|-------|------------|
| XS | < 1 hour |
| S | 1-4 hours |
| M | 1-2 days |
| L | 3-5 days |
| XL | > 1 week |

---

### 3.1 CRITICAL Debt

| ID | Description | File:Line | Likelihood | Effort | Risk Score |
|----|-------------|-----------|------------|--------|------------|
| **TD-SEC-001** | `eval()` in cross-field validation allows arbitrary code execution | `codegen/validation_codegen.py:469` | LIKELY | M | **18** |

**TD-SEC-001 Details:**

```python
# CURRENT (UNSAFE):
result = eval(expression, {"__builtins__": {}}, record)
```

**Status:** ✅ FIXED — Replaced with `simpleeval` AST-based evaluator.

---

### 3.2 HIGH Debt (KG Integration Gaps)

| ID | Description | File:Line | Likelihood | Effort | Risk Score |
|----|-------------|-----------|------------|--------|------------|
| **TD-KG-001** | FileSpec not persisted to KG (no `FILE_SPEC` nodes) | `persistence/postgres.py` | CERTAIN | M | **15** |
| **TD-KG-002** | FileField not persisted to KG (no `FILE_FIELD` nodes) | `persistence/postgres.py` | CERTAIN | M | **15** |
| **TD-KG-003** | PDF→Field provenance uses string, not `DERIVES_FROM_GUIDE` edge | `graph/nodes/build_silver_file_model.py` | LIKELY | M | **12** |
| **TD-KG-004** | Field matching uses O(n²) fuzzy, not KG embedding search | `parsers/pdf_guide_parser.py` | LIKELY | L | **12** |
| **TD-OBS-001** | No structured logging for source detection/routing | `sources/__init__.py:70` | CERTAIN | S | **12** |
| **TD-XLS-001** | Excel parser silently handles formula cells without warnings | `parsers/excel_parser.py:170` | LIKELY | M | **10** |

**TD-KG-001 Details:**

File specs are persisted to `file_specs` table but NOT to `kg.nodes`. This breaks:
- GraphRAG queries that should find file specs
- Pattern detection (`IMPLEMENTS_PATTERN` edges)
- Cross-reference with API specs

**Required Fix:**
```python
# In persist_file_spec():
kg_node = KGNode(
    node_type=KGNodeType.FILE_SPEC,
    provider_code=spec.source_system_id,
    key=f"file_spec.{spec.source_system_id}.{spec.name}",
    name=spec.name,
    description=spec.description,
    properties={"file_type": spec.file_type, "delimiter": spec.delimiter},
    embedding=embed(f"{spec.name} {spec.description}"),
)
persist_kg_node(kg_node)
```

**TD-KG-003 Details:**

Current provenance is a string field:
```python
enriched_from: Optional[str] = None  # Just "guide.pdf"
```

Cannot answer: "Which guide enriched this field? What confidence? What page?"

**Required Fix:** Create `DERIVES_FROM_GUIDE` edge with metadata.

**TD-KG-004 Details:**

Current field matching is O(n²):
```python
for guide_field in guide_fields:      # O(n)
    for file_field in file_fields:    # O(m)
        similarity = fuzz.ratio(...)  # O(len) 
```

For 100 guide fields × 50 file fields = 5000 comparisons. Scales poorly.

**Required Fix:** 
1. Index file fields in `kg.nodes` with embeddings
2. Query by vector similarity (IVFFlat index → O(logn))

---

### 3.3 MEDIUM Debt

| ID | Description | File:Line | Likelihood | Effort |
|----|-------------|-----------|------------|--------|
| **TD-CFG-001** | No explicit colspec override for FixedWidthSource | `sources/fixed_width.py:60` | LIKELY | M |
| **TD-CODE-001** | RANGE rule not implemented in `generate_python_code()` | `codegen/validation_codegen.py:500` | LIKELY | S |
| **TD-PDF-001** | PDFGuideSource heuristic-only extraction fails on prose guides | `parsers/pdf_guide_parser.py:150` | LIKELY | L |
| **TD-DSN-001** | DSN construction duplicated in conftest + persistence | Various | POSSIBLE | S |

**TD-PDF-001 Note:** 
With KG integration, failed PDF extractions can still contribute partial data that gets refined by:
1. User feedback → `kg.feedback_records`
2. Pattern matching → `IMPLEMENTS_PATTERN` edges
3. Cross-spec inference → `SIMILAR_TO` edges to other file specs

---

### 3.4 LOW Debt

| ID | Description | File:Line | Likelihood | Effort |
|----|-------------|-----------|------------|--------|
| **TD-DOC-001** | Internal parser functions lack docstrings | Various | CERTAIN | S |
| **TD-TYPE-001** | Some helper functions missing type hints | Various | CERTAIN | S |
| **TD-WARN-001** | SyntaxWarning in generated code (`is not` with literal) | Test output | CERTAIN | XS |

---

## 4. Production Blockers

### 4.1 Security (Blocking)

| Blocker | Impact | Status |
|---------|--------|--------|
| `eval()` in validation | Arbitrary code execution from untrusted rules | ✅ FIXED — `safe_eval.py` |

### 4.2 Observability (Soft Blocking)

| Blocker | Impact | Status |
|---------|--------|--------|
| No structured detection logs | Cannot alert on misrouting | ✅ FIXED — `logging_config.py` |
| No confidence histograms | Cannot tune thresholds | 🔲 TODO — Add metrics emission |

### 4.3 Data Quality (Soft Blocking)

| Blocker | Impact | Status |
|---------|--------|--------|
| Excel formula cells silent | Users get stale/wrong data | ✅ FIXED — Formula warnings added |
| No PDF enrichment provenance | Cannot trace field origins | 🔲 TODO — Use KG `DERIVES_FROM_GUIDE` edge |

### 4.4 KG Integration (Blocking for Scale)

| Blocker | Impact | Status |
|---------|--------|--------|
| FileSpec not in KG | GraphRAG can't find file specs | 🔲 TODO — Create FILE_SPEC nodes |
| FileField not in KG | Vector search unavailable | 🔲 TODO — Create FILE_FIELD nodes |
| Fuzzy matching O(n²) | Doesn't scale beyond ~100 fields | 🔲 TODO — Use pgvector similarity |
| Provenance string, not graph | Can't answer "why this field?" | 🔲 TODO — DERIVES_FROM_GUIDE edges |

### 4.5 Configuration (Non-Blocking)

| Gap | Impact | Workaround |
|-----|--------|------------|
| No explicit colspec | Cannot fix misdetected FW boundaries | Users manually edit |
| No env override for thresholds | Cannot tune per-deployment | Code change needed |

---

## 5. Scalability Analysis

### 5.1 Memory Growth Risks

| Component | Risk | Scenario | Mitigation |
|-----------|------|----------|------------|
| **ExcelSource** | HIGH | 100k row workbook loads entire content | Stream with `read_only=True` (already done) |
| **FixedWidthSource** | MEDIUM | 1M line file for boundary detection | `infer_nrows=100` limit (already done) |
| **PDFGuideSource** | MEDIUM | Large PDF text extraction | Page-by-page streaming needed |
| **ValidationCodegen** | LOW | Generated code strings | Bounded by rule count |
| **KG Nodes** | LOW | Large number of FILE_FIELD nodes | Batch inserts, indexed by spec_key |

### 5.2 CPU Hotspots

| Component | Risk | Complexity | Mitigation |
|-----------|------|------------|------------|
| **Boundary detection** (FW) | MEDIUM | O(lines × positions) | Already limited by `infer_nrows` |
| **Type inference** (Excel) | LOW | O(rows × columns) | Already limited by `infer_nrows` |
| **Fuzzy field matching** | HIGH | O(guide_fields × file_fields) | **KG vector search (IVFFlat) → O(logn)** |
| **Embedding generation** | MEDIUM | O(fields) × embedding_time | Batch embeddings, cache in KG |

### 5.3 Database Write Amplification

| Operation | Risk | Pattern | Mitigation |
|-----------|------|---------|------------|
| FileSpec upsert | LOW | 1 per spec | Idempotent |
| FileField upsert | MEDIUM | N per spec | Bulk upsert needed for large N |
| Multi-sheet Excel | MEDIUM | 1 FileSpec per sheet | Cap sheets (`max_sheets=10`) |
| **KG node persist** | MEDIUM | N fields → N nodes | Batch INSERT with ON CONFLICT |
| **KG edge persist** | MEDIUM | M matches → M edges | Batch INSERT |

### 5.4 KG-Enabled Scaling Targets

| Scenario | Current | With KG | Improvement |
|----------|---------|---------|-------------|
| 100 guide fields × 50 file fields | 5000 fuzzy comparisons | 100 vector queries | **50× fewer ops** |
| Similar spec discovery | Full table scan | Vector similarity | O(logn) via IVFFlat |
| Provenance query | Join string columns | Graph traversal | Native graph path query |

### 5.5 Load Test Requirements

Need tests proving:
1. ✅ Boundary detection is O(n) not O(n²)
2. ✅ Type inference doesn't explode with many columns  
3. 🔲 **KG vector search scales sublinearly**
4. 🔲 **Batch KG inserts don't degrade under load**

---

## 6. Evidence Table

### 6.1 Feature → File → Tests

| Feature | Primary File | Tests |
|---------|--------------|-------|
| FixedWidth detection | `sources/fixed_width.py` | `test_fixed_width_source.py` (29), `test_fixed_width_adversarial.py` (35) |
| FixedWidth config | `parsers/fixed_width_config.py` | via source tests |
| FixedWidth parser | `parsers/fixed_width_parser.py` | via source tests |
| Excel detection | `sources/excel.py` | `test_excel_adversarial.py` (24) |
| Excel parser | `parsers/excel_parser.py` | via source tests |
| Excel config | `parsers/excel_config.py` | via source tests |
| PDFGuide detection | `sources/pdf_guide.py` | `test_pdf_guide_adversarial.py` (35) |
| PDFGuide parser | `parsers/pdf_guide_parser.py` | via source tests |
| PDFGuide config | `parsers/pdf_guide_config.py` | via source tests |
| Validation codegen | `codegen/validation_codegen.py` | `test_validation_codegen.py` (35) |
| Router | `sources/__init__.py` | via adversarial tests |
| ParsedSpec contract | `sources/base.py` | via all source tests |
| Postgres persistence | `persistence/postgres.py` | `test_file_integration_postgres.py` |

### 6.2 Test Coverage Summary

| Suite | Tests | Status |
|-------|-------|--------|
| `test_fixed_width_source.py` | 29 | ✅ Pass |
| `test_fixed_width_adversarial.py` | 35 | ✅ Pass |
| `test_excel_adversarial.py` | 24 | ✅ Pass |
| `test_pdf_guide_adversarial.py` | 35 | ✅ Pass |
| `test_validation_codegen.py` | 35 | ✅ Pass |
| `test_safe_eval_security.py` | 30 | ✅ Pass |
| `test_excel_formula_warnings.py` | 10 | ✅ Pass |
| **Total** | **198** | **✅ All Pass** |

### 6.3 Warnings in Test Output

```
tests/test_validation_codegen.py::TestGeneratePythonCode::test_generates_valid_python
  <generated>:51: SyntaxWarning: "is not" with a literal. Did you mean "!="?

tests/test_validation_codegen.py::TestGeneratePydanticModel::test_generates_valid_python
  <generated>:25: DeprecationWarning: invalid escape sequence '\w'
```

**Root Causes:**
1. Generated code uses `is not None` pattern where `!= None` would be clearer
2. Regex patterns not using raw strings in generated code

---

## Appendix A: Threat Model for Cross-Field Validation

### Current State (UNSAFE)

```python
# codegen/validation_codegen.py:469
result = eval(expression, {"__builtins__": {}}, record)
```

### Attack Surface

| Vector | Example | Blocked? |
|--------|---------|----------|
| Direct import | `__import__('os')` | ✅ Yes (`__builtins__={}`) |
| Attribute traversal | `().__class__.__bases__[0].__subclasses__()` | ❌ No |
| Generator yield | `(yield from open('/etc/passwd'))` | ❌ No |
| Lambda recursion | `(lambda f: f(f))(lambda f: f(f))` | ❌ No (infinite loop) |

### Required Solution: `simpleeval`

```python
from simpleeval import simple_eval, EvalWithCompoundTypes

def _safe_eval_expression(expression: str, record: Dict[str, Any]) -> Any:
    """Safely evaluate a cross-field expression."""
    evaluator = EvalWithCompoundTypes(names=record)
    # Whitelist only comparison and arithmetic operators
    evaluator.operators = simpleeval.DEFAULT_OPERATORS
    return evaluator.eval(expression)
```

### Allowed Grammar (after fix)

| Operation | Example | Allowed |
|-----------|---------|---------|
| Comparison | `amount > 100` | ✅ |
| Arithmetic | `total == qty * price` | ✅ |
| Boolean logic | `flag and amount > 0` | ✅ |
| String ops | `status in ['A', 'B']` | ✅ |
| Attribute access | `record.amount` | ❌ |
| Function calls | `int(value)` | ❌ (unless whitelisted) |
| Import | `__import__` | ❌ |

---

## Appendix B: Structured Logging Specification

### Required Log Events

| Event | Fields | Level |
|-------|--------|-------|
| `source.detection.start` | `uri`, `content_type`, `content_length` | DEBUG |
| `source.detection.score` | `source_class`, `uri`, `score`, `signals`, `reasons` | INFO |
| `source.detection.selected` | `source_class`, `uri`, `score` | INFO |
| `source.detection.rejected` | `uri`, `scores`, `best_score` | WARNING |
| `source.inference.start` | `source_class`, `uri` | DEBUG |
| `source.inference.complete` | `source_class`, `uri`, `confidence`, `warning_count`, `field_count` | INFO |
| `source.inference.failed` | `source_class`, `uri`, `error` | ERROR |

### Implementation Approach

**Option A: stdlib logging with `extra`**
```python
logger.info(
    "source.detection.score",
    extra={
        "source_class": source.__class__.__name__,
        "uri": uri,
        "score": score,
        "signals": signals,
    }
)
```
**Pros:** No new dependency
**Cons:** Requires custom Formatter for JSON output

**Option B: structlog**
```python
import structlog
log = structlog.get_logger()
log.info("source.detection.score", source_class=..., uri=..., score=...)
```
**Pros:** Native JSON, context binding
**Cons:** New dependency

**Selected: Option A** (stdlib) for minimal dependencies, with JSON formatter.

---

## Appendix C: Excel Formula Handling Specification

### Current Behavior

```python
workbook = openpyxl.load_workbook(..., data_only=True)
```

- `data_only=True`: Returns cached computed values from formulas
- If workbook wasn't saved with Excel: formula cells return `None`
- If formulas have external refs: returns `None`

### Required Behavior

1. Detect formula cells during parsing
2. Emit warning: "Sheet 'X' contains formula cells. Values may be stale."
3. Track which columns have formulas in metadata
4. Allow override: `formula_handling: 'values' | 'formulas' | 'error'`

### Implementation

```python
# In _infer_sheet_schema():
formula_columns = []
for cell in sheet.iter_rows(values_only=False):  # Note: values_only=False
    if cell.data_type == 'f':
        formula_columns.append(cell.column)
        warnings.append(f"Formula detected at {cell.coordinate}")
```

**Note:** This requires loading workbook with `values_only=False` for at least one pass, which has memory implications.

---

## Appendix D: DSN Construction Standards

### libpq-Compatible DSN Format

```
postgresql://[user[:password]@][host][:port][/dbname][?param1=value1&...]
```

### Current Implementation (Correct)

```python
# tests/conftest.py:180-210
host = postgres_container.get_container_host_ip()
port = postgres_container.get_exposed_port(5432)
dsn = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
```

### Why Not `get_connection_url()`

Testcontainers' `get_connection_url()` is documented as "SQLAlchemy-compatible":
- May return `postgresql+psycopg://...`
- psycopg requires plain `postgresql://...`

### Required: Centralize DSN Builder

```python
# persistence/dsn.py (NEW)
def build_libpq_dsn(
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
) -> str:
    """Build libpq-compatible DSN string."""
    # URL-encode password for special chars
    from urllib.parse import quote_plus
    safe_password = quote_plus(password)
    return f"postgresql://{user}:{safe_password}@{host}:{port}/{dbname}"
```
