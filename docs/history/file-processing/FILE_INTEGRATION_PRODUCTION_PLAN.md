# File Integration Production-Ready Implementation Plan

**Date**: 2026-01-01  
**Version**: 2.1 (Tightened)  
**Status**: READY FOR IMPLEMENTATION  
**Complexity**: Medium (estimated 8-10 hours of dev work)

---

## Critical Corrections from V1.0

| Issue | V1.0 Flaw | V2.0 Fix |
|-------|-----------|----------|
| **Postgres not proven** | Claimed "no migrations" based on SQLite only | Verified Postgres DDL exists at [postgres.py#L549-634](../src/integration_coworker/persistence/postgres.py#L549); added Postgres test requirements |
| **Operations too broad** | Step 6 generated `Operation` for ALL `file_specs` | Now workflow-node-driven: only targeted files become Operations |
| **Metadata not JSON-safe** | Stored raw `FileSpec` objects in `Operation.metadata` | Now stores IDs only: `{"file_spec_id": int, "op": str}` |
| **Node naming inconsistent** | Mixed "file_parse" and "parse_file" | Standardized to `"parse_file"` as constant |
| **CLI as P2** | Treated input routing as optional polish | Elevated to P1 with full CLI contract |
| **Tests not reproducible** | "41 passed" without exact commands | Added exact invocations with env vars and markers |

## V2.1 Tightening

| Issue | V2.0 Gap | V2.1 Fix |
|-------|----------|----------|
| **Exit code citation** | Cited "argparse convention" for exit code 2 | Corrected: Click/Typer uses exit code 2 for usage errors ([docs][1]). Added CLI smoke test. |
| **Stringly-typed comparisons** | `f.field_type == "integer"` (string literals) | Must use `FileFieldType.INTEGER` (enum) or `.value` comparison |
| **Hardcoded symbol names** | `parse_{safe_name}` assumed to match templates | Added `FileParserTemplateResult` (Step 2.5) as single source of truth |
| **Dispatcher call sites** | Modified signature without propagation | Must update all 3 call sites + add backwards-compat test |
| **No guardrail test** | "Workflow nodes are source" asserted but not tested | Added test: no ops when `file_specs` exist but no `parse_file` nodes |
| **Postgres test isolation** | Assumed `@pytest.mark.postgres` sufficient | Added `skipif` when docker/env unavailable |

[1]: https://click.palletsprojects.com/en/8.1.x/quickstart/#error-handling

---

## Section 1: Evidence Map (Repo Reality Check)

### 1.1 Current File Infrastructure — Verified Complete

| Component | File | Line(s) | Status | Evidence |
|-----------|------|---------|--------|----------|
| **Domain Models** | | | | |
| `FileSpec` | [domain/models.py](../src/integration_coworker/domain/models.py#L238) | 238-283 | ✅ | dataclass with 15 fields |
| `FileField` | [domain/models.py](../src/integration_coworker/domain/models.py#L284) | 284-310 | ✅ | dataclass with 13 fields |
| `FileFieldType` | [domain/models.py](../src/integration_coworker/domain/models.py#L210) | 210-235 | ✅ | Enum with 10 types |
| **State Fields** | | | | |
| `state.file_specs` | [graph/state.py](../src/integration_coworker/graph/state.py#L108) | 108 | ✅ | `List[FileSpec]` |
| `state.file_fields` | [graph/state.py](../src/integration_coworker/graph/state.py#L109) | 109 | ✅ | `List[FileField]` |
| **Source Parsers** | | | | |
| `CSVSource` | [sources/csv_source.py](../src/integration_coworker/sources/csv_source.py#L39) | 39+ | ✅ | Registered at priority 70 |
| `ExcelSource` | [sources/excel.py](../src/integration_coworker/sources/excel.py#L42) | 42+ | ✅ | Registered at priority 65 |
| `FixedWidthSource` | [sources/fixed_width.py](../src/integration_coworker/sources/fixed_width.py#L48) | 48+ | ✅ | Registered at priority 60 |
| `PDFGuideSource` | [sources/pdf_guide.py](../src/integration_coworker/sources/pdf_guide.py#L42) | 42+ | ✅ | Registered at priority 40 |
| **Silver Model Node** | | | | |
| `build_silver_file_model` | [build_silver_file_model.py](../src/integration_coworker/graph/nodes/build_silver_file_model.py) | Full | ✅ | Produces `state.file_specs/fields` |
| **Code Templates** | | | | |
| `generate_csv_parser()` | [file_templates.py](../src/integration_coworker/codegen/file_templates.py#L281) | 281-340 | ✅ | Returns complete parser code |
| `generate_fixed_width_parser()` | [file_templates.py](../src/integration_coworker/codegen/file_templates.py#L343) | 343-410 | ✅ | Returns complete parser code |
| `generate_excel_parser()` | [file_templates.py](../src/integration_coworker/codegen/file_templates.py#L413) | 413-480 | ✅ | Returns complete parser code |
| **Postgres Persistence** | | | | |
| `spec_silver.file_specs` | [postgres.py](../src/integration_coworker/persistence/postgres.py#L549) | 549-575 | ✅ | DDL with indexes |
| `spec_silver.file_fields` | [postgres.py](../src/integration_coworker/persistence/postgres.py#L577) | 577-594 | ✅ | FK to file_specs |
| `spec_silver.record_layouts` | [postgres.py](../src/integration_coworker/persistence/postgres.py#L596) | 596-612 | ✅ | FK to file_specs |
| **Postgres Tests** | | | | |
| Schema init test | [test_file_integration_postgres.py](../tests/test_file_integration_postgres.py#L24) | 24-46 | ✅ | Verifies 4 tables exist |
| Column test | [test_file_integration_postgres.py](../tests/test_file_integration_postgres.py#L48) | 48-70 | ✅ | Verifies schema |
| **SQLite Persistence** | | | | |
| `file_specs` table | [db.py](../src/integration_coworker/persistence/db.py#L740) | 740-762 | ✅ | CREATE TABLE |
| `file_fields` table | [db.py](../src/integration_coworker/persistence/db.py#L763) | 763-784 | ✅ | CREATE TABLE |

### 1.2 Missing Infrastructure — Gaps to Close

| Gap | Location | Impact | Priority |
|-----|----------|--------|----------|
| **No `ProtocolType.FILE`** | [domain/ir.py#L18](../src/integration_coworker/domain/ir.py#L18) | Can't dispatch to file strategy | **P0** |
| **No `FileCodegenStrategy`** | `codegen/strategies/` (missing) | No codegen dispatch target | **P0** |
| **No `parse_file` node type** | [models.py#L467](../src/integration_coworker/domain/models.py#L467) | Can't represent file ops in flows | **P0** |
| **`understand_task` ignores files** | [understand_task.py](../src/integration_coworker/graph/nodes/understand_task.py) | No `target_files` extraction | **P1** |
| **`plan_integration_flow` API-only** | [plan_integration_flow.py#L231](../src/integration_coworker/graph/nodes/plan_integration_flow.py#L231) | No file node creation | **P1** |
| **CLI no file input flags** | [cli.py#L381](../src/integration_coworker/cli.py#L381) | Only `--spec-ref` for OpenAPI | **P1** |
| **`IntegrationTask` no file targeting** | [models.py#L442](../src/integration_coworker/domain/models.py#L442) | No structured file refs | P2 |

### 1.3 Dependency Graph (Corrected)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│              FILE INTEGRATION DEPENDENCY GRAPH (V2 - CORRECTED)             │
└─────────────────────────────────────────────────────────────────────────────┘

[INGESTION LAYER - READY]
━━━━━━━━━━━━━━━━━━━━━━━━━━
sources/csv_source.py ─────┬───▶ ParsedSpec(source_type=FILE)
sources/excel.py ──────────┤              │
sources/fixed_width.py ────┤              ▼
sources/pdf_guide.py ──────┘    detect_and_parse_spec
                                          │
                                          ▼
                               build_silver_file_model ───▶ state.file_specs (List[FileSpec])
                                          │                 state.file_fields (List[FileField])
                                          ▼
                               persist_silver_checkpoint ───▶ DB: file_specs, file_fields

[TARGETING LAYER - NEEDS IMPLEMENTATION]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                   ┌──────────────────────────────────────────────────────┐
understand_task ───│ MUST add: target_files=[{file_spec_id:int, op:str}] │
                   └──────────────────────────────────────────────────────┘
        │
        ▼ constraints.extra.target_files
        │
        │          ┌──────────────────────────────────────────────────────┐
plan_integration ──│ MUST create: IntegrationFlowNode(type="parse_file") │
_flow              └──────────────────────────────────────────────────────┘
        │
        ▼ state.workflow_nodes (with file nodes)

[CODEGEN LAYER - NEEDS WIRING]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                   ┌──────────────────────────────────────────────────────┐
generate_code_ ────│ MUST: workflow_node → Operation(protocol=FILE) →    │
and_tests          │       StrategyDispatcher → FileCodegenStrategy      │
                   └──────────────────────────────────────────────────────┘
        │
        ▼ Resolve file_spec_id → FileSpec from state
        │
codegen/file_templates.py ───▶ generate_file_parser(spec, fields)
                                          │
                                          ▼
                               CodeArtifact(artifact_type="parser", code=...)
```

---

## Section 2: Architecture Decision

### Decision: **APPROACH A** — FILE as ProtocolType

**Rationale** (unchanged from V1):
1. Minimal disruption: 6-8 files to touch
2. Pattern reuse: Follows existing Strategy pattern
3. Templates ready: `file_templates.py` generators exist
4. Reversible: Easy to refactor later

### Key Refinement: Docstring Updates for Semantic Clarity

`ProtocolType` is conceptually "API protocols" but we're adding FILE. To avoid confusion:

**Required docstring update** in `domain/ir.py`:
```python
class ProtocolType(str, Enum):
    """
    Supported protocol or integration kinds.
    
    Includes both API protocols (REST, GraphQL, etc.) and data integration
    kinds (FILE for CSV/Excel/fixed-width parsing).
    """
    REST = "rest"
    GRAPHQL = "graphql"
    # ... existing ...
    FILE = "file"  # CSV, Excel, fixed-width file parsing
```

This documents the expanded semantic scope without renaming the enum.

---

## Section 3: Data Contracts (Corrected)

### 3.1 Canonical Rule

> **Workflow nodes are the source of truth for what code to generate.**  
> Do NOT generate Operations from "all file_specs exist".  
> Generate Operations only from targeted workflow nodes.

### 3.2 Contract: `understand_task` Output

```python
# constraints.extra.target_files schema
target_files: List[Dict] = [
    {
        "file_spec_id": int,      # FK to file_specs.id (NOT name)
        "op": str,                # "parse" | "validate" | "transform"
        "reason": str             # Why this file was selected
    }
]
```

**Why IDs not names?**
- Names can have duplicates across source systems
- IDs are deterministic and DB-resolvable
- Avoids string matching bugs

### 3.3 Contract: `plan_integration_flow` Output

```python
# IntegrationFlowNode for file operations
IntegrationFlowNode(
    id=None,
    task_id=task_id,
    node_key=f"parse_file_{i}",    # Unique key
    node_type="parse_file",         # CONSTANT: always "parse_file"
    label=f"Parse {spec_name}",
    description=f"Parse {file_type} file",
    config={
        "file_spec_id": int,        # FK to file_specs.id
        "op": "parse"               # Operation type
    },
    position=i
)
```

**Node type constant** (define in one place):
```python
# In domain/models.py or a constants module
class NodeType:
    """Constants for IntegrationFlowNode.node_type values."""
    START = "start"
    END = "end"
    API_CALL = "api_call"
    PARSE_FILE = "parse_file"      # NEW
    TRANSFORM = "transform"
    VALIDATION = "validation"
    DECISION = "decision"
```

### 3.4 Contract: `Operation.metadata` (JSON-Safe)

```python
# CORRECT: IDs only, JSON-serializable
Operation(
    operation_id=f"parse_file_{spec.id}",
    name=f"Parse {spec.name}",
    description=f"Parse {spec.file_type} file",
    protocol=ProtocolType.FILE,
    pattern=CommunicationPattern.UNARY,
    metadata={
        "file_spec_id": spec.id,    # int, not FileSpec object
        "op": "parse"               # str
    }
)

# WRONG: Raw objects (breaks serialization)
metadata={"file_spec": spec, "fields": fields}  # ❌ NEVER DO THIS
```

**Strategy resolves IDs to objects at codegen time**:
```python
class FileCodegenStrategy:
    def generate_client(self, operation: Operation, context: CodegenContext) -> GeneratedArtifact:
        # Resolve from context (state passed in context or looked up)
        file_spec_id = operation.metadata["file_spec_id"]
        file_spec = self._resolve_file_spec(file_spec_id, context)
        fields = self._resolve_fields(file_spec_id, context)
        # ...
```

---

## Section 4: File-by-File Implementation Plan

### Current Step: 8/8 ✅ COMPLETE

**Implementation Status (2026-01-XX)**:
| Step | Description | Status | Notes |
|------|-------------|--------|-------|
| 1/8 | `ProtocolType.FILE` in ir.py | ✅ Complete | 3 tests added |
| 2/8 | `NodeType` constants in models.py | ✅ Complete | 4 tests added |
| 2.5/8 | `FileParserTemplateResult` wrapper | ✅ Complete | 10 tests added |
| 3/8 | `FileCodegenStrategy` | ✅ Complete | 280 LOC |
| 4/8 | Register in dispatcher | ✅ Complete | Backwards compat maintained |
| 5/8 | `understand_task` file detection | ✅ Complete | `_is_file_based_task()`, backward compat tests |
| 6/8 | `plan_integration_flow` file nodes | ✅ Complete | `parse_file` node creation |
| 7/8 | `generate_code_and_tests` wiring | ✅ Complete | FILE protocol dispatch |
| 8/8 | CLI `--file`/`--guide` options | ✅ Complete | Exit code 2 validation, hermetic tests |

**Post-Implementation Fixes (Architecture Critique)**:
| Issue | Fix | Status |
|-------|-----|--------|
| CLI tests env-dependent | Added `hermetic_cli_env` autouse fixture with monkeypatch | ✅ Fixed |
| Guide-file pairing undefined | Added warning for CSV + guide (irrelevant combo) | ✅ Fixed |
| understand_task backward compat | Verified defaults to REST, added unit tests | ✅ Verified |
| GraphQL test failures | Fixed test metadata keys (`_parsed_from` vs `detected_protocol`) | ✅ Fixed |
| Documentation step numbers | Updated to canonical status table | ✅ Fixed |

---

### Step 1/8: Add `ProtocolType.FILE` to IR ✅

**File**: [src/integration_coworker/domain/ir.py](../src/integration_coworker/domain/ir.py#L18)

**Change**:
```python
class ProtocolType(str, Enum):
    """
    Supported protocol or integration kinds.
    
    Includes API protocols (REST, GraphQL, gRPC, AsyncAPI, WebSocket, SOAP)
    and data integration kinds (FILE for CSV/Excel/fixed-width parsing).
    """
    REST = "rest"
    GRAPHQL = "graphql"
    GRPC = "grpc"
    ASYNCAPI = "asyncapi"
    WEBSOCKET = "websocket"
    SOAP = "soap"
    FILE = "file"  # CSV, Excel, fixed-width file parsing
```

**Downstream impact check**:
```bash
rg -n "ProtocolType\." src/ --type py | grep -v "import"
```

Only [generate_code_and_tests.py#L180](../src/integration_coworker/graph/nodes/generate_code_and_tests.py#L180) has a conditional check (`if operation.protocol == ProtocolType.REST`). This is non-exhaustive and will pass through to dispatcher, which is correct.

**Risk**: LOW

---

### Step 2/8: Add Node Type Constants

**File**: [src/integration_coworker/domain/models.py](../src/integration_coworker/domain/models.py)

**Add after line ~440** (before IntegrationTask):
```python
# =============================================================================
# Workflow Node Type Constants
# =============================================================================

class NodeType:
    """
    Constants for IntegrationFlowNode.node_type values.
    
    Use these instead of string literals to prevent drift.
    """
    START = "start"
    END = "end"
    API_CALL = "api_call"
    PARSE_FILE = "parse_file"      # NEW
    TRANSFORM = "transform"
    VALIDATION = "validation"
    DECISION = "decision"
```

**Update IntegrationFlowNode docstring** to reference NodeType.

---

### Step 2.5/8: Add `FileParserTemplateResult` Wrapper

**File**: [src/integration_coworker/codegen/file_templates.py](../src/integration_coworker/codegen/file_templates.py) (ADD)

**Purpose**: Single source of truth for generated symbol names. Prevents FileCodegenStrategy from hardcoding `parse_{safe_name}` that may not match actual template output.

```python
@dataclass
class FileParserTemplateResult:
    """
    Result of file parser generation with symbol names.
    
    This is the contract between file_templates.py and FileCodegenStrategy.
    Strategy uses these names instead of guessing symbol patterns.
    """
    code: str                    # The generated parser code
    module_basename: str         # e.g., "daily_transactions" 
    record_class_name: str       # e.g., "DailyTransactions"
    parse_func_name: str         # e.g., "parse_daily_transactions"
    validate_func_name: str      # e.g., "validate_daily_transactions"
    dependencies: List[str]      # e.g., ["openpyxl"] for Excel


def generate_file_parser_with_metadata(
    file_spec: FileSpec,
    fields: List[FileField],
    language: str = "python",
) -> FileParserTemplateResult:
    """
    Generate parser code with metadata about generated symbols.
    
    Wraps generate_file_parser() to add symbol name tracking.
    """
    code = generate_file_parser(file_spec, fields, language)
    
    # Use same naming helpers as templates
    safe_name = _to_snake_case(file_spec.name)
    class_name = _to_class_name(file_spec.name)
    
    # Dependencies based on file type (use FileType enum)
    dependencies = []
    if file_spec.file_type in (FileType.XLSX.value, FileType.XLS.value):
        dependencies.append("openpyxl")
    
    return FileParserTemplateResult(
        code=code,
        module_basename=safe_name,
        record_class_name=class_name,
        parse_func_name=f"parse_{safe_name}",
        validate_func_name=f"validate_{safe_name}",
        dependencies=dependencies,
    )
```

**Why this matters**:
- Templates define `parse_{function_name}` using `_to_snake_case()` helper
- Strategy was guessing with `safe_name = file_spec.name.lower().replace(...)`
- If naming logic drifts, generated flows/tests break silently
- This wrapper ensures consistency

---

### Step 3/8: Create `FileCodegenStrategy`

**File**: `src/integration_coworker/codegen/strategies/file.py` (NEW)

**CRITICAL**: Use enums for type comparisons, not string literals.

```python
"""
File-based codegen strategy for CSV/Excel/Fixed-Width parsers.

Implements CodegenStrategy Protocol for ProtocolType.FILE.
Delegates to templates in file_templates.py.
"""
from typing import List, Optional

from integration_coworker.codegen.protocol_dispatch import (
    ArtifactType,
    CodegenContext,
    GeneratedArtifact,
)
from integration_coworker.codegen.file_templates import (
    generate_file_parser_with_metadata,
    FileParserTemplateResult,
)
from integration_coworker.domain.ir import Operation, ProtocolType
from integration_coworker.domain.models import (
    FileSpec, 
    FileField, 
    FileType,           # Enum for file_type comparisons
    FileFieldType,      # Enum for field_type comparisons
)


class FileCodegenStrategy:
    """
    Codegen strategy for file-based data integrations.
    
    Generates parser code for CSV, Excel, and fixed-width files.
    Uses templates from file_templates.py.
    """
    
    def __init__(self, file_specs: Optional[List[FileSpec]] = None, 
                 file_fields: Optional[List[FileField]] = None):
        """
        Initialize with file specs/fields for ID resolution.
        
        Args:
            file_specs: List of FileSpec objects for ID lookup
            file_fields: List of FileField objects for ID lookup
        """
        self._file_specs = {fs.id: fs for fs in (file_specs or [])}
        self._file_fields_by_spec = {}
        for ff in (file_fields or []):
            if ff.file_spec_id not in self._file_fields_by_spec:
                self._file_fields_by_spec[ff.file_spec_id] = []
            self._file_fields_by_spec[ff.file_spec_id].append(ff)
    
    @property
    def protocol_type(self) -> ProtocolType:
        return ProtocolType.FILE
    
    def _resolve_file_spec(self, file_spec_id: int) -> FileSpec:
        """Resolve file_spec_id to FileSpec object."""
        if file_spec_id not in self._file_specs:
            raise ValueError(f"FileSpec ID {file_spec_id} not found in strategy context")
        return self._file_specs[file_spec_id]
    
    def _resolve_fields(self, file_spec_id: int) -> List[FileField]:
        """Resolve fields for a file_spec_id."""
        return self._file_fields_by_spec.get(file_spec_id, [])
    
    def generate_client(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """
        Generate parser code for a file operation.
        
        Expects operation.metadata = {"file_spec_id": int, "op": str}
        """
        file_spec_id = operation.metadata.get("file_spec_id")
        if file_spec_id is None:
            raise ValueError(
                f"Operation {operation.operation_id} missing file_spec_id in metadata"
            )
        
        file_spec = self._resolve_file_spec(file_spec_id)
        fields = self._resolve_fields(file_spec_id)
        
        # Generate using template wrapper (single source of truth for naming)
        result: FileParserTemplateResult = generate_file_parser_with_metadata(
            file_spec, fields, context.language
        )
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.CLIENT,
            filename=f"{result.module_basename}.py",
            code=result.code,
            language=context.language,
            imports=["csv", "dataclasses", "typing", "datetime", "decimal"],
            dependencies=result.dependencies,  # From template result, not guessed
            protocol=ProtocolType.FILE,
            operation_id=operation.operation_id,
            metadata={
                "file_spec_id": file_spec_id, 
                "file_type": file_spec.file_type,
                "parse_func": result.parse_func_name,       # Captured from template
                "validate_func": result.validate_func_name,  # Captured from template
                "record_class": result.record_class_name,    # Captured from template
            },
        )
    
    def generate_flow(
        self,
        operation: Operation,
        context: CodegenContext,
        client_artifact: Optional[GeneratedArtifact] = None,
    ) -> GeneratedArtifact:
        """
        Generate integration flow code for file parsing.
        
        Args:
            client_artifact: The client artifact from generate_client().
                            Contains parse_func, validate_func names in metadata.
        
        File flows are simpler than API flows:
        1. Read file
        2. Parse records
        3. Validate
        4. Return/transform
        """
        file_spec_id = operation.metadata.get("file_spec_id")
        file_spec = self._resolve_file_spec(file_spec_id)
        
        # Get symbol names from client artifact (single source of truth)
        if client_artifact and client_artifact.metadata:
            parse_func = client_artifact.metadata.get("parse_func", f"parse_{file_spec.name.lower()}")
            validate_func = client_artifact.metadata.get("validate_func", f"validate_{file_spec.name.lower()}")
            module_name = client_artifact.filename.replace(".py", "")
        else:
            # Fallback: regenerate metadata (slower but correct)
            result = generate_file_parser_with_metadata(file_spec, [], context.language)
            parse_func = result.parse_func_name
            validate_func = result.validate_func_name
            module_name = result.module_basename
        
        flow_code = f'''"""
Integration flow for {file_spec.name} file processing.
"""
from {module_name} import {parse_func}, {validate_func}


def process_{module_name}(file_path: str) -> dict:
    """
    Process {file_spec.name} file end-to-end.
    
    Args:
        file_path: Path to the input file
        
    Returns:
        dict with 'records' and 'errors' keys
    """
    # Parse
    records = {parse_func}(file_path)
    
    # Validate
    errors = {validate_func}(records)
    
    return {{
        "records": records,
        "record_count": len(records),
        "errors": errors,
        "error_count": len(errors),
        "is_valid": len(errors) == 0,
    }}
'''
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.FLOW,
            filename=f"flow_{module_name}.py",
            code=flow_code,
            language=context.language,
            imports=[],
            dependencies=[],
            protocol=ProtocolType.FILE,
            operation_id=operation.operation_id,
        )
    
    def generate_test(
        self,
        operation: Operation,
        context: CodegenContext,
        client_artifact: Optional[GeneratedArtifact] = None,
    ) -> GeneratedArtifact:
        """
        Generate pytest tests for the file parser.
        
        Args:
            client_artifact: The client artifact from generate_client().
                            Contains parse_func, validate_func, record_class names.
        """
        file_spec_id = operation.metadata.get("file_spec_id")
        file_spec = self._resolve_file_spec(file_spec_id)
        fields = self._resolve_fields(file_spec_id)
        
        # Get symbol names from client artifact (single source of truth)
        if client_artifact and client_artifact.metadata:
            parse_func = client_artifact.metadata.get("parse_func")
            validate_func = client_artifact.metadata.get("validate_func")
            class_name = client_artifact.metadata.get("record_class")
            module_name = client_artifact.filename.replace(".py", "")
        else:
            # Fallback: regenerate metadata
            result = generate_file_parser_with_metadata(file_spec, fields, context.language)
            parse_func = result.parse_func_name
            validate_func = result.validate_func_name
            class_name = result.record_class_name
            module_name = result.module_basename
        
        # Build sample data based on fields (use enum or .value for comparison)
        sample_row_parts = []
        for f in sorted(fields, key=lambda x: x.position):
            # Compare against enum values, not string literals
            field_type = f.field_type if isinstance(f.field_type, str) else f.field_type.value
            if field_type == FileFieldType.INTEGER.value:
                sample_row_parts.append("1")
            elif field_type == FileFieldType.DECIMAL.value:
                sample_row_parts.append("100.00")
            elif field_type == FileFieldType.DATE.value:
                sample_row_parts.append("2024-01-01")
            elif field_type == FileFieldType.BOOLEAN.value:
                sample_row_parts.append("true")
            else:
                sample_row_parts.append("test_value")
        
        delimiter = file_spec.delimiter or ","
        header_row = delimiter.join(f.name for f in sorted(fields, key=lambda x: x.position))
        data_row = delimiter.join(sample_row_parts)
        
        test_code = f'''"""
Tests for {file_spec.name} parser.

Generated by Integration Co-Worker.
"""
import pytest
import tempfile
import os

from {module_name} import {parse_func}, {validate_func}, {class_name}


@pytest.fixture
def sample_file():
    """Create a temporary sample file for testing."""
    content = """{header_row}
{data_row}
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(content)
        path = f.name
    yield path
    os.unlink(path)


class TestParse{class_name}:
    """Tests for {parse_func} function."""
    
    def test_parse_returns_list(self, sample_file):
        """Parser returns a list of records."""
        records = {parse_func}(sample_file)
        assert isinstance(records, list)
    
    def test_parse_returns_correct_count(self, sample_file):
        """Parser returns expected number of records."""
        records = {parse_func}(sample_file)
        assert len(records) == 1
    
    def test_parse_returns_dataclass_instances(self, sample_file):
        """Parser returns dataclass instances."""
        records = {parse_func}(sample_file)
        assert all(isinstance(r, {class_name}) for r in records)
    
    def test_validate_returns_empty_for_valid(self, sample_file):
        """Validator returns empty list for valid records."""
        records = {parse_func}(sample_file)
        errors = {validate_func}(records)
        assert errors == []
    
    def test_parse_raises_on_missing_file(self):
        """Parser raises FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            {parse_func}("/nonexistent/path.csv")
'''
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.TEST,
            filename=f"test_{module_name}.py",
            code=test_code,
            language=context.language,
            imports=["pytest", "tempfile", "os"],
            dependencies=["pytest"],
            protocol=ProtocolType.FILE,
            operation_id=operation.operation_id,
        )
    
    def build_prompt(
        self,
        operation: Operation,
        context: CodegenContext,
        artifact_type: ArtifactType,
        skeleton_code: str,
    ) -> str:
        """
        Build an LLM prompt for file parsing code enhancement.
        
        Used when templates need LLM enhancement for edge cases.
        """
        file_spec_id = operation.metadata.get("file_spec_id")
        file_spec = self._resolve_file_spec(file_spec_id)
        fields = self._resolve_fields(file_spec_id)
        
        field_descriptions = "\n".join(
            f"  - {f.name}: {f.field_type} (nullable={f.nullable})"
            for f in fields
        )
        
        return f"""You are generating a {file_spec.file_type} file parser.

File Specification:
- Name: {file_spec.name}
- Type: {file_spec.file_type}
- Encoding: {file_spec.encoding or 'utf-8'}
- Delimiter: {repr(file_spec.delimiter) if file_spec.delimiter else 'N/A'}
- Has Header: {file_spec.has_header}

Fields:
{field_descriptions}

Skeleton Code:
```python
{skeleton_code}
```

Generate production-quality code that:
1. Handles encoding correctly
2. Validates field types
3. Reports line-level errors
4. Is defensive against malformed input

Return only the code, no explanations.
"""


__all__ = ["FileCodegenStrategy"]
```

**Estimated LOC**: ~280

---

### Step 4/8: Register Strategy in Dispatcher

**File**: [src/integration_coworker/codegen/protocol_dispatch.py](../src/integration_coworker/codegen/protocol_dispatch.py#L240)

**Update `create_default_dispatcher()`**:
```python
def create_default_dispatcher(
    file_specs: Optional[List["FileSpec"]] = None,
    file_fields: Optional[List["FileField"]] = None,
) -> StrategyDispatcher:
    """
    Create a dispatcher with all built-in strategies registered.
    
    Args:
        file_specs: Optional FileSpec list for FileCodegenStrategy ID resolution
        file_fields: Optional FileField list for FileCodegenStrategy ID resolution
    
    Returns:
        A fully-configured StrategyDispatcher
    """
    from integration_coworker.codegen.strategies import (
        AsyncAPICodegenStrategy,
        GraphQLCodegenStrategy,
        RESTCodegenStrategy,
        FileCodegenStrategy,
    )
    
    return (
        StrategyDispatcher()
        .register(RESTCodegenStrategy())
        .register(GraphQLCodegenStrategy())
        .register(AsyncAPICodegenStrategy())
        .register(FileCodegenStrategy(file_specs=file_specs, file_fields=file_fields))
    )
```

**File**: [src/integration_coworker/codegen/strategies/__init__.py](../src/integration_coworker/codegen/strategies/__init__.py)

**Update exports**:
```python
from .rest import RESTCodegenStrategy
from .graphql import GraphQLCodegenStrategy
from .asyncapi import AsyncAPICodegenStrategy
from .file import FileCodegenStrategy

__all__ = [
    "RESTCodegenStrategy",
    "GraphQLCodegenStrategy",
    "AsyncAPICodegenStrategy",
    "FileCodegenStrategy",
]
```

---

### Step 5/8: Update `understand_task` for File Targeting

**File**: [src/integration_coworker/graph/nodes/understand_task.py](../src/integration_coworker/graph/nodes/understand_task.py)

**Changes**:

1. **Add file spec summary to prompt** (~line 80):
```python
def _get_file_spec_context(state: WorkflowState) -> str:
    """Build summary of available file specs for LLM context."""
    if not state.file_specs:
        return ""
    
    lines = ["## Available File Specifications"]
    for fs in state.file_specs:
        field_count = len([f for f in state.file_fields if f.file_spec_id == fs.id])
        lines.append(f"- ID={fs.id}: {fs.name} ({fs.file_type}, {field_count} fields)")
    
    return "\n".join(lines)
```

2. **Update TOON schema** (~line 266):
```python
target_operations=[{operation_id:string,method:string,path:string,reason:string}]
target_files=[{file_spec_id:integer,op:string,reason:string}]
```

3. **Update response parsing** (~line 310):
```python
target_files = response.get("target_files", [])
# Validate structure
validated_files = []
for tf in target_files:
    if isinstance(tf, dict) and "file_spec_id" in tf:
        validated_files.append({
            "file_spec_id": int(tf["file_spec_id"]),
            "op": tf.get("op", "parse"),
            "reason": tf.get("reason", "")
        })

constraints["extra"]["target_files"] = validated_files
```

4. **Update heuristic fallback** (~line 401):
```python
# Detect file operations from task keywords
task_lower = state.task_description.lower()
file_keywords = {"parse", "import", "csv", "excel", "load", "read", "data file", "fixed-width"}
target_files = []

if any(kw in task_lower for kw in file_keywords) and state.file_specs:
    for fs in state.file_specs:
        # Match by name or type in task
        if fs.name.lower() in task_lower or fs.file_type.lower() in task_lower:
            target_files.append({
                "file_spec_id": fs.id,
                "op": "parse",
                "reason": "keyword match in task description"
            })
    
    # If no specific match but file keywords present, use first spec
    if not target_files and state.file_specs:
        fs = state.file_specs[0]
        target_files.append({
            "file_spec_id": fs.id,
            "op": "parse", 
            "reason": "default: first available file spec"
        })

constraints["extra"]["target_files"] = target_files
```

---

### Step 6/8: Update `plan_integration_flow` for File Nodes

**File**: [src/integration_coworker/graph/nodes/plan_integration_flow.py](../src/integration_coworker/graph/nodes/plan_integration_flow.py)

**Changes**:

1. **Import NodeType constant**:
```python
from integration_coworker.domain.models import NodeType
```

2. **Extract target_files** (after line 214):
```python
target_files = state.integration_task.constraints.get("extra", {}).get("target_files", [])
logger.info(f"[DEBUG] target_files count: {len(target_files)}")
```

3. **Create parse_file nodes** (after api_call creation, ~line 250):
```python
# Create parse_file nodes from target_files
file_spec_map = {fs.id: fs for fs in state.file_specs}

for i, tf in enumerate(target_files):
    file_spec_id = tf.get("file_spec_id") if isinstance(tf, dict) else None
    
    if file_spec_id is None:
        logger.warning(f"target_file entry missing file_spec_id: {tf}")
        continue
    
    matched_spec = file_spec_map.get(file_spec_id)
    
    if not matched_spec:
        logger.warning(f"file_spec_id {file_spec_id} not found in state.file_specs")
        continue
    
    node = IntegrationFlowNode(
        id=None,
        task_id=task_id,
        node_key=f"parse_file_{i}",
        node_type=NodeType.PARSE_FILE,
        label=f"Parse {matched_spec.name}",
        description=f"Parse {matched_spec.file_type} file: {matched_spec.name}",
        config={
            "file_spec_id": matched_spec.id,
            "op": tf.get("op", "parse")
        },
        position=len(state.workflow_nodes) + i,
    )
    state.workflow_nodes.append(node)
    
    logger.info(f"Created parse_file node for spec {matched_spec.name} (id={matched_spec.id})")
```

4. **Update DAG validation** to accept parse_file as valid node type.

---

### Step 7/8: Wire File Operations in `generate_code_and_tests`

**File**: [src/integration_coworker/graph/nodes/generate_code_and_tests.py](../src/integration_coworker/graph/nodes/generate_code_and_tests.py)

**Changes**:

1. **Import NodeType**:
```python
from integration_coworker.domain.models import NodeType
```

2. **Create Operations from workflow nodes** (NOT from all file_specs):
```python
def _create_file_operations_from_nodes(state: WorkflowState) -> List[Operation]:
    """
    Create FILE Operations from parse_file workflow nodes.
    
    CRITICAL: Only targeted files become Operations.
    Do NOT iterate over all state.file_specs.
    """
    operations = []
    file_spec_map = {fs.id: fs for fs in state.file_specs}
    
    for node in state.workflow_nodes:
        if node.node_type != NodeType.PARSE_FILE:
            continue
        
        file_spec_id = node.config.get("file_spec_id") if node.config else None
        if file_spec_id is None:
            logger.warning(f"parse_file node {node.node_key} missing file_spec_id in config")
            continue
        
        file_spec = file_spec_map.get(file_spec_id)
        if not file_spec:
            logger.warning(f"file_spec_id {file_spec_id} not found for node {node.node_key}")
            continue
        
        op = Operation(
            operation_id=f"parse_file_{file_spec.id}",
            name=f"Parse {file_spec.name}",
            description=f"Parse {file_spec.file_type} file",
            protocol=ProtocolType.FILE,
            pattern=CommunicationPattern.UNARY,
            metadata={
                "file_spec_id": file_spec.id,  # ID only, JSON-safe
                "op": node.config.get("op", "parse")
            }
        )
        operations.append(op)
    
    return operations
```

3. **Update dispatcher creation** to pass file context:
```python
# Where create_default_dispatcher is called:
dispatcher = create_default_dispatcher(
    file_specs=state.file_specs,
    file_fields=state.file_fields,
)
```

4. **Wire file operations into codegen flow**:
```python
# Before dispatching operations
file_operations = _create_file_operations_from_nodes(state)
if file_operations:
    state.operations.extend(file_operations)
    logger.info(f"Added {len(file_operations)} file operations from workflow nodes")
```

---

### Step 8/8: CLI Input Routing (P1)

**File**: [src/integration_coworker/cli.py](../src/integration_coworker/cli.py)

**Changes**:

1. **Add file input option** to `run` command (~line 381):
```python
@app.command("run")
def run_integration(
    spec_ref: List[str] = typer.Option(
        [],
        "--spec-ref",
        "-s",
        help="OpenAPI/AsyncAPI spec reference (URL or path)",
    ),
    file_input: List[Path] = typer.Option(
        [],
        "--file",
        "-f",
        help="Data file input (CSV, Excel, fixed-width). Auto-detected by extension.",
    ),
    guide: Optional[Path] = typer.Option(
        None,
        "--guide",
        "-g",
        help="PDF guide for fixed-width file layout specification",
    ),
    task: str = typer.Option(..., "--task", "-t", help="Description of the integration task"),
    # ... rest of options ...
):
```

2. **Validate input precedence**:
```python
# At start of run_integration function
if not spec_ref and not file_input:
    typer.echo("Error: Must provide --spec-ref or --file input", err=True)
    raise typer.Exit(code=2)  # argparse convention for usage errors

# Build combined spec_refs
all_refs = list(spec_ref)
for f in file_input:
    if not f.exists():
        typer.echo(f"Error: File not found: {f}", err=True)
        raise typer.Exit(code=2)
    all_refs.append(str(f.resolve()))

if guide:
    if not guide.exists():
        typer.echo(f"Error: Guide file not found: {guide}", err=True)
        raise typer.Exit(code=2)
    # Guide gets passed separately for fixed-width parsing
```

3. **Update demo command** similarly for consistency.

4. **Exit code contract** (per Click/Typer behavior):
- Exit 0: Success
- Exit 1: Runtime error
- Exit 2: Usage/argument error (Click/Typer returns 2 for --help, bad options, missing required args)

**Reference**: [Click error handling docs](https://click.palletsprojects.com/en/8.1.x/quickstart/#error-handling)

5. **Required CLI smoke test** (add to tests/test_cli.py):
```python
def test_run_missing_input_exits_2():
    """CLI exits 2 when neither --spec-ref nor --file provided."""
    result = runner.invoke(app, ["run", "--task", "foo"])
    assert result.exit_code == 2

def test_run_nonexistent_file_exits_2():
    """CLI exits 2 for nonexistent file path."""
    result = runner.invoke(app, ["run", "--file", "/does/not/exist.csv", "--task", "foo"])
    assert result.exit_code == 2

def test_run_runtime_error_exits_1():
    """CLI exits 1 for runtime exceptions (not usage errors)."""
    # Mock a runtime failure in the pipeline
    ...
```

---

## Section 5: Test Matrix (Deterministic)

### 5.1 Exact Test Commands

```bash
# Environment setup
export BEADS_DB=/tmp/test_file_integration.db
export USE_MOCK_LLM=true
export INTEGRATION_COWORKER_LOG_LEVEL=DEBUG

# 1. Unit tests - file templates (no network, no DB)
pytest tests/test_file_templates.py -v --tb=short
# Expected: 41 passed

# 2. Unit tests - protocol dispatch (no network, no DB)
pytest tests/unit/codegen/test_semantic_preservation.py::TestStrategyDispatcher -v --tb=short
# Expected: 3+ passed

# 3. File strategy unit tests (new, add after implementation)
pytest tests/unit/codegen/test_file_strategy.py -v --tb=short
# Expected: 8+ passed

# 4. SQLite persistence integration
pytest tests/test_file_integration.py -v --tb=short
# Expected: 9 passed

# 5. Postgres persistence integration (requires docker)
pytest -m postgres tests/test_file_integration_postgres.py -v --tb=short
# Expected: 10+ passed
# Skip if no docker: pytest -m "not postgres" ...

# 6. Workflow node tests
pytest tests/graph/ -k "understand_task or plan_integration" -v --tb=short
# Expected: varies, check for no regressions

# 7. Full suite regression
pytest tests/ -v --tb=short -x --ignore=tests/e2e/
# Expected: All existing tests pass
```

### 5.2 New Tests to Add

| Test | File | Purpose | Deterministic |
|------|------|---------|---------------|
| `test_protocol_type_file_exists` | `tests/unit/domain/test_ir.py` | Enum has FILE | ✅ Yes |
| `test_node_type_constants` | `tests/unit/domain/test_models.py` | Constants defined | ✅ Yes |
| `test_file_strategy_init` | `tests/unit/codegen/test_file_strategy.py` | Strategy instantiates | ✅ Yes |
| `test_file_strategy_protocol_type` | `tests/unit/codegen/test_file_strategy.py` | Returns FILE | ✅ Yes |
| `test_file_strategy_generate_client_csv` | `tests/unit/codegen/test_file_strategy.py` | CSV parser generated | ✅ Yes |
| `test_file_strategy_generate_client_excel` | `tests/unit/codegen/test_file_strategy.py` | Excel parser generated | ✅ Yes |
| `test_file_strategy_generate_test` | `tests/unit/codegen/test_file_strategy.py` | Test code generated | ✅ Yes |
| `test_file_strategy_json_safe_metadata` | `tests/unit/codegen/test_file_strategy.py` | No raw objects | ✅ Yes |
| `test_file_strategy_uses_enum_values` | `tests/unit/codegen/test_file_strategy.py` | Fixture uses FileFieldType enum | ✅ Yes |
| `test_dispatcher_routes_file` | `tests/unit/codegen/test_protocol_dispatch.py` | FILE dispatched | ✅ Yes |
| `test_dispatcher_no_args_backwards_compat` | `tests/unit/codegen/test_protocol_dispatch.py` | `create_default_dispatcher()` works with no args | ✅ Yes |
| `test_dispatcher_with_file_context` | `tests/unit/codegen/test_protocol_dispatch.py` | `create_default_dispatcher(file_specs, file_fields)` works | ✅ Yes |
| `test_understand_task_target_files_llm` | `tests/graph/test_understand_task.py` | Mock LLM extracts files | ✅ Yes |
| `test_understand_task_target_files_heuristic` | `tests/graph/test_understand_task.py` | Heuristic fallback works | ✅ Yes |
| `test_plan_flow_creates_parse_file_nodes` | `tests/graph/test_plan_integration_flow.py` | Nodes created | ✅ Yes |
| `test_codegen_only_targeted_files` | `tests/graph/test_generate_code_and_tests.py` | No extra ops | ✅ Yes |
| `test_no_file_ops_without_parse_file_nodes` | `tests/graph/test_generate_code_and_tests.py` | **GUARDRAIL**: file_specs present but no parse_file nodes → 0 operations | ✅ Yes |
| `test_one_parse_file_node_one_operation` | `tests/graph/test_generate_code_and_tests.py` | **GUARDRAIL**: 1 node → exactly 1 operation | ✅ Yes |
| `test_postgres_file_specs_roundtrip` | `tests/test_file_integration_postgres.py` | Insert/read works | ✅ Yes |
| `test_cli_missing_input_exits_2` | `tests/test_cli.py` | Exit 2 for missing --spec-ref/--file | ✅ Yes |
| `test_cli_nonexistent_file_exits_2` | `tests/test_cli.py` | Exit 2 for nonexistent path | ✅ Yes |

### 5.3 Tests to REJECT (Not Production Readiness)

| Anti-Pattern | Why Reject |
|--------------|------------|
| "Real LLM calls" in CI | Flaky, expensive, non-deterministic |
| "Real repo integrations" | Non-deterministic, security risk |
| "Download external specs" | Network-dependent, version drift |
| "Scan unknown repos" | Scope creep, destabilizing |

---

## Section 6: Risk Assessment & Rollout

### 6.1 Risk Matrix (Updated)

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Breaks existing API codegen | Low | High | Full test suite after each step |
| StrategyDispatcher regression | Low | Medium | Unit test dispatch routes |
| JSON serialization breaks | Medium | High | **NEW**: Explicit metadata contract test |
| Node type drift | Medium | Medium | **NEW**: NodeType constants |
| CLI exit codes wrong | Low | Low | Test argparse error paths |
| Postgres schema missing | None | N/A | **VERIFIED**: DDL exists at postgres.py#L549 |

### 6.2 Phased Rollout (Updated)

**Phase 1: Foundation (Steps 1-4)** - 3 hours
- Add `ProtocolType.FILE`
- Add `NodeType` constants
- Create `FileCodegenStrategy`
- Register in dispatcher
- **Gate**: All existing tests pass

**Phase 2: Targeting (Steps 5-6)** - 2.5 hours
- Update `understand_task`
- Update `plan_integration_flow`
- **Gate**: New node tests pass

**Phase 3: Codegen Wiring (Step 7)** - 2 hours
- Wire workflow nodes to Operations
- Update dispatcher creation
- **Gate**: E2E file codegen test passes

**Phase 4: CLI (Step 8)** - 1.5 hours
- Add `--file` input option
- Validate exit codes
- **Gate**: CLI smoke tests pass

**Phase 5: Polish** - 1 hour
- Documentation updates
- CHANGELOG entry
- Final regression run

---

## Appendix A: Quick Reference

```
[IMPLEMENTATION ORDER]
1.   domain/ir.py                     - Add ProtocolType.FILE + test
2.   domain/models.py                 - Add NodeType constants
2.5. codegen/file_templates.py        - Add FileParserTemplateResult wrapper
3.   codegen/strategies/file.py       - NEW: FileCodegenStrategy (use template result)
4.   codegen/strategies/__init__.py   - Export FileCodegenStrategy
5.   codegen/protocol_dispatch.py     - Register in factory + update call sites
6.   graph/nodes/understand_task.py   - Add target_files
7.   graph/nodes/plan_integration_flow.py - Add parse_file nodes
8.   graph/nodes/generate_code_and_tests.py - Wire operations + guardrail test
9.   cli.py                           - Add --file input + exit code tests
```

---

## Appendix B: Data Contract Summary

```python
# understand_task output
constraints.extra.target_files = [
    {"file_spec_id": 1, "op": "parse", "reason": "matched task keywords"}
]

# plan_integration_flow output  
IntegrationFlowNode(
    node_type="parse_file",  # Use NodeType.PARSE_FILE constant
    config={"file_spec_id": 1, "op": "parse"}
)

# Operation metadata (JSON-safe)
Operation(
    protocol=ProtocolType.FILE,
    metadata={"file_spec_id": 1, "op": "parse"}  # IDs only, no objects
)

# FileCodegenStrategy resolution
strategy._resolve_file_spec(file_spec_id)  # Returns FileSpec from internal map
strategy._resolve_fields(file_spec_id)     # Returns List[FileField]
```

---

## Appendix C: Future Enhancement — CodegenContext Resolution

**Current Design (V2.1)**:
```python
# file_specs/file_fields passed at dispatcher construction time
dispatcher = create_default_dispatcher(
    file_specs=state.file_specs,
    file_fields=state.file_fields,
)
```

**Why this works**: The dispatcher is created once per codegen invocation with the current state's file context. Simple and functional.

**Why this is not ideal**: The strategy holds state (mutable internal maps). If dispatcher is reused across different states, the file context could be stale.

**Better Pattern for V3** (deferred):
```python
# Resolution via CodegenContext at generate-time
class CodegenContext:
    # ... existing fields ...
    file_spec_resolver: Optional[Callable[[int], FileSpec]] = None
    file_fields_resolver: Optional[Callable[[int], List[FileField]]] = None

class FileCodegenStrategy:
    def generate_client(self, operation: Operation, context: CodegenContext) -> GeneratedArtifact:
        # Resolve at generate-time, not at construction-time
        file_spec_id = operation.metadata["file_spec_id"]
        file_spec = context.file_spec_resolver(file_spec_id)
        fields = context.file_fields_resolver(file_spec_id)
        # ...
```

**Benefits of V3 approach**:
1. Stateless strategies - can be singletons
2. Resolution deferred to generate-time - always fresh context
3. Better testability - can mock resolvers
4. Scales to lazy loading from DB

**Migration path**:
1. Add resolver fields to CodegenContext (optional)
2. Update FileCodegenStrategy to prefer context resolvers
3. Fall back to constructor-injected maps for backward compat
4. Deprecate constructor injection in V4

---

**Document End**

*Implementation Complete*
