# File Processing Hardening Inventory

> Generated: 2025-12-20  
> Purpose: Audit current file processing implementation for enterprise-scale hardening  
> Baseline: 218 tests passing (Step 0 complete)

---

## Executive Summary

### Current State: "<50MB prod-ready"

The existing file processing implementation:
- ✅ Correctly detects and routes CSV, Excel, fixed-width, PDF sources
- ✅ Infers schemas with confidence scoring
- ✅ Persists to Silver model with idempotent upserts
- ❌ **Loads entire file into memory** (no streaming)
- ❌ **No timeout enforcement** (can hang on slow I/O)
- ❌ **No cancellation support** (no graceful shutdown)
- ❌ **No file-size gating** (will OOM on large files)
- ❌ **No progress tracking** (no visibility into long operations)

### Goal: "Enterprise scale"

After hardening:
- Stream large files in configurable chunks (default 10MB)
- Hard timeout on all file operations (default 5 minutes)
- CancelToken propagation for graceful shutdown
- Preflight size checks with clear error messages
- Progress callbacks for long operations

---

## Component Inventory

### 1. Source Handlers

| Component | File | Entry Points | Bytes Read At | Streaming? | Size Check? | Timeout? | Test File |
|-----------|------|--------------|---------------|------------|-------------|----------|-----------|
| **CSVSource** | `sources/csv_source.py` | `detect()` L55, `parse()` L85 | `normalize_content()` L56, L86 | ❌ No | ❌ No | ❌ No | `test_csv_source.py` (21 tests) |
| **ExcelSource** | `sources/excel.py` | `detect()` L45, `parse()` L90 | `openpyxl.load_workbook()` L95 | ❌ No | ❌ No | ❌ No | `test_excel_adversarial.py` (24 tests) |
| **FixedWidthSource** | `sources/fixed_width.py` | `detect()` L49, `parse()` L95 | `normalize_content()` L70, L120 | ❌ No | ❌ No | ❌ No | `test_fixed_width_adversarial.py` (33 tests) |
| **PDFGuideSource** | `sources/pdf_guide.py` | `detect()` L55, `parse()` L100 | `normalize_content_bytes()` L65, L110 | ❌ No | ❌ No | ❌ No | `test_pdf_guide_adversarial.py` (35 tests) |
| **OpenAPISource** | `sources/openapi_source.py` | `detect()`, `parse()` | `normalize_content()` | ❌ No | ❌ No | ❌ No | `test_openapi_source.py` |

### 2. Core Parsers

| Parser | File | Called From | Content Load | Streaming? | Size Check? | Timeout? | Test File |
|--------|------|-------------|--------------|------------|-------------|----------|-----------|
| **infer_csv_schema** | `parsers/csv_schema.py` | CSVSource.parse() | `StringIO(content)` then full read | ❌ No | ❌ No | ❌ No | `test_csv_schema.py` (27 tests) |
| **excel_parser** | `parsers/excel_parser.py` | ExcelSource.parse() | `openpyxl.load_workbook(BytesIO)` | ❌ No | `MAX_SIZE_FOR_FORMULA_CHECK=10MB` partial | ❌ No | `test_excel_adversarial.py` |
| **fixed_width_parser** | `parsers/fixed_width_parser.py` | FixedWidthSource.parse() | Full string content | ❌ No | ❌ No | ❌ No | `test_fixed_width_adversarial.py` |
| **pdf_guide_parser** | `parsers/pdf_guide_parser.py` | PDFGuideSource.parse() | Full bytes content | ❌ No | ❌ No | ❌ No | `test_pdf_guide_adversarial.py` |

### 3. Routing Layer

| Component | File | Function | Blocking Points | Streaming? | Timeout? | Test File |
|-----------|------|----------|-----------------|------------|----------|-----------|
| **detect_and_route** | `sources/__init__.py` L55-145 | Routes content to source | Calls all `detect()` sequentially | ❌ No | ❌ No | `test_fixed_width_adversarial.py::TestIntegration` |
| **normalize_content** | `sources/base.py` L100-130 | Converts bytes→str | Loads full content | ❌ No | ❌ No | (inline tests) |
| **normalize_content_bytes** | `sources/base.py` L135-155 | Returns raw bytes | Loads full content | ❌ No | ❌ No | (inline tests) |

### 4. Graph Nodes

| Node | File | Input | Content Processing | Streaming? | Timeout? | Test File |
|------|------|-------|-------------------|------------|----------|-----------|
| **build_silver_file_model** | `graph/nodes/build_silver_file_model.py` | `state.parsed_specs` | Already parsed (no file I/O) | N/A | ❌ No | `test_file_integration_e2e.py` |

---

## Critical Code Paths

### Path 1: CSV Detection + Parsing

```
User provides CSV bytes
    ↓
detect_and_route(content, uri, content_type)  [sources/__init__.py L55]
    ↓
CSVSource.detect(content, uri, content_type)  [csv_source.py L40]
    ↓
normalize_content(content)  ← FULL MEMORY LOAD  [base.py L100]
    ↓
_looks_like_delimited(content_str)  [csv_source.py L150]
    ↓
Return confidence score
    ↓
CSVSource.parse(content, uri)  [csv_source.py L85]
    ↓
normalize_content(content)  ← SECOND FULL MEMORY LOAD  [base.py L100]
    ↓
infer_csv_schema(content_str)  [csv_schema.py L45]
    ↓
pandas.read_csv(StringIO(content))  ← THIRD FULL MEMORY LOAD  [csv_schema.py L80]
    ↓
Return ParsedSpec with FileSpec + FileField
```

**Memory Usage**: 3× file size minimum (original bytes + decoded string + pandas DataFrame)

### Path 2: Excel Detection + Parsing

```
User provides Excel bytes
    ↓
detect_and_route(content, uri, content_type)  [sources/__init__.py L55]
    ↓
ExcelSource.detect(content, uri, content_type)  [excel.py L45]
    ↓
_try_load_workbook(content_bytes)  ← FULL MEMORY LOAD via openpyxl  [excel_parser.py L90]
    ↓
Return confidence score
    ↓
ExcelSource.parse(content, uri)  [excel.py L90]
    ↓
infer_schema(content_bytes)  [excel_parser.py L155]
    ↓
openpyxl.load_workbook(BytesIO(content), read_only=True)  ← SECOND FULL MEMORY LOAD
    ↓
_detect_formula_cells(content_bytes)  ← THIRD FULL MEMORY LOAD (if <10MB)  [excel_parser.py L270]
    ↓
Return ParsedSpec with FileSpec + FileField
```

**Memory Usage**: 2-3× file size (openpyxl internal structures + formula detection pass)

### Path 3: Fixed-Width Detection + Parsing

```
User provides fixed-width bytes
    ↓
detect_and_route(content, uri, content_type)  [sources/__init__.py L55]
    ↓
FixedWidthSource.detect(content, uri, content_type)  [fixed_width.py L49]
    ↓
normalize_content(content)  ← FULL MEMORY LOAD  [base.py L100]
    ↓
detect_with_confidence(content_str)  [fixed_width_parser.py]
    ↓
Return confidence score
    ↓
FixedWidthSource.parse(content, uri)  [fixed_width.py L95]
    ↓
normalize_content(content)  ← SECOND FULL MEMORY LOAD  [base.py L100]
    ↓
infer_schema(content_str)  [fixed_width_parser.py]
    ↓
Return ParsedSpec with FileSpec + FileField + RecordLayout
```

**Memory Usage**: 2× file size (bytes + decoded string)

---

## Gap Analysis

### Gap 1: No Streaming

**Current**: All sources call `normalize_content()` which decodes full bytes to string in memory.

**Evidence**:
- `sources/base.py` L100-130: `normalize_content()` returns `str` (full content)
- `parsers/csv_schema.py` L80: `pandas.read_csv(StringIO(content))` (full content)
- `parsers/excel_parser.py` L155: `openpyxl.load_workbook(BytesIO(content_bytes))` (full content)

**Impact**: Files >100MB will likely OOM on typical dev machines (8GB RAM).

**Fix Required**: Add `StreamingContentReader` with chunked iteration for type inference from sample rows.

### Gap 2: No Timeout Enforcement

**Current**: No timeout on any file operation. A slow network read or corrupted file could hang forever.

**Evidence**:
- `sources/__init__.py` L55-145: `detect_and_route()` has no timeout parameter
- All `detect()` and `parse()` methods have no timeout parameter
- No use of `asyncio.timeout()`, `signal.alarm()`, or `multiprocessing.Timeout`

**Impact**: Production workflows could stall indefinitely on slow/hung operations.

**Fix Required**: Add `TimeoutContext` wrapper with configurable hard timeout.

### Gap 3: No Cancellation Support

**Current**: No way to cancel a long-running parse operation. No cooperative cancellation checks.

**Evidence**:
- No `CancelToken` or equivalent pattern in sources/parsers
- No `check_cancelled()` calls in any parse loop
- No integration with asyncio cancellation

**Impact**: Cannot gracefully shut down during long operations. Must kill process.

**Fix Required**: Add `CancelToken` class with cooperative checking in parse loops.

### Gap 4: No File-Size Gating

**Current**: No preflight check on file size before attempting to load into memory.

**Evidence**:
- `detect_and_route()` accepts `content: ContentType` (already loaded)
- No `max_file_size` configuration
- One exception: `excel_parser.py` L270: `MAX_SIZE_FOR_FORMULA_CHECK = 10MB` (partial)

**Impact**: Large files will be loaded into memory before failing, wasting resources.

**Fix Required**: Add preflight `check_file_size()` with configurable limits and clear error messages.

### Gap 5: No Progress Tracking

**Current**: No progress callbacks or indicators during long operations.

**Evidence**:
- No `progress_callback` parameter on any source/parser
- No `yield` statements for incremental progress
- No integration with tqdm or similar

**Impact**: No visibility into long-running operations. Users don't know if stuck or progressing.

**Fix Required**: Add optional `ProgressCallback` protocol and wire through parse chain.

---

## Configuration Needed

Based on gap analysis, the following configuration items are needed:

```python
@dataclass
class FileProcessingConfig:
    """Configuration for enterprise-scale file processing."""
    
    # Streaming
    chunk_size_bytes: int = 10 * 1024 * 1024  # 10MB default
    sample_rows_for_inference: int = 1000     # Rows to sample for type inference
    
    # Timeouts
    detect_timeout_seconds: float = 30.0      # Max time for detection
    parse_timeout_seconds: float = 300.0      # Max time for parsing (5 min)
    
    # Size gating
    max_file_size_bytes: int = 500 * 1024 * 1024  # 500MB default
    warn_file_size_bytes: int = 50 * 1024 * 1024  # Warn at 50MB
    
    # Cancellation
    cancel_check_interval_rows: int = 100     # Check cancel token every N rows
    
    # Progress
    progress_report_interval_rows: int = 1000  # Report progress every N rows
```

---

## Test Coverage Summary

| Category | Tests | Coverage |
|----------|-------|----------|
| CSV source + parser | 48 | Good for happy path; no streaming/timeout/cancel tests |
| Excel source + parser | 24 | Good for happy path; no streaming/timeout/cancel tests |
| Fixed-width source + parser | 33 | Good for happy path; no streaming/timeout/cancel tests |
| PDF guide source + parser | 35 | Good for happy path; no streaming/timeout/cancel tests |
| File templates + validation | 76 | Codegen tests; not affected by hardening |
| **Total** | 218 | Baseline for hardening work |

### Tests Needed for Hardening

1. **Streaming tests**: Large file simulation with memory monitoring
2. **Timeout tests**: Slow reader simulation with timeout enforcement
3. **Cancellation tests**: Mid-parse cancellation with cleanup verification
4. **Size gating tests**: Oversized file rejection with clear errors
5. **Progress tests**: Progress callback invocation verification

---

## Next Steps

This inventory completes **Step 1** of the hardening plan. Next steps:

1. **Step 2**: Design decision on streaming approach (Python streaming vs Pandas chunked vs PyArrow/Polars)
2. **Step 3**: Design decision on timeout approach (in-process vs process-based vs cooperative)
3. **Step 4**: Design decision on cancellation pattern (CancelToken design)
4. **Steps 5-8**: Implementation
5. **Step 9**: Production validation tests
