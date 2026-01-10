"""
CSV/TSV source handler with TRUE STREAMING support.

Implements the SpecSource and StreamingSpecSource protocols for CSV/TSV files.
Uses the existing csv_schema.py parser for type inference with streaming
optimization for large files.

Streaming Architecture (v2.1):
    - detect_from_handle(): Sample-based detection (64KB max)
    - parse_from_handle(): TRUE streaming schema inference
      - Reads file line-by-line using Python csv module
      - Never loads entire file into memory
      - Infers types from first N rows (configurable)
"""

import csv
import io
import logging
from typing import TYPE_CHECKING, List, Optional, TextIO, Tuple, Union

from .base import (
    ContentType,
    ParsedSpec,
    SourceType,
    extract_name_from_uri,
    normalize_content,
)

if TYPE_CHECKING:
    from .content_handle import ContentHandle

logger = logging.getLogger(__name__)

# Streaming configuration
DETECT_SAMPLE_SIZE = 65536  # 64KB sample for detection
SCHEMA_SAMPLE_ROWS = 100    # Number of rows to sample for type inference


class CSVSource:
    """
    Handler for CSV/TSV/delimited files with TRUE STREAMING support.
    
    Implements both SpecSource (legacy) and StreamingSpecSource protocols:
    
    Legacy (SpecSource):
        - detect(): Score from in-memory content
        - parse(): Parse from in-memory content
        
    Streaming (StreamingSpecSource):
        - detect_from_handle(): Score from 64KB sample
        - parse_from_handle(): TRUE streaming, never loads full file
    
    Type inference supports:
        string, integer, decimal, date, datetime, boolean, uuid, email
    
    Outputs FileSpec + FileField for the Silver File Model.
    """
    
    # =========================================================================
    # Legacy SpecSource Protocol (backwards compatibility)
    # =========================================================================
    
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
        Parse CSV/TSV content from in-memory bytes/string.
        
        LEGACY: Loads entire content into memory.
        For large files, use parse_from_handle() instead.
        
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
                    "streaming": False,  # Legacy path
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
    
    # =========================================================================
    # StreamingSpecSource Protocol (TRUE STREAMING)
    # =========================================================================
    
    def detect_from_handle(
        self,
        handle: "ContentHandle",
        uri: str,
        content_type: str,
    ) -> float:
        """
        Detect if content is CSV/TSV using sample from handle.
        
        TRUE STREAMING: Only reads first 64KB for detection.
        
        Returns confidence score 0-1.
        """
        ct = content_type.lower()
        uri_lower = uri.lower()
        
        # High confidence from extension (no content read needed)
        if ".csv" in uri_lower:
            return 0.95
        if ".tsv" in uri_lower:
            return 0.95
        
        # High confidence from content-type
        if "text/csv" in ct or "text/tab-separated" in ct:
            return 0.95
        
        # For .txt or unknown, sample the content
        if ".txt" in uri_lower or not uri_lower.endswith(('.json', '.yaml', '.yml', '.xml')):
            # Sample first 64KB
            with handle.open_bytes() as f:
                sample = f.read(DETECT_SAMPLE_SIZE)
            
            if self._looks_like_delimited(sample):
                if ".txt" in uri_lower:
                    return 0.70
                return 0.60
        
        return 0.0
    
    def parse_from_handle(
        self,
        handle: "ContentHandle",
        uri: str,
    ) -> ParsedSpec:
        """
        Parse CSV/TSV content with TRUE STREAMING.
        
        This method NEVER loads the entire file into memory.
        It uses Python's csv module to iterate line-by-line.
        
        Schema inference:
            1. Read first row for headers
            2. Sample first N rows (default 100) for type inference
            3. Count total rows by iterating (without storing)
        
        Returns ParsedSpec with source_type=FILE.
        """
        try:
            # Detect delimiter from sample (streaming)
            with handle.open_bytes() as f:
                sample = f.read(DETECT_SAMPLE_SIZE)
            
            delimiter = self._detect_delimiter_from_sample(sample)
            
            # TRUE STREAMING: Open text stream and iterate
            row_count = 0
            headers: List[str] = []
            sample_rows: List[List[str]] = []
            
            with handle.open_text() as text_stream:
                reader = csv.reader(text_stream, delimiter=delimiter)
                
                for i, row in enumerate(reader):
                    if i == 0:
                        # First row is headers
                        headers = row
                        continue
                    
                    # Sample first N rows for type inference
                    if i <= SCHEMA_SAMPLE_ROWS:
                        sample_rows.append(row)
                    
                    row_count += 1
            
            if not headers:
                return ParsedSpec(
                    source_type=SourceType.FILE,
                    source_uri=uri,
                    data=None,
                    errors=["No rows found in CSV"],
                )
            
            # Infer schema from sampled rows
            csv_schema = self._infer_schema_from_samples(
                headers=headers,
                sample_rows=sample_rows,
                delimiter=delimiter,
                row_count=row_count,
            )
            
            # Convert to Silver model
            file_spec, fields = csv_schema_to_silver(
                csv_schema,
                name=extract_name_from_uri(uri),
                source_system_id=None,
            )
            
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data={"file_spec": file_spec, "fields": fields},
                metadata={
                    "row_count": row_count,
                    "column_count": len(headers),
                    "delimiter": delimiter,
                    "has_header": True,
                    "streaming": True,  # Streaming path!
                    "sample_rows_used": len(sample_rows),
                },
                confidence=0.9,
            )
            
        except Exception as e:
            logger.error(f"CSV streaming parse error: {e}", exc_info=True)
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data=None,
                errors=[f"CSV parse error: {e}"],
            )
    
    # =========================================================================
    # Private Helpers
    # =========================================================================
    
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
    
    def _detect_delimiter_from_sample(self, sample: bytes) -> str:
        """Detect delimiter from sample bytes."""
        try:
            content_str = sample.decode('utf-8', errors='ignore')
        except Exception:
            content_str = sample.decode('latin-1', errors='ignore')
        
        lines = content_str.split('\n')[:5]
        text = '\n'.join(lines)
        
        delimiters = [',', '\t', ';', '|']
        counts = {d: text.count(d) for d in delimiters}
        
        best = max(delimiters, key=lambda d: counts.get(d, 0))
        return best if counts.get(best, 0) > 0 else ','
    
    def _infer_schema_from_samples(
        self,
        headers: List[str],
        sample_rows: List[List[str]],
        delimiter: str,
        row_count: int,
    ) -> "CsvSchema":
        """
        Infer schema from sampled rows without loading full file.
        
        This is a streaming-optimized version of infer_csv_schema().
        """
        from integration_coworker.parsers.csv_schema import (
            CsvSchema,
            InferredField,
            _infer_column_type,
            _normalize_column_name,
        )
        
        schema = CsvSchema()
        schema.delimiter = delimiter
        schema.has_header = True
        schema.row_count = row_count
        
        # Infer types for each column
        for col_idx, header in enumerate(headers):
            col_values = []
            for row in sample_rows:
                if col_idx < len(row):
                    col_values.append(row[col_idx])
            
            inferred_type = _infer_column_type(col_values)
            nullable = any(
                not v or v.lower() in ("null", "none", "")
                for v in col_values
            )
            
            # Get sample non-empty values
            samples = [
                v for v in col_values
                if v and v.lower() not in ("null", "none")
            ][:5]
            
            schema.fields.append(InferredField(
                name=_normalize_column_name(header),
                inferred_type=inferred_type,
                nullable=nullable,
                sample_values=samples,
            ))
        
        return schema


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
