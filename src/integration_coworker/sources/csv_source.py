"""
CSV/TSV source handler.

Implements the SpecSource protocol for CSV and TSV files.
Uses the existing csv_schema.py parser for type inference.
"""

import logging
from typing import List, Optional, Tuple, Union

from .base import (
    ContentType,
    ParsedSpec,
    SourceType,
    extract_name_from_uri,
    normalize_content,
)

logger = logging.getLogger(__name__)


class CSVSource:
    """
    Handler for CSV/TSV/delimited files.
    
    Uses the existing csv_schema.py parser for:
    - Delimiter detection
    - Header detection
    - Type inference (string, integer, decimal, date, datetime, boolean, uuid, email)
    
    Outputs FileSpec + FileField for the Silver File Model.
    """
    
    def detect(
        self,
        content: ContentType,
        uri: str,
        content_type: str,
    ) -> float:
        """
        Detect if content is CSV/TSV.
        
        Returns confidence score 0-1.
        """
        ct = content_type.lower()
        uri_lower = uri.lower()
        
        # High confidence from extension
        if ".csv" in uri_lower:
            return 0.95
        if ".tsv" in uri_lower:
            return 0.95
        if ".txt" in uri_lower and self._looks_like_delimited(content):
            return 0.70  # Could be many things
        
        # High confidence from content-type
        if "text/csv" in ct or "text/tab-separated" in ct:
            return 0.95
        
        # Medium confidence from content heuristics
        if self._looks_like_delimited(content):
            return 0.60
        
        return 0.0
    
    def parse(
        self,
        content: ContentType,
        uri: str,
    ) -> ParsedSpec:
        """
        Parse CSV/TSV content.
        
        Returns ParsedSpec with source_type=FILE and data containing
        {"file_spec": FileSpec, "fields": List[FileField]}.
        """
        content_str = normalize_content(content)
        
        try:
            from integration_coworker.parsers.csv_schema import infer_csv_schema
        except ImportError:
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data=None,
                errors=["CSV parser (csv_schema.py) not available"],
            )
        
        try:
            # Use existing CSV schema inference
            csv_schema = infer_csv_schema(content_str)
            
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
                name=extract_name_from_uri(uri),
                source_system_id=None,  # Set during persistence
            )
            
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data={"file_spec": file_spec, "fields": fields},
                metadata={
                    "row_count": csv_schema.row_count,
                    "column_count": len(csv_schema.fields),
                    "delimiter": csv_schema.delimiter,
                    "has_header": csv_schema.has_header,
                },
                confidence=0.9,
            )
            
        except Exception as e:
            logger.error(f"CSV parse error: {e}", exc_info=True)
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data=None,
                errors=[f"CSV parse error: {e}"],
            )
    
    def _looks_like_delimited(self, content: ContentType) -> bool:
        """Check if content looks like a delimited file."""
        content_str = normalize_content(content)
        lines = content_str.split('\n')[:20]  # Check first 20 lines
        
        if len(lines) < 2:
            return False
        
        # Check for consistent delimiter
        for delim in [',', '\t', '|', ';']:
            counts = [line.count(delim) for line in lines if line.strip()]
            if len(counts) >= 2:
                # All non-empty lines should have same count, and >= 1 delimiter
                if len(set(counts)) == 1 and counts[0] >= 1:
                    return True
        
        return False


def csv_schema_to_silver(
    csv_schema,
    name: str,
    source_system_id: Optional[int] = None,
) -> Tuple["FileSpec", List["FileField"]]:
    """
    Convert CsvSchema from csv_schema.py to Silver domain models.
    
    Args:
        csv_schema: CsvSchema from parsers/csv_schema.py
        name: Name for the file spec
        source_system_id: Optional source system ID
        
    Returns:
        Tuple of (FileSpec, List[FileField])
    """
    from integration_coworker.domain.models import (
        FileField,
        FileSpec,
        FileType,
    )
    
    # Map delimiter to file type
    file_type = FileType.CSV
    if csv_schema.delimiter == '\t':
        file_type = FileType.TSV
    elif csv_schema.delimiter == '|':
        file_type = FileType.PIPE_DELIMITED
    elif csv_schema.delimiter not in [',', None]:
        file_type = FileType.OTHER_DELIMITED
    
    file_spec = FileSpec(
        id=None,
        source_system_id=source_system_id,
        name=name,
        file_type=file_type.value,
        delimiter=csv_schema.delimiter,
        has_header=csv_schema.has_header,
        encoding=getattr(csv_schema, 'encoding', 'utf-8'),
        description=f"CSV file with {len(csv_schema.fields)} fields",
    )
    
    fields = []
    for i, f in enumerate(csv_schema.fields):
        field = FileField(
            id=None,
            file_spec_id=None,  # Set during persistence
            name=f.name,
            field_type=_map_csv_type_to_file_type(f.inferred_type),
            position=i,
            nullable=f.nullable,
            sample_values=f.sample_values[:5] if f.sample_values else None,
            inference_confidence=f.confidence if hasattr(f, 'confidence') else 0.8,
        )
        fields.append(field)
    
    return file_spec, fields


def _map_csv_type_to_file_type(csv_type: str) -> str:
    """Map CSV parser type to FileFieldType."""
    type_map = {
        "string": "string",
        "integer": "integer",
        "number": "decimal",
        "decimal": "decimal",
        "date": "date",
        "datetime": "datetime",
        "boolean": "boolean",
        "uuid": "uuid",
        "email": "email",
    }
    return type_map.get(csv_type.lower(), "string")


__all__ = [
    "CSVSource",
    "csv_schema_to_silver",
]
