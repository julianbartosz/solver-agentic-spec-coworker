"""
File-based codegen strategy for CSV/Excel/Fixed-Width parsers.

Implements CodegenStrategy Protocol for ProtocolType.FILE.
Delegates to templates in file_templates.py for actual code generation.

CRITICAL: This strategy uses FileParserTemplateResult for all symbol names.
Do NOT guess names like "parse_{safe_name}" - always use the template result.
"""
import logging
from dataclasses import dataclass
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
    FileFieldType,
)

logger = logging.getLogger(__name__)


@dataclass
class FileCodegenStrategy:
    """
    Codegen strategy for file-based data integrations.
    
    Generates parser code for CSV, Excel, and fixed-width files.
    Uses FileParserTemplateResult as single source of truth for symbol names.
    
    Attributes:
        file_specs: FileSpec objects for ID lookup (keyed by ID)
        file_fields_by_spec: FileField lists keyed by file_spec_id
    """
    
    def __init__(
        self,
        file_specs: Optional[List[FileSpec]] = None,
        file_fields: Optional[List[FileField]] = None,
    ):
        """
        Initialize with file specs/fields for ID resolution.
        
        Args:
            file_specs: List of FileSpec objects for ID lookup
            file_fields: List of FileField objects for ID lookup
        """
        self._file_specs = {fs.id: fs for fs in (file_specs or []) if fs.id is not None}
        self._file_fields_by_spec: dict = {}
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
    
    def _get_template_result(
        self,
        file_spec: FileSpec,
        fields: List[FileField],
        language: str,
    ) -> FileParserTemplateResult:
        """
        Get template result - single source of truth for all symbol names.
        
        This is the ONLY place that determines symbol names like parse function,
        validate function, and record class. All artifacts must use these names.
        """
        return generate_file_parser_with_metadata(file_spec, fields, language)
    
    def generate_client(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """
        Generate parser code for a file operation.
        
        Expects operation.metadata = {"file_spec_id": int, "op": str}
        """
        file_spec_id = operation.metadata.get("file_spec_id") if operation.metadata else None
        if file_spec_id is None:
            raise ValueError(
                f"Operation {operation.operation_id} missing file_spec_id in metadata"
            )
        
        file_spec = self._resolve_file_spec(file_spec_id)
        fields = self._resolve_fields(file_spec_id)
        
        # Get template result - single source of truth for naming
        result = self._get_template_result(file_spec, fields, context.language)
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.CLIENT,
            filename=f"{result.module_basename}.py",
            code=result.code,
            language=context.language,
            imports=["csv", "dataclasses", "typing", "datetime", "decimal"],
            dependencies=result.dependencies,
            protocol=ProtocolType.FILE,
            operation_id=operation.operation_id,
            metadata={
                "file_spec_id": file_spec_id,
                "file_type": file_spec.file_type,
                "parse_func": result.parse_func_name,
                "validate_func": result.validate_func_name,
                "record_class": result.record_class_name,
                "module_basename": result.module_basename,
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
            operation: The file operation
            context: Codegen context
            client_artifact: Optional - if provided, uses metadata for symbol names
                            Otherwise regenerates template result.
        """
        file_spec_id = operation.metadata.get("file_spec_id") if operation.metadata else None
        if file_spec_id is None:
            raise ValueError(
                f"Operation {operation.operation_id} missing file_spec_id in metadata"
            )
        
        file_spec = self._resolve_file_spec(file_spec_id)
        
        # Get symbol names from client artifact or regenerate
        if client_artifact and client_artifact.metadata:
            meta = client_artifact.metadata
            parse_func = meta.get("parse_func")
            validate_func = meta.get("validate_func")
            module_name = meta.get("module_basename")
        else:
            # Regenerate template result for consistency
            fields = self._resolve_fields(file_spec_id)
            result = self._get_template_result(file_spec, fields, context.language)
            parse_func = result.parse_func_name
            validate_func = result.validate_func_name
            module_name = result.module_basename
        
        # Generate flow code using the authoritative symbol names
        flow_code = f'''"""
Integration flow for {file_spec.name} file processing.

Generated by Integration Co-Worker.
"""
from {module_name} import {parse_func}, {validate_func}


def process_{module_name}(file_path: str) -> dict:
    """
    Process {file_spec.name} file end-to-end.
    
    Args:
        file_path: Path to the input file
        
    Returns:
        dict with 'records', 'record_count', 'errors', 'error_count', 'is_valid'
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


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("Usage: python flow_{module_name}.py <file_path>")
        sys.exit(1)
    
    result = process_{module_name}(sys.argv[1])
    print(f"Processed {{result['record_count']}} records")
    print(f"Valid: {{result['is_valid']}}")
    if result['errors']:
        print(f"Errors: {{result['error_count']}}")
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
            metadata={
                "file_spec_id": file_spec_id,
                "parse_func": parse_func,
                "validate_func": validate_func,
            },
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
            operation: The file operation
            context: Codegen context
            client_artifact: Optional - if provided, uses metadata for symbol names
        """
        file_spec_id = operation.metadata.get("file_spec_id") if operation.metadata else None
        if file_spec_id is None:
            raise ValueError(
                f"Operation {operation.operation_id} missing file_spec_id in metadata"
            )
        
        file_spec = self._resolve_file_spec(file_spec_id)
        fields = self._resolve_fields(file_spec_id)
        
        # Get symbol names from client artifact or regenerate
        if client_artifact and client_artifact.metadata:
            meta = client_artifact.metadata
            parse_func = meta.get("parse_func")
            validate_func = meta.get("validate_func")
            class_name = meta.get("record_class")
            module_name = meta.get("module_basename")
        else:
            result = self._get_template_result(file_spec, fields, context.language)
            parse_func = result.parse_func_name
            validate_func = result.validate_func_name
            class_name = result.record_class_name
            module_name = result.module_basename
        
        # Build sample data based on fields (using enum-safe comparisons)
        sample_row_parts = []
        for f in sorted(fields, key=lambda x: x.position):
            # Normalize field_type to string for comparison (enum-safe)
            field_type_value = (
                f.field_type.value
                if hasattr(f.field_type, 'value')
                else str(f.field_type)
            ).lower()
            
            if field_type_value == FileFieldType.INTEGER.value:
                sample_row_parts.append("1")
            elif field_type_value == FileFieldType.DECIMAL.value:
                sample_row_parts.append("100.00")
            elif field_type_value == FileFieldType.DATE.value:
                sample_row_parts.append("2024-01-01")
            elif field_type_value == FileFieldType.BOOLEAN.value:
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
            metadata={
                "file_spec_id": file_spec_id,
                "parse_func": parse_func,
                "validate_func": validate_func,
                "record_class": class_name,
            },
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
        file_spec_id = operation.metadata.get("file_spec_id") if operation.metadata else None
        if file_spec_id is None:
            return ""
        
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
