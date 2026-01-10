# File Integration V1 Implementation Plan

**Author:** AI Code Agent  
**Date:** December 14, 2025  
**Status:** IN PROGRESS  
**Original Requirement:** "auto-discover source system data processing requirements based on... a data transmission guide for data file processing"

---

## Table of Contents

1. [Current Implementation Map](#1-current-implementation-map)
2. [Gap Analysis](#2-gap-analysis)
3. [Architecture Decision](#3-architecture-decision)
4. [Target Architecture](#4-target-architecture)
5. [Data Model & DB Schema](#5-data-model-db-schema)
6. [File-by-File Refactor Plan](#6-file-by-file-refactor-plan)
7. [New Modules to Create](#7-new-modules-to-create)
8. [KG & Codegen Changes](#8-kg-codegen-changes)
9. [Test Strategy](#9-test-strategy)
10. [Rollout Plan](#10-rollout-plan)
11. [Implementation Buckets](#11-implementation-buckets)

---

## 1. Current Implementation Map

### 1.1 Existing Parser Infrastructure

The system already has **partial file parsing support** that is disconnected from the main workflow:

| File | Purpose | Integration Status |
|------|---------|-------------------|
| `parsers/csv_schema.py` | CSV/TSV schema inference | ✅ Implemented, ❌ NOT integrated into workflow |
| `parsers/pdf_parser.py` | PDF text extraction → pseudo-OpenAPI | ✅ Implemented, 🟡 Integrated only for API endpoints |
| `parsers/html_parser.py` | HTML doc parsing → pseudo-OpenAPI | ✅ Implemented, 🟡 Integrated only for API endpoints |
| `parsers/text_spec_heuristics.py` | Regex-based endpoint/field detection | ✅ Implemented, 🟡 API-focused only |
| `parsers/message_schema.py` | Message/event schema parsing | ✅ Exists, ❌ NOT integrated |

### 1.2 Current Ingestion Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CURRENT PIPELINE (API-only)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  spec_refs ──► ingest_spec ──► detect_and_parse_spec ──► build_silver_api   │
│     │              │                    │                       │           │
│     │              │                    │                       ▼           │
│     │              ▼                    ▼                 Silver API Model  │
│     │        SpecDocument          OpenAPI dict            (endpoints,      │
│     │        (raw content)      (+ pseudo-OpenAPI           schemas,        │
│     │                            from HTML/PDF)             entities)       │
│                                                                             │
│  Limitation: PDF/HTML parsing extracts ENDPOINTS, not file field schemas   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.3 Existing DB Schema (Already Defined but Unused)

From `persistence/postgres.py` lines 287-302:

```sql
-- file_specs: CSV/EDI file metadata (for non-HTTP specs)
CREATE TABLE IF NOT EXISTS spec_silver.file_specs (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id),
    name             TEXT NOT NULL,
    file_type        TEXT NOT NULL,
    delimiter        TEXT,
    encoding         TEXT DEFAULT 'utf-8',
    header_row       BOOLEAN DEFAULT TRUE,
    schema_id        BIGINT REFERENCES spec_silver.schemas(id),
    description      TEXT,
    UNIQUE (source_system_id, name)
);
```

**Finding:** The table exists but is **never populated** by any workflow node.

### 1.4 Touch Points Analysis

Modules that will be affected by adding file integration:

| Module | Current Role | Required Changes |
|--------|--------------|------------------|
| `graph/nodes/detect_and_parse_spec.py` | Format detection, pseudo-OpenAPI | Add file format detection branch |
| `graph/nodes/build_silver_api_model.py` | OpenAPI → Silver entities | Add Silver File Model construction |
| `graph/state.py` | Workflow state dataclass | Add file-related state fields |
| `domain/models.py` | Domain models | Add FileSpec, FileField, RecordLayout |
| `persistence/postgres.py` | DDL definitions | Extend file_specs, add file_fields |
| `persistence/db.py` | SQLite equivalents | Mirror Postgres changes |
| `codegen/prompts.py` | Code generation prompts | Add file parser/validator prompts |
| `parsers/csv_schema.py` | CSV inference | Integrate into workflow |
| `kg/pattern_discovery.py` | Pattern learning | Add file pattern types |

---

## 2. Gap Analysis

### 2.1 Original Requirement Text

> "Build an AI Agentic Co-Worker team to auto-discover source system data processing requirements based on the source company system's written spec (such as a **public API spec** for integration purposes or a **data transmission guide for data file processing**)."

### 2.2 Gap Mapping

| Requirement Component | Current State | Gap |
|----------------------|---------------|-----|
| CSV/delimited file schema | `csv_schema.py` exists but unused | Parser not integrated into workflow |
| Fixed-width file parsing | Not implemented | Need colspec inference from guides |
| Excel schema extraction | Not implemented | Need openpyxl/pandas integration |
| PDF data guide extraction | PDF parser extracts endpoints only | Need field definition extraction |
| Word doc data guides | Not implemented | Need python-docx integration |
| EDI/X12 parsing | Not implemented | Need segment/element parsing |
| Silver File Model | Tables exist but unused | Need domain models + persistence |
| File-specific codegen | Not implemented | Need parser/validator templates |
| Unified KG for files | No file nodes/edges | Need FileSpec, FileField node types |

### 2.3 Root Cause

The `detect_and_parse_spec.py` node converts **all inputs to pseudo-OpenAPI format**, even PDFs. This forces file specifications through an API-shaped hole, losing file-specific metadata (positions, widths, delimiters, record layouts).

---

## 3. Architecture Decision

### 3.1 Option A: Extend Existing Pipeline

**Approach:** Add file parsers as siblings to OpenAPI in `detect_and_parse_spec.py`, output pseudo-OpenAPI for files too.

```python
# In detect_and_parse_spec.py
if _is_csv_content(content):
    return _parse_csv_to_pseudo_openapi(content, uri)
elif _is_fixed_width_content(content):
    return _parse_fixed_width_to_pseudo_openapi(content, uri)
```

**Pros:**
- Minimal code changes
- Reuses existing silver model flow
- Fast to implement (3-5 days)

**Cons:**
- Forces file metadata into API-shaped model (lossy)
- Cannot represent fixed-width positions, record layouts
- Future EDI parsing becomes awkward
- Technical debt accumulates

**Verdict:** ❌ Rejected — violates "design for extensibility" principle

---

### 3.2 Option B: SpecSource Plugin System

**Approach:** Create a unified `SpecSource` interface that all parsers implement.

```python
class SpecSource(Protocol):
    """Universal interface for all spec sources."""
    
    @staticmethod
    def detect(content: bytes, uri: str, content_type: str) -> float:
        """Return confidence 0-1 that this source handles the content."""
        ...
    
    def parse(self, content: bytes, uri: str) -> ParsedSpec:
        """Parse content into intermediate ParsedSpec."""
        ...
    
    def to_silver(self, parsed: ParsedSpec) -> SilverModel:
        """Convert to appropriate Silver model (API or File)."""
        ...
```

**Plugin implementations:**
- `OpenAPISource` — existing OpenAPI/Swagger
- `CSVSource` — CSV/TSV with schema inference
- `FixedWidthSource` — fixed-width with colspec
- `ExcelSource` — XLSX sheets
- `PDFGuideSource` — PDF data transmission guides
- `EDISource` — X12/EDIFACT (future)

**Pros:**
- Clean separation of concerns
- Each format has native representation
- Easy to add new formats
- Testable in isolation

**Cons:**
- Significant refactor of detection flow
- Need to handle mixed outputs (API + File in same run)
- Estimated 7-10 days

**Verdict:** ⭐ **SELECTED** — Best long-term architecture

---

### 3.3 Option C: Greenfield V2 Pipeline

**Approach:** Create entirely new pipeline package, migrate later.

```
src/
  integration_coworker/        # V1 (API-only, stable)
  integration_coworker_v2/     # V2 (unified API + File)
```

**Pros:**
- Zero risk to existing API pipeline
- Clean slate for design
- Can deprecate V1 after V2 stable

**Cons:**
- Code duplication
- Maintenance burden during transition
- Confusing for users
- Estimated 15-20 days

**Verdict:** ❌ Rejected — unnecessary given Option B's refactor is manageable

---

### 3.4 Final Decision

**Selected: Option B — SpecSource Plugin System**

**Rationale:**
1. The existing `_parse_*_to_pseudo_openapi` functions in `detect_and_parse_spec.py` already show a pattern begging for abstraction
2. The `file_specs` table already exists — just needs population
3. Sunk cost of existing API pipeline is preserved (it becomes `OpenAPISource`)
4. EDI/X12 support (Bucket 2) becomes straightforward with this design

---

## 4. Target Architecture

### 4.1 End-to-End Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TARGET PIPELINE (Unified)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  spec_refs ──► ingest_spec ──► detect_source_type ──► route by type         │
│                                       │                                     │
│                    ┌──────────────────┼──────────────────┐                  │
│                    ▼                  ▼                  ▼                  │
│              OpenAPISource      CSVSource         PDFGuideSource            │
│                    │                  │                  │                  │
│                    ▼                  ▼                  ▼                  │
│              SilverAPIModel    SilverFileModel    SilverFileModel           │
│              (endpoints,       (file_spec,        (file_fields,             │
│               schemas)          file_fields)       record_layout)           │
│                    │                  │                  │                  │
│                    └──────────────────┴──────────────────┘                  │
│                                       │                                     │
│                                       ▼                                     │
│                              Unified KG Population                          │
│                              (API + File nodes)                             │
│                                       │                                     │
│                                       ▼                                     │
│                              Unified Codegen                                │
│                         (API clients + File parsers)                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Module Diagram

```
src/integration_coworker/
├── sources/                          # NEW: SpecSource plugins
│   ├── __init__.py                   # Registry + detection
│   ├── base.py                       # Protocol definitions
│   ├── openapi.py                    # OpenAPISource (refactored from detect_and_parse_spec)
│   ├── csv_source.py                 # CSVSource (wraps parsers/csv_schema.py)
│   ├── fixed_width.py                # FixedWidthSource
│   ├── excel.py                      # ExcelSource
│   ├── pdf_guide.py                  # PDFGuideSource (file-focused, not endpoint-focused)
│   └── edi.py                        # EDISource (Bucket 2)
│
├── parsers/                          # Existing, minimal changes
│   ├── csv_schema.py                 # Already good
│   ├── pdf_parser.py                 # Split: endpoint vs field extraction
│   └── fixed_width_parser.py         # NEW
│
├── domain/models.py                  # Add FileSpec, FileField, RecordLayout
│
├── graph/nodes/
│   ├── detect_and_parse_spec.py      # Refactor to use sources/
│   └── build_silver_file_model.py    # NEW: parallel to build_silver_api_model.py
│
└── codegen/
    ├── file_templates.py             # NEW: file parser/validator templates
    └── prompts.py                    # Add file-specific prompt builders
```

---

## 5. Data Model & DB Schema

### 5.1 Domain Models to Add

```python
# In domain/models.py

@dataclass
class FileSpec:
    """A file-based data specification (CSV, fixed-width, Excel, etc.)."""
    id: Optional[int]
    source_system_id: Optional[int]
    spec_document_id: Optional[int]
    name: str                           # e.g., "daily_transactions"
    file_type: str                      # csv, tsv, fixed_width, xlsx, edi
    encoding: str = "utf-8"
    delimiter: Optional[str] = None     # For delimited files
    has_header: bool = True
    line_terminator: str = "\n"
    quote_char: Optional[str] = '"'
    escape_char: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None       # Guide version
    
@dataclass  
class FileField:
    """A field within a file specification."""
    id: Optional[int]
    file_spec_id: Optional[int]
    name: str
    field_type: str                     # string, integer, decimal, date, datetime, boolean
    position: int                       # 0-indexed column position (for delimited)
    start_position: Optional[int] = None  # For fixed-width: start byte
    length: Optional[int] = None        # For fixed-width: field length
    format_mask: Optional[str] = None   # e.g., "YYYYMMDD", "###.##"
    nullable: bool = True
    default_value: Optional[str] = None
    validation_regex: Optional[str] = None
    description: Optional[str] = None
    sample_values: Optional[list] = None  # For inference confidence
    inference_confidence: float = 1.0   # How confident are we in this inference

@dataclass
class RecordLayout:
    """Record layout for fixed-width or multi-record files."""
    id: Optional[int]
    file_spec_id: Optional[int]
    record_type: str                    # e.g., "header", "detail", "trailer"
    identifier_field: Optional[str] = None  # Field that identifies record type
    identifier_value: Optional[str] = None  # Value that identifies this type
    record_length: Optional[int] = None
    fields: List[FileField] = field(default_factory=list)

@dataclass
class FileValidationRule:
    """Validation rule for file data."""
    id: Optional[int]
    file_spec_id: Optional[int]
    field_name: Optional[str] = None    # None means file-level rule
    rule_type: str                      # required, range, regex, lookup, cross_field
    rule_config: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
```

### 5.2 Postgres DDL Additions

```sql
-- Extend file_specs table (already exists, add columns)
ALTER TABLE spec_silver.file_specs ADD COLUMN IF NOT EXISTS 
    spec_document_id BIGINT REFERENCES spec_silver.spec_documents(id);
ALTER TABLE spec_silver.file_specs ADD COLUMN IF NOT EXISTS 
    line_terminator TEXT DEFAULT E'\n';
ALTER TABLE spec_silver.file_specs ADD COLUMN IF NOT EXISTS 
    quote_char TEXT DEFAULT '"';
ALTER TABLE spec_silver.file_specs ADD COLUMN IF NOT EXISTS 
    escape_char TEXT;
ALTER TABLE spec_silver.file_specs ADD COLUMN IF NOT EXISTS 
    version TEXT;

-- NEW: file_fields table
CREATE TABLE IF NOT EXISTS spec_silver.file_fields (
    id                    BIGSERIAL PRIMARY KEY,
    file_spec_id          BIGINT NOT NULL REFERENCES spec_silver.file_specs(id) ON DELETE CASCADE,
    name                  TEXT NOT NULL,
    field_type            TEXT NOT NULL,
    position              INT NOT NULL,
    start_position        INT,           -- For fixed-width
    length                INT,           -- For fixed-width
    format_mask           TEXT,
    nullable              BOOLEAN DEFAULT TRUE,
    default_value         TEXT,
    validation_regex      TEXT,
    description           TEXT,
    sample_values         JSONB DEFAULT '[]',
    inference_confidence  DOUBLE PRECISION DEFAULT 1.0,
    UNIQUE (file_spec_id, name)
);

-- NEW: record_layouts table (for multi-record fixed-width files)
CREATE TABLE IF NOT EXISTS spec_silver.record_layouts (
    id                BIGSERIAL PRIMARY KEY,
    file_spec_id      BIGINT NOT NULL REFERENCES spec_silver.file_specs(id) ON DELETE CASCADE,
    record_type       TEXT NOT NULL,
    identifier_field  TEXT,
    identifier_value  TEXT,
    record_length     INT,
    UNIQUE (file_spec_id, record_type)
);

-- NEW: file_validation_rules table
CREATE TABLE IF NOT EXISTS spec_silver.file_validation_rules (
    id            BIGSERIAL PRIMARY KEY,
    file_spec_id  BIGINT NOT NULL REFERENCES spec_silver.file_specs(id) ON DELETE CASCADE,
    field_name    TEXT,              -- NULL for file-level rules
    rule_type     TEXT NOT NULL,     -- required, range, regex, lookup, cross_field
    rule_config   JSONB NOT NULL,
    error_message TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_file_fields_spec ON spec_silver.file_fields(file_spec_id);
CREATE INDEX IF NOT EXISTS idx_record_layouts_spec ON spec_silver.record_layouts(file_spec_id);
CREATE INDEX IF NOT EXISTS idx_file_validation_rules_spec ON spec_silver.file_validation_rules(file_spec_id);
```

### 5.3 SQLite DDL Additions (db.py)

```sql
-- file_fields table
CREATE TABLE IF NOT EXISTS file_fields (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    file_spec_id          INTEGER NOT NULL REFERENCES file_specs(id) ON DELETE CASCADE,
    name                  TEXT NOT NULL,
    field_type            TEXT NOT NULL,
    position              INTEGER NOT NULL,
    start_position        INTEGER,
    length                INTEGER,
    format_mask           TEXT,
    nullable              INTEGER DEFAULT 1,
    default_value         TEXT,
    validation_regex      TEXT,
    description           TEXT,
    sample_values         TEXT DEFAULT '[]',
    inference_confidence  REAL DEFAULT 1.0,
    UNIQUE (file_spec_id, name)
);

-- record_layouts table
CREATE TABLE IF NOT EXISTS record_layouts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    file_spec_id      INTEGER NOT NULL REFERENCES file_specs(id) ON DELETE CASCADE,
    record_type       TEXT NOT NULL,
    identifier_field  TEXT,
    identifier_value  TEXT,
    record_length     INTEGER,
    UNIQUE (file_spec_id, record_type)
);

-- file_validation_rules table
CREATE TABLE IF NOT EXISTS file_validation_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_spec_id  INTEGER NOT NULL REFERENCES file_specs(id) ON DELETE CASCADE,
    field_name    TEXT,
    rule_type     TEXT NOT NULL,
    rule_config   TEXT NOT NULL,
    error_message TEXT
);
```

---

## 6. File-by-File Refactor Plan

### 6.1 `graph/nodes/detect_and_parse_spec.py`

**Why:** Currently hardcoded to OpenAPI/pseudo-OpenAPI output. Needs to route to appropriate source handler.

**Current state:** 500+ lines with `_parse_*_to_pseudo_openapi()` functions.

**Refactor Options:**

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| A: Inline addition | Add more `if _is_csv...` branches | Fast | More spaghetti |
| B: Extract to sources/ | Move detection to registry | Clean, testable | Migration effort |
| C: Complete rewrite | New module, deprecate old | Cleanest | Risk |

**Selected: Option B** — Extract detection logic to `sources/` package, keep this node as thin orchestrator.

**Changes:**
```python
# Before (current)
def detect_and_parse_spec(state: WorkflowState) -> WorkflowState:
    ...
    if _is_html_content(content, content_type):
        result = _parse_html_to_pseudo_openapi(content, uri)
    elif _is_pdf_content(content, content_type):
        result = _parse_pdf_to_pseudo_openapi(content, uri)
    ...

# After (refactored)
def detect_and_parse_spec(state: WorkflowState) -> WorkflowState:
    from integration_coworker.sources import detect_and_route
    
    for spec_doc in state.spec_documents:
        parsed = detect_and_route(spec_doc.content, spec_doc.uri, spec_doc.content_type)
        
        if parsed.source_type == "api":
            state.openapi_spec = parsed.data  # existing flow
        elif parsed.source_type == "file":
            state.file_specs.append(parsed.data)  # new flow
```

**Downstream impacts:**
- `build_silver_api_model.py` — unchanged (handles openapi_spec)
- `build_silver_file_model.py` — NEW (handles file_specs)

---

### 6.2 `graph/state.py`

**Why:** Need to add file-related state fields.

**Changes:**
```python
# Add to WorkflowState dataclass:

# File specs (parallel to API specs)
file_specs: List[FileSpec] = field(default_factory=list)
file_fields: List[FileField] = field(default_factory=list)
record_layouts: List[RecordLayout] = field(default_factory=list)
file_validation_rules: List[FileValidationRule] = field(default_factory=list)
```

**Downstream impacts:**
- Serialization in checkpoints — need to handle new types
- State validation — update any state validators

---

### 6.3 `domain/models.py`

**Why:** Add Silver File Model domain classes.

**Changes:** Add `FileSpec`, `FileField`, `RecordLayout`, `FileValidationRule` dataclasses (see Section 5.1).

**Downstream impacts:**
- Imports in graph nodes
- Persistence layer mappings

---

### 6.4 `persistence/postgres.py`

**Why:** Add DDL for new tables, extend existing file_specs.

**Changes:** Add DDL from Section 5.2 to `SPEC_SILVER_DDL`.

**Downstream impacts:**
- Migration for existing DBs
- Need migration script or idempotent ALTER statements

---

### 6.5 `persistence/db.py`

**Why:** SQLite equivalent of Postgres changes.

**Changes:** Add DDL from Section 5.3 to `SQLITE_DDL`.

**Downstream impacts:** Same as Postgres.

---

### 6.6 `parsers/csv_schema.py`

**Why:** Already implemented but needs integration hooks.

**Current state:** Good — returns `CsvSchema` dataclass.

**Changes:**
- Add `to_silver()` method or adapter
- Add integration with `sources/csv_source.py`

```python
# Add adapter function
def csv_schema_to_silver(csv_schema: CsvSchema, name: str, source_system_id: int) -> Tuple[FileSpec, List[FileField]]:
    """Convert CsvSchema to Silver domain models."""
    file_spec = FileSpec(
        id=None,
        source_system_id=source_system_id,
        name=name,
        file_type="csv",
        delimiter=csv_schema.delimiter,
        has_header=csv_schema.has_header,
    )
    
    fields = [
        FileField(
            id=None,
            file_spec_id=None,
            name=f.name,
            field_type=f.inferred_type,
            position=i,
            nullable=f.nullable,
            sample_values=f.sample_values,
        )
        for i, f in enumerate(csv_schema.fields)
    ]
    
    return file_spec, fields
```

---

### 6.7 `codegen/prompts.py`

**Why:** Need prompts for file parser/validator code generation.

**Changes:**
- Add `LANGUAGE_CONVENTIONS` entries for file operations
- Add `build_file_parser_prompt()` function
- Add `build_file_validator_prompt()` function

---

## 7. New Modules to Create

### 7.1 `sources/__init__.py`

```python
"""
SpecSource plugin registry and detection.

Implements the SpecSource plugin pattern for unified handling of
API specs, file specs, and document guides.
"""
from typing import List, Optional, Tuple
from .base import SpecSource, ParsedSpec, SourceType
from .openapi import OpenAPISource
from .csv_source import CSVSource
from .fixed_width import FixedWidthSource
from .excel import ExcelSource
from .pdf_guide import PDFGuideSource

# Registry of all source handlers in detection priority order
SOURCE_REGISTRY: List[SpecSource] = [
    OpenAPISource(),
    CSVSource(),
    ExcelSource(),
    FixedWidthSource(),
    PDFGuideSource(),  # Last because it's the most permissive
]

def detect_and_route(content: bytes | str, uri: str, content_type: str) -> ParsedSpec:
    """
    Detect content type and route to appropriate source handler.
    
    Returns ParsedSpec with source_type indicating 'api' or 'file'.
    """
    # Score each source
    scores: List[Tuple[float, SpecSource]] = []
    for source in SOURCE_REGISTRY:
        score = source.detect(content, uri, content_type)
        if score > 0:
            scores.append((score, source))
    
    if not scores:
        raise ValueError(f"No source handler matched content from {uri}")
    
    # Use highest-scoring source
    scores.sort(key=lambda x: x[0], reverse=True)
    _, best_source = scores[0]
    
    return best_source.parse(content, uri)
```

### 7.2 `sources/base.py`

```python
"""
Base classes and protocols for SpecSource plugins.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Union

class SourceType(str, Enum):
    API = "api"
    FILE = "file"

@dataclass
class ParsedSpec:
    """Result of parsing a spec source."""
    source_type: SourceType
    source_uri: str
    data: Any  # OpenAPI dict for API, FileSpec for FILE
    metadata: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    confidence: float = 1.0

class SpecSource(Protocol):
    """Protocol for spec source handlers."""
    
    def detect(self, content: Union[bytes, str], uri: str, content_type: str) -> float:
        """
        Return confidence 0-1 that this source can handle the content.
        
        Higher confidence = better match.
        0 = cannot handle.
        """
        ...
    
    def parse(self, content: Union[bytes, str], uri: str) -> ParsedSpec:
        """
        Parse content into a ParsedSpec.
        
        For API sources: returns OpenAPI/Swagger dict
        For File sources: returns FileSpec + FileFields
        """
        ...
```

### 7.3 `sources/csv_source.py`

```python
"""
CSV/TSV source handler.
"""
from typing import Union
from .base import SpecSource, ParsedSpec, SourceType
from integration_coworker.parsers.csv_schema import infer_csv_schema, csv_schema_to_silver

class CSVSource:
    """Handler for CSV/TSV files."""
    
    def detect(self, content: Union[bytes, str], uri: str, content_type: str) -> float:
        """Detect if content is CSV/TSV."""
        ct = content_type.lower()
        uri_lower = uri.lower()
        
        # High confidence from extension/content-type
        if ".csv" in uri_lower or "text/csv" in ct:
            return 0.95
        if ".tsv" in uri_lower or "tab-separated" in ct:
            return 0.95
        
        # Medium confidence from content heuristics
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='ignore')
        
        lines = content.split('\n')[:10]
        if len(lines) >= 2:
            # Check for consistent delimiter
            for delim in [',', '\t', '|', ';']:
                counts = [line.count(delim) for line in lines if line.strip()]
                if len(set(counts)) == 1 and counts[0] >= 2:
                    return 0.7
        
        return 0.0
    
    def parse(self, content: Union[bytes, str], uri: str) -> ParsedSpec:
        """Parse CSV content."""
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='ignore')
        
        csv_schema = infer_csv_schema(content)
        
        if csv_schema.errors:
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data=None,
                errors=csv_schema.errors,
            )
        
        # Convert to Silver model
        file_spec, fields = csv_schema_to_silver(
            csv_schema,
            name=_extract_name_from_uri(uri),
            source_system_id=None,  # Will be set during persistence
        )
        
        return ParsedSpec(
            source_type=SourceType.FILE,
            source_uri=uri,
            data={"file_spec": file_spec, "fields": fields},
            metadata={"row_count": csv_schema.row_count},
        )

def _extract_name_from_uri(uri: str) -> str:
    """Extract file name from URI."""
    from pathlib import Path
    return Path(uri).stem
```

### 7.4 `sources/pdf_guide.py`

```python
"""
PDF data transmission guide source handler.

Extracts FIELD DEFINITIONS from PDFs, not API endpoints.
Uses LLM for structured extraction when heuristics fail.
"""
from typing import Union
from .base import SpecSource, ParsedSpec, SourceType
from integration_coworker.parsers.pdf_parser import extract_pdf_text

class PDFGuideSource:
    """Handler for PDF data transmission guides."""
    
    def detect(self, content: Union[bytes, str], uri: str, content_type: str) -> float:
        """Detect if content is a PDF data guide."""
        ct = content_type.lower()
        uri_lower = uri.lower()
        
        if "pdf" in ct or ".pdf" in uri_lower:
            # Check for data guide indicators in filename
            guide_keywords = ["guide", "spec", "format", "layout", "transmission", "file"]
            if any(kw in uri_lower for kw in guide_keywords):
                return 0.85
            return 0.5  # PDF but unknown purpose
        
        return 0.0
    
    def parse(self, content: Union[bytes, str], uri: str) -> ParsedSpec:
        """Parse PDF to extract field definitions."""
        if isinstance(content, str):
            content = content.encode('latin-1')
        
        # Extract text
        import io
        full_text, page_texts, metadata = extract_pdf_text(io.BytesIO(content))
        
        # Try heuristic extraction first
        fields = self._extract_fields_heuristic(full_text)
        
        if not fields:
            # Fall back to LLM extraction
            fields = self._extract_fields_llm(full_text, uri)
        
        if not fields:
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data=None,
                errors=["Could not extract field definitions from PDF"],
            )
        
        # Build Silver model
        file_spec = FileSpec(
            id=None,
            source_system_id=None,
            name=_extract_name_from_uri(uri),
            file_type=self._infer_file_type(full_text),
            description=f"Extracted from {uri}",
        )
        
        return ParsedSpec(
            source_type=SourceType.FILE,
            source_uri=uri,
            data={"file_spec": file_spec, "fields": fields},
            metadata={"page_count": metadata.get("num_pages", 0)},
        )
    
    def _extract_fields_heuristic(self, text: str) -> list:
        """Try to extract fields using regex patterns."""
        import re
        fields = []
        
        # Pattern: "Field Name" followed by type/length/description
        # Common patterns in data guides:
        # - "customer_id    INTEGER(10)    Customer identifier"
        # - "| amount | DECIMAL(10,2) | Transaction amount |"
        
        table_pattern = re.compile(
            r'([A-Za-z_][A-Za-z0-9_]*)\s+'
            r'(VARCHAR|CHAR|INTEGER|INT|DECIMAL|NUMERIC|DATE|DATETIME|TIMESTAMP|BOOLEAN|TEXT)'
            r'(?:\s*\(\s*(\d+)(?:\s*,\s*(\d+))?\s*\))?'
            r'(?:\s+(.{0,100}))?',
            re.IGNORECASE
        )
        
        for match in table_pattern.finditer(text):
            field = FileField(
                id=None,
                file_spec_id=None,
                name=match.group(1).lower(),
                field_type=self._normalize_type(match.group(2)),
                position=len(fields),
                length=int(match.group(3)) if match.group(3) else None,
                description=match.group(5).strip() if match.group(5) else None,
            )
            fields.append(field)
        
        return fields
    
    def _extract_fields_llm(self, text: str, uri: str) -> list:
        """Use LLM to extract field definitions."""
        try:
            from integration_coworker.llm import call_llm_for_node
        except ImportError:
            return []
        
        prompt = f"""Extract field definitions from this data transmission guide.

Return a JSON array where each field has:
- name: field name (lowercase, snake_case)
- type: one of [string, integer, decimal, date, datetime, boolean]
- length: max length if specified
- position: 0-indexed position  
- nullable: true/false
- description: field description

<document>
{text[:10000]}
</document>

Output ONLY valid JSON array. No markdown."""

        try:
            response = call_llm_for_node("pdf_guide_extraction", prompt)
            import json
            field_data = json.loads(response.strip())
            
            return [
                FileField(
                    id=None,
                    file_spec_id=None,
                    name=f.get("name", f"field_{i}"),
                    field_type=f.get("type", "string"),
                    position=f.get("position", i),
                    length=f.get("length"),
                    nullable=f.get("nullable", True),
                    description=f.get("description"),
                )
                for i, f in enumerate(field_data)
            ]
        except Exception:
            return []
    
    def _normalize_type(self, sql_type: str) -> str:
        """Normalize SQL type to our type system."""
        t = sql_type.upper()
        if t in ("VARCHAR", "CHAR", "TEXT"):
            return "string"
        if t in ("INTEGER", "INT", "BIGINT", "SMALLINT"):
            return "integer"
        if t in ("DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL"):
            return "decimal"
        if t == "DATE":
            return "date"
        if t in ("DATETIME", "TIMESTAMP"):
            return "datetime"
        if t in ("BOOLEAN", "BOOL"):
            return "boolean"
        return "string"
    
    def _infer_file_type(self, text: str) -> str:
        """Infer the file type from guide content."""
        text_lower = text.lower()
        if "fixed width" in text_lower or "fixed-width" in text_lower:
            return "fixed_width"
        if "tab-delimited" in text_lower or "tab delimited" in text_lower:
            return "tsv"
        if "comma-separated" in text_lower or "csv" in text_lower:
            return "csv"
        if "pipe-delimited" in text_lower or "pipe delimited" in text_lower:
            return "pipe_delimited"
        return "csv"  # Default assumption
```

### 7.5 `graph/nodes/build_silver_file_model.py`

```python
"""
build_silver_file_model node — constructs Silver File Model from parsed file specs.

Parallel to build_silver_api_model.py but for file-based integrations.
"""
import logging
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import FileSpec, FileField

logger = logging.getLogger(__name__)

def build_silver_file_model(state: WorkflowState) -> WorkflowState:
    """
    Build Silver File Model from parsed file specs.
    
    Reads: state.file_specs (raw parsed from sources/)
    Writes: state.file_specs, state.file_fields (populated with DB IDs)
    """
    if not state.file_specs:
        logger.debug("No file specs to process")
        state.completed_steps.append("build_silver_file_model")
        return state
    
    # Ensure source_system exists
    if not state.source_system or not state.source_system.id:
        state.errors.append("No source_system available for file spec persistence")
        state.completed_steps.append("build_silver_file_model")
        return state
    
    source_system_id = state.source_system.id
    
    # Process each file spec
    for file_spec in state.file_specs:
        file_spec.source_system_id = source_system_id
        
        # Extract fields if embedded in file_spec data
        if isinstance(file_spec, dict):
            # Handle ParsedSpec.data format
            spec_data = file_spec.get("file_spec")
            fields_data = file_spec.get("fields", [])
            
            if spec_data:
                spec_data.source_system_id = source_system_id
                state.file_specs.append(spec_data)
                state.file_fields.extend(fields_data)
    
    state.completed_steps.append("build_silver_file_model")
    return state
```

### 7.6 `codegen/file_templates.py`

```python
"""
Code generation templates for file parsers and validators.
"""
from typing import Dict, Any, List
from integration_coworker.domain.models import FileSpec, FileField

PYTHON_CSV_PARSER_TEMPLATE = '''
"""
Auto-generated CSV parser for {file_name}.

Generated by Integration Co-Worker.
"""
import csv
from dataclasses import dataclass
from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal


@dataclass
class {class_name}:
    """Record from {file_name}."""
{field_definitions}


def parse_{function_name}(file_path: str) -> List[{class_name}]:
    """
    Parse {file_name} CSV file.
    
    Args:
        file_path: Path to the CSV file
        
    Returns:
        List of parsed records
    """
    records = []
    
    with open(file_path, 'r', encoding='{encoding}') as f:
        reader = csv.DictReader(f, delimiter='{delimiter}')
        
        for row_num, row in enumerate(reader, start=2):
            try:
                record = {class_name}(
{field_parsing}
                )
                records.append(record)
            except (ValueError, KeyError) as e:
                raise ValueError(f"Error parsing row {{row_num}}: {{e}}")
    
    return records


def validate_{function_name}(records: List[{class_name}]) -> List[str]:
    """
    Validate parsed records.
    
    Returns list of validation errors (empty if valid).
    """
    errors = []
    
    for i, record in enumerate(records):
{validation_code}
    
    return errors
'''

def generate_csv_parser(
    file_spec: FileSpec,
    fields: List[FileField],
    language: str = "python"
) -> str:
    """Generate a CSV parser for the given file spec."""
    
    if language != "python":
        raise NotImplementedError(f"Language {language} not yet supported")
    
    class_name = _to_class_name(file_spec.name)
    function_name = _to_snake_case(file_spec.name)
    
    # Generate field definitions
    field_defs = []
    for f in fields:
        python_type = _to_python_type(f.field_type, f.nullable)
        field_defs.append(f"    {f.name}: {python_type}")
    
    # Generate field parsing
    field_parsing = []
    for f in fields:
        parser = _get_parser_expression(f)
        field_parsing.append(f"                    {f.name}={parser},")
    
    # Generate validation code
    validation = []
    for f in fields:
        if not f.nullable:
            validation.append(
                f"        if record.{f.name} is None:\n"
                f"            errors.append(f'Row {{i+1}}: {f.name} is required')"
            )
    
    return PYTHON_CSV_PARSER_TEMPLATE.format(
        file_name=file_spec.name,
        class_name=class_name,
        function_name=function_name,
        encoding=file_spec.encoding or "utf-8",
        delimiter=file_spec.delimiter or ",",
        field_definitions="\n".join(field_defs),
        field_parsing="\n".join(field_parsing),
        validation_code="\n".join(validation) if validation else "        pass",
    )


def _to_class_name(name: str) -> str:
    """Convert to PascalCase class name."""
    return "".join(word.title() for word in name.replace("-", "_").split("_"))


def _to_snake_case(name: str) -> str:
    """Convert to snake_case function name."""
    return name.replace("-", "_").lower()


def _to_python_type(field_type: str, nullable: bool) -> str:
    """Convert field type to Python type hint."""
    type_map = {
        "string": "str",
        "integer": "int",
        "decimal": "Decimal",
        "date": "date",
        "datetime": "datetime",
        "boolean": "bool",
    }
    base_type = type_map.get(field_type, "str")
    return f"Optional[{base_type}]" if nullable else base_type


def _get_parser_expression(field: FileField) -> str:
    """Get Python expression to parse field from row dict."""
    name = field.name
    
    if field.field_type == "integer":
        if field.nullable:
            return f"int(row['{name}']) if row.get('{name}') else None"
        return f"int(row['{name}'])"
    
    if field.field_type == "decimal":
        if field.nullable:
            return f"Decimal(row['{name}']) if row.get('{name}') else None"
        return f"Decimal(row['{name}'])"
    
    if field.field_type == "date":
        if field.nullable:
            return f"date.fromisoformat(row['{name}']) if row.get('{name}') else None"
        return f"date.fromisoformat(row['{name}'])"
    
    if field.field_type == "datetime":
        if field.nullable:
            return f"datetime.fromisoformat(row['{name}']) if row.get('{name}') else None"
        return f"datetime.fromisoformat(row['{name}'])"
    
    if field.field_type == "boolean":
        if field.nullable:
            return f"row.get('{name}', '').lower() in ('true', '1', 'yes') if row.get('{name}') else None"
        return f"row['{name}'].lower() in ('true', '1', 'yes')"
    
    # Default: string
    if field.nullable:
        return f"row.get('{name}') or None"
    return f"row['{name}']"
```

---

## 8. KG & Codegen Changes

### 8.1 KG Node Types to Add

```python
# In domain/models.py, extend KGNodeType enum:

class KGNodeType(str, Enum):
    # Existing
    PROVIDER = "provider"
    ENTITY = "entity"
    ENDPOINT = "endpoint"
    WORKFLOW_TEMPLATE = "workflow_template"
    TASK = "task"
    PATTERN = "pattern"
    
    # New for file integration
    FILE_SPEC = "file_spec"
    FILE_FIELD = "file_field"
    RECORD_LAYOUT = "record_layout"
    FILE_PATTERN = "file_pattern"  # e.g., "pattern.file.header_detail_trailer"
```

### 8.2 KG Edge Types to Add

```python
# In domain/models.py, extend KGEdgeRelation enum:

class KGEdgeRelation(str, Enum):
    # Existing
    USES_ENDPOINT = "uses_endpoint"
    PRODUCES_ENTITY = "produces_entity"
    # ...
    
    # New for file integration
    HAS_FIELD = "has_field"           # file_spec -> file_field
    MAPS_TO = "maps_to"               # file_field -> entity_field
    VALIDATED_BY = "validated_by"     # file_field -> validation_rule
    DERIVES_FROM = "derives_from"     # file_spec -> pdf_guide (provenance)
    FILE_FLOWS_TO = "file_flows_to"   # file_spec -> endpoint (ETL target)
```

### 8.3 Codegen Integration Points

| Artifact Type | Template | Trigger |
|--------------|----------|---------|
| `file_parser` | `file_templates.py:generate_csv_parser()` | FileSpec with type=csv |
| `file_validator` | `file_templates.py:generate_validator()` | FileSpec with validation rules |
| `file_transformer` | `file_templates.py:generate_transformer()` | FileSpec with entity mappings |
| `fixed_width_parser` | `file_templates.py:generate_fixed_width_parser()` | FileSpec with type=fixed_width |

---

## 9. Test Strategy

### 9.1 Unit Tests

| Module | Test File | Key Tests |
|--------|-----------|-----------|
| `sources/csv_source.py` | `tests/test_csv_source.py` | Detection, parsing, silver conversion |
| `sources/pdf_guide.py` | `tests/test_pdf_guide_source.py` | Field extraction, LLM fallback |
| `sources/fixed_width.py` | `tests/test_fixed_width_source.py` | Colspec parsing, position inference |
| `parsers/csv_schema.py` | `tests/test_csv_schema.py` | (already exists) |
| `codegen/file_templates.py` | `tests/test_file_templates.py` | Parser generation, validation code |

### 9.2 Integration Tests (Postgres)

```python
# tests/test_file_integration_postgres.py

@pytest.mark.postgres
class TestFileIntegrationPostgres:
    """End-to-end file integration tests with Postgres backend."""
    
    def test_csv_to_silver_to_kg(self):
        """CSV sample → Silver File Model → KG population."""
        
    def test_pdf_guide_to_codegen(self):
        """PDF guide → field extraction → parser code generation."""
        
    def test_mixed_api_and_file_run(self):
        """Run with both OpenAPI spec and CSV sample in same workflow."""
```

### 9.3 Production Simulation Matrix

| Scenario | Inputs | Expected Outputs | Validation |
|----------|--------|------------------|------------|
| CSV only | `sample.csv` | FileSpec, FileFields, parser code | Parser compiles, parses sample |
| PDF guide + CSV | `guide.pdf`, `sample.csv` | Enriched FileFields with descriptions | Field descriptions populated |
| Fixed-width | `layout.txt`, `data.dat` | RecordLayout, positioned fields | Positions match guide |
| Excel | `template.xlsx` | Sheet → FileSpec per sheet | All sheets extracted |
| Multi-format run | `api.yaml`, `file.csv` | Both API + File Silver models | KG has both node types |

### 9.4 Golden Fixtures

Create test fixtures in `tests/fixtures/file_specs/`:
- `simple_customers.csv` — basic CSV with headers
- `transactions_no_header.tsv` — TSV without header row
- `bank_fixed_width.txt` — fixed-width transaction file
- `data_guide.pdf` — sample data transmission guide
- `template.xlsx` — multi-sheet Excel template

---

## 10. Rollout Plan

### 10.1 Phase 1: Foundation (Week 1)

- [ ] Create `sources/` package with base protocol
- [ ] Move OpenAPI detection to `sources/openapi.py`
- [ ] Add domain models for FileSpec, FileField
- [ ] Add DDL to Postgres and SQLite
- [ ] Tests: Unit tests for sources, domain models

### 10.2 Phase 2: CSV End-to-End (Week 1-2)

- [ ] Implement `sources/csv_source.py`
- [ ] Implement `build_silver_file_model.py` node
- [ ] Add file persistence functions
- [ ] Generate CSV parser code
- [ ] Tests: Integration test CSV → Silver → Codegen

### 10.3 Phase 3: PDF Guide Extraction (Week 2)

- [ ] Implement `sources/pdf_guide.py`
- [ ] Add LLM extraction prompts
- [ ] Link PDF fields to CSV fields
- [ ] Tests: PDF extraction accuracy

### 10.4 Phase 4: Fixed-Width & Excel (Week 3)

- [ ] Implement `sources/fixed_width.py`
- [ ] Implement `sources/excel.py`
- [ ] Add RecordLayout support
- [ ] Tests: Multi-format scenarios

### 10.5 Phase 5: KG & Learning (Week 3)

- [ ] Add KG node types for files
- [ ] Seed file patterns
- [ ] Enable cross-format pattern matching
- [ ] Tests: KG queries for file specs

### 10.6 Rollback Plan

If issues arise:
1. Feature flag: `FILE_INTEGRATION_ENABLED=false` disables all file routes
2. Detection fallback: Unknown formats still route to OpenAPI parser
3. DB backward compatible: New tables don't break existing API flow

---

## 11. Implementation Buckets

### Bucket 1 (MVP — Must Ship First)

| Item | Status | Files |
|------|--------|-------|
| SpecSource plugin system | 🔲 | `sources/__init__.py`, `sources/base.py` |
| OpenAPISource refactor | 🔲 | `sources/openapi.py` |
| CSVSource | 🔲 | `sources/csv_source.py` |
| FileSpec domain model | 🔲 | `domain/models.py` |
| File DDL (Postgres + SQLite) | 🔲 | `persistence/postgres.py`, `persistence/db.py` |
| build_silver_file_model node | 🔲 | `graph/nodes/build_silver_file_model.py` |
| CSV parser codegen | 🔲 | `codegen/file_templates.py` |
| Unit + integration tests | 🔲 | `tests/test_*_source.py`, `tests/test_file_integration.py` |

**Estimated effort:** 7-10 days

### Bucket 2 (Next)

| Item | Status | Files |
|------|--------|-------|
| FixedWidthSource | 🔲 | `sources/fixed_width.py`, `parsers/fixed_width_parser.py` |
| ExcelSource | 🔲 | `sources/excel.py` |
| PDFGuideSource (file-focused) | 🔲 | `sources/pdf_guide.py` |
| RecordLayout support | 🔲 | Multi-record fixed-width files |
| Schema-driven validation | 🔲 | Validation rule generation |

**Estimated effort:** 5-7 days

### Bucket 3 (Design Now, Implement Later)

| Item | Notes |
|------|-------|
| EDI/X12 parsing | Evaluate `python-edi` vs custom parser |
| OCR fallback | For scanned PDFs, use `pytesseract` |
| Version tracking | Multiple spec versions with compatibility |
| Advanced transformations | Cross-field rules, lookups |

---

## Appendix A: CLI Changes

```bash
# Current (API-only)
iw run -s specs/stripe_api.json -t "Create checkout"

# Extended (API + Files)
iw run -s specs/stripe_api.json \
       -s data/customer_export.csv \
       -s docs/data_guide.pdf \
       -t "Import customer data and create Stripe subscriptions"
```

The `-s` flag already accepts multiple sources. Detection will route each to appropriate handler.

---

## Appendix B: Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| LLM extraction inaccurate | Medium | Medium | Heuristic first, LLM fallback |
| Fixed-width position errors | Medium | High | Require explicit colspec input |
| PDF quality varies | High | Medium | Graceful degradation, manual override |
| Performance regression | Low | Medium | Lazy loading, stream processing |
| Breaking API workflow | Low | High | Feature flag, separate nodes |

---

## Appendix C: References

- Original requirement: `docs/FINAL_DEMO_ASSESSMENT.md` R4
- Existing CSV parser: `parsers/csv_schema.py`
- Existing file_specs DDL: `persistence/postgres.py` lines 287-302
- V1 Gap Closure Plan P3: `docs/V1_GAP_CLOSURE_PLAN.md`
